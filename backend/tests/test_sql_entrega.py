"""Consolidação da entrega: verificação ESTÁTICA dos dois SQLs oficiais.

Só lê arquivos; nunca conecta ao PostgreSQL. Não comprova a execução real dos
scripts (guardas PL/pgSQL, constraints, rollback): isso exige instalação real
numa base vazia, ainda pendente.
"""
from collections import Counter
from pathlib import Path
import re
from unittest import TestCase, main

import bcrypt

from gerar_hash_senha import BCRYPT_CHECK, gerar_hash

BACKEND = Path(__file__).resolve().parents[1]
ASSETS = BACKEND / 'assets'
HISTORICO = ASSETS / 'historico'
TABELAS = ['usuarios', 'produtos', 'movimentacoes', 'corredores', 'posicoes_estoque',
           'itens_estoque', 'contagens_inventario', 'fornecedores']
PERFIS = ['OPERADOR', 'GESTOR', 'AUDITOR', 'ADMINISTRADOR']


def ler(path):
    return path.read_text(encoding='utf-8').replace('\r\n', '\n')


def sem_comentarios(sql):
    return re.sub(r'--[^\n]*', '', sql)


def instrucoes(sql):
    """Instruções fora dos blocos DO, com espaços normalizados."""
    sql = re.sub(r'DO \$\$.*?\$\$;', '', sem_comentarios(sql), flags=re.S)
    return [' '.join(s.split()) for s in sql.split(';') if s.strip()]


def ddl(sql):
    return [s for s in instrucoes(sql) if s.startswith(('CREATE', 'ALTER', 'COMMENT'))]


SQL01, SQL02 = ler(ASSETS / '01_estrutura_inventaire.sql'), ler(ASSETS / '02_dados_iniciais_inventaire.sql')
COMMENT_2E = next(s for s in ddl(ler(HISTORICO / 'etapa_2e_perfis.sql')) if s.startswith('COMMENT'))


class EstruturaArquivosTest(TestCase):
    def test_somente_dois_sqls_oficiais_no_diretorio_principal(self):
        self.assertEqual(sorted(p.name for p in ASSETS.glob('*.sql')),
                         ['01_estrutura_inventaire.sql', '02_dados_iniciais_inventaire.sql'])

    def test_historicos_preservados(self):
        esperados = {'etapa_2b_movimentacoes.sql', 'etapa_2c_rastreamento.sql', 'etapa_2d_contagens.sql',
                     'etapa_2e_perfis.sql', 'etapa_2g_fornecedores.sql', 'inventaire_schema.sql',
                     'tabelas_spi-postgres.sql', 'inserções_spi-postgres.sql'}
        self.assertEqual({p.name for p in HISTORICO.glob('*.sql')}, esperados)
        self.assertEqual({p.name for p in (HISTORICO / 'legado_spi').iterdir()},
                         {'modelo_DB.mwb', 'modelo_DB.mwb.bak'})

    def test_servidor_nao_executa_os_sqls(self):
        servidor = [BACKEND / 'app.py', BACKEND / 'extensions.py']
        for pasta in ('connection', 'controller', 'core', 'models', 'repository', 'schemas', 'services'):
            servidor += (BACKEND / pasta).rglob('*.py')
        for py in servidor:
            texto = py.read_text(encoding='utf-8', errors='ignore')
            self.assertNotIn('01_estrutura_inventaire', texto, py)
            self.assertNotIn('02_dados_iniciais_inventaire', texto, py)


class Estrutura01Test(TestCase):
    def test_ddl_identica_ao_schema_homologado_mais_comentario_2e(self):
        atual = ddl(SQL01)
        self.assertEqual(atual.count(COMMENT_2E), 1)
        atual.remove(COMMENT_2E)
        self.assertEqual(atual, ddl(ler(HISTORICO / 'inventaire_schema.sql')))

    def test_contem_todas_as_instrucoes_dos_incrementais(self):
        atual = set(ddl(SQL01))
        for nome in ('etapa_2b_movimentacoes.sql', 'etapa_2c_rastreamento.sql',
                     'etapa_2d_contagens.sql', 'etapa_2g_fornecedores.sql'):
            for instrucao in ddl(ler(HISTORICO / nome)):
                self.assertIn(instrucao, atual, nome)
        # 2E: mesma constraint, declarada na criação da tabela (base nova, sem normalização de aliases).
        self.assertIn("CONSTRAINT usuarios_perfil_check CHECK (perfil IN ('OPERADOR', 'GESTOR', 'AUDITOR', "
                      "'ADMINISTRADOR'))", ' '.join(sem_comentarios(SQL01).split()))

    def test_oito_tabelas_e_objetos_criados_uma_vez(self):
        texto = sem_comentarios(SQL01)
        self.assertEqual(re.findall(r'CREATE TABLE (\w+)', texto), TABELAS)
        indices = re.findall(r'CREATE INDEX (\w+)', texto)
        self.assertEqual(len(indices), 14)  # índices explícitos do catálogo 2G.2 (sem PK/UNIQUE)
        self.assertEqual(max(Counter(indices).values()), 1)
        nomes = re.findall(r'CONSTRAINT (\w+)', texto)
        self.assertEqual(max(Counter(nomes).values()), 1)
        self.assertEqual(texto.count('GENERATED ALWAYS AS IDENTITY PRIMARY KEY'), 8)

    def test_ordem_das_dependencias(self):
        texto = sem_comentarios(SQL01)
        criacao = {t: texto.index(f'CREATE TABLE {t} (') for t in TABELAS}
        for ref in re.finditer(r'REFERENCES (\w+)\(', texto):
            self.assertLess(criacao[ref.group(1)], ref.start(), ref.group(0))
        # Alvo da FK composta contagens → movimentacoes precisa existir antes.
        self.assertLess(texto.index('ADD CONSTRAINT movimentacoes_contagem_ajuste_key'),
                        criacao['contagens_inventario'])

    def test_transacional_guardado_e_sem_ddl_destrutivo_ou_dados(self):
        texto = sem_comentarios(SQL01)
        self.assertEqual(instrucoes(SQL01)[0], 'BEGIN')
        self.assertEqual(instrucoes(SQL01)[-1], 'COMMIT')
        self.assertIn("current_database() <> 'inventaire'", texto)
        self.assertIn('não está vazia', texto)
        proibidos = r'\b(DROP|TRUNCATE|CASCADE|IF NOT EXISTS|INSERT INTO|DELETE FROM|CREATE DATABASE|CREATE EXTENSION)\b'
        self.assertIsNone(re.search(proibidos, texto, re.I))
        self.assertIsNone(re.search(r'\bUPDATE\s+\w+\s+SET\b', texto, re.I))
        self.assertNotIn('\\i ', texto)


