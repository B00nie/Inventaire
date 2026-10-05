from psycopg2.errors import UndefinedTable, UndefinedColumn

from core.errors import AuthorizationError, BusinessError, NotFoundError, SchemaPendingError, ValidationError
from core.permissoes import pode, permissao_movimentacao
from models.movimentacoes import Movimentacao
from schemas.movimentacao_dto import MAX_INTEGER
from schemas.rastreamento_dto import ItemEstoqueDTO
from repository.rastreamento_repository import schema_ativo, bloquear_corredor, validar_capacidade, SCHEMA_MESSAGE as PHYSICAL_SCHEMA_MESSAGE
from repository.fornecedor_schema import bloquear_para_recebimento, schema_fornecedores_ativo


COLUMNS = ('m.id_movimentacao, m.id_produto, m.id_usuario, m.tipo, m.quantidade, '
           'm.estoque_anterior, m.estoque_posterior, m.motivo, m.data_hora, '
           "p.nome, p.codigo, concat_ws(' ', u.nome, u.sobrenome)")
SELECT = (f'SELECT {COLUMNS} FROM movimentacoes m '
          'JOIN produtos p ON p.id_produto = m.id_produto '
          'JOIN usuarios u ON u.id_usuario = m.id_usuario ')
SCHEMA_MESSAGE = 'Movimentações indisponíveis: aplique manualmente o SQL da Etapa 2B no banco inventaire.'
PHYSICAL_COLUMNS = ('m.id_item_estoque, m.id_posicao, m.lote, m.codigo_posicao, m.corredor, '
                    'm.quantidade_item_anterior, m.quantidade_item_posterior')
# Etapa 2G: só entram no SELECT quando o schema 2G completo foi detectado (nunca em banco pré-2G).
SUPPLIER_COLUMNS = 'm.id_fornecedor, m.fornecedor_razao_social, m.fornecedor_cnpj'


def select_historico(fisico, fornecedor=False):
    # O schema 2G exige a 2C: colunas de fornecedor sempre vêm depois das físicas (posição no model).
    if fornecedor:
        return SELECT.replace(COLUMNS, COLUMNS + ', ' + PHYSICAL_COLUMNS + ', ' + SUPPLIER_COLUMNS)
    return SELECT.replace(COLUMNS, COLUMNS + ', ' + PHYSICAL_COLUMNS) if fisico else SELECT


