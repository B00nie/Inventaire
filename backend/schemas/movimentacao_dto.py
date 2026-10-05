from dataclasses import dataclass

from core.errors import ValidationError
from schemas.fornecedor_dto import CAMPOS_SNAPSHOT, id_fornecedor_payload
from schemas.rastreamento_dto import destino

MAX_INTEGER = 2147483647


@dataclass(frozen=True)
class MovimentacaoDTO:
    tipo: str
    quantidade: int  # AJUSTE: saldo alvo persistido; payload usa novo_estoque.
    motivo: str | None
    id_posicao: int | None = None
    lote: str | None = None
    # Etapa 2G: origem opcional, somente em ENTRADA. Snapshots nunca vêm do cliente.
    id_fornecedor: int | None = None

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValidationError('A movimentação deve ser um objeto JSON.')
        tipo = data.get('tipo')
        if not isinstance(tipo, str) or tipo not in ('ENTRADA', 'SAIDA', 'AJUSTE'):
            raise ValidationError('tipo deve ser ENTRADA, SAIDA ou AJUSTE.')
        if any(campo in data for campo in CAMPOS_SNAPSHOT):
            raise ValidationError('Razão social e CNPJ do fornecedor são registrados pelo servidor; informe apenas id_fornecedor.')
        if 'id_fornecedor' in data and tipo != 'ENTRADA':
            raise ValidationError('id_fornecedor só é aceito em ENTRADA (origem do recebimento).')
        id_fornecedor = id_fornecedor_payload(data)
        id_posicao, lote = destino(data)
        field = ('novo_saldo_item' if id_posicao is not None else 'novo_estoque') if tipo == 'AJUSTE' else 'quantidade'
        permitidos = {'tipo', field, 'motivo', 'id_posicao', 'lote'} | ({'id_fornecedor'} if tipo == 'ENTRADA' else set())
        if set(data) - permitidos:
            raise ValidationError('Campos não permitidos. Informe apenas tipo, ' + field + ' e motivo.')
        quantidade = data.get(field)
        minimum = 0 if tipo == 'AJUSTE' else 1
        if type(quantidade) is not int or not minimum <= quantidade <= MAX_INTEGER:
            raise ValidationError(f'{field} deve ser inteiro entre {minimum} e {MAX_INTEGER}.')
        motivo = data.get('motivo')
        if motivo is not None:
            if not isinstance(motivo, str) or '\x00' in motivo:
                raise ValidationError('motivo deve ser texto.')
            motivo = motivo.strip() or None
        if tipo == 'AJUSTE' and not motivo:
            raise ValidationError('Informe o motivo obrigatório do ajuste.')
        return cls(tipo, quantidade, motivo, id_posicao, lote, id_fornecedor)
