from dataclasses import asdict

from repository.movimentacao_repository import MovimentacaoRepository
from schemas.movimentacao_dto import MovimentacaoDTO

# Etapa 2G: origem do recebimento no histórico (null em movimentações sem fornecedor e antes do SQL 2G).
CAMPOS_FORNECEDOR = ('id_fornecedor', 'fornecedor_razao_social', 'fornecedor_cnpj')


class MovimentacaoService:
    def __init__(self, connection):
        self.repository = MovimentacaoRepository(connection)

    @staticmethod
    def _serialize(movimento):
        result = asdict(movimento)
        result['data_hora'] = movimento.data_hora.isoformat()
        return result

    def registrar(self, produto_id, usuario_id, data):
        dto = MovimentacaoDTO.from_dict(data)
        return self._serialize(self.repository.registrar(produto_id, usuario_id, dto))

    def listar(self, usuario_id, produto_id=None):
        return [self._serialize(m) for m in self.repository.listar(usuario_id, produto_id)]

    def obter(self, movimento_id, usuario_id):
        return self._serialize(self.repository.obter(movimento_id, usuario_id))
