"""Usuários. Alterações administrativas (Etapa 2E) usam uma única transação:

Ordem de locks: linhas de usuarios (responsável + alvo + ADMINISTRADORES ativos)
FOR UPDATE em uma só instrução, por id crescente. Nenhum lock do responsável é
obtido antes dessa instrução, então duas operações administrativas não invertem
a ordem entre si. Operações de estoque só bloqueiam a linha do próprio
responsável (FOR SHARE) e nunca outra linha de usuarios, portanto também não
formam ciclo com esta ordem.

Edição do próprio perfil (PATCH /me/perfil): bloqueia SÓ a linha do próprio usuário
(FOR UPDATE) e a atualiza no mesmo cursor. Com um único lock de linha não há ciclo com a
administração (uma instrução ordenada) nem com o estoque (FOR SHARE da própria linha, antes de
fornecedor/produto); no pior caso, espera a outra transação terminar.
"""
from contextlib import contextmanager

from psycopg2.errors import DeadlockDetected, ForeignKeyViolation, RestrictViolation, UniqueViolation

from core.errors import AuthorizationError, BusinessError, NotFoundError
from core.permissoes import ALIASES_ADMINISTRADOR, normalizar_perfil, pode
from models.usuarios import Usuario

COLUNAS_PUBLICAS = 'id_usuario, nome, sobrenome, email, perfil, unidade, telefone, ativo, acesso'
LOCK_ADMINISTRATIVO = ('SELECT id_usuario, ativo, perfil FROM usuarios '
                       'WHERE id_usuario IN (%s, %s) OR (ativo = TRUE AND lower(btrim(perfil)) IN %s) '
                       'ORDER BY id_usuario FOR UPDATE')


