"""RF11 — Contagem de inventário. Nunca altera estoque fora de Movimentações.

Ordem global de locks (estende a 2C):
usuário (SHARE) -> produto -> contagem -> corredor -> posição -> item.
- Registro: usuário FOR SHARE -> produto FOR SHARE. Movimentações exigem produto
  FOR UPDATE, então o saldo/versão do item ficam estáveis até o commit do registro.
- Aplicação: usuário FOR SHARE -> produto FOR UPDATE -> contagem FOR UPDATE ->
  aplicar_no_cursor (corredor -> posição -> item), sem commit intermediário.
"""
from contextlib import contextmanager

from psycopg2.errors import UndefinedTable, UndefinedColumn, UniqueViolation

from core.errors import BusinessError, NotFoundError, SchemaPendingError
from models.contagens import ContagemInventario
from repository.movimentacao_repository import MovimentacaoRepository
from schemas.movimentacao_dto import MovimentacaoDTO

SCHEMA_MESSAGE = 'Contagens indisponíveis: aplique manualmente o SQL da Etapa 2D no banco inventaire.'
SELECT = ("SELECT c.id_contagem, c.id_item_estoque, c.id_produto, c.id_posicao, c.lote, "
          "c.quantidade_sistema, c.quantidade_fisica, c.divergencia, c.status_item, c.codigo_posicao, c.corredor, "
          "c.observacao, c.data_hora, c.id_usuario, concat_ws(' ', u.nome, u.sobrenome), pr.nome, pr.codigo, "
          "ps.id_corredor, c.id_movimentacao_ajuste, m.data_hora, NULLIF(concat_ws(' ', um.nome, um.sobrenome), '') "
          "FROM contagens_inventario c "
          "JOIN produtos pr ON pr.id_produto = c.id_produto "
          "JOIN usuarios u ON u.id_usuario = c.id_usuario "
          "JOIN posicoes_estoque ps ON ps.id_posicao = c.id_posicao "
          "LEFT JOIN movimentacoes m ON m.id_movimentacao = c.id_movimentacao_ajuste "
          "LEFT JOIN usuarios um ON um.id_usuario = m.id_usuario ")
ORDER = 'ORDER BY c.data_hora DESC, c.id_contagem DESC'
# Token de versão: todas as alterações de quantidade do item geram movimentação com id_item_estoque.
ULTIMA_MOVIMENTACAO = 'SELECT MAX(id_movimentacao) FROM movimentacoes WHERE id_item_estoque = %s'
FILTROS = (('id_produto', 'c.id_produto = %s'), ('id_item_estoque', 'c.id_item_estoque = %s'),
           ('id_posicao', 'c.id_posicao = %s'), ('id_corredor', 'ps.id_corredor = %s'), ('lote', 'c.lote = %s'))
PAIS = (('produtos', 'id_produto'), ('itens_estoque', 'id_item_estoque'),
        ('posicoes_estoque', 'id_posicao'), ('corredores', 'id_corredor'))
SITUACOES = {'APLICADA': 'c.id_movimentacao_ajuste IS NOT NULL',
             'SEM_DIVERGENCIA': 'c.divergencia = 0',
             'PENDENTE': 'c.id_movimentacao_ajuste IS NULL AND c.divergencia <> 0'}


