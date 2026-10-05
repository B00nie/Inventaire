"""Regressões isoladas; doubles de banco NÃO comprovam integração PostgreSQL."""
import os
from datetime import datetime, timedelta
from unittest import TestCase, main
from unittest.mock import MagicMock, patch

from flask import Flask
from psycopg2.errors import NotNullViolation, UniqueViolation

from app import app
from connection.conn import Connection
from core.errors import ValidationError
from core.security import Security
from models.usuarios import Usuario
from repository.produto_repository import ProdutoRepository
from repository.usuario_repository import UsuarioRepository
from schemas.produto_dto import ProdutoDTO
from schemas.usuario_dto import LoginDTO, SignupDTO, UsuarioDTO
from services.usuario_service import UsuarioService


USER = dict(email='test@example.invalid', password='test-only-password',
            nome='Teste', sobrenome='Local', perfil='admin')
PRODUTO = dict(nome='Item', categoria='Categoria', codigo='SKU-001', localizacao='A1',
           validade='2028-02-29', estoque=10, quantidade_min=2)


class ValidationTests(TestCase):
    def test_login_bad_payloads_and_types(self):
        for payload in (None, [], {}, {'email': 4, 'password': 'x'},
                        {'email': 'x', 'password': []},
                        {'email': 'x', 'password': ' '},
                        {'email': 'x', 'password': 'á' * 37}):
            with self.subTest(payload_type=type(payload).__name__):
                with self.assertRaises(ValidationError):
                    LoginDTO.from_dict(payload)

    def test_signup_requires_surname_and_string_fields(self):
        for key in ('nome', 'sobrenome', 'email', 'password', 'perfil'):
            for value in (None, '', '  ', 42, [], False):
                with self.subTest(field=key, value_type=type(value).__name__):
                    with self.assertRaises(ValidationError):
                        SignupDTO.from_dict({**USER, key: value})

    def test_user_update_requires_safe_types(self):
        # 2E: PUT não aceita 'password' (campo de signup) e senha ausente/null preserva o hash;
        # senha informada continua não podendo ser vazia nem não textual.
        payload = {k: v for k, v in USER.items() if k != 'password'} | {'id': 1, 'senha': USER['password']}
        for key, value in (('id', True), ('id', -1), ('ativo', 'false'), ('senha', ''), ('senha', '  '), ('senha', 42)):
            with self.subTest(field=key):
                with self.assertRaises(ValidationError):
                    UsuarioDTO.from_dict({**payload, key: value})

    def test_valid_frontend_payloads(self):
        self.assertEqual(SignupDTO.from_dict(USER).sobrenome, 'Local')
        self.assertEqual(LoginDTO.from_dict(USER).email, USER['email'])
        self.assertEqual(ProdutoDTO.from_dict(PRODUTO).validade.isoformat(), PRODUTO['validade'])

    def test_produto_rejects_invalid_values(self):
        cases = [(key, value) for key in ('nome', 'categoria', 'codigo', 'localizacao')
                 for value in ('', ' ', None, 12)]
        cases += [(key, value) for key in ('estoque', 'quantidade_min')
                  for value in (-1, True, 1.5, '1', None)]
        cases += [('validade', value) for value in
                  ('2027-02-29', '2028-13-01', '20280229', '', 1)]
        for key, value in cases:
            with self.subTest(field=key, value_type=type(value).__name__):
                with self.assertRaises(ValidationError):
                    ProdutoDTO.from_dict({**PRODUTO, key: value})

    def test_produto_does_not_add_domain_rules(self):
        item = ProdutoDTO.from_dict({**PRODUTO, 'estoque': 0, 'quantidade_min': 0, 'validade': '2000-01-01'})
        self.assertEqual(item.validade.isoformat(), '2000-01-01')
        self.assertEqual(item.quantidade_min, 0)

    def test_datetime_generated_per_instance(self):
        first, second = datetime(2020, 1, 1), datetime(2021, 1, 1)
        with patch('models.usuarios.datetime') as clock:
            clock.now.side_effect = [first, second]
            a = Usuario(1, 'A', 'B', 'a', 'hash', 'admin')
            b = Usuario(2, 'A', 'B', 'b', 'hash', 'admin')
        self.assertEqual(a.acesso, first.isoformat())
        self.assertEqual(b.acesso, second.isoformat())


class UserTests(TestCase):
    def setUp(self):
        self.service = UsuarioService(MagicMock())
        self.repo = self.service.user_repository = MagicMock()
        self.user = Usuario(1, 'Teste', 'Local', USER['email'],
                            Security().hash_password(USER['password']), 'admin')
        self.repo.get_usuario_por_email.return_value = self.user

    def test_inactive_user_cannot_login_or_update_access(self):
        self.user.ativo = False
        self.assertIsNone(self.service.login(USER['email'], USER['password']))
        self.repo.atualizar_acesso.assert_not_called()
        self.assertIs(self.service.obter_status_ativo(USER['email']), False)

    def test_wrong_password_does_not_update_access(self):
        self.assertIsNone(self.service.login(USER['email'], 'wrong'))
        self.repo.atualizar_acesso.assert_not_called()

    def test_active_login_updates_access(self):
        self.assertIs(self.service.login(USER['email'], USER['password']), self.user)
        self.repo.atualizar_acesso.assert_called_once()
        self.assertIs(self.service.obter_status_ativo(USER['email']), True)

    def test_update_hashes_password(self):
        payload = {k: v for k, v in USER.items() if k != 'password'} | {'id': 1, 'senha': USER['password']}
        self.service.atualizar_usuario(2, payload)  # 2E: responsável explícito
        hashed = self.repo.atualizar_usuario.call_args.args[5]
        self.assertNotEqual(hashed, USER['password'])
        self.assertTrue(Security().check_password(USER['password'], hashed))


