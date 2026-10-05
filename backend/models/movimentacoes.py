from dataclasses import dataclass
from datetime import datetime


@dataclass
class Movimentacao:
    id_movimentacao: int
    id_produto: int
    id_usuario: int
    tipo: str
    quantidade: int
    estoque_anterior: int
    estoque_posterior: int
    motivo: str | None
    data_hora: datetime
    produto_nome: str
    produto_codigo: str
    usuario_nome: str
    id_item_estoque: int | None = None
    id_posicao: int | None = None
    lote: str | None = None
    codigo_posicao: str | None = None
    corredor: str | None = None
    quantidade_item_anterior: int | None = None
    quantidade_item_posterior: int | None = None
    # Etapa 2G: origem do recebimento (somente ENTRADA). Snapshot capturado pelo backend sob lock.
    id_fornecedor: int | None = None
    fornecedor_razao_social: str | None = None
    fornecedor_cnpj: str | None = None
