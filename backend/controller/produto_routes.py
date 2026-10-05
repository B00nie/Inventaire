from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException

from core.auth import criar_autorizacao
from core.errors import AuthorizationError, BusinessError, SchemaPendingError, ValidationError, NotFoundError
from services.produto_service import ProdutoService


def create_produto_bp(connection):
    produto_service = ProdutoService(connection)
    produto_bp = Blueprint('produto_bp', __name__)
    requer = criar_autorizacao(connection)

    @produto_bp.errorhandler(NotFoundError)
    def not_found(error):
        return jsonify({'message': str(error)}), 404

    @produto_bp.errorhandler(AuthorizationError)
    def unauthorized(error):
        return jsonify({'message': str(error)}), 403

    @produto_bp.errorhandler(SchemaPendingError)
    def pending_schema(error):
        return jsonify({'message': str(error)}), 503

    @produto_bp.errorhandler(ValidationError)
    def invalid_product(error):
        return jsonify({'message': str(error)}), 400

    @produto_bp.errorhandler(BusinessError)
    def conflict(error):
        return jsonify({'message': str(error)}), 409

    @produto_bp.errorhandler(Exception)
    def internal_error(error):
        if isinstance(error, HTTPException):
            status = 400 if error.code in (400, 415) else error.code
            return jsonify({'message': 'Requisição JSON inválida.'}), status
        # Não registrar parâmetros SQL, conteúdo de produtos ou credenciais.
        current_app.logger.error('Falha em produtos: %s', type(error).__name__)
        return jsonify({'message': 'Erro interno do servidor'}), 500

    @produto_bp.get('/produtos')
    @requer('produtos:consultar')
    def listar_produtos():
        return jsonify(produto_service.listar_produtos()), 200

    @produto_bp.get('/produtos/<int:produto_id>')
    @requer('produtos:consultar')
    def obter_produto_por_id(produto_id):
        produto = produto_service.obter_produto_por_id(produto_id)
        if produto is None:
            return jsonify({'message': 'Produto não encontrado'}), 404
        return jsonify(produto), 200

    @produto_bp.post('/produtos')
    @requer('produtos:gerenciar')
    def registrar_produto():
        return jsonify(produto_service.registrar_produto(request.get_json(), session['user_id'])), 201

    @produto_bp.put('/produtos/<int:produto_id>')
    @requer('produtos:gerenciar')
    def atualizar_produto(produto_id):
        produto = produto_service.atualizar_produto(produto_id, request.get_json(), session['user_id'])
        if produto is None:
            return jsonify({'message': 'Produto não encontrado'}), 404
        return jsonify(produto), 200

    @produto_bp.delete('/produtos/<int:produto_id>')
    @requer('produtos:gerenciar')
    def deletar_produto(produto_id):
        if not produto_service.deletar_produto(produto_id, session['user_id']):
            return jsonify({'message': 'Produto não encontrado'}), 404
        return jsonify({'message': 'Produto excluído com sucesso'}), 200

    return produto_bp
