"""RF04 isolado. Doubles transacionais com locks por linha; nenhum PostgreSQL.

O double interpreta somente o SQL usado nesta cadeia. Não homologa constraints
ou isolamento PostgreSQL; esses checks reais ficam para a fase manual 2C.2.
"""
from copy import deepcopy
from datetime import datetime, timezone
import threading
from unittest import TestCase
from unittest.mock import patch
from flask.sessions import SecureCookieSessionInterface
from psycopg2.errors import UniqueViolation
from app import create_app
from core.errors import BusinessError, ValidationError, NotFoundError, SchemaPendingError
from schemas.rastreamento_dto import CorredorDTO, PosicaoDTO, ItemEstoqueDTO, PesquisaItensDTO
from schemas.movimentacao_dto import MovimentacaoDTO
from services.rastreamento_service import CorredorService, PosicaoService, ItemEstoqueService
from services.movimentacao_service import MovimentacaoService
from services.produto_service import ProdutoService
from test_produtos import PAYLOAD


class Duplicate(UniqueViolation):
    def __init__(self, name):
        self.name = name

    @property
    def diag(self):
        return type('Diag', (), {'constraint_name': self.name})()


class PhysicalDB:
    def __init__(self):
        self.products = {7: [7, 'Caixa', 'Embalagem', 'CX-01', 'Legado', None, 0, 0],
                         8: [8, 'Papel', 'Embalagem', 'PP-01', 'Legado', None, 0, 0]}
        self.corridors = {1: [1, 'A', 20, True], 2: [2, 'B', 100, True]}
        self.positions = {1: [1, 'A-01', 1, True], 2: [2, 'A-02', 1, True], 3: [3, 'B-01', 2, True]}
        self.items, self.movements = {}, {}
        self.active, self.role, self.schema = True, 'admin', True
        self.locks = {}
        self.guard = threading.Lock()
        self.first_locked, self.second_waiting, self.release_first = (threading.Event() for _ in range(3))
        self.coordinate = None

    def lock(self, key):
        with self.guard:
            return self.locks.setdefault(key, threading.RLock())


