from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException
from core.auth import criar_autorizacao
from core.errors import AuthorizationError, BusinessError, NotFoundError, SchemaPendingError, ValidationError
from services.rastreamento_service import CorredorService, PosicaoService, ItemEstoqueService


def create_rastreamento_bp(connection):
    bp = Blueprint('rastreamento_bp', __name__)
    corredores, posicoes, itens = CorredorService(connection), PosicaoService(connection), ItemEstoqueService(connection)
    requer = criar_autorizacao(connection)

    @bp.errorhandler(Exception)
    def error_response(error):
        statuses = {ValidationError: 400, AuthorizationError: 403, NotFoundError: 404,
                    BusinessError: 409, SchemaPendingError: 503}
        if type(error) in statuses:
            return jsonify(message=str(error)), statuses[type(error)]
        if isinstance(error, HTTPException):
            return jsonify(message='Requisição inválida.'), 400 if error.code == 415 else error.code
        current_app.logger.error('Falha em rastreamento: %s', type(error).__name__)
        return jsonify(message='Erro interno do servidor'), 500

    @bp.get('/corredores')
    @requer('localizacoes:consultar')
    def listar_corredores():
        return jsonify(corredores.listar(session['user_id']))

    @bp.get('/corredores/<int:id>')
    @requer('localizacoes:consultar')
    def obter_corredor(id):
        return jsonify(corredores.obter(session['user_id'], id))

    @bp.post('/corredores')
    @requer('localizacoes:gerenciar')
    def criar_corredor():
        return jsonify(corredores.salvar(session['user_id'], request.get_json())), 201

    @bp.put('/corredores/<int:id>')
    @requer('localizacoes:gerenciar')
    def editar_corredor(id):
        return jsonify(corredores.salvar(session['user_id'], request.get_json(), id))

    @bp.get('/posicoes')
    @requer('localizacoes:consultar')
    def listar_posicoes():
        return jsonify(posicoes.listar(session['user_id']))

    @bp.get('/posicoes/<int:id>')
    @requer('localizacoes:consultar')
    def obter_posicao(id):
        return jsonify(posicoes.obter(session['user_id'], id))

    @bp.get('/corredores/<int:id>/posicoes')
    @requer('localizacoes:consultar')
    def posicoes_corredor(id):
        return jsonify(posicoes.listar(session['user_id'], id))

    @bp.post('/posicoes')
    @requer('localizacoes:gerenciar')
    def criar_posicao():
        return jsonify(posicoes.salvar(session['user_id'], request.get_json())), 201

    @bp.put('/posicoes/<int:id>')
    @requer('localizacoes:gerenciar')
    def editar_posicao(id):
        return jsonify(posicoes.salvar(session['user_id'], request.get_json(), id))

    @bp.get('/itens-estoque')
    @requer('localizacoes:consultar')
    def listar_itens():
        return jsonify(itens.listar(session['user_id'], request.args.to_dict()))

    @bp.get('/itens-estoque/<int:id>')
    @requer('localizacoes:consultar')
    def obter_item(id):
        return jsonify(itens.obter(session['user_id'], id))

    @bp.get('/produtos/<int:id>/itens-estoque')
    @requer('localizacoes:consultar')
    def itens_produto(id):
        return jsonify(itens.listar(session['user_id'], request.args.to_dict(), id_produto=id))

    @bp.get('/posicoes/<int:id>/itens-estoque')
    @requer('localizacoes:consultar')
    def itens_posicao(id):
        return jsonify(itens.listar(session['user_id'], request.args.to_dict(), id_posicao=id))

    return bp
