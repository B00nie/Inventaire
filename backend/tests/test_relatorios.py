"""Etapa 2F isolada — reposição, dashboard e relatórios. Nenhum PostgreSQL.

O double interpreta somente o SQL 2F (rótulos /* 2F:... */), avaliando cada predicado
do WHERE na ordem textual e consumindo os parâmetros na mesma ordem do PostgreSQL.
Ele RECUSA qualquer escrita/lock (INSERT/UPDATE/DELETE/DDL/FOR SHARE...), exige que a
transação comece com SET TRANSACTION ... READ ONLY e conta commits (devem ser zero).
Não homologa planos, índices nem o SQL no PostgreSQL real (reservado à 2F.2).
"""
from datetime import date, datetime, timedelta, timezone
import json
import re
from unittest import TestCase
from unittest.mock import patch

from flask.sessions import SecureCookieSessionInterface
from psycopg2.errors import UndefinedTable

from app import create_app
from core.errors import AuthorizationError, NotFoundError, SchemaPendingError, ValidationError
import repository.relatorios_repository as relatorios_repository
from repository.relatorios_repository import JANELA_DASHBOARD
from repository.usuario_repository import UsuarioRepository
from schemas.relatorios_dto import (FiltroDivergenciasDTO, FiltroMovimentacoesDTO, FiltroProdutosDTO,
                                    FiltroSaidasDTO, parametros)
from services.relatorios_service import RelatoriosService, reposicao
from test_contagens import CAMPOS_RESPOSTA as CAMPOS_CONTAGEM

T0 = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)  # = 2026-10-01T00:00:00-03:00
INICIO, FIM = '2026-10-01T00:00:00-03:00', '2026-10-08T00:00:00-03:00'  # [T0, T0 + 7 dias)
ROTAS = ('/dashboard/resumo', '/relatorios/posicao-estoque', '/relatorios/estoque-baixo',
         f'/relatorios/movimentacoes?data_inicio={INICIO}&data_fim={FIM}', '/relatorios/divergencias',
         f'/relatorios/saidas-periodo?data_inicio={INICIO}&data_fim={FIM}')
CAMPOS_MOVIMENTACAO = {'id_movimentacao', 'id_produto', 'id_usuario', 'tipo', 'quantidade', 'estoque_anterior',
                       'estoque_posterior', 'motivo', 'data_hora', 'produto_nome', 'produto_codigo', 'usuario_nome',
                       'id_item_estoque', 'id_posicao', 'lote', 'codigo_posicao', 'corredor',
                       'quantidade_item_anterior', 'quantidade_item_posterior'}
CAMPOS_PRODUTO = {'id_produto', 'nome', 'codigo', 'categoria', 'estoque', 'quantidade_min', 'estoque_baixo',
                  'sem_saldo', 'situacao_reposicao', 'quantidade_sugerida', 'localizacao_legada', 'validade'}
PROIBIDOS = re.compile(r'\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE|COPY|LOCK|CALL|'
                       r'NEXTVAL|SETVAL)\b|FOR SHARE', re.I)
# Literal independente do código: se o repository mudar o modo da transação, o double recusa.
SOMENTE_LEITURA = 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'
SENSIVEIS = ('senha', 'password', 'hash', 'email', 'cookie', 'session', 'sessao', 'token')


# ── Double ───────────────────────────────────────────────────────────────────

class ReportDB:
    def __init__(self):
        self.users = {1: (True, 'ADMINISTRADOR')}
        self.names = {1: 'Ana Teste'}
        self.products, self.items, self.movements, self.counts = {}, {}, {}, {}
        self.corridors = {1: 'A', 2: 'B'}
        self.positions = {1: ['A-01', 1], 2: ['A-02', 1], 3: ['B-01', 2]}
        self.tables = {'produtos', 'usuarios', 'movimentacoes', 'corredores', 'posicoes_estoque', 'itens_estoque',
                       'contagens_inventario'}
        self.clock = T0 + timedelta(days=8)
        self.fail = None

    def product(self, pid, nome, estoque, minimo, categoria='Embalagem', validade=None):
        self.products[pid] = dict(id=pid, nome=nome, codigo=f'{nome[:2].upper()}-{pid:02d}', categoria=categoria,
                                  estoque=estoque, minimo=minimo, localizacao=f'Prateleira legada {pid}',
                                  validade=validade)

    def item(self, iid, produto, posicao, lote, quantidade, status='DISPONIVEL'):
        self.items[iid] = dict(id=iid, produto=produto, posicao=posicao, lote=lote, quantidade=quantidade,
                               status=status)

    def move(self, mid, produto, tipo, quantidade, anterior, posterior, data, item=None, usuario=1):
        fisico = dict(item=None, posicao=None, lote=None, codigo=None, corredor=None, qa=None, qp=None)
        if item:
            i = self.items[item]
            posicao = self.positions[i['posicao']]
            fisico = dict(item=item, posicao=i['posicao'], lote=i['lote'], codigo=posicao[0],
                          corredor=self.corridors[posicao[1]], qa=0, qp=posterior - anterior)
        self.movements[mid] = dict(id=mid, produto=produto, usuario=usuario, tipo=tipo, quantidade=quantidade,
                                   anterior=anterior, posterior=posterior, motivo=f'Motivo {mid}', data=data, **fisico)

    def count(self, cid, item, sistema, fisica, data, ajuste=None, observacao=None):
        i = self.items[item]
        posicao = self.positions[i['posicao']]
        self.counts[cid] = dict(id=cid, item=item, produto=i['produto'], posicao=i['posicao'], lote=i['lote'],
                                usuario=1, sistema=sistema, fisica=fisica, status=i['status'], codigo=posicao[0],
                                corredor=self.corridors[posicao[1]], observacao=observacao, data=data, ajuste=ajuste)


