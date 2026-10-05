"""Etapa 2F.2 (correção aprovada, opção B) — exportação CSV em UMA requisição e UMA transação, isolada.

Reutiliza o double somente leitura de test_relatorios (nenhum PostgreSQL). Comprova a estrutura: uma única transação
REPEATABLE READ READ ONLY por exportação, total e linhas lidos nela, limite de 1.000, contrato e permissões. O snapshot
real do PostgreSQL é comprovado na homologação 2F.2 (instância auxiliar + servidor principal).
"""
from datetime import timedelta
import json
from unittest import TestCase
from unittest.mock import patch

from flask.sessions import SecureCookieSessionInterface

from app import create_app
from core.errors import ValidationError
from repository.relatorios_repository import RelatoriosRepository
from schemas.relatorios_dto import LIMITE_EXPORTACAO, PAGE_SIZE_MAXIMO, FiltroMovimentacoesDTO, FiltroProdutosDTO
from test_relatorios import (FIM, INICIO, SENSIVEIS, SOMENTE_LEITURA, T0, ReportConnection, ReportDB, args, chaves,
                             periodo, seed)

PER = f'data_inicio={INICIO}&data_fim={FIM}'
EXPORTACOES = {  # rota de exportação → rota da consulta paginada equivalente
    '/relatorios/posicao-estoque/exportacao': '/relatorios/posicao-estoque',
    '/relatorios/estoque-baixo/exportacao': '/relatorios/estoque-baixo',
    f'/relatorios/movimentacoes/exportacao?{PER}': f'/relatorios/movimentacoes?{PER}',
    '/relatorios/divergencias/exportacao': '/relatorios/divergencias',
    f'/relatorios/saidas-periodo/exportacao?{PER}': f'/relatorios/saidas-periodo?{PER}',
}
CONTRATO = {'itens', 'total', 'quantidade', 'limite', 'truncado', 'gerado_em', 'filtros', 'ordenacao'}


class TrackedConnection(ReportConnection):
    """Registra os ROLLBACKs na mesma sequência das consultas (fronteiras de transação)."""

    def rollback(self):
        self.queries.append(('ROLLBACK', ()))
        super().rollback()


class ExportBase(TestCase):
    def setUp(self):
        self.db = ReportDB()
        seed(self.db)
        self.conn = TrackedConnection(self.db)
        with patch('app.Connection.get_connection', return_value=self.conn):
            self.app = create_app()
        self.app.session_interface = SecureCookieSessionInterface()
        self.client = self.app.test_client()
        with self.client.session_transaction() as s:
            s['user_id'] = 1

    def tearDown(self):
        self.assertEqual(self.conn.commits, 0, 'exportação não pode fazer commit')

    def movimentos(self, n):
        """n movimentações ENTRADA do produto 6 dentro do período (instantes distintos)."""
        for k in range(n):
            self.db.move(1000 + k, 6, 'ENTRADA', 1, k, k + 1, T0 + timedelta(hours=5, seconds=k))

    def exportar_mov(self, **extra):
        query = '&'.join(f'{k}={v}' for k, v in dict(id_produto=6, **extra).items())
        return self.client.get(f'/relatorios/movimentacoes/exportacao?{PER}&{query}')


