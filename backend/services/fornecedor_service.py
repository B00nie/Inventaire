from dataclasses import asdict

from repository.fornecedor_repository import FornecedorRepository
from schemas.fornecedor_dto import SEPARADORES, FornecedorDTO, PaginaDTO, PesquisaFornecedoresDTO, formatar_cnpj
from schemas.relatorios_dto import parametros
from services.movimentacao_service import MovimentacaoService

LIMITE_SELECAO = 100
PERMISSOES_SELECAO = ('fornecedores:selecionar', 'movimentacoes:entrada')


def _pagina(itens, total, filtro):
    return dict(itens=itens, total=total, page=filtro.page, page_size=filtro.page_size,
                paginas=(total + filtro.page_size - 1) // filtro.page_size)


class FornecedorService:
    def __init__(self, connection):
        self.repository = FornecedorRepository(connection)

    @staticmethod
    def _serialize(fornecedor):
        result = asdict(fornecedor)
        result['cnpj_formatado'] = formatar_cnpj(fornecedor.cnpj)
        return result

    def listar(self, usuario_id, args):
        pesquisa = PesquisaFornecedoresDTO.from_args(args)
        # CNPJ é comparado sem separadores (normalizado); razão social/contato com o texto informado.
        cnpj_q = SEPARADORES.sub('', pesquisa.q).upper() if pesquisa.q else None
        itens, total = self.repository.listar(usuario_id, pesquisa, cnpj_q)
        return _pagina([self._serialize(f) for f in itens], total, pesquisa)

    def selecao_entrada(self, usuario_id, args):
        """Opção B (aprovada): lista mínima de fornecedores ATIVOS para o seletor da ENTRADA.
        Sem parâmetros, sem contato, sem recebimentos; até LIMITE_SELECAO itens na ordem da listagem."""
        parametros(args, ())
        pesquisa = PesquisaFornecedoresDTO(ativo=True, page_size=LIMITE_SELECAO)
        itens, total = self.repository.listar(usuario_id, pesquisa, permissoes=PERMISSOES_SELECAO)
        return dict(itens=[dict(id_fornecedor=f.id_fornecedor, razao_social=f.razao_social,
                                cnpj_formatado=formatar_cnpj(f.cnpj)) for f in itens],
                    total=total, limite=LIMITE_SELECAO)

    def obter(self, usuario_id, fornecedor_id):
        return self._serialize(self.repository.obter(usuario_id, fornecedor_id))

    def criar(self, usuario_id, data):
        return self._serialize(self.repository.criar(usuario_id, FornecedorDTO.from_dict(data)))

    def atualizar(self, usuario_id, fornecedor_id, data):
        dto = FornecedorDTO.from_dict(data, atualizando=True)
        return self._serialize(self.repository.atualizar(usuario_id, fornecedor_id, dto))

    def excluir(self, usuario_id, fornecedor_id):
        self.repository.excluir(usuario_id, fornecedor_id)

    def recebimentos(self, usuario_id, fornecedor_id, args):
        pagina = PaginaDTO.from_args(args)
        itens, total = self.repository.recebimentos(usuario_id, fornecedor_id, pagina)
        return _pagina([MovimentacaoService._serialize(m) for m in itens], total, pagina)
