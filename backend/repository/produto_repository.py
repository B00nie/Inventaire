from psycopg2.errors import ForeignKeyViolation, RestrictViolation, UndefinedTable, UndefinedColumn, UniqueViolation

from core.errors import BusinessError, SchemaPendingError
from models.produtos import Produto
from repository.fornecedor_schema import bloquear_para_recebimento
from repository.movimentacao_repository import MovimentacaoRepository
from repository.rastreamento_repository import SCHEMA_MESSAGE as PHYSICAL_SCHEMA_MESSAGE
from schemas.movimentacao_dto import MovimentacaoDTO


COLUMNS = 'id_produto, nome, categoria, codigo, localizacao, validade, estoque, quantidade_min'


class ProdutoRepository:
    def __init__(self, connection):
        self.conn = connection

    def _execute(self, query, params=(), *, many=False, write=False, usuario_id=None):
        try:
            with self.conn.cursor() as cursor:
                if usuario_id is not None:
                    # Etapa 2E: perfil persistido revalidado na mesma transação da escrita.
                    MovimentacaoRepository.autorizar_usuario(cursor, usuario_id, 'produtos:gerenciar', escrita=True)
                cursor.execute(query, params)
                result = cursor.fetchall() if many else cursor.fetchone()
            if write:
                self.conn.commit()
            return result
        except (ForeignKeyViolation, RestrictViolation):
            self.conn.rollback()
            raise BusinessError('Produto possui histórico de movimentações e não pode ser excluído.') from None
        except UniqueViolation as error:
            self.conn.rollback()
            if error.diag.constraint_name == 'produtos_codigo_key':
                raise BusinessError('Já existe um produto com este código/SKU.') from None
            raise
        except Exception:
            self.conn.rollback()
            raise

    def get_produtos(self) -> list[Produto]:
        rows = self._execute(f'SELECT {COLUMNS} FROM produtos ORDER BY id_produto', many=True)
        return [Produto(*row) for row in rows]

    def get_produto_por_id(self, produto_id: int) -> Produto | None:
        row = self._execute(f'SELECT {COLUMNS} FROM produtos WHERE id_produto = %s', (produto_id,))
        return Produto(*row) if row else None

    def registrar_produto(self, nome, categoria, codigo, localizacao, validade, estoque, quantidade_min, usuario_id, id_posicao=None, lote=None, id_fornecedor=None) -> Produto:
        try:
            with self.conn.cursor() as cursor:
                MovimentacaoRepository.verificar_schema(cursor)
                MovimentacaoRepository.autorizar_usuario(cursor, usuario_id, 'produtos:gerenciar', escrita=True)
                # Etapa 2G: fornecedor bloqueado ANTES do INSERT do Produto (Usuário → Fornecedor → Produto).
                fornecedor = bloquear_para_recebimento(cursor, id_fornecedor) if id_fornecedor is not None else None
                cursor.execute(
                    'INSERT INTO produtos (nome, categoria, codigo, localizacao, validade, estoque, quantidade_min) '
                    f'VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {COLUMNS}',
                    (nome, categoria, codigo, localizacao, validade, 0, quantidade_min))
                produto = Produto(*cursor.fetchone())
                if estoque > 0:
                    movement = MovimentacaoRepository.aplicar_no_cursor(
                        cursor, produto.id_produto, usuario_id,
                        MovimentacaoDTO('ENTRADA', estoque, 'Estoque inicial', id_posicao, lote, id_fornecedor),
                        fornecedor=fornecedor)
                    produto.estoque = movement.estoque_posterior
            self.conn.commit()
            return produto
        except (UndefinedTable, UndefinedColumn):
            self.conn.rollback()
            raise SchemaPendingError(PHYSICAL_SCHEMA_MESSAGE) from None
        except UniqueViolation as error:
            self.conn.rollback()
            if error.diag.constraint_name == 'produtos_codigo_key':
                raise BusinessError('Já existe um produto com este código/SKU.') from None
            raise
        except Exception:
            self.conn.rollback()
            raise

    def atualizar_produto(self, produto_id, nome, categoria, codigo, localizacao, validade, quantidade_min, *, usuario_id) -> Produto | None:
        row = self._execute(
            'UPDATE produtos SET nome = %s, categoria = %s, codigo = %s, localizacao = %s, '
            f'validade = %s, quantidade_min = %s WHERE id_produto = %s RETURNING {COLUMNS}',
            (nome, categoria, codigo, localizacao, validade, quantidade_min, produto_id), write=True,
            usuario_id=usuario_id,
        )
        return Produto(*row) if row else None

    def deletar_produto(self, produto_id: int, *, usuario_id) -> bool:
        row = self._execute('DELETE FROM produtos WHERE id_produto = %s RETURNING id_produto',
                            (produto_id,), write=True, usuario_id=usuario_id)
        return row is not None
