"""Banco em memória com transações/lock simulados; nunca conecta ao PostgreSQL."""
from copy import deepcopy
from datetime import datetime, timezone
import threading
from unittest import TestCase
from unittest.mock import MagicMock, patch

from flask.sessions import SecureCookieSessionInterface
from psycopg2.errors import ForeignKeyViolation, RestrictViolation, UndefinedTable

from app import create_app
from core.errors import AuthorizationError, BusinessError, NotFoundError, SchemaPendingError, ValidationError
from repository.movimentacao_repository import MovimentacaoRepository
from repository.produto_repository import ProdutoRepository
from schemas.movimentacao_dto import MovimentacaoDTO
from services.movimentacao_service import MovimentacaoService
from test_produtos import PAYLOAD, UPDATE_PAYLOAD


class MemoryDatabase:
    def __init__(self, stock=10):
        self.products = {7: [7, 'Caixa', 'Embalagem', 'CX-01', 'A-1', None, stock, 0]}
        self.movements = []
        self.active = True
        self.role = 'admin'
        self.lock = threading.Lock()
        self.first_locked = threading.Event()
        self.second_waiting = threading.Event()
        self.release_first = threading.Event()
        self.coordinate = False


class MemoryConnection:
    def __init__(self, db, fail=None):
        self.db, self.fail = db, fail
        self.queries = []
        self.held = False
        self.snapshot = None
        self.result = None
        self.commits = self.rollbacks = 0

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def begin(self):
        if not self.held:
            self.db.lock.acquire()
            self.held = True
            self.snapshot = deepcopy((self.db.products, self.db.movements))

    def execute(self, query, params=()):
        self.queries.append((query, params))
        if query.startswith('SELECT to_regclass'):
            self.result = (False,)  # Baseline 2B, antes da instalação manual 2C.
        elif query.startswith('SELECT id_movimentacao FROM movimentacoes LIMIT 0'):
            if self.fail == 'schema':
                raise UndefinedTable()
        elif query.startswith('SELECT ativo, perfil'):
            self.result = (self.db.active, self.db.role)
        elif query.startswith('SELECT perfil'):
            self.result = (self.db.role,)
        elif query.startswith('SELECT estoque'):
            if 'FOR UPDATE' not in query:
                raise AssertionError('Leitura de saldo precisa do lock')
            if self.db.coordinate:
                if self.db.first_locked.is_set():
                    self.db.second_waiting.set()
                self.begin()
                if not self.db.first_locked.is_set():
                    self.db.first_locked.set()
                    if not self.db.release_first.wait(5):
                        raise AssertionError('Timeout de coordenação')
            else:
                self.begin()
            row = self.db.products.get(params[0])
            self.result = (row[6],) if row else None
        elif query.startswith('UPDATE produtos SET estoque'):
            if self.fail == 'update':
                raise RuntimeError('update simulado')
            self.db.products[params[1]][6] = params[0]
        elif query.startswith('INSERT INTO movimentacoes'):
            if self.fail == 'insert':
                raise RuntimeError('insert simulado')
            product = self.db.products[params[0]]
            row = (len(self.db.movements) + 1, *params, datetime.now(timezone.utc), product[1], product[3], 'Teste Local')
            self.db.movements.append(row)
            self.result = (row[0],)
        elif query.startswith('INSERT INTO produtos'):
            self.begin()
            self.db.products[8] = [8, *params]
            self.result = tuple(self.db.products[8])
        elif query.startswith('SELECT id_produto FROM produtos'):
            self.result = (params[0],) if params[0] in self.db.products else None
        elif 'FROM movimentacoes m' in query:
            rows = list(reversed(self.db.movements))
            if 'WHERE m.id_movimentacao' in query:
                rows = [r for r in rows if r[0] == params[0]]
            elif 'WHERE m.id_produto' in query:
                rows = [r for r in rows if r[1] == params[0]]
            self.result = rows
        else:
            raise AssertionError('Consulta não suportada pelo double')

    def fetchone(self):
        if isinstance(self.result, list):
            return self.result[0] if self.result else None
        return self.result

    def fetchall(self):
        return self.result

    def commit(self):
        if self.fail == 'commit':
            raise RuntimeError('commit simulado')
        self.commits += 1
        self.finish()

    def rollback(self):
        self.rollbacks += 1
        if self.snapshot is not None:
            self.db.products, self.db.movements = deepcopy(self.snapshot)
        self.finish()

    def finish(self):
        self.snapshot = None
        if self.held:
            self.held = False
            self.db.lock.release()