def seed(db):
    """Cenário de referência (valores esperados calculados à mão nos testes)."""
    db.product(1, 'Caixa', 12, 5, validade=date(2027, 1, 31))       # acima do mínimo
    db.product(2, 'Fita', 5, 5)                                      # no limite
    db.product(3, 'Luva', 2, 10, categoria='Limpeza')                # abaixo, sugere 8
    db.product(4, 'Papel', 0, 0, categoria='Escritorio')             # mínimo 0 e saldo 0
    db.product(5, 'Copo', 0, 4, categoria='Limpeza')                 # sem saldo, sugere 4
    db.product(6, 'Tinta', 9, 0, categoria='Escritorio')             # mínimo 0 e saldo > 0
    db.item(1, 1, 1, 'L1', 7)
    db.item(2, 1, 2, 'L2', 3, 'RESERVADO')
    db.item(3, 1, 3, 'L3', 2, 'BLOQUEADO')
    db.item(4, 1, 1, 'L0', 0)
    db.item(5, 2, 1, 'F1', 5)
    db.item(6, 3, 3, 'LV1', 2)
    db.item(7, 5, 2, 'C1', 0)
    db.item(8, 6, 3, 'T1', 9)
    db.move(1, 1, 'ENTRADA', 10, 0, 10, T0 - timedelta(seconds=1), item=1)   # antes do início
    db.move(2, 1, 'ENTRADA', 2, 10, 12, T0, item=1)                          # exatamente no início
    db.move(3, 3, 'SAIDA', 3, 5, 2, T0 + timedelta(days=1), item=6)
    db.move(4, 2, 'AJUSTE', 5, 6, 5, T0 + timedelta(days=1), item=5)         # empate de instante com 3
    db.move(5, 5, 'SAIDA', 4, 4, 0, T0 + timedelta(days=2), item=7)
    db.move(6, 6, 'ENTRADA', 9, 0, 9, T0 + timedelta(days=7), item=8)        # exatamente no fim (exclusivo)
    db.move(7, 3, 'SAIDA', 1, 3, 2, T0 + timedelta(days=3), item=6)
    db.count(1, 1, 7, 7, T0 + timedelta(hours=1))                            # sem divergência
    db.count(2, 6, 2, 1, T0 + timedelta(hours=2))                            # −1 pendente
    db.count(3, 5, 5, 8, T0 + timedelta(hours=3), ajuste=4)                  # +3 aplicada
    db.count(4, 2, 3, 2, T0 + timedelta(days=10), observacao='<img src=x onerror=alert(1)>')  # −1 pendente


def _corte(sql):
    for fim in (' GROUP BY ', ' ORDER BY ', ' LIMIT '):
        if fim in sql:
            sql = sql[:sql.index(fim)]
    return sql.strip()