class PhysicalConnection:
    def __init__(self, db, fail=None):
        self.db, self.fail = db, fail
        self.result, self.undo, self.held, self.queries = None, {}, [], []
        self.commits = self.rollbacks = 0

    def cursor(self): return self
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def fetchone(self): return self.result[0] if isinstance(self.result, list) and self.result else self.result
    def fetchall(self): return self.result

    def acquire(self, table, key):
        lock = self.db.lock((table, key))
        if self.db.coordinate == table:
            if self.db.first_locked.is_set(): self.db.second_waiting.set()
            lock.acquire()
            if not self.db.first_locked.is_set():
                self.db.first_locked.set()
                if not self.db.release_first.wait(5): raise AssertionError('Timeout de coordenação')
        else:
            lock.acquire()
        self.held.append(lock)

    def write(self, table, key, value):
        data = getattr(self.db, table)
        self.undo.setdefault((table, key), deepcopy(data.get(key)))
        data[key] = value

    def finish(self):
        self.undo.clear()
        for lock in reversed(self.held): lock.release()
        self.held.clear()

    def commit(self):
        if self.fail == 'commit': raise RuntimeError('Falha simulada')
        self.commits += 1
        self.finish()

    def rollback(self):
        self.rollbacks += 1
        for (table, key), value in reversed(list(self.undo.items())):
            if value is None: getattr(self.db, table).pop(key, None)
            else: getattr(self.db, table)[key] = value
        self.finish()

    def execute(self, q, p=()):
        self.queries.append((q, p))
        db = self.db
        if q.startswith('SELECT to_regclass'): self.result = (db.schema,)
        elif q.startswith('SELECT id_movimentacao FROM movimentacoes LIMIT 0'): self.result = None
        elif q.startswith('SELECT ativo, perfil'): self.result = (db.active, db.role)
        elif q.startswith('SELECT perfil'): self.result = (db.role,)
        elif q.startswith('SELECT estoque FROM produtos'):
            assert 'FOR UPDATE' in q
            self.acquire('products', p[0])
            self.result = (db.products[p[0]][6],) if p[0] in db.products else None
        elif q.startswith('SELECT COALESCE(SUM(quantidade), 0) FROM itens_estoque'):
            index = 1 if 'id_produto' in q else 2
            self.result = (sum(i[4] for i in db.items.values() if i[index] == p[0]),)
        elif q.startswith('SELECT COALESCE(SUM(i.quantidade)'):
            self.result = (sum(i[4] for i in db.items.values() if db.positions[i[2]][2] == p[0] and db.positions[i[2]][3]),)
        elif q.startswith('SELECT id_corredor FROM posicoes_estoque'):
            if 'FOR UPDATE' in q: self.acquire('positions', p[0])
            self.result = (db.positions[p[0]][2],) if p[0] in db.positions else None
        elif q.startswith('SELECT id_corredor, identificacao'):
            assert 'FOR UPDATE' in q
            self.acquire('corridors', p[0])
            self.result = tuple(db.corridors[p[0]]) if p[0] in db.corridors else None
        elif q.startswith('SELECT codigo_posicao, ativo'):
            assert 'FOR UPDATE' in q
            self.acquire('positions', p[0])
            self.result = (db.positions[p[0]][1], db.positions[p[0]][3])
        elif q.startswith('SELECT id_item_estoque, quantidade, status'):
            assert 'FOR UPDATE' in q
            row = next((i for i in db.items.values() if tuple(i[1:4]) == p), None)
            if row: self.acquire('items', row[0])
            self.result = (row[0], row[4], row[5]) if row else None
        elif q.startswith('INSERT INTO itens_estoque'):
            if any(tuple(i[1:4]) == p[:3] for i in db.items.values()):
                raise Duplicate('itens_estoque_produto_posicao_lote_key')
            key = max(db.items, default=0) + 1
            self.write('items', key, [key, *p]); self.result = (key,)
            if self.fail == 'item': raise RuntimeError('Falha simulada')
        elif q.startswith('UPDATE itens_estoque'):
            row = list(db.items[p[1]]); row[4] = p[0]; self.write('items', p[1], row)
            if self.fail == 'item': raise RuntimeError('Falha simulada')
        elif q.startswith('UPDATE produtos SET estoque'):
            row = list(db.products[p[1]]); row[6] = p[0]; self.write('products', p[1], row)
            if self.fail == 'product': raise RuntimeError('Falha simulada')
        elif q.startswith('INSERT INTO produtos'):
            key = max(db.products) + 1
            self.acquire('products', key)
            self.write('products', key, [key, *p]); self.result = tuple(db.products[key])
        elif q.startswith('INSERT INTO movimentacoes'):
            key = max(db.movements, default=0) + 1
            product = db.products[p[0]]
            self.write('movements', key, [key, *p, datetime.now(timezone.utc), product[1], product[3], 'Teste', *([None] * 7)])
            self.result = (key,)
            if self.fail == 'movement': raise RuntimeError('Falha simulada')
        elif q.startswith('UPDATE movimentacoes SET'):
            row = list(db.movements[p[-1]]); row[12:] = p[:-1]; self.write('movements', p[-1], row)
            if self.fail == 'context': raise RuntimeError('Falha simulada')
        elif 'FROM movimentacoes m' in q:
            if self.fail == 'response': raise RuntimeError('Falha simulada')
            rows = list(reversed(list(db.movements.values())))
            if 'WHERE m.id_movimentacao' in q: rows = [r for r in rows if r[0] == p[0]]
            elif 'WHERE m.id_produto' in q: rows = [r for r in rows if r[1] == p[0]]
            self.result = [tuple(r) for r in rows]
        elif q.startswith(('INSERT INTO corredores', 'UPDATE corredores')):
            key = p[-1] if q.startswith('UPDATE') else max(db.corridors, default=0) + 1
            if any(r[1] == p[0] and r[0] != key for r in db.corridors.values()): raise Duplicate('corredores_identificacao_key')
            self.write('corridors', key, [key, *p[:3]]); self.result = (key,)
        elif q.startswith(('INSERT INTO posicoes_estoque', 'UPDATE posicoes_estoque')):
            updating = q.startswith('UPDATE')
            key = p[-1] if updating else max(db.positions, default=0) + 1
            if any(r[1] == p[0] and r[0] != key for r in db.positions.values()): raise Duplicate('posicoes_estoque_codigo_posicao_key')
            row = [key, p[0], db.positions[key][2], p[1]] if updating else [key, *p]
            self.write('positions', key, row); self.result = (key,)
        elif q.startswith('SELECT c.id_corredor'):
            self.result = [tuple(c) + (sum(i[4] for i in db.items.values() if db.positions[i[2]][2] == c[0] and db.positions[i[2]][3]),)
                           for c in db.corridors.values() if not p or c[0] == p[0]]
        elif q.startswith('SELECT p.id_posicao'):
            self.result = [tuple(r) + (db.corridors[r[2]][1], db.corridors[r[2]][3]) for r in db.positions.values()
                           if not p or r[2 if 'WHERE p.id_corredor' in q else 0] == p[0]]
        elif q.startswith('SELECT i.id_item_estoque'):
            rows = [tuple(i) + (db.products[i[1]][1], db.products[i[1]][3], db.positions[i[2]][1], db.positions[i[2]][2], db.corridors[db.positions[i[2]][2]][1]) for i in db.items.values()]
            offset = 0
            for col, index in (('i.id_produto', 1), ('i.id_posicao', 2), ('c.id_corredor', 9), ('i.id_item_estoque', 0)):
                if col + ' = %s' in q:
                    rows = [r for r in rows if r[index] == p[offset]]; offset += 1
            if 'ILIKE' in q:
                term = p[-1][1:-1].replace('!%', '%').replace('!_', '_').replace('!!', '!').lower()
                rows = [r for r in rows if term in ' '.join(str(r[i]) for i in (6, 7, 3, 8, 10)).lower()]
            self.result = rows
        elif q.startswith('SELECT id_'):
            table = 'products' if 'FROM produtos' in q else 'corridors' if 'FROM corredores' in q else 'positions'
            self.result = (p[0],) if p[0] in getattr(db, table) else None
        else: raise AssertionError('Consulta não suportada: ' + q)


