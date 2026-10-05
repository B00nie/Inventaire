"""Correção de identidade, edição do próprio perfil e restrições do OPERADOR (05/10/2026).

Testes isolados com doubles; NÃO comprovam PostgreSQL, Redis nem navegador reais.
Cobrem: /session por identidade e por contexto (cookie compartilhado × independente), PATCH /me/perfil
(alvo só da sessão, whitelist estrita, preservação de senha/perfil/ativo/unidade/acesso, e-mail
duplicado com rollback, revogação, lock da própria linha, login com o e-mail novo), a nova matriz do
OPERADOR (Relatórios e Fornecedores negados no backend, Dashboard mantido) e a opção B aprovada
(lista mínima de fornecedores ativos para a ENTRADA). Também verificações estáticas do frontend.
"""
from copy import deepcopy
import re
import threading
import time
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from flask.sessions import SecureCookieSessionInterface
from psycopg2.errors import UniqueViolation

from app import create_app
from core.errors import ValidationError
from core.permissoes import PERFIS, pode
from repository.usuario_repository import LOCK_ADMINISTRATIVO, UsuarioRepository
from schemas.usuario_dto import LIMITES_PERFIL_PROPRIO, PerfilProprioDTO
from services.fornecedor_service import FornecedorService
from services.relatorios_service import RelatoriosService
from test_fornecedores import CNPJ_A, SupplierConnection, SupplierDB
from test_perfis import SENHA, UsersConn, UsersDB

RAIZ = Path(__file__).resolve().parents[2]
IDS = {'ADMINISTRADOR': 1, 'OPERADOR': 3, 'GESTOR': 4, 'AUDITOR': 6}  # 1 = administrador preexistente


class EmailDuplicado(UniqueViolation):
    @property
    def diag(self):
        return type('Diag', (), {'constraint_name': 'usuarios_email_key'})()


class UniqueUsersConn(UsersConn):
    """UsersConn + UNIQUE(email) emulada no UPDATE (como usuarios_email_key)."""

    def execute(self, q, p=()):
        if q.startswith('UPDATE usuarios SET') and 'email = %s' in q:
            campos = [c.split(' = ')[0] for c in q[len('UPDATE usuarios SET '):q.index(' WHERE')].split(', ')]
            email = p[campos.index('email')]
            if any(u['email'] == email and uid != p[-1] for uid, u in self.db.users.items()):
                raise EmailDuplicado()
        return super().execute(q, p)


class Base(TestCase):
    def setUp(self):
        self.db = UsersDB()
        for perfil, uid in IDS.items():
            self.db.add(uid, perfil)
        self.db.users[1].update(nome='AdminPreexistente', unidade='Matriz')
        self.conn = UniqueUsersConn(self.db)
        with patch('app.Connection.get_connection', return_value=self.conn):
            self.app = create_app()
        self.app.session_interface = SecureCookieSessionInterface()
        self.app.session_interface.regenerate = lambda sessao: None  # Flask-Session/Redis: suíte opt-in

    def client(self, uid=None):
        client = self.app.test_client()
        if uid is not None:
            with client.session_transaction() as s:
                s.update(user_id=uid)
        return client

    def login(self, client, email, senha=SENHA):
        return client.post('/login', json={'email': email, 'password': senha})

    def assertNoLocks(self):
        self.assertFalse(any(lock.locked() for lock in self.db.locks.values()))


# ── Diagnóstico: identidade da sessão efetiva ───────────────────────────────

