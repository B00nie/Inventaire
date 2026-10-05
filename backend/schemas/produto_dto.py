from dataclasses import dataclass
from datetime import date

from core.errors import ValidationError
from schemas.fornecedor_dto import CAMPOS_SNAPSHOT, id_fornecedor_payload
from schemas.rastreamento_dto import destino

# Etapa 2G: listas explícitas. Consumidor examinado: js/inventario.js (toApi) envia só estes campos.
CAMPOS_EDICAO = ('nome', 'categoria', 'codigo', 'localizacao', 'validade', 'quantidade_min')
CAMPOS_CADASTRO = CAMPOS_EDICAO + ('estoque', 'id_posicao', 'lote', 'id_fornecedor')


@dataclass
class ProdutoDTO:
    nome: str
    categoria: str
    codigo: str
    localizacao: str
    validade: date | None
    estoque: int
    quantidade_min: int
    id_posicao: int | None = None
    lote: str | None = None
    id_fornecedor: int | None = None

    @classmethod
    def from_dict(cls, data: dict, *, updating=False):
        if not isinstance(data, dict):
            raise ValidationError('O produto deve ser um objeto JSON.')
        if updating and 'estoque' in data:
            raise ValidationError('Estoque deve ser alterado por Movimentações, não pela edição de Produto.')
        if updating and ('id_posicao' in data or 'lote' in data):
            raise ValidationError('Posição e lote devem ser informados em Movimentações.')
        if any(campo in data for campo in CAMPOS_SNAPSHOT):
            raise ValidationError('Razão social e CNPJ do fornecedor são registrados pelo servidor; informe apenas id_fornecedor.')
        if updating and 'id_fornecedor' in data:
            raise ValidationError('Fornecedor é a origem de cada ENTRADA; a edição de Produto não altera fornecedor nem histórico.')
        # Etapa 2G: nenhum campo do cliente é descartado em silêncio.
        extras = sorted(str(campo)[:40] for campo in set(data) - set(CAMPOS_EDICAO if updating else CAMPOS_CADASTRO))
        if extras:
            raise ValidationError('Campos não permitidos: ' + ', '.join(extras) + '.')
        id_posicao, lote = destino(data)
        id_fornecedor = id_fornecedor_payload(data)

        fields = {}
        for key in ('nome', 'categoria', 'codigo', 'localizacao'):
            value = data.get(key)
            if not isinstance(value, str) or not value.strip() or '\x00' in value:
                raise ValidationError(f'{key} deve ser um texto não vazio.')
            fields[key] = value.strip()

        for key in ('estoque', 'quantidade_min'):
            value = 0 if updating and key == 'estoque' else data.get(key)
            if type(value) is not int or not 0 <= value <= 2147483647:
                raise ValidationError(f'{key} deve ser um inteiro entre 0 e 2147483647.')
            fields[key] = value

        validade = data.get('validade')
        if validade is not None:
            if not isinstance(validade, str):
                raise ValidationError('validade deve usar YYYY-MM-DD ou null.')
            try:
                parsed = date.fromisoformat(validade)
            except ValueError:
                raise ValidationError('validade deve ser uma data válida em YYYY-MM-DD.') from None
            if parsed.isoformat() != validade:
                raise ValidationError('validade deve usar YYYY-MM-DD.')
            validade = parsed
        if id_posicao is not None and fields['estoque'] == 0:
            raise ValidationError('Destino inicial exige estoque positivo. Para saldo zero, use Entrada posteriormente.')
        if id_fornecedor is not None and (fields['estoque'] == 0 or id_posicao is None):
            raise ValidationError('Fornecedor do estoque inicial exige estoque positivo e destino físico (id_posicao e lote).')
        return cls(**fields, validade=validade, id_posicao=id_posicao, lote=lote, id_fornecedor=id_fornecedor)
