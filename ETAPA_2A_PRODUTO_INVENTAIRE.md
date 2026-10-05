# Etapa 2A — Produto e banco Inventaire

Data: 26/09/2026. Escopo: exclusivamente este workspace.

## Resultado e estado inicial

A migração de código **EPI → Produto está implementada**, incluindo schema novo, backend, frontend e testes. O servidor inicia em `http://127.0.0.1:5000`, com usuários/autenticação e `/produtos`. `/epis` deixou de ser registrado e não há alias.

**PostgreSQL permanece pendente. Nenhum banco, tabela ou usuário foi criado.** A inspeção restrita da configuração encontrou `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_NAME` e `DB_PASSWORD` ausentes em `backend/.env` e no processo. O usuário respondeu: “Continuar e documentar PostgreSQL pendente”. Não foram presumidos host, porta, usuário, senha, nome efetivo da base ou método alternativo de autenticação.

Antes desta etapa, o Flask mínimo já iniciava sem visão computacional e Redis/Flask-Session já haviam sido validados. O inventário ainda dependia de `Epi`, `/epis`, `certificado`, `em_uso` e tabela `epis`; código/localização estavam desabilitados na interface. PostgreSQL e login persistido não estavam homologados.

Foram lidos integralmente os três relatórios históricos, os dois READMEs e a cadeia Frontend → API → Controller → Service → Repository → Model/DTO → SQL antes da primeira edição. Não existe `AGENTS.md` neste workspace. A pasta não é um repositório Git. A comparação de alterações usa hashes iniciais, sem copiar conteúdos de `.env`.

## Decisão sobre SQL legado

Foram analisados os quatro arquivos originais de `backend/assets/`:

| Arquivo | Constatação e decisão |
| --- | --- |
| `tabelas_spi-postgres.sql` | Dez tabelas, nove comandos iniciais `DROP TABLE ... CASCADE` e dois índices ligados a estatísticas. Preservado, jamais executado. Excessivamente acoplado ao domínio antigo. |
| `inserções_spi-postgres.sql` | Seeds de usuários e estruturas de visão, IDs relacionais fixos e consulta de alertas. Literais de dados foram omitidos na inspeção exibida. Nenhum insert/hash/credencial foi copiado ou usado para login. |
| `modelo_DB.mwb` | Arquivo ZIP de MySQL Workbench; XML inspecionado sem executar o modelo. Nove tabelas, tipos MySQL, diferenças em tamanho de nome (`36` versus `40` no DDL PostgreSQL), identidade e campos de câmeras/alertas. Não adotado como fonte do schema PostgreSQL. |
| `modelo_DB.mwb.bak` | Modelo de referência também inspecionado; contém a mesma estrutura legada de nove tabelas. Preservado. |

Reaproveitados: contrato de `usuarios`, tipos/tamanhos do DDL PostgreSQL, identity, PK, unicidade do email, booleano de atividade, timestamp de acesso e perfil/unidade textuais. O nome `usuarios_email_key` permanece compatível com o tratamento de duplicidade no repository. Não existe necessidade de tabelas próprias de perfis/unidades ou relação com setores nesta etapa.

Excluídos **somente do novo schema**: EPIs, setores, responsabilidades industriais, câmeras, zonas, monitoramento, alarmes, alertas, estatísticas de detecção e seus relacionamentos/índices. Visão, curadoria e Active Learning não integram o modelo novo. Nenhum arquivo antigo foi apagado.

Criado apenas `backend/assets/inventaire_schema.sql`: DDL transacional, sem reset, sem seed e sem execução automática. Antes do CREATE, há proteção para bancos reservados/identificados como SPI e objetos de aplicação existentes (schemas, relações, funções e tipos). Não substitui a conferência explícita de host, porta, base e exclusividade. A reexecução deve falhar e preservar o conteúdo existente; não usa `IF NOT EXISTS` para ocultar incompatibilidades.

## Schema novo

