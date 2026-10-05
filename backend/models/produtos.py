from dataclasses import dataclass
from datetime import date


@dataclass
class Produto:
    id_produto: int
    nome: str
    categoria: str
    codigo: str
    localizacao: str
    validade: date | None
    estoque: int
    quantidade_min: int