class Seed02Test(TestCase):
    def test_placeholders_e_comentarios_por_perfil(self):
        for perfil in PERFIS:
            for campo in ('NOME', 'SOBRENOME', 'HASH_BCRYPT'):
                self.assertEqual(SQL02.count(f"'PREENCHER_{campo}_{perfil}'"), 1, (campo, perfil))
        self.assertEqual(SQL02.count('-- Coloque o nome do usuário aqui.'), 4)
        self.assertEqual(SQL02.count('-- Coloque o sobrenome do usuário aqui.'), 4)
        self.assertEqual(SQL02.count('-- Coloque o hash bcrypt da senha deste usuário aqui.'), 4)

    def test_quatro_perfis_e_emails_previstos(self):
        self.assertIn("ARRAY['OPERADOR', 'GESTOR', 'AUDITOR', 'ADMINISTRADOR']", SQL02)
        self.assertIn("ARRAY['operador@empresa.com', 'gestor@empresa.com',\n"
                      "                               'auditor@empresa.com', 'administrador@empresa.com']", SQL02)

    def test_sem_hash_real_senha_ou_extensao(self):
        self.assertIsNone(re.search(r'\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}', SQL02))
        self.assertIsNone(re.search(r'pgcrypto|CREATE EXTENSION|\bcrypt\s*\(|gen_salt', SQL02, re.I))

    def test_aborta_antes_de_inserir_e_nao_altera_existentes(self):
        texto = sem_comentarios(SQL02)
        self.assertEqual(instrucoes(SQL02)[0], 'BEGIN')
        self.assertEqual(instrucoes(SQL02)[-1], 'COMMIT')
        insert = texto.index('INSERT INTO usuarios')
        for guarda in ("current_database() <> 'inventaire'", 'IF existentes IS NOT NULL THEN',
                       "LIKE '%PREENCHER%'", 'Configuração inválida; nenhum usuário foi inserido'):
            self.assertLess(texto.index(guarda), insert, guarda)
        self.assertEqual(texto.count('INSERT INTO'), 1)
        self.assertIn('INSERT INTO usuarios (nome, sobrenome, email, senha, perfil, ativo)', texto)
        self.assertIn('perfis[i], TRUE)', texto)
        proibidos = r'\b(ON CONFLICT|DELETE FROM|DROP|TRUNCATE|CASCADE|OVERRIDING)\b'
        self.assertIsNone(re.search(proibidos, texto, re.I))
        self.assertIsNone(re.search(r'\bUPDATE\s+\w+\s+SET\b', texto, re.I))
        self.assertNotIn('id_usuario)', texto.split('INSERT INTO usuarios')[1].split('VALUES')[0])

    def test_condicoes_plpgsql_sem_case(self):
        # PL/pgSQL corta a condição de IF/ELSIF no primeiro THEN, inclusive o de um CASE
        # ("erro de sintaxe no fim da entrada" na execução real do 02).
        for sql in (SQL01, SQL02):
            for corpo in re.findall(r'DO \$\$(.*?)\$\$;', sem_comentarios(sql), re.S):
                for condicao in re.findall(r'(?<!END )\b(?:ELSIF|IF)\b(.*?)\bTHEN\b', corpo, re.S):
                    self.assertNotIn('CASE', condicao)

    def test_regex_do_seed_igual_a_constraint_e_ao_utilitario(self):
        constraint = re.search(r"usuarios_senha_bcrypt_check\s+CHECK \(senha ~ '([^']+)'\)", SQL01).group(1)
        self.assertIn(f"valor !~ '{constraint}'", SQL02)
        self.assertEqual(BCRYPT_CHECK.pattern, constraint)


class GerarHashTest(TestCase):
    def test_hash_compativel_com_constraint_e_login(self):
        senha = 'senha-local-de-teste'
        hash_senha = gerar_hash(senha)
        self.assertRegex(hash_senha, BCRYPT_CHECK)
        self.assertTrue(hash_senha.startswith('$2b$12$'))
        self.assertTrue(bcrypt.checkpw(senha.encode(), hash_senha.encode()))
        self.assertNotEqual(hash_senha, gerar_hash(senha))  # salt aleatório

    def test_recusa_senhas_invalidas(self):
        for senha in ('', '   ', ' com-espaco', 'com-espaco ', 'x' * 73, 'ç' * 37):
            with self.assertRaises(ValueError):
                gerar_hash(senha)


if __name__ == '__main__':
    main()
