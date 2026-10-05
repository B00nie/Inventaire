from dataclasses import dataclass
from core.errors import ValidationError

MAX_INTEGER = 2147483647
STATUS = ('DISPONIVEL', 'RESERVADO', 'BLOQUEADO')


def texto(value, campo):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValidationError(f'{campo} deve ser texto não vazio.')
    return value.strip()


def inteiro(value, campo, minimo=1):
    if type(value) is not int or not minimo <= value <= MAX_INTEGER:
        raise ValidationError(f'{campo} deve ser inteiro entre {minimo} e {MAX_INTEGER}.')
    return value


def objeto(data, campos):
    if not isinstance(data, dict) or set(data) - set(campos):
        raise ValidationError('Objeto JSON inválido ou campos não permitidos.')


def ativo(data):
    value = data.get('ativo', True)
    if type(value) is not bool:
        raise ValidationError('ativo deve ser booleano.')
    return value


def destino(data):
    if 'id_posicao' not in data and 'lote' not in data:
        return None, None
    return inteiro(data.get('id_posicao'), 'id_posicao'), texto(data.get('lote'), 'lote')


@dataclass(frozen=True)
class CorredorDTO:
    identificacao: str
    capacidade_maxima: int
    ativo: bool

    @classmethod
    def from_dict(cls, data):
        objeto(data, ('identificacao', 'capacidade_maxima', 'ativo'))
        return cls(texto(data.get('identificacao'), 'identificacao'),
                   inteiro(data.get('capacidade_maxima'), 'capacidade_maxima'), ativo(data))


@dataclass(frozen=True)
class PosicaoDTO:
    codigo_posicao: str
    id_corredor: int
    ativo: bool

    @classmethod
    def from_dict(cls, data):
        objeto(data, ('codigo_posicao', 'id_corredor', 'ativo'))
        return cls(texto(data.get('codigo_posicao'), 'codigo_posicao'),
                   inteiro(data.get('id_corredor'), 'id_corredor'), ativo(data))


@dataclass(frozen=True)
class ItemEstoqueDTO:
    """Validação interna; não existe endpoint de escrita livre de itens."""
    id_produto: int
    id_posicao: int
    lote: str
    quantidade: int
    status: str = 'DISPONIVEL'

    def __post_init__(self):
        inteiro(self.id_produto, 'id_produto')
        inteiro(self.id_posicao, 'id_posicao')
        if texto(self.lote, 'lote') != self.lote:
            raise ValidationError('lote deve estar normalizado.')
        inteiro(self.quantidade, 'quantidade', 0)
        if self.status not in STATUS:
            raise ValidationError('status inválido.')


@dataclass(frozen=True)
class PesquisaItensDTO:
    q: str = ''
    id_produto: int | None = None
    id_posicao: int | None = None
    id_corredor: int | None = None

    @classmethod
    def from_dict(cls, data):
        objeto(data, ('q', 'id_produto', 'id_posicao', 'id_corredor'))
        q = data.get('q', '')
        if not isinstance(q, str) or '\x00' in q or len(q) > 200:
            raise ValidationError('Pesquisa inválida (máximo 200 caracteres).')
        values = {'q': q.strip()}
        for key in ('id_produto', 'id_posicao', 'id_corredor'):
            if key in data:
                value = data[key]
                if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
                    raise ValidationError(f'{key} inválido.')
                values[key] = inteiro(int(value), key)
        return cls(**values)

