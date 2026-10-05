-- Inventaire / Etapa 2A. Executar somente em uma base nova, exclusiva e vazia,
-- após conferir host, porta, nome, schemas e objetos existentes por leitura.
-- Não é reset nem migração de dados. Reexecução falha sem apagar objetos.
-- Schema oficial, sincronizado com tabelas_spi-postgres.sql (Etapa 2A.1).
-- Aplicação manual no pgAdmin: escolher apenas um dos dois arquivos de schema.
BEGIN;
SET LOCAL search_path = public;

-- Defesa adicional: não instalar em bases reservadas/legadas ou com objetos
-- de aplicação. A confirmação humana do destino continua sendo necessária.
DO $$
BEGIN
    IF lower(current_database()) IN ('postgres', 'template0', 'template1')
       OR lower(current_database()) LIKE '%spi%' THEN
        RAISE EXCEPTION 'Base reservada ou legada: instalação interrompida';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_namespace
        WHERE nspname <> 'public' AND nspname <> 'information_schema'
          AND nspname NOT LIKE 'pg_%'
    ) OR EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
    ) OR EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
    ) OR EXISTS (
        SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public'
    ) THEN
        RAISE EXCEPTION 'Objetos de aplicação existentes: inspecione a base, sem alterá-la';
    END IF;
END;
$$;

CREATE TABLE usuarios (
    id_usuario INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nome       VARCHAR(40) NOT NULL,
    sobrenome  VARCHAR(90) NOT NULL,
    email      VARCHAR(60) NOT NULL,
    senha      VARCHAR(255) NOT NULL,
    perfil     VARCHAR(20) NOT NULL,
    unidade    VARCHAR(45),
    telefone   VARCHAR(45),
    ativo      BOOLEAN NOT NULL DEFAULT TRUE,
    acesso     TIMESTAMP,
    CONSTRAINT usuarios_email_key UNIQUE (email),
    -- Etapa 2E (RF09): perfis canônicos; matriz em backend/core/permissoes.py.
    CONSTRAINT usuarios_perfil_check
        CHECK (perfil IN ('OPERADOR', 'GESTOR', 'AUDITOR', 'ADMINISTRADOR')),
    CONSTRAINT usuarios_senha_bcrypt_check
        CHECK (senha ~ '^\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$')
);

CREATE TABLE produtos (
    id_produto     INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nome           TEXT NOT NULL CHECK (nome ~ '[^[:space:]]'),
    categoria      TEXT NOT NULL CHECK (categoria ~ '[^[:space:]]'),
    codigo         TEXT NOT NULL CHECK (codigo ~ '[^[:space:]]' AND codigo !~ '^[[:space:]]|[[:space:]]$'),
    localizacao    TEXT NOT NULL CHECK (localizacao ~ '[^[:space:]]'),
    validade       DATE,
    estoque        INTEGER NOT NULL DEFAULT 0 CHECK (estoque >= 0),
    quantidade_min INTEGER NOT NULL DEFAULT 0 CHECK (quantidade_min >= 0),
    CONSTRAINT produtos_codigo_key UNIQUE (codigo)
);

COMMENT ON COLUMN produtos.codigo IS 'SKU operacional fornecido pelo usuário, único e sensível a maiúsculas.';
COMMENT ON COLUMN produtos.localizacao IS 'Localização legada; RF04 usa itens_estoque, posicoes_estoque e corredores.';
COMMENT ON COLUMN produtos.estoque IS 'Saldo atual; alterado por movimentacoes transacionais na Etapa 2B.';

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


-- Etapa 2D (RF11): contagens de inventário. Exige PostgreSQL 12+ (coluna gerada).
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

-- Etapa 2G (RF12): fornecedores e origem opcional da ENTRADA (snapshot). Histórico nulo permitido.
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

COMMIT;
