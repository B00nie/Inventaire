from dataclasses import dataclass
from core.errors import ValidationError
from core.permissoes import PERFIS, normalizar_perfil


def _perfil(data, key='perfil'):
    """Aceita canônicos e aliases aprovados; devolve sempre o valor canônico."""
    perfil = normalizar_perfil(_text(data, key))
    if perfil is None:
        raise ValidationError('perfil deve ser ' + ', '.join(PERFIS) + '.')
    return perfil


def _somente(data, permitidos):
    # Bloqueia mass assignment: admin, permissoes, senha via PATCH etc.
    extras = set(data) - set(permitidos)
    if extras:
        raise ValidationError('Campos não permitidos: ' + ', '.join(sorted(map(str, extras))) + '.')


def _ativo_opcional(data):
    ativo = data.get('ativo')
    if ativo is not None and type(ativo) is not bool:
        raise ValidationError("Ativo must be a boolean.")
    return ativo


def _payload(data):
    if not isinstance(data, dict):
        raise ValidationError("Payload invalid.")


def _text(data, key, required=True):
    value = data.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{key} must be a string.")
    value = value.strip()
    if not value:
        if required:
            raise ValidationError(f"{key} is required.")
        return None
    return value


def _password(data, key):
    value = _text(data, key)
    # bcrypt aceita no máximo 72 bytes, independentemente do número de caracteres.
    if len(value.encode('utf-8')) > 72:
        raise ValidationError("Password must not exceed 72 UTF-8 bytes.")
    return value


@dataclass
class LoginDTO:
    email: str
    password: str

    @classmethod
    def from_dict(cls, data: dict):
        _payload(data)
        return cls(email=_text(data, 'email').lower(),
                   password=_password(data, 'password'))


@dataclass
class SignupDTO:
    email: str
    password: str
    nome: str
    sobrenome: str
    perfil: str
    unidade: str | None = None
    telefone: str | None = None

    @classmethod
    def from_dict(cls, data: dict):
        _payload(data)
        _somente(data, ('email', 'password', 'nome', 'sobrenome', 'perfil', 'unidade', 'telefone'))
        return cls(
            email=_text(data, 'email').lower(),
            password=_password(data, 'password'),
            nome=_text(data, 'nome'),
            sobrenome=_text(data, 'sobrenome'),
            perfil=_perfil(data),
            unidade=_text(data, 'unidade', required=False),
            telefone=_text(data, 'telefone', required=False),
        )


@dataclass
class UsuarioDTO:
    id: int
    nome: str
    sobrenome: str
    email: str
    senha: str | None  # None = senha não alterada (hash preservado)
    perfil: str
    unidade: str | None = None
    telefone: str | None = None
    ativo: bool | None = None  # None = atividade preservada

    @classmethod
    def from_dict(cls, data: dict):
        _payload(data)
        _somente(data, ('id', 'nome', 'sobrenome', 'email', 'senha', 'perfil', 'unidade', 'telefone', 'ativo'))
        usuario_id = data.get('id')
        if type(usuario_id) is not int or usuario_id <= 0:
            raise ValidationError("ID must be a positive integer.")
        return cls(
            id=usuario_id,
            nome=_text(data, 'nome'),
            sobrenome=_text(data, 'sobrenome'),
            email=_text(data, 'email').lower(),
            # Ausente/null preserva; presente nunca pode ser vazio.
            senha=_password(data, 'senha') if data.get('senha') is not None else None,
            perfil=_perfil(data),
            unidade=_text(data, 'unidade', required=False),
            telefone=_text(data, 'telefone', required=False),
            ativo=_ativo_opcional(data),
        )


# Limites reais das colunas de usuarios (VARCHAR do schema oficial): validados antes do banco.
LIMITES_PERFIL_PROPRIO = {'nome': 40, 'sobrenome': 90, 'email': 60, 'telefone': 45}


@dataclass
class PerfilProprioDTO:
    """PATCH /me/perfil: só dados pessoais da própria conta. Qualquer outro campo (id, perfil,
    ativo, unidade, senha, admin...) → 400, mesmo com o valor atual. Telefone vazio/null = sem telefone."""
    campos: dict

    @classmethod
    def from_dict(cls, data: dict):
        _payload(data)
        if not data:
            raise ValidationError('Informe ao menos um campo: nome, sobrenome, email ou telefone.')
        _somente(data, LIMITES_PERFIL_PROPRIO)
        campos = {}
        for chave, limite in LIMITES_PERFIL_PROPRIO.items():
            if chave not in data:
                continue
            valor = _text(data, chave, required=chave != 'telefone')
            if chave == 'email':
                valor = valor.lower()
            if valor is not None and len(valor) > limite:
                raise ValidationError(f'{chave} deve ter no máximo {limite} caracteres.')
            campos[chave] = valor
        return cls(campos)


@dataclass
class AcessoDTO:
    perfil: str | None
    ativo: bool | None
    # Ellipsis = unidade não enviada (preservada); None = sem unidade (null ou texto vazio).
    unidade: str | None = ...

    @classmethod
    def from_dict(cls, data: dict):
        _payload(data)
        _somente(data, ('perfil', 'ativo', 'unidade'))
        perfil = _perfil(data) if data.get('perfil') is not None else None
        ativo = _ativo_opcional(data)
        unidade = ...
        if 'unidade' in data:
            unidade = _text(data, 'unidade', required=False)
            if unidade is not None and len(unidade) > 45:
                raise ValidationError('unidade deve ter no máximo 45 caracteres.')
        if perfil is None and ativo is None and unidade is ...:
            raise ValidationError('Informe perfil, ativo e/ou unidade.')
        return cls(perfil, ativo, unidade)