class MovementDTOTests(TestCase):
    def test_valid_contracts_and_trimming(self):
        self.assertEqual(MovimentacaoDTO.from_dict({'tipo': 'ENTRADA', 'quantidade': 5}).motivo, None)
        self.assertEqual(MovimentacaoDTO.from_dict({'tipo': 'AJUSTE', 'novo_estoque': 0, 'motivo': ' Contagem '}).motivo, 'Contagem')

    def test_invalid_types_quantities_and_reasons(self):
        cases = [None, [], {}, {'tipo': []}, {'tipo': 'entrada', 'quantidade': 1}]
        for tipo in ('ENTRADA', 'SAIDA', 'AJUSTE'):
            key = 'novo_estoque' if tipo == 'AJUSTE' else 'quantidade'
            for value in (True, False, -1, '3', 1.5, None, 2147483648):
                cases.append({'tipo': tipo, key: value, 'motivo': 'Contagem'})
        cases += [{'tipo': t, 'quantidade': 0} for t in ('ENTRADA', 'SAIDA')]
        cases += [{'tipo': 'AJUSTE', 'novo_estoque': 8, 'motivo': m} for m in (None, '', '  ', True, 'A\x00B')]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValidationError):
                MovimentacaoDTO.from_dict(case)

    def test_rejects_ambiguous_adjustment_and_server_fields(self):
        for field in ('id_produto', 'id_usuario', 'estoque_anterior', 'estoque_posterior', 'data_hora', 'id_movimentacao', 'novo_estoque'):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                MovimentacaoDTO.from_dict({'tipo': 'ENTRADA', 'quantidade': 1, field: 42})
        for data in ({'tipo': 'AJUSTE', 'quantidade': 8, 'motivo': 'Contagem'},
                     {'tipo': 'AJUSTE', 'novo_estoque': 8, 'quantidade': 8, 'motivo': 'Contagem'}):
            with self.assertRaises(ValidationError):
                MovimentacaoDTO.from_dict(data)


