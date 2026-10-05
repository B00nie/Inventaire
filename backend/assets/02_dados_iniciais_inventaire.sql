-- ============================================================================
-- Inventaire — 02_dados_iniciais_inventaire.sql  (MODELO COM PLACEHOLDERS)
-- Usuários iniciais de uma INSTALAÇÃO NOVA: OPERADOR, GESTOR, AUDITOR e
-- ADMINISTRADOR. Nenhum outro dado (sem produtos, fornecedores, lotes ou
-- movimentações). IDs gerados pela identity; nenhum ID fixo.
--
-- ORDEM: executar DEPOIS de 01_estrutura_inventaire.sql, na base inventaire.
-- Usuários já existentes são PRESERVADOS (nenhum UPDATE/DELETE); se algum dos
-- quatro e-mails abaixo já estiver cadastrado, o script aborta sem alterar nada.
--
-- COMO PREENCHER (ver README.md, seção "Usuários iniciais"):
--   1. Copie este arquivo para 02_dados_iniciais_inventaire.local.sql
--      (nome ignorado pelo .gitignore) e edite SOMENTE a cópia.
--   2. Substitua os 12 placeholders PREENCHER_* do bloco CONFIGURAÇÃO.
--      Valores ficam entre aspas simples; apóstrofo é escrito duplicado
--      (ex.: 'D''Ávila'). Sem espaços no início/fim.
--   3. Hash: gere um por usuário, localmente, com
--         .\.venv\Scripts\python.exe -B backend/gerar_hash_senha.py
--      e cole o resultado ($2b$12$...). NÃO existe senha padrão.
--   4. Execute a cópia preenchida. Enquanto restar qualquer placeholder,
--      o script aborta sem inserir nada.
--
-- SEGURANÇA: a cópia preenchida contém material de autenticação (hashes
-- bcrypt). Não a publique nem a versione; apague-a após a instalação.
-- Este modelo versionado deve continuar com os placeholders.
--
-- Os e-mails abaixo são contas de exemplo para avaliação, não caixas
-- postais cuja existência tenha sido verificada.
-- ============================================================================
BEGIN;
SET LOCAL search_path = public;

DO $$
DECLARE
    -- ======================== CONFIGURAÇÃO ==================================

    -- ---- OPERADOR  (e-mail: operador@empresa.com) ----
    -- Coloque o nome do usuário aqui.
    nome_operador          TEXT := 'Enzo';
    -- Coloque o sobrenome do usuário aqui.
    sobrenome_operador     TEXT := 'Silva';
    -- Coloque o hash bcrypt da senha deste usuário aqui.
    hash_operador          TEXT := '$2b$12$3dL0nxlXD73uAA4az98Owu.cq48f5FgqMY0.dFdqxJk/ue5qFJITS';

    -- ---- GESTOR  (e-mail: gestor@empresa.com) ----
    -- Coloque o nome do usuário aqui.
    nome_gestor            TEXT := 'Rodrigo';
    -- Coloque o sobrenome do usuário aqui.
    sobrenome_gestor       TEXT := 'Medina';
    -- Coloque o hash bcrypt da senha deste usuário aqui.
    hash_gestor            TEXT := '$2b$12$Omzes1EBUeFyfq9tIPnJT.QISyhE4qK77ZZvRNmxDt2ottIL8QTsm';

    -- ---- AUDITOR  (e-mail: auditor@empresa.com) ----
    -- Coloque o nome do usuário aqui.
    nome_auditor           TEXT := 'Antonio';
    -- Coloque o sobrenome do usuário aqui.
    sobrenome_auditor      TEXT := 'Ribeiro';
    -- Coloque o hash bcrypt da senha deste usuário aqui.
    hash_auditor           TEXT := '$2b$12$IyqaHvSMapYaAvtn6bL3s.ONDAKsJ/qRHL8Zg6V5e/fsI2B5vOcT6';

    -- ---- ADMINISTRADOR  (e-mail: administrador@empresa.com) ----
    -- Coloque o nome do usuário aqui.
    nome_administrador      TEXT := 'Cauã';
    -- Coloque o sobrenome do usuário aqui.
    sobrenome_administrador TEXT := 'Pereira';
    -- Coloque o hash bcrypt da senha deste usuário aqui.
    hash_administrador      TEXT := '$2b$12$zNSReNZrbxWDmRPDLGR.0OtNAWx3dFE0ji2WAcA79hjjBh5YsaCl6';

    -- ===================== FIM DA CONFIGURAÇÃO ==============================
    -- Não altere nada abaixo desta linha.

    perfis     TEXT[] := ARRAY['OPERADOR', 'GESTOR', 'AUDITOR', 'ADMINISTRADOR'];
    emails     TEXT[] := ARRAY['operador@empresa.com', 'gestor@empresa.com',
                               'auditor@empresa.com', 'administrador@empresa.com'];
    nomes      TEXT[] := ARRAY[nome_operador, nome_gestor, nome_auditor, nome_administrador];
    sobrenomes TEXT[] := ARRAY[sobrenome_operador, sobrenome_gestor, sobrenome_auditor, sobrenome_administrador];
    hashes     TEXT[] := ARRAY[hash_operador, hash_gestor, hash_auditor, hash_administrador];

    lim_nome      INTEGER;
    lim_sobrenome INTEGER;
    lim_email     INTEGER;
    lim_perfil    INTEGER;
    lim_senha     INTEGER;
    valor  TEXT;
    campo  TEXT;
    custo  INTEGER;
    limite INTEGER;
    existentes    TEXT;
    preexistentes BIGINT;
    erros  TEXT[] := ARRAY[]::TEXT[];
    i      INTEGER;
