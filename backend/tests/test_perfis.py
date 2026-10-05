"""Etapa 2E — perfis e permissões. Testes isolados com doubles; NÃO comprovam PostgreSQL real.

Cobrem a matriz aprovada (D1–D8): ação × perfil no decorator HTTP, revalidação
persistida na transação, tipos de movimentação, registro × aplicação de contagem,
administração de usuários, proteções do último ADMINISTRADOR/autoalteração,
sessões antigas após rebaixamento/inativação e concorrência por locks de linha.
"""
from copy import deepcopy
import threading
import time
from unittest import TestCase
from unittest.mock import MagicMock, patch

from flask.sessions import SecureCookieSessionInterface
from psycopg2.errors import ForeignKeyViolation, UniqueViolation

from app import create_app
from core.errors import AuthorizationError, BusinessError, ValidationError
from core.permissoes import ALIASES, PERFIS, PERMISSOES, normalizar_perfil, permissoes_de, pode
from core.security import Security
from repository.movimentacao_repository import MovimentacaoRepository
from repository.usuario_repository import LOCK_ADMINISTRATIVO, UsuarioRepository
from schemas.usuario_dto import AcessoDTO, SignupDTO, UsuarioDTO
from services.contagem_service import ContagemService
from services.fornecedor_service import FornecedorService
from services.movimentacao_service import MovimentacaoService
from services.produto_service import ProdutoService
from services.rastreamento_service import CorredorService, ItemEstoqueService, PosicaoService
from services.usuario_service import UsuarioService
from test_contagens import CountConnection, CountDB

SENHA = 'test-only-password'
_HASH = []


def hash_teste():
    if not _HASH:
        _HASH.append(Security().hash_password(SENHA))
    return _HASH[0]


# Matriz aprovada (registro da decisão em ETAPA_2E_PERFIS_INVENTAIRE.md). Mantida aqui
# de forma literal e independente para que qualquer alteração em core/permissoes.py
# seja detectada. Etapa 2G: ampliação aprovada (D7) — fornecedores:consultar nos 4 perfis e
# fornecedores:gerenciar para GESTOR/ADMINISTRADOR; nenhuma outra decisão 2E alterada.
# Correção de perfil/acessos (pedido posterior do usuário, 05/10/2026), que altera a 2G: OPERADOR sem
# fornecedores:consultar e sem relatorios:consultar (nova); fornecedores:selecionar (opção B) para quem
# registra ENTRADA; perfil:editar_proprio nos 4 perfis. Resultados que mudaram: OPERADOR recebe 403 em
# /fornecedores, /fornecedores/<id>, /fornecedores/<id>/recebimentos e em todo /relatorios/*.
OPERACIONAIS = {'produtos:consultar', 'localizacoes:consultar', 'movimentacoes:consultar',
                'contagens:consultar', 'contagens:registrar', 'perfil:editar_proprio'}
CONSULTAS = OPERACIONAIS | {'fornecedores:consultar', 'relatorios:consultar'}
MATRIZ = {
    'OPERADOR': OPERACIONAIS | {'movimentacoes:entrada', 'movimentacoes:saida', 'fornecedores:selecionar'},
    'AUDITOR': CONSULTAS,
    'GESTOR': CONSULTAS | {'produtos:gerenciar', 'localizacoes:gerenciar', 'movimentacoes:entrada',
                           'movimentacoes:saida', 'movimentacoes:ajuste', 'contagens:aplicar',
                           'fornecedores:gerenciar', 'fornecedores:selecionar'},
    'ADMINISTRADOR': CONSULTAS | {'produtos:gerenciar', 'localizacoes:gerenciar', 'movimentacoes:entrada',
                                  'movimentacoes:saida', 'movimentacoes:ajuste', 'contagens:aplicar',
                                  'usuarios:gerenciar', 'fornecedores:gerenciar', 'fornecedores:selecionar'},
}
TODAS = set().union(*MATRIZ.values())
PERFIS_TESTE = ('operador', 'auditor', 'gestor', 'supervisor', 'admin', 'administrador',
                'OPERADOR', 'AUDITOR', 'GESTOR', 'ADMINISTRADOR', 'leitor', 'root', '')


class PermissoesTests(TestCase):
    def test_matrix_is_exactly_the_approved_one(self):
        self.assertEqual(set(PERFIS), set(MATRIZ))
        self.assertEqual({k: set(v) for k, v in PERMISSOES.items()}, MATRIZ)

    def test_approved_aliases_and_canonicals(self):
        esperado = {'admin': 'ADMINISTRADOR', 'administrador': 'ADMINISTRADOR', 'supervisor': 'GESTOR',
                    'gestor': 'GESTOR', 'operador': 'OPERADOR', 'auditor': 'AUDITOR'}
        self.assertEqual(ALIASES, esperado)
        for valor, canonico in esperado.items():
            for variante in (valor, valor.upper(), f'  {valor.title()}  ', canonico):
                self.assertEqual(normalizar_perfil(variante), canonico)

    def test_unknown_profiles_have_no_permission(self):
        for valor in ('leitor', 'root', 'superadmin', 'admin2', '', '   ', 'ad min', 'administrador\x00',
                      None, 1, True, ['admin'], {'perfil': 'admin'}):
            with self.subTest(valor=valor):
                self.assertIsNone(normalizar_perfil(valor))
                self.assertEqual(permissoes_de(valor), frozenset())
                self.assertFalse(any(pode(valor, p) for p in TODAS))

    def test_key_decisions(self):
        # D1, D3, D5, D7: decisões destacadas na aprovação.
        self.assertFalse(pode('operador', 'movimentacoes:ajuste'))
        self.assertTrue(all(pode(p, 'contagens:registrar') for p in PERFIS))
        self.assertEqual({p for p in PERFIS if pode(p, 'contagens:aplicar')}, {'GESTOR', 'ADMINISTRADOR'})
        self.assertEqual({p for p in PERFIS if pode(p, 'usuarios:gerenciar')}, {'ADMINISTRADOR'})
        self.assertFalse(any(pode('AUDITOR', p) for p in ('movimentacoes:entrada', 'movimentacoes:saida',
                                                          'movimentacoes:ajuste', 'contagens:aplicar')))


