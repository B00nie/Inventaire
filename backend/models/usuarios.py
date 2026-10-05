from dataclasses import dataclass, field
from datetime import datetime

from core.permissoes import normalizar_perfil

@dataclass
class Usuario:
    id: int
    nome: str
    sobrenome: str
    email: str
    password: str
    perfil: str
    unidade: str | None = None
    telefone: str | None = None
    ativo: bool = True
    acesso: str | datetime = field(default_factory=lambda: datetime.now().isoformat())

    def get_id(self) -> int | None:
        return self.id

    def get_nome(self) -> str:
        return self.nome

    def get_sobrenome(self) -> str:
        return self.sobrenome

    def get_nome_completo(self) -> str:
        if self.sobrenome:
            return f"{self.nome} {self.sobrenome}"
        return self.nome

    def get_email(self) -> str:
        return self.email

    def get_password(self) -> str:
        return self.password

    def get_perfil(self) -> str:
        return self.perfil

    def is_admin(self) -> bool:
        return normalizar_perfil(self.perfil) == 'ADMINISTRADOR'

    def get_unidade(self) -> str | None:
        return self.unidade

    def get_telefone(self) -> str | None:
        return self.telefone

    def is_ativo(self) -> bool:
        return self.ativo

    def get_acesso(self) -> str | None:
        return self.acesso

    def set_acesso(self, acesso: datetime) -> None:
        self.acesso = acesso
