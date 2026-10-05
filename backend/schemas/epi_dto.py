from dataclasses import dataclass
from datetime import date

@dataclass
class EpiDTO:
    nome: str
    categoria: str
    certificado: str
    validade: str
    estoque: int
    quantidade_min: int
    em_uso: int

    @classmethod
    def from_dict(cls, data: dict):

        if not isinstance(data, dict):
            raise ValueError("Payload invalid.")

        nome = data.get('nome')
        categoria = data.get('categoria')
        certificado = data.get('certificado')
        validade = data.get('validade')
        estoque = data.get('estoque')
        quantidade_min = data.get('quantidade_min')
        em_uso = data.get('em_uso')

        if not isinstance(nome, str) or not nome.strip():
            raise ValueError("Nome must be a string.")

        if not isinstance(categoria, str) or not categoria.strip():
            raise ValueError("Categoria must be a string.")

        if not isinstance(certificado, str) or not certificado.strip():
            raise ValueError("Certificado must be a string.")

        if not isinstance(validade, str):
            raise ValueError("Validade must be a string.")

        try:
            parsed_date = date.fromisoformat(validade)
        except ValueError:
            raise ValueError("Validade must be a valid YYYY-MM-DD date.") from None
        if parsed_date.isoformat() != validade:
            raise ValueError("Validade must use YYYY-MM-DD format.")

        if type(estoque) is not int or estoque < 0:
            raise ValueError("Estoque must be an integer.")

        if type(quantidade_min) is not int or quantidade_min < 0:
            raise ValueError("Quantidade Min must be an integer.")

        if type(em_uso) is not int:
            raise ValueError("Em Uso must be an integer.")

        return cls(
            nome=nome,
            categoria=categoria,
            certificado=certificado,
            validade=validade,
            estoque=estoque,
            quantidade_min=quantidade_min,
            em_uso=em_uso
        )