# ── Decorator HTTP: cada rota × cada perfil ────────────────────────────────

ROTAS = [
    ('get', '/produtos', None, {'produtos:consultar'}),
    ('get', '/produtos/7', None, {'produtos:consultar'}),
    ('post', '/produtos', {}, {'produtos:gerenciar'}),
    ('put', '/produtos/7', {}, {'produtos:gerenciar'}),
    ('delete', '/produtos/7', None, {'produtos:gerenciar'}),
    ('get', '/movimentacoes', None, {'movimentacoes:consultar'}),
    ('get', '/movimentacoes/1', None, {'movimentacoes:consultar'}),
    ('get', '/produtos/7/movimentacoes', None, {'movimentacoes:consultar'}),
    ('post', '/produtos/7/movimentacoes', {}, {'movimentacoes:entrada', 'movimentacoes:saida', 'movimentacoes:ajuste'}),
    ('get', '/corredores', None, {'localizacoes:consultar'}),
    ('get', '/corredores/1', None, {'localizacoes:consultar'}),
    ('get', '/corredores/1/posicoes', None, {'localizacoes:consultar'}),
    ('get', '/posicoes', None, {'localizacoes:consultar'}),
    ('get', '/posicoes/1', None, {'localizacoes:consultar'}),
    ('get', '/itens-estoque', None, {'localizacoes:consultar'}),
    ('get', '/itens-estoque/1', None, {'localizacoes:consultar'}),
    ('get', '/produtos/7/itens-estoque', None, {'localizacoes:consultar'}),
    ('get', '/posicoes/1/itens-estoque', None, {'localizacoes:consultar'}),
    ('post', '/corredores', {}, {'localizacoes:gerenciar'}),
    ('put', '/corredores/1', {}, {'localizacoes:gerenciar'}),
    ('post', '/posicoes', {}, {'localizacoes:gerenciar'}),
    ('put', '/posicoes/1', {}, {'localizacoes:gerenciar'}),
    ('get', '/contagens', None, {'contagens:consultar'}),
    ('get', '/contagens/1', None, {'contagens:consultar'}),
    ('get', '/produtos/7/contagens', None, {'contagens:consultar'}),
    ('get', '/itens-estoque/1/contagens', None, {'contagens:consultar'}),
    ('post', '/contagens', {}, {'contagens:registrar'}),
    ('post', '/contagens/1/aplicar', {}, {'contagens:aplicar'}),
    ('get', '/users', None, {'usuarios:gerenciar'}),
    ('get', '/users/ativos', None, {'usuarios:gerenciar'}),
    ('post', '/signup', dict(email='novo@example.invalid', password=SENHA, nome='N', sobrenome='S', perfil='OPERADOR'),
     {'usuarios:gerenciar'}),
    ('put', '/users', {}, {'usuarios:gerenciar'}),
    ('patch', '/users/2/acesso', {}, {'usuarios:gerenciar'}),
    ('delete', '/users/2', None, {'usuarios:gerenciar'}),
    # Etapa 2G (recebimentos exige as duas permissões: coberto em test_fornecedores.py).
    ('get', '/fornecedores', None, {'fornecedores:consultar'}),
    ('get', '/fornecedores/1', None, {'fornecedores:consultar'}),
    ('post', '/fornecedores', {}, {'fornecedores:gerenciar'}),
    ('put', '/fornecedores/1', {}, {'fornecedores:gerenciar'}),
    ('delete', '/fornecedores/1', None, {'fornecedores:gerenciar'}),
]
SERVICOS = {
    ProdutoService: ('listar_produtos', 'obter_produto_por_id', 'registrar_produto', 'atualizar_produto', 'deletar_produto'),
    MovimentacaoService: ('listar', 'obter', 'registrar'),
    CorredorService: ('listar', 'obter', 'salvar'),
    PosicaoService: ('listar', 'obter', 'salvar'),
    ItemEstoqueService: ('listar', 'obter'),
    ContagemService: ('listar', 'obter', 'registrar', 'aplicar'),
    FornecedorService: ('listar', 'obter', 'criar', 'atualizar', 'excluir', 'recebimentos'),
    UsuarioService: ('listar_usuarios', 'listar_usuarios_ativos', 'signup', 'atualizar_usuario',
                     'alterar_acesso', 'deletar_usuario'),
}


