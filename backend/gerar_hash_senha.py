"""Gera localmente o hash bcrypt de uma senha para 02_dados_iniciais_inventaire.sql.

Não conecta ao banco, não lê .env, não grava arquivo e não registra log: a senha é
lida sem eco e o hash sai só neste terminal, para ser colado na cópia local do SQL.
O hash é material de autenticação: não o publique nem o versione. Não há senha padrão.
Uso (da raiz): .\\.venv\\Scripts\\python.exe -B backend/gerar_hash_senha.py
"""
from getpass import getpass
import re

from core.security import Security

# Mesma expressão de usuarios_senha_bcrypt_check (01_estrutura_inventaire.sql).
BCRYPT_CHECK = re.compile(r'^\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$')


def gerar_hash(senha: str) -> str:
    if not senha.strip():
        raise ValueError('A senha não pode ser vazia.')
    # O login (LoginDTO) remove espaços das pontas: o hash precisa ser da senha já aparada.
    if senha != senha.strip():
        raise ValueError('A senha não pode começar nem terminar com espaço.')
    # Mesmo limite do login (schemas/usuario_dto.py): bcrypt usa no máximo 72 bytes.
    if len(senha.encode('utf-8')) > 72:
        raise ValueError('A senha não pode exceder 72 bytes UTF-8.')
    hash_senha = Security().hash_password(senha)
    if not BCRYPT_CHECK.match(hash_senha):
        raise ValueError('Hash gerado incompatível com a constraint do banco.')
    return hash_senha


def main():
    try:
        senha = getpass('Senha (sem eco): ')
        if senha != getpass('Repita a senha: '):
            print('As senhas não coincidem. Nenhum hash gerado.')
            return 1
        print(gerar_hash(senha))
        print('Cole o hash acima somente na cópia local do SQL. Não o publique.')
        return 0
    except ValueError as erro:
        print(f'{erro} Nenhum hash gerado.')
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nCancelado. Nenhum hash gerado.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
