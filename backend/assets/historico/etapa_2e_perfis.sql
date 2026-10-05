-- Inventaire / Etapa 2E — Perfis e permissões (RF09). Execução MANUAL, única e integral no pgAdmin.
-- Banco: inventaire, com a baseline 2D homologada. Não executar schemas completos sobre a baseline.
-- Normaliza SOMENTE os aliases aprovados (D6) e adiciona a constraint dos quatro perfis canônicos (D8).
-- Preserva IDs, senhas, dados cadastrais, atividade e todo o histórico. Não cria usuário nem senha.
-- Qualquer valor não previsto aborta tudo: nada é promovido, removido ou reclassificado em silêncio.
BEGIN;
SET LOCAL search_path = public;

-- Impede alterações concorrentes de usuários durante a verificação e a normalização.
LOCK TABLE usuarios IN SHARE ROW EXCLUSIVE MODE;

DO $$
DECLARE
    desconhecidos TEXT;
BEGIN
    IF current_database() <> 'inventaire' THEN
        RAISE EXCEPTION 'Selecione o banco inventaire antes de aplicar a Etapa 2E';
    END IF;
    IF to_regclass('public.usuarios') IS NULL OR to_regclass('public.produtos') IS NULL
       OR to_regclass('public.movimentacoes') IS NULL OR to_regclass('public.itens_estoque') IS NULL
       OR to_regclass('public.contagens_inventario') IS NULL THEN
        RAISE EXCEPTION 'A baseline da Etapa 2D precisa estar instalada';
    END IF;
    -- Sem IF NOT EXISTS: constraint preexistente é tratada como conflito, nunca mascarada.
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'usuarios_perfil_check'
               AND conrelid = 'public.usuarios'::regclass) THEN
        RAISE EXCEPTION 'Etapa 2E já aplicada ou constraint conflitante: não reaplique este arquivo';
    END IF;
    SELECT string_agg(DISTINCT quote_literal(perfil), ', ') INTO desconhecidos
    FROM usuarios
    WHERE perfil IS NULL OR lower(btrim(perfil)) NOT IN
        ('admin', 'administrador', 'supervisor', 'gestor', 'operador', 'auditor');
    IF desconhecidos IS NOT NULL THEN
        RAISE EXCEPTION 'Perfis não previstos exigem decisão manual antes da Etapa 2E: %', desconhecidos;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM usuarios WHERE ativo = TRUE
                   AND lower(btrim(perfil)) IN ('admin', 'administrador')) THEN
        RAISE EXCEPTION 'Nenhum ADMINISTRADOR ativo: a migração não será aplicada';
    END IF;
END;
$$;

-- Mapeamento aprovado (D6). Só toca linhas cujo valor ainda não é canônico.
UPDATE usuarios SET perfil = CASE lower(btrim(perfil))
        WHEN 'admin' THEN 'ADMINISTRADOR'
        WHEN 'administrador' THEN 'ADMINISTRADOR'
        WHEN 'supervisor' THEN 'GESTOR'
        WHEN 'gestor' THEN 'GESTOR'
        WHEN 'operador' THEN 'OPERADOR'
        WHEN 'auditor' THEN 'AUDITOR'
    END
WHERE perfil NOT IN ('OPERADOR', 'GESTOR', 'AUDITOR', 'ADMINISTRADOR');

ALTER TABLE usuarios ADD CONSTRAINT usuarios_perfil_check
    CHECK (perfil IN ('OPERADOR', 'GESTOR', 'AUDITOR', 'ADMINISTRADOR'));

COMMENT ON COLUMN usuarios.perfil IS
    'Perfil canônico (RF09): OPERADOR, GESTOR, AUDITOR ou ADMINISTRADOR. Matriz em backend/core/permissoes.py.';

-- Conferência final dentro da mesma transação.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM usuarios WHERE ativo = TRUE AND perfil = 'ADMINISTRADOR') THEN
        RAISE EXCEPTION 'Conferência falhou: nenhum ADMINISTRADOR ativo após a normalização';
    END IF;
END;
$$;

COMMIT;