class DecoratorMatrixTests(TestCase):
    def setUp(self):
        with patch('app.Connection.get_connection', return_value=MagicMock()):
            self.app = create_app()
        self.app.session_interface = SecureCookieSessionInterface()
        self.client = self.app.test_client()
        self.acesso = None
        self.servicos = []
        for cls, metodos in SERVICOS.items():
            for metodo in metodos:
                patcher = patch.object(cls, metodo, return_value={'ok': True})
                self.servicos.append(patcher.start())
                self.addCleanup(patcher.stop)
        patcher = patch.object(UsuarioRepository, 'obter_acesso', side_effect=lambda _id: self.acesso)
        patcher.start()
        self.addCleanup(patcher.stop)

    def login(self, perfil, ativo=True, sessao=None):
        self.acesso = (ativo, perfil)
        with self.client.session_transaction() as s:
            s.update(user_id=2, **(sessao or {}))

    def call(self, method, path, body):
        return getattr(self.client, method)(path, json=body) if body is not None else getattr(self.client, method)(path)

    def chamadas(self):
        return sum(m.call_count for m in self.servicos)

    def test_every_route_for_every_profile(self):
        for perfil in PERFIS_TESTE:
            permitidas = permissoes_de(perfil)
            for method, path, body, exigidas in ROTAS:
                with self.subTest(perfil=perfil, method=method, path=path):
                    self.login(perfil)
                    antes = self.chamadas()
                    status = self.call(method, path, body).status_code
                    if permitidas & exigidas:
                        self.assertNotIn(status, (401, 403))
                        self.assertEqual(self.chamadas(), antes + 1)
                    else:
                        self.assertEqual(status, 403)
                        self.assertEqual(self.chamadas(), antes)

    def test_no_session_is_401_everywhere(self):
        for method, path, body, _ in ROTAS:
            with self.subTest(path=path, method=method):
                self.assertEqual(self.call(method, path, body).status_code, 401)
        self.assertEqual(self.chamadas(), 0)

    def test_inactive_or_removed_user_is_403_and_session_revoked(self):
        for acesso in ((False, 'ADMINISTRADOR'), None):
            for method, path, body, _ in ROTAS:
                with self.subTest(acesso=acesso, path=path):
                    self.login('ADMINISTRADOR')
                    self.acesso = acesso
                    response = self.call(method, path, body)
                    self.assertEqual((response.status_code, response.json), (403, {'message': 'Usuário inativo'}))
                    self.assertEqual(self.client.get('/produtos').status_code, 401)
        self.assertEqual(self.chamadas(), 0)

    def test_session_flags_query_and_payload_never_grant(self):
        # Sessão forjada/antiga dizendo admin; perfil persistido é OPERADOR.
        self.login('OPERADOR', sessao=dict(user_perfil='admin', user_admin=True, user_ativo=True, perfil='ADMINISTRADOR'))
        for path in ('/users', '/users?perfil=ADMINISTRADOR&admin=true', '/users/ativos?admin=1'):
            self.assertEqual(self.client.get(path).status_code, 403)
        forged = {'perfil': 'ADMINISTRADOR', 'admin': True, 'permissoes': ['usuarios:gerenciar']}
        self.assertEqual(self.client.patch('/users/2/acesso', json=forged).status_code, 403)
        self.assertEqual(self.client.post('/produtos', json=forged).status_code, 403)
        self.assertEqual(self.chamadas(), 0)

    def test_authorization_failure_is_generic_500(self):
        self.login('ADMINISTRADOR')
        with patch.object(UsuarioRepository, 'obter_acesso', side_effect=RuntimeError('detalhe interno')):
            response = self.client.get('/produtos')
        self.assertEqual((response.status_code, response.json), (500, {'message': 'Erro interno do servidor'}))

    def test_logout_remains_available_for_unknown_profile(self):
        self.login('leitor')
        self.assertEqual(self.client.post('/logout').status_code, 200)
        self.assertEqual(self.client.get('/produtos').status_code, 401)


# ── Revalidação transacional ───────────────────────────────────────────────

class Cursor:
    def __init__(self, row):
        self.row, self.queries = row, []

    def execute(self, query, params=()):
        self.queries.append(query)

    def fetchone(self):
        return self.row


class TransactionalAuthorizationTests(TestCase):
    def test_matrix_inside_transaction_and_lock_mode(self):
        for perfil in PERFIS_TESTE:
            for permissao in sorted(TODAS):
                for escrita in (False, True):
                    with self.subTest(perfil=perfil, permissao=permissao, escrita=escrita):
                        cursor = Cursor((True, perfil))
                        if pode(perfil, permissao):
                            MovimentacaoRepository.autorizar_usuario(cursor, 23, permissao, escrita=escrita)
                        else:
                            with self.assertRaises(AuthorizationError):
                                MovimentacaoRepository.autorizar_usuario(cursor, 23, permissao, escrita=escrita)
                        self.assertEqual(cursor.queries[0].endswith('FOR SHARE'), escrita)

    def test_inactive_missing_and_non_boolean_active(self):
        for row in ((False, 'ADMINISTRADOR'), None, ('t', 'ADMINISTRADOR'), (1, 'ADMINISTRADOR')):
            with self.subTest(row=row), self.assertRaises(AuthorizationError):
                MovimentacaoRepository.autorizar_usuario(Cursor(row), 23, 'produtos:consultar')


class MovementTypeTests(TestCase):
    """Mesmo endpoint, permissões distintas por tipo (D1), sobre todas as camadas reais."""

    def setUp(self):
        self.db = CountDB()
        self.conn = CountConnection(self.db)
        with patch('app.Connection.get_connection', return_value=self.conn):
            self.app = create_app()
        self.app.session_interface = SecureCookieSessionInterface()
        self.client = self.app.test_client()
        self.addCleanup(self.conn.rollback)
        MovimentacaoService(CountConnection(self.db)).registrar(7, 23, dict(tipo='ENTRADA', quantidade=10, id_posicao=1, lote='L-1'))

    def login(self, role):
        self.db.active, self.db.role = True, role
        with self.client.session_transaction() as s:
            s.update(user_id=23)

    def post(self, tipo, valor=1, **extra):
        campo = 'novo_saldo_item' if tipo == 'AJUSTE' else 'quantidade'
        return self.client.post('/produtos/7/movimentacoes', json={'tipo': tipo, campo: valor, 'id_posicao': 1,
                                                                   'lote': 'L-1', 'motivo': 'Teste', **extra})

    def estado(self):
        return deepcopy((self.db.products, self.db.items, self.db.movements))

    def test_types_per_profile(self):
        esperado = {'operador': (201, 201, 403), 'gestor': (201, 201, 201), 'supervisor': (201, 201, 201),
                    'admin': (201, 201, 201), 'ADMINISTRADOR': (201, 201, 201), 'auditor': (403, 403, 403),
                    'leitor': (403, 403, 403)}
        for role, (entrada, saida, ajuste) in esperado.items():
            with self.subTest(role=role):
                self.login(role)
                for tipo, status in (('ENTRADA', entrada), ('SAIDA', saida), ('AJUSTE', ajuste)):
                    antes = self.estado()
                    self.assertEqual(self.post(tipo, 10 if tipo == 'AJUSTE' else 1).status_code, status, tipo)
                    if status == 403:
                        self.assertEqual(self.estado(), antes)

    def test_operator_cannot_reach_adjustment_through_entry_permission(self):
        self.login('operador')
        antes = self.estado()
        for body in ({'tipo': 'AJUSTE', 'novo_saldo_item': 0, 'id_posicao': 1, 'lote': 'L-1', 'motivo': 'x'},
                     {'tipo': 'ajuste', 'quantidade': 1}, {'tipo': 'ENTRADA', 'quantidade': 1, 'tipo_real': 'AJUSTE'},
                     {'tipo': 'ENTRADA', 'quantidade': 1, 'perfil': 'GESTOR'}):
            self.assertIn(self.client.post('/produtos/7/movimentacoes', json=body).status_code, (400, 403))
        self.assertEqual(self.estado(), antes)
        rollbacks = self.conn.rollbacks
        self.assertEqual(self.post('AJUSTE', 0).status_code, 403)
        self.assertEqual(self.conn.rollbacks, rollbacks + 1)  # rejeição sob lock é revertida

    def test_old_session_after_demotion_and_promotion(self):
        self.login('gestor')
        self.assertEqual(self.post('AJUSTE', 9).status_code, 201)
        self.db.role = 'OPERADOR'  # rebaixado por um administrador; nenhuma nova autenticação
        antes = self.estado()
        self.assertEqual(self.post('AJUSTE', 8).status_code, 403)
        self.assertEqual(self.estado(), antes)
        self.assertEqual(self.post('ENTRADA').status_code, 201)
        self.db.role = 'GESTOR'  # elevado: vale na requisição seguinte
        self.assertEqual(self.post('AJUSTE', 5).status_code, 201)


