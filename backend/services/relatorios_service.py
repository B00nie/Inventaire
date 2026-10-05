"""Etapa 2F — serialização dos relatórios. Somente leitura; sem custo, fornecedor ou previsão (2G mantém o contrato)."""
from dataclasses import asdict

from repository.relatorios_repository import RelatoriosRepository
from schemas.relatorios_dto import (LIMITE_EXPORTACAO, TIPOS, FiltroDivergenciasDTO, FiltroMovimentacoesDTO,
                                    FiltroProdutosDTO, FiltroSaidasDTO, parametros)
from services.contagem_service import ContagemService
from services.movimentacao_service import CAMPOS_FORNECEDOR, MovimentacaoService

CRITERIO_BAIXO = 'estoque <= quantidade_min'
CRITERIO_SUGESTAO = 'max(0, quantidade_min - estoque)'
INDICADOR_SAIDAS = ('Saídas no período: soma das quantidades das movimentações SAIDA em [data_inicio, data_fim). '
                    'Não é giro de estoque; AJUSTEs não entram.')


def reposicao(estoque, minimo):
    """Regra 2F (RF06). Com mínimo 0, só o saldo 0 é baixo (consequência da fórmula).
    Sugestão 0 significa "no limite mínimo": não há quantidade a repor."""
    return dict(estoque_baixo=estoque <= minimo, sem_saldo=estoque == 0,
                situacao_reposicao=('ABAIXO_DO_MINIMO' if estoque < minimo else
                                    'NO_LIMITE' if estoque == minimo else 'ACIMA_DO_MINIMO'),
                quantidade_sugerida=max(0, minimo - estoque))


def _produto(produto, fisico):
    result = dict(id_produto=produto.id_produto, nome=produto.nome, codigo=produto.codigo,
                  categoria=produto.categoria, estoque=produto.estoque, quantidade_min=produto.quantidade_min,
                  **reposicao(produto.estoque, produto.quantidade_min),
                  localizacao_legada=produto.localizacao,
                  validade=produto.validade.isoformat() if produto.validade else None)
    if fisico:
        result['distribuicao'] = dict(
            lotes=produto.lotes, posicoes=produto.posicoes, corredores=produto.corredores,
            unidades_fisicas=produto.unidades_fisicas, unidades_disponiveis=produto.unidades_disponiveis,
            unidades_reservadas=produto.unidades_reservadas, unidades_bloqueadas=produto.unidades_bloqueadas,
            # Invariante homologada: estoque = SUM(itens). Exibida, nunca corrigida aqui.
            confere_com_saldo=produto.unidades_fisicas == produto.estoque)
    return result


def _movimentacao(movimento):
    # D9 (2G): contratos 2F idênticos — campos de fornecedor excluídos explicitamente
    # do Dashboard, dos relatórios e do CSV; continuam no histórico de Movimentações.
    result = MovimentacaoService._serialize(movimento)
    for campo in CAMPOS_FORNECEDOR:
        del result[campo]
    return result


def _divergencia(contagem):
    result = ContagemService._serialize(contagem)
    result['sinal'] = 'positiva' if contagem.divergencia > 0 else 'negativa'
    return result


def _por_tipo(resumo, tipos=TIPOS):
    # Tipos sem eventos no recorte são zero real da consulta (GROUP BY não os devolve).
    linhas = {r.tipo: r for r in resumo}
    result = []
    for tipo in tipos:
        eventos = linhas[tipo].eventos if tipo in linhas else 0
        variacao = linhas[tipo].variacao_liquida if tipo in linhas else 0
        # ENTRADA/SAIDA: unidades = |variação| (CHECK movimentacoes_saldo_check). AJUSTE grava
        # o saldo alvo em quantidade; somar unidades de ajuste seria enganoso.
        result.append(dict(tipo=tipo, eventos=eventos, variacao_liquida=variacao,
                           unidades=abs(variacao) if tipo != 'AJUSTE' else None))
    return result


def _periodo(periodo):
    return None if periodo is None else dict(data_inicio=periodo.inicio.isoformat(),
                                             data_fim_exclusivo=periodo.fim.isoformat())