class ReportConnection:
    """Cursor/conexão únicos, como o LocalProxy da aplicação."""

    def __init__(self, db):
        self.db, self.queries, self.rows = db, [], []
        self.commits = self.rollbacks = 0
        self.readonly, self.statements = False, 0

    def cursor(self): return self
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return list(self.rows)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1
        self.readonly, self.statements = False, 0

    def execute(self, q, p=()):
        self.queries.append((q, tuple(p)))
        if PROIBIDOS.search(re.sub(r'/\*.*?\*/', '', q)):
            raise AssertionError('Escrita/lock emitido por consulta 2F: ' + q)
        primeiro = self.statements == 0
        self.statements += 1
        if q == SOMENTE_LEITURA:
            assert primeiro, 'SET TRANSACTION precisa ser a primeira instrução da transação'
            self.readonly, self.rows = True, []
            return
        if q.startswith('SELECT ativo, perfil FROM usuarios WHERE id_usuario = %s'):
            self.rows = [self.db.users[p[0]]] if p[0] in self.db.users else []
            return
        assert self.readonly, 'Consulta fora da transação somente leitura: ' + q
        if q == 'SELECT to_regclass(%s) IS NOT NULL':
            self.rows = [(p[0].removeprefix('public.') in self.db.tables,)]
            return
        tag = re.match(r'/\* 2F:(\w+) \*/ ', q)
        assert tag, 'Consulta sem rótulo 2F: ' + q
        if self.db.fail == tag.group(1):
            raise RuntimeError('detalhe interno sigiloso')
        if self.db.fail == 'undefined':
            raise UndefinedTable()
        self.rows = getattr(self, 'q_' + tag.group(1))(q, list(p))

    # Avaliador de WHERE: predicados conhecidos, parâmetros consumidos em ordem textual.
    @staticmethod
    def _filtrar(rows, where, params, predicados):
        for predicado in [] if not where else where.split(' AND '):
            if predicado not in predicados:
                raise AssertionError('Predicado não previsto no double: ' + predicado)
            valor = params.pop(0) if '%s' in predicado else None
            rows = [r for r in rows if predicados[predicado](r, valor)]
        return rows

    @staticmethod
    def _where(q, ancora):
        resto = q[q.index(ancora) + len(ancora):]
        return _corte(resto[len('WHERE '):]) if resto.startswith('WHERE ') else ''

    @staticmethod
    def _pagina(rows, params):
        assert len(params) == 2, 'LIMIT/OFFSET esperados ao final'
        limite, deslocamento = params
        return rows[deslocamento:deslocamento + limite]

    # Produtos
    @staticmethod
    def _pred_produtos():
        return {'p.id_produto = %s': lambda r, v: r['id'] == v,
                'p.categoria = %s': lambda r, v: r['categoria'] == v,
                'p.estoque <= p.quantidade_min': lambda r, v: r['estoque'] <= r['minimo']}

    def _produtos(self, q, p):
        rows = self._filtrar(list(self.db.products.values()), self._where(q, 'FROM produtos p '), p,
                             self._pred_produtos())
        if 'ORDER BY p.nome, p.id_produto' in q:
            rows.sort(key=lambda r: (r['nome'], r['id']))
        elif 'ORDER BY GREATEST(p.quantidade_min - p.estoque, 0) DESC, p.estoque, p.id_produto' in q:
            rows.sort(key=lambda r: (-max(r['minimo'] - r['estoque'], 0), r['estoque'], r['id']))
        elif 'ORDER BY' in q:
            raise AssertionError('Ordenação de produtos não prevista')
        return rows

    @staticmethod
    def _linha_produto(r):
        return (r['id'], r['nome'], r['codigo'], r['categoria'], r['estoque'], r['minimo'], r['localizacao'],
                r['validade'])

    def q_produtos_total(self, q, p):
        rows = self._produtos(q, p)
        assert not p
        return [(len(rows),)]

    def q_produtos(self, q, p):
        return [self._linha_produto(r) for r in self._pagina(self._produtos(q, p), p)]

    def q_produtos_fisico(self, q, p):
        assert 'WHERE i.id_produto = pg.id_produto' in q and 'LEFT JOIN LATERAL' in q
        result = []
        for r in self._pagina(self._produtos(q, p), p):
            itens = [i for i in self.db.items.values() if i['produto'] == r['id']]
            positivos = [i for i in itens if i['quantidade'] > 0]
            soma = lambda status=None: sum(i['quantidade'] for i in itens if status in (None, i['status']))
            result.append((*self._linha_produto(r), len(positivos), len({i['posicao'] for i in positivos}),
                           len({self.db.positions[i['posicao']][1] for i in positivos}), soma(),
                           soma('DISPONIVEL'), soma('RESERVADO'), soma('BLOQUEADO')))
        return result

    def q_resumo_estoque(self, q, p):
        rows = self._filtrar(list(self.db.products.values()), self._where(q, 'FROM produtos p '), p,
                             self._pred_produtos())
        assert not p
        return [(len(rows), sum(r['estoque'] for r in rows),
                 sum(r['estoque'] <= r['minimo'] for r in rows), sum(r['estoque'] < r['minimo'] for r in rows),
                 sum(r['estoque'] == r['minimo'] for r in rows), sum(r['estoque'] == 0 for r in rows),
                 sum(max(r['minimo'] - r['estoque'], 0) for r in rows))]

    def q_existe(self, q, p):
        tabela = re.search(r'FROM (\w+) WHERE', q).group(1)
        fonte = {'produtos': self.db.products, 'posicoes_estoque': self.db.positions,
                 'corredores': self.db.corridors}[tabela]
        return [(1,)] if p[0] in fonte else []

    def q_agora(self, q, p):
        return [(self.db.clock,)]

    # Movimentações
    def _pred_movimentacoes(self):
        db = self.db
        return {'m.data_hora >= %s': lambda r, v: r['data'] >= v, 'm.data_hora < %s': lambda r, v: r['data'] < v,
                'm.tipo = %s': lambda r, v: r['tipo'] == v, "m.tipo = 'SAIDA'": lambda r, v: r['tipo'] == 'SAIDA',
                'm.id_produto = %s': lambda r, v: r['produto'] == v, 'm.id_posicao = %s': lambda r, v: r['posicao'] == v,
                'm.id_posicao IN (SELECT ps.id_posicao FROM posicoes_estoque ps WHERE ps.id_corredor = %s)':
                    lambda r, v: r['posicao'] is not None and db.positions[r['posicao']][1] == v,
                'm.lote = %s': lambda r, v: r['lote'] == v,
                'p.categoria = %s': lambda r, v: db.products[r['produto']]['categoria'] == v,
                JANELA_DASHBOARD: lambda r, v: r['data'] >= db.clock - timedelta(days=30)}

    def q_movimentacoes(self, q, p):
        rows = self._filtrar(list(self.db.movements.values()),
                             self._where(q, 'JOIN usuarios u ON u.id_usuario = m.id_usuario '), p,
                             self._pred_movimentacoes())
        assert 'ORDER BY m.data_hora DESC, m.id_movimentacao DESC LIMIT %s OFFSET %s' in q
        rows.sort(key=lambda r: (r['data'], r['id']), reverse=True)
        return [(r['id'], r['produto'], r['usuario'], r['tipo'], r['quantidade'], r['anterior'], r['posterior'],
                 r['motivo'], r['data'], self.db.products[r['produto']]['nome'],
                 self.db.products[r['produto']]['codigo'], self.db.names[r['usuario']], r['item'], r['posicao'],
                 r['lote'], r['codigo'], r['corredor'], r['qa'], r['qp']) for r in self._pagina(rows, p)]

    def q_movimentacoes_resumo(self, q, p):
        rows = self._filtrar(list(self.db.movements.values()), self._where(q, 'FROM movimentacoes m '), p,
                             self._pred_movimentacoes())
        assert not p and q.endswith('GROUP BY m.tipo ORDER BY m.tipo')
        return [(t, sum(r['tipo'] == t for r in rows),
                 sum(r['posterior'] - r['anterior'] for r in rows if r['tipo'] == t))
                for t in sorted({r['tipo'] for r in rows})]

    def _saidas(self, q, p):
        return self._filtrar(list(self.db.movements.values()), self._where(q, 'ON p.id_produto = m.id_produto '), p,
                             self._pred_movimentacoes())

    def q_saidas_total(self, q, p):
        rows = self._saidas(q, p)
        assert not p
        return [(len({r['produto'] for r in rows}), len(rows), sum(r['quantidade'] for r in rows))]

    def q_saidas(self, q, p):
        rows = self._saidas(q, p)
        assert 'ORDER BY SUM(m.quantidade) DESC, p.id_produto LIMIT %s OFFSET %s' in q
        grupos = {}
        for r in rows:
            g = grupos.setdefault(r['produto'], [0, 0])
            g[0] += 1
            g[1] += r['quantidade']
        ordem = sorted(grupos.items(), key=lambda kv: (-kv[1][1], kv[0]))
        return [(pid, self.db.products[pid]['nome'], self.db.products[pid]['codigo'],
                 self.db.products[pid]['categoria'], eventos, unidades)
                for pid, (eventos, unidades) in self._pagina(ordem, p)]

    # Contagens
    def _pred_contagens(self):
        db = self.db
        div = lambda r: r['fisica'] - r['sistema']
        return {'c.divergencia <> 0': lambda r, v: div(r) != 0, 'c.divergencia = 0': lambda r, v: div(r) == 0,
                'c.divergencia > 0': lambda r, v: div(r) > 0, 'c.divergencia < 0': lambda r, v: div(r) < 0,
                'c.id_movimentacao_ajuste IS NULL': lambda r, v: r['ajuste'] is None,
                'c.id_movimentacao_ajuste IS NOT NULL': lambda r, v: r['ajuste'] is not None,
                'c.data_hora >= %s': lambda r, v: r['data'] >= v, 'c.data_hora < %s': lambda r, v: r['data'] < v,
                'c.id_produto = %s': lambda r, v: r['produto'] == v, 'c.id_posicao = %s': lambda r, v: r['posicao'] == v,
                'ps.id_corredor = %s': lambda r, v: db.positions[r['posicao']][1] == v,
                'c.lote = %s': lambda r, v: r['lote'] == v}

    def q_contagens_resumo(self, q, p):
        criterio = re.match(r'/\* 2F:contagens_resumo \*/ SELECT COUNT\(\*\) FILTER \(WHERE (.*?)\), ', q).group(1)
        rows = self._filtrar(list(self.db.counts.values()), self._where(q, 'ON ps.id_posicao = c.id_posicao '), p,
                             self._pred_contagens())
        assert not p
        sel = self._filtrar(rows, criterio, [], self._pred_contagens())
        div = lambda r: r['fisica'] - r['sistema']
        return [(len(sel), sum(r['ajuste'] is None for r in sel), sum(r['ajuste'] is not None for r in sel),
                 sum(div(r) > 0 for r in sel), sum(div(r) < 0 for r in sel),
                 sum(div(r) for r in sel if div(r) > 0), sum(div(r) for r in sel if div(r) < 0),
                 sum(div(r) == 0 for r in rows))]

    def q_divergencias(self, q, p):
        rows = self._filtrar(list(self.db.counts.values()),
                             self._where(q, 'LEFT JOIN usuarios um ON um.id_usuario = m.id_usuario '), p,
                             self._pred_contagens())
        assert 'ORDER BY c.data_hora DESC, c.id_contagem DESC LIMIT %s OFFSET %s' in q
        rows.sort(key=lambda r: (r['data'], r['id']), reverse=True)
        result = []
        for r in self._pagina(rows, p):
            produto, ajuste = self.db.products[r['produto']], self.db.movements.get(r['ajuste'])
            result.append((r['id'], r['item'], r['produto'], r['posicao'], r['lote'], r['sistema'], r['fisica'],
                           r['fisica'] - r['sistema'], r['status'], r['codigo'], r['corredor'], r['observacao'],
                           r['data'], r['usuario'], self.db.names[r['usuario']], produto['nome'], produto['codigo'],
                           self.db.positions[r['posicao']][1], r['ajuste'], ajuste['data'] if ajuste else None,
                           self.db.names[ajuste['usuario']] if ajuste else None))
        return result


