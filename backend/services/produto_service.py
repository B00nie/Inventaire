from dataclasses import asdict

from models.produtos import Produto
from repository.produto_repository import ProdutoRepository
from schemas.produto_dto import ProdutoDTO


class ProdutoService:
    def __init__(self, connection):
        self.produto_repository = ProdutoRepository(connection)

    @staticmethod
    def _serialize(produto: Produto | None) -> dict | None:
        if produto is None:
            return None
        result = asdict(produto)
        result['validade'] = produto.validade.isoformat() if produto.validade else None
        return result

    def listar_produtos(self) -> list[dict]:
        return [self._serialize(produto) for produto in self.produto_repository.get_produtos()]

    def obter_produto_por_id(self, produto_id: int) -> dict | None:
        return self._serialize(self.produto_repository.get_produto_por_id(produto_id))

    def registrar_produto(self, data: dict, usuario_id: int) -> dict:
        dto = ProdutoDTO.from_dict(data)
        fields = asdict(dto)
        if dto.id_posicao is None:
            fields.pop('id_posicao')
            fields.pop('lote')
        if dto.id_fornecedor is None:
            fields.pop('id_fornecedor')
        return self._serialize(self.produto_repository.registrar_produto(**fields, usuario_id=usuario_id))

    def atualizar_produto(self, produto_id: int, data: dict, usuario_id: int) -> dict | None:
        dto = ProdutoDTO.from_dict(data, updating=True)
        fields = asdict(dto)
        fields.pop('estoque')
        fields.pop('id_posicao')
        fields.pop('lote')
        fields.pop('id_fornecedor')
        return self._serialize(self.produto_repository.atualizar_produto(produto_id, **fields, usuario_id=usuario_id))

    def deletar_produto(self, produto_id: int, usuario_id: int) -> bool:
        return self.produto_repository.deletar_produto(produto_id, usuario_id=usuario_id)