class IdentidadeSessaoTests(Base):
    def test_session_returns_each_identity_never_the_preexisting_admin(self):
        for perfil, uid in IDS.items():
            with self.subTest(perfil=perfil):
                c = self.client()
                self.assertEqual(self.login(c, f'u{uid}@example.invalid').status_code, 200)
                r = c.get('/session')
                user = r.json['user']
                self.assertEqual((user['id'], user['email'], user['perfil']), (uid, f'u{uid}@example.invalid', perfil))
                if uid != 1:
                    self.assertNotEqual(user['nome'], 'AdminPreexistente')
                self.assertEqual(r.headers.get('Cache-Control'), 'no-store')

    def test_shared_cookie_follows_last_login_independent_contexts_do_not_mix(self):
        # Caso B do diagnóstico: abas do mesmo contexto compartilham UM cookie.
        compartilhado = self.client()
        self.login(compartilhado, 'u3@example.invalid')
        self.assertEqual(compartilhado.get('/session').json['user']['id'], 3)
        self.login(compartilhado, 'u1@example.invalid')  # "outra aba" do mesmo contexto
        self.assertEqual(compartilhado.get('/session').json['user']['id'], 1)
        # Contextos independentes (outro perfil do navegador / janela anônima): sem mistura.
        operador, admin = self.client(), self.client()
        self.login(operador, 'u3@example.invalid')
        self.login(admin, 'u1@example.invalid')
        for _ in range(3):
            self.assertEqual(operador.get('/session').json['user']['id'], 3)
            self.assertEqual(admin.get('/session').json['user']['id'], 1)
        self.assertEqual(operador.patch('/me/perfil', json={'telefone': '555'}).status_code, 200)
        self.assertEqual((self.db.users[3]['telefone'], self.db.users[1]['telefone']), ('555', '1'))

    def test_logout_ends_identity(self):
        c = self.client()
        self.login(c, 'u3@example.invalid')
        self.assertEqual(c.post('/logout').status_code, 200)
        self.assertEqual(c.get('/session').status_code, 401)
        self.assertEqual(c.patch('/me/perfil', json={'nome': 'X'}).status_code, 401)


# ── PATCH /me/perfil ────────────────────────────────────────────────────────