**Definido em arquivo, ainda não aplicado nem validado por PostgreSQL real.** Destino recomendado: base exclusiva `inventaire`. Outro nome só pode ser usado se explicitamente escolhido pelo usuário.

### usuarios

| Campo | Tipo e constraints | Finalidade |
| --- | --- | --- |
| `id_usuario` | INTEGER GENERATED ALWAYS AS IDENTITY, PRIMARY KEY | Identificador |
| `nome` | VARCHAR(40), NOT NULL | Nome |
| `sobrenome` | VARCHAR(90), NOT NULL | Sobrenome |
| `email` | VARCHAR(60), NOT NULL, UNIQUE `usuarios_email_key` | Login; DTO atual normaliza para minúsculas |
| `senha` | VARCHAR(255), NOT NULL, CHECK de formato bcrypt | Hash; aplicação/bootstrap calculam bcrypt, nunca texto puro |
| `perfil` | VARCHAR(20), NOT NULL | Perfil usado pela autorização existente |
| `unidade` | VARCHAR(45), nullable | Unidade administrativa textual |
| `telefone` | VARCHAR(45), nullable | Contato |
| `ativo` | BOOLEAN, NOT NULL, DEFAULT TRUE | Controle ativo/inativo |
| `acesso` | TIMESTAMP sem fuso, nullable | Último login; contrato existente preservado |

O check de senha verifica formato bcrypt `$2a$`, `$2b$` ou `$2y$`; não substitui a geração/verificação criptográfica pelo bcrypt. Não há usuário ou senha real no DDL.

### produtos

| Campo | Tipo e constraints | Finalidade |
| --- | --- | --- |
| `id_produto` | INTEGER GENERATED ALWAYS AS IDENTITY, PRIMARY KEY | Identificador retornado pela API |
| `nome` | TEXT, NOT NULL, CHECK não vazio/branco | Nome do produto |
| `categoria` | TEXT, NOT NULL, CHECK não vazio/branco | Classificação textual |
| `codigo` | TEXT, NOT NULL, CHECK não vazio e sem espaços externos, UNIQUE `produtos_codigo_key` | SKU operacional fornecido pelo usuário |
| `localizacao` | TEXT, NOT NULL, CHECK não vazio/branco | Local de armazenamento simples |
| `validade` | DATE, nullable | Validade opcional |
| `estoque` | INTEGER, NOT NULL, DEFAULT 0, CHECK >= 0 | Saldo administrativo atual |
| `quantidade_min` | INTEGER, NOT NULL, DEFAULT 0, CHECK >= 0 | Limiar de estoque baixo |

SKU é único e sensível a maiúsculas: `CX-1` e `cx-1` são distintos. Não deriva de certificado nem é gerado automaticamente. A aplicação remove espaços externos de textos. Não há `certificado`, `em_uso`, tabela `epis`, relacionamento com visão, movimentações ou auditoria.

São esperadas duas sequências identity e quatro índices automáticos (duas PKs e duas UNIQUEs). Nenhum índice de estatísticas foi reaproveitado. Não há FK entre usuários/produtos nesta etapa. Quantidades usam o tipo nativo INTEGER do PostgreSQL, com seu limite técnico de representação; não foi criada uma faixa de negócio adicional.

## Arquivos modificados

Nove arquivos existentes modificados; nenhum original excluído:

| Arquivo | Alteração |
| --- | --- |
| `backend/app.py` | Registra `produto_bp` em lugar de `epi_bp`; preserva Redis, conexão preguiçosa, autenticação e CORS. |
| `js/api.js` | Identifica Inventaire e expõe `apiProduto` com cinco helpers para `/produtos`, usando a camada HTTP com credenciais existente. |
| `js/inventario.js` | Consome `id_produto`, SKU, localização, validade opcional e helpers novos; remove campos/identificadores de EPI e indicador “em uso”; mantém CRUD, pesquisa, paginação, gráficos, carregamento, estoque baixo e CSV. |
| `inventario.html` | Habilita SKU/localização obrigatórios; validade opcional; remove certificado/em uso; tabela mostra mínimo; pesquisa inclui SKU/localização. |
| `js/common.js` | Em HTTP 409, apresenta a mensagem de conflito devolvida pela API, permitindo explicar SKU duplicado. O texto anterior afirmava incorretamente haver vínculos. |
| `backend/tests/test_core.py` | Migra regressões do inventário e composição para Produto; conserva autenticação, usuários e conexão. |
| `backend/tests/test_redis_session.py` | Migra endpoints/repository das verificações de sessão para Produto; mantém os cinco cenários Redis. |
| `README.md` | Documenta Produto, configuração, schema, bootstrap, execução, testes e limites de homologação. |
| `backend/README.md` | Passa a descrever o backend ativo, contratos e instalação segura; SQL antigo identificado como referência. |

## Arquivos criados

Nove arquivos de entrega:

| Arquivo | Finalidade |
| --- | --- |
| `backend/models/produtos.py` | Dataclass Produto tipada, com data opcional. |
| `backend/schemas/produto_dto.py` | Validação de textos, inteiros não negativos, booleanos, data e payload. |
| `backend/repository/produto_repository.py` | CRUD parametrizado com colunas explícitas, commit/rollback, retornos previsíveis e conflito específico de SKU. |
| `backend/services/produto_service.py` | Validação e serialização única de Produto, data ISO/null, sem HTTP/SQL. |
| `backend/controller/produto_routes.py` | Cinco endpoints, autorização e respostas JSON coerentes. |
| `backend/assets/inventaire_schema.sql` | Schema seguro de instalação em base nova. |
| `backend/bootstrap_admin.py` | Bootstrap manual com destino explícito, confirmação por nome, dados fornecidos interativamente e bcrypt; senha lida sem eco. Não executado contra banco. |
| `backend/tests/test_produtos.py` | 30 testes isolados adicionais de Produto e proteção de configuração do bootstrap. |
| `ETAPA_2A_PRODUTO_INVENTAIRE.md` | Este relatório. |

Artefatos auxiliares locais, ignorados por `.gitignore`, em `.validation/`: `etapa2a_initial_hashes.json`, `etapa2a_legacy_scan.json`, `etapa2a_http.py`, `etapa2a_browser.cjs` e perfis isolados `etapa2a_chrome_*`. Os scripts HTTP/navegador documentam as verificações, sem credenciais. Todos os processos de teste foram encerrados. Esses artefatos não são dependências de execução da aplicação.

## Migração EPI → Produto

| Antes | Depois |
| --- | --- |
| `models/epis.py`, `Epi` | `models/produtos.py`, `Produto` |
| `schemas/epi_dto.py`, `EpiDTO` | `schemas/produto_dto.py`, `ProdutoDTO` |
| `repository/epi_repository.py`, `EpiRepository` | `repository/produto_repository.py`, `ProdutoRepository` |
| `services/epi_service.py`, `EpiService` | `services/produto_service.py`, `ProdutoService` |
| `controller/epi_routes.py`, `epi_bp` | `controller/produto_routes.py`, `produto_bp` |
| `/epis` | `/produtos`; endpoint antigo retorna 404 |
| `epis.id_epi`, JSON `id` | `produtos.id_produto`, JSON `id_produto` |
| Chamadas genéricas com `/epis`, `apiEpi`, `savedEpi`, `data-delete-epi` | Helpers `apiProduto`, variável `produto`, `savedProduto`, `data-delete-produto` |
| `certificado` | Retirado; `codigo` é um novo campo/SKU independente, sem migração de certificados |
| `em_uso` | Retirado, sem conversão em saída/movimentação |
| Localização indisponível | `localizacao` textual obrigatória |
| Validade obrigatória | Omitida/null permitida; preenchida exige `YYYY-MM-DD`; datas passadas permitidas |