class RelatoriosService:
    def __init__(self, connection):
        self.repository = RelatoriosRepository(connection)

    @staticmethod
    def _pagina(filtro, dados, itens, exportacao=False, **extra):
        total = dados['total']
        if exportacao:
            # Exportação (2F.2): total, linhas e metadados vêm da MESMA transação de leitura (uma chamada ao
            # repository). gerado_em é só o instante de geração, não um identificador de snapshot.
            return dict(itens=itens, total=total, quantidade=len(itens), limite=LIMITE_EXPORTACAO,
                        truncado=total > len(itens), gerado_em=dados['gerado_em'].isoformat(), **extra)
        return dict(itens=itens, total=total, page=filtro.page, page_size=filtro.page_size,
                    paginas=(total + filtro.page_size - 1) // filtro.page_size,
                    gerado_em=dados['gerado_em'].isoformat(), **extra)

    def posicao_estoque(self, usuario_id, args, exportacao=False):
        filtro = FiltroProdutosDTO.from_args(args, estoque_baixo=False, exportacao=exportacao)
        dados = self.repository.posicao_estoque(usuario_id, filtro)
        return self._pagina(filtro, dados, [_produto(p, True) for p in dados['itens']], exportacao,
                            filtros=dict(id_produto=filtro.id_produto, categoria=filtro.categoria,
                                         estoque_baixo=filtro.somente_baixo),
                            ordenacao='nome, id_produto', criterio_estoque_baixo=CRITERIO_BAIXO)

    def estoque_baixo(self, usuario_id, args, exportacao=False):
        filtro = FiltroProdutosDTO.from_args(args, estoque_baixo=True, exportacao=exportacao)
        dados = self.repository.estoque_baixo(usuario_id, filtro)
        return self._pagina(filtro, dados, [_produto(p, False) for p in dados['itens']], exportacao,
                            resumo=asdict(dados['resumo']),
                            filtros=dict(id_produto=filtro.id_produto, categoria=filtro.categoria),
                            ordenacao='quantidade_sugerida DESC, estoque, id_produto',
                            criterio_estoque_baixo=CRITERIO_BAIXO, criterio_sugestao=CRITERIO_SUGESTAO)

    def movimentacoes(self, usuario_id, args, exportacao=False):
        filtro = FiltroMovimentacoesDTO.from_args(args, exportacao=exportacao)
        dados = self.repository.movimentacoes(usuario_id, filtro)
        return self._pagina(filtro, dados, [_movimentacao(m) for m in dados['itens']], exportacao,
                            resumo=_por_tipo(dados['resumo'], (filtro.tipo,) if filtro.tipo else TIPOS),
                            filtros=dict(periodo=_periodo(filtro.periodo), tipo=filtro.tipo,
                                         id_produto=filtro.id_produto, id_corredor=filtro.id_corredor,
                                         id_posicao=filtro.id_posicao, lote=filtro.lote),
                            ordenacao='data_hora DESC, id_movimentacao DESC')

    def divergencias(self, usuario_id, args, exportacao=False):
        filtro = FiltroDivergenciasDTO.from_args(args, exportacao=exportacao)
        dados = self.repository.divergencias(usuario_id, filtro)
        return self._pagina(filtro, dados, [_divergencia(c) for c in dados['itens']], exportacao,
                            resumo=asdict(dados['resumo']),
                            filtros=dict(periodo=_periodo(filtro.periodo), situacao=filtro.situacao,
                                         sinal=filtro.sinal, id_produto=filtro.id_produto,
                                         id_corredor=filtro.id_corredor, id_posicao=filtro.id_posicao,
                                         lote=filtro.lote),
                            ordenacao='data_hora DESC, id_contagem DESC',
                            criterio='divergencia = quantidade_fisica - quantidade_sistema <> 0 (snapshot do registro)')

    def saidas_periodo(self, usuario_id, args, exportacao=False):
        filtro = FiltroSaidasDTO.from_args(args, exportacao=exportacao)
        dados = self.repository.saidas_periodo(usuario_id, filtro)
        return self._pagina(filtro, dados, [asdict(s) for s in dados['itens']], exportacao,
                            totais=asdict(dados['totais']),
                            filtros=dict(periodo=_periodo(filtro.periodo), id_produto=filtro.id_produto,
                                         categoria=filtro.categoria),
                            ordenacao='unidades DESC, id_produto', indicador=INDICADOR_SAIDAS)

    def dashboard(self, usuario_id, args):
        parametros(args, ())  # nenhum filtro: qualquer parâmetro → 400
        dados = self.repository.dashboard(usuario_id)
        contagens = asdict(dados['contagens'])
        janela = _por_tipo(dados['movimentacoes_30_dias'])
        return dict(
            gerado_em=dados['gerado_em'].isoformat(),
            estoque=asdict(dados['estoque']),
            reposicao=[_produto(p, False) for p in dados['reposicao']],
            movimentacoes_recentes=[_movimentacao(m) for m in dados['movimentacoes_recentes']],
            movimentacoes_30_dias=dict(janela_dias=30, eventos=sum(t['eventos'] for t in janela), por_tipo=janela),
            contagens=dict(contagens, total=contagens['com_divergencia'] + contagens['sem_divergencia']),
            divergencias_pendentes=[_divergencia(c) for c in dados['divergencias_pendentes']],
            criterio_estoque_baixo=CRITERIO_BAIXO, criterio_sugestao=CRITERIO_SUGESTAO)