class PerfilProprioTests(Base):
    def test_each_profile_edits_own_data_and_session_reflects_it(self):
        for perfil, uid in IDS.items():
            with self.subTest(perfil=perfil):
                antes = deepcopy(self.db.users)
                c = self.client(uid)
                corpo = {'nome': f' Novo{uid} ', 'sobrenome': 'Silva', 'email': f' NOVO{uid}@Example.Invalid ',
                         'telefone': '11 9999'}
                r = c.patch('/me/perfil', json=corpo, headers={'X-Inventaire-Usuario': str(uid)})
                self.assertEqual(r.status_code, 200, r.json)
                self.assertEqual(r.headers.get('Cache-Control'), 'no-store')
                user = r.json['user']
                self.assertEqual((user['id'], user['nome'], user['email'], user['perfil']),
                                 (uid, f'Novo{uid}', f'novo{uid}@example.invalid', perfil))
                self.assertIn('permissoes', user)
                self.assertNotIn('senha', user)
                atual = self.db.users[uid]
                for campo in ('senha', 'perfil', 'ativo', 'unidade', 'acesso'):
                    self.assertEqual(atual[campo], antes[uid][campo], campo)
                self.assertEqual({k: v for k, v in self.db.users.items() if k != uid},
                                 {k: v for k, v in antes.items() if k != uid})
                self.assertEqual(c.get('/session').json['user']['email'], f'novo{uid}@example.invalid')
                with c.session_transaction() as s:
                    self.assertEqual((s['user_id'], s['user_email'], s['user_nome']),
                                     (uid, f'novo{uid}@example.invalid', f'Novo{uid}'))
        self.assertNoLocks()

    def test_partial_update_and_phone_removal(self):
        c = self.client(3)
        self.assertEqual(c.patch('/me/perfil', json={'telefone': None}).status_code, 200)
        self.assertIsNone(self.db.users[3]['telefone'])
        self.assertEqual(c.patch('/me/perfil', json={'telefone': '  '}).status_code, 200)
        self.assertIsNone(self.db.users[3]['telefone'])
        self.assertEqual(c.patch('/me/perfil', json={'sobrenome': 'Só'}).json['user']['nome'], 'Nome3')

    def test_admin_taiga_can_edit_own_data_but_not_own_access(self):
        c = self.client(1)
        self.assertEqual(c.patch('/me/perfil', json={'nome': 'Taiga'}).status_code, 200)
        self.assertEqual((self.db.users[1]['nome'], self.db.users[1]['perfil']), ('Taiga', 'ADMINISTRADOR'))
        self.assertEqual(c.patch('/me/perfil', json={'perfil': 'OPERADOR'}).status_code, 400)
        self.assertEqual(c.patch('/users/1/acesso', json={'ativo': False}).status_code, 409)  # proteção 2E intacta

    def test_target_only_from_session_attempts_against_admin(self):
        antes = deepcopy(self.db.users)
        c = self.client(3)
        for corpo in ({'id': 1, 'nome': 'Hack'}, {'id_usuario': 1, 'nome': 'Hack'}, {'usuario_id': 1, 'nome': 'Hack'},
                      {'email': 'u1@example.invalid', 'id': 1}):
            with self.subTest(corpo=corpo):
                self.assertEqual(c.patch('/me/perfil', json=corpo).status_code, 400)
        for url in ('/me/perfil/1', '/me/perfil?id=1', '/me/perfil?id_usuario=1'):
            with self.subTest(url=url):
                r = c.patch(url, json={'nome': 'Hack'})
                if '?' in url:  # query string é ignorada: o alvo continua sendo o próprio usuário
                    self.assertEqual((r.status_code, r.json['user']['id']), (200, 3))
                    self.db.users[3]['nome'] = antes[3]['nome']
                else:
                    self.assertEqual(r.status_code, 404)
        # Cabeçalho de identidade de outra conta (formulário aberto como Taiga): nada é gravado.
        r = c.patch('/me/perfil', json={'nome': 'Hack'}, headers={'X-Inventaire-Usuario': '1'})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.db.users, antes)

    def test_forbidden_and_unknown_fields_rejected_even_with_current_values(self):
        atual = self.db.users[3]
        antes = deepcopy(self.db.users)
        c = self.client(3)
        proibidos = {'perfil': atual['perfil'], 'ativo': atual['ativo'], 'unidade': atual['unidade'],
                     'senha': SENHA, 'password': SENHA, 'admin': False, 'permissoes': [], 'acesso': None,
                     'id': 3, 'foo': 'bar'}
        for campo, valor in proibidos.items():
            with self.subTest(campo=campo):
                r = c.patch('/me/perfil', json={'nome': 'Novo', campo: valor})
                self.assertEqual(r.status_code, 400)
                self.assertIn(campo, r.json['message'])
        self.assertEqual(self.db.users, antes)
        self.assertFalse(any(q.startswith('UPDATE usuarios') for q, _ in self.conn.queries))

    def test_validation_errors(self):
        antes = deepcopy(self.db.users)
        c = self.client(3)
        casos = [{}, [], 'x', {'nome': ''}, {'nome': '   '}, {'nome': None}, {'email': None}, {'nome': 5},
                 {'nome': 'a' * 41}, {'sobrenome': 'a' * 91}, {'email': 'a' * 56 + '@x.io'}, {'telefone': '1' * 46}]
        for corpo in casos:
            with self.subTest(corpo=corpo):
                self.assertEqual(c.patch('/me/perfil', json=corpo).status_code, 400)
        self.assertEqual(self.db.users, antes)
        self.assertEqual(c.patch('/me/perfil', data='{', content_type='application/json').status_code, 400)
        self.assertEqual(c.patch('/me/perfil', json={'nome': 'a' * 40, 'sobrenome': 'b' * 90}).status_code, 200)
        self.assertEqual(self.db.users[3]['nome'], 'a' * 40)
        self.assertEqual(set(LIMITES_PERFIL_PROPRIO), {'nome', 'sobrenome', 'email', 'telefone'})
        with self.assertRaises(ValidationError):
            PerfilProprioDTO.from_dict({'nome': 'x', 'senha': 'y'})

    def test_duplicate_email_is_409_with_rollback(self):
        antes = deepcopy(self.db.users)
        rollbacks = self.conn.rollbacks
        r = self.client(3).patch('/me/perfil', json={'nome': 'Mudou', 'email': 'U1@example.invalid'})
        self.assertEqual((r.status_code, r.json), (409, {'message': 'E-mail já cadastrado.'}))
        self.assertEqual(self.db.users, antes)
        self.assertGreater(self.conn.rollbacks, rollbacks)
        self.assertNoLocks()

    def test_no_session_is_401_and_unknown_profile_403(self):
        self.assertEqual(self.client().patch('/me/perfil', json={'nome': 'X'}).status_code, 401)
        self.db.add(9, 'leitor')
        self.assertEqual(self.client(9).patch('/me/perfil', json={'nome': 'X'}).status_code, 403)
        self.assertEqual(self.db.users[9]['nome'], 'Nome9')

    def test_inactive_or_removed_is_revoked(self):
        for remover in (False, True):
            with self.subTest(remover=remover):
                self.db.add(7, 'OPERADOR')
                c = self.client(7)
                if remover:
                    del self.db.users[7]
                else:
                    self.db.users[7]['ativo'] = False
                r = c.patch('/me/perfil', json={'nome': 'X'})
                self.assertEqual((r.status_code, r.json), (403, {'message': 'Usuário inativo'}))
                self.assertEqual(c.get('/session').status_code, 401)

    def test_revalidated_inside_transaction(self):
        # O decorator autorizou; o usuário foi inativado antes do lock da própria linha.
        self.db.users[3]['ativo'] = False
        c = self.client(3)
        with patch.object(UsuarioRepository, 'obter_acesso', return_value=(True, 'OPERADOR')):
            r = c.patch('/me/perfil', json={'nome': 'X'})
        self.assertEqual((r.status_code, r.json), (403, {'message': 'Usuário inativo'}))
        self.assertEqual(self.db.users[3]['nome'], 'Nome3')
        self.assertEqual(c.get('/session').status_code, 401)

    def test_unexpected_error_is_generic_500_with_rollback(self):
        antes = deepcopy(self.db.users)
        original = self.conn.execute

        def falhar(q, p=()):
            if q.startswith('UPDATE usuarios SET'):
                original(q, p)
                raise RuntimeError('detalhe interno sigiloso')
            return original(q, p)
        with patch.object(self.conn, 'execute', side_effect=falhar):
            r = self.client(3).patch('/me/perfil', json={'nome': 'X'})
        self.assertEqual((r.status_code, r.json), (500, {'message': 'Erro interno do servidor'}))
        self.assertEqual(self.db.users, antes)
        self.assertNoLocks()

    def test_login_with_updated_email_and_original_password(self):
        self.assertEqual(self.client(4).patch('/me/perfil', json={'email': 'gestor.novo@example.invalid'}).status_code, 200)
        c = self.client()
        self.assertEqual(self.login(c, 'u4@example.invalid').status_code, 401)
        r = self.login(c, 'GESTOR.NOVO@example.invalid')
        self.assertEqual((r.status_code, r.json['user']['id']), (200, 4))
        self.assertEqual(self.login(c, 'gestor.novo@example.invalid', 'outra-senha').status_code, 401)

    def test_lock_only_own_row_no_administrative_lock(self):
        self.conn.queries.clear()
        self.assertEqual(self.client(3).patch('/me/perfil', json={'nome': 'X'}).status_code, 200)
        locks = [(q, p) for q, p in self.conn.queries if 'FOR UPDATE' in q or 'FOR SHARE' in q]
        self.assertEqual(locks, [('SELECT ativo, perfil FROM usuarios WHERE id_usuario = %s FOR UPDATE', (3,))])
        self.assertNotIn(LOCK_ADMINISTRATIVO, [q for q, _ in self.conn.queries])
        update = [q for q, _ in self.conn.queries if q.startswith('UPDATE usuarios')]
        self.assertEqual(update, ['UPDATE usuarios SET nome = %s WHERE id_usuario = %s'])

    def test_waits_for_concurrent_admin_and_sees_committed_inactivation(self):
        admin = UsersConn(self.db)
        admin.execute(LOCK_ADMINISTRATIVO, (1, 3, ('admin', 'administrador')))  # segura a linha 3
        resultado = {}
        repo = UsuarioRepository(UsersConn(self.db))
        t = threading.Thread(target=lambda: resultado.update(r=repo.atualizar_perfil_proprio(3, {'nome': 'X'})))
        t.start()
        time.sleep(0.1)
        self.assertNotIn('r', resultado)  # esperando o lock da própria linha
        admin.execute('UPDATE usuarios SET ativo = %s WHERE id_usuario = %s', (False, 3))
        admin.commit()
        t.join(5)
        self.assertIsNone(resultado['r'])  # inativo sob lock → sessão revogada pelo controller
        self.assertEqual(self.db.users[3]['nome'], 'Nome3')
        self.assertNoLocks()

    def test_admin_management_still_requires_usuarios_gerenciar(self):
        for uid in (3, 4, 6):
            c = self.client(uid)
            self.assertEqual(c.put('/users', json={'id': uid, 'nome': 'A', 'sobrenome': 'B', 'email': 'x@y.z',
                                                   'perfil': 'ADMINISTRADOR'}).status_code, 403)
            self.assertEqual(c.get('/users').status_code, 403)
        self.assertTrue(all(pode(p, 'perfil:editar_proprio') for p in PERFIS))
        self.assertEqual({p for p in PERFIS if pode(p, 'usuarios:gerenciar')}, {'ADMINISTRADOR'})