GET retorna lista/objeto; POST retorna 201; PUT/DELETE retornam 200. Produto inexistente em GET/PUT/DELETE retorna 404. Erros usam `message`: 400 validação/JSON, 401 sem sessão, 403 sem autorização, 409 duplicidade de SKU, 500 genérico. Leituras exigem sessão ativa; mutações mantêm admin/supervisor. Erros internos não são convertidos em validação nem divulgam SQL/dados/credenciais.

O Dashboard permaneceu inalterado e sem métricas novas. Não foram implementadas entradas, saídas, transferências, auditoria, múltiplos depósitos, fornecedores, pedidos ou códigos de barras.

## Banco e bloqueio atual

| Item | Resultado |
| --- | --- |
| Host | Não configurado; nenhum destino presumido |
| Porta | Não configurada; nenhuma conexão tentada usando default |
| Usuário PostgreSQL | Não configurado |
| Nome efetivo da base | Não configurado; `inventaire` é o nome proposto, não uma base existente confirmada |
| Categoria do bloqueio | Configuração ausente; não é erro de senha nem falta de permissão comprovada |
| Conexão autenticada | Não testada |
| Existência/exclusividade/conteúdo da base | Não verificados no servidor |
| CREATE DATABASE / CREATE TABLE | Não executados |
| Schema | Criado em arquivo; não aplicado |
| SELECT/INSERT/UPDATE/DELETE e constraints PostgreSQL | Não testados no servidor |
| Login e usuário persistido | Não testados; nenhum administrador automático |

Nenhum SQL foi enviado ao PostgreSQL, incluindo consultas de diagnóstico. Nenhum banco `postgres` foi usado como banco da aplicação, nenhum banco SPI foi acessado/alterado e nenhuma credencial histórica/default foi tentada.

## Testes

### Automatizados Python

Comando em `backend/`:

```powershell
$env:INVENTAIRE_TEST_REDIS = '1'
..\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

**58 testes aprovados, zero falhas e zero pulados** na execução com Redis habilitado:

| Conjunto | Quantidade | Natureza e resultado |
| --- | ---: | --- |
| `test_core.py` | 23 | Isolado/mock: autenticação, bcrypt, DTOs de usuário/Produto, repository, conexão e composição. Aprovados. |
| `test_produtos.py` | 30 | Isolado/mock: 5 DTO, 8 repository, 5 service, 10 HTTP e 2 guardas do bootstrap. Aprovados. |
| `test_redis_session.py` | 5 | Redis real, usuário/repository isolados. Sessão, TTL, recuperação/rotação, logout, revogação por inatividade, validação e restrição de perfil. Aprovados. |

Subcasos cobrem campos vazios/tipos errados, estoque/mínimo negativos, booleanos como inteiros, data inválida, localização inválida, SKU vazio/duplicado, JSON inválido, ausência de produto, parâmetros SQL, rollback inclusive em leitura/falha de commit, erro interno genérico, leitores, administradores, supervisores e usuários inativos. Não são testes de persistência PostgreSQL.

Redis manteve a interface RedisSessionInterface da aplicação. Cada teste opt-in usa prefixo UUID próprio e verifica a remoção de suas chaves, sem FLUSHDB/FLUSHALL ou alteração de chaves desconhecidas. A sessão de cookie usada nos testes isolados de Produto pertence somente ao app construído para esses testes, sem modificar a composição real.

Flask-Session emite aviso de depreciação de `SESSION_USE_SIGNER`; a configuração foi preservada nesta etapa. Os logs de RuntimeError durante os cenários de falha são esperados; os testes confirmam HTTP 500 genérico.

### HTTP real

**15 verificações aprovadas** contra um processo real `backend/app.py`, iniciado em `127.0.0.1:5000` e encerrado ao final:

- GET `/session`, `/users`, `/produtos`, `/produtos/1`: 401 sem sessão.
- POST/PUT/DELETE de produtos: 401 sem sessão, sem alcançar banco.
- Três corpos inválidos em `/login`: 400.
- `/epis`, `/movimentacoes`, `/estatisticas`: 404.
- POST `/logout` sem sessão: 200.
- OPTIONS `/produtos`: 200, origem local e credenciais CORS confirmadas.

Isso comprova inicialização, roteamento e barreiras HTTP, **não** login/CRUD PostgreSQL.

### Navegador

**24 verificações aprovadas no Chrome headless com API simulada**, sem consultas ao banco:

- Lista vazia, carregamento e estado de API indisponível.
- Campos SKU/localização obrigatórios, validade opcional, payload exato e `id_produto`.
- Cadastro, edição, exclusão, preenchimento do formulário e liberação do botão após salvar.
- Pesquisa por SKU/localização e pesquisa sem resultado.
- Estoque baixo, validade passada aceita e status calculado.
- CSV com SKU, localização e mínimo.
- Conflito de SKU preserva formulário e exibe a mensagem correta.
- Restrição dos botões de escrita para operador e cookies `credentials: include`.
- Nenhuma chamada a endpoints legados.

CDNs foram bloqueadas nesse teste: o fallback de Chart.js foi exercitado; os dados dos gráficos foram conferidos com doubles. **Renderização real de Chart.js e ícones CDN não foi homologada.** O primeiro lançamento do ambiente de navegador não respondeu ao CDP; a execução concluída usou perfil temporário exclusivo em `.validation/`, modo headless e `--no-sandbox`, somente com conteúdo local/API simulada.

### Outras verificações e não testados

- AST dos **36 arquivos Python**: aprovado.
- `node --check` nos **10 arquivos JavaScript**: aprovado.
- Blueprint ativo: somente `user_bp` e `produto_bp`.
- Ausentes dos módulos importados: `cv2`, `ultralytics`, `worker`, `events`, `flask_socketio`, `extensions`, módulos de câmeras e cadeia EPI.
- PostgreSQL real, aplicação do DDL, constraints, persistência CRUD, recuperação após erro SQL real: **não testados**.
- Login persistido, cookie/sessão/logout de usuário PostgreSQL e fluxo completo Login → Dashboard → Inventário/CRUD → Administração → Perfil → Configurações → Logout sem API simulada: **não testados**.

## Legado restante

Busca final por `EPI`, `EPIs`, `epi`, `epis`, `id_epi`, `/epis`, `apiEpi`, `savedEpi`, `certificado` e `em_uso`, sem imprimir conteúdo de arquivos de ambiente. A lista de linhas está em `.validation/etapa2a_legacy_scan.json`; a classificação por arquivo cobre todas as ocorrências do código/documentação inspecionados:

| Arquivo/grupo | Classificação e motivo |
| --- | --- |
| `backend/controller/epi_routes.py` | Legado desacoplado; não registrado/importado pelo app. Candidato à remoção após homologação. |
| `backend/services/epi_service.py` | Legado desacoplado; candidato à remoção posterior. |
| `backend/repository/epi_repository.py` | Legado desacoplado; suas queries nunca são usadas pelo Produto. Candidato à remoção posterior. |
| `backend/models/epis.py` | Model antigo preservado; sem import ativo. Candidato à remoção posterior. |
| `backend/schemas/epi_dto.py` | DTO antigo preservado; testes do domínio ativo já migrados. Candidato à remoção posterior. |
| Dois SQLs antigos e dois diagramas `backend/assets/` | Referência histórica preservada; sem execução. Diagramas analisados via XML, fora da busca textual comum por serem binários. |
| `DIAGNOSTICO_BACKEND_INVENTAIRE.md`, `REFATORACAO_INVENTAIRE.md`, `CORRECAO_BACKEND_INVENTAIRE.md` | Relatórios históricos íntegros, conferidos por hash; registram corretamente as etapas anteriores. |
| `README.md`, `backend/README.md`, este relatório | Explicação da migração e do legado; não são contratos ativos de EPI. |
| `js/dashboard.js` | Indicadores antigos de EPI, sem carregamento por HTML atual. Preservado como legado. |
| `js/api.js` | Ocorrências em chaves `visaoepi_session`, `visaoepi_profile`, `visaoepi_cache`: limpeza/compatibilidade técnica de armazenamento. O inventário usa somente `apiProduto`/`/produtos`, sem cache de EPI. Helpers de visão antigos continuam sem uso pelas telas atuais. |
| `js/theme.js`, `js/configuracao.js` | `visaoepi_theme` e evento de tema preservam preferência do navegador; não representam entidade/campo/endpoint EPI. |
| `backend/tests/test_produtos.py` | Referências intencionais que verificam ausência de imports `epi_` e resposta 404 de `/epis`. |
| `backend/bootstrap_admin.py` | Falso positivo da busca sem fronteira de palavra: “Repita a senha” contém a sequência `epi`. |
| `.validation/` | Scripts/artefatos de verificação, incluindo assertiva de `/epis` ausente; sem participação na aplicação. |

Não restou dependência do domínio EPI em `inventario.html`, `js/inventario.js`, `backend/app.py` nem em qualquer arquivo novo da cadeia Produto. Os nomes técnicos de armazenamento e configuração antigos não exigem tabelas ou serviços do projeto anterior. `js/notifications.js`, extensões/estatísticas legadas e requirements completos também permanecem desacoplados, conforme etapas anteriores.

## Pendências e próxima etapa

1. O usuário deve configurar localmente `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` e `DB_NAME=inventaire` (ou nome alternativo explícito) em `backend/.env`, preservando a SECRET_KEY atual e sem enviar senhas na conversa.
2. Confirmar host/porta/nome e que a base é exclusiva do Inventaire. Se existente, inspecionar somente por leitura os schemas, objetos e presença de dados; parar diante de conteúdo desconhecido.
3. Se necessário, criar a base com usuário PostgreSQL autorizado. Sem permissão de CREATE DATABASE, solicitar criação manual por responsável e aguardar, sem usar outra base.
4. Aplicar exclusivamente o DDL novo na base vazia confirmada. Verificar colunas, identity, PKs, unicidades, formato bcrypt, nulabilidade, checks e privilégios reais.
5. Usar um usuário de teste explicitamente fornecido ou executar manualmente o bootstrap após confirmação do destino. Ele não foi executado nesta entrega.
6. Validar SELECT/INSERT/UPDATE/DELETE, duplicidade de SKU/email, quantidades, datas, rollback e recuperação. Criar apenas registros próprios de teste e remover somente esses registros ao final.
7. Validar login, cookie Redis, `/session`, logout, sessão pós-logout e todas as telas com API real. Homologar também os gráficos com Chart.js disponível.

**Ainda não considero seguro avançar para a Etapa 2B como base homologada.** A refatoração de Produto está preparada e passou nas verificações disponíveis, mas PostgreSQL e a integração autenticada precisam ser concluídos antes de introduzir Movimentações de Estoque e Auditoria. Nenhuma funcionalidade da Etapa 2B foi implementada.

## Preservação e segurança

- Nenhuma SECRET_KEY ou senha de configuração foi mostrada, copiada para relatório ou substituída; arquivos `.env` preservados por hash.
- Nenhum SQL destrutivo executado; nenhum SQL enviado a PostgreSQL.
- Nenhum banco SPI alterado, nenhuma credencial presumida e nenhum usuário padrão criado.
- Nenhum commit, push ou Pull Request; a pasta continua sem repositório Git.
- Nenhum arquivo fora deste workspace foi alterado pelas ações de edição/geração da tarefa; scripts, perfis e temporários utilizados ficaram em `.validation/`.
- Nenhum arquivo original excluído, nenhuma dependência nova instalada e relatórios históricos/SQLs antigos preservados.
