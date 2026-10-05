"""Produto: testes isolados com doubles. Nenhuma consulta PostgreSQL real."""
from dataclasses import asdict
from datetime import date
import os
import sys
from unittest import TestCase
from unittest.mock import MagicMock, patch

from flask.sessions import SecureCookieSessionInterface
from psycopg2.errors import UniqueViolation

from app import create_app
from bootstrap_admin import database_config
from core.errors import BusinessError, ValidationError
from models.produtos import Produto
from repository.produto_repository import ProdutoRepository
from repository.usuario_repository import UsuarioRepository
from schemas.produto_dto import ProdutoDTO
from services.produto_service import ProdutoService


PAYLOAD = dict(nome='Caixa', categoria='Embalagem', codigo='CX-01', localizacao='A-1',
               validade=None, estoque=10, quantidade_min=0)
UPDATE_PAYLOAD = {k: v for k, v in PAYLOAD.items() if k != 'estoque'}
ROW = (7, 'Caixa', 'Embalagem', 'CX-01', 'A-1', None, 10, 0)


class ProdutoDTOTests(TestCase):
    def test_optional_date_missing_or_null(self):
        payload = {k: v for k, v in PAYLOAD.items() if k != 'validade'}
        self.assertIsNone(ProdutoDTO.from_dict(payload).validade)
        self.assertIsNone(ProdutoDTO.from_dict(PAYLOAD).validade)

    def test_trims_text_and_preserves_sku_case(self):
        dto = ProdutoDTO.from_dict({**PAYLOAD, 'codigo': '  Sku-01  ', 'localizacao': ' A 1 '})
        self.assertEqual(dto.codigo, 'Sku-01')
        self.assertEqual(dto.localizacao, 'A 1')

    def test_requires_all_non_optional_fields(self):
        for key in ('nome', 'categoria', 'codigo', 'localizacao', 'estoque', 'quantidade_min'):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                ProdutoDTO.from_dict({k: v for k, v in PAYLOAD.items() if k != key})

    def test_invalid_payloads_and_null_bytes(self):
        for data in (None, [], 'produto', True, 3, {**PAYLOAD, 'codigo': 'A\x00B'}):
            with self.subTest(kind=type(data).__name__), self.assertRaises(ValidationError):
                ProdutoDTO.from_dict(data)

    def test_date_format_and_type(self):
        for value in ('2026-W01-1', '20260926', '2026-02-30', '2026-01-01T00:00:00',
                      '', ' ', False, [], {}, 42):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ProdutoDTO.from_dict({**PAYLOAD, 'validade': value})
        self.assertEqual(ProdutoDTO.from_dict({**PAYLOAD, 'validade': '2000-02-29'}).validade,
                         date(2000, 2, 29))


