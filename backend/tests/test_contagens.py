"""RF11 isolado: contagem de inventário. Nenhum PostgreSQL.

Estende o double transacional da 2C (locks por linha, rollback por registro
alterado) com a tabela contagens_inventario. Emula as FKs/CHECK/UNIQUE do
vínculo com o AJUSTE apenas para detectar uso incorreto; não homologa o
PostgreSQL real, reservado para a Etapa 2D.2.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import threading
from unittest import TestCase
from unittest.mock import patch

from flask.sessions import SecureCookieSessionInterface
from psycopg2.errors import ForeignKeyViolation, UndefinedTable

from app import create_app
from core.errors import AuthorizationError, BusinessError, NotFoundError, SchemaPendingError, ValidationError
from schemas.contagem_dto import CAMPOS_SERVIDOR, AplicacaoContagemDTO, ContagemDTO, PesquisaContagensDTO
from services.contagem_service import ContagemService
from services.movimentacao_service import MovimentacaoService
from services.rastreamento_service import CorredorService, PosicaoService
from test_rastreamento import Duplicate, PhysicalConnection, PhysicalDB

MAX = 2147483647
CAMPOS_RESPOSTA = {'id_contagem', 'id_item_estoque', 'id_produto', 'id_posicao', 'lote', 'quantidade_sistema',
                   'quantidade_fisica', 'divergencia', 'status_item', 'codigo_posicao', 'corredor', 'observacao',
                   'data_hora', 'id_usuario', 'usuario_nome', 'produto_nome', 'produto_codigo', 'id_corredor',
                   'id_movimentacao_ajuste', 'aplicada_em', 'aplicada_por', 'situacao'}


class CountDB(PhysicalDB):
    def __init__(self):
        super().__init__()
        # [id, item, produto, posicao, lote, usuario, sistema, fisica, status, codigo, corredor, obs, ultima, data, ajuste]
        self.counts = {}
        self.count_schema = True
        self.clock = None


class CountConnection(PhysicalConnection):
    PREFIXES = ('SELECT id_produto FROM itens_estoque', 'SELECT id_item_estoque FROM itens_estoque',
                'SELECT i.id_produto, i.id_posicao', 'SELECT MAX(id_movimentacao)', 'SELECT quantidade FROM itens_estoque')

    def execute(self, q, p=()):
        if ('contagens_inventario' in q or q.startswith(self.PREFIXES)
                or ('FOR SHARE' in q and 'FROM produtos' in q)):
            self.queries.append((q, p))
            return self.count_execute(q, p)
        return super().execute(q, p)

    def count_execute(self, q, p):
        db = self.db
        if q.startswith("SELECT to_regclass('public.contagens_inventario')"):
            self.result = (db.count_schema,)
            return
        if 'contagens_inventario' in q and not db.count_schema:
            raise UndefinedTable()
        if q.startswith('SELECT id_produto FROM itens_estoque'):
            self.result = (db.items[p[0]][1],) if p[0] in db.items else None
        elif q.startswith('SELECT id_item_estoque FROM itens_estoque'):
            self.result = (p[0],) if p[0] in db.items else None
        elif 'FOR SHARE' in q:
            self.acquire('products', p[0])
            self.result = (p[0],) if p[0] in db.products else None
        elif q.startswith('SELECT i.id_produto, i.id_posicao'):
            item = db.items[p[0]]
            position = db.positions[item[2]]
            self.result = (item[1], item[2], item[3], item[4], item[5], position[1], db.corridors[position[2]][1])
        elif q.startswith('SELECT MAX(id_movimentacao)'):
            self.result = (max((m[0] for m in db.movements.values() if m[12] == p[0]), default=None),)
        elif q.startswith('SELECT quantidade FROM itens_estoque'):
            self.result = (db.items[p[0]][4],)
        elif q.startswith('INSERT INTO contagens_inventario'):
            key = max(db.counts, default=0) + 1
            self.write('counts', key, [key, *p, db.clock or datetime.now(timezone.utc), None])
            self.result = (key,)
            if self.fail == 'count_insert': raise RuntimeError('Falha simulada')
        elif q.startswith('SELECT id_produto FROM contagens_inventario'):
            self.result = (db.counts[p[0]][2],) if p[0] in db.counts else None
        elif q.startswith('SELECT id_item_estoque, id_posicao, lote, quantidade_sistema'):
            assert 'FOR UPDATE' in q
            self.acquire('counts', p[0])
            r = db.counts[p[0]]
            self.result = (r[1], r[3], r[4], r[6], r[7], r[12], r[14])
        elif q.startswith('UPDATE contagens_inventario SET id_movimentacao_ajuste'):
            row = list(db.counts[p[1]])
            if row[14] is not None:
                self.result = None
                return
            m = db.movements.get(p[0])
            # Emula contagens_inventario_ajuste_fk / _divergencia_check / _ajuste_key.
            if m is None or (m[12], m[3], m[17], m[18]) != (row[1], 'AJUSTE', row[6], row[7]):
                raise ForeignKeyViolation()
            assert row[6] != row[7]
            if any(r[14] == p[0] for r in db.counts.values()):
                raise Duplicate('contagens_inventario_ajuste_key')
            row[14] = p[0]
            self.write('counts', p[1], row)
            self.result = (p[1],)
            if self.fail == 'link': raise RuntimeError('Falha simulada')
        elif q.startswith('SELECT c.id_contagem'):
            if self.fail == 'count_response': raise RuntimeError('Falha simulada')
            assert q.endswith('ORDER BY c.data_hora DESC, c.id_contagem DESC')
            rows = []
            for r in db.counts.values():
                product, position, move = db.products[r[2]], db.positions[r[3]], db.movements.get(r[14])
                rows.append((r[0], r[1], r[2], r[3], r[4], r[6], r[7], r[7] - r[6], r[8], r[9], r[10], r[11], r[13],
                             r[5], 'Teste', product[1], product[3], position[2], r[14],
                             move[8] if move else None, 'Teste' if move else None))
            offset = 0
            for clause, index in (('c.id_produto = %s', 2), ('c.id_item_estoque = %s', 1), ('c.id_posicao = %s', 3),
                                  ('ps.id_corredor = %s', 17), ('c.lote = %s', 4), ('c.id_contagem = %s', 0)):
                if clause in q:
                    rows = [r for r in rows if r[index] == p[offset]]
                    offset += 1
            for clause, keep in (('c.divergencia <> 0', lambda r: r[7] != 0), ('c.divergencia = 0', lambda r: r[7] == 0),
                                 ('c.id_movimentacao_ajuste IS NOT NULL', lambda r: r[18] is not None),
                                 ('c.id_movimentacao_ajuste IS NULL', lambda r: r[18] is None)):
                if clause in q:
                    rows = [r for r in rows if keep(r)]
            self.result = sorted(rows, key=lambda r: (r[12], r[0]), reverse=True)
        else:
            raise AssertionError('Consulta de contagem não suportada: ' + q)


class CountTests(TestCase):
    def setUp(self):
        self.db = CountDB()
        self.conn = CountConnection(self.db)
        self.counts = ContagemService(self.conn)
        self.addCleanup(self.conn.rollback)

    def move(self, tipo='ENTRADA', amount=5, product=7, position=1, lot='L-1'):
        return MovimentacaoService(CountConnection(self.db)).registrar(product, 23, dict(
            tipo=tipo, id_posicao=position, lote=lot, motivo='Operação',
            **{'novo_saldo_item' if tipo == 'AJUSTE' else 'quantidade': amount}))

    def count(self, fisica, item=1, conn=None, **extra):
        return ContagemService(conn or self.conn).registrar(23, dict(id_item_estoque=item, quantidade_fisica=fisica, **extra))

    def apply(self, count_id=1, conn=None, motivo='Divergência confirmada'):
        return ContagemService(conn or self.conn).aplicar(count_id, 23, {'motivo': motivo})

    def state(self):
        return deepcopy((self.db.products, self.db.items, self.db.movements, self.db.counts))

    def stock_state(self):
        return deepcopy((self.db.products, self.db.items, self.db.movements))

    def invariant(self):
        for key, product in self.db.products.items():
            self.assertEqual(product[6], sum(i[4] for i in self.db.items.values() if i[1] == key))

    # DTOs e filtros
    def test_dto_accepts_zero_max_and_normalizes_observation(self):
        self.assertEqual(ContagemDTO.from_dict({'id_item_estoque': 1, 'quantidade_fisica': 0}).quantidade_fisica, 0)
        self.assertEqual(ContagemDTO.from_dict({'id_item_estoque': 1, 'quantidade_fisica': MAX}).quantidade_fisica, MAX)
        self.assertIsNone(ContagemDTO.from_dict({'id_item_estoque': 1, 'quantidade_fisica': 1, 'observacao': '  '}).observacao)
        self.assertEqual(ContagemDTO.from_dict({'id_item_estoque': 1, 'quantidade_fisica': 1, 'observacao': ' ok '}).observacao, 'ok')

    def test_dto_rejects_invalid_quantities_and_identifiers(self):
        for value in (-1, True, False, '3', 1.5, None, MAX + 1):
            with self.subTest(quantidade=value), self.assertRaises(ValidationError):
                ContagemDTO.from_dict({'id_item_estoque': 1, 'quantidade_fisica': value})
        for value in (0, -1, True, '1', 1.0, None, MAX + 1):
            with self.subTest(item=value), self.assertRaises(ValidationError):
                ContagemDTO.from_dict({'id_item_estoque': value, 'quantidade_fisica': 1})
        for data in (None, [], 'x', {'id_item_estoque': 1}, {'quantidade_fisica': 1},
                     {'id_item_estoque': 1, 'quantidade_fisica': 1, 'observacao': 3},
                     {'id_item_estoque': 1, 'quantidade_fisica': 1, 'observacao': 'a\x00b'}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                ContagemDTO.from_dict(data)

    def test_dto_rejects_extra_and_server_fields(self):
        for field in (*CAMPOS_SERVIDOR, 'extra'):
            with self.subTest(field=field), self.assertRaises(ValidationError) as ctx:
                ContagemDTO.from_dict({'id_item_estoque': 1, 'quantidade_fisica': 1, field: 1})
            if field != 'extra':
                self.assertIn(field, str(ctx.exception))
        for data in ({}, {'motivo': '  '}, {'motivo': 5}, {'motivo': 'ok', 'id_item_estoque': 2},
                     {'motivo': 'ok', 'novo_saldo_item': 3}, {'motivo': 'ok', 'id_movimentacao_ajuste': 1}, None):
            with self.subTest(apply=data), self.assertRaises(ValidationError):
                AplicacaoContagemDTO.from_dict(data)
        self.assertEqual(AplicacaoContagemDTO.from_dict({'motivo': ' Recontagem '}).motivo, 'Recontagem')

    def test_search_dto_filters(self):
        dto = PesquisaContagensDTO.from_dict({'id_produto': '7', 'id_item_estoque': '1', 'id_posicao': '1', 'id_corredor': '1',
                                              'lote': ' L-1 ', 'divergencia': 'com', 'situacao': 'PENDENTE'})
        self.assertEqual((dto.id_produto, dto.lote, dto.divergencia, dto.situacao), (7, 'L-1', 'com', 'PENDENTE'))
        for data in ({'id_produto': '-1'}, {'id_produto': '0'}, {'id_posicao': 'true'}, {'id_corredor': '1.5'},
                     {'id_item_estoque': '٣'}, {'id_produto': '2147483648'}, {'divergencia': 'sim'},
                     {'situacao': 'pendente'}, {'lote': ' '}, {'lote': 'x' * 201}, {'extra': '1'}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                PesquisaContagensDTO.from_dict(data)

    # Registro
    def test_register_captures_backend_snapshot_and_divergence(self):
        self.move(amount=5)
        for fisica, divergencia in ((7, 2), (3, -2), (5, 0), (0, -5)):
            result = self.count(fisica)
            self.assertEqual((result['quantidade_sistema'], result['quantidade_fisica'], result['divergencia']), (5, fisica, divergencia))
        self.assertEqual(set(result), CAMPOS_RESPOSTA)
        self.assertEqual((result['id_usuario'], result['codigo_posicao'], result['corredor'], result['status_item'],
                          result['lote'], result['id_produto'], result['id_corredor']), (23, 'A-01', 'A', 'DISPONIVEL', 'L-1', 7, 1))
        self.assertEqual(self.db.counts[1][12], 1)  # versão: última movimentação do item
        self.assertIsNotNone(datetime.fromisoformat(result['data_hora']).tzinfo)

    def test_register_situations(self):
        self.move(amount=5)
        self.assertEqual(self.count(7)['situacao'], 'PENDENTE')
        zero = self.count(5)
        self.assertEqual((zero['situacao'], zero['divergencia'], zero['id_movimentacao_ajuste']), ('SEM_DIVERGENCIA', 0, None))

    def test_register_does_not_change_stock_or_history(self):
        self.move(amount=5)
        before = self.stock_state()
        self.count(9, observacao='Caixa avariada')
        self.assertEqual(self.stock_state(), before)
        self.assertEqual(self.conn.commits, 1)
        self.assertFalse(any(q.startswith(('UPDATE', 'DELETE')) or 'INSERT INTO movimentacoes' in q for q, _ in self.conn.queries))

    def test_register_zero_item_is_valid_identity(self):
        self.move(amount=5)
        self.move('SAIDA', 5)
        result = self.count(2)
        self.assertEqual((result['quantidade_sistema'], result['divergencia']), (0, 2))

    def test_register_missing_item_is_rejected_without_state(self):
        before = self.state()
        with self.assertRaises(NotFoundError):
            self.count(1, item=999)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.db.items, {})

    def test_register_reserved_status_is_snapshot_only(self):
        self.move(amount=5)
        self.db.items[1][5] = 'BLOQUEADO'
        self.assertEqual(self.count(5)['status_item'], 'BLOQUEADO')
        self.assertEqual(self.db.items[1][5], 'BLOQUEADO')

    def test_snapshots_survive_renames(self):
        self.move(amount=5)
        result = self.count(6)
        CorredorService(self.conn).salvar(23, dict(identificacao='AA', capacidade_maxima=20), 1)
        PosicaoService(self.conn).salvar(23, dict(codigo_posicao='AA-01', id_corredor=1), 1)
        self.assertEqual(self.counts.obter(23, 1), result)

    def test_register_rollback_at_each_stage(self):
        self.move(amount=5)
        for stage in ('count_insert', 'count_response', 'commit'):
            before = self.state()
            conn = CountConnection(self.db, stage)
            with self.subTest(stage=stage), self.assertRaises(RuntimeError):
                self.count(7, conn=conn)
            self.assertEqual(self.state(), before)
            self.assertEqual(conn.rollbacks, 1)

    def test_register_and_apply_revalidate_user(self):
        self.move(amount=5)
        self.db.active = False
        with self.assertRaises(AuthorizationError): self.count(7)
        with self.assertRaises(AuthorizationError): self.counts.listar(23, {})
        self.db.active, self.db.role = True, 'leitor'
        with self.assertRaises(AuthorizationError): self.count(7)
        self.db.role = 'operador'
        self.count(7)
        self.db.role = 'leitor'
        with self.assertRaises(AuthorizationError): self.apply()
        self.assertIsNone(self.db.counts[1][14])

    # Consultas
    def test_queries_filters_and_deterministic_order(self):
        self.move(amount=5)
        self.move(amount=4, product=8, position=3, lot='L-2')
        self.db.clock = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.count(5)                 # 1: item 1, sem divergência
        self.count(6)                 # 2: item 1, pendente
        self.count(1, item=2)         # 3: item 2, pendente
        self.db.clock += timedelta(minutes=1)
        self.count(4, item=2)         # 4: item 2, sem divergência, mais recente
        ids = lambda rows: [r['id_contagem'] for r in rows]
        self.assertEqual(ids(self.counts.listar(23, {})), [4, 3, 2, 1])  # empate: id DESC
        self.apply(2)
        cases = {(): [4, 3, 2, 1], (('id_produto', '7'),): [2, 1], (('id_item_estoque', '2'),): [4, 3],
                 (('id_posicao', '3'),): [4, 3], (('id_corredor', '1'),): [2, 1], (('lote', 'L-2'),): [4, 3],
                 (('divergencia', 'com'),): [3, 2], (('divergencia', 'sem'),): [4, 1],
                 (('situacao', 'PENDENTE'),): [3], (('situacao', 'APLICADA'),): [2],
                 (('situacao', 'SEM_DIVERGENCIA'),): [4, 1], (('id_produto', '8'), ('situacao', 'PENDENTE')): [3]}
        for params, expected in cases.items():
            with self.subTest(params=params):
                self.assertEqual(ids(self.counts.listar(23, dict(params))), expected)
        self.assertEqual(ids(self.counts.listar(23, {'id_produto': '8'}, id_produto=7)), [2, 1])  # pai prevalece
        self.assertEqual(ids(self.counts.listar(23, {}, id_item_estoque=2)), [4, 3])
        self.assertEqual(self.counts.listar(23, {'lote': 'inexistente'}), [])
        for parent in ({'id_produto': 999}, {'id_item_estoque': 999}):
            with self.assertRaises(NotFoundError): self.counts.listar(23, {}, **parent)
        with self.assertRaises(NotFoundError): self.counts.listar(23, {'id_corredor': '999'})
        with self.assertRaises(NotFoundError): self.counts.obter(23, 999)

    def test_query_parameterization(self):
        self.move(amount=5)
        self.counts.listar(23, {'lote': "x' OR 1=1 --"})
        query, params = self.conn.queries[-1]
        self.assertNotIn('OR 1=1', query)
        self.assertIn("x' OR 1=1 --", params)

    # Aplicação
    def test_apply_creates_linked_audited_adjustment(self):
        self.move(amount=5)
        self.move(amount=4, position=2, lot='L-9')
        self.count(8, observacao='Recontado')
        result = self.apply()
        count, move = result['contagem'], result['movimentacao']
        self.assertEqual((count['situacao'], count['id_movimentacao_ajuste'], count['aplicada_por']), ('APLICADA', move['id_movimentacao'], 'Teste'))
        self.assertIsNotNone(count['aplicada_em'])
        self.assertEqual((move['tipo'], move['id_usuario'], move['id_item_estoque'], move['quantidade_item_anterior'],
                          move['quantidade_item_posterior'], move['estoque_anterior'], move['estoque_posterior']),
                         ('AJUSTE', 23, 1, 5, 8, 9, 12))
        self.assertEqual(move['motivo'], 'Contagem #1: Divergência confirmada')
        self.assertEqual(self.db.items[2][4], 4)  # outro lote intacto
        self.invariant()
        self.assertEqual(self.conn.commits, 2)  # um commit no registro e um na aplicação

    def test_apply_negative_and_zero_physical(self):
        self.move(amount=5)
        self.count(0)
        move = self.apply()['movimentacao']
        self.assertEqual((move['quantidade_item_posterior'], move['estoque_posterior'], self.db.items[1][4]), (0, 0, 0))
        self.assertIn(1, self.db.items)  # identidade preservada
        self.invariant()

    def test_apply_twice_conflicts_without_new_adjustment(self):
        self.move(amount=5)
        self.count(7)
        self.apply()
        before = self.state()
        with self.assertRaises(BusinessError) as ctx:
            self.apply()
        self.assertIn('já aplicada', str(ctx.exception))
        self.assertEqual(self.state(), before)

    def test_zero_divergence_cannot_be_applied(self):
        self.move(amount=5)
        self.count(5)
        before = self.state()
        with self.assertRaises(BusinessError) as ctx:
            self.apply()
        self.assertIn('sem divergência', str(ctx.exception))
        self.assertEqual(self.state(), before)

    def test_apply_missing_count(self):
        with self.assertRaises(NotFoundError):
            self.apply(999)

    def test_stale_quantity_rejected(self):
        self.move(amount=5)
        self.count(8)
        self.move('SAIDA', 1)
        before = self.state()
        with self.assertRaises(BusinessError) as ctx:
            self.apply()
        self.assertIn('desatualizada', str(ctx.exception))
        self.assertEqual(self.state(), before)

    def test_stale_version_detected_even_when_quantity_returns(self):
        self.move(amount=5)
        self.count(8)
        self.move(amount=2)
        self.move('SAIDA', 2)
        self.assertEqual(self.db.items[1][4], 5)  # A -> B -> A
        with self.assertRaises(BusinessError):
            self.apply()
        self.assertIsNone(self.db.counts[1][14])

    def test_stale_quantity_without_movement_is_rejected(self):
        # Saldo alterado fora da API (sem movimentação): o token não muda, a quantidade sim.
        self.move(amount=5)
        self.count(8)
        self.db.items[1][4] = self.db.products[7][6] = 6
        before = self.state()
        with self.assertRaises(BusinessError):
            self.apply()
        self.assertEqual(self.state(), before)

    def test_two_counts_same_item_only_first_applies(self):
        self.move(amount=5)
        self.count(7)
        self.count(6)
        self.apply(1)
        with self.assertRaises(BusinessError):
            self.apply(2)
        self.assertEqual((self.db.items[1][4], len(self.db.movements)), (7, 2))
        self.assertEqual(self.counts.obter(23, 2)['situacao'], 'PENDENTE')

    def test_apply_respects_capacity(self):
        self.move(amount=5)
        self.count(21)
        before = self.state()
        with self.assertRaises(BusinessError) as ctx:
            self.apply()
        self.assertIn('Capacidade', str(ctx.exception))
        self.assertEqual(self.state(), before)

    def test_apply_preserves_reserved_and_blocked_status(self):
        for status in ('RESERVADO', 'BLOQUEADO'):
            with self.subTest(status=status):
                self.setUp()
                self.move(amount=5)
                self.db.items[1][5] = status
                self.count(3)
                self.apply()
                self.assertEqual((self.db.items[1][5], self.db.items[1][4]), (status, 3))
                self.invariant()

    def test_apply_does_not_reconcile_preexisting_global_divergence(self):
        self.move(amount=5)
        self.count(7)
        self.db.products[7][6] = 9
        before = self.state()
        with self.assertRaises(BusinessError) as ctx:
            self.apply()
        self.assertIn('Reconciliação', str(ctx.exception))
        self.assertEqual(self.state(), before)

    def test_apply_overflow_rejected(self):
        self.db.corridors[2][2] = MAX
        self.move(amount=10, position=3)
        self.move(amount=1, position=3, lot='L-2')
        self.count(MAX)
        before = self.state()
        with self.assertRaises(BusinessError) as ctx:
            self.apply()
        self.assertIn('limite', str(ctx.exception))
        self.assertEqual(self.state(), before)

    def test_apply_rollback_at_every_stage(self):
        self.move(amount=5)
        self.count(8)
        for stage in ('item', 'product', 'movement', 'context', 'response', 'link', 'count_response', 'commit'):
            before = self.state()
            conn = CountConnection(self.db, stage)
            with self.subTest(stage=stage), self.assertRaises(RuntimeError):
                self.apply(conn=conn)
            self.assertEqual(self.state(), before)
            self.assertEqual(conn.rollbacks, 1)
            self.assertIsNone(self.db.counts[1][14])
        self.apply()
        self.assertEqual(self.db.counts[1][14], max(self.db.movements))
        self.invariant()

    def test_database_rejecting_link_rolls_back_adjustment(self):
        self.move(amount=5)
        self.count(8)
        conn = CountConnection(self.db)
        original = conn.count_execute
        def reject(q, p):
            if q.startswith('UPDATE contagens_inventario'): raise ForeignKeyViolation()
            return original(q, p)
        conn.count_execute = reject
        before = self.state()
        with self.assertRaises(ForeignKeyViolation):
            self.apply(conn=conn)
        self.assertEqual(self.state(), before)
        self.assertEqual(conn.rollbacks, 1)

    def test_application_never_runs_ddl(self):
        self.move(amount=5)
        self.count(8)
        self.apply()
        self.counts.listar(23, {})
        self.db.count_schema = False
        with self.assertRaises(SchemaPendingError): self.counts.listar(23, {})
        self.assertFalse(any(q.lstrip().upper().startswith(('CREATE', 'ALTER', 'DROP')) for q, _ in self.conn.queries))

    def test_schema_pending_blocks_counts_but_keeps_movements(self):
        self.move(amount=5)
        self.db.count_schema = False
        for action in (lambda: self.count(7), lambda: self.apply(), lambda: self.counts.listar(23, {}),
                       lambda: self.counts.obter(23, 1)):
            with self.assertRaises(SchemaPendingError): action()
        self.move(amount=1)
        self.assertEqual(self.db.items[1][4], 6)
        self.assertEqual(self.db.counts, {})

    def test_schema_missing_mid_operation_maps_to_pending(self):
        self.move(amount=5)
        conn = CountConnection(self.db)
        original = conn.count_execute
        def vanish(q, p):
            if q.startswith('INSERT INTO contagens_inventario'): raise UndefinedTable()
            return original(q, p)
        conn.count_execute = vanish
        with self.assertRaises(SchemaPendingError): self.count(7, conn=conn)
        self.assertEqual((conn.rollbacks, self.db.counts), (1, {}))

    # Concorrência (double com locks por linha; não substitui a 2D.2)
    def race(self, first, second):
        self.db.coordinate = 'products'
        outcomes, errors = {}, []
        def run(index, action):
            try: action(CountConnection(self.db)); outcomes[index] = 'ok'
            except BusinessError: outcomes[index] = 'conflict'
            except Exception as error: errors.append(repr(error))
        threads = [threading.Thread(target=run, args=(i, a)) for i, a in enumerate((first, second))]
        try:
            threads[0].start(); self.assertTrue(self.db.first_locked.wait(3))
            threads[1].start(); self.assertTrue(self.db.second_waiting.wait(3))
            self.assertEqual(outcomes, {})
        finally:
            self.db.release_first.set()
            for thread in threads:
                if thread.ident: thread.join(5)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.db.coordinate = None
        return outcomes[0], outcomes[1]

    def test_concurrent_applications_same_count_single_adjustment(self):
        self.move(amount=5)
        self.count(8)
        apply = lambda conn: self.apply(conn=conn)
        self.assertEqual(self.race(apply, apply), ('ok', 'conflict'))
        self.assertEqual(sum(m[3] == 'AJUSTE' for m in self.db.movements.values()), 1)
        self.assertEqual(self.db.items[1][4], 8)
        self.invariant()

    def test_movement_before_concurrent_application_makes_count_stale(self):
        self.move(amount=5)
        self.count(8)
        exit_ = lambda conn: MovimentacaoService(conn).registrar(7, 23, dict(tipo='SAIDA', quantidade=1, id_posicao=1, lote='L-1'))
        self.assertEqual(self.race(exit_, lambda conn: self.apply(conn=conn)), ('ok', 'conflict'))
        self.assertEqual((self.db.items[1][4], self.db.counts[1][14]), (4, None))
        self.invariant()

    def test_application_before_concurrent_movement_is_not_overwritten(self):
        self.move(amount=5)
        self.count(8)
        exit_ = lambda conn: MovimentacaoService(conn).registrar(7, 23, dict(tipo='SAIDA', quantidade=1, id_posicao=1, lote='L-1'))
        self.assertEqual(self.race(lambda conn: self.apply(conn=conn), exit_), ('ok', 'ok'))
        self.assertEqual(self.db.items[1][4], 7)  # 5 -> 8 (ajuste) -> 7 (saída posterior)
        self.invariant()

    def test_registration_waits_for_inflight_movement_snapshot(self):
        self.move(amount=5)
        entry = lambda conn: MovimentacaoService(conn).registrar(7, 23, dict(tipo='ENTRADA', quantidade=3, id_posicao=1, lote='L-1'))
        self.assertEqual(self.race(entry, lambda conn: self.count(8, conn=conn)), ('ok', 'ok'))
        self.assertEqual((self.db.counts[1][6], self.db.counts[1][12]), (8, 2))  # snapshot pós-entrada
        self.assertEqual(self.counts.obter(23, 1)['situacao'], 'SEM_DIVERGENCIA')


class CountHttpTests(TestCase):
    def setUp(self):
        self.db = CountDB()
        self.conn = CountConnection(self.db)
        with patch('app.Connection.get_connection', return_value=self.conn):
            self.app = create_app()
        self.app.session_interface = SecureCookieSessionInterface()
        self.client = self.app.test_client()
        self.addCleanup(self.conn.rollback)

    def login(self, role='admin', persisted=None):
        self.db.role = persisted or role
        with self.client.session_transaction() as s:
            s.update(user_id=23, user_perfil=role, user_admin=role == 'admin', user_ativo=True)

    def seed(self, amount=5):
        MovimentacaoService(CountConnection(self.db)).registrar(7, 23, dict(tipo='ENTRADA', quantidade=amount, id_posicao=1, lote='L-1'))

    def test_routes_require_authentication(self):
        for path in ('/contagens', '/contagens/1', '/produtos/7/contagens', '/itens-estoque/1/contagens'):
            self.assertEqual(self.client.get(path).status_code, 401)
        for path in ('/contagens', '/contagens/1/aplicar'):
            self.assertEqual(self.client.post(path, json={}).status_code, 401)
        self.assertEqual(self.db.counts, {})

    def test_roundtrip_register_apply_and_conflicts(self):
        self.seed()
        self.login('operador')
        created = self.client.post('/contagens', json={'id_item_estoque': 1, 'quantidade_fisica': 8})
        self.assertEqual(created.status_code, 201)
        self.assertEqual((created.json['quantidade_sistema'], created.json['divergencia'], created.json['id_usuario']), (5, 3, 23))
        self.assertEqual(self.client.get('/contagens/1').json, created.json)
        self.assertEqual(len(self.client.get('/produtos/7/contagens?id_produto=8').json), 1)
        self.assertEqual(len(self.client.get('/itens-estoque/1/contagens').json), 1)
        self.login('gestor')  # 2E (D3): aplicação exige GESTOR/ADMINISTRADOR
        applied = self.client.post('/contagens/1/aplicar', json={'motivo': 'Recontagem confirmada'})
        self.assertEqual(applied.status_code, 201)
        self.assertEqual((applied.json['contagem']['situacao'], applied.json['movimentacao']['tipo']), ('APLICADA', 'AJUSTE'))
        self.assertEqual(self.client.post('/contagens/1/aplicar', json={'motivo': 'De novo'}).status_code, 409)
        self.assertEqual(self.client.get('/produtos/7/itens-estoque').json[0]['quantidade'], 8)
        self.assertEqual(self.client.post('/contagens', json={'id_item_estoque': 1, 'quantidade_fisica': 8}).json['situacao'], 'SEM_DIVERGENCIA')
        self.assertEqual(self.client.post('/contagens/2/aplicar', json={'motivo': 'x'}).status_code, 409)
        self.assertEqual(self.client.post('/contagens/999/aplicar', json={'motivo': 'x'}).status_code, 404)

    def test_validation_and_not_found(self):
        self.seed()
        self.login()
        for body in ({'id_item_estoque': 1, 'quantidade_fisica': -1}, {'id_item_estoque': 1, 'quantidade_fisica': True},
                     {'id_item_estoque': 1, 'quantidade_fisica': 1, 'quantidade_sistema': 1},
                     {'id_item_estoque': 1, 'quantidade_fisica': 1, 'id_usuario': 99}, None):
            self.assertEqual(self.client.post('/contagens', json=body).status_code, 400)
        self.assertEqual(self.client.post('/contagens', data='{', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post('/contagens', json={'id_item_estoque': 999, 'quantidade_fisica': 1}).status_code, 404)
        self.client.post('/contagens', json={'id_item_estoque': 1, 'quantidade_fisica': 7})
        self.assertEqual(self.client.post('/contagens/1/aplicar', json={}).status_code, 400)
        self.assertEqual(self.client.post('/contagens/1/aplicar', json={'motivo': 'ok', 'id_item_estoque': 2}).status_code, 400)
        self.assertEqual(self.client.get('/contagens?situacao=x').status_code, 400)
        self.assertEqual(self.client.get('/contagens/999').status_code, 404)
        self.assertEqual(self.client.get('/produtos/999/contagens').status_code, 404)
        self.assertEqual(self.client.get('/itens-estoque/999/contagens').status_code, 404)
        self.assertIsNone(self.db.counts[1][14])

    def test_permissions(self):
        self.seed()
        self.login('operador')
        self.client.post('/contagens', json={'id_item_estoque': 1, 'quantidade_fisica': 7})
        # 2E (D6): perfil desconhecido não lê, não registra e não aplica.
        self.login('leitor')
        self.assertEqual(self.client.get('/contagens').status_code, 403)
        self.assertEqual(self.client.post('/contagens', json={'id_item_estoque': 1, 'quantidade_fisica': 7}).status_code, 403)
        self.assertEqual(self.client.post('/contagens/1/aplicar', json={'motivo': 'x'}).status_code, 403)
        # Leitura preservada para perfis reconhecidos; AUDITOR/OPERADOR não aplicam (D3).
        for role in ('auditor', 'operador'):
            self.login(role)
            self.assertEqual(self.client.get('/contagens').status_code, 200)
            self.assertEqual(self.client.post('/contagens/1/aplicar', json={'motivo': 'x'}).status_code, 403)
        self.login('gestor', persisted='leitor')  # sessão antiga com perfil rebaixado
        self.assertEqual(self.client.post('/contagens/1/aplicar', json={'motivo': 'x'}).status_code, 403)
        self.login('supervisor')
        self.db.active = False
        self.assertEqual(self.client.get('/contagens').status_code, 403)
        self.assertEqual((len(self.db.counts), self.db.counts[1][14]), (1, None))

    def test_counts_are_immutable_through_api(self):
        self.seed()
        self.login()
        self.client.post('/contagens', json={'id_item_estoque': 1, 'quantidade_fisica': 7})
        before = deepcopy(self.db.counts)
        for method in ('put', 'patch', 'delete'):
            self.assertEqual(getattr(self.client, method)('/contagens/1', json={'quantidade_fisica': 1}).status_code, 405)
        self.assertEqual(self.client.post('/contagens/1', json={}).status_code, 405)
        self.assertEqual(self.db.counts, before)

    def test_schema_pending_returns_503_and_baseline_keeps_working(self):
        self.seed()
        self.login()
        self.db.count_schema = False
        for method, path in (('get', '/contagens'), ('get', '/contagens/1'), ('get', '/produtos/7/contagens'),
                             ('post', '/contagens'), ('post', '/contagens/1/aplicar')):
            response = getattr(self.client, method)(path, json={'id_item_estoque': 1, 'quantidade_fisica': 1} if path == '/contagens' else {'motivo': 'x'})
            self.assertEqual(response.status_code, 503, path)
            self.assertIn('Etapa 2D', response.json['message'])
        self.assertEqual(self.client.get('/itens-estoque').status_code, 200)
        self.assertEqual(self.client.post('/produtos/7/movimentacoes', json=dict(tipo='AJUSTE', novo_saldo_item=3, id_posicao=1,
                                                                                 lote='L-1', motivo='Ajuste manual 2C')).status_code, 201)

    def test_internal_errors_do_not_leak_details(self):
        self.seed()
        self.login()
        self.client.post('/contagens', json={'id_item_estoque': 1, 'quantidade_fisica': 7})
        self.conn.fail = 'link'
        response = self.client.post('/contagens/1/aplicar', json={'motivo': 'x'})
        self.assertEqual((response.status_code, response.json), (500, {'message': 'Erro interno do servidor'}))
        self.assertNotIn('Falha simulada', response.get_data(as_text=True))
        self.assertIsNone(self.db.counts[1][14])
        self.assertEqual(self.db.items[1][4], 5)
