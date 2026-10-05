from repository.usuario_repository import UsuarioRepository
from schemas.usuario_dto import AcessoDTO, PerfilProprioDTO, UsuarioDTO
from core.permissoes import normalizar_perfil, permissoes_de
from core.security import Security

from datetime import datetime

class UsuarioService:
    def __init__(self, connection):
        self.user_repository = UsuarioRepository(connection)
        self.security = Security()

    def login(self, email: str, password: str):
        usuario = self.user_repository.get_usuario_por_email(email)

        if usuario and usuario.is_ativo() and self.security.check_password(password, usuario.get_password()):

            # Atualiza a data do último login do usuário
            usuario.set_acesso(datetime.now())
            self.user_repository.atualizar_acesso(usuario.get_id(), usuario.get_acesso())

            return usuario

        return None

    def signup(self, email: str, password: str, nome: str, sobrenome: str, perfil: str, unidade: str = None, telefone: str = None):
        usuario_existente = self.user_repository.get_usuario_por_email(email)

        if usuario_existente:
            return None

        hashed_password = self.security.hash_password(password)

        novo_usuario = self.user_repository.criar_usuario(email, hashed_password, nome, sobrenome, perfil, unidade, telefone)

        return novo_usuario

    def obter_email_usuario_por_id(self, usuario_id: int) -> str | None:
        email = self.user_repository.get_usuario_email_por_id(usuario_id)

        if email:
            return email

        return None

    def obter_status_ativo(self, email: str) -> bool:
        usuario = self.user_repository.get_usuario_por_email(email)

        if usuario:
            return usuario.is_ativo()

        return False

    @staticmethod
    def serializar(usuario) -> dict:
        """Campos públicos; perfil canônico (valor bruto só se não reconhecido). Nunca senha."""
        return {
            'id': usuario.get_id(),
            'nome': usuario.get_nome(),
            'sobrenome': usuario.get_sobrenome(),
            'email': usuario.get_email(),
            'perfil': normalizar_perfil(usuario.get_perfil()) or usuario.get_perfil(),
            'unidade': usuario.get_unidade(),
            'telefone': usuario.get_telefone(),
            'admin': usuario.is_admin(),
            'ativo': usuario.is_ativo(),
            'acesso': usuario.get_acesso()
        }

    def sessao(self, usuario_id: int) -> dict | None:
        """Estado persistido atual; None se removido ou inativo (sessão deve ser revogada)."""
        usuario = self.user_repository.get_usuario_por_id(usuario_id)
        if usuario is None or usuario.is_ativo() is not True:
            return None
        return self._dados_sessao(usuario)

    def _dados_sessao(self, usuario) -> dict:
        dados = self.serializar(usuario)
        del dados['acesso']
        dados['perfil_reconhecido'] = normalizar_perfil(usuario.get_perfil()) is not None
        dados['permissoes'] = sorted(permissoes_de(usuario.get_perfil()))
        return dados

    def atualizar_perfil_proprio(self, usuario_id: int, data) -> dict | None:
        """Alvo = usuário da sessão (nunca do payload). None = inativo/removido. Mesmo formato de /session."""
        dto = PerfilProprioDTO.from_dict(data)
        usuario = self.user_repository.atualizar_perfil_proprio(usuario_id, dto.campos)
        return None if usuario is None else self._dados_sessao(usuario)

    def listar_usuarios(self) -> list[dict]:
        return [self.serializar(u) for u in self.user_repository.listar_usuarios() or []]

    def atualizar_usuario(self, ator_id: int, data) -> dict:
        usuario_dto = UsuarioDTO.from_dict(data)
        senha = self.security.hash_password(usuario_dto.senha) if usuario_dto.senha is not None else None

        usuario_atualizado = self.user_repository.atualizar_usuario(
            ator_id,
            usuario_dto.id,
            usuario_dto.nome,
            usuario_dto.sobrenome,
            usuario_dto.email,
            senha,
            usuario_dto.perfil,
            usuario_dto.unidade,
            usuario_dto.telefone,
            usuario_dto.ativo
        )

        return self.serializar(usuario_atualizado)

    def alterar_acesso(self, ator_id: int, usuario_id: int, data) -> dict:
        dto = AcessoDTO.from_dict(data)
        return self.serializar(self.user_repository.alterar_acesso(ator_id, usuario_id, dto.perfil, dto.ativo, dto.unidade))

    def deletar_usuario(self, ator_id: int, usuario_id: int) -> bool:
        return self.user_repository.deletar_usuario(ator_id, usuario_id)

    def listar_usuarios_ativos(self) -> list[dict]:
        return [self.serializar(u) for u in self.user_repository.listar_usuarios_ativos() or []]
