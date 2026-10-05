"""Etapa 2F (RF06, RF08, RF10) — reposição, dashboard e relatórios. SOMENTE LEITURA.

Cada consulta roda em uma transação própria REPEATABLE READ, READ ONLY:
- snapshot único: totais, listas e resumos de uma resposta são coerentes entre si;
- o PostgreSQL recusa qualquer escrita; ao final há sempre ROLLBACK, nunca commit.
Nenhuma rotina daqui altera produto, item, movimentação, contagem ou usuário.

Agregações no banco (nada de baixar linhas para somar em Python), uma consulta por
bloco (sem N+1), paginação com LIMIT/OFFSET parametrizados e ordenação determinística
com desempate pela chave primária. Valores do cliente entram somente como %s; nomes de
tabela/coluna vêm de constantes. Cada SQL começa com um rótulo /* 2F:... */ (comentário
inerte para o PostgreSQL, usado pelos testes isolados para reconhecer a consulta).

Estoque baixo (RF06): produtos.estoque <= produtos.quantidade_min.
Sugestão: GREATEST(quantidade_min - estoque, 0) — pode ser 0 (no limite mínimo).
"""
from contextlib import contextmanager

from psycopg2.errors import UndefinedColumn, UndefinedTable

from core.errors import AuthorizationError, NotFoundError, SchemaPendingError
from core.permissoes import pode
from models.contagens import ContagemInventario
from models.movimentacoes import Movimentacao
from models.relatorios import (PosicaoProduto, ResumoContagens, ResumoEstoque, ResumoTipoMovimentacao,
                               SaidaProduto, TotaisSaidas)
from repository.contagem_repository import ORDER as ORDEM_CONTAGENS, SELECT as SELECT_CONTAGENS
from repository.movimentacao_repository import select_historico
from schemas.relatorios_dto import FiltroDivergenciasDTO, FiltroProdutosDTO

SOMENTE_LEITURA = 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'
SCHEMA_MESSAGE = 'Relatórios indisponíveis: o schema das Etapas 2C/2D precisa estar aplicado no banco inventaire.'
LIMITE_DASHBOARD = 5
JANELA_DASHBOARD = "m.data_hora >= CURRENT_TIMESTAMP - INTERVAL '30 days'"

BAIXO = '{a}.estoque <= {a}.quantidade_min'
SUGESTAO = 'GREATEST({a}.quantidade_min - {a}.estoque, 0)'
ORDENS = {'nome': 'ORDER BY {a}.nome, {a}.id_produto',
          'reposicao': 'ORDER BY ' + SUGESTAO + ' DESC, {a}.estoque, {a}.id_produto'}
PRODUTO = ('{a}.id_produto, {a}.nome, {a}.codigo, {a}.categoria, {a}.estoque, {a}.quantidade_min, '
           '{a}.localizacao, {a}.validade')
# Distribuição física agregada por Produto (fonte RF04: itens_estoque → posicoes_estoque → corredores).
FISICO = ("LEFT JOIN LATERAL (SELECT COUNT(*) FILTER (WHERE i.quantidade > 0) AS lotes, "
          "COUNT(DISTINCT i.id_posicao) FILTER (WHERE i.quantidade > 0) AS posicoes, "
          "COUNT(DISTINCT ps.id_corredor) FILTER (WHERE i.quantidade > 0) AS corredores, "
          "COALESCE(SUM(i.quantidade), 0) AS fisico, "
          "COALESCE(SUM(i.quantidade) FILTER (WHERE i.status = 'DISPONIVEL'), 0) AS disponivel, "
          "COALESCE(SUM(i.quantidade) FILTER (WHERE i.status = 'RESERVADO'), 0) AS reservado, "
          "COALESCE(SUM(i.quantidade) FILTER (WHERE i.status = 'BLOQUEADO'), 0) AS bloqueado "
          "FROM itens_estoque i JOIN posicoes_estoque ps ON ps.id_posicao = i.id_posicao "
          "WHERE i.id_produto = pg.id_produto) f ON TRUE ")
