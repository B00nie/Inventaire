from dataclasses import dataclass

from core.errors import ValidationError
from schemas.rastreamento_dto import inteiro, objeto, texto

# Valores capturados/calculados pelo servidor; nunca aceitos do cliente.
CAMPOS_SERVIDOR = ('id_usuario', 'usuario', 'responsavel', 'quantidade_sistema', 'divergencia', 'data_hora',
                   'situacao', 'aplicada', 'aplicada_em', 'aplicada_por', 'id_movimentacao_ajuste',
                   'id_produto', 'id_posicao', 'lote', 'status_item', 'codigo_posicao', 'corredor')
SITUACOES = ('PENDENTE', 'APLICADA', 'SEM_DIVERGENCIA')


def _rejeitar_campos_servidor(data):
    if isinstance(data, dict):
        servidor = sorted(set(data) & set(CAMPOS_SERVIDOR))
        if servidor:
            raise ValidationError('Campos definidos pelo servidor não podem ser enviados: ' + ', '.join(servidor) + '.')


@dataclass(frozen=True)
class ContagemDTO:
    id_item_estoque: int
    quantidade_fisica: int
    observacao: str | None = None

    @classmethod
    def from_dict(cls, data):
        _rejeitar_campos_servidor(data)
        objeto(data, ('id_item_estoque', 'quantidade_fisica', 'observacao'))
        observacao = data.get('observacao')
        if observacao is not None:
            if not isinstance(observacao, str) or '\x00' in observacao:
                raise ValidationError('observacao deve ser texto.')
            observacao = observacao.strip() or None
        return cls(inteiro(data.get('id_item_estoque'), 'id_item_estoque'),
                   inteiro(data.get('quantidade_fisica'), 'quantidade_fisica', 0), observacao)


@dataclass(frozen=True)
class AplicacaoContagemDTO:
    motivo: str

    @classmethod
    def from_dict(cls, data):
        _rejeitar_campos_servidor(data)
        objeto(data, ('motivo',))
        return cls(texto(data.get('motivo'), 'motivo'))


@dataclass(frozen=True)
class PesquisaContagensDTO:
    id_produto: int | None = None
    id_item_estoque: int | None = None
    id_posicao: int | None = None
    id_corredor: int | None = None
    lote: str | None = None
    divergencia: str | None = None  # 'com' | 'sem'
    situacao: str | None = None

    @classmethod
    def from_dict(cls, data):
        objeto(data, ('id_produto', 'id_item_estoque', 'id_posicao', 'id_corredor', 'lote', 'divergencia', 'situacao'))
        values = {}
        for key in ('id_produto', 'id_item_estoque', 'id_posicao', 'id_corredor'):
            if key in data:
                value = data[key]
                if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
                    raise ValidationError(f'{key} inválido.')
                values[key] = inteiro(int(value), key)
        if 'lote' in data:
            lote = data['lote']
            if not isinstance(lote, str) or len(lote) > 200:
                raise ValidationError('lote inválido (máximo 200 caracteres).')
            values['lote'] = texto(lote, 'lote')
        if 'divergencia' in data:
            if data['divergencia'] not in ('com', 'sem'):
                raise ValidationError('divergencia deve ser com ou sem.')
            values['divergencia'] = data['divergencia']
        if 'situacao' in data:
            if data['situacao'] not in SITUACOES:
                raise ValidationError('situacao deve ser PENDENTE, APLICADA ou SEM_DIVERGENCIA.')
            values['situacao'] = data['situacao']
        return cls(**values)
