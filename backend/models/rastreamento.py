from dataclasses import dataclass


@dataclass
class Corredor:
    id_corredor: int
    identificacao: str
    capacidade_maxima: int
    ativo: bool
    ocupacao: int = 0


@dataclass
class PosicaoEstoque:
    id_posicao: int
    codigo_posicao: str
    id_corredor: int
    ativo: bool
    corredor: str
    corredor_ativo: bool


@dataclass
class ItemEstoque:
    id_item_estoque: int
    id_produto: int
    id_posicao: int
    lote: str
    quantidade: int
    status: str
    produto_nome: str
    produto_codigo: str
    codigo_posicao: str
    id_corredor: int
    corredor: str