# ── Administração: unidade de outros usuários (PATCH /users/<id>/acesso) ────

class UnidadeAdministracaoTests(Base):
    def test_admin_changes_and_clears_unit_preserving_everything_else(self):
        antes = deepcopy(self.db.users)
        c = self.client(1)
        r = c.patch('/users/3/acesso', json={'unidade': '  Depósito Norte  '})
        self.assertEqual((r.status_code, r.json['unidade'], r.json['perfil']), (200, 'Depósito Norte', 'OPERADOR'))
        for campo in ('nome', 'sobrenome', 'email', 'senha', 'perfil', 'telefone', 'ativo', 'acesso'):
            self.assertEqual(self.db.users[3][campo], antes[3][campo], campo)
        for vazio in (None, '', '   '):
            with self.subTest(vazio=vazio):
                self.db.users[3]['unidade'] = 'X'
                self.assertEqual(c.patch('/users/3/acesso', json={'unidade': vazio}).status_code, 200)
                self.assertIsNone(self.db.users[3]['unidade'])
        r = c.patch('/users/4/acesso', json={'perfil': 'AUDITOR', 'unidade': 'SP'})
        self.assertEqual((r.status_code, self.db.users[4]['perfil'], self.db.users[4]['unidade']), (200, 'AUDITOR', 'SP'))
        # Perfil/atividade sem unidade preservam a unidade.
        self.assertEqual(c.patch('/users/4/acesso', json={'ativo': False}).status_code, 200)
        self.assertEqual(self.db.users[4]['unidade'], 'SP')
        self.assertNoLocks()

    def test_validation_and_permission(self):
        antes = deepcopy(self.db.users)
        c = self.client(1)
        for corpo in ({'unidade': 'a' * 46}, {'unidade': 5}, {'unidade': ['SP']}, {}, {'unidade': 'SP', 'nome': 'X'}):
            with self.subTest(corpo=corpo):
                self.assertEqual(c.patch('/users/3/acesso', json=corpo).status_code, 400)
        self.assertEqual(c.patch('/users/99/acesso', json={'unidade': 'SP'}).status_code, 404)
        for uid in (3, 4, 6):
            with self.subTest(uid=uid):
                self.assertEqual(self.client(uid).patch('/users/3/acesso', json={'unidade': 'SP'}).status_code, 403)
        self.assertEqual(self.db.users, antes)
        self.assertEqual(c.patch('/users/3/acesso', json={'unidade': 'a' * 45}).status_code, 200)

    def test_admin_own_unit_allowed_but_not_own_access(self):
        c = self.client(1)
        self.assertEqual(c.patch('/users/1/acesso', json={'unidade': 'RJ'}).status_code, 200)
        self.assertEqual(self.db.users[1]['unidade'], 'RJ')
        self.assertEqual(c.patch('/users/1/acesso', json={'unidade': 'SP', 'ativo': False}).status_code, 409)
        self.assertEqual((self.db.users[1]['unidade'], self.db.users[1]['ativo']), ('RJ', True))  # rollback

    def test_admin_page_has_unit_field(self):
        js = fonte('js/administracao.js')
        self.assertIn('data-user-unit', js)
        self.assertIn('{ unidade: unidade || null }', js)
        self.assertIn('maxlength="45"', js)


