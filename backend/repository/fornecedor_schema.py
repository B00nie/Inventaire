"""Etapa 2G — detecção do schema de Fornecedores e lock do fornecedor no recebimento.

Módulo separado para evitar import circular entre Movimentação e Fornecedor.
A detecção é somente SELECT (nenhum DDL) e só considera o schema pronto quando a
tabela fornecedores E as três colunas de movimentacoes existem. Instalação parcial
ou incompatível = indisponível (503), sem SELECT de colunas novas e sem correção automática.
"""
from core.errors import BusinessError, NotFoundError, SchemaPendingError

SCHEMA_MESSAGE = ('Fornecedores indisponíveis: o SQL da Etapa 2G não está aplicado (ou está incompleto) '
                  'no banco inventaire. Aplique manualmente backend/assets/historico/etapa_2g_fornecedores.sql.')
DETECCAO = ("SELECT to_regclass('public.fornecedores') IS NOT NULL AND "
            "(SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = 'movimentacoes' AND column_name IN "
            "('id_fornecedor', 'fornecedor_razao_social', 'fornecedor_cnpj')) = 3")


def schema_fornecedores_ativo(cursor):
    cursor.execute(DETECCAO)
    return cursor.fetchone()[0] is True


def exigir_schema_fornecedores(cursor):
    if not schema_fornecedores_ativo(cursor):
        raise SchemaPendingError(SCHEMA_MESSAGE)


def bloquear_para_recebimento(cursor, fornecedor_id):
    """FOR SHARE: inativação, edição e exclusão concorrentes esperam o commit desta ENTRADA;
    se uma delas veio antes, a leitura sob lock já enxerga o estado confirmado.
    Retorna (id, razão social, CNPJ) — o snapshot gravado na movimentação."""
    exigir_schema_fornecedores(cursor)
    cursor.execute('SELECT id_fornecedor, razao_social, cnpj, ativo FROM fornecedores '
                   'WHERE id_fornecedor = %s FOR SHARE', (fornecedor_id,))
    row = cursor.fetchone()
    if row is None:
        raise NotFoundError('Fornecedor não encontrado.')
    if row[3] is not True:
        raise BusinessError('Fornecedor inativo: não pode ser selecionado para novos recebimentos.')
    return row[:3]
