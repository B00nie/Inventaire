"""RF04. Escritas de quantidade pertencem exclusivamente à Movimentação.

Ordem de locks: usuário -> produto -> corredor -> posição -> item.
Posições não mudam de corredor; assim a descoberta do pai é estável.
"""
from contextlib import contextmanager
from psycopg2.errors import UndefinedTable, UndefinedColumn, UniqueViolation
from core.errors import BusinessError, NotFoundError, SchemaPendingError
from models.rastreamento import Corredor, PosicaoEstoque, ItemEstoque

SCHEMA_MESSAGE = 'Rastreamento indisponível: aplique manualmente o SQL da Etapa 2C no banco inventaire.'


def schema_ativo(cursor):
    cursor.execute("SELECT to_regclass('public.itens_estoque') IS NOT NULL")
    return cursor.fetchone()[0]


def exigir_schema(cursor):
    if not schema_ativo(cursor):
        raise SchemaPendingError(SCHEMA_MESSAGE)


def autorizar(cursor, usuario_id, escrita=False):
    # Import local evita dependência circular com o coordenador de movimentações.
    from repository.movimentacao_repository import MovimentacaoRepository
    MovimentacaoRepository.autorizar_usuario(
        cursor, usuario_id, 'localizacoes:gerenciar' if escrita else 'localizacoes:consultar', escrita=escrita)


@contextmanager
def transacao(conn, usuario_id, escrita=False):
    try:
        with conn.cursor() as cursor:
            autorizar(cursor, usuario_id, escrita)
            exigir_schema(cursor)
            yield cursor
        if escrita:
            conn.commit()
    except (UndefinedTable, UndefinedColumn):
        conn.rollback()
        raise SchemaPendingError(SCHEMA_MESSAGE) from None
    except UniqueViolation as error:
        conn.rollback()
        if error.diag.constraint_name in ('corredores_identificacao_key', 'posicoes_estoque_codigo_posicao_key'):
            raise BusinessError('Identificação ou código já cadastrado.') from None
        raise
    except Exception:
        conn.rollback()
        raise


def bloquear_corredor(cursor, corredor_id):
    cursor.execute('SELECT id_corredor, identificacao, capacidade_maxima, ativo FROM corredores '
                   'WHERE id_corredor = %s FOR UPDATE', (corredor_id,))
    row = cursor.fetchone()
    if row is None:
        raise NotFoundError('Corredor não encontrado.')
    return Corredor(*row)


def calcular_ocupacao(cursor, corredor_id):
    cursor.execute('SELECT COALESCE(SUM(i.quantidade), 0) FROM itens_estoque i '
                   'JOIN posicoes_estoque p ON p.id_posicao = i.id_posicao '
                   'WHERE p.id_corredor = %s AND p.ativo = TRUE', (corredor_id,))
    return cursor.fetchone()[0]


def validar_capacidade(cursor, corredor, acrescimo):
    if acrescimo > 0 and calcular_ocupacao(cursor, corredor.id_corredor) + acrescimo > corredor.capacidade_maxima:
        raise BusinessError('Capacidade do corredor excedida.')


class CorredorRepository:
    def __init__(self, connection):
        self.conn = connection

    def listar(self, usuario_id, corredor_id=None):
        with transacao(self.conn, usuario_id) as cursor:
            query = ('SELECT c.id_corredor, c.identificacao, c.capacidade_maxima, c.ativo, '
                     'COALESCE(SUM(i.quantidade), 0) FROM corredores c '
                     'LEFT JOIN posicoes_estoque p ON p.id_corredor = c.id_corredor AND p.ativo = TRUE '
                     'LEFT JOIN itens_estoque i ON i.id_posicao = p.id_posicao ')
            cursor.execute(query + ('WHERE c.id_corredor = %s ' if corredor_id is not None else '') +
                           'GROUP BY c.id_corredor ORDER BY c.identificacao',
                           (corredor_id,) if corredor_id is not None else ())
            rows = [Corredor(*row) for row in cursor.fetchall()]
            if corredor_id is not None and not rows:
                raise NotFoundError('Corredor não encontrado.')
            return rows

    def salvar(self, usuario_id, dto, corredor_id=None):
        with transacao(self.conn, usuario_id, True) as cursor:
            ocupacao = 0
            if corredor_id is not None:
                bloquear_corredor(cursor, corredor_id)
                ocupacao = calcular_ocupacao(cursor, corredor_id)
                if dto.capacidade_maxima < ocupacao or (not dto.ativo and ocupacao > 0):
                    raise BusinessError('Corredor ocupado: não pode inativar nem reduzir capacidade abaixo da ocupação.')
                cursor.execute('UPDATE corredores SET identificacao = %s, capacidade_maxima = %s, ativo = %s '
                               'WHERE id_corredor = %s RETURNING id_corredor',
                               (dto.identificacao, dto.capacidade_maxima, dto.ativo, corredor_id))
            else:
                cursor.execute('INSERT INTO corredores (identificacao, capacidade_maxima, ativo) '
                               'VALUES (%s, %s, %s) RETURNING id_corredor',
                               (dto.identificacao, dto.capacidade_maxima, dto.ativo))
            result = Corredor(cursor.fetchone()[0], dto.identificacao, dto.capacidade_maxima, dto.ativo, ocupacao)
        return result


