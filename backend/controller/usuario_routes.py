from flask import Blueprint, current_app, jsonify, request, session
from services.usuario_service import UsuarioService
from schemas.usuario_dto import LoginDTO, SignupDTO
from core.auth import criar_autorizacao
from core.errors import AuthorizationError, BusinessError, NotFoundError, ValidationError
from core.permissoes import permissoes_de

STATUS = {ValidationError: 400, AuthorizationError: 403, NotFoundError: 404, BusinessError: 409}


def create_user_bp(connection):
    user_bp = Blueprint('user_bp', __name__)
    usuario_service = UsuarioService(connection)
    requer = criar_autorizacao(connection)

    def responder(operacao, sucesso=200):
        try:
            return jsonify(operacao()), sucesso
        except tuple(STATUS) as error:
            return jsonify({'message': str(error)}), STATUS[type(error)]
        except Exception as error:
            current_app.logger.error('Falha em usuários: %s', type(error).__name__)
            return jsonify({'message': 'Erro interno do servidor'}), 500


    @user_bp.route('/login', methods=['POST'])
    def login():
        data = request.get_json()

        try:
            login_dto = LoginDTO.from_dict(data)

            user = usuario_service.login(login_dto.email, login_dto.password)

            if user:
                session.clear()
                session.permanent = True # Define a sessão como permanente para respeitar o tempo de expiração configurado
                # Etapa 2E: apenas identidade. Perfil, admin e atividade são sempre
                # relidos do banco; nada na sessão concede permissão.
                session['user_id'] = user.id
                session['user_email'] = user.email
                session['user_nome'] = user.nome
                session['user_sobrenome'] = user.sobrenome
                session['user_acesso'] = str(user.acesso) if user.acesso else None
                current_app.session_interface.regenerate(session)

                dados = usuario_service.serializar(user)
                del dados['acesso']
                dados['permissoes'] = sorted(permissoes_de(user.perfil))
                return jsonify({'message': 'Login successful', 'user': dados}), 200
            else:
                return jsonify({'message': 'Invalid email or password'}), 401

        except ValidationError as e:
            return jsonify({'message': str(e)}), 400
        except Exception as e:
            print(f"Erro no login: {type(e).__name__}")
            return jsonify({'message': 'Erro interno do servidor'}), 500


    @user_bp.route('/logout', methods=['POST'])
    def logout():
        session.clear()
        return jsonify({'message': 'Logout successful'}), 200


    @user_bp.route('/session', methods=['GET'])
    def get_session():
        """
        Estado persistido do usuário da sessão (perfil canônico e permissões
        efetivas calculadas pelo servidor). Perfil desconhecido: autenticado, sem
        permissões. Inativo ou removido: sessão revogada.
        """
        if 'user_id' not in session:
            return jsonify({'message': 'Não autenticado'}), 401

        try:
            user = usuario_service.sessao(session['user_id'])
        except Exception as e:
            current_app.logger.error('Falha em sessão: %s', type(e).__name__)
            return jsonify({'message': 'Erro interno do servidor'}), 500

        if user is None:
            session.clear()
            return jsonify({'message': 'Usuário desativado'}), 403

        resposta = jsonify({'authenticated': True, 'user': user})
        resposta.headers['Cache-Control'] = 'no-store'  # nunca reapresentar a identidade de outra sessão
        return resposta, 200


    @user_bp.route('/me/perfil', methods=['PATCH'])
    @requer('perfil:editar_proprio')
    def atualizar_perfil_proprio():
        """Dados pessoais da PRÓPRIA conta. O alvo vem só da sessão. X-Inventaire-Usuario (opcional,
        sempre enviado pela tela) é o id que o formulário exibia: se a sessão do navegador trocou de
        conta, 409 e nada é gravado. Não é autorização: só impede aplicar dados antigos à nova conta."""
        esperado = request.headers.get('X-Inventaire-Usuario')
        if esperado is not None and esperado != str(session['user_id']):
            return jsonify({'message': 'A sessão deste navegador mudou de usuário. Recarregue a página '
                                       'e confira seus dados antes de salvar.'}), 409
        try:
            user = usuario_service.atualizar_perfil_proprio(session['user_id'], request.get_json(silent=True))
        except tuple(STATUS) as error:
            return jsonify({'message': str(error)}), STATUS[type(error)]
        except Exception as error:
            current_app.logger.error('Falha em perfil próprio: %s', type(error).__name__)
            return jsonify({'message': 'Erro interno do servidor'}), 500
        if user is None:
            session.clear()
            return jsonify({'message': 'Usuário inativo'}), 403
        # Identidade redundante da sessão acompanha o banco; a autorização continua pelo id.
        session['user_email'], session['user_nome'], session['user_sobrenome'] = (
            user['email'], user['nome'], user['sobrenome'])
        resposta = jsonify({'message': 'Perfil atualizado.', 'user': user})
        resposta.headers['Cache-Control'] = 'no-store'
        return resposta, 200


    @user_bp.route('/signup', methods=['POST'])
    @requer('usuarios:gerenciar')
    def signup():
        data = request.get_json()

        try:
            signup_dto = SignupDTO.from_dict(data)
            user = usuario_service.signup(
                signup_dto.email,
                signup_dto.password,
                signup_dto.nome,
                signup_dto.sobrenome,
                signup_dto.perfil,
                signup_dto.unidade,
                signup_dto.telefone,
            )

            if user:
                return jsonify({'message': 'Signup successful'}), 201
            else:
                return jsonify({'message': 'Email already exists'}), 400

        except ValidationError as e:
            return jsonify({'message': str(e)}), 400
        except Exception as e:
            print(f"Erro no signup: {type(e).__name__}")
            return jsonify({'message': 'Erro interno do servidor'}), 500


    @user_bp.route('/users', methods=['GET'])
    @requer('usuarios:gerenciar')
    def listar_usuarios():
        return responder(usuario_service.listar_usuarios)


    @user_bp.route('/users', methods=['PUT'])
    @requer('usuarios:gerenciar')
    def atualizar_usuario():
        data = request.get_json()
        return responder(lambda: usuario_service.atualizar_usuario(session['user_id'], data))


    @user_bp.route('/users/<int:usuario_id>/acesso', methods=['PATCH'])
    @requer('usuarios:gerenciar')
    def alterar_acesso(usuario_id):
        data = request.get_json()
        return responder(lambda: usuario_service.alterar_acesso(session['user_id'], usuario_id, data))


    @user_bp.route('/users/<int:usuario_id>', methods=['DELETE'])
    @requer('usuarios:gerenciar')
    def deletar_usuario(usuario_id):
        def excluir():
            if not usuario_service.deletar_usuario(session['user_id'], usuario_id):
                raise NotFoundError('Usuário não encontrado.')
            return {'message': 'Usuário deletado com sucesso'}
        return responder(excluir)

    @user_bp.route('/users/ativos', methods=['GET'])
    @requer('usuarios:gerenciar')
    def listar_usuarios_ativos():
        return responder(usuario_service.listar_usuarios_ativos)

    return user_bp