class MovementTransactionTests(TestCase):
    def setUp(self):
        self.db = MemoryDatabase()
        self.conn = MemoryConnection(self.db)
        self.service = MovimentacaoService(self.conn)

    def move(self, tipo, amount, motivo=None, product=7):
        return self.service.registrar(product, 23, {'tipo': tipo,
            'novo_estoque' if tipo == 'AJUSTE' else 'quantidade': amount, 'motivo': motivo})

    def test_entry_balance_and_history(self):
        result = self.move('ENTRADA', 5)
        self.assertEqual((result['estoque_anterior'], result['estoque_posterior']), (10, 15))
        self.assertEqual(self.db.products[7][6], 15)
        self.assertEqual(result['id_usuario'], 23)
        self.assertEqual(result['quantidade'], 5)
        self.assertTrue(result['data_hora'].endswith('+00:00'))
        self.assertEqual(len(self.db.movements), 1)
        self.assertEqual(self.conn.commits, 1)

    def test_exit(self):
        self.move('SAIDA', 4)
        self.assertEqual(self.db.products[7][6], 6)

    def test_insufficient_balance_no_changes(self):
        self.db.products[7][6] = 3
        with self.assertRaises(BusinessError):
            self.move('SAIDA', 5)
        self.assertEqual(self.db.products[7][6], 3)
        self.assertEqual(self.db.movements, [])
        self.assertEqual(self.conn.rollbacks, 1)

    def test_adjustment_down_up_and_equal(self):
        for target in (8, 20, 20):
            result = self.move('AJUSTE', target, 'Contagem física')
            self.assertEqual(self.db.products[7][6], target)
            self.assertEqual(result['motivo'], 'Contagem física')
            self.assertEqual(result['quantidade'], target)

    def test_adjustment_zero(self):
        self.move('AJUSTE', 0, 'Contagem')
        self.assertEqual(self.db.products[7][6], 0)

    def test_missing_product(self):
        with self.assertRaises(NotFoundError):
            self.move('ENTRADA', 1, product=999)
        self.assertEqual(self.db.movements, [])

    def test_insert_failure_rolls_back_updated_stock(self):
        self.conn.fail = 'insert'
        with self.assertRaises(RuntimeError):
            self.move('ENTRADA', 5)
        self.assertTrue(any(q.startswith('UPDATE produtos') for q, _ in self.conn.queries))
        self.assertEqual(self.db.products[7][6], 10)
        self.assertEqual(self.db.movements, [])
        self.assertEqual((self.conn.commits, self.conn.rollbacks), (0, 1))

    def test_update_failure_creates_no_history(self):
        self.conn.fail = 'update'
        with self.assertRaises(RuntimeError):
            self.move('ENTRADA', 5)
        self.assertFalse(any(q.startswith('INSERT INTO movimentacoes') for q, _ in self.conn.queries))
        self.assertEqual((self.db.products[7][6], self.db.movements), (10, []))

    def test_commit_failure_rolls_back_both_writes(self):
        self.conn.fail = 'commit'
        with self.assertRaises(RuntimeError):
            self.move('ENTRADA', 5)
        self.assertEqual((self.db.products[7][6], self.db.movements), (10, []))

    def test_inactive_persisted_user_and_revoked_role(self):
        self.db.active = False
        with self.assertRaises(AuthorizationError):
            self.move('ENTRADA', 1)
        self.db.active, self.db.role = True, 'leitor'
        with self.assertRaises(AuthorizationError):
            self.move('ENTRADA', 1)
        self.assertEqual(self.db.movements, [])

    def test_overflow_rejected(self):
        self.db.products[7][6] = 2147483647
        with self.assertRaises(BusinessError):
            self.move('ENTRADA', 1)
        self.assertEqual(self.db.movements, [])

    def test_missing_schema_is_explicit_and_rolls_back(self):
        self.conn.fail = 'schema'
        with self.assertRaises(SchemaPendingError):
            self.move('ENTRADA', 1)
        self.assertEqual((self.conn.commits, self.conn.rollbacks), (0, 1))

    def test_history_global_product_id_and_not_found(self):
        first = self.move('ENTRADA', 5)
        second = self.move('SAIDA', 2)
        self.assertEqual(self.service.listar(23), [second, first])
        self.assertEqual(self.service.listar(23, 7), [second, first])
        self.assertEqual(self.service.obter(first['id_movimentacao'], 23), first)
        with self.assertRaises(NotFoundError):
            self.service.obter(999, 23)
        with self.assertRaises(NotFoundError):
            self.service.listar(23, 999)

    def test_two_concurrent_exits_serialize_after_lock(self):
        self.db.products[7][6] = 5
        self.db.coordinate = True
        results = []
        connections = [MemoryConnection(self.db), MemoryConnection(self.db)]
        def withdraw(conn):
            try:
                MovimentacaoService(conn).registrar(7, 23, {'tipo': 'SAIDA', 'quantidade': 4})
                results.append('accepted')
            except BusinessError:
                results.append('insufficient')
        threads = [threading.Thread(target=withdraw, args=(c,)) for c in connections]
        try:
            threads[0].start()
            self.assertTrue(self.db.first_locked.wait(3))
            threads[1].start()
            self.assertTrue(self.db.second_waiting.wait(3))
            self.assertEqual(results, [])  # segunda operação espera a primeira liberar o lock
        finally:
            self.db.release_first.set()
            for thread in threads:
                if thread.ident:
                    thread.join(5)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertCountEqual(results, ['accepted', 'insufficient'])
        self.assertEqual(self.db.products[7][6], 1)
        self.assertEqual(len(self.db.movements), 1)

    def test_initial_positive_stock_is_atomic_entry(self):
        product = ProdutoRepository(self.conn).registrar_produto(**PAYLOAD, usuario_id=23)
        self.assertEqual(product.estoque, 10)
        self.assertEqual(self.db.products[8][6], 10)
        self.assertEqual(self.db.movements[0][1:8], (8, 23, 'ENTRADA', 10, 0, 10, 'Estoque inicial'))
        self.assertEqual(self.conn.commits, 1)

    def test_initial_zero_has_no_movement(self):
        product = ProdutoRepository(self.conn).registrar_produto(**{**PAYLOAD, 'estoque': 0}, usuario_id=23)
        self.assertEqual(product.estoque, 0)
        self.assertEqual(self.db.movements, [])

    def test_initial_failure_removes_product_and_history(self):
        for fail in ('insert', 'update', 'commit', 'schema'):
            with self.subTest(fail=fail):
                conn = MemoryConnection(self.db, fail=fail)
                with self.assertRaises((RuntimeError, SchemaPendingError)):
                    ProdutoRepository(conn).registrar_produto(**PAYLOAD, usuario_id=23)
                self.assertNotIn(8, self.db.products)
                self.assertEqual(self.db.movements, [])

    def test_initial_zero_requires_schema_too(self):
        self.conn.fail = 'schema'
        with self.assertRaises(SchemaPendingError):
            ProdutoRepository(self.conn).registrar_produto(**{**PAYLOAD, 'estoque': 0}, usuario_id=23)
        self.assertNotIn(8, self.db.products)


