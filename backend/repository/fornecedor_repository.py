"""Etapa 2G (RF12) — Fornecedores. SQL parametrizado, campos explícitos, ordem determinística.

Locks: Usuário (FOR SHARE, revalidação 2E) → Fornecedor (UPDATE/DELETE de uma linha), sem
nenhum lock posterior. A ENTRADA usa Usuário → Fornecedor (FOR SHARE) → Produto → ..., então
não há inversão. Leituras: transação REPEATABLE READ READ ONLY (total e página do mesmo
snapshot), encerrada com ROLLBACK, como nos relatórios 2F.
"""
from contextlib import contextmanager

from psycopg2.errors import ForeignKeyViolation, RestrictViolation, UndefinedColumn, UndefinedTable, UniqueViolation

from core.errors import BusinessError, NotFoundError, SchemaPendingError
from models.fornecedores import Fornecedor
from models.movimentacoes import Movimentacao
from repository.fornecedor_schema import SCHEMA_MESSAGE, exigir_schema_fornecedores
from repository.movimentacao_repository import MovimentacaoRepository, select_historico
from repository.relatorios_repository import SOMENTE_LEITURA

COLUNAS = 'id_fornecedor, razao_social, cnpj, contato, ativo'
ORDEM = 'ORDER BY razao_social, id_fornecedor'
UNICO_CNPJ = 'fornecedores_cnpj_key'
FK_HISTORICO = 'movimentacoes_fornecedor_fk'
RECEBIMENTOS = "m.id_fornecedor = %s AND m.tipo = 'ENTRADA' "
ORDEM_RECEBIMENTOS = 'ORDER BY m.data_hora DESC, m.id_movimentacao DESC'


def _padrao(texto):
    return '%' + texto.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'


