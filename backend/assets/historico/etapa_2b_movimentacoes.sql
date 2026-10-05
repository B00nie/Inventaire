-- Inventaire / Etapa 2B. Executar MANUALMENTE no pgAdmin, banco inventaire.
-- Incremental: preserva usuarios, produtos e todos os dados existentes.
-- Executar uma única vez, por inteiro. Não executar os schemas completos.
BEGIN;
SET LOCAL search_path = public;

DO $$
BEGIN
    IF current_database() <> 'inventaire' THEN
        RAISE EXCEPTION 'Selecione o banco inventaire antes de aplicar a Etapa 2B';
    END IF;
    IF to_regclass('public.usuarios') IS NULL OR to_regclass('public.produtos') IS NULL THEN
        RAISE EXCEPTION 'A base da Etapa 2A precisa estar instalada';
    END IF;
END;
$$;

CREATE TABLE movimentacoes (
    id_movimentacao INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id_produto INTEGER NOT NULL REFERENCES produtos(id_produto) ON DELETE RESTRICT,
    id_usuario INTEGER NOT NULL REFERENCES usuarios(id_usuario) ON DELETE RESTRICT,
    tipo TEXT NOT NULL CHECK (tipo IN ('ENTRADA', 'SAIDA', 'AJUSTE')),
    quantidade INTEGER NOT NULL,
    estoque_anterior INTEGER NOT NULL CHECK (estoque_anterior >= 0),
    estoque_posterior INTEGER NOT NULL CHECK (estoque_posterior >= 0),
    motivo TEXT,
    data_hora TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT movimentacoes_quantidade_check CHECK (
        (tipo IN ('ENTRADA', 'SAIDA') AND quantidade > 0)
        OR (tipo = 'AJUSTE' AND quantidade >= 0)
    ),
    CONSTRAINT movimentacoes_saldo_check CHECK (
        (tipo = 'ENTRADA' AND estoque_posterior::BIGINT = estoque_anterior::BIGINT + quantidade)
        OR (tipo = 'SAIDA' AND estoque_posterior::BIGINT = estoque_anterior::BIGINT - quantidade)
        OR (tipo = 'AJUSTE' AND estoque_posterior = quantidade)
    )
);

CREATE INDEX movimentacoes_produto_data_idx ON movimentacoes (id_produto, data_hora DESC, id_movimentacao DESC);
CREATE INDEX movimentacoes_usuario_idx ON movimentacoes (id_usuario);
CREATE INDEX movimentacoes_data_idx ON movimentacoes (data_hora DESC, id_movimentacao DESC);

COMMENT ON COLUMN movimentacoes.quantidade IS
    'Unidades em ENTRADA/SAIDA; saldo alvo em AJUSTE (payload novo_estoque).';
COMMENT ON TABLE movimentacoes IS
    'Historico de estoque, sem edicao/exclusao pela API. Correcao exige novo AJUSTE.';

COMMIT;
