from repository.epi_repository import EpiRepository
from schemas.epi_dto import EpiDTO

class EpiService:
    def __init__(self, connection):
        self.epi_repository = EpiRepository(connection)

    def listar_epis(self) -> list[dict]:
        epis = self.epi_repository.get_epis()

        epis_lista: list[dict] = []

        if not epis:
            return []

        for epi in epis:
            epi_dict = {
                'id': epi.id,
                'nome': epi.nome,
                'categoria': epi.categoria,
                'certificado': epi.certificado,
                'validade': str(epi.validade) if epi.validade else None,
                'estoque': epi.estoque,
                'quantidade_min': epi.quantidade_min,
                'em_uso': epi.em_uso
            }

            epis_lista.append(epi_dict)

        return epis_lista

    def obter_epi_por_id(self, epi_id: int) -> dict | None:
        epi = self.epi_repository.get_epi_por_id(epi_id)

        if epi:
            return {
                'id': epi.id,
                'nome': epi.nome,
                'categoria': epi.categoria,
                'certificado': epi.certificado,
                'validade': str(epi.validade) if epi.validade else None,
                'estoque': epi.estoque,
                'quantidade_min': epi.quantidade_min,
                'em_uso': epi.em_uso
            }
        
        return None

    def registrar_epi(self, data: dict) -> dict | None:
        try:
            epi_dto = EpiDTO.from_dict(data)
        except ValueError as e:
            print(f"Erro ao criar EpiDTO: {e}")
            return None

        epi = self.epi_repository.registrar_epi(
            epi_dto.nome,
            epi_dto.categoria,
            epi_dto.certificado,
            epi_dto.validade,
            epi_dto.estoque,
            epi_dto.quantidade_min,
            epi_dto.em_uso
        )

        if epi:
            return {
                'id': epi.id,
                'nome': epi.nome,
                'categoria': epi.categoria,
                'certificado': epi.certificado,
                'validade': str(epi.validade) if epi.validade else None,
                'estoque': epi.estoque,
                'quantidade_min': epi.quantidade_min,
                'em_uso': epi.em_uso
            }

        return None

    def atualizar_epi(self, epi_id: int, data: dict) -> dict | None:
        try:
            epi_dto = EpiDTO.from_dict(data)
        except ValueError as e:
            print(f"Erro ao criar EpiDTO: {e}")
            return None

        epi = self.epi_repository.atualizar_epi(
            epi_id,
            epi_dto.nome,
            epi_dto.categoria,
            epi_dto.certificado,
            epi_dto.validade,
            epi_dto.estoque,
            epi_dto.quantidade_min,
            epi_dto.em_uso
        )

        if epi:
            return {
                'id': epi.id,
                'nome': epi.nome,
                'categoria': epi.categoria,
                'certificado': epi.certificado,
                'validade': str(epi.validade) if epi.validade else None,
                'estoque': epi.estoque,
                'quantidade_min': epi.quantidade_min,
                'em_uso': epi.em_uso
            }

        return None

    def deletar_epi(self, epi_id: int) -> bool:
        return self.epi_repository.deletar_epi(epi_id)
