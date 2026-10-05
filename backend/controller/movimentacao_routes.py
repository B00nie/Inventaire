from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException

from core.auth import criar_autorizacao
from core.permissoes import MOVIMENTACOES
from core.errors import AuthorizationError, BusinessError, NotFoundError, SchemaPendingError, ValidationError
from services.movimentacao_service import MovimentacaoService


def create_movimentacao_bp(connection):
    bp = Blueprint('movimentacao_bp', __name__)
    service = MovimentacaoService(connection)
    requer = criar_autorizacao(connection)

    @bp.errorhandler(Exception)
    def error_response(error):
        statuses = {ValidationError: 400, AuthorizationError: 403, NotFoundError: 404,
                    BusinessError: 409, SchemaPendingError: 503}
        if type(error) in statuses:
            return jsonify(message=str(error)), statuses[type(error)]
        if isinstance(error, HTTPException):
            return jsonify(message='Requisição JSON inválida.'), 400 if error.code == 415 else error.code
        current_app.logger.error('Falha em movimentações: %s', type(error).__name__)
        return jsonify(message='Erro interno do servidor'), 500

    @bp.get('/movimentacoes')
    @requer('movimentacoes:consultar')
    def listar():
        return jsonify(service.listar(session['user_id']))

    @bp.get('/movimentacoes/<int:movimento_id>')
    @requer('movimentacoes:consultar')
    def obter(movimento_id):
        return jsonify(service.obter(movimento_id, session['user_id']))

    @bp.get('/produtos/<int:produto_id>/movimentacoes')
    @requer('movimentacoes:consultar')
    def historico(produto_id):
        return jsonify(service.listar(session['user_id'], produto_id))

    @bp.post('/produtos/<int:produto_id>/movimentacoes')
    # Basta um tipo permitido aqui; o tipo exato do payload é revalidado na transação.
    @requer(*MOVIMENTACOES)
    def registrar(produto_id):
        return jsonify(service.registrar(produto_id, session['user_id'], request.get_json())), 201

    return bp