BEGIN
    -- 1. Destino e estrutura esperada (instalada por 01_estrutura_inventaire.sql).
    IF current_database() <> 'inventaire' THEN
        RAISE EXCEPTION 'Conecte-se à base inventaire antes de executar este arquivo';
    END IF;
    IF to_regclass('public.usuarios') IS NULL OR to_regclass('public.produtos') IS NULL
       OR to_regclass('public.movimentacoes') IS NULL OR to_regclass('public.corredores') IS NULL
       OR to_regclass('public.posicoes_estoque') IS NULL OR to_regclass('public.itens_estoque') IS NULL
       OR to_regclass('public.contagens_inventario') IS NULL OR to_regclass('public.fornecedores') IS NULL THEN
        RAISE EXCEPTION 'Estrutura incompleta: execute antes 01_estrutura_inventaire.sql nesta base';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'usuarios_perfil_check'
                   AND conrelid = 'public.usuarios'::regclass)
       OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'usuarios_senha_bcrypt_check'
                      AND conrelid = 'public.usuarios'::regclass)
       OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'usuarios_email_key'
                      AND conrelid = 'public.usuarios'::regclass)
       OR NOT EXISTS (SELECT 1 FROM information_schema.columns
                      WHERE table_schema = 'public' AND table_name = 'usuarios' AND column_name = 'id_usuario'
                        AND is_identity = 'YES' AND identity_generation = 'ALWAYS')
       OR (SELECT count(*) FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'usuarios'
             AND (column_name::TEXT, data_type::TEXT, is_nullable::TEXT) IN (
                 ('nome', 'character varying', 'NO'), ('sobrenome', 'character varying', 'NO'),
                 ('email', 'character varying', 'NO'), ('senha', 'character varying', 'NO'),
                 ('perfil', 'character varying', 'NO'), ('ativo', 'boolean', 'NO'))) <> 6 THEN
        RAISE EXCEPTION 'Tabela usuarios diferente da estrutura esperada: inspecione a base, sem alterá-la';
    END IF;

    -- 2. Usuários já existentes são preservados (nenhum UPDATE/DELETE). Só os quatro
    --    e-mails deste arquivo não podem existir: nada é substituído.
    -- O lock impede inserções concorrentes entre esta conferência e os INSERTs.
    LOCK TABLE usuarios IN SHARE ROW EXCLUSIVE MODE;
    SELECT string_agg(email, ', ' ORDER BY email) INTO existentes
      FROM usuarios WHERE lower(email) = ANY (emails);
    IF existentes IS NOT NULL THEN
        RAISE EXCEPTION 'E-mail(s) já cadastrado(s): %. Nenhum usuário foi criado ou alterado', existentes;
    END IF;
    SELECT count(*) INTO preexistentes FROM usuarios;

    -- 3. Limites reais das colunas, lidos do catálogo.
    SELECT max(character_maximum_length) FILTER (WHERE column_name = 'nome'),
           max(character_maximum_length) FILTER (WHERE column_name = 'sobrenome'),
           max(character_maximum_length) FILTER (WHERE column_name = 'email'),
           max(character_maximum_length) FILTER (WHERE column_name = 'perfil'),
           max(character_maximum_length) FILTER (WHERE column_name = 'senha')
      INTO lim_nome, lim_sobrenome, lim_email, lim_perfil, lim_senha
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'usuarios';

    -- 4. Valida as QUATRO configurações antes de inserir qualquer uma.
    FOR i IN 1..4 LOOP
        FOREACH campo IN ARRAY ARRAY['nome', 'sobrenome'] LOOP
            valor := CASE campo WHEN 'nome' THEN nomes[i] ELSE sobrenomes[i] END;
            -- Fora da condição: em IF/ELSIF o PL/pgSQL corta a expressão no primeiro THEN (inclusive de CASE).
            limite := CASE campo WHEN 'nome' THEN lim_nome ELSE lim_sobrenome END;
            IF valor IS NULL OR valor !~ '[^[:space:]]' THEN
                erros := erros || format('%s: %s vazio', perfis[i], campo);
            ELSIF upper(valor) LIKE '%PREENCHER%' THEN
                erros := erros || format('%s: %s ainda não preenchido', perfis[i], campo);
            ELSIF valor <> btrim(valor) OR valor ~ '[[:cntrl:]]' THEN
                erros := erros || format('%s: %s com espaço nas pontas ou caractere de controle', perfis[i], campo);
            ELSIF char_length(valor) > limite THEN
                erros := erros || format('%s: %s excede %s caracteres', perfis[i], campo, limite);
            END IF;
        END LOOP;

        valor := hashes[i];
        IF valor IS NULL OR upper(valor) LIKE '%PREENCHER%' THEN
            erros := erros || format('%s: hash bcrypt ainda não preenchido', perfis[i]);
        -- Mesma expressão de usuarios_senha_bcrypt_check.
        ELSIF valor !~ '^\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$' OR char_length(valor) > lim_senha THEN
            erros := erros || format('%s: hash bcrypt inválido (esperado $2b$12$ + 53 caracteres)', perfis[i]);
        ELSE
            -- A biblioteca bcrypt da aplicação recusa custo fora de 04..31 (login falharia).
            custo := substr(valor, 5, 2)::INTEGER;
            IF custo < 4 OR custo > 31 THEN
                erros := erros || format('%s: custo bcrypt %s fora de 04..31', perfis[i], custo);
            END IF;
        END IF;

        IF char_length(emails[i]) > lim_email OR char_length(perfis[i]) > lim_perfil THEN
            erros := erros || format('%s: e-mail ou perfil excede o limite da tabela', perfis[i]);
        END IF;
    END LOOP;

    -- Hashes bcrypt têm salt aleatório: dois iguais indicam cópia/credencial compartilhada.
    IF (SELECT count(DISTINCT h) FROM unnest(hashes) AS h) <> 4 THEN
        erros := erros || 'hashes repetidos: gere um hash próprio para cada usuário'::TEXT;
    END IF;

    IF array_length(erros, 1) IS NOT NULL THEN
        RAISE EXCEPTION 'Configuração inválida; nenhum usuário foi inserido. Corrija: %',
            array_to_string(erros, '; ');
    END IF;

    -- 5. Inserção atômica (mesma transação). Sem id_usuario: identity ALWAYS gera os IDs.
    FOR i IN 1..4 LOOP
        INSERT INTO usuarios (nome, sobrenome, email, senha, perfil, ativo)
        VALUES (nomes[i], sobrenomes[i], emails[i], hashes[i], perfis[i], TRUE);
    END LOOP;

    -- 6. Conferência final: só os quatro novos foram acrescentados, ativos, um por perfil.
    IF (SELECT count(*) FROM usuarios) <> preexistentes + 4
       OR (SELECT count(DISTINCT perfil) FROM usuarios WHERE ativo AND lower(email) = ANY (emails)) <> 4 THEN
        RAISE EXCEPTION 'Conferência falhou: esperados 4 novos usuários ativos, um por perfil canônico';
    END IF;
END;
$$;

COMMIT;
