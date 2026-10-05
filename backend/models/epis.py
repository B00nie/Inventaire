from dataclasses import dataclass

@dataclass
class Epi:
    id: int
    nome: str
    categoria: str
    certificado: str
    validade: str
    estoque: int
    quantidade_min: int
    em_uso: int