# ── Nova regra do OPERADOR (decorator HTTP) ─────────────────────────────────

PER = 'data_inicio=2026-10-01T00:00:00-03:00&data_fim=2026-10-08T00:00:00-03:00'
RELATORIOS = ['/relatorios/posicao-estoque', '/relatorios/estoque-baixo', f'/relatorios/movimentacoes?{PER}',
              '/relatorios/divergencias', f'/relatorios/saidas-periodo?{PER}']
RELATORIOS += [r.replace('?', '/exportacao?') if '?' in r else r + '/exportacao' for r in RELATORIOS]
FORNECEDORES = [('get', '/fornecedores', None), ('get', '/fornecedores/1', None),
                ('get', '/fornecedores/1/recebimentos', None), ('post', '/fornecedores', {}),
                ('put', '/fornecedores/1', {}), ('delete', '/fornecedores/1', None)]


class MatrizOperadorTests(TestCase):
    def setUp(self):
        with patch('app.Connection.get_connection', return_value=MagicMock()):
            self.app = create_app()
        self.app.session_interface = SecureCookieSessionInterface()
        self.client = self.app.test_client()
        self.acesso = None
        self.servicos = []
        for cls, metodos in {RelatoriosService: ('dashboard', 'posicao_estoque', 'estoque_baixo', 'movimentacoes',
                                                 'divergencias', 'saidas_periodo'),
                             FornecedorService: ('listar', 'obter', 'criar', 'atualizar', 'excluir',
                                                 'recebimentos', 'selecao_entrada')}.items():
            for metodo in metodos:
                patcher = patch.object(cls, metodo, return_value={'ok': True})
                self.servicos.append(patcher.start())
                self.addCleanup(patcher.stop)
        patcher = patch.object(UsuarioRepository, 'obter_acesso', side_effect=lambda _id: self.acesso)
        patcher.start()
        self.addCleanup(patcher.stop)

    def login(self, perfil):
        self.acesso = (True, perfil)
        with self.client.session_transaction() as s:
            s['user_id'] = 2

    def chamadas(self):
        return sum(m.call_count for m in self.servicos)

    def test_operator_403_on_reports_exports_and_supplier_apis_dashboard_kept(self):
        self.login('OPERADOR')
        for rota in RELATORIOS:
            with self.subTest(rota=rota):
                self.assertEqual(self.client.get(rota).status_code, 403)
        for method, path, body in FORNECEDORES:
            with self.subTest(path=path, method=method):
                r = getattr(self.client, method)(path, json=body) if body is not None else getattr(self.client, method)(path)
                self.assertEqual(r.status_code, 403)
        self.assertEqual(self.chamadas(), 0)
        self.assertEqual(self.client.get('/dashboard/resumo').status_code, 200)
        self.assertEqual(self.client.get('/fornecedores/selecao-entrada').status_code, 200)

    def test_other_profiles_keep_their_access(self):
        for perfil in ('AUDITOR', 'GESTOR', 'ADMINISTRADOR'):
            self.login(perfil)
            for rota in RELATORIOS + ['/dashboard/resumo', '/fornecedores', '/fornecedores/1',
                                      '/fornecedores/1/recebimentos']:
                with self.subTest(perfil=perfil, rota=rota):
                    self.assertEqual(self.client.get(rota).status_code, 200)
            gerencia = 201 if perfil != 'AUDITOR' else 403
            self.assertEqual(self.client.post('/fornecedores', json={}).status_code, gerencia)
            # Seleção para ENTRADA: só quem registra ENTRADA (AUDITOR não registra).
            self.assertEqual(self.client.get('/fornecedores/selecao-entrada').status_code,
                             403 if perfil == 'AUDITOR' else 200)

    def test_report_permission_does_not_replace_domain_permissions(self):
        self.login('AUDITOR')
        with patch.dict('core.permissoes.PERMISSOES', {'AUDITOR': frozenset({'relatorios:consultar'})}):
            for rota in RELATORIOS:
                with self.subTest(rota=rota):
                    self.assertEqual(self.client.get(rota).status_code, 403)
        self.assertEqual(self.chamadas(), 0)