class PhysicalTests(TestCase):
    def setUp(self):
        self.db = PhysicalDB(); self.conn = PhysicalConnection(self.db)
        self.moves = MovimentacaoService(self.conn)
        self.corridors = CorredorService(self.conn); self.positions = PosicaoService(self.conn)
        self.items = ItemEstoqueService(self.conn)
        self.addCleanup(self.conn.rollback)

    def move(self, tipo='ENTRADA', amount=5, product=7, position=1, lot='L-1', conn=None):
        return MovimentacaoService(conn or self.conn).registrar(product, 23, dict(tipo=tipo, id_posicao=position,
            lote=lot, motivo='Conferência', **{'novo_saldo_item' if tipo == 'AJUSTE' else 'quantidade': amount}))

    def invariant(self):
        for key, product in self.db.products.items():
            self.assertEqual(product[6], sum(i[4] for i in self.db.items.values() if i[1] == key))

    def state(self):
        return deepcopy((self.db.products, self.db.items, self.db.movements))

    def test_corridor_create_and_read(self):
        result = self.corridors.salvar(23, dict(identificacao=' C ', capacidade_maxima=30))
        self.assertEqual((result['identificacao'], result['ocupacao'], result['ativo']), ('C', 0, True))
        self.assertEqual(self.corridors.obter(23, result['id_corredor']), result)

    def test_corridor_duplicate(self):
        with self.assertRaises(BusinessError): self.corridors.salvar(23, dict(identificacao='A', capacidade_maxima=30))
        self.assertEqual(len(self.db.corridors), 2)

    def test_corridor_invalid_capacity(self):
        for value in (0, -1, True, '10', 1.5, 2147483648):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.corridors.salvar(23, dict(identificacao='C', capacidade_maxima=value))

    def test_corridor_update_and_deactivate(self):
        result = self.corridors.salvar(23, dict(identificacao='AA', capacidade_maxima=30, ativo=False), 1)
        self.assertEqual((result['identificacao'], result['capacidade_maxima'], result['ativo']), ('AA', 30, False))

    def test_occupied_corridor_cannot_shrink_or_deactivate(self):
        self.move(amount=5)
        for capacity, active in ((4, True), (20, False)):
            with self.assertRaises(BusinessError): self.corridors.salvar(23, dict(identificacao='A', capacidade_maxima=capacity, ativo=active), 1)
        self.assertEqual(self.db.corridors[1], [1, 'A', 20, True])

    def test_position_create_and_list_corridor(self):
        result = self.positions.salvar(23, dict(codigo_posicao=' A-03 ', id_corredor=1))
        self.assertEqual(result['codigo_posicao'], 'A-03')
        self.assertEqual(len(self.positions.listar(23, 1)), 3)
        self.assertEqual(self.positions.obter(23, result['id_posicao']), result)

    def test_position_duplicate(self):
        with self.assertRaises(BusinessError): self.positions.salvar(23, dict(codigo_posicao='A-01', id_corredor=1))

    def test_position_missing_corridor(self):
        with self.assertRaises(NotFoundError): self.positions.salvar(23, dict(codigo_posicao='X', id_corredor=999))

    def test_position_cannot_change_corridor(self):
        with self.assertRaises(BusinessError): self.positions.salvar(23, dict(codigo_posicao='X', id_corredor=2), 1)
        self.assertEqual(self.db.positions[1][2], 1)

    def test_position_occupied_inactivation_rejected_empty_allowed(self):
        self.move()
        with self.assertRaises(BusinessError): self.positions.salvar(23, dict(codigo_posicao='A-01', id_corredor=1, ativo=False), 1)
        self.move('SAIDA')
        result = self.positions.salvar(23, dict(codigo_posicao='A-01', id_corredor=1, ativo=False), 1)
        self.assertFalse(result['ativo'])

    def test_item_dto_valid_statuses(self):
        for status in ('DISPONIVEL', 'RESERVADO', 'BLOQUEADO'):
            self.assertEqual(ItemEstoqueDTO(7, 1, 'L-1', 0, status).status, status)

    def test_item_dto_invalid_status_negative_and_identifiers(self):
        for args in ((7, 1, 'L', -1), (7, 1, 'L', 2, 'INVALIDO'), (True, 1, 'L', 1), (7, 0, 'L', 1), (7, 1, ' ', 1)):
            with self.subTest(args=args), self.assertRaises(ValidationError): ItemEstoqueDTO(*args)

    def test_physical_contract_rejects_missing_ambiguous_fields(self):
        for payload in (dict(tipo='ENTRADA', quantidade=1, lote='L'), dict(tipo='ENTRADA', quantidade=1, id_posicao=1),
                        dict(tipo='AJUSTE', novo_estoque=3, motivo='Contagem', id_posicao=1, lote='L'),
                        dict(tipo='ENTRADA', quantidade=1, id_posicao=True, lote='L')):
            with self.subTest(payload=payload), self.assertRaises(ValidationError): MovimentacaoDTO.from_dict(payload)

    def test_entry_creates_item_product_history_and_invariant(self):
        result = self.move()
        self.assertEqual((self.db.products[7][6], self.db.items[1][4], len(self.db.movements)), (5, 5, 1))
        self.assertEqual((result['id_posicao'], result['lote'], result['corredor'], result['quantidade_item_anterior'], result['quantidade_item_posterior']), (1, 'L-1', 'A', 0, 5))
        self.assertEqual(self.conn.commits, 1); self.invariant()

    def test_same_product_position_lot_merges_and_other_lots_are_distinct(self):
        self.move(); self.move(); self.move(lot='L-2'); self.move(position=2)
        self.assertEqual(len(self.db.items), 3)
        self.assertEqual(self.db.items[1][4], 10); self.invariant()

    def test_capacity_exceeded_no_partial_state(self):
        self.move(amount=18); before = self.state()
        with self.assertRaises(BusinessError): self.move(amount=3, product=8, position=2)
        self.assertEqual(self.state(), before); self.invariant()

    def test_capacity_counts_reserved_and_blocked_units(self):
        self.move(amount=18); self.db.items[1][5] = 'BLOQUEADO'
        with self.assertRaises(BusinessError): self.move(amount=3, product=8, position=2)

    def test_exit_and_invariant(self):
        self.move(amount=10); result = self.move('SAIDA', 4)
        self.assertEqual((result['estoque_posterior'], result['quantidade_item_posterior']), (6, 6)); self.invariant()

    def test_exit_above_lot_even_when_global_sufficient(self):
        self.move(amount=3); self.move(amount=10, position=2); before = self.state()
        with self.assertRaises(BusinessError): self.move('SAIDA', 4)
        self.assertEqual(self.state(), before)

    def test_adjustment_changes_only_selected_lot_and_invariant(self):
        self.move(amount=5); self.move(amount=4, position=2)
        for target in (2, 8, 8, 0):
            result = self.move('AJUSTE', target)
            self.assertEqual(result['estoque_posterior'], target + 4)
            self.assertEqual(result['quantidade'], target + 4)
            self.assertEqual(result['quantidade_item_posterior'], target)
            self.assertEqual(self.db.items[2][4], 4); self.invariant()

    def test_adjustment_capacity_rejected(self):
        self.move(amount=10); before = self.state()
        with self.assertRaises(BusinessError): self.move('AJUSTE', 21)
        self.assertEqual(self.state(), before)

    def test_rollback_at_every_write_and_response_and_commit(self):
        for stage in ('item', 'product', 'movement', 'context', 'response', 'commit'):
            before = self.state(); conn = PhysicalConnection(self.db, stage)
            with self.subTest(stage=stage), self.assertRaises(RuntimeError): self.move(conn=conn)
            self.assertEqual(self.state(), before); self.assertEqual(conn.rollbacks, 1)
        self.move(); self.invariant()

    def test_rollback_existing_item(self):
        self.move(); before = self.state()
        with self.assertRaises(RuntimeError): self.move(conn=PhysicalConnection(self.db, 'movement'))
        self.assertEqual(self.state(), before)

    def test_initial_stock_requires_destination_and_rolls_back_product(self):
        before = self.state()
        with self.assertRaises(ValidationError): ProdutoService(self.conn).registrar_produto(PAYLOAD, 23)
        self.assertEqual(self.state(), before)

    def test_initial_stock_physical_atomic(self):
        result = ProdutoService(self.conn).registrar_produto({**PAYLOAD, 'id_posicao': 1, 'lote': 'INIT'}, 23)
        self.assertEqual(result['estoque'], 10)
        self.assertEqual(self.db.movements[1][7], 'Estoque inicial'); self.invariant()

    def test_initial_failure_rolls_back_product_item_and_history(self):
        before = self.state(); self.conn.fail = 'context'
        with self.assertRaises(RuntimeError): ProdutoService(self.conn).registrar_produto({**PAYLOAD, 'id_posicao': 1, 'lote': 'INIT'}, 23)
        self.assertEqual(self.state(), before)

    def test_legacy_positive_balance_requires_reconciliation(self):
        self.db.products[7][6] = 10; before = self.state()
        with self.assertRaises(BusinessError): self.move()
        self.assertEqual(self.state(), before)

    def test_legacy_contract_cannot_mutate_when_schema_active(self):
        for payload in ({'tipo': 'ENTRADA', 'quantidade': 1}, {'tipo': 'AJUSTE', 'novo_estoque': 5, 'motivo': 'Contagem'}):
            with self.assertRaises(ValidationError): self.moves.registrar(7, 23, payload)
        self.assertEqual(self.db.items, {}); self.assertEqual(self.db.movements, {})

    def test_physical_payload_before_schema_returns_pending(self):
        self.db.schema = False
        with self.assertRaises(SchemaPendingError): self.move()
        with self.assertRaises(SchemaPendingError): self.corridors.listar(23)
        self.assertEqual(self.db.movements, {})

    def test_inactive_locations_rejected(self):
        self.db.corridors[1][3] = False
        with self.assertRaises(BusinessError): self.move()
        self.db.corridors[1][3] = True; self.db.positions[1][3] = False
        with self.assertRaises(BusinessError): self.move()

    def test_reserved_blocked_no_exit_adjustment_keeps_status(self):
        self.move()
        for status in ('RESERVADO', 'BLOQUEADO'):
            self.db.items[1][5] = status
            with self.assertRaises(BusinessError): self.move('SAIDA', 1)
            self.move('AJUSTE', 4)
            self.assertEqual(self.db.items[1][5], status); self.invariant()

    def test_missing_product_position_item(self):
        for kwargs in ({'product': 999}, {'position': 999}, {'tipo': 'SAIDA'}, {'tipo': 'AJUSTE'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(NotFoundError): self.move(**kwargs)

    def test_history_snapshots_survive_renames(self):
        result = self.move()
        self.corridors.salvar(23, dict(identificacao='AA', capacidade_maxima=20), 1)
        self.positions.salvar(23, dict(codigo_posicao='AA-01', id_corredor=1), 1)
        self.assertEqual(self.moves.obter(1, 23), result)

    def test_tracking_search_all_dimensions_and_parent_filters(self):
        self.move()
        for term in ('Caixa', 'CX-01', 'L-1', 'A-01', 'A'):
            rows = self.items.listar(23, {'q': term})
            self.assertEqual(len(rows), 1); self.assertEqual(rows[0]['status'], 'DISPONIVEL')
        self.assertEqual(len(self.items.listar(23, {'id_corredor': '1'}, id_produto=7)), 1)
        self.assertEqual(self.items.listar(23, {}, id_posicao=2), [])
        with self.assertRaises(NotFoundError): self.items.listar(23, {}, id_produto=999)
        self.assertEqual(self.items.obter(23, 1)['produto_codigo'], 'CX-01')

    def test_search_sql_parameterization_and_wildcard_escape(self):
        self.items.listar(23, {'q': "x%' OR 1=1 --"})
        query, params = self.conn.queries[-1]
        self.assertNotIn("OR 1=1", query); self.assertIn("!%", params[0])
        for data in ({'id_produto': '-1'}, {'id_posicao': 'true'}, {'extra': 'x'}):
            with self.assertRaises(ValidationError): PesquisaItensDTO.from_dict(data)

    def concurrent(self, operations, lock_table):
        self.db.coordinate = lock_table
        results, errors = [], []
        def run(kwargs):
            try: self.move(conn=PhysicalConnection(self.db), **kwargs); results.append('ok')
            except BusinessError: results.append('conflict')
            except Exception as error: errors.append(type(error).__name__)
        threads = [threading.Thread(target=run, args=(kwargs,)) for kwargs in operations]
        try:
            threads[0].start(); self.assertTrue(self.db.first_locked.wait(3))
            threads[1].start(); self.assertTrue(self.db.second_waiting.wait(3))
            self.assertEqual(results, [])
        finally:
            self.db.release_first.set()
            for thread in threads:
                if thread.ident: thread.join(5)
        self.assertFalse(any(t.is_alive() for t in threads)); self.assertEqual(errors, [])
        self.assertCountEqual(results, ['ok', 'conflict']); self.invariant()

    def test_concurrent_entries_different_products_share_corridor_capacity(self):
        self.concurrent([dict(amount=15), dict(amount=15, product=8, position=2)], 'corridors')
        self.assertEqual(sum(i[4] for i in self.db.items.values()), 15)
        self.assertEqual(len(self.db.movements), 1)

    def test_concurrent_exits_same_lot(self):
        self.move(amount=5)
        self.concurrent([dict(tipo='SAIDA', amount=4), dict(tipo='SAIDA', amount=4)], 'products')
        self.assertEqual(self.db.items[1][4], 1); self.assertEqual(len(self.db.movements), 2)


class PhysicalHttpTests(TestCase):
    def setUp(self):
        self.db = PhysicalDB(); self.conn = PhysicalConnection(self.db)
        with patch('app.Connection.get_connection', return_value=self.conn): self.app = create_app()
        self.app.session_interface = SecureCookieSessionInterface()
        self.client = self.app.test_client()
        self.addCleanup(self.conn.rollback)

    def login(self, role='admin'):
        self.db.role = role
        with self.client.session_transaction() as s: s.update(user_id=23, user_perfil=role, user_admin=role == 'admin', user_ativo=True)

    def test_all_tracking_routes_require_authentication(self):
        for path in ('/corredores', '/corredores/1', '/corredores/1/posicoes', '/posicoes', '/posicoes/1', '/itens-estoque', '/itens-estoque/1', '/produtos/7/itens-estoque', '/posicoes/1/itens-estoque'):
            self.assertEqual(self.client.get(path).status_code, 401)
        for method, path in (('post', '/corredores'), ('put', '/corredores/1'), ('post', '/posicoes'), ('put', '/posicoes/1')):
            self.assertEqual(getattr(self.client, method)(path, json={}).status_code, 401)

    def test_reader_reads_cannot_manage_or_change_quantity(self):
        # 2E (D6): perfil desconhecido não lê nem escreve; AUDITOR assume o papel de leitor.
        self.login('leitor')
        self.assertEqual(self.client.get('/corredores').status_code, 403)
        self.assertEqual(self.client.post('/corredores', json={}).status_code, 403)
        self.login('auditor')
        self.assertEqual(self.client.get('/corredores').status_code, 200)
        self.assertEqual(self.client.post('/corredores', json={}).status_code, 403)
        for method in ('post', 'put', 'patch', 'delete'):
            self.assertIn(getattr(self.client, method)('/itens-estoque/1', json={'quantidade': 2}).status_code, (404, 405))

    def test_management_http_crud_conflicts_and_invalid_json(self):
        self.login()
        self.assertEqual(self.client.post('/corredores', json={'identificacao': 'C', 'capacidade_maxima': 2}).status_code, 201)
        self.assertEqual(self.client.post('/corredores', json={'identificacao': 'C', 'capacidade_maxima': 2}).status_code, 409)
        self.assertEqual(self.client.post('/posicoes', json={'codigo_posicao': 'C-1', 'id_corredor': 3}).status_code, 201)
        self.assertEqual(self.client.put('/corredores/3', json={'identificacao': 'C', 'capacidade_maxima': 3, 'ativo': False}).status_code, 200)
        self.assertEqual(self.client.get('/corredores/3/posicoes').json[0]['codigo_posicao'], 'C-1')
        for path in ('/corredores', '/posicoes'):
            self.assertEqual(self.client.post(path, data='{', content_type='application/json').status_code, 400)
            self.assertEqual(self.client.post(path, json={'ativo': 'false'}).status_code, 400)

    def test_physical_http_roundtrip_and_errors(self):
        self.login('operador')
        payload = dict(tipo='ENTRADA', quantidade=5, id_posicao=1, lote='L')
        self.assertEqual(self.client.post('/produtos/7/movimentacoes', json=payload).status_code, 201)
        self.assertEqual(self.client.get('/produtos/7/itens-estoque').json[0]['quantidade'], 5)
        self.assertEqual(self.client.post('/produtos/7/movimentacoes', json={**payload, 'quantidade': 30}).status_code, 409)
        self.assertEqual(self.client.post('/produtos/7/movimentacoes', json={'tipo': 'SAIDA', 'quantidade': 1}).status_code, 400)
        self.assertEqual(self.client.get('/itens-estoque/999').status_code, 404)

    def test_persisted_revocation_and_schema_pending(self):
        self.login(); self.db.active = False
        self.assertEqual(self.client.get('/posicoes').status_code, 403)
        self.assertEqual(self.client.get('/posicoes').status_code, 401)  # 2E: sessão revogada
        self.db.active = True; self.login('admin'); self.db.role = 'operador'
        self.assertEqual(self.client.post('/corredores', json={'identificacao': 'C', 'capacidade_maxima': 2}).status_code, 403)
        self.db.schema = False
        self.assertEqual(self.client.get('/itens-estoque').status_code, 503)