class ProdutoRepositoryTests(TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.cursor = self.conn.cursor.return_value.__enter__.return_value
        self.repo = ProdutoRepository(self.conn)

    def test_list_maps_explicit_columns_and_empty_result(self):
        self.cursor.fetchall.return_value = [ROW]
        self.assertEqual(self.repo.get_produtos(), [Produto(*ROW)])
        query = self.cursor.execute.call_args.args[0]
        self.assertNotIn('*', query)
        self.assertIn('FROM produtos', query)
        self.cursor.fetchall.return_value = []
        self.assertEqual(self.repo.get_produtos(), [])

    def test_get_parameterized_and_missing(self):
        self.cursor.fetchone.return_value = ROW
        self.assertEqual(self.repo.get_produto_por_id(7), Produto(*ROW))
        self.assertEqual(self.cursor.execute.call_args.args[1], (7,))
        self.cursor.fetchone.return_value = None
        self.assertIsNone(self.repo.get_produto_por_id(8))

    def test_insert_parameterized_sku_and_commit(self):
        self.cursor.fetchone.side_effect = [(True, 'admin'), ROW]
        payload = {**PAYLOAD, 'estoque': 0, 'codigo': "SKU'); SELECT 1; --"}
        self.assertEqual(self.repo.registrar_produto(**payload, usuario_id=1), Produto(*ROW))
        query, params = self.cursor.execute.call_args.args
        self.assertNotIn(payload['codigo'], query)
        self.assertEqual(params[2], payload['codigo'])
        self.conn.commit.assert_called_once()

    def test_update_and_missing_return(self):
        # 2E: a primeira leitura é a revalidação do perfil persistido na mesma transação.
        self.cursor.fetchone.side_effect = [(True, 'admin'), ROW]
        self.assertEqual(self.repo.atualizar_produto(7, **UPDATE_PAYLOAD, usuario_id=1), Produto(*ROW))
        query, params = self.cursor.execute.call_args.args
        self.assertEqual(params[-1], 7)
        self.assertIn('WHERE id_produto = %s', query)
        self.cursor.fetchone.side_effect = [(True, 'admin'), None]
        self.assertIsNone(self.repo.atualizar_produto(8, **UPDATE_PAYLOAD, usuario_id=1))

    def test_delete_returns_existence(self):
        for row, expected in (((7,), True), (None, False)):
            self.cursor.fetchone.side_effect = [(True, 'admin'), row]
            self.assertIs(self.repo.deletar_produto(7, usuario_id=1), expected)
        self.cursor.execute.assert_called_with(
            'DELETE FROM produtos WHERE id_produto = %s RETURNING id_produto', (7,))

    def test_all_operations_rollback_and_propagate(self):
        operations = (lambda: self.repo.get_produtos(), lambda: self.repo.get_produto_por_id(7),
                      lambda: self.repo.registrar_produto(**PAYLOAD, usuario_id=1),
                      lambda: self.repo.atualizar_produto(7, **UPDATE_PAYLOAD, usuario_id=1), lambda: self.repo.deletar_produto(7, usuario_id=1))
        for operation in operations:
            self.conn.reset_mock()
            self.cursor.execute.side_effect = RuntimeError('internal')
            with self.assertRaises(RuntimeError):
                operation()
            self.conn.rollback.assert_called_once()
            self.conn.commit.assert_not_called()

    def test_commit_failure_rolls_back(self):
        self.cursor.fetchone.return_value = ROW
        self.cursor.fetchone.side_effect = [(True, 'admin'), ROW]
        self.conn.commit.side_effect = RuntimeError()
        with self.assertRaises(RuntimeError):
            self.repo.registrar_produto(**{**PAYLOAD, 'estoque': 0}, usuario_id=1)
        self.conn.rollback.assert_called_once()

    def test_only_sku_constraint_is_conflict(self):
        class Duplicate(UniqueViolation):
            @property
            def diag(self):
                return type('Diag', (), {'constraint_name': self.constraint})()
        for constraint, expected in (('produtos_codigo_key', BusinessError), ('other_key', UniqueViolation)):
            error = Duplicate()
            error.constraint = constraint
            self.cursor.execute.side_effect = error
            with self.assertRaises(expected):
                self.repo.registrar_produto(**PAYLOAD, usuario_id=1)
        self.assertEqual(self.conn.rollback.call_count, 2)


class ProdutoServiceTests(TestCase):
    def setUp(self):
        self.service = ProdutoService(MagicMock())
        self.repo = self.service.produto_repository = MagicMock()

    def test_list_and_get_serialize_dates_and_ids(self):
        produto = Produto(*ROW)
        produto.validade = date(2020, 1, 2)
        self.repo.get_produtos.return_value = [produto]
        self.repo.get_produto_por_id.return_value = produto
        expected = {**asdict(produto), 'validade': '2020-01-02'}
        self.assertEqual(self.service.listar_produtos(), [expected])
        self.assertEqual(self.service.obter_produto_por_id(7), expected)

    def test_empty_and_missing(self):
        self.repo.get_produtos.return_value = []
        self.repo.get_produto_por_id.return_value = None
        self.assertEqual(self.service.listar_produtos(), [])
        self.assertIsNone(self.service.obter_produto_por_id(7))

    def test_create_and_update_pass_validated_data(self):
        self.repo.registrar_produto.return_value = Produto(*ROW)
        self.repo.atualizar_produto.return_value = Produto(*ROW)
        self.assertEqual(self.service.registrar_produto(PAYLOAD, 1), asdict(Produto(*ROW)))
        self.repo.registrar_produto.assert_called_once_with(**PAYLOAD, usuario_id=1)
        self.assertEqual(self.service.atualizar_produto(7, UPDATE_PAYLOAD, 1), asdict(Produto(*ROW)))
        self.repo.atualizar_produto.assert_called_once_with(7, **UPDATE_PAYLOAD, usuario_id=1)

    def test_invalid_data_never_reaches_repository(self):
        for method in (lambda data: self.service.registrar_produto(data, 1), lambda data: self.service.atualizar_produto(7, data, 1)):
            with self.assertRaises(ValidationError):
                method({**PAYLOAD, 'estoque': -1})
        self.assertEqual(self.repo.mock_calls, [])

    def test_delete_missing_update_and_internal_error(self):
        self.repo.deletar_produto.return_value = False
        self.assertFalse(self.service.deletar_produto(7, 1))
        self.repo.atualizar_produto.return_value = None
        self.assertIsNone(self.service.atualizar_produto(7, UPDATE_PAYLOAD, 1))
        self.repo.get_produtos.side_effect = RuntimeError()
        with self.assertRaises(RuntimeError):
            self.service.listar_produtos()


class ProdutoHttpTests(TestCase):
    def setUp(self):
        # Só nesta suíte: sessão isolada. Redis real é exercitado na suíte opt-in.
        self.app = create_app()
        self.app.session_interface = SecureCookieSessionInterface()
        self.client = self.app.test_client()
        # O blueprint já possui a instância: patch nos métodos da classe real.
        self.patches = {}
        for method in ('get_produtos', 'get_produto_por_id', 'registrar_produto',
                       'atualizar_produto', 'deletar_produto'):
            patcher = patch.object(ProdutoRepository, method)
            self.patches[method] = patcher.start()
            self.addCleanup(patcher.stop)
        for method in ('get_produto_por_id', 'registrar_produto', 'atualizar_produto'):
            self.patches[method].return_value = Produto(*ROW)
        self.patches['get_produtos'].return_value = [Produto(*ROW)]
        self.patches['deletar_produto'].return_value = True
        # 2E: perfil/atividade persistidos são relidos a cada requisição.
        self.acesso = None
        patcher = patch.object(UsuarioRepository, 'obter_acesso', side_effect=lambda _id: self.acesso)
        patcher.start()
        self.addCleanup(patcher.stop)

    def login(self, perfil='admin', ativo=True):
        self.acesso = (ativo, perfil)
        with self.client.session_transaction() as session:
            session.update(user_id=1, user_perfil=perfil, user_admin=perfil == 'admin', user_ativo=ativo)

    def test_all_routes_require_authentication(self):
        for method, path in (('get', '/produtos'), ('get', '/produtos/7'), ('post', '/produtos'),
                             ('put', '/produtos/7'), ('delete', '/produtos/7')):
            with self.subTest(method=method):
                self.assertEqual(getattr(self.client, method)(path, json=PAYLOAD).status_code, 401)

    def test_reader_can_read_but_cannot_write(self):
        self.login('operador')
        self.assertEqual(self.client.get('/produtos').status_code, 200)
        self.assertEqual(self.client.get('/produtos/7').status_code, 200)
        for method, path in (('post', '/produtos'), ('put', '/produtos/7'), ('delete', '/produtos/7')):
            self.assertEqual(getattr(self.client, method)(path, json=PAYLOAD).status_code, 403)

    def test_inactive_user_blocked(self):
        for method, path in (('get', '/produtos'), ('get', '/produtos/7'), ('post', '/produtos'),
                             ('put', '/produtos/7'), ('delete', '/produtos/7')):
            self.login(ativo=False)  # 2E: cada negativa por inatividade revoga a sessão
            self.assertEqual(getattr(self.client, method)(path, json=PAYLOAD).status_code, 403)
            self.assertEqual(self.client.get('/produtos').status_code, 401)
        for method in ('registrar_produto', 'atualizar_produto', 'deletar_produto'):
            self.patches[method].assert_not_called()

    def test_admin_and_supervisor_crud(self):
        for role in ('admin', 'supervisor'):
            self.login(role)
            self.assertEqual(self.client.get('/produtos').json, [asdict(Produto(*ROW))])
            self.assertEqual(self.client.get('/produtos/7').json['id_produto'], 7)
            result = self.client.post('/produtos', json=PAYLOAD)
            self.assertEqual(result.status_code, 201)
            self.assertIsNone(result.json['validade'])
            self.assertEqual(self.client.put('/produtos/7', json=UPDATE_PAYLOAD).status_code, 200)
            self.assertEqual(self.client.delete('/produtos/7').status_code, 200)

    def test_not_found_for_get_put_delete(self):
        self.login()
        for method in ('get_produto_por_id', 'atualizar_produto', 'deletar_produto'):
            self.patches[method].return_value = None
        for method in ('get', 'put', 'delete'):
            result = getattr(self.client, method)('/produtos/999', json=UPDATE_PAYLOAD)
            self.assertEqual(result.status_code, 404)
            self.assertIn('message', result.json)

    def test_invalid_fields_on_create_and_update(self):
        self.login()
        cases = [(field, value) for field in ('nome', 'categoria', 'codigo', 'localizacao')
                 for value in ('', ' ', 42, None, False, [])]
        cases += [(field, value) for field in ('estoque', 'quantidade_min')
                  for value in (-1, True, False, 1.2, '1', None)]
        cases += [('validade', '2026-02-30')]
        for field, value in cases:
            for method, path in (('post', '/produtos'), ('put', '/produtos/7')):
                with self.subTest(field=field, value=value, method=method):
                    result = getattr(self.client, method)(path, json={**(UPDATE_PAYLOAD if method == 'put' else PAYLOAD), field: value})
                    self.assertEqual(result.status_code, 400)
                    self.assertIn('message', result.json)
        self.patches['registrar_produto'].assert_not_called()
        self.patches['atualizar_produto'].assert_not_called()

    def test_invalid_json_payloads(self):
        self.login()
        for method, path in (('post', '/produtos'), ('put', '/produtos/7')):
            for body, content_type in (('[]', 'application/json'), ('null', 'application/json'),
                                       ('{', 'application/json'), ('text', 'text/plain')):
                result = getattr(self.client, method)(path, data=body, content_type=content_type)
                self.assertEqual(result.status_code, 400)
                self.assertIn('message', result.json)

    def test_duplicate_sku_is_conflict(self):
        self.login()
        for operation, method, path in (('registrar_produto', 'post', '/produtos'),
                                        ('atualizar_produto', 'put', '/produtos/7')):
            self.patches[operation].side_effect = BusinessError('Código duplicado')
            self.assertEqual(getattr(self.client, method)(path, json=UPDATE_PAYLOAD if method == 'put' else PAYLOAD).status_code, 409)

    def test_all_internal_errors_are_generic_json(self):
        self.login()
        for operation, method, path in (('get_produtos', 'get', '/produtos'),
                                        ('get_produto_por_id', 'get', '/produtos/7'),
                                        ('registrar_produto', 'post', '/produtos'),
                                        ('atualizar_produto', 'put', '/produtos/7'),
                                        ('deletar_produto', 'delete', '/produtos/7')):
            self.patches[operation].side_effect = RuntimeError('detalhe interno que deve ser omitido')
            response = getattr(self.client, method)(path, json=UPDATE_PAYLOAD if method == 'put' else PAYLOAD)
            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.json, {'message': 'Erro interno do servidor'})

    def test_composition_has_no_legacy_imports_or_routes(self):
        self.assertEqual(set(self.app.blueprints), {'user_bp', 'produto_bp', 'movimentacao_bp', 'rastreamento_bp', 'contagem_bp', 'relatorio_bp', 'fornecedor_bp'})
        for name in sys.modules:
            self.assertFalse(name.split('.')[0] in ('cv2', 'ultralytics', 'worker', 'events', 'flask_socketio', 'extensions'))
            self.assertNotIn('cameras', name)
            self.assertNotIn('epi_', name)
        self.assertEqual(self.client.get('/epis').status_code, 404)
        self.assertEqual(self.client.get('/movimentacoes').status_code, 401)


class BootstrapTests(TestCase):
    def test_missing_configuration_stops_before_connection(self):
        with patch.dict(os.environ, {}, clear=True), patch('bootstrap_admin.load_dotenv'):
            with self.assertRaises(ValidationError):
                database_config('inventaire')

    def test_database_mismatch_or_reserved_or_legacy_blocked(self):
        for name, expected in (('spi_database', 'spi_database'), ('postgres', 'postgres'),
                               ('template1', 'template1'), ('outro', 'inventaire')):
            with patch.dict(os.environ, dict(DB_HOST='test', DB_PORT='5432', DB_USER='test', DB_NAME=name)), \
                 patch('bootstrap_admin.load_dotenv'), self.assertRaises(ValidationError):
                database_config(expected)
