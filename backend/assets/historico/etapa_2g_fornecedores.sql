-- Inventaire / Etapa 2G — Gestão de Fornecedores (RF12). Execução MANUAL, única e integral no pgAdmin.
-- Banco: inventaire, com a baseline 2E homologada (2F não tem SQL). Não executar schemas completos sobre a baseline.
-- Cria somente a tabela fornecedores e o vínculo opcional ENTRADA → fornecedor (com snapshot) em movimentacoes.
-- Não migra, não altera e não cria dados: nenhum fornecedor fictício, nenhuma origem retroativa.
-- Histórico existente permanece com fornecedor nulo. Recomenda-se parar o backend durante a aplicação.
BEGIN;
SET LOCAL search_path = public;

DO $$
BEGIN
    IF current_database() <> 'inventaire' THEN
        RAISE EXCEPTION 'Selecione o banco inventaire antes de aplicar a Etapa 2G';
    END IF;
    IF to_regclass('public.usuarios') IS NULL OR to_regclass('public.produtos') IS NULL
       OR to_regclass('public.movimentacoes') IS NULL OR to_regclass('public.corredores') IS NULL
       OR to_regclass('public.posicoes_estoque') IS NULL OR to_regclass('public.itens_estoque') IS NULL
       OR to_regclass('public.contagens_inventario') IS NULL THEN
        RAISE EXCEPTION 'A baseline das Etapas 2C/2D precisa estar instalada';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'usuarios_perfil_check'
                   AND conrelid = 'public.usuarios'::regclass)
       OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'movimentacoes_item_contexto_fk'
                      AND conrelid = 'public.movimentacoes'::regclass)
       OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'movimentacoes_contagem_ajuste_key'
                      AND conrelid = 'public.movimentacoes'::regclass)
       OR (SELECT count(*) FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'movimentacoes'
             AND column_name IN ('id_item_estoque', 'quantidade_item_anterior', 'quantidade_item_posterior')) <> 3 THEN
        RAISE EXCEPTION 'Baseline 2E incompleta (perfis canônicos, 2C ou 2D ausentes): inspecione o banco antes de prosseguir';
    END IF;
    -- Sem IF NOT EXISTS: estrutura preexistente (total ou parcial) é conflito, nunca mascarada.
    IF to_regclass('public.fornecedores') IS NOT NULL
       OR EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_schema = 'public' AND table_name = 'movimentacoes'
                    AND column_name IN ('id_fornecedor', 'fornecedor_razao_social', 'fornecedor_cnpj'))
       OR EXISTS (SELECT 1 FROM pg_constraint WHERE conname IN
                  ('fornecedores_cnpj_key', 'movimentacoes_fornecedor_fk', 'movimentacoes_fornecedor_check'))
       OR to_regclass('public.movimentacoes_fornecedor_idx') IS NOT NULL THEN
        RAISE EXCEPTION 'Etapa 2G já aplicada ou objeto conflitante: não reaplique este arquivo';
    END IF;
END;
$$;

CREATE TABLE fornecedores (
    id_fornecedor INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    razao_social TEXT NOT NULL CHECK (razao_social ~ '[^[:space:]]'
        AND razao_social !~ '^[[:space:]]|[[:space:]]$' AND char_length(razao_social) <= 200),
    -- D2 (opção A): CNPJ normalizado, apenas formato (aceita o alfanumérico). Não verifica dígitos
    -- verificadores, existência nem situação cadastral.
    cnpj TEXT NOT NULL CHECK (cnpj ~ '^[0-9A-Z]{12}[0-9]{2}$'),
    contato TEXT CHECK (contato IS NULL OR (contato ~ '[^[:space:]]'
        AND contato !~ '^[[:space:]]|[[:space:]]$' AND char_length(contato) <= 200)),
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT fornecedores_cnpj_key UNIQUE (cnpj)
);

COMMENT ON TABLE fornecedores IS
    'RF12. Inativação em vez de exclusão quando referenciado (FK RESTRICT em movimentacoes).';
COMMENT ON COLUMN fornecedores.cnpj IS
    'Normalizado (14 caracteres). Validação de formato; não verifica dígitos verificadores, existência ou situação cadastral.';

-- Colunas nulas, sem default: alteração só de catálogo; histórico existente fica com fornecedor nulo.
ALTER TABLE movimentacoes
    ADD COLUMN id_fornecedor INTEGER,
    ADD COLUMN fornecedor_razao_social TEXT,
    ADD COLUMN fornecedor_cnpj TEXT;

ALTER TABLE movimentacoes ADD CONSTRAINT movimentacoes_fornecedor_fk
    FOREIGN KEY (id_fornecedor) REFERENCES fornecedores(id_fornecedor) ON DELETE RESTRICT;

-- Origem só em ENTRADA; contexto totalmente nulo ou totalmente preenchido (snapshot do servidor).
ALTER TABLE movimentacoes ADD CONSTRAINT movimentacoes_fornecedor_check CHECK (
    (id_fornecedor IS NULL AND fornecedor_razao_social IS NULL AND fornecedor_cnpj IS NULL)
    OR (tipo = 'ENTRADA' AND id_fornecedor IS NOT NULL
        AND fornecedor_razao_social IS NOT NULL AND fornecedor_cnpj IS NOT NULL)
);

-- Suporte à FK RESTRICT (exclusão de fornecedor) e à consulta de recebimentos por fornecedor.
CREATE INDEX movimentacoes_fornecedor_idx
    ON movimentacoes (id_fornecedor, data_hora DESC, id_movimentacao DESC);

COMMENT ON COLUMN movimentacoes.fornecedor_razao_social IS
    'Snapshot da razão social no instante da ENTRADA; editar o fornecedor não reescreve o histórico.';
COMMENT ON COLUMN movimentacoes.fornecedor_cnpj IS
    'Snapshot do CNPJ normalizado no instante da ENTRADA.';

-- Conferência final dentro da mesma transação: nenhuma origem atribuída retroativamente.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM movimentacoes WHERE id_fornecedor IS NOT NULL) OR EXISTS (SELECT 1 FROM fornecedores) THEN
        RAISE EXCEPTION 'Conferência falhou: a Etapa 2G não deve criar fornecedores nem vínculos';
    END IF;
END;
$$;

COMMIT;