class ExportacaoTransacaoTests(ExportBase):
    def test_single_read_only_transaction_holds_total_rows_and_metadata(self):
        for rota in EXPORTACOES:
            with self.subTest(rota=rota):
                self.conn.queries.clear()
                response = self.client.get(rota)
                self.assertEqual(response.status_code, 200, response.json)
                seq = [q for q, _ in self.conn.queries]
                self.assertEqual(seq.count(SOMENTE_LEITURA), 1, 'uma única transação de leitura por exportação')
                inicio = seq.index(SOMENTE_LEITURA)
                fim = seq.index('ROLLBACK', inicio)
                dentro = seq[inicio:fim]
                rotuladas = [q for q in seq if q.startswith('/* 2F:')]
                self.assertTrue(rotuladas)
                # Revalidação do usuário, total, linhas e gerado_em: todos entre SET TRANSACTION e o ROLLBACK final.
                self.assertTrue(all(q in dentro for q in rotuladas))
                self.assertIn('SELECT ativo, perfil FROM usuarios WHERE id_usuario = %s', dentro)
                self.assertIn('/* 2F:agora */ SELECT CURRENT_TIMESTAMP', dentro)
                self.assertTrue(any(q.startswith(('/* 2F:produtos_total', '/* 2F:movimentacoes_resumo',
                                                  '/* 2F:contagens_resumo', '/* 2F:saidas_total')) for q in dentro))
                self.assertEqual(seq[-1], 'ROLLBACK')  # transação encerrada antes da resposta
                self.assertFalse(self.conn.readonly)

    def test_limit_and_offset_are_the_export_ones(self):
        self.conn.queries.clear()
        self.exportar_mov()
        listagem = [p for q, p in self.conn.queries if q.startswith('/* 2F:movimentacoes */')]
        self.assertEqual(len(listagem), 1)
        self.assertEqual(listagem[0][-2:], (LIMITE_EXPORTACAO, 0))

    def test_repository_called_once_per_export(self):
        for metodo, rota in (('posicao_estoque', '/relatorios/posicao-estoque/exportacao'),
                             ('estoque_baixo', '/relatorios/estoque-baixo/exportacao'),
                             ('movimentacoes', f'/relatorios/movimentacoes/exportacao?{PER}'),
                             ('divergencias', '/relatorios/divergencias/exportacao'),
                             ('saidas_periodo', f'/relatorios/saidas-periodo/exportacao?{PER}')):
            with self.subTest(rota=rota), patch.object(RelatoriosRepository, metodo,
                                                       wraps=getattr(RelatoriosRepository(self.conn), metodo)) as spy:
                self.assertEqual(self.client.get(rota).status_code, 200)
                self.assertEqual(spy.call_count, 1)
                self.assertEqual(spy.call_args.args[1].page_size, LIMITE_EXPORTACAO)

    def test_failure_mid_export_is_generic_500_with_rollback_and_no_rows(self):
        self.db.fail = 'movimentacoes'
        self.conn.queries.clear()
        response = self.exportar_mov()
        self.assertEqual((response.status_code, response.json), (500, {'message': 'Erro interno do servidor'}))
        self.assertEqual(self.conn.queries[-1][0], 'ROLLBACK')
        self.assertFalse(self.conn.readonly)


