from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException

from core.auth import criar_autorizacao
from core.errors import AuthorizationError, BusinessError, NotFoundError, SchemaPendingError, ValidationError
from services.relatorios_service import RelatoriosService


def create_relatorio_bp(connection):
    """Etapa 2F: somente GET. Todas as permissões listadas são exigidas (requer.todas).
    Correção de acessos: /relatorios/* (inclusive /exportacao) exigem também relatorios:consultar;
    /dashboard/resumo não (o OPERADOR mantém o Dashboard)."""
    bp = Blueprint('relatorio_bp', __name__)
    service = RelatoriosService(connection)
    requer = criar_autorizacao(connection)

    @bp.errorhandler(Exception)
    def error_response(error):
        statuses = {ValidationError: 400, AuthorizationError: 403, NotFoundError: 404,
                    BusinessError: 409, SchemaPendingError: 503}
        if type(error) in statuses:
            return jsonify(message=str(error)), statuses[type(error)]
        if isinstance(error, HTTPException):
            return jsonify(message='Requisição inválida.'), error.code
        current_app.logger.error('Falha em relatórios: %s', type(error).__name__)
        return jsonify(message='Erro interno do servidor'), 500

    def args():
        # flat=False: parâmetros repetidos chegam como lista e são rejeitados pelo DTO.
        return request.args.to_dict(flat=False)

    @bp.get('/dashboard/resumo')
    @requer.todas('produtos:consultar', 'movimentacoes:consultar', 'contagens:consultar')
    def dashboard():
        return jsonify(service.dashboard(session['user_id'], args()))

    @bp.get('/relatorios/posicao-estoque')
    @requer.todas('relatorios:consultar', 'produtos:consultar', 'localizacoes:consultar')
    def posicao_estoque():
        return jsonify(service.posicao_estoque(session['user_id'], args()))

    @bp.get('/relatorios/estoque-baixo')
    @requer.todas('relatorios:consultar', 'produtos:consultar')
    def estoque_baixo():
        return jsonify(service.estoque_baixo(session['user_id'], args()))

    @bp.get('/relatorios/movimentacoes')
    @requer.todas('relatorios:consultar', 'movimentacoes:consultar')
    def movimentacoes():
        return jsonify(service.movimentacoes(session['user_id'], args()))

    @bp.get('/relatorios/divergencias')
    @requer.todas('relatorios:consultar', 'contagens:consultar')
    def divergencias():
        return jsonify(service.divergencias(session['user_id'], args()))

    @bp.get('/relatorios/saidas-periodo')
    @requer.todas('relatorios:consultar', 'movimentacoes:consultar')
    def saidas_periodo():
        return jsonify(service.saidas_periodo(session['user_id'], args()))

    # Exportação CSV (2F.2): uma requisição, uma transação REPEATABLE READ READ ONLY, até 1.000 linhas.
    # Mesmas permissões (todas exigidas) e mesmos filtros da consulta; page/page_size são recusados (400).
    def exportacao(rota, metodo, *permissoes):
        @requer.todas('relatorios:consultar', *permissoes)
        def view():
            return jsonify(getattr(service, metodo)(session['user_id'], args(), exportacao=True))
        bp.add_url_rule(f'/relatorios/{rota}/exportacao', f'exportar_{metodo}', view, methods=['GET'])

    exportacao('posicao-estoque', 'posicao_estoque', 'produtos:consultar', 'localizacoes:consultar')
    exportacao('estoque-baixo', 'estoque_baixo', 'produtos:consultar')
    exportacao('movimentacoes', 'movimentacoes', 'movimentacoes:consultar')
    exportacao('divergencias', 'divergencias', 'contagens:consultar')
    exportacao('saidas-periodo', 'saidas_periodo', 'movimentacoes:consultar')

    return bp
