"""Etapa 2G (RF12) — validação de Fornecedor.

CNPJ (decisão D2, opção A): normalização + formato apenas. Remove '.', '/', '-' e
espaços, converte para maiúsculas e exige ^[0-9A-Z]{12}[0-9]{2}$ (aceita o formato
alfanumérico). NÃO verifica dígitos verificadores, existência nem situação cadastral.
"""
import re
from dataclasses import dataclass

from core.errors import ValidationError
from schemas.rastreamento_dto import inteiro, objeto, texto
from schemas.relatorios_dto import PAGINACAO, _paginacao, parametros

VALIDACAO_CNPJ = 'Validação de formato; não verifica dígitos verificadores, existência ou situação cadastral.'
CNPJ = re.compile(r'[0-9A-Z]{12}[0-9]{2}\Z', re.ASCII)
SEPARADORES = re.compile(r'[./\- ]')
TEXTO_MAXIMO = 200
# Snapshots históricos da ENTRADA: sempre capturados pelo servidor, nunca aceitos do cliente.
CAMPOS_SNAPSHOT = ('fornecedor_razao_social', 'fornecedor_cnpj')


def normalizar_cnpj(valor, campo='cnpj'):
    if not isinstance(valor, str) or len(valor) > 40 or not valor.isascii():
        raise ValidationError(f'{campo} deve ser texto com 14 caracteres (separadores . / - opcionais).')
    normalizado = SEPARADORES.sub('', valor).upper()
    if not CNPJ.match(normalizado):
        raise ValidationError(f'{campo} inválido: use 12 caracteres alfanuméricos seguidos de 2 dígitos '
                              f'(ex.: 12.345.678/0001-95). {VALIDACAO_CNPJ}')
    return normalizado


def formatar_cnpj(cnpj):
    return f'{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}'


def _texto_limitado(valor, campo):
    resultado = texto(valor, campo)
    if len(resultado) > TEXTO_MAXIMO:
        raise ValidationError(f'{campo} deve ter no máximo {TEXTO_MAXIMO} caracteres.')
    return resultado


def id_fornecedor_payload(data):
    """Semântica do payload: ausente ou null = sem fornecedor; inteiro 1..2147483647 = ID.
    Zero, negativos, booleanos, frações e textos → 400."""
    valor = data.get('id_fornecedor')
    return None if valor is None else inteiro(valor, 'id_fornecedor')


@dataclass(frozen=True)
class FornecedorDTO:
    razao_social: str
    cnpj: str
    contato: str | None
    ativo: bool

    @classmethod
    def from_dict(cls, data, *, atualizando=False):
        objeto(data, ('razao_social', 'cnpj', 'contato', 'ativo'))
        contato = data.get('contato')
        if contato is not None:
            if not isinstance(contato, str):
                raise ValidationError('contato deve ser texto ou null.')
            # Texto em branco equivale a "sem contato" (null); nenhum outro valor é descartado.
            contato = _texto_limitado(contato, 'contato') if contato.strip() else None
        if atualizando and 'ativo' not in data:
            # PUT representa o estado completo: atividade nunca é alterada implicitamente.
            raise ValidationError('Informe ativo (true/false) na edição do fornecedor.')
        ativo = data.get('ativo', True)
        if type(ativo) is not bool:
            raise ValidationError('ativo deve ser booleano.')
        return cls(_texto_limitado(data.get('razao_social'), 'razao_social'),
                   normalizar_cnpj(data.get('cnpj')), contato, ativo)


@dataclass(frozen=True)
class PesquisaFornecedoresDTO:
    q: str | None = None
    ativo: bool | None = None
    page: int = 1
    page_size: int = 20

    @classmethod
    def from_args(cls, args):
        valores = parametros(args, ('q', 'ativo', *PAGINACAO))
        q = None
        if 'q' in valores:
            if len(valores['q']) > TEXTO_MAXIMO:
                raise ValidationError(f'q inválido (máximo {TEXTO_MAXIMO} caracteres).')
            q = texto(valores['q'], 'q')
        ativo = None
        if 'ativo' in valores:
            if valores['ativo'] not in ('sim', 'nao'):
                raise ValidationError('ativo deve ser sim ou nao.')
            ativo = valores['ativo'] == 'sim'
        return cls(q, ativo, **_paginacao(valores))


@dataclass(frozen=True)
class PaginaDTO:
    page: int = 1
    page_size: int = 20

    @classmethod
    def from_args(cls, args):
        return cls(**_paginacao(parametros(args, PAGINACAO)))