class RepositoryTests(TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.cursor = self.conn.cursor.return_value.__enter__.return_value
        self.repo = UsuarioRepository(self.conn)

    def test_update_preserves_access(self):
        # 2E: transação administrativa (lock ordenado) antes do UPDATE.
        self.cursor.fetchall.return_value = [(1, True, 'admin'), (2, True, 'ADMINISTRADOR')]
        self.cursor.fetchone.return_value = (1, 'A', 'B', 'a', 'ADMINISTRADOR', None, None, True, '2020-01-01')
        self.repo.atualizar_usuario(2, 1, 'A', 'B', 'a', 'bcrypt-hash', 'ADMINISTRADOR')
        sql, params = self.cursor.execute.call_args_list[1].args
        self.assertTrue(sql.startswith('UPDATE usuarios'))
        self.assertNotIn('acesso', sql)
        self.assertEqual(params[-1], 1)

    def test_delete_is_parameterized_and_reports_missing(self):
        self.cursor.fetchall.return_value = [(1, True, 'admin'), (7, True, 'operador')]
        for count in (0, 1):
            self.cursor.rowcount = count
            self.assertEqual(self.repo.deletar_usuario(1, 7), bool(count))
        self.cursor.execute.assert_called_with('DELETE FROM usuarios WHERE id_usuario = %s', (7,))

    def test_not_null_is_not_reported_as_duplicate_email(self):
        self.cursor.execute.side_effect = NotNullViolation()
        with self.assertRaises(NotNullViolation):
            self.repo.criar_usuario('a', 'hash', 'A', None, 'admin')
        self.conn.rollback.assert_called_once()

    def test_only_email_constraint_maps_to_duplicate(self):
        class Duplicate(UniqueViolation):
            @property
            def diag(self):
                return type('Diag', (), {'constraint_name': self.constraint})()
        for constraint in ('usuarios_email_key', 'another_unique_key'):
            error = Duplicate()
            error.constraint = constraint
            self.cursor.execute.side_effect = error
            if constraint == 'usuarios_email_key':
                self.assertIsNone(self.repo.criar_usuario('a', 'hash', 'A', 'B', 'admin'))
            else:
                with self.assertRaises(UniqueViolation):
                    self.repo.criar_usuario('a', 'hash', 'A', 'B', 'admin')

    def test_inventory_internal_error_propagates_with_rollback(self):
        self.cursor.execute.side_effect = RuntimeError('test failure')
        with self.assertRaises(RuntimeError):
            ProdutoRepository(self.conn).registrar_produto(**PRODUTO, usuario_id=1)
        self.conn.rollback.assert_called_once()


class ConnectionTests(TestCase):
    def test_lazy_connection_reused_only_within_context_and_closed(self):
        flask_app = Flask('connection-test')
        database = Connection()
        database.init_app(flask_app)
        first, second = MagicMock(closed=False), MagicMock(closed=False)
        with patch.dict(os.environ, {'DB_HOST': 'test', 'DB_USER': 'test', 'DB_NAME': 'test'}):
            with patch('connection.conn.psycopg2.connect', side_effect=[first, second]) as connect:
                proxy = database.get_connection()
                connect.assert_not_called()
                with flask_app.app_context():
                    proxy.cursor()
                    proxy.cursor()
                    self.assertEqual(connect.call_count, 1)
                first.rollback.assert_called_once()
                first.close.assert_called_once()
                with self.assertRaises(RuntimeError):
                    with flask_app.app_context():
                        proxy.cursor()
                        raise RuntimeError('test error')
                second.rollback.assert_called_once()
                second.close.assert_called_once()
                self.assertEqual(connect.call_count, 2)

    def test_connection_closes_even_when_rollback_fails(self):
        flask_app = Flask('rollback-test')
        database = Connection()
        connection = MagicMock(closed=False)
        connection.rollback.side_effect = RuntimeError('test error')
        with flask_app.app_context():
            from flask import g
            g.inventaire_db = connection
            with self.assertRaises(RuntimeError):
                database.close()
        connection.close.assert_called_once()


class HttpTests(TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_minimal_composition_and_lifetime(self):
        self.assertEqual(set(app.blueprints), {'user_bp', 'produto_bp', 'movimentacao_bp', 'rastreamento_bp', 'contagem_bp', 'relatorio_bp', 'fornecedor_bp'})
        self.assertEqual(app.permanent_session_lifetime, timedelta(minutes=30))
        self.assertNotIn('SESSION_SESSION_LIFETIME', app.config)

    def test_unauthenticated_routes(self):
        for path in ('/session', '/users', '/produtos', '/produtos/1'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

    def test_invalid_login_json_is_400(self):
        for payload in ({}, [], {'email': 4, 'password': 'x'}):
            response = self.client.post('/login', json=payload)
            self.assertEqual(response.status_code, 400)
            self.assertIn('message', response.json)
        for kwargs in ({'data': '{', 'content_type': 'application/json'}, {'data': 'text'}):
            response = self.client.post('/login', **kwargs)
            self.assertEqual(response.status_code, 400)
            self.assertIn('message', response.json)

    def test_inactive_login_has_no_session_cookie(self):
        user = Usuario(1, 'Teste', 'Local', USER['email'], 'unused', 'admin', ativo=False)
        with patch.object(UsuarioRepository, 'get_usuario_por_email', return_value=user):
            response = self.client.post('/login', json=USER)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('Set-Cookie', response.headers)

    def test_internal_login_failure_stays_500(self):
        with patch.object(UsuarioRepository, 'get_usuario_por_email', side_effect=RuntimeError()):
            self.assertEqual(self.client.post('/login', json=USER).status_code, 500)


if __name__ == '__main__':
    main()