class CountPermissionTests(TestCase):
    """Registro de contagem (todos) × aplicação (GESTOR/ADMINISTRADOR), com permissão própria."""

    def setUp(self):
        self.db = CountDB()
        self.conn = CountConnection(self.db)
        with patch('app.Connection.get_connection', return_value=self.conn):
            self.app = create_app()
        self.app.session_interface = SecureCookieSessionInterface()
        self.client = self.app.test_client()
        self.addCleanup(self.conn.rollback)
        MovimentacaoService(CountConnection(self.db)).registrar(7, 23, dict(tipo='ENTRADA', quantidade=5, id_posicao=1, lote='L-1'))

    def login(self, role):
        self.db.active, self.db.role = True, role
        with self.client.session_transaction() as s:
            s.update(user_id=23)

    def estado(self):
        return deepcopy((self.db.products, self.db.items, self.db.movements, self.db.counts))

    def test_register_and_apply_per_profile(self):
        for role in ('operador', 'auditor', 'gestor', 'admin'):
            with self.subTest(role=role):
                self.login(role)
                created = self.client.post('/contagens', json={'id_item_estoque': 1, 'quantidade_fisica': 7})
                self.assertEqual(created.status_code, 201)
                self.assertEqual(created.json['id_usuario'], 23)
        estoque = deepcopy((self.db.products, self.db.items, self.db.movements))
        for role in ('operador', 'auditor', 'leitor'):
            self.login(role)
            antes = self.estado()
            self.assertEqual(self.client.post('/contagens/1/aplicar', json={'motivo': 'x'}).status_code, 403)
            self.assertEqual(self.estado(), antes)
        self.assertEqual(deepcopy((self.db.products, self.db.items, self.db.movements)), estoque)
        # D4: sem separação obrigatória — gestor aplica a contagem que registrou.
        self.login('gestor')
        self.assertEqual(self.client.post('/contagens/3/aplicar', json={'motivo': 'Confirmado'}).status_code, 201)
        self.assertEqual(self.client.get('/produtos/7/itens-estoque').json[0]['quantidade'], 7)

    def test_unknown_profile_cannot_register_and_forged_fields_rejected(self):
        self.login('leitor')
        antes = self.estado()
        self.assertEqual(self.client.post('/contagens', json={'id_item_estoque': 1, 'quantidade_fisica': 7}).status_code, 403)
        self.login('operador')
        for extra in ({'perfil': 'GESTOR'}, {'admin': True}, {'id_usuario': 1}):
            self.assertEqual(self.client.post('/contagens', json={'id_item_estoque': 1, 'quantidade_fisica': 7, **extra}).status_code, 400)
        self.assertEqual(self.estado(), antes)

    def test_apply_revalidated_inside_transaction(self):
        self.login('gestor')
        self.client.post('/contagens', json={'id_item_estoque': 1, 'quantidade_fisica': 9})
        antes = self.estado()
        # Decorator já teria passado (GESTOR); antes do lock o perfil vira AUDITOR.
        self.db.role = 'AUDITOR'
        with self.assertRaises(AuthorizationError):
            ContagemService(CountConnection(self.db)).aplicar(1, 23, {'motivo': 'x'})
        self.assertEqual(self.estado(), antes)


# ── Administração de usuários ──────────────────────────────────────────────

class UsersDB:
    def __init__(self):
        self.users, self.referenced, self.locks = {}, set(), {}
        self.guard = threading.Lock()
        self.after_admin_lock = None

    def add(self, uid, perfil, ativo=True):
        self.users[uid] = dict(nome=f'Nome{uid}', sobrenome='Teste', email=f'u{uid}@example.invalid',
                               senha=hash_teste(), perfil=perfil, unidade='U1', telefone='1', ativo=ativo, acesso=None)

    def lock(self, uid):
        with self.guard:
            return self.locks.setdefault(uid, threading.Lock())

    def public(self, uid):
        u = self.users[uid]
        return (uid, u['nome'], u['sobrenome'], u['email'], u['perfil'], u['unidade'], u['telefone'], u['ativo'], u['acesso'])