# ── Opção B: lista mínima de fornecedores ativos para a ENTRADA ─────────────

class SelecaoEntradaTests(TestCase):
    def setUp(self):
        self.db = SupplierDB()
        self.conn = SupplierConnection(self.db)
        with patch('app.Connection.get_connection', return_value=self.conn):
            self.app = create_app()
        self.app.session_interface = SecureCookieSessionInterface()
        self.client = self.app.test_client()
        self.addCleanup(self.conn.rollback)

    def login(self, role):
        self.db.role = role
        with self.client.session_transaction() as s:
            s['user_id'] = 23

    def test_operator_gets_only_minimal_active_list_and_can_receive(self):
        self.db.suppliers[2][4] = False
        self.login('OPERADOR')
        r = self.client.get('/fornecedores/selecao-entrada')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.json), {'itens', 'total', 'limite'})
        self.assertEqual(r.json['itens'], [{'id_fornecedor': 1, 'razao_social': 'Alfa Ltda',
                                            'cnpj_formatado': '12.345.678/0001-95'}])
        self.assertEqual((r.json['total'], r.json['limite']), (1, 100))
        self.assertNotIn('compras@alfa.example', r.get_data(as_text=True))
        corpo = dict(tipo='ENTRADA', quantidade=2, id_posicao=1, lote='L', id_fornecedor=1)
        self.assertEqual(self.client.post('/produtos/7/movimentacoes', json=corpo).status_code, 201)
        # Detalhe, cadastro e recebimentos continuam negados.
        for rota in ('/fornecedores', '/fornecedores/1', '/fornecedores/1/recebimentos'):
            self.assertEqual(self.client.get(rota).status_code, 403)

    def test_parameters_rejected_and_methods(self):
        self.login('OPERADOR')
        for q in ('?ativo=nao', '?page_size=1000', '?q=Beta', '?page=2'):
            with self.subTest(q=q):
                self.assertEqual(self.client.get('/fornecedores/selecao-entrada' + q).status_code, 400)
        self.assertEqual(self.client.post('/fornecedores/selecao-entrada', json={}).status_code, 405)

    def test_revalidated_inside_read_transaction(self):
        self.login('AUDITOR')  # persistido: sem ENTRADA
        with patch.object(UsuarioRepository, 'obter_acesso', return_value=(True, 'OPERADOR')):
            self.assertEqual(self.client.get('/fornecedores/selecao-entrada').status_code, 403)
        self.login('OPERADOR')
        self.db.active = False
        with patch.object(UsuarioRepository, 'obter_acesso', return_value=(True, 'OPERADOR')):
            self.assertEqual(self.client.get('/fornecedores/selecao-entrada').status_code, 403)

    def test_historical_snapshot_still_visible_in_movements(self):
        self.login('GESTOR')
        self.client.post('/produtos/7/movimentacoes', json=dict(tipo='ENTRADA', quantidade=1, id_posicao=1,
                                                                 lote='L', id_fornecedor=1))
        self.login('OPERADOR')
        historico = self.client.get('/movimentacoes').json
        self.assertEqual(historico[0]['fornecedor_cnpj'], CNPJ_A)