class MovimentacaoRepository:
    def __init__(self, connection):
        self.conn = connection

    @staticmethod
    def verificar_schema(cursor):
        # Também impede cadastro com saldo zero antes de instalar a Etapa 2B.
        cursor.execute('SELECT id_movimentacao FROM movimentacoes LIMIT 0')

    @staticmethod
    def autorizar_usuario(cursor, usuario_id, permissao, *, escrita=False):
        # Etapa 2E: matriz central. Nenhuma senha/hash é necessário. Em escritas,
        # FOR SHARE estabiliza atividade/perfil até o commit: uma alteração
        # concorrente de perfil/atividade espera esta operação terminar.
        cursor.execute('SELECT ativo, perfil FROM usuarios WHERE id_usuario = %s' +
                       (' FOR SHARE' if escrita else ''), (usuario_id,))
        row = cursor.fetchone()
        if not row or row[0] is not True:
            raise AuthorizationError('Usuário inativo ou indisponível.')
        if not pode(row[1], permissao):
            raise AuthorizationError('Seu perfil não tem permissão para esta operação.')

    @staticmethod
    def aplicar_no_cursor(cursor, produto_id, usuario_id, dto, fornecedor=None):
        """Não faz commit: chamador é dono da transação, inclusive no cadastro.

        Etapa 2G: com dto.id_fornecedor, o fornecedor é bloqueado (FOR SHARE) e revalidado
        ANTES do Produto — ordem Usuário → Fornecedor → Produto → Corredor → Posição → Item.
        O cadastro com estoque inicial já o bloqueou antes do INSERT e repassa o snapshot."""
        if dto.id_fornecedor is not None and fornecedor is None:
            fornecedor = bloquear_para_recebimento(cursor, dto.id_fornecedor)
        fisico = schema_ativo(cursor)
        if dto.id_posicao is not None and not fisico:
            raise SchemaPendingError(PHYSICAL_SCHEMA_MESSAGE)
        cursor.execute('SELECT estoque FROM produtos WHERE id_produto = %s FOR UPDATE', (produto_id,))
        row = cursor.fetchone()
        if row is None:
            raise NotFoundError('Produto não encontrado.')
        anterior = row[0]
        contexto = None
        if fisico:
            if dto.id_posicao is None:
                raise ValidationError('Informe id_posicao e lote para movimentar estoque na Etapa 2C.')
            contexto = MovimentacaoRepository.aplicar_item(cursor, produto_id, anterior, dto)
            posterior = anterior + contexto[-1] - contexto[-2]
        elif dto.tipo == 'ENTRADA':
            posterior = anterior + dto.quantidade
        elif dto.tipo == 'SAIDA':
            if dto.quantidade > anterior:
                raise BusinessError('Saldo insuficiente para esta saída.')
            posterior = anterior - dto.quantidade
        else:
            posterior = dto.quantidade
        if not 0 <= posterior <= MAX_INTEGER:
            raise BusinessError('O saldo excede o limite de armazenamento do estoque.')
        cursor.execute('UPDATE produtos SET estoque = %s WHERE id_produto = %s', (posterior, produto_id))
        quantidade = posterior if dto.tipo == 'AJUSTE' else dto.quantidade
        cursor.execute(
            'INSERT INTO movimentacoes '
            '(id_produto, id_usuario, tipo, quantidade, estoque_anterior, estoque_posterior, motivo) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id_movimentacao',
            (produto_id, usuario_id, dto.tipo, quantidade, anterior, posterior, dto.motivo))
        movimento_id = cursor.fetchone()[0]
        if contexto:
            cursor.execute('UPDATE movimentacoes SET id_item_estoque = %s, id_posicao = %s, lote = %s, '
                           'codigo_posicao = %s, corredor = %s, quantidade_item_anterior = %s, '
                           'quantidade_item_posterior = %s WHERE id_movimentacao = %s', (*contexto, movimento_id))
        if fornecedor is not None:
            # Snapshot lido sob lock nesta transação; nunca enviado pelo cliente.
            cursor.execute('UPDATE movimentacoes SET id_fornecedor = %s, fornecedor_razao_social = %s, '
                           'fornecedor_cnpj = %s WHERE id_movimentacao = %s', (*fornecedor, movimento_id))
        cursor.execute(select_historico(fisico, fornecedor is not None) + 'WHERE m.id_movimentacao = %s',
                       (movimento_id,))
        return Movimentacao(*cursor.fetchone())

    @staticmethod
    def aplicar_item(cursor, produto_id, estoque, dto):
        # Produto já bloqueado: estabiliza a soma, inclusive criação do primeiro lote.
        cursor.execute('SELECT COALESCE(SUM(quantidade), 0) FROM itens_estoque WHERE id_produto = %s', (produto_id,))
        if cursor.fetchone()[0] != estoque:
            raise BusinessError('Saldo global diverge da distribuição física. Reconciliação supervisionada necessária.')
        cursor.execute('SELECT id_corredor FROM posicoes_estoque WHERE id_posicao = %s', (dto.id_posicao,))
        row = cursor.fetchone()
        if row is None:
            raise NotFoundError('Posição não encontrada.')
        corredor = bloquear_corredor(cursor, row[0])
        cursor.execute('SELECT codigo_posicao, ativo FROM posicoes_estoque WHERE id_posicao = %s FOR UPDATE', (dto.id_posicao,))
        codigo, ativo = cursor.fetchone()
        if not ativo or not corredor.ativo:
            raise BusinessError('Posição ou corredor inativo.')
        cursor.execute('SELECT id_item_estoque, quantidade, status FROM itens_estoque '
                       'WHERE id_produto = %s AND id_posicao = %s AND lote = %s FOR UPDATE',
                       (produto_id, dto.id_posicao, dto.lote))
        item = cursor.fetchone()
        if item is None and dto.tipo != 'ENTRADA':
            raise NotFoundError('Lote não encontrado nesta posição. Cadastre pela Entrada.')
        item_id, anterior, status = item if item else (None, 0, 'DISPONIVEL')
        if status != 'DISPONIVEL' and dto.tipo != 'AJUSTE':
            raise BusinessError('Lote reservado ou bloqueado: entrada/saída indisponível.')
        posterior = (anterior + dto.quantidade if dto.tipo == 'ENTRADA' else
                     anterior - dto.quantidade if dto.tipo == 'SAIDA' else dto.quantidade)
        if posterior < 0:
            raise BusinessError('Saldo insuficiente no lote/posição.')
        if posterior > MAX_INTEGER or estoque + posterior - anterior > MAX_INTEGER:
            raise BusinessError('O saldo excede o limite de armazenamento do estoque.')
        ItemEstoqueDTO(produto_id, dto.id_posicao, dto.lote, posterior, status)
        validar_capacidade(cursor, corredor, posterior - anterior)
        if item_id is None:
            cursor.execute('INSERT INTO itens_estoque (id_produto, id_posicao, lote, quantidade, status) '
                           'VALUES (%s, %s, %s, %s, %s) RETURNING id_item_estoque',
                           (produto_id, dto.id_posicao, dto.lote, posterior, status))
            item_id = cursor.fetchone()[0]
        else:
            cursor.execute('UPDATE itens_estoque SET quantidade = %s WHERE id_item_estoque = %s', (posterior, item_id))
        return (item_id, dto.id_posicao, dto.lote, codigo, corredor.identificacao, anterior, posterior)

    def registrar(self, produto_id, usuario_id, dto):
        try:
            with self.conn.cursor() as cursor:
                self.verificar_schema(cursor)
                self.autorizar_usuario(cursor, usuario_id, permissao_movimentacao(dto.tipo), escrita=True)
                result = self.aplicar_no_cursor(cursor, produto_id, usuario_id, dto)
            self.conn.commit()
            return result
        except (UndefinedTable, UndefinedColumn):
            self.conn.rollback()
            raise SchemaPendingError(PHYSICAL_SCHEMA_MESSAGE) from None
        except Exception:
            self.conn.rollback()
            raise

    def _ler(self, usuario_id, *, produto_id=None, movimento_id=None):
        try:
            with self.conn.cursor() as cursor:
                self.autorizar_usuario(cursor, usuario_id, 'movimentacoes:consultar')
                if produto_id is not None:
                    cursor.execute('SELECT id_produto FROM produtos WHERE id_produto = %s', (produto_id,))
                    if cursor.fetchone() is None:
                        raise NotFoundError('Produto não encontrado.')
                fisico = schema_ativo(cursor)
                query, params = select_historico(fisico, fisico and schema_fornecedores_ativo(cursor)), ()
                if produto_id is not None:
                    query += 'WHERE m.id_produto = %s '
                    params = (produto_id,)
                elif movimento_id is not None:
                    query += 'WHERE m.id_movimentacao = %s '
                    params = (movimento_id,)
                cursor.execute(query + 'ORDER BY m.data_hora DESC, m.id_movimentacao DESC', params)
                return [Movimentacao(*row) for row in cursor.fetchall()]
        except (UndefinedTable, UndefinedColumn):
            self.conn.rollback()
            raise SchemaPendingError(PHYSICAL_SCHEMA_MESSAGE) from None
        except Exception:
            self.conn.rollback()
            raise

    def listar(self, usuario_id, produto_id=None):
        return self._ler(usuario_id, produto_id=produto_id)

    def obter(self, movimento_id, usuario_id):
        rows = self._ler(usuario_id, movimento_id=movimento_id)
        if not rows:
            raise NotFoundError('Movimentação não encontrada.')
        return rows[0]
