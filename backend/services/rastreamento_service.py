from dataclasses import asdict, replace
from repository.rastreamento_repository import CorredorRepository, PosicaoRepository, ItemEstoqueRepository
from schemas.rastreamento_dto import CorredorDTO, PosicaoDTO, PesquisaItensDTO


class CorredorService:
    def __init__(self, connection):
        self.repository = CorredorRepository(connection)

    def listar(self, usuario_id):
        return [asdict(r) for r in self.repository.listar(usuario_id)]

    def obter(self, usuario_id, corredor_id):
        return asdict(self.repository.listar(usuario_id, corredor_id)[0])

    def salvar(self, usuario_id, data, corredor_id=None):
        return asdict(self.repository.salvar(usuario_id, CorredorDTO.from_dict(data), corredor_id))


class PosicaoService:
    def __init__(self, connection):
        self.repository = PosicaoRepository(connection)

    def listar(self, usuario_id, corredor_id=None):
        return [asdict(r) for r in self.repository.listar(usuario_id, corredor_id=corredor_id)]

    def obter(self, usuario_id, posicao_id):
        return asdict(self.repository.listar(usuario_id, posicao_id=posicao_id)[0])

    def salvar(self, usuario_id, data, posicao_id=None):
        return asdict(self.repository.salvar(usuario_id, PosicaoDTO.from_dict(data), posicao_id))


class ItemEstoqueService:
    def __init__(self, connection):
        self.repository = ItemEstoqueRepository(connection)

    def listar(self, usuario_id, params, **parent):
        pesquisa = replace(PesquisaItensDTO.from_dict(params), **parent)
        return [asdict(r) for r in self.repository.listar(usuario_id, pesquisa)]

    def obter(self, usuario_id, item_id):
        return asdict(self.repository.listar(usuario_id, PesquisaItensDTO(), item_id)[0])

