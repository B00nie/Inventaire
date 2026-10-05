from dataclasses import asdict, replace

from repository.contagem_repository import ContagemRepository
from schemas.contagem_dto import AplicacaoContagemDTO, ContagemDTO, PesquisaContagensDTO
from services.movimentacao_service import MovimentacaoService


class ContagemService:
    def __init__(self, connection):
        self.repository = ContagemRepository(connection)

    @staticmethod
    def _serialize(contagem):
        result = asdict(contagem)
        result['data_hora'] = contagem.data_hora.isoformat()
        result['aplicada_em'] = contagem.aplicada_em.isoformat() if contagem.aplicada_em else None
        # Estado derivado do vínculo; não existe coluna de estado que possa divergir.
        result['situacao'] = ('APLICADA' if contagem.id_movimentacao_ajuste is not None else
                              'SEM_DIVERGENCIA' if contagem.divergencia == 0 else 'PENDENTE')
        return result

    def registrar(self, usuario_id, data):
        return self._serialize(self.repository.registrar(usuario_id, ContagemDTO.from_dict(data)))

    def listar(self, usuario_id, params, **parent):
        # Pai da rota prevalece sobre a query string.
        pesquisa = replace(PesquisaContagensDTO.from_dict(params), **parent)
        return [self._serialize(c) for c in self.repository.listar(usuario_id, pesquisa)]

    def obter(self, usuario_id, contagem_id):
        return self._serialize(self.repository.listar(usuario_id, PesquisaContagensDTO(), contagem_id)[0])

    def aplicar(self, contagem_id, usuario_id, data):
        contagem, movimento = self.repository.aplicar(contagem_id, usuario_id, AplicacaoContagemDTO.from_dict(data))
        return {'contagem': self._serialize(contagem), 'movimentacao': MovimentacaoService._serialize(movimento)}