class MovementHttpTests(TestCase):
    def setUp(self):
        # Usa todas as camadas reais sobre conexão transacional simulada.
        self.db = MemoryDatabase()
        self.conn = MemoryConnection(self.db)
        with patch('app.Connection.get_connection', return_value=self.conn):
            self.app = create_app()
        self.app.session_interface = SecureCookieSessionInterface()
        self.client = self.app.test_client()

    def login(self, role='admin', active=True):
        # 2E: o estado persistido decide; flags da sessão não concedem nem negam acesso.
        self.db.role, self.db.active = role, active
        with self.client.session_transaction() as session:
            session.update(user_id=23, user_perfil=role, user_admin=role == 'admin', user_ativo=active)

    def post(self, **payload):
        return self.client.post('/produtos/7/movimentacoes', json=payload or {'tipo': 'ENTRADA', 'quantidade': 5})

    def test_all_endpoints_need_authentication(self):
        for route in ('/movimentacoes', '/movimentacoes/1', '/produtos/7/movimentacoes'):
            self.assertEqual(self.client.get(route).status_code, 401)
        self.assertEqual(self.post().status_code, 401)

    def test_admin_supervisor_operator_can_register(self):
        for role in ('admin', 'administrador', 'supervisor', 'operador'):
            self.login(role)
            response = self.post()
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json['id_usuario'], 23)
            self.assertNotIn('senha', response.json)

    def test_inactive_session_and_persisted_revocation(self):
        self.login(active=False)
        self.assertEqual(self.post().status_code, 403)
        self.login()
        self.db.active = False
        self.assertEqual(self.post().status_code, 403)
        # 2E: a negativa por inatividade revoga a sessão; a requisição seguinte é 401.
        self.assertEqual(self.client.get('/movimentacoes').status_code, 401)
        self.login(active=False)
        self.assertEqual(self.client.get('/movimentacoes').status_code, 403)
        self.assertEqual(self.db.movements, [])

    def test_unrecognized_role_cannot_write(self):
        self.login('leitor')
        self.assertEqual(self.post().status_code, 403)

    def test_read_routes_and_missing(self):
        self.login()
        self.post()
        self.assertEqual(len(self.client.get('/movimentacoes').json), 1)
        self.assertEqual(len(self.client.get('/produtos/7/movimentacoes').json), 1)
        self.assertEqual(self.client.get('/movimentacoes/1').json['id_usuario'], 23)
        for path in ('/movimentacoes/999', '/produtos/999/movimentacoes'):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post('/produtos/999/movimentacoes', json={'tipo':'ENTRADA', 'quantidade':1}).status_code, 404)

    def test_conflict_and_validation(self):
        self.login()
        self.assertEqual(self.post(tipo='SAIDA', quantidade=11).status_code, 409)
        self.assertEqual(self.post(tipo='AJUSTE', novo_estoque=2).status_code, 400)
        self.assertEqual(self.post(tipo='ENTRADA', quantidade=1, id_usuario=99).status_code, 400)
        self.assertEqual(self.db.movements, [])

    def test_malformed_json(self):
        self.login()
        for body, content_type in (('{', 'application/json'), ('[]', 'application/json'), ('null', 'application/json'), ('text', 'text/plain')):
            self.assertEqual(self.client.post('/produtos/7/movimentacoes', data=body, content_type=content_type).status_code, 400)

    def test_history_is_immutable(self):
        self.login()
        self.post()
        before = deepcopy(self.db.movements)
        for method in ('put', 'patch', 'delete'):
            for path in ('/movimentacoes', '/movimentacoes/1', '/produtos/7/movimentacoes'):
                self.assertEqual(getattr(self.client, method)(path, json={}).status_code, 405)
        self.assertEqual(self.db.movements, before)

    def test_product_put_rejects_stock_even_if_unchanged(self):
        self.login()
        for stock in (10, 0, 20):
            response = self.client.put('/produtos/7', json={**PAYLOAD, 'estoque': stock})
            self.assertEqual(response.status_code, 400)
            self.assertIn('Movimentações', response.json['message'])
        self.assertEqual(self.db.products[7][6], 10)
        # 2E: só a leitura de autorização (perfil persistido) ocorre; nenhuma consulta de produto.
        self.assertTrue(self.conn.queries)
        self.assertTrue(all(q.startswith('SELECT ativo, perfil FROM usuarios') for q, _ in self.conn.queries))

    def test_missing_schema_503_and_unexpected_failure_500(self):
        self.login()
        self.conn.fail = 'schema'
        self.assertEqual(self.post().status_code, 503)
        self.assertEqual(self.client.post('/produtos', json=PAYLOAD).status_code, 503)
        self.conn.fail = 'insert'
        response = self.post()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json, {'message': 'Erro interno do servidor'})

    def test_product_fk_returns_conflict_and_rolls_back(self):
        self.login()
        for violation in (ForeignKeyViolation, RestrictViolation):
            with self.subTest(violation=violation.__name__):
                before = self.conn.rollbacks
                original = self.conn.execute
                def falhar(query, params=(), violation=violation):
                    # 2E: autorização persistida segue normal; a exclusão viola a FK.
                    if query.startswith('DELETE'):
                        raise violation()
                    return original(query, params)
                with patch.object(self.conn, 'execute', side_effect=falhar):
                    result = self.client.delete('/produtos/7')
                self.assertEqual(result.status_code, 409)
                self.assertIn('histórico', result.json['message'])
                self.assertEqual(self.conn.rollbacks, before + 1)

    def test_product_update_query_never_writes_stock(self):
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchone.side_effect = [(True, 'admin'), self.db.products[7]]
        ProdutoRepository(conn).atualizar_produto(7, **UPDATE_PAYLOAD, usuario_id=23)
        query = conn.cursor.return_value.__enter__.return_value.execute.call_args.args[0]
        self.assertNotIn('estoque', query.split('RETURNING')[0])
