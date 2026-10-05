"""Etapa 2G (RF12) — Fornecedores. Testes isolados; NÃO comprovam PostgreSQL real (fica para a 2G.2).

Estende o double transacional da 2C (locks por linha, rollback por registro) com a tabela
fornecedores, o vínculo/snapshot da ENTRADA, transações somente leitura e o estado do schema
2G (presente ou ausente). Emula UNIQUE/FK/CHECK apenas para detectar uso incorreto.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import threading
from unittest import TestCase
from unittest.mock import patch

from flask.sessions import SecureCookieSessionInterface
from psycopg2.errors import RestrictViolation, UndefinedColumn

from app import create_app
from core.errors import AuthorizationError, BusinessError, NotFoundError, SchemaPendingError, ValidationError
from core.permissoes import PERFIS, pode
from repository.fornecedor_schema import DETECCAO, SCHEMA_MESSAGE
from repository.relatorios_repository import SOMENTE_LEITURA
from repository.usuario_repository import UsuarioRepository
from schemas.fornecedor_dto import (VALIDACAO_CNPJ, FornecedorDTO, PaginaDTO, PesquisaFornecedoresDTO, formatar_cnpj,
                                    normalizar_cnpj)
from schemas.movimentacao_dto import MovimentacaoDTO
from schemas.produto_dto import ProdutoDTO
from services import relatorios_service
from services.fornecedor_service import FornecedorService
from services.movimentacao_service import CAMPOS_FORNECEDOR, MovimentacaoService
from services.produto_service import ProdutoService
from test_produtos import PAYLOAD
from test_rastreamento import Duplicate, PhysicalConnection, PhysicalDB
from test_relatorios import CAMPOS_MOVIMENTACAO

BACKEND = Path(__file__).resolve().parents[1]
CNPJ_A, CNPJ_B = '12345678000195', '12ABC34501DE35'
ESCRITAS = re.compile(r'^\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE)\b|FOR (UPDATE|SHARE)', re.I)


class Restrict(RestrictViolation):
    def __init__(self, name):
        self.name = name

    @property
    def diag(self):
        return type('Diag', (), {'constraint_name': self.name})()


class SupplierDB(PhysicalDB):
    def __init__(self):
        super().__init__()
        # [id, razao_social, cnpj, contato, ativo]
        self.suppliers = {1: [1, 'Alfa Ltda', CNPJ_A, 'compras@alfa.example', True],
                          2: [2, 'Beta S.A.', CNPJ_B, None, True]}
        self.supplier_schema = True
        self.partial = False  # instalação parcial: tabela fornecedores existe, colunas de movimentacoes não
        self.clock = None
        self.fk_name = 'movimentacoes_fornecedor_fk'


class SupplierConnection(PhysicalConnection):
    """Intercepta o SQL de Fornecedores; o restante segue o double físico da 2C."""

    def __init__(self, db, fail=None):
        super().__init__(db, fail)
        self.statements, self.readonly = 0, False

    def finish(self):
        super().finish()
        self.statements, self.readonly = 0, False

    def execute(self, q, p=()):
        primeiro = self.statements == 0
        self.statements += 1
        if self.readonly and ESCRITAS.search(q):
            raise AssertionError('Escrita/lock em transação somente leitura: ' + q)
        db = self.db
        if q == SOMENTE_LEITURA:
            assert primeiro, 'SET TRANSACTION precisa ser a primeira instrução'
            self.queries.append((q, p))
            self.readonly = True
            return
        if q == DETECCAO:
            self.queries.append((q, p))
            self.result = (db.supplier_schema,)
            return
        tabela = ('FROM fornecedores' in q or 'INTO fornecedores' in q
                  or q.startswith(('UPDATE fornecedores', 'DELETE FROM fornecedores')))
        if 'fornecedor' in q and not db.supplier_schema and not (db.partial and tabela):
            # Banco pré-2G (ou parcial): SQL com objetos 2G ausentes falharia no PostgreSQL.
            self.queries.append((q, p))
            raise UndefinedColumn()
        if tabela:
            self.queries.append((q, p))
            return self.supplier_execute(q, list(p))
        if q.startswith('UPDATE movimentacoes SET id_fornecedor'):
            self.queries.append((q, p))
            row = db.movements[p[3]]
            # Emula movimentacoes_fornecedor_fk e movimentacoes_fornecedor_check.
            assert row[3] == 'ENTRADA' and p[0] in db.suppliers and None not in p
            self.write('movements', p[3], list(row[:19]) + [p[0], p[1], p[2]])
            if self.fail == 'supplier_link': raise RuntimeError('Falha simulada')
            return
        if q.startswith('SELECT COUNT(*) FROM movimentacoes m WHERE m.id_fornecedor'):
            self.queries.append((q, p))
            self.result = (len(self._recebimentos(p[0])),)
            return
        if 'FROM movimentacoes m' in q and 'm.id_fornecedor = %s' in q:
            self.queries.append((q, p))
            rows = self._recebimentos(p[0])
            self.result = [self._linha(r, q) for r in rows[p[2]:p[2] + p[1]]]
            return
        super().execute(q, p)
        if q.startswith('INSERT INTO movimentacoes') and db.clock is not None:
            db.movements[self.result[0]][8] = db.clock
        if 'FROM movimentacoes m' in q:
            self.result = [self._linha(r, q) for r in self.result]

    def _recebimentos(self, fornecedor_id):
        rows = [r for r in self.db.movements.values() if len(r) > 19 and r[19] == fornecedor_id and r[3] == 'ENTRADA']
        return sorted(rows, key=lambda r: (r[8], r[0]), reverse=True)

    @staticmethod
    def _linha(row, q):
        row = list(row) + [None] * (22 - len(row))
        return tuple(row if 'm.fornecedor_cnpj' in q else row[:19])

    def supplier_execute(self, q, p):
        db = self.db
        if q.startswith('SELECT id_fornecedor, razao_social, cnpj, ativo FROM fornecedores'):
            assert q.endswith('FOR SHARE')
            self.acquire('suppliers', p[0])
            row = db.suppliers.get(p[0])
            self.result = (row[0], row[1], row[2], row[4]) if row else None
        elif q.startswith('SELECT COUNT(*) FROM fornecedores') or ('ORDER BY razao_social, id_fornecedor' in q):
            rows = list(db.suppliers.values())
            if 'ILIKE' in q:
                termos = [x[1:-1].replace('!%', '%').replace('!_', '_').replace('!!', '!').lower() for x in p[:3]]
                del p[:3]
                rows = [r for r in rows if termos[0] in r[1].lower() or termos[1] in (r[3] or '\x00').lower()
                        or termos[2] in r[2].lower()]
            if 'ativo = %s' in q:
                valor = p.pop(0)
                rows = [r for r in rows if r[4] is valor]
            if q.startswith('SELECT COUNT(*)'):
                self.result = (len(rows),)
            else:
                rows.sort(key=lambda r: (r[1], r[0]))
                limite, deslocamento = p
                self.result = [tuple(r) for r in rows[deslocamento:deslocamento + limite]]
        elif q.startswith('SELECT id_fornecedor, razao_social, cnpj, contato, ativo FROM fornecedores WHERE id_fornecedor'):
            row = db.suppliers.get(p[0])
            self.result = tuple(row) if row else None
        elif q.startswith('INSERT INTO fornecedores'):
            if any(r[2] == p[1] for r in db.suppliers.values()): raise Duplicate('fornecedores_cnpj_key')
            key = max(db.suppliers, default=0) + 1
            self.write('suppliers', key, [key, *p])
            self.result = tuple(db.suppliers[key])
        elif q.startswith('UPDATE fornecedores'):
            key = p[-1]
            self.acquire('suppliers', key)
            if key not in db.suppliers:
                self.result = None
                return
            if any(r[2] == p[1] and r[0] != key for r in db.suppliers.values()): raise Duplicate('fornecedores_cnpj_key')
            self.write('suppliers', key, [key, *p[:4]])
            self.result = tuple(db.suppliers[key])
        elif q.startswith('DELETE FROM fornecedores'):
            key = p[0]
            self.acquire('suppliers', key)
            if any(len(r) > 19 and r[19] == key for r in db.movements.values()):
                raise Restrict(db.fk_name)
            if key not in db.suppliers:
                self.result = None
                return
            self.write('suppliers', key, None)
            db.suppliers.pop(key)
            self.result = (key,)
        else:
            raise AssertionError('Consulta de fornecedor não suportada: ' + q)


class Base(TestCase):
    def setUp(self):
        self.db = SupplierDB()
        self.conn = SupplierConnection(self.db)
        self.service = FornecedorService(self.conn)
        self.addCleanup(self.conn.rollback)

    def move(self, tipo='ENTRADA', amount=5, product=7, position=1, lot='L-1', conn=None, **extra):
        return MovimentacaoService(conn or SupplierConnection(self.db)).registrar(product, 23, dict(
            tipo=tipo, id_posicao=position, lote=lot, motivo='Operação',
            **{'novo_saldo_item' if tipo == 'AJUSTE' else 'quantidade': amount}, **extra))

    def state(self):
        return deepcopy((self.db.products, self.db.items, self.db.movements, self.db.suppliers))

    def invariant(self):
        for key, product in self.db.products.items():
            self.assertEqual(product[6], sum(i[4] for i in self.db.items.values() if i[1] == key))

    def no_ddl(self, *conns):
        for conn in conns or (self.conn,):
            self.assertFalse([q for q, _ in conn.queries if re.match(r'\s*(CREATE|ALTER|DROP|TRUNCATE)\b', q, re.I)])


# ── DTOs ─────────────────────────────────────────────────────────────────────

class CnpjTests(TestCase):
    def test_normalization_and_alphanumeric_format(self):
        for valor, esperado in (('12.345.678/0001-95', CNPJ_A), ('12345678000195', CNPJ_A),
                                (' 12 345 678 0001 95 ', CNPJ_A), ('12.abc.345/01de-35', CNPJ_B),
                                ('12-ABC-345-01DE-35', CNPJ_B)):
            with self.subTest(valor=valor):
                self.assertEqual(normalizar_cnpj(valor), esperado)

    def test_invalid_format(self):
        for valor in ('', '   ', '1234567800019', '123456780001950', '123456780001A5',
                      '12.345.678_0001-95', '１２３４５６７８０００１９５', '12345678000195\x00', 'ÇA345678000195',
                      '1' * 41, None, 12345678000195, True, ['12345678000195']):
            with self.subTest(valor=valor), self.assertRaises(ValidationError):
                normalizar_cnpj(valor)

    def test_display_and_declared_limits(self):
        self.assertEqual(formatar_cnpj(CNPJ_A), '12.345.678/0001-95')
        self.assertEqual(formatar_cnpj(CNPJ_B), '12.ABC.345/01DE-35')
        self.assertEqual(VALIDACAO_CNPJ, 'Validação de formato; não verifica dígitos verificadores, '
                                         'existência ou situação cadastral.')
        # Formato válido, dígitos verificadores errados: aceito (política A, sem validação fiscal).
        self.assertEqual(normalizar_cnpj('12.345.678/0001-00'), '12345678000100')
        with self.assertRaises(ValidationError) as ctx:
            normalizar_cnpj('123')
        self.assertIn(VALIDACAO_CNPJ, str(ctx.exception))


class FornecedorDTOTests(TestCase):
    def test_valid_create_and_trimming(self):
        dto = FornecedorDTO.from_dict(dict(razao_social='  Alfa  ', cnpj='12.345.678/0001-95', contato=' tel '))
        self.assertEqual((dto.razao_social, dto.cnpj, dto.contato, dto.ativo), ('Alfa', CNPJ_A, 'tel', True))
        self.assertIsNone(FornecedorDTO.from_dict(dict(razao_social='A', cnpj=CNPJ_A, contato='  ')).contato)
        self.assertIsNone(FornecedorDTO.from_dict(dict(razao_social='A', cnpj=CNPJ_A, contato=None)).contato)

    def test_invalid_fields(self):
        base = dict(razao_social='Alfa', cnpj=CNPJ_A)
        for data in ([], 'x', None, {}, dict(base, razao_social=''), dict(base, razao_social='  '),
                     dict(base, razao_social='x' * 201), dict(base, razao_social=1), dict(base, razao_social='a\x00'),
                     dict(base, contato=5), dict(base, contato='x' * 201), dict(base, ativo='true'), dict(base, ativo=1),
                     dict(base, cnpj='1'), dict(base, id_fornecedor=1), dict(base, admin=True),
                     dict(base, permissoes=['x']), {'cnpj': CNPJ_A}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                FornecedorDTO.from_dict(data)
        self.assertEqual(FornecedorDTO.from_dict(dict(base, razao_social='x' * 200)).razao_social, 'x' * 200)

    def test_put_requires_explicit_activity(self):
        with self.assertRaises(ValidationError):
            FornecedorDTO.from_dict(dict(razao_social='A', cnpj=CNPJ_A), atualizando=True)
        self.assertFalse(FornecedorDTO.from_dict(dict(razao_social='A', cnpj=CNPJ_A, ativo=False), atualizando=True).ativo)

    def test_search_and_pagination_filters(self):
        dto = PesquisaFornecedoresDTO.from_args({'q': [' alfa '], 'ativo': ['sim'], 'page': ['2'], 'page_size': ['100']})
        self.assertEqual((dto.q, dto.ativo, dto.page, dto.page_size), ('alfa', True, 2, 100))
        self.assertEqual(PesquisaFornecedoresDTO.from_args({}).page_size, 20)
        self.assertFalse(PesquisaFornecedoresDTO.from_args({'ativo': ['nao']}).ativo)
        for args in ({'page_size': ['101']}, {'page_size': ['0']}, {'page': ['0']}, {'page': ['100001']},
                     {'page': ['1', '2']}, {'x': ['1']}, {'ativo': ['true']}, {'q': ['']}, {'q': ['x' * 201]},
                     {'page': ['-1']}, {'page': ['1.5']}, {'page': ['٣']}, {'perfil': ['ADMINISTRADOR']}):
            with self.subTest(args=args), self.assertRaises(ValidationError):
                PesquisaFornecedoresDTO.from_args(args)
        with self.assertRaises(ValidationError):
            PaginaDTO.from_args({'page_size': ['101']})
        with self.assertRaises(ValidationError):
            PaginaDTO.from_args({'q': ['x']})


class PayloadContractTests(TestCase):
    def test_entrada_supplier_id_semantics(self):
        base = dict(tipo='ENTRADA', quantidade=1, id_posicao=1, lote='L')
        self.assertEqual(MovimentacaoDTO.from_dict(dict(base, id_fornecedor=3)).id_fornecedor, 3)
        self.assertIsNone(MovimentacaoDTO.from_dict(dict(base, id_fornecedor=None)).id_fornecedor)
        self.assertIsNone(MovimentacaoDTO.from_dict(base).id_fornecedor)
        for valor in (0, -1, True, False, '3', 1.5, 2147483648, [3], {'id': 3}):
            with self.subTest(valor=valor), self.assertRaises(ValidationError):
                MovimentacaoDTO.from_dict(dict(base, id_fornecedor=valor))

    def test_supplier_rejected_in_saida_and_ajuste(self):
        for data in (dict(tipo='SAIDA', quantidade=1, id_posicao=1, lote='L'),
                     dict(tipo='AJUSTE', novo_saldo_item=1, id_posicao=1, lote='L', motivo='m')):
            for valor in (1, None):
                with self.subTest(tipo=data['tipo'], valor=valor):
                    with self.assertRaises(ValidationError) as ctx:
                        MovimentacaoDTO.from_dict(dict(data, id_fornecedor=valor))
                    self.assertIn('só é aceito em ENTRADA', str(ctx.exception))

    def test_forged_snapshots_rejected(self):
        for tipo, campo in (('ENTRADA', 'quantidade'), ('SAIDA', 'quantidade')):
            for forjado in ('fornecedor_razao_social', 'fornecedor_cnpj'):
                with self.subTest(tipo=tipo, forjado=forjado), self.assertRaises(ValidationError):
                    MovimentacaoDTO.from_dict({'tipo': tipo, campo: 1, 'id_posicao': 1, 'lote': 'L',
                                               'id_fornecedor': 1, forjado: 'Forjado'})

    def test_produto_allowed_fields_post_and_put(self):
        self.assertEqual(ProdutoDTO.from_dict(PAYLOAD).nome, 'Caixa')
        destino = dict(PAYLOAD, id_posicao=1, lote='L')
        self.assertEqual(ProdutoDTO.from_dict(dict(destino, id_fornecedor=1)).id_fornecedor, 1)
        self.assertIsNone(ProdutoDTO.from_dict(dict(PAYLOAD, id_fornecedor=None)).id_fornecedor)
        update = {k: v for k, v in PAYLOAD.items() if k != 'estoque'}
        self.assertEqual(ProdutoDTO.from_dict(update, updating=True).nome, 'Caixa')
        for data, updating in ((dict(PAYLOAD, id_produto=1), False), (dict(PAYLOAD, fornecedor='Alfa'), False),
                               (dict(PAYLOAD, admin=True), False), (dict(update, id_produto=1), True),
                               (dict(update, descricao='x'), True), (dict(update, id_fornecedor=1), True),
                               (dict(update, id_fornecedor=None), True), (dict(destino, fornecedor_cnpj=CNPJ_A), False),
                               (dict(destino, fornecedor_razao_social='X', id_fornecedor=1), False)):
            with self.subTest(data=data, updating=updating), self.assertRaises(ValidationError):
                ProdutoDTO.from_dict(data, updating=updating)
        with self.assertRaises(ValidationError) as ctx:
            ProdutoDTO.from_dict(dict(PAYLOAD, extra_1=1, extra_2=2))
        self.assertIn('extra_1, extra_2', str(ctx.exception))

    def test_produto_supplier_requires_positive_stock_and_destination(self):
        for data in (dict(PAYLOAD, estoque=0, id_fornecedor=1), dict(PAYLOAD, id_fornecedor=1),
                     dict(PAYLOAD, id_posicao=1, lote='L', id_fornecedor=0),
                     dict(PAYLOAD, id_posicao=1, lote='L', id_fornecedor='1')):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                ProdutoDTO.from_dict(data)

    def test_frontend_product_payload_is_within_allowed_fields(self):
        # Consumidor examinado: js/inventario.js (toApi + destino do estoque inicial + fornecedor opcional).
        fonte = (BACKEND.parent / 'js/inventario.js').read_text(encoding='utf-8')
        corpo = re.search(r'function toApi\(frontendItem\) \{\s*return \{(.*?)\};', fonte, re.S).group(1)
        campos = set(re.findall(r'(\w+):', corpo)) | {'id_posicao', 'lote', 'id_fornecedor'}
        from schemas.produto_dto import CAMPOS_CADASTRO, CAMPOS_EDICAO
        self.assertLessEqual(campos, set(CAMPOS_CADASTRO))
        self.assertLessEqual(campos - {'estoque', 'id_posicao', 'lote', 'id_fornecedor'}, set(CAMPOS_EDICAO))


# ── Cadastro, consulta e manutenção ──────────────────────────────────────────

class FornecedorServiceTests(Base):
    def test_create_normalizes_and_returns_display(self):
        r = self.service.criar(23, dict(razao_social=' Gama ', cnpj='98.765.432/0001-10', contato=None))
        self.assertEqual(r, dict(id_fornecedor=3, razao_social='Gama', cnpj='98765432000110',
                                 cnpj_formatado='98.765.432/0001-10', contato=None, ativo=True))
        self.assertEqual(self.service.obter(23, 3), r)
        self.assertEqual(self.conn.commits, 1)
        self.no_ddl()

    def test_duplicate_cnpj_in_any_format_is_409(self):
        for cnpj in (CNPJ_A, '12.345.678/0001-95', '12abc34501de35'):
            with self.subTest(cnpj=cnpj), self.assertRaises(BusinessError):
                self.service.criar(23, dict(razao_social='Outro', cnpj=cnpj))
        self.assertEqual(len(self.db.suppliers), 2)

    def test_update_corrects_cnpj_and_inactivates_and_reactivates(self):
        r = self.service.atualizar(23, 1, dict(razao_social='Alfa Comércio', cnpj='11.111.111/0001-11',
                                               contato='x', ativo=False))
        self.assertEqual((r['razao_social'], r['cnpj'], r['ativo']), ('Alfa Comércio', '11111111000111', False))
        self.assertTrue(self.service.atualizar(23, 1, dict(razao_social='Alfa', cnpj=CNPJ_A, ativo=True))['ativo'])
        self.assertIsNone(self.db.suppliers[1][3])  # PUT = estado completo; contato omitido = null

    def test_update_errors(self):
        with self.assertRaises(NotFoundError):
            self.service.atualizar(23, 99, dict(razao_social='X', cnpj='12345678000190', ativo=True))
        with self.assertRaises(BusinessError):
            self.service.atualizar(23, 1, dict(razao_social='X', cnpj=CNPJ_B, ativo=True))
        with self.assertRaises(ValidationError):
            self.service.atualizar(23, 1, dict(razao_social='X', cnpj=CNPJ_A))
        self.assertEqual(self.db.suppliers[1][1], 'Alfa Ltda')

    def test_list_search_filters_pagination_and_order(self):
        for nome, cnpj in (('Alfa Ltda', '22222222000122'), ('Zeta 100%_ok', '33333333000133')):
            self.service.criar(23, dict(razao_social=nome, cnpj=cnpj))
        r = self.service.listar(23, {})
        self.assertEqual([f['id_fornecedor'] for f in r['itens']], [1, 3, 2, 4])  # razão social, id (empate)
        self.assertEqual((r['total'], r['page'], r['page_size'], r['paginas']), (4, 1, 20, 1))
        busca = lambda q: [f['id_fornecedor'] for f in self.service.listar(23, {'q': [q]})['itens']]
        self.assertEqual(busca('alfa'), [1, 3])
        self.assertEqual(busca('compras@'), [1])
        self.assertEqual(busca('345.678/0001'), [1])
        self.assertEqual(busca('abc'), [2])
        self.assertEqual(busca('100%_'), [4])
        self.assertEqual(busca('%'), [4])
        self.assertEqual(busca("' OR 1=1 --"), [])
        self.db.suppliers[2][4] = False
        self.assertEqual([f['id_fornecedor'] for f in self.service.listar(23, {'ativo': ['nao']})['itens']], [2])
        self.assertEqual(len(self.service.listar(23, {'ativo': ['sim']})['itens']), 3)
        pagina = self.service.listar(23, {'page': ['2'], 'page_size': ['3']})
        self.assertEqual(([f['id_fornecedor'] for f in pagina['itens']], pagina['total'], pagina['paginas']), ([4], 4, 2))
        alem = self.service.listar(23, {'page': ['9'], 'page_size': ['3']})
        self.assertEqual((alem['itens'], alem['total']), ([], 4))

    def test_reads_are_single_readonly_snapshot_with_rollback(self):
        rollbacks = self.conn.rollbacks
        self.service.listar(23, {'q': ['alfa']})
        nomes = [q for q, _ in self.conn.queries]
        self.assertEqual(nomes[0], SOMENTE_LEITURA)
        self.assertEqual(self.conn.commits, 0)
        self.assertGreaterEqual(self.conn.rollbacks - rollbacks, 2)  # encerra a leitura do decorator e a própria
        self.assertFalse(any('alfa' in q.lower() for q in nomes))  # valores só como parâmetro

    def test_delete_without_references(self):
        self.service.excluir(23, 2)
        self.assertNotIn(2, self.db.suppliers)
        with self.assertRaises(NotFoundError):
            self.service.excluir(23, 2)

    def test_delete_with_references_is_409_and_preserves_everything(self):
        self.move(id_fornecedor=1)
        antes = self.state()
        rollbacks = self.conn.rollbacks
        with self.assertRaises(BusinessError):
            self.service.excluir(23, 1)
        self.assertEqual(self.state(), antes)
        self.assertGreater(self.conn.rollbacks, rollbacks)
        # Inativação continua permitida e o histórico continua consultável.
        self.service.atualizar(23, 1, dict(razao_social='Alfa Ltda', cnpj=CNPJ_A, ativo=False))
        self.assertEqual(self.service.recebimentos(23, 1, {})['total'], 1)

    def test_unexpected_constraint_is_not_a_business_conflict(self):
        self.move(id_fornecedor=1)
        self.db.fk_name = 'outra_fk_desconhecida'
        with self.assertRaises(RestrictViolation):
            self.service.excluir(23, 1)
        self.assertIn(1, self.db.suppliers)

    def test_authorization_is_revalidated_in_transaction(self):
        self.db.role = 'OPERADOR'
        with self.assertRaises(AuthorizationError):
            self.service.criar(23, dict(razao_social='X', cnpj='44444444000144'))
        self.assertEqual(len(self.db.suppliers), 2)
        # Correção de acessos (pedido posterior do usuário, 05/10/2026): resultado esperado alterado - OPERADOR não consulta mais o cadastro.
        with self.assertRaises(AuthorizationError):
            self.service.listar(23, {})
        self.db.role = 'AUDITOR'
        self.assertEqual(self.service.listar(23, {})['total'], 2)  # consulta permitida
        self.db.active = False
        with self.assertRaises(AuthorizationError):
            self.service.listar(23, {})


# ── Recebimento (ENTRADA) com origem ─────────────────────────────────────────

class RecebimentoTests(Base):
    def test_entrada_with_supplier_records_server_snapshot(self):
        r = self.move(amount=5, id_fornecedor=1)
        self.assertEqual((r['id_fornecedor'], r['fornecedor_razao_social'], r['fornecedor_cnpj']), (1, 'Alfa Ltda', CNPJ_A))
        self.assertEqual((r['estoque_anterior'], r['estoque_posterior'], r['quantidade_item_posterior']), (0, 5, 5))
        self.assertEqual(self.db.movements[1][19:], [1, 'Alfa Ltda', CNPJ_A])
        self.invariant()

    def test_entrada_without_supplier_and_old_history_stay_null(self):
        r = self.move(amount=2)
        self.assertEqual([r[c] for c in CAMPOS_FORNECEDOR], [None, None, None])
        self.move(amount=1, id_fornecedor=2)
        historico = MovimentacaoService(SupplierConnection(self.db)).listar(23)
        self.assertEqual([(m['id_movimentacao'], m['id_fornecedor']) for m in historico], [(2, 2), (1, None)])
        self.assertEqual(set(historico[0]), CAMPOS_MOVIMENTACAO | set(CAMPOS_FORNECEDOR))

    def test_different_suppliers_for_same_product(self):
        self.move(amount=1, id_fornecedor=1)
        self.move(amount=1, id_fornecedor=2)
        self.move(amount=1, lot='L-2', position=2, id_fornecedor=1)
        self.assertEqual([m[19] for m in self.db.movements.values()], [1, 2, 1])
        self.assertEqual(self.db.products[7][6], 3)
        self.invariant()

    def test_inactive_or_missing_supplier_rejects_without_side_effects(self):
        self.db.suppliers[2][4] = False
        antes = self.state()
        for fornecedor, erro in ((2, BusinessError), (99, NotFoundError)):
            with self.subTest(fornecedor=fornecedor), self.assertRaises(erro):
                self.move(id_fornecedor=fornecedor)
        self.assertEqual(self.state(), antes)

    def test_supplier_rejected_for_saida_ajuste_before_any_write(self):
        self.move(amount=5)
        antes = self.state()
        for tipo in ('SAIDA', 'AJUSTE'):
            with self.subTest(tipo=tipo), self.assertRaises(ValidationError):
                self.move(tipo=tipo, amount=1, id_fornecedor=1)
        self.assertEqual(self.state(), antes)

    def test_snapshot_preserved_after_supplier_edit(self):
        self.move(amount=1, id_fornecedor=1)
        self.service.atualizar(23, 1, dict(razao_social='Alfa Renomeada', cnpj='55555555000155', ativo=True))
        historico = MovimentacaoService(SupplierConnection(self.db)).listar(23)
        self.assertEqual((historico[0]['fornecedor_razao_social'], historico[0]['fornecedor_cnpj']), ('Alfa Ltda', CNPJ_A))
        r = self.service.recebimentos(23, 1, {})['itens'][0]
        self.assertEqual((r['fornecedor_razao_social'], r['fornecedor_cnpj']), ('Alfa Ltda', CNPJ_A))
        self.move(amount=1, id_fornecedor=1)
        self.assertEqual(self.db.movements[2][20:], ['Alfa Renomeada', '55555555000155'])

    def test_supplier_edit_never_touches_stock(self):
        self.move(amount=4, id_fornecedor=1)
        estoque = deepcopy((self.db.products, self.db.items, self.db.movements))
        self.service.atualizar(23, 1, dict(razao_social='Outra', cnpj=CNPJ_A, ativo=False))
        self.assertEqual(deepcopy((self.db.products, self.db.items, self.db.movements)), estoque)

    def test_lock_order_user_supplier_product_corridor_position_item(self):
        conn = SupplierConnection(self.db)
        self.move(amount=1, id_fornecedor=1, conn=conn)
        q = [x for x, _ in conn.queries]
        idx = lambda prefixo: next(i for i, x in enumerate(q) if x.startswith(prefixo))
        ordem = [idx('SELECT ativo, perfil FROM usuarios'), idx('SELECT id_fornecedor, razao_social, cnpj, ativo'),
                 idx('SELECT estoque FROM produtos'), idx('SELECT id_corredor, identificacao'),
                 idx('SELECT codigo_posicao, ativo'), idx('SELECT id_item_estoque, quantidade, status')]
        self.assertEqual(ordem, sorted(ordem))
        self.assertIn('FOR SHARE', q[ordem[0]])
        self.assertEqual(conn.commits, 1)

    def test_rollback_at_every_stage_leaves_no_partial_state(self):
        self.move(amount=2)
        for etapa in ('item', 'product', 'movement', 'context', 'supplier_link', 'response', 'commit'):
            with self.subTest(etapa=etapa):
                antes = self.state()
                conn = SupplierConnection(self.db, fail=etapa)
                with self.assertRaises(RuntimeError):
                    self.move(amount=3, id_fornecedor=1, conn=conn)
                self.assertEqual(self.state(), antes)
                self.assertEqual(conn.commits, 0)
                self.assertFalse(any(len(m) > 19 and m[19] for m in self.db.movements.values()))
        self.invariant()

    def test_capacity_and_other_lots_preserved(self):
        self.move(amount=5, position=2, lot='OUTRO')
        antes_outro = deepcopy(self.db.items[1])
        with self.assertRaises(BusinessError):
            self.move(amount=16, id_fornecedor=1)  # corredor A: capacidade 20, ocupação 5
        self.move(amount=15, id_fornecedor=1)
        self.assertEqual(self.db.items[1], antes_outro)
        self.invariant()


class EstoqueInicialTests(Base):
    def produto(self, conn=None, **extra):
        return ProdutoService(conn or SupplierConnection(self.db)).registrar_produto(
            dict(PAYLOAD, codigo='NOVO-1', id_posicao=1, lote='INIT', **extra), 23)

    def test_initial_stock_entrada_receives_supplier_in_same_transaction(self):
        conn = SupplierConnection(self.db)
        produto = self.produto(conn, id_fornecedor=2)
        movimento = next(m for m in self.db.movements.values() if m[1] == produto['id_produto'])
        self.assertEqual((movimento[3], movimento[7], movimento[19:]), ('ENTRADA', 'Estoque inicial', [2, 'Beta S.A.', CNPJ_B]))
        q = [x for x, _ in conn.queries]
        fornecedor = next(i for i, x in enumerate(q) if x.startswith('SELECT id_fornecedor, razao_social, cnpj, ativo'))
        insert = next(i for i, x in enumerate(q) if x.startswith('INSERT INTO produtos'))
        self.assertLess(fornecedor, insert)  # lock do fornecedor antes do INSERT do Produto
        self.assertEqual(conn.commits, 1)
        self.invariant()

    def test_inactive_supplier_reverts_product_creation(self):
        self.db.suppliers[1][4] = False
        antes = self.state()
        with self.assertRaises(BusinessError):
            self.produto(id_fornecedor=1)
        self.assertEqual(self.state(), antes)

    def test_failure_after_link_reverts_product_item_and_history(self):
        antes = self.state()
        for etapa in ('supplier_link', 'response'):
            with self.subTest(etapa=etapa), self.assertRaises(RuntimeError):
                self.produto(SupplierConnection(self.db, fail=etapa), id_fornecedor=1)
            self.assertEqual(self.state(), antes)

    def test_zero_stock_with_supplier_is_400(self):
        with self.assertRaises(ValidationError):
            ProdutoService(SupplierConnection(self.db)).registrar_produto(dict(PAYLOAD, estoque=0, id_fornecedor=1), 23)

    def test_product_update_cannot_alter_origin(self):
        self.move(amount=1, id_fornecedor=1)
        antes = self.state()
        update = {k: v for k, v in PAYLOAD.items() if k != 'estoque'}
        for extra in (dict(id_fornecedor=2), dict(id_fornecedor=None), dict(fornecedor_razao_social='X')):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                ProdutoService(SupplierConnection(self.db)).atualizar_produto(7, dict(update, **extra), 23)
        self.assertEqual(self.state(), antes)


class ConcorrenciaTests(Base):
    def corrida(self, primeiro, segundo):
        """Primeiro adquire o lock do fornecedor e pausa; o segundo espera nesse lock."""
        self.db.first_locked.clear(); self.db.second_waiting.clear(); self.db.release_first.clear()
        self.db.coordinate = 'suppliers'
        resultados = {}

        def rodar(nome, funcao):
            try:
                resultados[nome] = ('ok', funcao(SupplierConnection(self.db)))
            except Exception as error:  # registrado para asserção
                resultados[nome] = (type(error).__name__, None)
        t1 = threading.Thread(target=rodar, args=('primeiro', primeiro))
        t2 = threading.Thread(target=rodar, args=('segundo', segundo))
        t1.start()
        self.assertTrue(self.db.first_locked.wait(3))
        t2.start()
        self.assertTrue(self.db.second_waiting.wait(3))
        self.db.release_first.set()
        t1.join(5)
        t2.join(5)
        self.db.coordinate = None
        return resultados['primeiro'], resultados['segundo']

    def entrada(self, conn):
        return self.move(amount=1, id_fornecedor=1, conn=conn)

    def editar(self, **dados):
        base = dict(razao_social='Alfa Ltda', cnpj=CNPJ_A, ativo=True)
        return lambda conn: FornecedorService(conn).atualizar(23, 1, dict(base, **dados))

    def test_receipt_then_inactivation(self):
        r1, r2 = self.corrida(self.entrada, self.editar(ativo=False))
        self.assertEqual((r1[0], r2[0]), ('ok', 'ok'))
        self.assertEqual(r1[1]['fornecedor_razao_social'], 'Alfa Ltda')
        with self.assertRaises(BusinessError):
            self.entrada(SupplierConnection(self.db))

    def test_inactivation_then_receipt_is_409(self):
        r1, r2 = self.corrida(self.editar(ativo=False), self.entrada)
        self.assertEqual((r1[0], r2[0]), ('ok', 'BusinessError'))
        self.assertEqual(len(self.db.movements), 0)

    def test_receipt_versus_rename_keeps_committed_snapshot(self):
        r1, _ = self.corrida(self.entrada, self.editar(razao_social='Novo Nome'))
        self.assertEqual(r1[1]['fornecedor_razao_social'], 'Alfa Ltda')
        _, r2 = self.corrida(self.editar(razao_social='Depois'), self.entrada)
        self.assertEqual(r2[1]['fornecedor_razao_social'], 'Depois')
        self.assertEqual([m[20] for m in self.db.movements.values()], ['Alfa Ltda', 'Depois'])

    def test_receipt_versus_delete(self):
        r1, r2 = self.corrida(self.entrada, lambda conn: FornecedorService(conn).excluir(23, 1))
        self.assertEqual((r1[0], r2[0]), ('ok', 'BusinessError'))
        self.assertIn(1, self.db.suppliers)
        r1, r2 = self.corrida(lambda conn: FornecedorService(conn).excluir(23, 2),
                              lambda conn: self.move(amount=1, id_fornecedor=2, conn=conn))
        self.assertEqual((r1[0], r2[0]), ('ok', 'NotFoundError'))
        self.assertEqual(len(self.db.movements), 1)
        self.invariant()


# ── Recebimentos por fornecedor ──────────────────────────────────────────────

class RecebimentosConsultaTests(Base):
    def test_only_entradas_of_supplier_ordered_and_paginated(self):
        self.db.clock = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
        for fornecedor in (1, 2, 1, 1):
            self.move(amount=1, id_fornecedor=fornecedor)
        self.move(tipo='SAIDA', amount=1)
        self.move(amount=1)
        r = self.service.recebimentos(23, 1, {'page_size': ['2']})
        self.assertEqual(([m['id_movimentacao'] for m in r['itens']], r['total'], r['paginas']), ([4, 3], 3, 2))
        segunda = self.service.recebimentos(23, 1, {'page': ['2'], 'page_size': ['2']})
        self.assertEqual([m['id_movimentacao'] for m in segunda['itens']], [1])
        self.assertTrue(all(m['tipo'] == 'ENTRADA' and m['id_fornecedor'] == 1 for m in r['itens']))
        self.db.movements[1][8] = self.db.clock + timedelta(seconds=1)  # data_hora prevalece sobre o id
        self.assertEqual(self.service.recebimentos(23, 1, {})['itens'][0]['id_movimentacao'], 1)

    def test_missing_empty_and_inactive(self):
        with self.assertRaises(NotFoundError):
            self.service.recebimentos(23, 99, {})
        self.assertEqual(self.service.recebimentos(23, 2, {})['itens'], [])
        self.db.suppliers[2][4] = False
        self.assertEqual(self.service.recebimentos(23, 2, {})['total'], 0)
        with self.assertRaises(ValidationError):
            self.service.recebimentos(23, 1, {'page_size': ['101']})

    def test_requires_both_permissions_in_transaction(self):
        with patch.dict('core.permissoes.PERMISSOES', {'AUDITOR': frozenset({'fornecedores:consultar'})}):
            self.db.role = 'AUDITOR'
            with self.assertRaises(AuthorizationError):
                self.service.recebimentos(23, 1, {})


# ── Compatibilidade antes do SQL 2G ──────────────────────────────────────────

class SchemaPendenteTests(Base):
    def setUp(self):
        super().setUp()
        self.db.supplier_schema = False

    def test_supplier_features_return_503_without_ddl(self):
        self._todas_503()
        self.db.partial = True  # tabela presente, colunas ausentes: não é schema pronto
        self._todas_503()
        self.assertEqual(len(self.db.suppliers), 2)

    def _todas_503(self):
        for chamada in (lambda: self.service.listar(23, {}), lambda: self.service.obter(23, 1),
                        lambda: self.service.criar(23, dict(razao_social='X', cnpj=CNPJ_A)),
                        lambda: self.service.atualizar(23, 1, dict(razao_social='X', cnpj=CNPJ_A, ativo=True)),
                        lambda: self.service.excluir(23, 1), lambda: self.service.recebimentos(23, 1, {})):
            with self.subTest(chamada=chamada), self.assertRaises(SchemaPendingError) as ctx:
                chamada()
            self.assertEqual(str(ctx.exception), SCHEMA_MESSAGE)
        self.no_ddl()

    def test_entrada_with_supplier_is_503_not_discarded(self):
        antes = self.state()
        for parcial in (False, True):
            self.db.partial = parcial
            with self.subTest(parcial=parcial):
                with self.assertRaises(SchemaPendingError) as ctx:
                    self.move(id_fornecedor=1)
                self.assertEqual(str(ctx.exception), SCHEMA_MESSAGE)
                with self.assertRaises(SchemaPendingError) as ctx:
                    ProdutoService(SupplierConnection(self.db)).registrar_produto(
                        dict(PAYLOAD, codigo='NOVO', id_posicao=1, lote='L', id_fornecedor=1), 23)
                self.assertEqual(str(ctx.exception), SCHEMA_MESSAGE)
        self.assertEqual(self.state(), antes)

    def test_baseline_entrada_and_history_never_select_new_columns(self):
        conn = SupplierConnection(self.db)
        r = self.move(amount=3, conn=conn)
        self.assertEqual([r[c] for c in CAMPOS_FORNECEDOR], [None, None, None])
        leitura = SupplierConnection(self.db)
        historico = MovimentacaoService(leitura).listar(23)
        self.assertEqual(len(historico), 1)
        for c in (conn, leitura):
            self.assertFalse([q for q, _ in c.queries if 'fornecedor' in q and q != DETECCAO])
        self.no_ddl(conn, leitura)

    def test_detection_requires_table_and_all_movement_columns(self):
        self.assertIn("to_regclass('public.fornecedores')", DETECCAO)
        for coluna in CAMPOS_FORNECEDOR:
            self.assertIn(f"'{coluna}'", DETECCAO)
        self.assertIn(') = 3', DETECCAO)
        self.assertFalse(re.search(r'\b(CREATE|ALTER|DROP)\b', DETECCAO))


# ── Contratos 2F preservados (D9) ────────────────────────────────────────────

class RelatoriosPreservadosTests(Base):
    def test_report_serialization_excludes_supplier_fields_explicitly(self):
        r = self.move(amount=2, id_fornecedor=1)
        historico = MovimentacaoService(SupplierConnection(self.db)).listar(23)[0]
        movimento = MovimentacaoService(SupplierConnection(self.db)).repository.listar(23)[0]
        self.assertEqual(set(historico), CAMPOS_MOVIMENTACAO | set(CAMPOS_FORNECEDOR))
        relatorio = relatorios_service._movimentacao(movimento)
        self.assertEqual(set(relatorio), CAMPOS_MOVIMENTACAO)
        self.assertEqual({k: v for k, v in historico.items() if k in CAMPOS_MOVIMENTACAO}, relatorio)
        self.assertEqual(r['fornecedor_cnpj'], CNPJ_A)

    def test_report_sql_and_count_application_never_reference_supplier(self):
        for arquivo in ('repository/relatorios_repository.py', 'repository/contagem_repository.py',
                        'schemas/relatorios_dto.py'):
            self.assertNotIn('fornecedor', (BACKEND / arquivo).read_text(encoding='utf-8'))
        from repository.movimentacao_repository import select_historico
        self.assertNotIn('fornecedor', select_historico(True))
        self.assertIn('m.fornecedor_cnpj', select_historico(True, True))


# ── HTTP: autenticação, permissões aprovadas (D7), erros ─────────────────────

class FornecedorHttpTests(TestCase):
    ROTAS = (('get', '/fornecedores', None), ('get', '/fornecedores/1', None),
             ('post', '/fornecedores', {}), ('put', '/fornecedores/1', {}), ('delete', '/fornecedores/1', None),
             ('get', '/fornecedores/1/recebimentos', None))

    def setUp(self):
        self.db = SupplierDB()
        self.conn = SupplierConnection(self.db)
        with patch('app.Connection.get_connection', return_value=self.conn):
            self.app = create_app()
        self.app.session_interface = SecureCookieSessionInterface()
        self.client = self.app.test_client()
        self.addCleanup(self.conn.rollback)

    def login(self, role='ADMINISTRADOR'):
        self.db.role = role
        with self.client.session_transaction() as s:
            s.update(user_id=23)

    def call(self, method, path, body=None):
        return getattr(self.client, method)(path, json=body) if body is not None else getattr(self.client, method)(path)

    def test_no_session_is_401_everywhere(self):
        for method, path, body in self.ROTAS:
            with self.subTest(path=path, method=method):
                self.assertEqual(self.call(method, path, body).status_code, 401)
        self.assertEqual(self.conn.queries, [])

    def test_approved_matrix_for_canonical_profiles(self):
        # Correção de acessos (pedido posterior do usuário, 05/10/2026): resultado esperado alterado - OPERADOR: 403 na consulta e nos recebimentos.
        self.assertEqual({p for p in PERFIS if pode(p, 'fornecedores:consultar')}, set(PERFIS) - {'OPERADOR'})
        self.assertEqual({p for p in PERFIS if pode(p, 'fornecedores:gerenciar')}, {'GESTOR', 'ADMINISTRADOR'})
        for indice, perfil in enumerate(PERFIS):
            with self.subTest(perfil=perfil):
                self.login(perfil)
                consulta = 403 if perfil == 'OPERADOR' else 200
                self.assertEqual(self.client.get('/fornecedores').status_code, consulta)
                self.assertEqual(self.client.get('/fornecedores/1/recebimentos').status_code, consulta)
                gerencia = perfil in ('GESTOR', 'ADMINISTRADOR')
                r = self.client.post('/fornecedores', json=dict(razao_social='Novo ' + perfil, cnpj=f'7777777700{indice}177'))
                self.assertEqual(r.status_code, 201 if gerencia else 403)
                r = self.client.put('/fornecedores/2', json=dict(razao_social='Beta S.A.', cnpj=CNPJ_B, ativo=True))
                self.assertEqual(r.status_code, 200 if gerencia else 403)
        self.assertEqual(len(self.db.suppliers), 4)

    def test_operator_receives_with_active_supplier_auditor_cannot(self):
        corpo = dict(tipo='ENTRADA', quantidade=2, id_posicao=1, lote='L', id_fornecedor=1)
        self.login('OPERADOR')
        r = self.client.post('/produtos/7/movimentacoes', json=corpo)
        self.assertEqual((r.status_code, r.json['fornecedor_razao_social']), (201, 'Alfa Ltda'))
        self.login('AUDITOR')
        self.assertEqual(self.client.post('/produtos/7/movimentacoes', json=corpo).status_code, 403)
        self.login('OPERADOR')
        self.db.suppliers[1][4] = False
        self.assertEqual(self.client.post('/produtos/7/movimentacoes', json=corpo).status_code, 409)
        self.assertEqual(self.client.post('/produtos/7/movimentacoes', json=dict(corpo, id_fornecedor=99)).status_code, 404)
        r = self.client.post('/produtos/7/movimentacoes', json=dict(tipo='SAIDA', quantidade=1, id_posicao=1, lote='L',
                                                                    id_fornecedor=2))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(self.db.movements), 1)

    def test_partial_permissions_on_receipts_denied_by_decorator(self):
        # Decorator isolado (requer.todas): o service nem é chamado; a revalidação do repository
        # é coberta à parte, para que uma não mascare a regressão da outra.
        self.login('AUDITOR')
        for parcial in ({'fornecedores:consultar'}, {'movimentacoes:consultar'}):
            with self.subTest(parcial=parcial),                     patch.dict('core.permissoes.PERMISSOES', {'AUDITOR': frozenset(parcial)}),                     patch.object(FornecedorService, 'recebimentos', return_value={}) as servico:
                self.assertEqual(self.client.get('/fornecedores/1/recebimentos').status_code, 403)
                self.assertFalse(servico.called)
        with patch.object(FornecedorService, 'recebimentos', return_value={'itens': []}) as servico:
            self.assertEqual(self.client.get('/fornecedores/1/recebimentos').status_code, 200)
        self.assertTrue(servico.called)

    def test_revocation_applies_on_next_request(self):
        self.login('GESTOR')
        corpo = dict(razao_social='Primeiro', cnpj='88888888000188')
        self.assertEqual(self.client.post('/fornecedores', json=corpo).status_code, 201)
        self.db.role = 'AUDITOR'  # rebaixado sem novo login
        self.assertEqual(self.client.post('/fornecedores', json=dict(corpo, cnpj='99999999000199')).status_code, 403)
        self.assertEqual(self.client.get('/fornecedores').status_code, 200)
        # Correção de acessos (pedido posterior do usuário, 05/10/2026): resultado esperado alterado - rebaixado a OPERADOR perde também a consulta.
        self.db.role = 'OPERADOR'
        self.assertEqual(self.client.get('/fornecedores').status_code, 403)
        self.db.active = False
        r = self.client.get('/fornecedores')
        self.assertEqual((r.status_code, r.json), (403, {'message': 'Usuário inativo'}))
        self.assertEqual(self.client.get('/fornecedores').status_code, 401)

    def test_repository_revalidates_after_decorator(self):
        self.login('OPERADOR')
        with patch.object(UsuarioRepository, 'obter_acesso', return_value=(True, 'ADMINISTRADOR')):
            r = self.client.post('/fornecedores', json=dict(razao_social='X', cnpj='10101010000110'))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(len(self.db.suppliers), 2)

    def test_forged_inputs_grant_nothing(self):
        self.login('OPERADOR')
        corpo = dict(razao_social='X', cnpj='20202020000120')
        self.assertEqual(self.client.post('/fornecedores?perfil=ADMINISTRADOR&admin=true', json=corpo).status_code, 403)
        # Correção de acessos (pedido posterior do usuário, 05/10/2026): resultado esperado alterado - OPERADOR nem chega à validação (403).
        self.assertEqual(self.client.get('/fornecedores?perfil=ADMINISTRADOR').status_code, 403)
        self.login('AUDITOR')
        self.assertEqual(self.client.get('/fornecedores?perfil=ADMINISTRADOR').status_code, 400)
        self.login('GESTOR')
        self.assertEqual(self.client.post('/fornecedores', json=dict(corpo, admin=True)).status_code, 400)
        self.assertEqual(self.client.post('/fornecedores', json=dict(corpo, id_fornecedor=50)).status_code, 400)
        self.assertEqual(len(self.db.suppliers), 2)

    def test_status_codes_and_generic_500(self):
        self.login()
        self.assertEqual(self.client.get('/fornecedores/99').status_code, 404)
        self.assertEqual(self.client.post('/fornecedores', json=dict(razao_social='X', cnpj=CNPJ_A)).status_code, 409)
        self.assertEqual(self.client.post('/fornecedores', data='{', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post('/fornecedores', json=dict(razao_social='X', cnpj='1')).status_code, 400)
        self.assertEqual(self.client.get('/fornecedores?page_size=101').status_code, 400)
        self.assertEqual(self.client.patch('/fornecedores/1', json={}).status_code, 405)
        self.assertEqual(self.client.delete('/fornecedores').status_code, 405)
        self.assertEqual(self.client.put('/fornecedores/1/recebimentos', json={}).status_code, 405)
        with patch.object(FornecedorService, 'listar', side_effect=RuntimeError('detalhe interno sigiloso')):
            r = self.client.get('/fornecedores')
        self.assertEqual((r.status_code, r.json), (500, {'message': 'Erro interno do servidor'}))
        r = self.client.get('/fornecedores/1')
        self.assertEqual(set(r.json), {'id_fornecedor', 'razao_social', 'cnpj', 'cnpj_formatado', 'contato', 'ativo'})

    def test_delete_flow_over_http(self):
        self.login('GESTOR')
        self.client.post('/produtos/7/movimentacoes', json=dict(tipo='ENTRADA', quantidade=1, id_posicao=1,
                                                                 lote='L', id_fornecedor=1))
        r = self.client.delete('/fornecedores/1')
        self.assertEqual(r.status_code, 409)
        self.assertIn('Inative-o', r.json['message'])
        self.assertEqual(self.client.delete('/fornecedores/2').status_code, 200)
        self.assertEqual(self.client.delete('/fornecedores/2').status_code, 404)

    def test_schema_pending_is_503_over_http_and_baseline_works(self):
        self.db.supplier_schema = False
        self.login()
        for method, path, body in self.ROTAS:
            with self.subTest(path=path, method=method):
                body = dict(razao_social='X', cnpj=CNPJ_A, ativo=True) if body is not None else None
                self.assertEqual(self.call(method, path, body).status_code, 503)
        corpo = dict(tipo='ENTRADA', quantidade=1, id_posicao=1, lote='L')
        self.assertEqual(self.client.post('/produtos/7/movimentacoes', json=dict(corpo, id_fornecedor=1)).status_code, 503)
        r = self.client.post('/produtos/7/movimentacoes', json=corpo)
        self.assertEqual((r.status_code, r.json['id_fornecedor']), (201, None))
        self.assertEqual(self.client.get('/movimentacoes').status_code, 200)