# ── Frontend (verificação estática; comportamento no harness de .validation) ─

def fonte(caminho):
    return (RAIZ / caminho).read_text(encoding='utf-8')


class FrontendEstaticoTests(TestCase):
    def test_profile_page_uses_session_identity_never_users_or_fixed_id(self):
        js = fonte('js/perfil.js')
        self.assertNotRegex(js, r"""['"`]/users""")  # nenhuma chamada a /users
        self.assertNotIn('apiGet(', js)
        self.assertIn('getSessionUser()', js)
        self.assertIn('apiPerfil.atualizar(formUserId', js)
        self.assertIn('getSession()?.userId !== formUserId', js)  # resposta atrasada descartada
        self.assertNotRegex(js, r'innerHTML')
        api = fonte('js/api.js')
        self.assertIn("'/me/perfil'", api)
        self.assertIn("'X-Inventaire-Usuario'", api)

    def test_profile_form_fields_and_limits(self):
        html = fonte('perfil.html')
        editaveis = dict(re.findall(r'name="(\w+)" [^>]*maxlength="(\d+)"', html))
        self.assertEqual({k: int(v) for k, v in editaveis.items()}, LIMITES_PERFIL_PROPRIO)
        for campo in ('profilePerfil', 'profileUnidade'):
            self.assertRegex(html, rf'id="{campo}" readonly')
        self.assertNotIn('Matrícula', html)
        self.assertIn('css/perfil.css', html)

    def test_identity_guard_and_cache_bound_to_identity(self):
        common, api = fonte('js/common.js'), fonte('js/api.js')
        self.assertIn('window.addEventListener("storage"', common)
        self.assertIn('publishIdentity(session.userId)', common)
        self.assertIn('session.userId !== verifiedSession.userId', common)
        self.assertIn('"visibilitychange"', common)
        self.assertIn('event.persisted', common)
        self.assertIn('localStorage.removeItem(IDENTITY_KEY)', api)
        self.assertRegex(api, r'visaoepi_cache:\$\{API_BASE_URL\}:\$\{userId\}:')

    def test_pages_menu_and_search_follow_new_permissions(self):
        common = fonte('js/common.js')
        self.assertIn('reports: ["relatorios:consultar"', common)
        self.assertIn('suppliers: "fornecedores:consultar"', common)
        self.assertIn('return allowed && searchable.includes(term)', common)  # busca filtrada por página
        self.assertIn('canAccessPage("reports")', fonte('js/dashboard.js'))
        mov = fonte('js/movimentacoes.js')
        self.assertIn('apiFornecedor.selecaoEntrada()', mov)
        self.assertNotIn('apiFornecedor.listar', mov)