class UsersConn:
    """Double com lock exclusivo por linha (FOR SHARE/FOR UPDATE) e reavaliação após lock."""

    def __init__(self, db):
        self.db, self.held, self.undo, self.queries = db, [], {}, []
        self.result, self.rowcount, self.commits, self.rollbacks = None, 0, 0, 0

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def hold(self, uid):
        if uid not in self.held:
            if not self.db.lock(uid).acquire(timeout=5):
                raise AssertionError('Timeout de lock (possível deadlock)')
            self.held.append(uid)

    def touch(self, uid):
        self.undo.setdefault(uid, deepcopy(self.db.users.get(uid)))

    def execute(self, q, p=()):
        self.queries.append((q, p))
        db = self.db
        if q.startswith('SELECT ativo, perfil FROM usuarios'):
            if 'FOR SHARE' in q or 'FOR UPDATE' in q:  # FOR UPDATE: PATCH /me/perfil
                self.hold(p[0])
            u = db.users.get(p[0])
            self.result = (u['ativo'], u['perfil']) if u else None
        elif q == LOCK_ADMINISTRATIVO:
            ator, alvo, aliases = p
            match = lambda i, u: i in (ator, alvo) or (u['ativo'] is True and u['perfil'].strip().lower() in aliases)
            for uid in sorted(i for i, u in db.users.items() if match(i, u)):
                self.hold(uid)
            # Reavaliação após o lock (como o PostgreSQL faz com linhas alteradas).
            self.result = [(i, u['ativo'], u['perfil']) for i, u in sorted(db.users.items())
                           if i in self.held and match(i, u)]
            if db.after_admin_lock:
                db.after_admin_lock(self)
        elif q.startswith('UPDATE usuarios SET'):
            uid = p[-1]
            self.touch(uid)
            campos = [c.split(' = ')[0] for c in q[len('UPDATE usuarios SET '):q.index(' WHERE')].split(', ')]
            db.users[uid].update(zip(campos, p[:-1]))
            self.rowcount = 1
        elif q.startswith('DELETE FROM usuarios'):
            if p[0] in db.referenced:
                raise ForeignKeyViolation()
            self.touch(p[0])
            self.rowcount = 1 if db.users.pop(p[0], None) else 0
        elif q.startswith('SELECT id_usuario, nome, sobrenome, email, perfil'):
            if 'WHERE id_usuario' in q:
                self.result = db.public(p[0]) if p[0] in db.users else None
            else:
                self.result = [db.public(i) for i in sorted(db.users)
                               if 'ativo = TRUE' not in q or db.users[i]['ativo']]
        elif q.startswith('SELECT id_usuario, nome, sobrenome, email, senha'):
            row = next(((i, u) for i, u in db.users.items() if u['email'] == p[0]), None)
            self.result = None if row is None else (row[0], *(row[1][k] for k in (
                'nome', 'sobrenome', 'email', 'senha', 'perfil', 'unidade', 'telefone', 'ativo', 'acesso')))
        elif q.startswith('INSERT INTO usuarios'):
            uid = max(db.users) + 1
            self.touch(uid)
            email, senha, nome, sobrenome, perfil, unidade, telefone = p
            db.users[uid] = dict(nome=nome, sobrenome=sobrenome, email=email, senha=senha, perfil=perfil,
                                 unidade=unidade, telefone=telefone, ativo=True, acesso=None)
            self.result = (uid,)
        else:
            raise AssertionError('Consulta não suportada pelo double: ' + q)

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.result

    def commit(self):
        self.commits += 1
        self.undo = {}
        self.release()

    def rollback(self):
        self.rollbacks += 1
        for uid, row in self.undo.items():
            if row is None:
                self.db.users.pop(uid, None)
            else:
                self.db.users[uid] = row
        self.undo = {}
        self.release()

    def release(self):
        for uid in self.held:
            self.db.lock(uid).release()
        self.held = []


