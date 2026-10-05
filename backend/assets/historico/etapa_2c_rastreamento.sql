-- Inventaire / Etapa 2C. Execução MANUAL, única e integral no pgAdmin.
-- Não executar schemas completos sobre a baseline. Nenhum dado é migrado.
BEGIN;
SET LOCAL search_path = public;

DO $$
BEGIN
    IF current_database() <> 'inventaire' THEN
        RAISE EXCEPTION 'Selecione o banco inventaire antes de aplicar a Etapa 2C';
    END IF;
    IF to_regclass('public.usuarios') IS NULL
       OR to_regclass('public.produtos') IS NULL
       OR to_regclass('public.movimentacoes') IS NULL THEN
        RAISE EXCEPTION 'A baseline da Etapa 2B precisa estar instalada';
    END IF;
END;
$$;

CREATE TABLE corredores (
    id_corredor INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    identificacao TEXT NOT NULL CHECK (identificacao ~ '[^[:space:]]' AND identificacao !~ '^[[:space:]]|[[:space:]]$'),
    capacidade_maxima INTEGER NOT NULL CHECK (capacidade_maxima > 0),
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT corredores_identificacao_key UNIQUE (identificacao)
);

CREATE TABLE posicoes_estoque (
    id_posicao INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo_posicao TEXT NOT NULL CHECK (codigo_posicao ~ '[^[:space:]]' AND codigo_posicao !~ '^[[:space:]]|[[:space:]]$'),
    id_corredor INTEGER NOT NULL REFERENCES corredores(id_corredor) ON DELETE RESTRICT,
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT posicoes_estoque_codigo_posicao_key UNIQUE (codigo_posicao)
);

CREATE TABLE itens_estoque (
    id_item_estoque INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id_produto INTEGER NOT NULL REFERENCES produtos(id_produto) ON DELETE RESTRICT,
    id_posicao INTEGER NOT NULL REFERENCES posicoes_estoque(id_posicao) ON DELETE RESTRICT,
    lote TEXT NOT NULL CHECK (lote ~ '[^[:space:]]' AND lote !~ '^[[:space:]]|[[:space:]]$'),
    quantidade INTEGER NOT NULL CHECK (quantidade >= 0),
    status TEXT NOT NULL DEFAULT 'DISPONIVEL' CHECK (status IN ('DISPONIVEL', 'RESERVADO', 'BLOQUEADO')),
    CONSTRAINT itens_estoque_produto_posicao_lote_key UNIQUE (id_produto, id_posicao, lote),
    -- Permite FK composta do histórico garantindo correspondência do item.
    CONSTRAINT itens_estoque_contexto_key UNIQUE (id_item_estoque, id_produto, id_posicao, lote)
);

CREATE INDEX posicoes_estoque_corredor_idx ON posicoes_estoque (id_corredor);
CREATE INDEX itens_estoque_posicao_idx ON itens_estoque (id_posicao);
CREATE INDEX itens_estoque_lote_idx ON itens_estoque (lote);

ALTER TABLE movimentacoes
    ADD COLUMN id_item_estoque INTEGER,
    ADD COLUMN id_posicao INTEGER REFERENCES posicoes_estoque(id_posicao) ON DELETE RESTRICT,
    ADD COLUMN lote TEXT,
    ADD COLUMN codigo_posicao TEXT,
    ADD COLUMN corredor TEXT,
    ADD COLUMN quantidade_item_anterior INTEGER,
    ADD COLUMN quantidade_item_posterior INTEGER,
    ADD CONSTRAINT movimentacoes_item_contexto_fk
        FOREIGN KEY (id_item_estoque, id_produto, id_posicao, lote)
        REFERENCES itens_estoque(id_item_estoque, id_produto, id_posicao, lote) ON DELETE RESTRICT,
    ADD CONSTRAINT movimentacoes_contexto_fisico_check CHECK (
        (id_item_estoque IS NULL AND id_posicao IS NULL AND lote IS NULL
         AND codigo_posicao IS NULL AND corredor IS NULL
         AND quantidade_item_anterior IS NULL AND quantidade_item_posterior IS NULL)
        OR
        (id_item_estoque IS NOT NULL AND id_posicao IS NOT NULL AND lote IS NOT NULL
         AND codigo_posicao IS NOT NULL AND corredor IS NOT NULL
         AND lote ~ '[^[:space:]]' AND codigo_posicao ~ '[^[:space:]]' AND corredor ~ '[^[:space:]]'
         AND quantidade_item_anterior IS NOT NULL AND quantidade_item_anterior >= 0
         AND quantidade_item_posterior IS NOT NULL AND quantidade_item_posterior >= 0
         AND estoque_posterior::BIGINT - estoque_anterior::BIGINT =
             quantidade_item_posterior::BIGINT - quantidade_item_anterior::BIGINT)
    );

CREATE INDEX movimentacoes_item_idx ON movimentacoes (id_item_estoque);
CREATE INDEX movimentacoes_posicao_idx ON movimentacoes (id_posicao);
COMMENT ON COLUMN movimentacoes.codigo_posicao IS 'Código da posição no instante da operação (snapshot).';
COMMENT ON COLUMN movimentacoes.corredor IS 'Identificação do corredor no instante da operação (snapshot).';
COMMENT ON COLUMN movimentacoes.quantidade_item_posterior IS 'Saldo alvo do lote no AJUSTE físico; quantidade mantém saldo global alvo da Etapa 2B.';

COMMIT;
