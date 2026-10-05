"""Etapa 2F — linhas de leitura dos relatórios. Nenhuma tabela própria: tudo vem das
tabelas homologadas (produtos, itens_estoque, movimentacoes, contagens_inventario)."""
from dataclasses import dataclass
from datetime import date


@dataclass
class PosicaoProduto:
    id_produto: int
    nome: str
    codigo: str
    categoria: str
    estoque: int
    quantidade_min: int
    localizacao: str  # descrição legada (produtos.localizacao), não é a fonte física
    validade: date | None
    # Distribuição física agregada (itens_estoque → posicoes_estoque); None quando não consultada.
    lotes: int | None = None
    posicoes: int | None = None
    corredores: int | None = None
    unidades_fisicas: int | None = None
    unidades_disponiveis: int | None = None
    unidades_reservadas: int | None = None
    unidades_bloqueadas: int | None = None


@dataclass
class ResumoEstoque:
    total_produtos: int
    unidades_em_estoque: int
    estoque_baixo: int
    abaixo_do_minimo: int
    no_limite: int
    sem_saldo: int
    reposicao_sugerida_total: int


@dataclass
class ResumoTipoMovimentacao:
    tipo: str
    eventos: int
    variacao_liquida: int  # SUM(estoque_posterior - estoque_anterior)


@dataclass
class ResumoContagens:
    com_divergencia: int  # contagens com divergencia <> 0 que atendem aos filtros
    pendentes: int
    aplicadas: int
    positivas: int
    negativas: int
    soma_positiva: int
    soma_negativa: int
    sem_divergencia: int


@dataclass
class SaidaProduto:
    id_produto: int
    nome: str
    codigo: str
    categoria: str
    eventos: int
    unidades: int


@dataclass
class TotaisSaidas:
    produtos: int
    eventos: int
    unidades: int