class ContagemRepository:
    def __init__(self, connection):
        self.conn = connection

    @contextmanager
    def _transacao(self, usuario_id, permissao, escrita=False):
        try:
            with self.conn.cursor() as cursor:
                MovimentacaoRepository.autorizar_usuario(cursor, usuario_id, permissao, escrita=escrita)
                # Detecção somente por SELECT: nenhum DDL é disparado pela aplicação.
                cursor.execute("SELECT to_regclass('public.contagens_inventario') IS NOT NULL")
                if not cursor.fetchone()[0]:
                    raise SchemaPendingError(SCHEMA_MESSAGE)
                yield cursor
            if escrita:
                self.conn.commit()
        except (UndefinedTable, UndefinedColumn):
            self.conn.rollback()
            raise SchemaPendingError(SCHEMA_MESSAGE) from None
        except UniqueViolation as error:
            self.conn.rollback()
            if error.diag.constraint_name == 'contagens_inventario_ajuste_key':
                raise BusinessError('Contagem já aplicada: nenhum novo ajuste foi gerado.') from None
            raise
        except Exception:
            self.conn.rollback()
            raise

    @staticmethod
    def _obter(cursor, contagem_id):
        cursor.execute(SELECT + 'WHERE c.id_contagem = %s ' + ORDER, (contagem_id,))
        return ContagemInventario(*cursor.fetchone())

    def registrar(self, usuario_id, dto):
        with self._transacao(usuario_id, 'contagens:registrar', True) as cursor:
            cursor.execute('SELECT id_produto FROM itens_estoque WHERE id_item_estoque = %s', (dto.id_item_estoque,))
            row = cursor.fetchone()
            if row is None:
                raise NotFoundError('Item de estoque não encontrado. Lotes novos são cadastrados pela Entrada.')
            # Espera Movimentações em curso do Produto e impede novas até o commit.
            cursor.execute('SELECT id_produto FROM produtos WHERE id_produto = %s FOR SHARE', (row[0],))
            cursor.execute('SELECT i.id_produto, i.id_posicao, i.lote, i.quantidade, i.status, p.codigo_posicao, '
                           'c.identificacao FROM itens_estoque i '
                           'JOIN posicoes_estoque p ON p.id_posicao = i.id_posicao '
                           'JOIN corredores c ON c.id_corredor = p.id_corredor WHERE i.id_item_estoque = %s',
                           (dto.id_item_estoque,))
            produto, posicao, lote, quantidade, status, codigo, corredor = cursor.fetchone()
            cursor.execute(ULTIMA_MOVIMENTACAO, (dto.id_item_estoque,))
            ultima = cursor.fetchone()[0]
            cursor.execute(
                'INSERT INTO contagens_inventario (id_item_estoque, id_produto, id_posicao, lote, id_usuario, '
                'quantidade_sistema, quantidade_fisica, status_item, codigo_posicao, corredor, observacao, '
                'id_ultima_movimentacao_item) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) '
                'RETURNING id_contagem',
                (dto.id_item_estoque, produto, posicao, lote, usuario_id, quantidade, dto.quantidade_fisica,
                 status, codigo, corredor, dto.observacao, ultima))
            return self._obter(cursor, cursor.fetchone()[0])

    def aplicar(self, contagem_id, usuario_id, dto):
        with self._transacao(usuario_id, 'contagens:aplicar', True) as cursor:
            # Produto da contagem é imutável: pode ser lido antes de bloquear o Produto.
            cursor.execute('SELECT id_produto FROM contagens_inventario WHERE id_contagem = %s', (contagem_id,))
            row = cursor.fetchone()
            if row is None:
                raise NotFoundError('Contagem não encontrada.')
            produto_id = row[0]
            cursor.execute('SELECT estoque FROM produtos WHERE id_produto = %s FOR UPDATE', (produto_id,))
            cursor.execute('SELECT id_item_estoque, id_posicao, lote, quantidade_sistema, quantidade_fisica, '
                           'id_ultima_movimentacao_item, id_movimentacao_ajuste FROM contagens_inventario '
                           'WHERE id_contagem = %s FOR UPDATE', (contagem_id,))
            item_id, posicao, lote, sistema, fisica, ultima, ajuste = cursor.fetchone()
            if ajuste is not None:
                raise BusinessError('Contagem já aplicada: nenhum novo ajuste foi gerado.')
            if fisica == sistema:
                raise BusinessError('Contagem sem divergência: não há ajuste a aplicar.')
            cursor.execute('SELECT quantidade FROM itens_estoque WHERE id_item_estoque = %s', (item_id,))
            atual = cursor.fetchone()[0]
            cursor.execute(ULTIMA_MOVIMENTACAO, (item_id,))
            if atual != sistema or cursor.fetchone()[0] != ultima:
                raise BusinessError('Contagem desatualizada: o item foi movimentado após o registro. '
                                    'Registre uma nova contagem.')
            movimento = MovimentacaoRepository.aplicar_no_cursor(
                cursor, produto_id, usuario_id,
                MovimentacaoDTO('AJUSTE', fisica, f'Contagem #{contagem_id}: {dto.motivo}', posicao, lote))
            if (movimento.id_item_estoque, movimento.quantidade_item_anterior,
                    movimento.quantidade_item_posterior) != (item_id, sistema, fisica):
                raise BusinessError('Ajuste não corresponde à contagem; operação revertida.')
            cursor.execute('UPDATE contagens_inventario SET id_movimentacao_ajuste = %s '
                           'WHERE id_contagem = %s AND id_movimentacao_ajuste IS NULL RETURNING id_contagem',
                           (movimento.id_movimentacao, contagem_id))
            if cursor.fetchone() is None:
                raise BusinessError('Contagem já aplicada: nenhum novo ajuste foi gerado.')
            return self._obter(cursor, contagem_id), movimento

    def listar(self, usuario_id, pesquisa, contagem_id=None):
        with self._transacao(usuario_id, 'contagens:consultar') as cursor:
            for table, key in PAIS:
                value = getattr(pesquisa, key)
                if value is not None:
                    cursor.execute(f'SELECT {key} FROM {table} WHERE {key} = %s', (value,))
                    if cursor.fetchone() is None:
                        raise NotFoundError('Registro de consulta não encontrado.')
            clauses, params = [], []
            for field, clause in FILTROS:
                if getattr(pesquisa, field) is not None:
                    clauses.append(clause)
                    params.append(getattr(pesquisa, field))
            if contagem_id is not None:
                clauses.append('c.id_contagem = %s')
                params.append(contagem_id)
            if pesquisa.divergencia:
                clauses.append('c.divergencia <> 0' if pesquisa.divergencia == 'com' else 'c.divergencia = 0')
            if pesquisa.situacao:
                clauses.append(SITUACOES[pesquisa.situacao])
            cursor.execute(SELECT + ('WHERE ' + ' AND '.join(clauses) + ' ' if clauses else '') + ORDER, tuple(params))
            rows = [ContagemInventario(*row) for row in cursor.fetchall()]
            if contagem_id is not None and not rows:
                raise NotFoundError('Contagem não encontrada.')
            return rows
