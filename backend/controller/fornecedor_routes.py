from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException

from core.auth import criar_autorizacao
from core.errors import AuthorizationError, BusinessError, NotFoundError, SchemaPendingError, ValidationError
from services.fornecedor_service import FornecedorService


def create_fornecedor_bp(connection):
    """Etapa 2G (RF12). Gestão GESTOR/ADMINISTRADOR. Correção de acessos: consulta sem o OPERADOR,
    que usa só /fornecedores/selecao-entrada (fornecedores:selecionar)."""
    bp = Blueprint('fornecedor_bp', __name__)
    service = FornecedorService(connection)
    requer = criar_autorizacao(connection)

    @bp.errorhandler(Exception)
    def error_response(error):
        statuses = {ValidationError: 400, AuthorizationError: 403, NotFoundError: 404,
                    BusinessError: 409, SchemaPendingError: 503}
        if type(error) in statuses:
            return jsonify(message=str(error)), statuses[type(error)]
        if isinstance(error, HTTPException):
            return jsonify(message='Requisição inválida.'), 400 if error.code == 415 else error.code
        current_app.logger.error('Falha em fornecedores: %s', type(error).__name__)
        return jsonify(message='Erro interno do servidor'), 500

    @bp.get('/fornecedores')
    @requer('fornecedores:consultar')
    def listar():
        return jsonify(service.listar(session['user_id'], request.args.to_dict(flat=False)))

    # Opção B aprovada: seletor da ENTRADA (inclusive OPERADOR, que não tem fornecedores:consultar).
    @bp.get('/fornecedores/selecao-entrada')
    @requer.todas('fornecedores:selecionar', 'movimentacoes:entrada')
    def selecao_entrada():
        return jsonify(service.selecao_entrada(session['user_id'], request.args.to_dict(flat=False)))

    @bp.get('/fornecedores/<int:fornecedor_id>')
    @requer('fornecedores:consultar')
    def obter(fornecedor_id):
        return jsonify(service.obter(session['user_id'], fornecedor_id))

    @bp.post('/fornecedores')
    @requer('fornecedores:gerenciar')
    def criar():
        return jsonify(service.criar(session['user_id'], request.get_json())), 201

    @bp.put('/fornecedores/<int:fornecedor_id>')
    @requer('fornecedores:gerenciar')
    def atualizar(fornecedor_id):
        return jsonify(service.atualizar(session['user_id'], fornecedor_id, request.get_json()))

    @bp.delete('/fornecedores/<int:fornecedor_id>')
    @requer('fornecedores:gerenciar')
    def excluir(fornecedor_id):
        service.excluir(session['user_id'], fornecedor_id)
        return jsonify(message='Fornecedor excluído.')

    @bp.get('/fornecedores/<int:fornecedor_id>/recebimentos')
    @requer.todas('fornecedores:consultar', 'movimentacoes:consultar')
    def recebimentos(fornecedor_id):
        return jsonify(service.recebimentos(session['user_id'], fornecedor_id, request.args.to_dict(flat=False)))

    return bp
