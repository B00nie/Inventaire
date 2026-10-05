from core.auth import login_required, perfil_required
from flask import Blueprint, jsonify, request
from services.epi_service import EpiService

def create_epi_bp(connection):
    epi_service = EpiService(connection)
    epi_bp = Blueprint('epi_bp', __name__)

    @epi_bp.route('/epis', methods=['GET'])
    @login_required
    def listar_epis():
        epis = epi_service.listar_epis()
        return jsonify(epis), 200

    @epi_bp.route('/epis/<int:epi_id>', methods=['GET'])
    @login_required
    def obter_epi_por_id(epi_id):
        epi = epi_service.obter_epi_por_id(epi_id)

        if epi:
            return jsonify(epi), 200
        else:
            return jsonify({"error": "EPI não encontrado"}), 404

    @epi_bp.route('/epis', methods=['POST'])
    @perfil_required('admin', 'supervisor')
    def registrar_epi():
        data = request.get_json()
        epi = epi_service.registrar_epi(data)

        if epi:
            return jsonify(epi), 201
        else:
            return jsonify({"error": "Falha ao registrar o EPI"}), 400

    @epi_bp.route('/epis/<int:epi_id>', methods=['PUT'])
    @perfil_required('admin', 'supervisor')
    def atualizar_epi(epi_id):
        data = request.get_json()
        epi = epi_service.atualizar_epi(epi_id, data)

        if epi:
            return jsonify(epi), 200
        else:
            return jsonify({"error": "Falha ao atualizar o EPI"}), 400

    @epi_bp.route('/epis/<int:epi_id>', methods=['DELETE'])
    @perfil_required('admin', 'supervisor')
    def deletar_epi(epi_id):
        sucesso = epi_service.deletar_epi(epi_id)

        if sucesso:
            return jsonify({"message": "EPI deletado com sucesso"}), 200
        else:
            return jsonify({"error": "Falha ao deletar o EPI"}), 400
        
    return epi_bp