class UserAdminHttpTests(TestCase):
    def setUp(self):
        self.db = UsersDB()
        self.db.add(1, 'admin')          # administrador legado preexistente (alias)
        self.db.add(2, 'ADMINISTRADOR')
        self.db.add(3, 'operador')
        self.db.add(4, 'supervisor')
        self.db.add(5, 'leitor')         # valor desconhecido: preservado, sem permissões
        self.conn = UsersConn(self.db)
        with patch('app.Connection.get_connection', return_value=self.conn):
            self.app = create_app()
        self.app.session_interface = SecureCookieSessionInterface()

    def client(self, uid):
        client = self.app.test_client()
        with client.session_transaction() as s:
            s.update(user_id=uid)
        return client

    def snapshot(self):
        return deepcopy(self.db.users)

    def assertNoLocks(self):
        self.assertFalse(any(lock.locked() for lock in self.db.locks.values()))

    def test_non_admin_profiles_cannot_administer(self):
        antes = self.snapshot()
        for uid in (3, 4, 5):
            c = self.client(uid)
            for method, path, body in (('get', '/users', None), ('get', '/users/ativos', None),
                                       ('post', '/signup', dict(email='x@example.invalid', password=SENHA, nome='X', sobrenome='Y', perfil='ADMINISTRADOR')),
                                       ('put', '/users', dict(id=uid, nome='X', sobrenome='Y', email=f'u{uid}@example.invalid', perfil='ADMINISTRADOR')),
                                       ('patch', f'/users/{uid}/acesso', {'perfil': 'ADMINISTRADOR'}),
                                       ('delete', '/users/1', None)):
                with self.subTest(uid=uid, path=path, method=method):
                    response = getattr(c, method)(path, json=body) if body else getattr(c, method)(path)
                    self.assertEqual(response.status_code, 403)
        self.assertEqual(self.snapshot(), antes)

    def test_list_serializes_canonical_profiles_without_password(self):
        users = self.client(1).get('/users').json
        self.assertEqual([u['perfil'] for u in users], ['ADMINISTRADOR', 'ADMINISTRADOR', 'OPERADOR', 'GESTOR', 'leitor'])
        self.assertEqual([u['admin'] for u in users], [True, True, False, False, False])
        self.assertTrue(all('senha' not in u and 'password' not in u for u in users))

    def test_patch_changes_only_profile_and_activity(self):
        antes = self.snapshot()
        response = self.client(1).patch('/users/3/acesso', json={'perfil': ' supervisor '})
        self.assertEqual((response.status_code, response.json['perfil']), (200, 'GESTOR'))
        self.assertEqual(self.db.users[3]['perfil'], 'GESTOR')  # gravado na forma canônica
        response = self.client(1).patch('/users/3/acesso', json={'ativo': False})
        self.assertEqual((response.status_code, response.json['ativo']), (200, False))
        for campo in ('nome', 'sobrenome', 'email', 'senha', 'unidade', 'telefone', 'acesso'):
            self.assertEqual(self.db.users[3][campo], antes[3][campo])
        self.assertNotIn('senha', response.json)
        self.assertNoLocks()

    def test_patch_strict_validation_and_mass_assignment(self):
        antes = self.snapshot()
        c = self.client(1)
        for body in ({}, {'perfil': None, 'ativo': None}, {'perfil': 'root'}, {'perfil': ''}, {'perfil': 7},
                     {'ativo': 'false'}, {'ativo': 0}, {'perfil': 'GESTOR', 'senha': 'nova-senha'},
                     {'perfil': 'GESTOR', 'admin': True}, {'ativo': True, 'permissoes': ['usuarios:gerenciar']},
                     {'perfil': 'GESTOR', 'nome': 'Outro'}, {'perfil': 'GESTOR', 'id': 1}, [], 'GESTOR'):
            with self.subTest(body=body):
                self.assertEqual(c.patch('/users/3/acesso', json=body).status_code, 400)
        self.assertEqual(c.patch('/users/3/acesso', data='{', content_type='application/json').status_code, 400)
        self.assertEqual(c.patch('/users/999/acesso', json={'ativo': False}).status_code, 404)
        self.assertEqual(self.snapshot(), antes)
        self.assertNoLocks()

    def test_self_change_blocked_on_every_route(self):
        antes = self.snapshot()
        c = self.client(2)
        base = dict(id=2, nome='Nome2', sobrenome='Teste', email='u2@example.invalid', perfil='ADMINISTRADOR')
        for method, path, body in (('patch', '/users/2/acesso', {'perfil': 'GESTOR'}),
                                   ('patch', '/users/2/acesso', {'ativo': False}),
                                   ('put', '/users', {**base, 'perfil': 'OPERADOR'}),
                                   ('put', '/users', {**base, 'ativo': False}),
                                   ('delete', '/users/2', None)):
            with self.subTest(method=method, body=body):
                response = getattr(c, method)(path, json=body) if body else getattr(c, method)(path)
                self.assertEqual(response.status_code, 409)
        self.assertEqual(self.snapshot(), antes)
        self.assertNoLocks()
        # Alterações neutras no próprio cadastro continuam possíveis (perfil/atividade iguais).
        self.assertEqual(c.put('/users', json={**base, 'nome': 'Renomeado', 'perfil': 'administrador'}).status_code, 200)
        self.assertEqual(c.patch('/users/2/acesso', json={'perfil': 'ADMINISTRADOR', 'ativo': True}).status_code, 200)

    def test_put_preserves_password_and_activity_unless_sent(self):
        self.db.users[3]['ativo'] = False
        senha_antes = self.db.users[3]['senha']
        c = self.client(1)
        body = dict(id=3, nome='Novo', sobrenome='Nome', email='U3@Example.invalid', perfil='operador', unidade=None)
        self.assertEqual(c.put('/users', json=body).status_code, 200)
        u = self.db.users[3]
        self.assertEqual((u['nome'], u['email'], u['perfil'], u['ativo'], u['senha']),
                         ('Novo', 'u3@example.invalid', 'OPERADOR', False, senha_antes))
        self.assertEqual(c.put('/users', json={**body, 'senha': None}).status_code, 200)
        self.assertEqual(self.db.users[3]['senha'], senha_antes)
        self.assertEqual(c.put('/users', json={**body, 'senha': 'outra-senha-teste'}).status_code, 200)
        self.assertNotEqual(self.db.users[3]['senha'], senha_antes)
        self.assertTrue(Security().check_password('outra-senha-teste', self.db.users[3]['senha']))

    def test_put_rejects_empty_password_unknown_profile_and_forged_fields(self):
        antes = self.snapshot()
        c = self.client(1)
        body = dict(id=3, nome='Novo', sobrenome='Nome', email='u3@example.invalid', perfil='OPERADOR')
        for extra in ({'senha': ''}, {'senha': '   '}, {'perfil': 'root'}, {'perfil': None}, {'admin': True},
                      {'password': SENHA}, {'permissoes': []}, {'acesso': '2020-01-01'}, {'ativo': 'true'}):
            with self.subTest(extra=extra):
                self.assertEqual(c.put('/users', json={**body, **extra}).status_code, 400)
        self.assertEqual(c.put('/users', json={**body, 'id': 999}).status_code, 404)
        self.assertEqual(self.snapshot(), antes)

    def test_put_duplicate_email_is_conflict_with_rollback(self):
        class EmailDuplicado(UniqueViolation):
            @property
            def diag(self):
                return type('Diag', (), {'constraint_name': 'usuarios_email_key'})()
        antes = self.snapshot()
        original = self.conn.execute

        def falhar(q, p=()):
            if q.startswith('UPDATE usuarios SET'):
                raise EmailDuplicado()
            return original(q, p)
        with patch.object(self.conn, 'execute', side_effect=falhar):
            response = self.client(1).put('/users', json=dict(id=3, nome='A', sobrenome='B', email='u4@example.invalid', perfil='OPERADOR'))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.snapshot(), antes)
        self.assertNoLocks()

    def test_delete_rules(self):
        c = self.client(1)
        self.db.referenced.add(3)
        antes = self.snapshot()
        rollbacks = self.conn.rollbacks
        response = c.delete('/users/3')
        self.assertEqual(response.status_code, 409)
        self.assertIn('histórico', response.json['message'])
        self.assertEqual(self.snapshot(), antes)
        self.assertEqual(self.conn.rollbacks, rollbacks + 1)
        self.assertEqual(c.delete('/users/1').status_code, 409)  # autoexclusão
        self.assertEqual(c.delete('/users/999').status_code, 404)
        self.assertEqual(c.delete('/users/4').status_code, 200)
        self.assertNotIn(4, self.db.users)
        self.assertNoLocks()

    def test_admin_can_remove_other_admin_while_one_remains(self):
        self.assertEqual(self.client(1).patch('/users/2/acesso', json={'ativo': False}).status_code, 200)
        # Restou apenas o 1: a sessão antiga do 2 perde acesso imediatamente.
        c2 = self.client(2)
        self.assertEqual(c2.get('/users').status_code, 403)
        self.assertEqual(c2.get('/users').status_code, 401)

    def test_signup_validates_and_stores_canonical_profile(self):
        c = self.client(1)
        base = dict(email='novo@example.invalid', password=SENHA, nome='N', sobrenome='S')
        for perfil in ('root', '', None, 'leitor'):
            self.assertEqual(c.post('/signup', json={**base, 'perfil': perfil}).status_code, 400)
        for extra in ({'admin': True}, {'ativo': False}, {'permissoes': ['x']}):
            self.assertEqual(c.post('/signup', json={**base, 'perfil': 'OPERADOR', **extra}).status_code, 400)
        self.assertEqual(c.post('/signup', json={**base, 'perfil': ' Auditor '}).status_code, 201)
        novo = max(self.db.users)
        self.assertEqual(self.db.users[novo]['perfil'], 'AUDITOR')

    def test_demotion_and_promotion_take_effect_without_new_login(self):
        c2 = self.client(2)
        self.assertEqual(c2.get('/users').status_code, 200)
        self.assertEqual(self.client(1).patch('/users/2/acesso', json={'perfil': 'GESTOR'}).status_code, 200)
        self.assertEqual(c2.get('/users').status_code, 403)
        sessao = c2.get('/session').json['user']
        self.assertEqual((sessao['perfil'], sessao['admin']), ('GESTOR', False))
        self.assertNotIn('usuarios:gerenciar', sessao['permissoes'])
        self.assertEqual(self.client(1).patch('/users/2/acesso', json={'perfil': 'ADMINISTRADOR'}).status_code, 200)
        self.assertEqual(c2.get('/users').status_code, 200)

    def test_session_fields_and_unknown_profile(self):
        sessao = self.client(4).get('/session')
        self.assertEqual(sessao.status_code, 200)
        user = sessao.json['user']
        for campo in ('id', 'nome', 'sobrenome', 'email', 'perfil', 'unidade', 'telefone', 'admin', 'ativo'):
            self.assertIn(campo, user)
        self.assertEqual((user['perfil'], user['admin'], user['perfil_reconhecido']), ('GESTOR', False, True))
        self.assertEqual(set(user['permissoes']), MATRIZ['GESTOR'])
        self.assertNotIn('senha', user)
        desconhecido = self.client(5)
        user = desconhecido.get('/session').json['user']
        self.assertEqual((user['perfil'], user['permissoes'], user['perfil_reconhecido']), ('leitor', [], False))
        self.assertEqual(desconhecido.get('/users').status_code, 403)
        self.assertEqual(desconhecido.post('/logout').status_code, 200)

    def test_session_revoked_for_inactive_or_removed(self):
        c3 = self.client(3)
        self.db.users[3]['ativo'] = False
        self.assertEqual(c3.get('/session').status_code, 403)
        self.assertEqual(c3.get('/session').status_code, 401)
        c4 = self.client(4)
        del self.db.users[4]
        self.assertEqual(c4.get('/session').status_code, 403)
        self.assertEqual(c4.get('/session').status_code, 401)

    def test_login_keeps_only_identity_in_session(self):
        client = self.app.test_client()
        # regenerate() é do Flask-Session/Redis (coberto na suíte Redis opt-in).
        self.app.session_interface.regenerate = lambda sessao: None
        response = client.post('/login', json={'email': 'u4@example.invalid', 'password': SENHA})
        self.assertEqual(response.status_code, 200)
        user = response.json['user']
        self.assertEqual((user['perfil'], user['admin']), ('GESTOR', False))
        self.assertEqual(set(user['permissoes']), MATRIZ['GESTOR'])
        with client.session_transaction() as s:
            self.assertFalse({'user_perfil', 'user_admin', 'user_ativo'} & set(s))
            self.assertEqual(s['user_id'], 4)


