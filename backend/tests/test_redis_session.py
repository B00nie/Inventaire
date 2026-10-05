"""Redis REAL com usuário/repository isolado; NÃO é login PostgreSQL real.

Ative explicitamente com INVENTAIRE_TEST_REDIS=1. Só remove chaves do teste.
"""
import os
import uuid
from unittest import TestCase, skipUnless
from unittest.mock import patch

from app import app
from core.security import Security
from models.usuarios import Usuario
from repository.usuario_repository import UsuarioRepository
from repository.produto_repository import ProdutoRepository


@skipUnless(os.getenv('INVENTAIRE_TEST_REDIS') == '1', 'Teste Redis opt-in')
class RedisSessionTests(TestCase):
    def setUp(self):
        self.prefix = 'inventaire:test:session:' + uuid.uuid4().hex + ':'
        self.redis = app.config['SESSION_REDIS']
        self.prefix_patch = patch.object(app.session_interface, 'key_prefix', self.prefix)
        self.prefix_patch.start()
        self.addCleanup(self.cleanup)
        self.user = Usuario(1, 'Teste', 'Local', 'test@example.invalid',
                            Security().hash_password('test-only-password'), 'admin')
        self.lookup = patch.object(UsuarioRepository, 'get_usuario_por_email', return_value=self.user)
        self.lookup.start()
        self.addCleanup(self.lookup.stop)
        # 2E: /session e rotas protegidas releem o usuário persistido por id.
        for method, value in (('get_usuario_por_id', lambda _id: self.user),
                              ('obter_acesso', lambda _id: (self.user.ativo, self.user.perfil))):
            patcher = patch.object(UsuarioRepository, method, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.access = patch.object(UsuarioRepository, 'atualizar_acesso')
        self.access.start()
        self.addCleanup(self.access.stop)
        self.client = app.test_client()
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])

    def login(self):
        return self.client.post('/login', json={'email': self.user.email,
                                               'password': 'test-only-password'})

    def cleanup(self):
        # SCAN restrito ao UUID desta execução; jamais FLUSHDB/FLUSHALL.
        keys = list(self.redis.scan_iter(match=self.prefix + '*'))
        if keys:
            self.redis.delete(*keys)
        assert not list(self.redis.scan_iter(match=self.prefix + '*'))
        self.prefix_patch.stop()

    def test_session_lifecycle_and_rotation(self):
        keys = list(self.redis.scan_iter(match=self.prefix + '*'))
        self.assertEqual(len(keys), 1)
        self.assertTrue(0 < self.redis.ttl(keys[0]) <= 1800)
        self.assertEqual(self.client.get('/session').status_code, 200)
        self.assertEqual(self.login().status_code, 200)
        new_keys = list(self.redis.scan_iter(match=self.prefix + '*'))
        self.assertEqual(len(new_keys), 1)
        self.assertNotEqual(keys, new_keys)
        self.assertEqual(self.client.post('/logout').status_code, 200)
        self.assertEqual(self.client.get('/session').status_code, 401)
        self.assertEqual(list(self.redis.scan_iter(match=self.prefix + '*')), [])

    def test_inactive_session_is_revoked(self):
        self.user.ativo = False
        self.assertEqual(self.client.get('/session').status_code, 403)
        self.assertEqual(self.client.get('/session').status_code, 401)
        self.assertEqual(list(self.redis.scan_iter(match=self.prefix + '*')), [])

    def test_signup_validation_does_not_reach_database(self):
        with patch.object(UsuarioRepository, 'criar_usuario') as create:
            response = self.client.post('/signup', json={'nome': 'A', 'sobrenome': None,
                'email': 'test@example.invalid', 'password': 'test-only-password', 'perfil': 'admin'})
            self.assertEqual(response.status_code, 400)
            self.assertIn('message', response.json)
            create.assert_not_called()

    def test_invalid_inventory_does_not_reach_database(self):
        with patch.object(ProdutoRepository, 'registrar_produto') as create:
            response = self.client.post('/produtos', json={})
            self.assertEqual(response.status_code, 400)
            self.assertIn('message', response.json)
            create.assert_not_called()

    def test_role_restrictions(self):
        self.user.perfil = 'operador'
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.client.get('/users').status_code, 403)
        self.assertEqual(self.client.post('/produtos', json={}).status_code, 403)
