import bcrypt

class Security:
    def check_password(self, password: str, hashed_password: str | bytes) -> bool:
            password_bytes = password.encode("utf-8")

            if isinstance(hashed_password, str):
                hashed_password = hashed_password.encode("utf-8")

            return bcrypt.checkpw(password_bytes, hashed_password)

    def hash_password(self, password: str) -> str:
        # Converte a senha de string para bytes
        password_bytes = password.encode('utf-8')
        
        # Gera um salt e faz o hash da senha
        salt_password = bcrypt.hashpw(password_bytes, bcrypt.gensalt(rounds=12))

        # Converte o hash de bytes para string antes de armazenar
        password_string = salt_password.decode('utf-8')
        #print(f"Hash gerado (string): {password_string}")

        return password_string