class UsuarioRepository:
    def __init__(self, connection):
        self.conn = connection

    def get_usuario_por_email(self, email: str) -> Usuario | None:
        busca = "SELECT id_usuario, nome, sobrenome, email, senha, perfil, unidade, telefone, ativo, acesso FROM usuarios WHERE email = %s"

        with self.conn.cursor() as cursor:
            cursor.execute(busca, (email,))
            resultado = cursor.fetchone()

            if resultado:
                id_usuario, nome, sobrenome, email, senha, perfil, unidade, telefone, ativo, acesso = resultado

                return Usuario(id_usuario, nome, sobrenome, email, senha, perfil, unidade, telefone, ativo, acesso)
            else:
                return None

    def obter_acesso(self, usuario_id: int):
        """(ativo, perfil) persistidos, sem senha; None se o usuário não existe."""
        with self.conn.cursor() as cursor:
            cursor.execute('SELECT ativo, perfil FROM usuarios WHERE id_usuario = %s', (usuario_id,))
            return cursor.fetchone()

    def get_usuario_por_id(self, usuario_id: int) -> Usuario | None:
        with self.conn.cursor() as cursor:
            return self._obter_publico(cursor, usuario_id)

    def criar_usuario(self, email: str, hashed_password: str, nome: str, sobrenome: str, perfil: str, unidade: str = None, telefone: str = None) -> Usuario | None:
        insert = "INSERT INTO usuarios (email, senha, nome, sobrenome, perfil, unidade, telefone) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id_usuario"

        try:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    insert,
                    (email, hashed_password, nome, sobrenome, perfil, unidade, telefone)
                )
                novo_id = cursor.fetchone()[0]

                self.conn.commit()

            return Usuario(id=novo_id, nome=nome, sobrenome=sobrenome, email=email, password=hashed_password, perfil=perfil, unidade=unidade, telefone=telefone)

        except UniqueViolation as error:
            self.conn.rollback()
            # Nome da constraint UNIQUE(email) do schema legado. Outras causas
            # devem continuar como erro interno, nunca como e-mail duplicado.
            if error.diag.constraint_name == 'usuarios_email_key':
                return None
            raise
        except Exception:
            self.conn.rollback()
            raise

    @contextmanager
    def _transacao_administrativa(self, ator_id, alvo_id):
        """Bloqueia responsável, alvo e administradores ativos; revalida o responsável sob lock."""
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(LOCK_ADMINISTRATIVO, (ator_id, alvo_id, ALIASES_ADMINISTRADOR))
                linhas = {row[0]: (row[1], normalizar_perfil(row[2])) for row in cursor.fetchall()}
                ator = linhas.get(ator_id)
                if ator is None or ator[0] is not True:
                    raise AuthorizationError('Usuário inativo ou indisponível.')
                if not pode(ator[1], 'usuarios:gerenciar'):
                    raise AuthorizationError('Seu perfil não pode administrar usuários.')
                if alvo_id not in linhas:
                    raise NotFoundError('Usuário não encontrado.')
                admins = {i for i, (ativo, perfil) in linhas.items() if ativo is True and perfil == 'ADMINISTRADOR'}
                yield cursor, linhas[alvo_id], admins
            self.conn.commit()
        except (ForeignKeyViolation, RestrictViolation):
            self.conn.rollback()
            raise BusinessError('Usuário referenciado pelo histórico não pode ser excluído. Inative-o.') from None
        except UniqueViolation as error:
            self.conn.rollback()
            if error.diag.constraint_name == 'usuarios_email_key':
                raise BusinessError('E-mail já cadastrado.') from None
            raise
        except Exception:
            self.conn.rollback()
            raise

    @staticmethod
    def _proteger(ator_id, alvo_id, alvo, admins, perfil, ativo, excluir=False):
        """D7: vale para PUT /users, PATCH /users/<id>/acesso e DELETE /users/<id>."""
        if alvo_id == ator_id:
            if excluir:
                raise BusinessError('Você não pode excluir o próprio usuário.')
            if perfil != alvo[1] or ativo is not alvo[0]:
                raise BusinessError('Você não pode alterar o próprio perfil ou a própria atividade.')
        perde = excluir or perfil != 'ADMINISTRADOR' or ativo is not True
        if alvo_id in admins and perde and not admins - {alvo_id}:
            raise BusinessError('Operação bloqueada: o sistema precisa manter ao menos um ADMINISTRADOR ativo.')

    @staticmethod
    def _obter_publico(cursor, usuario_id):
        cursor.execute(f'SELECT {COLUNAS_PUBLICAS} FROM usuarios WHERE id_usuario = %s', (usuario_id,))
        row = cursor.fetchone()
        return Usuario(row[0], row[1], row[2], row[3], '', *row[4:]) if row else None

    def alterar_acesso(self, ator_id: int, alvo_id: int, perfil: str | None, ativo: bool | None,
                       unidade=...) -> Usuario:
        """perfil canônico ou None (preserva); ativo bool ou None (preserva); unidade texto/None
        (grava) ou Ellipsis (preserva). Senha e demais dados pessoais intocados."""
        with self._transacao_administrativa(ator_id, alvo_id) as (cursor, alvo, admins):
            novo_perfil = perfil if perfil is not None else alvo[1]
            novo_ativo = ativo if ativo is not None else alvo[0]
            self._proteger(ator_id, alvo_id, alvo, admins, novo_perfil, novo_ativo)
            campos, valores = [], []
            if perfil is not None:
                campos.append('perfil = %s'); valores.append(perfil)
            if ativo is not None:
                campos.append('ativo = %s'); valores.append(ativo)
            if unidade is not ...:
                campos.append('unidade = %s'); valores.append(unidade)
            cursor.execute(f"UPDATE usuarios SET {', '.join(campos)} WHERE id_usuario = %s", (*valores, alvo_id))
            return self._obter_publico(cursor, alvo_id)

    def atualizar_usuario(self, ator_id: int, usuario_id: int, nome: str, sobrenome: str, email: str,
                          senha: str | None, perfil: str, unidade: str | None = None,
                          telefone: str | None = None, ativo: bool | None = None) -> Usuario:
        """senha=None preserva o hash atual; ativo=None preserva a atividade. Nunca altera acesso."""
        with self._transacao_administrativa(ator_id, usuario_id) as (cursor, alvo, admins):
            novo_ativo = ativo if ativo is not None else alvo[0]
            self._proteger(ator_id, usuario_id, alvo, admins, perfil, novo_ativo)
            campos = ['nome = %s', 'sobrenome = %s', 'email = %s', 'perfil = %s', 'unidade = %s',
                      'telefone = %s', 'ativo = %s']
            valores = [nome, sobrenome, email, perfil, unidade, telefone, novo_ativo]
            if senha is not None:
                campos.append('senha = %s'); valores.append(senha)
            cursor.execute(f"UPDATE usuarios SET {', '.join(campos)} WHERE id_usuario = %s", (*valores, usuario_id))
            return self._obter_publico(cursor, usuario_id)

    def atualizar_perfil_proprio(self, usuario_id: int, campos: dict) -> Usuario | None:
        """campos: subconjunto validado de nome/sobrenome/email/telefone (PerfilProprioDTO).
        None = usuário removido ou inativo sob lock (sessão deve ser revogada). Perfil, ativo,
        unidade, senha e acesso nunca entram no UPDATE."""
        assert campos and set(campos) <= {'nome', 'sobrenome', 'email', 'telefone'}
        try:
            with self.conn.cursor() as cursor:
                cursor.execute('SELECT ativo, perfil FROM usuarios WHERE id_usuario = %s FOR UPDATE', (usuario_id,))
                row = cursor.fetchone()
                if not row or row[0] is not True:
                    self.conn.rollback()
                    return None
                if not pode(row[1], 'perfil:editar_proprio'):
                    raise AuthorizationError('Seu perfil não pode editar dados pessoais.')
                cursor.execute(f"UPDATE usuarios SET {', '.join(c + ' = %s' for c in campos)} WHERE id_usuario = %s",
                               (*campos.values(), usuario_id))
                usuario = self._obter_publico(cursor, usuario_id)
            self.conn.commit()
            return usuario
        except UniqueViolation as error:
            self.conn.rollback()
            if error.diag.constraint_name == 'usuarios_email_key':
                raise BusinessError('E-mail já cadastrado.') from None
            raise
        except DeadlockDetected:
            # Ex.: duas contas trocando e-mails entre si ao mesmo tempo; o PostgreSQL aborta uma.
            self.conn.rollback()
            raise BusinessError('Alteração concorrente. Recarregue a página e tente novamente.') from None
        except Exception:
            self.conn.rollback()
            raise

    def deletar_usuario(self, ator_id: int, usuario_id: int) -> bool:
        with self._transacao_administrativa(ator_id, usuario_id) as (cursor, alvo, admins):
            self._proteger(ator_id, usuario_id, alvo, admins, None, None, excluir=True)
            cursor.execute('DELETE FROM usuarios WHERE id_usuario = %s', (usuario_id,))
            return cursor.rowcount > 0

    def atualizar_acesso(self, usuario_id: int, acesso) -> None:
        update = "UPDATE usuarios SET acesso = %s WHERE id_usuario = %s"

        with self.conn.cursor() as cursor:
            cursor.execute(update, (acesso, usuario_id))
            self.conn.commit()

    def get_usuario_email_por_id(self, usuario_id: int) -> str | None:
        busca = "SELECT email FROM usuarios WHERE id_usuario = %s"

        with self.conn.cursor() as cursor:
            cursor.execute(busca, (usuario_id,))
            resultado = cursor.fetchone()

            if resultado:
                return resultado[0]

        return None

    def listar_usuarios(self) -> list[Usuario]:
        with self.conn.cursor() as cursor:
            consulta = "SELECT id_usuario, nome, sobrenome, email, perfil, unidade, telefone, ativo, acesso FROM usuarios"
            cursor.execute(consulta)
            resultados = cursor.fetchall()

            usuarios = []
            for resultado in resultados:
                id_usuario, nome, sobrenome, email, perfil, unidade, telefone, ativo, acesso = resultado
                usuario = Usuario(id_usuario, nome, sobrenome, email, "", perfil, unidade, telefone, ativo, acesso)
                usuarios.append(usuario)

            return usuarios

    def listar_usuarios_ativos(self) -> list[Usuario]:
        with self.conn.cursor() as cursor:
            consulta = "SELECT id_usuario, nome, sobrenome, email, perfil, unidade, telefone, ativo, acesso FROM usuarios WHERE ativo = TRUE"
            cursor.execute(consulta)
            resultados = cursor.fetchall()

            usuarios_ativos = []
            for resultado in resultados:
                id_usuario, nome, sobrenome, email, perfil, unidade, telefone, ativo, acesso = resultado
                usuario = Usuario(id_usuario, nome, sobrenome, email, "", perfil, unidade, telefone, ativo, acesso)
                usuarios_ativos.append(usuario)

            return usuarios_ativos