class ExportacaoContratoTests(ExportBase):
    def test_below_equal_and_above_limit(self):
        for n, quantidade, truncado in ((999, 999, False), (1000, 1000, False), (1001, 1000, True)):
            with self.subTest(n=n):
                self.db.movements = {k: v for k, v in self.db.movements.items() if k < 1000}
                self.movimentos(n)  # o movimento 6 do produto 6 está no fim exclusivo: fora do período
                data = self.exportar_mov().json
                self.assertEqual((data['total'], data['quantidade'], len(data['itens']), data['truncado'],
                                  data['limite']), (n, quantidade, quantidade, truncado, LIMITE_EXPORTACAO))

    def test_empty_result(self):
        data = self.client.get(f'/relatorios/movimentacoes/exportacao?{PER}&tipo=AJUSTE&id_produto=3').json
        self.assertEqual((data['itens'], data['total'], data['quantidade'], data['truncado']), ([], 0, 0, False))

    def test_contract_keys_and_same_content_as_paginated_queries(self):
        for exportacao, consulta in EXPORTACOES.items():
            with self.subTest(rota=exportacao):
                data = self.client.get(exportacao).json
                self.assertTrue(CONTRATO <= set(data))
                self.assertFalse({'page', 'page_size', 'paginas'} & set(data))
                pagina = self.client.get(consulta + ('&' if '?' in consulta else '?') + 'page_size=100').json
                # Sem concorrência, a exportação reproduz a consulta (mesmos itens, ordem, total, resumo e filtros).
                self.assertEqual(data['itens'], pagina['itens'])
                self.assertEqual((data['total'], data['ordenacao'], data['filtros']),
                                 (pagina['total'], pagina['ordenacao'], pagina['filtros']))
                for extra in ('resumo', 'totais', 'criterio', 'indicador', 'criterio_estoque_baixo', 'criterio_sugestao'):
                    self.assertEqual(data.get(extra), pagina.get(extra))
                self.assertEqual(data['quantidade'], len(data['itens']))

    def test_stable_order_with_ties_matches_concatenated_pages(self):
        self.movimentos(250)
        for k in range(30):  # empates de instante: desempate pela chave primária
            self.db.move(2000 + k, 6, 'SAIDA', 1, 5, 4, T0 + timedelta(hours=5))
        exportados = [i['id_movimentacao'] for i in self.exportar_mov().json['itens']]
        paginas = []
        for page in range(1, 4):
            paginas += [i['id_movimentacao'] for i in
                        self.client.get(f'/relatorios/movimentacoes?{PER}&id_produto=6&page={page}&page_size=100')
                        .json['itens']]
        self.assertEqual(exportados, paginas)
        self.assertEqual(len(exportados), len(set(exportados)))
        datas = [(self.db.movements[i]['data'], i) for i in exportados]
        self.assertEqual(datas, sorted(datas, reverse=True))

    def test_filters_apply_and_truncation_uses_the_same_response_total(self):
        self.movimentos(1200)
        data = self.exportar_mov(tipo='ENTRADA').json
        self.assertEqual((data['total'], data['quantidade'], data['truncado']), (1200, 1000, True))
        self.assertTrue(all(i['tipo'] == 'ENTRADA' and i['id_produto'] == 6 for i in data['itens']))
        self.assertEqual(data['filtros']['tipo'], 'ENTRADA')
        data = self.client.get('/relatorios/divergencias/exportacao?situacao=PENDENTE&sinal=negativa').json
        self.assertEqual(data['total'], 2)
        self.assertTrue(all(i['situacao'] == 'PENDENTE' and i['divergencia'] < 0 for i in data['itens']))
        data = self.client.get('/relatorios/posicao-estoque/exportacao?categoria=Limpeza&estoque_baixo=sim').json
        self.assertEqual([i['id_produto'] for i in data['itens']], [5, 3])


class ExportacaoValidacaoTests(ExportBase):
    def test_pagination_parameters_are_rejected_and_normal_limit_preserved(self):
        for rota in EXPORTACOES:
            for extra in ('page=1', 'page_size=1000', 'page_size=100'):
                with self.subTest(rota=rota, extra=extra):
                    r = self.client.get(rota + ('&' if '?' in rota else '?') + extra)
                    self.assertEqual(r.status_code, 400)
                    self.assertEqual(set(r.json), {'message'})
        self.assertEqual(self.client.get('/relatorios/posicao-estoque?page_size=1000').status_code, 400)
        self.assertEqual(self.client.get('/relatorios/posicao-estoque?page_size=101').status_code, 400)
        self.assertEqual(PAGE_SIZE_MAXIMO, 100)
        with self.assertRaises(ValidationError):
            FiltroProdutosDTO.from_args(args(page_size=1000), estoque_baixo=False)
        self.assertEqual(FiltroMovimentacoesDTO.from_args(periodo(), exportacao=True).page_size, LIMITE_EXPORTACAO)

    def test_strict_validation_and_404(self):
        casos = ('/relatorios/movimentacoes/exportacao',                                   # período obrigatório
                 f'/relatorios/movimentacoes/exportacao?{PER}&tipo=X',
                 f'/relatorios/movimentacoes/exportacao?{PER}&tipo=SAIDA&tipo=ENTRADA',
                 '/relatorios/estoque-baixo/exportacao?formato=xlsx',
                 '/relatorios/estoque-baixo/exportacao?limite=5000',
                 '/relatorios/posicao-estoque/exportacao?id_produto=%D9%A1',
                 '/relatorios/divergencias/exportacao?situacao=SEM_DIVERGENCIA',
                 '/relatorios/saidas-periodo/exportacao?data_inicio=2026-10-01&data_fim=2026-10-02',
                 '/relatorios/estoque-baixo/exportacao?perfil=ADMINISTRADOR')
        for rota in casos:
            with self.subTest(rota=rota):
                self.assertEqual(self.client.get(rota).status_code, 400)
        self.assertEqual(self.client.get('/relatorios/posicao-estoque/exportacao?id_produto=99').status_code, 404)
        r = self.client.get("/relatorios/divergencias/exportacao?lote=L1'%3B%20DROP%20TABLE%20produtos%3B--")
        self.assertEqual((r.status_code, r.json['itens'], r.json['total']), (200, [], 0))

    def test_methods_other_than_get_are_405(self):
        for rota in EXPORTACOES:
            for metodo in ('post', 'put', 'patch', 'delete'):
                with self.subTest(rota=rota, metodo=metodo):
                    self.assertEqual(getattr(self.client, metodo)(rota.split('?')[0], json={}).status_code, 405)