PAIS = {'id_produto': ('produtos', 'Produto'), 'id_posicao': ('posicoes_estoque', 'Posição'),
        'id_corredor': ('corredores', 'Corredor')}
ORDEM_MOVIMENTACOES = 'ORDER BY m.data_hora DESC, m.id_movimentacao DESC'
SITUACOES = {'PENDENTE': 'c.id_movimentacao_ajuste IS NULL', 'APLICADA': 'c.id_movimentacao_ajuste IS NOT NULL'}
SINAIS = {'positiva': 'c.divergencia > 0', 'negativa': 'c.divergencia < 0'}


def _where(clauses):
    return 'WHERE ' + ' AND '.join(clauses) + ' ' if clauses else ''


def _offset(filtro):
    return filtro.page_size, (filtro.page - 1) * filtro.page_size


class RelatoriosRepository:
    def __init__(self, connection):
        self.conn = connection

    @contextmanager
    def _leitura(self, usuario_id, permissoes, tabelas=()):
        # A leitura de autorização do decorator (só SELECT) já abriu uma transação;
        # encerrá-la permite iniciar esta em modo somente leitura com snapshot próprio.
        self.conn.rollback()
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(SOMENTE_LEITURA)
                # Revalidação persistida (política 2E): todas as permissões são exigidas.
                cursor.execute('SELECT ativo, perfil FROM usuarios WHERE id_usuario = %s', (usuario_id,))
                row = cursor.fetchone()
                if not row or row[0] is not True:
                    raise AuthorizationError('Usuário inativo ou indisponível.')
                if not all(pode(row[1], permissao) for permissao in permissoes):
                    raise AuthorizationError('Seu perfil não tem permissão para esta consulta.')
                for tabela in tabelas:
                    # Detecção somente por SELECT: nenhum DDL e nenhum fallback com consulta diferente.
                    cursor.execute('SELECT to_regclass(%s) IS NOT NULL', ('public.' + tabela,))
                    if not cursor.fetchone()[0]:
                        raise SchemaPendingError(SCHEMA_MESSAGE)
                yield cursor
        except (UndefinedTable, UndefinedColumn):
            raise SchemaPendingError(SCHEMA_MESSAGE) from None
        finally:
            self.conn.rollback()

    @staticmethod
    def _agora(cursor):
        cursor.execute('/* 2F:agora */ SELECT CURRENT_TIMESTAMP')
        return cursor.fetchone()[0]

    @staticmethod
    def _exigir_pais(cursor, filtro):
        for campo, (tabela, rotulo) in PAIS.items():
            valor = getattr(filtro, campo, None)
            if valor is not None:
                cursor.execute(f'/* 2F:existe */ SELECT 1 FROM {tabela} WHERE {campo} = %s', (valor,))
                if cursor.fetchone() is None:
                    raise NotFoundError(f'{rotulo} do filtro não encontrado.')

    # ── Produtos: posição de estoque e reposição ────────────────────────────

    @staticmethod
    def _filtro_produtos(filtro, alias='p'):
        clauses, params = [], []
        if filtro.id_produto is not None:
            clauses.append(f'{alias}.id_produto = %s')
            params.append(filtro.id_produto)
        if filtro.categoria is not None:
            clauses.append(f'{alias}.categoria = %s')
            params.append(filtro.categoria)
        if filtro.somente_baixo:
            clauses.append(BAIXO.format(a=alias))
        return clauses, params

    @classmethod
    def _produtos(cls, cursor, filtro, *, fisico, ordem):
        clauses, params = cls._filtro_produtos(filtro)
        cursor.execute('/* 2F:produtos_total */ SELECT COUNT(*) FROM produtos p ' + _where(clauses), tuple(params))
        total = cursor.fetchone()[0]
        pagina = (f'SELECT {PRODUTO.format(a="p")} FROM produtos p ' + _where(clauses) +
                  ORDENS[ordem].format(a='p') + ' LIMIT %s OFFSET %s')
        if fisico:
            query = (f'/* 2F:produtos_fisico */ WITH pg AS ({pagina}) SELECT {PRODUTO.format(a="pg")}, '
                     'f.lotes, f.posicoes, f.corredores, f.fisico, f.disponivel, f.reservado, f.bloqueado '
                     'FROM pg ' + FISICO + ORDENS[ordem].format(a='pg'))
        else:
            query = '/* 2F:produtos */ ' + pagina
        cursor.execute(query, (*params, *_offset(filtro)))
        return [PosicaoProduto(*row) for row in cursor.fetchall()], total

    @staticmethod
    def _resumo_estoque(cursor, filtro=None):
        clauses, params = ([], []) if filtro is None else RelatoriosRepository._filtro_produtos(filtro)
        clauses = [c for c in clauses if c != BAIXO.format(a='p')]  # o resumo decompõe o próprio critério
        cursor.execute(
            '/* 2F:resumo_estoque */ SELECT COUNT(*), COALESCE(SUM(p.estoque), 0), '
            f'COUNT(*) FILTER (WHERE {BAIXO.format(a="p")}), COUNT(*) FILTER (WHERE p.estoque < p.quantidade_min), '
            'COUNT(*) FILTER (WHERE p.estoque = p.quantidade_min), COUNT(*) FILTER (WHERE p.estoque = 0), '
            f'COALESCE(SUM({SUGESTAO.format(a="p")}), 0) FROM produtos p ' + _where(clauses), tuple(params))
        return ResumoEstoque(*cursor.fetchone())

    def posicao_estoque(self, usuario_id, filtro):
        with self._leitura(usuario_id, ('relatorios:consultar', 'produtos:consultar', 'localizacoes:consultar'),
                           ('itens_estoque', 'posicoes_estoque')) as cursor:
            self._exigir_pais(cursor, filtro)
            itens, total = self._produtos(cursor, filtro, fisico=True, ordem='nome')
            return dict(itens=itens, total=total, gerado_em=self._agora(cursor))

    def estoque_baixo(self, usuario_id, filtro):
        with self._leitura(usuario_id, ('relatorios:consultar', 'produtos:consultar')) as cursor:
            self._exigir_pais(cursor, filtro)
            itens, total = self._produtos(cursor, filtro, fisico=False, ordem='reposicao')
            return dict(itens=itens, total=total, resumo=self._resumo_estoque(cursor, filtro),
                        gerado_em=self._agora(cursor))

    # ── Movimentações ────────────────────────────────────────────────────────

    @staticmethod
    def _filtro_movimentacoes(filtro):
        clauses, params = [], []
        periodo = getattr(filtro, 'periodo', None)
        if periodo is not None:
            # Comparação direta na coluna TIMESTAMPTZ (usa movimentacoes_data_idx); [início, fim).
            clauses += ['m.data_hora >= %s', 'm.data_hora < %s']
            params += [periodo.inicio, periodo.fim]
        for campo, clause in (('tipo', 'm.tipo = %s'), ('id_produto', 'm.id_produto = %s'),
                              ('id_posicao', 'm.id_posicao = %s'),
                              # O corredor de uma posição é imutável (2C): a posição gravada determina o corredor.
                              ('id_corredor', 'm.id_posicao IN (SELECT ps.id_posicao FROM posicoes_estoque ps '
                                              'WHERE ps.id_corredor = %s)'),
                              ('lote', 'm.lote = %s')):
            valor = getattr(filtro, campo, None)
            if valor is not None:
                clauses.append(clause)
                params.append(valor)
        return clauses, params

    @staticmethod
    def _movimentacoes(cursor, clauses, params, limite, deslocamento):
        cursor.execute('/* 2F:movimentacoes */ ' + select_historico(True) + _where(clauses) +
                       ORDEM_MOVIMENTACOES + ' LIMIT %s OFFSET %s', (*params, limite, deslocamento))
        return [Movimentacao(*row) for row in cursor.fetchall()]

    @staticmethod
    def _resumo_movimentacoes(cursor, clauses, params):
        cursor.execute('/* 2F:movimentacoes_resumo */ SELECT m.tipo, COUNT(*), '
                       'COALESCE(SUM(m.estoque_posterior - m.estoque_anterior), 0) FROM movimentacoes m ' +
                       _where(clauses) + 'GROUP BY m.tipo ORDER BY m.tipo', tuple(params))
        return [ResumoTipoMovimentacao(*row) for row in cursor.fetchall()]

    def movimentacoes(self, usuario_id, filtro):
        with self._leitura(usuario_id, ('relatorios:consultar', 'movimentacoes:consultar'), ('itens_estoque',)) as cursor:
            self._exigir_pais(cursor, filtro)
            clauses, params = self._filtro_movimentacoes(filtro)
            resumo = self._resumo_movimentacoes(cursor, clauses, params)
            itens = self._movimentacoes(cursor, clauses, params, *_offset(filtro))
            return dict(itens=itens, total=sum(r.eventos for r in resumo), resumo=resumo,
                        gerado_em=self._agora(cursor))

    # ── Divergências de contagem ─────────────────────────────────────────────

    @staticmethod
    def _filtro_contagens(filtro):
        clauses, params = [], []
        if filtro is not None and filtro.periodo is not None:
            clauses += ['c.data_hora >= %s', 'c.data_hora < %s']
            params += [filtro.periodo.inicio, filtro.periodo.fim]
        for campo, clause in (('id_produto', 'c.id_produto = %s'), ('id_corredor', 'ps.id_corredor = %s'),
                              ('id_posicao', 'c.id_posicao = %s'), ('lote', 'c.lote = %s')):
            valor = getattr(filtro, campo, None)
            if valor is not None:
                clauses.append(clause)
                params.append(valor)
        return clauses, params

    @staticmethod
    def _criterio_divergencia(filtro):
        # Definição 2F: divergência = contagem com divergencia <> 0 (snapshot histórico, sem saldo atual).
        criterio = ['c.divergencia <> 0']
        if filtro is not None and filtro.situacao:
            criterio.append(SITUACOES[filtro.situacao])
        if filtro is not None and filtro.sinal:
            criterio.append(SINAIS[filtro.sinal])
        return ' AND '.join(criterio)

    @classmethod
    def _resumo_contagens(cls, cursor, filtro=None):
        clauses, params = cls._filtro_contagens(filtro)
        f = cls._criterio_divergencia(filtro)
        cursor.execute(
            f'/* 2F:contagens_resumo */ SELECT COUNT(*) FILTER (WHERE {f}), '
            f'COUNT(*) FILTER (WHERE {f} AND c.id_movimentacao_ajuste IS NULL), '
            f'COUNT(*) FILTER (WHERE {f} AND c.id_movimentacao_ajuste IS NOT NULL), '
            f'COUNT(*) FILTER (WHERE {f} AND c.divergencia > 0), COUNT(*) FILTER (WHERE {f} AND c.divergencia < 0), '
            f'COALESCE(SUM(c.divergencia) FILTER (WHERE {f} AND c.divergencia > 0), 0), '
            f'COALESCE(SUM(c.divergencia) FILTER (WHERE {f} AND c.divergencia < 0), 0), '
            'COUNT(*) FILTER (WHERE c.divergencia = 0) '
            'FROM contagens_inventario c JOIN posicoes_estoque ps ON ps.id_posicao = c.id_posicao ' +
            _where(clauses), tuple(params))
        return ResumoContagens(*cursor.fetchone())

    @classmethod
    def _divergencias(cls, cursor, filtro, limite, deslocamento):
        clauses, params = cls._filtro_contagens(filtro)
        cursor.execute('/* 2F:divergencias */ ' + SELECT_CONTAGENS +
                       _where([cls._criterio_divergencia(filtro), *clauses]) + ORDEM_CONTAGENS +
                       ' LIMIT %s OFFSET %s', (*params, limite, deslocamento))
        return [ContagemInventario(*row) for row in cursor.fetchall()]

    def divergencias(self, usuario_id, filtro):
        with self._leitura(usuario_id, ('relatorios:consultar', 'contagens:consultar'), ('contagens_inventario',)) as cursor:
            self._exigir_pais(cursor, filtro)
            resumo = self._resumo_contagens(cursor, filtro)
            itens = self._divergencias(cursor, filtro, *_offset(filtro))
            return dict(itens=itens, total=resumo.com_divergencia, resumo=resumo, gerado_em=self._agora(cursor))

    # ── Indicador aprovado (opção B): saídas no período — não é giro ─────────

    def saidas_periodo(self, usuario_id, filtro):
        with self._leitura(usuario_id, ('relatorios:consultar', 'movimentacoes:consultar')) as cursor:
            self._exigir_pais(cursor, filtro)
            clauses = ["m.tipo = 'SAIDA'", 'm.data_hora >= %s', 'm.data_hora < %s']
            params = [filtro.periodo.inicio, filtro.periodo.fim]
            for campo, clause in (('id_produto', 'm.id_produto = %s'), ('categoria', 'p.categoria = %s')):
                if getattr(filtro, campo) is not None:
                    clauses.append(clause)
                    params.append(getattr(filtro, campo))
            origem = 'FROM movimentacoes m JOIN produtos p ON p.id_produto = m.id_produto ' + _where(clauses)
            cursor.execute('/* 2F:saidas_total */ SELECT COUNT(DISTINCT m.id_produto), COUNT(*), '
                           'COALESCE(SUM(m.quantidade), 0) ' + origem, tuple(params))
            totais = TotaisSaidas(*cursor.fetchone())
            cursor.execute('/* 2F:saidas */ SELECT p.id_produto, p.nome, p.codigo, p.categoria, COUNT(*), '
                           'SUM(m.quantidade) ' + origem + 'GROUP BY p.id_produto '
                           'ORDER BY SUM(m.quantidade) DESC, p.id_produto LIMIT %s OFFSET %s',
                           (*params, *_offset(filtro)))
            itens = [SaidaProduto(*row) for row in cursor.fetchall()]
            return dict(itens=itens, total=totais.produtos, totais=totais, gerado_em=self._agora(cursor))

    # ── Dashboard ────────────────────────────────────────────────────────────

    def dashboard(self, usuario_id):
        with self._leitura(usuario_id, ('produtos:consultar', 'movimentacoes:consultar', 'contagens:consultar'),
                           ('itens_estoque', 'contagens_inventario')) as cursor:
            estoque = self._resumo_estoque(cursor)
            reposicao, _ = self._produtos(cursor, FiltroProdutosDTO(somente_baixo=True, page_size=LIMITE_DASHBOARD),
                                          fisico=False, ordem='reposicao')
            recentes = self._movimentacoes(cursor, [], [], LIMITE_DASHBOARD, 0)
            janela = self._resumo_movimentacoes(cursor, [JANELA_DASHBOARD], [])
            contagens = self._resumo_contagens(cursor)
            pendentes = self._divergencias(cursor, FiltroDivergenciasDTO(situacao='PENDENTE'), LIMITE_DASHBOARD, 0)
            return dict(estoque=estoque, reposicao=reposicao, movimentacoes_recentes=recentes,
                        movimentacoes_30_dias=janela, contagens=contagens, divergencias_pendentes=pendentes,
                        gerado_em=self._agora(cursor))
