from dataclasses import dataclass
from datetime import datetime


@dataclass
class ContagemInventario:
    id_contagem: int
    id_item_estoque: int
    id_produto: int
    id_posicao: int
    lote: str
    quantidade_sistema: int
    quantidade_fisica: int
    divergencia: int
    status_item: str
    codigo_posicao: str
    corredor: str
    observacao: str | None
    data_hora: datetime
    id_usuario: int
    usuario_nome: str
    produto_nome: str
    produto_codigo: str
    id_corredor: int
    id_movimentacao_ajuste: int | None
    aplicada_em: datetime | None
    aplicada_por: str | None