class Base(TestCase):
    def setUp(self):
        self.db = ReportDB()
        seed(self.db)
        self.conn = ReportConnection(self.db)
        self.service = RelatoriosService(self.conn)

    def tearDown(self):
        self.assertEqual(self.conn.commits, 0, 'consulta 2F não pode fazer commit')


def periodo(inicio=INICIO, fim=FIM, **extra):
    return dict(data_inicio=[inicio], data_fim=[fim], **{k: [str(v)] for k, v in extra.items()})


def args(**valores):
    return {k: [str(v)] for k, v in valores.items()}


def chaves(obj):
    if isinstance(obj, dict):
        return set(obj) | set().union(*(chaves(v) for v in obj.values()))
    if isinstance(obj, list):
        return set().union(*(chaves(v) for v in obj)) if obj else set()
    return set()


# ── Regra de reposição (RF06) ───────────────────────────────────────────────

class ReposicaoRuleTests(TestCase):
    def test_above_equal_below(self):
        self.assertEqual(reposicao(12, 5), dict(estoque_baixo=False, sem_saldo=False,
                                                situacao_reposicao='ACIMA_DO_MINIMO', quantidade_sugerida=0))
        self.assertEqual(reposicao(5, 5), dict(estoque_baixo=True, sem_saldo=False,
                                               situacao_reposicao='NO_LIMITE', quantidade_sugerida=0))
        self.assertEqual(reposicao(2, 10), dict(estoque_baixo=True, sem_saldo=False,
                                                situacao_reposicao='ABAIXO_DO_MINIMO', quantidade_sugerida=8))

    def test_minimum_zero_and_zero_balance(self):
        # Consequência direta da fórmula: com mínimo 0, só saldo 0 é baixo.
        self.assertEqual(reposicao(0, 0), dict(estoque_baixo=True, sem_saldo=True,
                                               situacao_reposicao='NO_LIMITE', quantidade_sugerida=0))
        self.assertFalse(reposicao(1, 0)['estoque_baixo'])
        self.assertEqual(reposicao(0, 4)['quantidade_sugerida'], 4)
        self.assertTrue(reposicao(0, 4)['sem_saldo'])

    def test_suggestion_never_negative_and_exact(self):
        for estoque in (0, 1, 5, 6, 2147483647):
            for minimo in (0, 1, 5, 6, 2147483647):
                with self.subTest(estoque=estoque, minimo=minimo):
                    r = reposicao(estoque, minimo)
                    self.assertGreaterEqual(r['quantidade_sugerida'], 0)
                    self.assertEqual(r['quantidade_sugerida'], max(0, minimo - estoque))
                    self.assertEqual(r['estoque_baixo'], estoque <= minimo)


# ── Estoque baixo / reposição ───────────────────────────────────────────────

class EstoqueBaixoTests(Base):
    def test_lists_only_at_or_below_minimum_in_suggestion_order(self):
        r = self.service.estoque_baixo(1, {})
        self.assertEqual([p['id_produto'] for p in r['itens']], [3, 5, 4, 2])
        self.assertEqual([p['quantidade_sugerida'] for p in r['itens']], [8, 4, 0, 0])
        self.assertEqual([p['situacao_reposicao'] for p in r['itens']],
                         ['ABAIXO_DO_MINIMO', 'ABAIXO_DO_MINIMO', 'NO_LIMITE', 'NO_LIMITE'])
        self.assertEqual(r['total'], 4)
        self.assertEqual(r['resumo'], dict(total_produtos=6, unidades_em_estoque=28, estoque_baixo=4,
                                           abaixo_do_minimo=2, no_limite=2, sem_saldo=2, reposicao_sugerida_total=12))
        self.assertEqual(r['criterio_estoque_baixo'], 'estoque <= quantidade_min')
        self.assertTrue(all(set(p) == CAMPOS_PRODUTO for p in r['itens']))

    def test_product_without_physical_stock_is_reported_without_inventing_items(self):
        # Produto 4: saldo 0, mínimo 0, nenhum item físico; aparece como no limite.
        r = self.service.estoque_baixo(1, args(id_produto=4))
        self.assertEqual(r['itens'][0]['sem_saldo'], True)
        posicao = self.service.posicao_estoque(1, args(id_produto=4))['itens'][0]['distribuicao']
        self.assertEqual(posicao, dict(lotes=0, posicoes=0, corredores=0, unidades_fisicas=0, unidades_disponiveis=0,
                                       unidades_reservadas=0, unidades_bloqueadas=0, confere_com_saldo=True))

    def test_filters_and_pagination(self):
        r = self.service.estoque_baixo(1, args(categoria='Limpeza'))
        self.assertEqual([p['id_produto'] for p in r['itens']], [3, 5])
        self.assertEqual(r['resumo']['total_produtos'], 2)
        pagina = self.service.estoque_baixo(1, args(page=2, page_size=3))
        self.assertEqual(([p['id_produto'] for p in pagina['itens']], pagina['total'], pagina['paginas']), ([2], 4, 2))
        vazia = self.service.estoque_baixo(1, args(page=9, page_size=3))
        self.assertEqual((vazia['itens'], vazia['total']), ([], 4))
        self.assertEqual(self.service.estoque_baixo(1, args(id_produto=1))['itens'], [])  # acima do mínimo

    def test_unknown_product_filter_is_404_and_text_injection_is_literal(self):
        with self.assertRaises(NotFoundError):
            self.service.estoque_baixo(1, args(id_produto=999))
        r = self.service.estoque_baixo(1, args(categoria="Limpeza' OR '1'='1"))
        self.assertEqual((r['itens'], r['total']), ([], 0))
        self.assertIn("Limpeza' OR '1'='1", [v for q, ps in self.conn.queries for v in ps])

    def test_empty_database_is_a_real_zero(self):
        self.db.products.clear()
        r = self.service.estoque_baixo(1, {})
        self.assertEqual((r['itens'], r['total'], r['paginas']), ([], 0, 0))
        self.assertEqual(set(r['resumo'].values()), {0})


