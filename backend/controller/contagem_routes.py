from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException

from core.auth import criar_autorizacao
from core.errors import AuthorizationError, BusinessError, NotFoundError, SchemaPendingError, ValidationError
from services.contagem_service import ContagemService



def create_contagem_bp(connection):
    bp = Blueprint('contagem_bp', __name__)
    service = ContagemService(connection)
    requer = criar_autorizacao(connection)

    @bp.errorhandler(Exception)
    def error_response(error):
        statuses = {ValidationError: 400, AuthorizationError: 403, NotFoundError: 404,
                    BusinessError: 409, SchemaPendingError: 503}
        if type(error) in statuses:
            return jsonify(message=str(error)), statuses[type(error)]
        if isinstance(error, HTTPException):
            return jsonify(message='Requisição inválida.'), 400 if error.code == 415 else error.code
        current_app.logger.error('Falha em contagens: %s', type(error).__name__)
        return jsonify(message='Erro interno do servidor'), 500

    @bp.get('/contagens')
    @requer('contagens:consultar')
    def listar():
        return jsonify(service.listar(session['user_id'], request.args.to_dict()))

    @bp.post('/contagens')
    @requer('contagens:registrar')
    def registrar():
        return jsonify(service.registrar(session['user_id'], request.get_json())), 201

    @bp.get('/contagens/<int:contagem_id>')
    @requer('contagens:consultar')
    def obter(contagem_id):
        return jsonify(service.obter(session['user_id'], contagem_id))

    @bp.post('/contagens/<int:contagem_id>/aplicar')
    @requer('contagens:aplicar')
    def aplicar(contagem_id):
        return jsonify(service.aplicar(contagem_id, session['user_id'], request.get_json())), 201

    @bp.get('/produtos/<int:produto_id>/contagens')
    @requer('contagens:consultar')
    def por_produto(produto_id):
        return jsonify(service.listar(session['user_id'], request.args.to_dict(), id_produto=produto_id))

    @bp.get('/itens-estoque/<int:item_id>/contagens')
    @requer('contagens:consultar')
    def por_item(item_id):
        return jsonify(service.listar(session['user_id'], request.args.to_dict(), id_item_estoque=item_id))

    return bp