class PosicaoRepository:
    def __init__(self, connection):
        self.conn = connection

    def listar(self, usuario_id, *, corredor_id=None, posicao_id=None):
        with transacao(self.conn, usuario_id) as cursor:
            if corredor_id is not None:
                cursor.execute('SELECT id_corredor FROM corredores WHERE id_corredor = %s', (corredor_id,))
                if cursor.fetchone() is None:
                    raise NotFoundError('Corredor não encontrado.')
            query = ('SELECT p.id_posicao, p.codigo_posicao, p.id_corredor, p.ativo, c.identificacao, c.ativo '
                     'FROM posicoes_estoque p JOIN corredores c ON c.id_corredor = p.id_corredor ')
            clause, params = '', ()
            if corredor_id is not None:
                clause, params = 'WHERE p.id_corredor = %s ', (corredor_id,)
            elif posicao_id is not None:
                clause, params = 'WHERE p.id_posicao = %s ', (posicao_id,)
            cursor.execute(query + clause + 'ORDER BY p.codigo_posicao', params)
            rows = [PosicaoEstoque(*row) for row in cursor.fetchall()]
            if posicao_id is not None and not rows:
                raise NotFoundError('Posição não encontrada.')
            return rows

    def salvar(self, usuario_id, dto, posicao_id=None):
        with transacao(self.conn, usuario_id, True) as cursor:
            corredor = bloquear_corredor(cursor, dto.id_corredor)
            if dto.ativo and not corredor.ativo:
                raise BusinessError('Corredor inativo.')
            if posicao_id is not None:
                cursor.execute('SELECT id_corredor FROM posicoes_estoque WHERE id_posicao = %s FOR UPDATE', (posicao_id,))
                row = cursor.fetchone()
                if row is None:
                    raise NotFoundError('Posição não encontrada.')
                if row[0] != dto.id_corredor:
                    raise BusinessError('O corredor da posição é imutável. Cadastre outra posição.')
                if not dto.ativo:
                    cursor.execute('SELECT COALESCE(SUM(quantidade), 0) FROM itens_estoque WHERE id_posicao = %s', (posicao_id,))
                    if cursor.fetchone()[0] > 0:
                        raise BusinessError('Posição ocupada não pode ser inativada.')
                cursor.execute('UPDATE posicoes_estoque SET codigo_posicao = %s, ativo = %s '
                               'WHERE id_posicao = %s RETURNING id_posicao', (dto.codigo_posicao, dto.ativo, posicao_id))
            else:
                cursor.execute('INSERT INTO posicoes_estoque (codigo_posicao, id_corredor, ativo) '
                               'VALUES (%s, %s, %s) RETURNING id_posicao', (dto.codigo_posicao, dto.id_corredor, dto.ativo))
            result = PosicaoEstoque(cursor.fetchone()[0], dto.codigo_posicao, dto.id_corredor,
                                   dto.ativo, corredor.identificacao, corredor.ativo)
        return result


class ItemEstoqueRepository:
    def __init__(self, connection):
        self.conn = connection

    def listar(self, usuario_id, pesquisa, item_id=None):
        with transacao(self.conn, usuario_id) as cursor:
            # Pais ausentes são 404; pais existentes sem itens retornam [].
            for table, key, value in (('produtos', 'id_produto', pesquisa.id_produto),
                                      ('posicoes_estoque', 'id_posicao', pesquisa.id_posicao),
                                      ('corredores', 'id_corredor', pesquisa.id_corredor)):
                if value is not None:
                    cursor.execute(f'SELECT {key} FROM {table} WHERE {key} = %s', (value,))
                    if cursor.fetchone() is None:
                        raise NotFoundError('Registro de consulta não encontrado.')
            clauses, params = [], []
            for column, value in (('i.id_produto', pesquisa.id_produto), ('i.id_posicao', pesquisa.id_posicao),
                                  ('c.id_corredor', pesquisa.id_corredor), ('i.id_item_estoque', item_id)):
                if value is not None:
                    clauses.append(column + ' = %s')
                    params.append(value)
            if pesquisa.q:
                clauses.append("concat_ws(' ', pr.nome, pr.codigo, i.lote, p.codigo_posicao, c.identificacao) ILIKE %s ESCAPE '!' ")
                params.append('%' + pesquisa.q.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%')
            cursor.execute(
                'SELECT i.id_item_estoque, i.id_produto, i.id_posicao, i.lote, i.quantidade, i.status, '
                'pr.nome, pr.codigo, p.codigo_posicao, c.id_corredor, c.identificacao '
                'FROM itens_estoque i JOIN produtos pr ON pr.id_produto = i.id_produto '
                'JOIN posicoes_estoque p ON p.id_posicao = i.id_posicao '
                'JOIN corredores c ON c.id_corredor = p.id_corredor ' +
                ('WHERE ' + ' AND '.join(clauses) if clauses else '') + ' ORDER BY i.id_item_estoque', tuple(params))
            rows = [ItemEstoque(*row) for row in cursor.fetchall()]
            if item_id is not None and not rows:
                raise NotFoundError('Item de estoque não encontrado.')
            return rows
