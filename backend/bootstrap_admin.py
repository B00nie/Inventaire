"""Bootstrap manual; nunca importado/executado pelo servidor e sem senha padrão."""
import argparse
from getpass import getpass
import os
from pathlib import Path

from dotenv import load_dotenv
import psycopg2

from core.errors import ValidationError
from schemas.usuario_dto import SignupDTO
from services.usuario_service import UsuarioService


def database_config(expected_database):
    load_dotenv(Path(__file__).resolve().parent / '.env')
    missing = [key for key in ('DB_HOST', 'DB_PORT', 'DB_USER', 'DB_NAME') if not os.getenv(key)]
    if missing:
        raise ValidationError('Configuração pendente: ' + ', '.join(missing))
    name = os.environ['DB_NAME']
    if name != expected_database or name.lower() in ('postgres', 'template0', 'template1') or 'spi' in name.lower():
        raise ValidationError('Destino divergente, reservado ou legado. Nenhuma alteração permitida.')
    return dict(host=os.environ['DB_HOST'], port=os.environ['DB_PORT'],
                dbname=name, user=os.environ['DB_USER'], password=os.getenv('DB_PASSWORD'),
                connect_timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, help='Nome exato da base exclusiva do Inventaire')
    args = parser.parse_args()
    connection = None
    try:
        config = database_config(args.database)
        print(f"Host: {config['host']} | Porta: {config['port']} | Banco: {config['dbname']}")
        confirmation = input('Confirme que esta base é exclusiva do Inventaire digitando seu nome: ')
        if confirmation != args.database:
            raise ValidationError('Confirmação divergente; nenhuma alteração realizada.')
        data = dict(nome=input('Nome: '), sobrenome=input('Sobrenome: '),
                    email=input('E-mail: '), unidade=input('Unidade (opcional): '),
                    telefone=input('Telefone (opcional): '), perfil='admin')
        data['password'] = getpass('Senha: ')
        if data['password'] != getpass('Repita a senha: '):
            raise ValidationError('As senhas não coincidem.')
        dto = SignupDTO.from_dict(data)
        connection = psycopg2.connect(**config)
        user = UsuarioService(connection).signup(
            dto.email, dto.password, dto.nome, dto.sobrenome, dto.perfil, dto.unidade, dto.telefone)
        if user is None:
            raise ValidationError('E-mail já cadastrado; usuário existente preservado.')
        print('Administrador criado com bcrypt. Nenhuma senha foi impressa.')
        return 0
    except ValidationError as error:
        print(str(error))
        return 1
    except Exception as error:
        # Exceções do driver podem incluir valores de conexão/SQL; exibir só categoria.
        print(f'Bootstrap interrompido: {type(error).__name__}. Nenhuma credencial será exibida.')
        return 1
    finally:
        if connection is not None:
            try:
                connection.rollback()
            finally:
                connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
