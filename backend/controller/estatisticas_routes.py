from services.estatisticas_service import EstatisticasService
from flask import Blueprint, jsonify, request
from core.auth import login_required


def create_estatisticas_bp(connection):
    estatisticas_service = EstatisticasService(connection)
    estatisticas_bp = Blueprint('estatisticas_bp', __name__)

    @estatisticas_bp.route('/estatisticas/conformidade', methods=['GET'])
    @login_required
    def obter_estatisticas_conformidade():
        try:
            estatisticas = estatisticas_service.obter_estatisticas_conformidade(request.args)

            return jsonify(estatisticas), 200

        except ValueError as e:
            return jsonify({'message': str(e)}), 400

        except Exception as e:
            print(f"Erro ao obter estatísticas de conformidade: {e}")
            return jsonify({'message': 'Erro interno do servidor'}), 500

    @estatisticas_bp.route('/estatisticas/setor/<int:setor_id>', methods=['GET'])
    @login_required
    def obter_estatisticas_por_setor(setor_id):
        try:
            estatisticas = estatisticas_service.obter_estatisticas_por_setor(setor_id, request.args)

            return jsonify(estatisticas), 200

        except ValueError as e:
            return jsonify({'message': str(e)}), 400

        except Exception as e:
            print(f"Erro ao obter estatísticas por setor: {e}")
            return jsonify({'message': 'Erro interno do servidor'}), 500

    @estatisticas_bp.route('/estatisticas/conformes', methods=['GET'])
    @login_required
    def obter_quantidade_conformes():
        try:
            quantidade = estatisticas_service.obter_quantidade_conformes()

            return jsonify(quantidade), 200

        except ValueError as e:
            return jsonify({'message': str(e)}), 400

        except Exception as e:
            print(f"Erro ao obter estatísticas de conformes: {e}")
            return jsonify({'message': 'Erro interno do servidor'}), 500

    return estatisticas_bp