class ExportacaoAutorizacaoTests(ExportBase):
    def test_no_session_is_401_without_queries(self):
        with self.client.session_transaction() as s:
            s.clear()
        for rota in EXPORTACOES:
            with self.subTest(rota=rota):
                self.assertEqual(self.client.get(rota).status_code, 401)
        self.assertEqual(self.conn.queries, [])

    def test_canonical_profiles_unknown_profile_and_inactive(self):
        # Correção de acessos (pedido posterior do usuário, 05/10/2026): resultado esperado alterado - OPERADOR: 403 em toda exportação.
        for perfil in ('OPERADOR', 'GESTOR', 'AUDITOR', 'ADMINISTRADOR'):
            self.db.users[1] = (True, perfil)
            for rota in EXPORTACOES:
                with self.subTest(perfil=perfil, rota=rota):
                    self.assertEqual(self.client.get(rota).status_code, 403 if perfil == 'OPERADOR' else 200)
        self.db.users[1] = (True, 'leitor')
        for rota in EXPORTACOES:
            self.assertEqual(self.client.get(rota).status_code, 403)
        self.db.users[1] = (False, 'ADMINISTRADOR')
        r = self.client.get('/relatorios/estoque-baixo/exportacao')
        self.assertEqual((r.status_code, r.json), (403, {'message': 'Usuário inativo'}))
        self.assertEqual(self.client.get('/relatorios/estoque-baixo/exportacao').status_code, 401)

    def test_every_listed_permission_is_required(self):
        # relatorios:consultar presente: as permissões de domínio continuam exigidas.
        parcial = {'AUDITOR': frozenset({'relatorios:consultar', 'produtos:consultar', 'movimentacoes:consultar'})}
        esperado = {'/relatorios/posicao-estoque/exportacao': 403, '/relatorios/estoque-baixo/exportacao': 200,
                    f'/relatorios/movimentacoes/exportacao?{PER}': 200, '/relatorios/divergencias/exportacao': 403,
                    f'/relatorios/saidas-periodo/exportacao?{PER}': 200}
        self.db.users[1] = (True, 'AUDITOR')
        with patch.dict('core.permissoes.PERMISSOES', parcial):
            for rota, status in esperado.items():
                with self.subTest(rota=rota):
                    self.assertEqual(self.client.get(rota).status_code, status)

    def test_repository_revalidates_inside_the_export_transaction(self):
        self.db.users[1] = (False, 'ADMINISTRADOR')
        with patch('repository.usuario_repository.UsuarioRepository.obter_acesso', return_value=(True, 'ADMINISTRADOR')):
            r = self.client.get('/relatorios/divergencias/exportacao')
        self.assertEqual(r.status_code, 403)
        self.assertFalse(any(q.startswith('/* 2F:') for q, _ in self.conn.queries))

    def test_no_sensitive_fields_and_no_turnover_label(self):
        for rota in EXPORTACOES:
            with self.subTest(rota=rota):
                data = self.client.get(rota).json
                self.assertFalse({k for k in chaves(data) if any(s in k.lower() for s in SENSIVEIS)})
                self.assertNotIn('giro', json.dumps(data, ensure_ascii=False).lower().replace('não é giro', ''))
