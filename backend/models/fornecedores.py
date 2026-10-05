from dataclasses import dataclass


@dataclass
class Fornecedor:
    """RF12. CNPJ armazenado normalizado (14 caracteres; validação apenas de formato)."""
    id_fornecedor: int
    razao_social: str
    cnpj: str
    contato: str | None
    ativo: bool
