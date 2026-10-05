from flask import current_app, jsonify, session
from functools import wraps

from core.permissoes import normalizar_perfil, pode

""" Módulo para verificar se há autenticação e autorização para rotas Flask. """

NEGADO = 'Acesso negado: você não tem permissão para acessar este recurso.'


def criar_autorizacao(connection):
    """
    Etapa 2E: decorator das rotas ativas. Ativo e perfil são lidos do banco a cada
    requisição; sessão, flag admin e payload nunca concedem permissão.
    Uso: requer = criar_autorizacao(connection); @requer('produtos:consultar')
    Várias permissões = basta uma (ex.: tipos de movimentação; o tipo exato é
    revalidado na transação). Etapa 2F: @requer.todas(...) exige todas
    (consultas que combinam dados de mais de um recurso).
    """
    from repository.usuario_repository import UsuarioRepository
    usuarios = UsuarioRepository(connection)

    def exigir(permissoes, todas):
        def decorator(view):
            @wraps(view)
            def decorated_function(*args, **kwargs):
                if 'user_id' not in session:
                    return jsonify({'message': 'Não autenticado'}), 401
                try:
                    acesso = usuarios.obter_acesso(session['user_id'])
                except Exception as error:
                    current_app.logger.error('Falha ao validar acesso: %s', type(error).__name__)
                    return jsonify({'message': 'Erro interno do servidor'}), 500
                if acesso is None or acesso[0] is not True:
                    # Usuário inativo ou removido: sessão revogada imediatamente.
                    session.clear()
                    return jsonify({'message': 'Usuário inativo'}), 403
                perfil = normalizar_perfil(acesso[1])
                if not (all if todas else any)(pode(perfil, permissao) for permissao in permissoes):
                    return jsonify({'message': NEGADO}), 403
                return view(*args, **kwargs)
            return decorated_function
        return decorator

    def requer(*permissoes):
        return exigir(permissoes, False)

    requer.todas = lambda *permissoes: exigir(permissoes, True)
    return requer


# Decorators legados (SPI) abaixo: usados apenas por módulos não registrados no app.
# Confiam na sessão; não usar em rotas ativas do Inventaire.

def login_required(view):
    """ Decorator para verificar se o usuário está autenticado e ativo antes de acessar a rota. """
    @wraps(view)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'message': 'Não autenticado'}), 401

        if session.get('user_ativo') is False:
            return jsonify({'message': 'Usuário inativo'}), 403
        
        return view(*args, **kwargs)

    return decorated_function

def perfil_required(*perfis_permitidos):
    """ 
        Decorator para verificar se o usuário possui privilégios necessários antes de acessar a rota. 
        Exemplos de uso:
            @perfil_required('admin', 'operador')
            @perfil_required('admin')
    """
    def decorator(view):
        @wraps(view)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return jsonify({'message': 'Não autenticado'}), 401

            if session.get('user_ativo') is False:
                return jsonify({'message': 'Usuário inativo'}), 403

            perfis_validos = []
            for perfil in perfis_permitidos:
                perfis_validos.append(perfil.strip().lower())

            user_perfil = session.get('user_perfil', '').strip().lower()

            is_admin = session.get('user_admin', False)
            if user_perfil not in perfis_validos and not is_admin:
                return jsonify({
                    'message': 'Acesso negado: você não tem permissão para acessar este recurso.'
                }), 403
            
            return view(*args, **kwargs)
        return decorated_function
    return decorator