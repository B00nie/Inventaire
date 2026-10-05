"""RF09 — fonte única da matriz de permissões aprovada na Etapa 2E.

Controllers, repositories, /session e frontend consomem somente este módulo.
Perfil desconhecido não recebe nenhuma permissão funcional.
"""

PERFIS = ('OPERADOR', 'GESTOR', 'AUDITOR', 'ADMINISTRADOR')

# Aliases legados aprovados (D6). Valores canônicos também são aceitos.
ALIASES = {'admin': 'ADMINISTRADOR', 'administrador': 'ADMINISTRADOR', 'supervisor': 'GESTOR',
           'gestor': 'GESTOR', 'operador': 'OPERADOR', 'auditor': 'AUDITOR'}

# Etapa 2G (ampliação aprovada): fornecedores:gerenciar para GESTOR e ADMINISTRADOR.
# Correção de perfil/acessos (pedido posterior do usuário, 05/10/2026), que substitui a 2G neste ponto:
# - OPERADOR perde fornecedores:consultar e não recebe relatorios:consultar (páginas Fornecedores e
#   Relatórios negadas); mantém as consultas operacionais e o Dashboard;
# - fornecedores:selecionar (opção B aprovada): só a lista mínima de ativos para a ENTRADA;
# - perfil:editar_proprio: nome, sobrenome, e-mail e telefone da própria conta (PATCH /me/perfil).
OPERACIONAL = frozenset({'produtos:consultar', 'localizacoes:consultar', 'movimentacoes:consultar',
                         'contagens:consultar', 'contagens:registrar', 'perfil:editar_proprio'})
CONSULTA = OPERACIONAL | {'fornecedores:consultar', 'relatorios:consultar'}
_GESTOR = CONSULTA | {'produtos:gerenciar', 'localizacoes:gerenciar', 'movimentacoes:entrada',
                      'movimentacoes:saida', 'movimentacoes:ajuste', 'contagens:aplicar',
                      'fornecedores:gerenciar', 'fornecedores:selecionar'}
PERMISSOES = {
    'OPERADOR': OPERACIONAL | {'movimentacoes:entrada', 'movimentacoes:saida', 'fornecedores:selecionar'},
    'AUDITOR': CONSULTA,
    'GESTOR': _GESTOR,
    'ADMINISTRADOR': _GESTOR | {'usuarios:gerenciar'},
}
MOVIMENTACOES = ('movimentacoes:entrada', 'movimentacoes:saida', 'movimentacoes:ajuste')


def normalizar_perfil(valor):
    """Perfil canônico ou None. Sem fallback: valores não previstos são negados."""
    if not isinstance(valor, str):
        return None
    return ALIASES.get(valor.strip().lower())


def permissoes_de(perfil):
    return PERMISSOES.get(normalizar_perfil(perfil), frozenset())


def pode(perfil, permissao):
    return permissao in permissoes_de(perfil)


def permissao_movimentacao(tipo):
    return 'movimentacoes:' + tipo.lower()


# Usado no SQL de lock dos administradores (comparação lower/btrim, como normalizar_perfil).
ALIASES_ADMINISTRADOR = tuple(sorted(k for k, v in ALIASES.items() if v == 'ADMINISTRADOR'))