class AdminLockTests(TestCase):
    def setUp(self):
        self.db = UsersDB()
        for uid, perfil in ((1, 'admin'), (2, 'ADMINISTRADOR'), (3, 'OPERADOR')):
            self.db.add(uid, perfil)

    def repo(self):
        return UsuarioRepository(UsersConn(self.db))

    def admins_ativos(self):
        return {i for i, u in self.db.users.items() if u['ativo'] and normalizar_perfil(u['perfil']) == 'ADMINISTRADOR'}

    def test_lock_is_single_ordered_statement_without_prior_actor_lock(self):
        conn = UsersConn(self.db)
        UsuarioRepository(conn).alterar_acesso(2, 3, 'AUDITOR', None)
        primeira = conn.queries[0][0]
        self.assertEqual(primeira, LOCK_ADMINISTRATIVO)
        self.assertIn('ORDER BY id_usuario FOR UPDATE', primeira)
        self.assertFalse(any('FOR SHARE' in q for q, _ in conn.queries))
        self.assertEqual(conn.queries[0][1][2], ('admin', 'administrador'))

    def test_last_admin_rule_unit(self):
        proteger = UsuarioRepository._proteger
        alvo = (True, 'ADMINISTRADOR')
        for perfil, ativo, excluir in (('GESTOR', True, False), ('ADMINISTRADOR', False, False), (None, None, True)):
            with self.assertRaises(BusinessError):
                proteger(9, 5, alvo, {5}, perfil, ativo, excluir)
        proteger(9, 5, alvo, {5, 9}, 'GESTOR', True)
        proteger(9, 6, (True, 'OPERADOR'), {5}, 'AUDITOR', False)

    def test_inactive_or_non_admin_actor_rejected_under_lock(self):
        self.db.users[2]['ativo'] = False
        antes = deepcopy(self.db.users)
        for ator in (2, 3, 99):
            with self.assertRaises(AuthorizationError):
                self.repo().alterar_acesso(ator, 1, 'OPERADOR', None)
        self.assertEqual(self.db.users, antes)
        self.assertFalse(any(lock.locked() for lock in self.db.locks.values()))

    def _concorrentes(self, primeira, segunda):
        entrou, liberar = threading.Event(), threading.Event()

        def hook(conn):
            if not entrou.is_set():
                entrou.set()
                liberar.wait(5)
        self.db.after_admin_lock = hook
        resultados = {}

        def run(nome, args):
            try:
                self.repo().alterar_acesso(*args)
                resultados[nome] = 'ok'
            except Exception as error:  # noqa: BLE001
                resultados[nome] = type(error).__name__
        t1 = threading.Thread(target=run, args=('primeira', primeira))
        t1.start()
        self.assertTrue(entrou.wait(5))
        t2 = threading.Thread(target=run, args=('segunda', segunda))
        t2.start()
        time.sleep(0.2)
        self.assertTrue(t2.is_alive())  # bloqueada pelo lock da primeira
        liberar.set()
        t1.join(5)
        t2.join(5)
        self.assertFalse(t1.is_alive() or t2.is_alive())  # nenhum deadlock
        self.assertFalse(any(lock.locked() for lock in self.db.locks.values()))
        return resultados

    def test_two_admins_removing_each_other_concurrently(self):
        resultados = self._concorrentes((1, 2, None, False), (2, 1, None, False))
        self.assertEqual(resultados, {'primeira': 'ok', 'segunda': 'AuthorizationError'})
        self.assertEqual(self.admins_ativos(), {1})

    def test_two_admins_demoting_each_other_concurrently(self):
        resultados = self._concorrentes((2, 1, 'GESTOR', None), (1, 2, 'GESTOR', None))
        self.assertEqual(resultados, {'primeira': 'ok', 'segunda': 'AuthorizationError'})
        self.assertEqual(self.admins_ativos(), {2})
        self.assertEqual(self.db.users[1]['perfil'], 'GESTOR')

    def test_third_admin_removing_two_concurrently_keeps_itself(self):
        self.db.add(4, 'administrador')
        resultados = self._concorrentes((4, 1, None, False), (4, 2, None, False))
        self.assertEqual(resultados, {'primeira': 'ok', 'segunda': 'ok'})
        self.assertEqual(self.admins_ativos(), {4})

    def test_operation_under_share_lock_completes_before_concurrent_demotion(self):
        # Escrita de estoque do usuário 3 já autorizada sob FOR SHARE (lock da linha 3).
        movimento = UsersConn(self.db)
        MovimentacaoRepository.autorizar_usuario(movimento, 3, 'movimentacoes:entrada', escrita=True)
        terminou = threading.Event()

        def rebaixar():
            self.repo().alterar_acesso(1, 3, 'AUDITOR', None)
            terminou.set()
        t = threading.Thread(target=rebaixar)
        t.start()
        self.assertFalse(terminou.wait(0.2))  # espera a operação em curso
        self.assertEqual(self.db.users[3]['perfil'], 'OPERADOR')
        movimento.commit()  # a operação autorizada conclui (sem cancelamento retroativo)
        t.join(5)
        self.assertTrue(terminou.is_set())
        self.assertEqual(self.db.users[3]['perfil'], 'AUDITOR')
        with self.assertRaises(AuthorizationError):  # a próxima operação já é negada
            MovimentacaoRepository.autorizar_usuario(UsersConn(self.db), 3, 'movimentacoes:entrada', escrita=True)

    def test_admin_with_own_stock_operation_in_flight_does_not_invert_locks(self):
        movimento = UsersConn(self.db)
        MovimentacaoRepository.autorizar_usuario(movimento, 1, 'movimentacoes:ajuste', escrita=True)
        terminou = threading.Event()

        def administrar():
            self.repo().alterar_acesso(1, 3, 'GESTOR', None)
            terminou.set()
        t = threading.Thread(target=administrar)
        t.start()
        self.assertFalse(terminou.wait(0.2))
        movimento.commit()
        t.join(5)
        self.assertTrue(terminou.is_set())
        self.assertEqual(self.db.users[3]['perfil'], 'GESTOR')


class UserDTOTests(TestCase):
    def test_acesso_dto(self):
        self.assertEqual(AcessoDTO.from_dict({'perfil': 'supervisor'}), AcessoDTO('GESTOR', None))
        self.assertEqual(AcessoDTO.from_dict({'ativo': False}), AcessoDTO(None, False))
        for body in (None, [], {}, {'perfil': 'x'}, {'ativo': 1}, {'senha': 'x', 'ativo': True}):
            with self.assertRaises(ValidationError):
                AcessoDTO.from_dict(body)

    def test_signup_and_update_profiles_are_canonical(self):
        base = dict(email='A@example.invalid', password=SENHA, nome='A', sobrenome='B')
        self.assertEqual(SignupDTO.from_dict({**base, 'perfil': 'admin'}).perfil, 'ADMINISTRADOR')
        dto = UsuarioDTO.from_dict(dict(id=1, nome='A', sobrenome='B', email='a@example.invalid', perfil='gestor'))
        self.assertEqual((dto.perfil, dto.senha, dto.ativo), ('GESTOR', None, None))