class FornecedorRepository:
    def __init__(self, connection):
        self.conn = connection

    @contextmanager
    def _escrita(self, usuario_id):
        try:
            with self.conn.cursor() as cursor:
                MovimentacaoRepository.autorizar_usuario(cursor, usuario_id, 'fornecedores:gerenciar', escrita=True)
                exigir_schema_fornecedores(cursor)
                yield cursor
            self.conn.commit()
        except (UndefinedTable, UndefinedColumn):
            self.conn.rollback()
            raise SchemaPendingError(SCHEMA_MESSAGE) from None
        except UniqueViolation as error:
            self.conn.rollback()
            if error.diag.constraint_name == UNICO_CNPJ:
                raise BusinessError('Já existe fornecedor com este CNPJ.') from None
            raise
        except (ForeignKeyViolation, RestrictViolation) as error:
            self.conn.rollback()
            # Só a FK do histórico vira conflito de negócio; qualquer outra continua erro interno.
            if error.diag.constraint_name == FK_HISTORICO:
                raise BusinessError('Fornecedor referenciado por recebimentos não pode ser excluído. '
                                    'Inative-o para impedir novos recebimentos.') from None
            raise
        except Exception:
            self.conn.rollback()
            raise

    @contextmanager
    def _leitura(self, usuario_id, permissoes):
        # Encerra a leitura de autorização do decorator para iniciar o snapshot somente leitura.
        self.conn.rollback()
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(SOMENTE_LEITURA)
                for permissao in permissoes:
                    MovimentacaoRepository.autorizar_usuario(cursor, usuario_id, permissao)
                exigir_schema_fornecedores(cursor)
                yield cursor
        except (UndefinedTable, UndefinedColumn):
            raise SchemaPendingError(SCHEMA_MESSAGE) from None
        finally:
            self.conn.rollback()

    @staticmethod
    def _obter(cursor, fornecedor_id):
        cursor.execute(f'SELECT {COLUNAS} FROM fornecedores WHERE id_fornecedor = %s', (fornecedor_id,))
        row = cursor.fetchone()
        if row is None:
            raise NotFoundError('Fornecedor não encontrado.')
        return Fornecedor(*row)

    def listar(self, usuario_id, pesquisa, cnpj_q=None, permissoes=('fornecedores:consultar',)):
        clauses, params = [], []
        if pesquisa.q is not None:
            clauses.append("(razao_social ILIKE %s ESCAPE '!' OR contato ILIKE %s ESCAPE '!' "
                           "OR cnpj ILIKE %s ESCAPE '!')")
            params += [_padrao(pesquisa.q), _padrao(pesquisa.q), _padrao(cnpj_q or pesquisa.q)]
        if pesquisa.ativo is not None:
            clauses.append('ativo = %s')
            params.append(pesquisa.ativo)
        where = ('WHERE ' + ' AND '.join(clauses) + ' ') if clauses else ''
        with self._leitura(usuario_id, permissoes) as cursor:
            cursor.execute('SELECT COUNT(*) FROM fornecedores ' + where, tuple(params))
            total = cursor.fetchone()[0]
            cursor.execute(f'SELECT {COLUNAS} FROM fornecedores ' + where + ORDEM + ' LIMIT %s OFFSET %s',
                           (*params, pesquisa.page_size, (pesquisa.page - 1) * pesquisa.page_size))
            return [Fornecedor(*row) for row in cursor.fetchall()], total

    def obter(self, usuario_id, fornecedor_id):
        with self._leitura(usuario_id, ('fornecedores:consultar',)) as cursor:
            return self._obter(cursor, fornecedor_id)

    def criar(self, usuario_id, dto):
        with self._escrita(usuario_id) as cursor:
            cursor.execute(f'INSERT INTO fornecedores (razao_social, cnpj, contato, ativo) VALUES (%s, %s, %s, %s) '
                           f'RETURNING {COLUNAS}', (dto.razao_social, dto.cnpj, dto.contato, dto.ativo))
            return Fornecedor(*cursor.fetchone())

    def atualizar(self, usuario_id, fornecedor_id, dto):
        # Edição cadastral: não toca movimentações (snapshots preservados) nem estoque.
        with self._escrita(usuario_id) as cursor:
            cursor.execute('UPDATE fornecedores SET razao_social = %s, cnpj = %s, contato = %s, ativo = %s '
                           f'WHERE id_fornecedor = %s RETURNING {COLUNAS}',
                           (dto.razao_social, dto.cnpj, dto.contato, dto.ativo, fornecedor_id))
            row = cursor.fetchone()
            if row is None:
                raise NotFoundError('Fornecedor não encontrado.')
            return Fornecedor(*row)

    def excluir(self, usuario_id, fornecedor_id):
        # Somente sem referências: a FK RESTRICT do histórico recusa (409, com rollback).
        with self._escrita(usuario_id) as cursor:
            cursor.execute('DELETE FROM fornecedores WHERE id_fornecedor = %s RETURNING id_fornecedor', (fornecedor_id,))
            if cursor.fetchone() is None:
                raise NotFoundError('Fornecedor não encontrado.')

    def recebimentos(self, usuario_id, fornecedor_id, pagina):
        with self._leitura(usuario_id, ('fornecedores:consultar', 'movimentacoes:consultar')) as cursor:
            self._obter(cursor, fornecedor_id)  # inexistente → 404; inativo continua consultável
            cursor.execute('SELECT COUNT(*) FROM movimentacoes m WHERE ' + RECEBIMENTOS, (fornecedor_id,))
            total = cursor.fetchone()[0]
            # Schema 2G implica 2C (guarda do SQL incremental): colunas físicas e de fornecedor.
            cursor.execute(select_historico(True, True) + 'WHERE ' + RECEBIMENTOS + ORDEM_RECEBIMENTOS +
                           ' LIMIT %s OFFSET %s',
                           (fornecedor_id, pagina.page_size, (pagina.page - 1) * pagina.page_size))
            return [Movimentacao(*row) for row in cursor.fetchall()], total
