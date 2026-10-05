"""Etapa 2F — validação estrita dos filtros de consulta (query string).

Nada é ignorado em silêncio: parâmetro desconhecido, repetido, vazio ou fora do
contrato → 400. Valores seguem para o SQL apenas como parâmetros (%s).

Período: intervalo fechado-aberto [data_inicio, data_fim). Ambos são instantes
ISO 8601 com fuso explícito (Z ou ±HH:MM); sem fuso → 400 (o servidor não adivinha
fuso). A tela converte "até o dia D" em data_fim = meia-noite local de D+1.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from core.errors import ValidationError
from schemas.rastreamento_dto import MAX_INTEGER, texto

TIPOS = ('ENTRADA', 'SAIDA', 'AJUSTE')
SITUACOES = ('PENDENTE', 'APLICADA')
SINAIS = ('positiva', 'negativa')
PAGE_SIZE_PADRAO, PAGE_SIZE_MAXIMO, PAGE_MAXIMA = 20, 100, 100000
# Exportação CSV (2F.2): uma única requisição/transação com até 1.000 linhas; não aceita page/page_size.
LIMITE_EXPORTACAO = 1000
PERIODO_MAXIMO = timedelta(days=366)
TEXTO_MAXIMO = 200
INSTANTE = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})\Z', re.ASCII)
PAGINACAO = ('page', 'page_size')


def parametros(args, permitidos):
    """args = request.args.to_dict(flat=False): {nome: [valores]}. Cada parâmetro no máximo uma vez."""
    if not isinstance(args, dict):
        raise ValidationError('Parâmetros de consulta inválidos.')
    desconhecidos = sorted(str(chave)[:40] for chave in set(args) - set(permitidos))
    if desconhecidos:
        raise ValidationError('Parâmetros não permitidos: ' + ', '.join(desconhecidos) + '.')
    valores = {}
    for chave, lista in args.items():
        if not isinstance(lista, list) or len(lista) != 1 or not isinstance(lista[0], str):
            raise ValidationError(f'Parâmetro repetido ou inválido: {chave}.')
        valores[chave] = lista[0]
    return valores


def _inteiro(valor, campo, minimo, maximo):
    # Limite de dígitos antes do int(): evita conversões enormes.
    if not (valor.isascii() and valor.isdecimal()) or len(valor) > 10 or not minimo <= int(valor) <= maximo:
        raise ValidationError(f'{campo} deve ser inteiro entre {minimo} e {maximo}.')
    return int(valor)


def _id(valores, campo):
    return _inteiro(valores[campo], campo, 1, MAX_INTEGER) if campo in valores else None


def _texto(valores, campo):
    if campo not in valores:
        return None
    if len(valores[campo]) > TEXTO_MAXIMO:
        raise ValidationError(f'{campo} inválido (máximo {TEXTO_MAXIMO} caracteres).')
    return texto(valores[campo], campo)


def _opcao(valores, campo, opcoes):
    if campo not in valores:
        return None
    if valores[campo] not in opcoes:
        raise ValidationError(f'{campo} deve ser ' + ' ou '.join(opcoes) + '.')
    return valores[campo]


def _instante(valor, campo):
    if len(valor) > 40 or not INSTANTE.match(valor):
        raise ValidationError(f'{campo} deve ser instante ISO 8601 com fuso, ex.: 2026-10-01T00:00:00-03:00.')
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        raise ValidationError(f'{campo} não é uma data/hora válida.') from None


def _paginacao(valores, exportacao=False):
    if exportacao:  # página única, limite próprio; page/page_size já foram recusados como desconhecidos
        return dict(page=1, page_size=LIMITE_EXPORTACAO)
    return dict(page=_inteiro(valores.get('page', '1'), 'page', 1, PAGE_MAXIMA),
                page_size=_inteiro(valores.get('page_size', str(PAGE_SIZE_PADRAO)), 'page_size', 1, PAGE_SIZE_MAXIMO))


@dataclass(frozen=True)
class Periodo:
    inicio: datetime
    fim: datetime  # exclusivo

    @classmethod
    def ler(cls, valores, *, obrigatorio):
        presentes = [chave in valores for chave in ('data_inicio', 'data_fim')]
        if not any(presentes):
            if obrigatorio:
                raise ValidationError('Informe data_inicio e data_fim: intervalo [data_inicio, data_fim).')
            return None
        if not all(presentes):
            raise ValidationError('Informe data_inicio e data_fim juntos.')
        inicio, fim = _instante(valores['data_inicio'], 'data_inicio'), _instante(valores['data_fim'], 'data_fim')
        if fim <= inicio:
            raise ValidationError('data_fim deve ser posterior a data_inicio (limite exclusivo).')
        if fim - inicio > PERIODO_MAXIMO:
            raise ValidationError('O período máximo é de 366 dias.')
        return cls(inicio, fim)


@dataclass(frozen=True)
class FiltroProdutosDTO:
    """Posição de estoque e estoque baixo."""
    id_produto: int | None = None
    categoria: str | None = None
    somente_baixo: bool = False
    page: int = 1
    page_size: int = PAGE_SIZE_PADRAO

    @classmethod
    def from_args(cls, args, *, estoque_baixo, exportacao=False):
        permitidos = (('id_produto', 'categoria') + (() if exportacao else PAGINACAO) +
                      (() if estoque_baixo else ('estoque_baixo',)))
        valores = parametros(args, permitidos)
        somente = estoque_baixo or _opcao(valores, 'estoque_baixo', ('sim',)) == 'sim'
        return cls(_id(valores, 'id_produto'), _texto(valores, 'categoria'), somente, **_paginacao(valores, exportacao))


@dataclass(frozen=True)
class FiltroMovimentacoesDTO:
    periodo: Periodo
    tipo: str | None = None
    id_produto: int | None = None
    id_corredor: int | None = None
    id_posicao: int | None = None
    lote: str | None = None
    page: int = 1
    page_size: int = PAGE_SIZE_PADRAO

    @classmethod
    def from_args(cls, args, *, exportacao=False):
        valores = parametros(args, ('data_inicio', 'data_fim', 'tipo', 'id_produto', 'id_corredor', 'id_posicao',
                                    'lote', *(() if exportacao else PAGINACAO)))
        return cls(Periodo.ler(valores, obrigatorio=True), _opcao(valores, 'tipo', TIPOS),
                   _id(valores, 'id_produto'), _id(valores, 'id_corredor'), _id(valores, 'id_posicao'),
                   _texto(valores, 'lote'), **_paginacao(valores, exportacao))


@dataclass(frozen=True)
class FiltroDivergenciasDTO:
    periodo: Periodo | None = None
    situacao: str | None = None
    sinal: str | None = None
    id_produto: int | None = None
    id_corredor: int | None = None
    id_posicao: int | None = None
    lote: str | None = None
    page: int = 1
    page_size: int = PAGE_SIZE_PADRAO

    @classmethod
    def from_args(cls, args, *, exportacao=False):
        valores = parametros(args, ('data_inicio', 'data_fim', 'situacao', 'sinal', 'id_produto', 'id_corredor',
                                    'id_posicao', 'lote', *(() if exportacao else PAGINACAO)))
        return cls(Periodo.ler(valores, obrigatorio=False), _opcao(valores, 'situacao', SITUACOES),
                   _opcao(valores, 'sinal', SINAIS), _id(valores, 'id_produto'), _id(valores, 'id_corredor'),
                   _id(valores, 'id_posicao'), _texto(valores, 'lote'), **_paginacao(valores, exportacao))


@dataclass(frozen=True)
class FiltroSaidasDTO:
    """Indicador aprovado (opção B): saídas no período. Não é giro."""
    periodo: Periodo
    id_produto: int | None = None
    categoria: str | None = None
    page: int = 1
    page_size: int = PAGE_SIZE_PADRAO

    @classmethod
    def from_args(cls, args, *, exportacao=False):
        valores = parametros(args, ('data_inicio', 'data_fim', 'id_produto', 'categoria',
                                    *(() if exportacao else PAGINACAO)))
        return cls(Periodo.ler(valores, obrigatorio=True), _id(valores, 'id_produto'), _texto(valores, 'categoria'),
                   **_paginacao(valores, exportacao))
