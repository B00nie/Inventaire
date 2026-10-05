-- Inventaire / Etapa 2D — Contagem de inventário (RF11). Execução MANUAL, única e integral no pgAdmin.
-- Banco: inventaire, com a baseline 2C homologada. Não executar schemas completos sobre a baseline.
-- Não migra, não altera e não cria dados: nenhuma contagem retroativa.
BEGIN;
SET LOCAL search_path = public;

DO $$
BEGIN
    IF current_database() <> 'inventaire' THEN
        RAISE EXCEPTION 'Selecione o banco inventaire antes de aplicar a Etapa 2D';
    END IF;
    IF current_setting('server_version_num')::INTEGER < 120000 THEN
        RAISE EXCEPTION 'A Etapa 2D exige PostgreSQL 12 ou superior (coluna gerada)';
    END IF;
    IF to_regclass('public.usuarios') IS NULL OR to_regclass('public.produtos') IS NULL
       OR to_regclass('public.movimentacoes') IS NULL OR to_regclass('public.corredores') IS NULL
       OR to_regclass('public.posicoes_estoque') IS NULL OR to_regclass('public.itens_estoque') IS NULL THEN
        RAISE EXCEPTION 'A baseline da Etapa 2C precisa estar instalada';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'itens_estoque_contexto_key'
                   AND conrelid = 'public.itens_estoque'::regclass)
       OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'movimentacoes_item_contexto_fk'
                      AND conrelid = 'public.movimentacoes'::regclass)
       OR (SELECT count(*) FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'movimentacoes'
             AND column_name IN ('id_item_estoque', 'quantidade_item_anterior', 'quantidade_item_posterior')) <> 3 THEN
        RAISE EXCEPTION 'Estrutura 2C incompleta: inspecione o banco antes de prosseguir';
    END IF;
    -- Sem IF NOT EXISTS: estrutura preexistente é tratada como conflito, nunca mascarada.
    IF to_regclass('public.contagens_inventario') IS NOT NULL
       OR EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'movimentacoes_contagem_ajuste_key') THEN
        RAISE EXCEPTION 'Etapa 2D já aplicada ou objeto conflitante: não reaplique este arquivo';
    END IF;
END;
$$;

-- Alvo da FK de vínculo. id_movimentacao já é PK: a unicidade é trivial e nenhum dado muda.
ALTER TABLE movimentacoes ADD CONSTRAINT movimentacoes_contagem_ajuste_key
    UNIQUE (id_movimentacao, id_item_estoque, tipo, quantidade_item_anterior, quantidade_item_posterior);

CREATE TABLE contagens_inventario (
    id_contagem INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id_item_estoque INTEGER NOT NULL,
    id_produto INTEGER NOT NULL,
    id_posicao INTEGER NOT NULL,
    lote TEXT NOT NULL,
    id_usuario INTEGER NOT NULL REFERENCES usuarios(id_usuario) ON DELETE RESTRICT,
    quantidade_sistema INTEGER NOT NULL CHECK (quantidade_sistema >= 0),
    quantidade_fisica INTEGER NOT NULL CHECK (quantidade_fisica >= 0),
    -- Ambos >= 0: a diferença cabe em INTEGER (-2147483647..2147483647).
    divergencia INTEGER GENERATED ALWAYS AS (quantidade_fisica - quantidade_sistema) STORED,
    status_item TEXT NOT NULL CHECK (status_item IN ('DISPONIVEL', 'RESERVADO', 'BLOQUEADO')),
    codigo_posicao TEXT NOT NULL CHECK (codigo_posicao ~ '[^[:space:]]'),
    corredor TEXT NOT NULL CHECK (corredor ~ '[^[:space:]]'),
    observacao TEXT CHECK (observacao IS NULL OR observacao ~ '[^[:space:]]'),
    id_ultima_movimentacao_item INTEGER REFERENCES movimentacoes(id_movimentacao) ON DELETE RESTRICT,
    data_hora TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    id_movimentacao_ajuste INTEGER,
    tipo_ajuste TEXT NOT NULL DEFAULT 'AJUSTE' CHECK (tipo_ajuste = 'AJUSTE'),
    CONSTRAINT contagens_inventario_item_contexto_fk
        FOREIGN KEY (id_item_estoque, id_produto, id_posicao, lote)
        REFERENCES itens_estoque(id_item_estoque, id_produto, id_posicao, lote) ON DELETE RESTRICT,
    -- Vínculo verificável: AJUSTE do mesmo item, do saldo sistêmico registrado ao físico contado.
    CONSTRAINT contagens_inventario_ajuste_fk
        FOREIGN KEY (id_movimentacao_ajuste, id_item_estoque, tipo_ajuste, quantidade_sistema, quantidade_fisica)
        REFERENCES movimentacoes(id_movimentacao, id_item_estoque, tipo, quantidade_item_anterior, quantidade_item_posterior)
        ON DELETE RESTRICT,
    CONSTRAINT contagens_inventario_ajuste_key UNIQUE (id_movimentacao_ajuste),
    CONSTRAINT contagens_inventario_ajuste_divergencia_check
        CHECK (id_movimentacao_ajuste IS NULL OR quantidade_fisica <> quantidade_sistema)
);

CREATE INDEX contagens_inventario_item_data_idx ON contagens_inventario (id_item_estoque, data_hora DESC, id_contagem DESC);
CREATE INDEX contagens_inventario_produto_data_idx ON contagens_inventario (id_produto, data_hora DESC, id_contagem DESC);
CREATE INDEX contagens_inventario_data_idx ON contagens_inventario (data_hora DESC, id_contagem DESC);
CREATE INDEX contagens_inventario_usuario_idx ON contagens_inventario (id_usuario);
CREATE INDEX contagens_inventario_ultima_mov_idx ON contagens_inventario (id_ultima_movimentacao_item);

COMMENT ON TABLE contagens_inventario IS
    'Contagens físicas por ItemEstoque (RF11). Sem edição/exclusão pela API; correção exige nova contagem.';
COMMENT ON COLUMN contagens_inventario.quantidade_sistema IS 'Saldo do item capturado pelo backend no registro (snapshot).';
COMMENT ON COLUMN contagens_inventario.id_ultima_movimentacao_item IS
    'Última movimentação do item no registro; aplicação exige o mesmo valor (detecta alterações intermediárias).';
COMMENT ON COLUMN contagens_inventario.id_movimentacao_ajuste IS 'AJUSTE gerado pela aplicação; NULL enquanto não aplicada.';
COMMENT ON COLUMN contagens_inventario.tipo_ajuste IS 'Constante usada pela FK de vínculo para exigir tipo AJUSTE.';

COMMIT;