# ── Posição de estoque ──────────────────────────────────────────────────────

class PosicaoEstoqueTests(Base):
    def test_multiple_lots_positions_and_statuses(self):
        r = self.service.posicao_estoque(1, args(id_produto=1))
        p = r['itens'][0]
        self.assertEqual((p['nome'], p['codigo'], p['categoria'], p['estoque'], p['quantidade_min']),
                         ('Caixa', 'CA-01', 'Embalagem', 12, 5))
        self.assertEqual(p['localizacao_legada'], 'Prateleira legada 1')
        self.assertEqual(p['validade'], '2027-01-31')
        self.assertFalse(p['estoque_baixo'])
        # Lote zerado (L0) não conta como lote ocupado; status separados; soma confere com o saldo global.
        self.assertEqual(p['distribuicao'], dict(lotes=3, posicoes=3, corredores=2, unidades_fisicas=12,
                                                 unidades_disponiveis=7, unidades_reservadas=3,
                                                 unidades_bloqueadas=2, confere_com_saldo=True))
        self.assertEqual(set(p) - {'distribuicao'}, CAMPOS_PRODUTO)

    def test_order_pagination_and_low_stock_filter(self):
        r = self.service.posicao_estoque(1, args(page_size=4))
        self.assertEqual([p['nome'] for p in r['itens']], ['Caixa', 'Copo', 'Fita', 'Luva'])
        self.assertEqual((r['total'], r['paginas'], r['ordenacao']), (6, 2, 'nome, id_produto'))
        r = self.service.posicao_estoque(1, args(page=2, page_size=4))
        self.assertEqual([p['nome'] for p in r['itens']], ['Papel', 'Tinta'])
        baixo = self.service.posicao_estoque(1, args(estoque_baixo='sim'))
        self.assertEqual(sorted(p['id_produto'] for p in baixo['itens']), [2, 3, 4, 5])

    def test_global_and_physical_divergence_is_shown_never_corrected(self):
        self.db.products[2]['estoque'] = 6  # invariante quebrada fora da aplicação
        p = self.service.posicao_estoque(1, args(id_produto=2))['itens'][0]
        self.assertEqual((p['estoque'], p['distribuicao']['unidades_fisicas'], p['distribuicao']['confere_com_saldo']),
                         (6, 5, False))
        self.assertEqual(self.db.products[2]['estoque'], 6)

    def test_requires_location_permission_in_transaction(self):
        self.db.users[1] = (True, 'leitor')
        with self.assertRaises(AuthorizationError):
            self.service.posicao_estoque(1, {})
        self.assertFalse(any(q.startswith('/* 2F:') for q, _ in self.conn.queries))

    def test_invalid_filters(self):
        for bad in (args(estoque_baixo='nao'), args(id_produto='abc'), args(id_produto=0), args(categoria=''),
                    args(categoria='x' * 201), args(lote='L1'), {'id_produto': ['1', '2']}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.service.posicao_estoque(1, bad)


# ── Movimentações ───────────────────────────────────────────────────────────

class MovimentacoesTests(Base):
    def test_closed_open_interval_and_stable_order(self):
        r = self.service.movimentacoes(1, periodo())
        # 1 (antes do início) e 6 (exatamente no fim) ficam fora; 3 e 4 empatam no instante → id DESC.
        self.assertEqual([m['id_movimentacao'] for m in r['itens']], [7, 5, 4, 3, 2])
        self.assertEqual(r['filtros']['periodo'], dict(data_inicio='2026-10-01T00:00:00-03:00',
                                                       data_fim_exclusivo='2026-10-08T00:00:00-03:00'))
        self.assertEqual(r['ordenacao'], 'data_hora DESC, id_movimentacao DESC')

    def test_equivalent_offsets_are_the_same_instants(self):
        utc = self.service.movimentacoes(1, periodo('2026-10-01T03:00:00Z', '2026-10-08T03:00:00+00:00'))
        local = self.service.movimentacoes(1, periodo())
        self.assertEqual([m['id_movimentacao'] for m in utc['itens']], [m['id_movimentacao'] for m in local['itens']])
        params = [ps for q, ps in self.conn.queries if q.startswith('/* 2F:movimentacoes */')]
        self.assertTrue(all(isinstance(v, datetime) and v.tzinfo for ps in params for v in ps[:2]))

    def test_summary_by_type(self):
        r = self.service.movimentacoes(1, periodo())
        self.assertEqual(r['total'], 5)
        self.assertEqual(r['resumo'], [dict(tipo='ENTRADA', eventos=1, variacao_liquida=2, unidades=2),
                                       dict(tipo='SAIDA', eventos=3, variacao_liquida=-8, unidades=8),
                                       dict(tipo='AJUSTE', eventos=1, variacao_liquida=-1, unidades=None)])

    def test_type_and_combined_filters(self):
        r = self.service.movimentacoes(1, periodo(tipo='SAIDA'))
        self.assertEqual([m['id_movimentacao'] for m in r['itens']], [7, 5, 3])
        self.assertEqual(r['resumo'], [dict(tipo='SAIDA', eventos=3, variacao_liquida=-8, unidades=8)])
        r = self.service.movimentacoes(1, periodo(tipo='SAIDA', id_produto=3, id_corredor=2, lote='LV1'))
        self.assertEqual([m['id_movimentacao'] for m in r['itens']], [7, 3])
        r = self.service.movimentacoes(1, periodo(id_posicao=1))
        self.assertEqual([m['id_movimentacao'] for m in r['itens']], [4, 2])
        vazio = self.service.movimentacoes(1, periodo(tipo='AJUSTE', id_corredor=2))
        self.assertEqual((vazio['itens'], vazio['total']), ([], 0))
        self.assertEqual([t['eventos'] for t in vazio['resumo']], [0])

    def test_pagination_and_fields_without_personal_data(self):
        r = self.service.movimentacoes(1, periodo(page=2, page_size=2))
        self.assertEqual(([m['id_movimentacao'] for m in r['itens']], r['paginas']), ([4, 3], 3))
        self.assertTrue(all(set(m) == CAMPOS_MOVIMENTACAO for m in r['itens']))
        self.assertFalse({k for k in chaves(r) if any(s in k.lower() for s in SENSIVEIS)})

    def test_invalid_period_and_type(self):
        casos = [{}, dict(data_inicio=[INICIO]), periodo(INICIO, INICIO), periodo(FIM, INICIO),
                 periodo('2026-10-01', FIM), periodo('2026-10-01T00:00:00', FIM), periodo('2026-13-01T00:00:00Z', FIM),
                 periodo('2026-10-01T00:00:00-03:00', '2027-10-03T00:00:00-03:00'), periodo('ontem', FIM),
                 periodo(tipo='saida'), periodo(tipo='TRANSFERENCIA'), periodo(tipo=''),
                 dict(periodo(), data_inicio=[INICIO, INICIO]), periodo(page=0), periodo(page_size=101),
                 periodo(page='1e3'), periodo(page='١'), periodo(id_corredor=-1), periodo(perfil='ADMINISTRADOR')]
        for caso in casos:
            with self.subTest(caso=caso), self.assertRaises(ValidationError):
                self.service.movimentacoes(1, caso)
        self.assertEqual(self.conn.queries, [])

    def test_max_period_is_inclusive_of_366_days(self):
        r = self.service.movimentacoes(1, periodo('2026-01-01T00:00:00Z', '2027-01-02T00:00:00Z'))
        self.assertEqual(r['total'], 7)

    def test_unknown_parents_are_404(self):
        for campo in ('id_produto', 'id_corredor', 'id_posicao'):
            with self.subTest(campo=campo), self.assertRaises(NotFoundError):
                self.service.movimentacoes(1, periodo(**{campo: 999}))


# ── Divergências ────────────────────────────────────────────────────────────

class DivergenciasTests(Base):
    def test_only_non_zero_divergences_with_summary(self):
        r = self.service.divergencias(1, {})
        self.assertEqual([c['id_contagem'] for c in r['itens']], [4, 3, 2])
        self.assertEqual([(c['divergencia'], c['sinal'], c['situacao']) for c in r['itens']],
                         [(-1, 'negativa', 'PENDENTE'), (3, 'positiva', 'APLICADA'), (-1, 'negativa', 'PENDENTE')])
        self.assertEqual(r['resumo'], dict(com_divergencia=3, pendentes=2, aplicadas=1, positivas=1, negativas=2,
                                           soma_positiva=3, soma_negativa=-2, sem_divergencia=1))
        self.assertTrue(all(set(c) == CAMPOS_CONTAGEM | {'sinal'} for c in r['itens']))

    def test_situation_sign_period_and_location_filters(self):
        ids = lambda **kw: [c['id_contagem'] for c in self.service.divergencias(1, args(**kw))['itens']]
        self.assertEqual(ids(situacao='PENDENTE'), [4, 2])
        self.assertEqual(ids(situacao='APLICADA'), [3])
        self.assertEqual(ids(sinal='positiva'), [3])
        self.assertEqual(ids(sinal='negativa', situacao='PENDENTE'), [4, 2])
        self.assertEqual(ids(id_corredor=2), [2])
        self.assertEqual(ids(id_posicao=2, lote='L2'), [4])
        self.assertEqual(ids(id_produto=3), [2])
        r = self.service.divergencias(1, periodo())
        self.assertEqual(([c['id_contagem'] for c in r['itens']], r['resumo']['sem_divergencia']), ([3, 2], 1))
        r = self.service.divergencias(1, args(situacao='PENDENTE'))
        self.assertEqual((r['resumo']['pendentes'], r['resumo']['aplicadas'], r['total']), (2, 0, 2))

    def test_historical_snapshot_is_not_reinterpreted_by_current_balance(self):
        self.db.items[6]['quantidade'] = 50
        self.db.positions[3][0] = 'B-99'
        self.db.corridors[2] = 'Renomeado'
        c = next(c for c in self.service.divergencias(1, {})['itens'] if c['id_contagem'] == 2)
        self.assertEqual((c['quantidade_sistema'], c['quantidade_fisica'], c['divergencia'], c['codigo_posicao'],
                          c['corredor']), (2, 1, -1, 'B-01', 'B'))

    def test_pagination_validation_and_schema(self):
        r = self.service.divergencias(1, args(page=2, page_size=2))
        self.assertEqual(([c['id_contagem'] for c in r['itens']], r['total'], r['paginas']), ([2], 3, 2))
        for bad in (args(situacao='SEM_DIVERGENCIA'), args(situacao='pendente'), args(sinal='zero'),
                    dict(data_inicio=[INICIO]), args(lote=' ')):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.service.divergencias(1, bad)
        self.db.tables.discard('contagens_inventario')
        with self.assertRaises(SchemaPendingError):
            self.service.divergencias(1, {})

    def test_text_is_returned_literally_for_client_side_escaping(self):
        c = self.service.divergencias(1, args(situacao='PENDENTE'))['itens'][0]
        self.assertEqual(c['observacao'], '<img src=x onerror=alert(1)>')


# ── Indicador aprovado: saídas no período (opção B) ─────────────────────────

class SaidasPeriodoTests(Base):
    def test_outputs_in_window_ordered_and_totals(self):
        r = self.service.saidas_periodo(1, periodo())
        # Empate de 4 unidades: desempate por id_produto.
        self.assertEqual([(s['id_produto'], s['eventos'], s['unidades']) for s in r['itens']], [(3, 2, 4), (5, 1, 4)])
        self.assertEqual(r['totais'], dict(produtos=2, eventos=3, unidades=8))
        self.assertEqual(r['ordenacao'], 'unidades DESC, id_produto')

    def test_no_outputs_and_entries_or_adjustments_never_count(self):
        r = self.service.saidas_periodo(1, periodo('2026-09-01T00:00:00-03:00', '2026-09-30T00:00:00-03:00'))
        self.assertEqual((r['itens'], r['total'], r['totais']), ([], 0, dict(produtos=0, eventos=0, unidades=0)))
        r = self.service.saidas_periodo(1, periodo(id_produto=2))  # produto 2 só tem AJUSTE no período
        self.assertEqual(r['itens'], [])

    def test_is_labelled_outputs_never_turnover(self):
        r = self.service.saidas_periodo(1, periodo(categoria='Limpeza', page_size=1))
        self.assertEqual(([s['id_produto'] for s in r['itens']], r['paginas']), ([3], 2))
        self.assertNotIn('giro', {k.lower() for k in chaves(r)})
        self.assertIn('Não é giro', r['indicador'])

    def test_requires_period(self):
        for bad in ({}, args(categoria='Limpeza'), periodo(tipo='SAIDA')):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.service.saidas_periodo(1, bad)


# ── Dashboard ───────────────────────────────────────────────────────────────

class DashboardTests(Base):
    def test_cards_and_lists_come_from_the_queries(self):
        r = self.service.dashboard(1, {})
        self.assertEqual(r['estoque'], dict(total_produtos=6, unidades_em_estoque=28, estoque_baixo=4,
                                            abaixo_do_minimo=2, no_limite=2, sem_saldo=2, reposicao_sugerida_total=12))
        self.assertEqual([p['id_produto'] for p in r['reposicao']], [3, 5, 4, 2])
        self.assertEqual([m['id_movimentacao'] for m in r['movimentacoes_recentes']], [6, 7, 5, 4, 3])
        self.assertEqual(r['movimentacoes_30_dias']['eventos'], 7)
        self.assertEqual(r['contagens'], dict(com_divergencia=3, pendentes=2, aplicadas=1, positivas=1, negativas=2,
                                              soma_positiva=3, soma_negativa=-2, sem_divergencia=1, total=4))
        self.assertEqual([c['id_contagem'] for c in r['divergencias_pendentes']], [4, 2])
        self.assertEqual(r['gerado_em'], (T0 + timedelta(days=8)).isoformat())
        self.assertEqual(set(r), {'gerado_em', 'estoque', 'reposicao', 'movimentacoes_recentes',
                                  'movimentacoes_30_dias', 'contagens', 'divergencias_pendentes',
                                  'criterio_estoque_baixo', 'criterio_sugestao'})
        self.assertFalse({k for k in chaves(r) if any(s in k.lower() for s in SENSIVEIS)})

    def test_window_uses_database_clock(self):
        self.db.clock = T0 + timedelta(days=31, hours=12)  # janela começa em T0 + 1d12h
        r = self.service.dashboard(1, {})
        self.assertEqual(r['movimentacoes_30_dias']['eventos'], 3)  # movimentações 5, 6 e 7

    def test_empty_database(self):
        for tabela in ('products', 'items', 'movements', 'counts'):
            getattr(self.db, tabela).clear()
        r = self.service.dashboard(1, {})
        self.assertEqual(set(r['estoque'].values()), {0})
        self.assertEqual((r['reposicao'], r['movimentacoes_recentes'], r['divergencias_pendentes']), ([], [], []))
        self.assertEqual([t['eventos'] for t in r['movimentacoes_30_dias']['por_tipo']], [0, 0, 0])

    def test_constant_number_of_queries_no_n_plus_one(self):
        self.service.dashboard(1, {})
        poucos = len(self.conn.queries)
        for pid in range(10, 80):
            self.db.product(pid, f'Extra{pid}', 0, 3)
            self.db.item(pid, pid, 1, f'X{pid}', 0)
        self.conn.queries.clear()
        self.service.dashboard(1, {})
        self.assertEqual(len(self.conn.queries), poucos)
        self.service.posicao_estoque(1, args(page_size=100))
        self.assertEqual(sum(q.startswith('/* 2F:produtos_fisico */') for q, _ in self.conn.queries), 1)

    def test_no_parameters_and_schema_detection_without_fallback(self):
        with self.assertRaises(ValidationError):
            self.service.dashboard(1, args(janela=90))
        self.db.tables.discard('itens_estoque')
        with self.assertRaises(SchemaPendingError):
            self.service.dashboard(1, {})
        self.assertFalse(any(q.startswith('/* 2F:') for q, _ in self.conn.queries))


# ── DTOs ────────────────────────────────────────────────────────────────────

class DtoTests(TestCase):
    def test_parameters_are_strict(self):
        self.assertEqual(parametros({'a': ['1']}, ('a',)), {'a': '1'})
        for bad in ({'a': ['1', '2']}, {'b': ['1']}, {'a': []}, {'a': [1]}, ['a']):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                parametros(bad, ('a',))

    def test_defaults_and_limits(self):
        f = FiltroProdutosDTO.from_args({}, estoque_baixo=True)
        self.assertEqual((f.page, f.page_size, f.somente_baixo), (1, 20, True))
        self.assertEqual(FiltroProdutosDTO.from_args(args(page_size=100), estoque_baixo=False).page_size, 100)
        for bad in (args(page='00000000001'), args(page='100001'), args(page_size='0'), args(page=' 1')):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                FiltroProdutosDTO.from_args(bad, estoque_baixo=False)
        f = FiltroMovimentacoesDTO.from_args(periodo('2026-10-01T00:00Z', '2026-10-02T00:00:00.5+05:30'))
        self.assertEqual(f.periodo.inicio, T0 - timedelta(hours=3))
        self.assertIsNone(FiltroDivergenciasDTO.from_args({}).periodo)
        saidas = FiltroSaidasDTO.from_args(periodo()).periodo
        self.assertEqual(saidas.fim - saidas.inicio, timedelta(days=7))


# ── HTTP: autenticação, autorização, erros, ausência de escrita ─────────────

class HttpTests(TestCase):
    def setUp(self):
        self.db = ReportDB()
        seed(self.db)
        self.conn = ReportConnection(self.db)
        with patch('app.Connection.get_connection', return_value=self.conn):
            self.app = create_app()
        self.app.session_interface = SecureCookieSessionInterface()
        self.client = self.app.test_client()

    def tearDown(self):
        self.assertEqual(self.conn.commits, 0)

    def login(self, uid=1):
        with self.client.session_transaction() as s:
            s['user_id'] = uid

    def test_no_session_is_401(self):
        for rota in ROTAS:
            with self.subTest(rota=rota):
                self.assertEqual(self.client.get(rota).status_code, 401)
        self.assertEqual(self.conn.queries, [])

    def test_all_canonical_profiles_can_read(self):
        # Correção de acessos (pedido posterior do usuário, 05/10/2026): resultado esperado alterado - OPERADOR: Dashboard 200, /relatorios/* 403.
        for perfil in ('OPERADOR', 'GESTOR', 'AUDITOR', 'ADMINISTRADOR', 'supervisor'):
            self.db.users[1] = (True, perfil)
            self.login()
            for rota in ROTAS:
                with self.subTest(perfil=perfil, rota=rota):
                    response = self.client.get(rota)
                    negado = perfil == 'OPERADOR' and rota.startswith('/relatorios/')
                    self.assertEqual(response.status_code, 403 if negado else 200, response.json)

    def test_unknown_profile_and_inactive_user(self):
        self.db.users[1] = (True, 'leitor')
        self.login()
        for rota in ROTAS:
            with self.subTest(rota=rota):
                self.assertEqual(self.client.get(rota).status_code, 403)
        self.db.users[1] = (False, 'ADMINISTRADOR')
        response = self.client.get('/dashboard/resumo')
        self.assertEqual((response.status_code, response.json), (403, {'message': 'Usuário inativo'}))
        self.assertEqual(self.client.get('/relatorios/estoque-baixo').status_code, 401)  # sessão revogada
        self.assertFalse(any(q.startswith('/* 2F:') for q, _ in self.conn.queries))

    def test_every_listed_permission_is_required(self):
        # relatorios:consultar presente: as permissões de domínio continuam exigidas.
        parcial = {'AUDITOR': frozenset({'relatorios:consultar', 'produtos:consultar', 'movimentacoes:consultar'})}
        esperado = {'/dashboard/resumo': 403, '/relatorios/posicao-estoque': 403, '/relatorios/estoque-baixo': 200,
                    ROTAS[3]: 200, '/relatorios/divergencias': 403, ROTAS[5]: 200}
        self.db.users[1] = (True, 'AUDITOR')
        self.login()
        with patch.dict('core.permissoes.PERMISSOES', parcial):
            for rota, status in esperado.items():
                with self.subTest(rota=rota):
                    self.assertEqual(self.client.get(rota).status_code, status)

    def test_demotion_takes_effect_without_new_login(self):
        self.login()
        self.assertEqual(self.client.get('/dashboard/resumo').status_code, 200)
        self.db.users[1] = (True, 'leitor')
        self.assertEqual(self.client.get('/dashboard/resumo').status_code, 403)
        self.db.users[1] = (True, 'AUDITOR')
        self.assertEqual(self.client.get('/dashboard/resumo').status_code, 200)

    def test_repository_revalidates_persisted_user(self):
        # O decorator autorizou; o usuário foi inativado antes da transação de leitura.
        self.login()
        self.db.users[1] = (False, 'ADMINISTRADOR')
        with patch.object(UsuarioRepository, 'obter_acesso', return_value=(True, 'ADMINISTRADOR')):
            response = self.client.get('/relatorios/estoque-baixo')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(any(q.startswith('/* 2F:') for q, _ in self.conn.queries))

    def test_forged_parameters_never_grant_or_widen(self):
        self.db.users[1] = (True, 'leitor')
        self.login()
        self.assertEqual(self.client.get('/dashboard/resumo?perfil=ADMINISTRADOR&admin=true').status_code, 403)
        # Correção de acessos (pedido posterior do usuário, 05/10/2026): resultado esperado alterado - OPERADOR recebe 403 em /relatorios/*; a validação é exercida com AUDITOR.
        self.db.users[1] = (True, 'AUDITOR')
        for rota in ('/dashboard/resumo?admin=true', '/relatorios/estoque-baixo?permissoes=usuarios:gerenciar',
                     '/relatorios/divergencias?id_usuario=2', ROTAS[3] + '&usuario=1'):
            with self.subTest(rota=rota):
                response = self.client.get(rota)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('itens', response.json)

    def test_validation_and_injection_over_http(self):
        self.login()
        self.assertEqual(self.client.get('/relatorios/estoque-baixo?page=1&page=2').status_code, 400)
        self.assertEqual(self.client.get('/relatorios/estoque-baixo?page_size=500').status_code, 400)
        self.assertEqual(self.client.get('/relatorios/movimentacoes?data_inicio=2026-10-01&data_fim=2026-10-02')
                         .status_code, 400)
        response = self.client.get("/relatorios/divergencias?lote=L1'%3B%20DROP%20TABLE%20produtos%3B--")
        self.assertEqual((response.status_code, response.json['itens']), (200, []))

    def test_success_payload_is_json_and_without_sensitive_fields(self):
        self.login()
        for rota in ROTAS:
            with self.subTest(rota=rota):
                response = self.client.get(rota)
                self.assertEqual(response.status_code, 200)
                self.assertFalse({k for k in chaves(response.json) if any(s in k.lower() for s in SENSIVEIS)})
                texto = json.dumps(response.json, ensure_ascii=False).lower().replace('não é giro', '')
                self.assertNotIn('giro', texto)

    def test_no_writes_read_only_transaction_and_rollback(self):
        self.login()
        for rota in ROTAS:
            with self.subTest(rota=rota):
                self.conn.queries.clear()
                antes = self.conn.rollbacks
                self.assertEqual(self.client.get(rota).status_code, 200)
                consultas = [q for q, _ in self.conn.queries]
                indice = consultas.index(SOMENTE_LEITURA)
                rotuladas = [i for i, q in enumerate(consultas) if q.startswith('/* 2F:')]
                self.assertTrue(rotuladas and all(i > indice for i in rotuladas))
                self.assertGreaterEqual(self.conn.rollbacks - antes, 2)  # antes e depois da leitura
                self.assertFalse(self.conn.readonly)

    def test_errors_schema_unexpected_and_methods(self):
        self.login()
        self.db.tables.discard('contagens_inventario')
        response = self.client.get('/relatorios/divergencias')
        self.assertEqual(response.status_code, 503)
        self.assertIn('Etapas 2C/2D', response.json['message'])
        self.db.tables.add('contagens_inventario')
        self.db.fail = 'undefined'
        self.assertEqual(self.client.get('/relatorios/estoque-baixo').status_code, 503)
        self.db.fail = 'resumo_estoque'
        response = self.client.get('/dashboard/resumo')
        self.assertEqual((response.status_code, response.json), (500, {'message': 'Erro interno do servidor'}))
        self.db.fail = None
        for metodo in ('post', 'put', 'patch', 'delete'):
            with self.subTest(metodo=metodo):
                self.assertEqual(getattr(self.client, metodo)('/relatorios/estoque-baixo', json={}).status_code, 405)

    def test_decorator_requires_every_permission_before_the_service(self):
        # Isola o decorator: mesmo sem a revalidação do repository, nenhum dado é lido sem todas as permissões.
        self.db.users[1] = (True, 'AUDITOR')
        self.login()
        parcial = {'AUDITOR': frozenset({'produtos:consultar', 'movimentacoes:consultar'})}
        with patch.dict('core.permissoes.PERMISSOES', parcial),                 patch.object(RelatoriosService, 'dashboard', return_value={'ok': True}) as dashboard,                 patch.object(RelatoriosService, 'posicao_estoque', return_value={'ok': True}) as posicao:
            self.assertEqual(self.client.get('/dashboard/resumo').status_code, 403)
            self.assertEqual(self.client.get('/relatorios/posicao-estoque').status_code, 403)
            self.assertEqual((dashboard.call_count, posicao.call_count), (0, 0))

    def test_read_only_mode_is_the_documented_one(self):
        self.assertEqual(relatorios_repository.SOMENTE_LEITURA, SOMENTE_LEITURA)

    def test_existing_any_of_decorator_is_unchanged(self):
        rotas = {r.rule: r.methods for r in self.app.url_map.iter_rules()}
        self.assertEqual(rotas['/relatorios/estoque-baixo'] - {'HEAD', 'OPTIONS'}, {'GET'})
        # Endpoint compartilhado de movimentação: basta uma das permissões (2E preservada).
        self.db.users[1] = (True, 'OPERADOR')
        self.login()
        with patch('services.movimentacao_service.MovimentacaoService.registrar', return_value={'ok': True}):
            self.assertEqual(self.client.post('/produtos/1/movimentacoes', json={}).status_code, 201)
