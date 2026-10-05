# Inventaire

Sistema acadêmico para gestão de inventário e movimentação de depósito: produtos, entradas/saídas/ajustes auditáveis, localização física por lote, contagens cíclicas, perfis de acesso, reposição, relatórios e fornecedores. Frontend (HTML/CSS/JS sem framework) e backend (Flask + PostgreSQL + Redis) ficam neste mesmo repositório.

> **Estado real (04/10/2026)**
> - Etapas **2A a 2G homologadas** em ambiente real (Chrome → Flask → Redis → PostgreSQL), com as exceções registradas em cada relatório. Baseline atual: **2G.2** — 8 tabelas, perfis canônicos, schema 2G aplicado, 349 testes Python isolados aprovados na homologação.
> - **RNF01 (desempenho): PARCIAL.**
> - **"Saídas no período" NÃO é giro de estoque** (RF08 não atendido integralmente).
> - **TRANSFERÊNCIA entre posições não foi implementada.**
> - CNPJ de fornecedores é validado **somente no formato**.
> - **O projeto não está declarado integralmente concluído** e nem todos os requisitos estão atendidos (seção 15).
> - **Correção de perfil e acessos (05/10/2026), pedido posterior do usuário: implementada e coberta só por testes isolados, ainda SEM homologação real** ([relatório](CORRECAO_PERFIL_ACESSOS_INVENTAIRE.md)). Inclui Meu Perfil editável (nome, sobrenome, e-mail e telefone), OPERADOR sem Fornecedores e Relatórios e guarda de identidade entre abas.
> - A **instalação nova pelos dois SQLs oficiais ainda não foi validada num PostgreSQL real** (seção 5). Ela foi conferida só por revisão estática e testes isolados ([relatório de consolidação](CONSOLIDACAO_ENTREGA_INVENTAIRE.md)).

## Sumário

1. [Arquitetura](#1-arquitetura)
2. [Estrutura de diretórios](#2-estrutura-de-diretórios)
3. [Pré-requisitos e versões testadas](#3-pré-requisitos-e-versões-testadas)
4. [Dependências e configuração local](#4-dependências-e-configuração-local)
5. [Banco de dados: instalação nova](#5-banco-de-dados-instalação-nova)
6. [Usuários iniciais e senhas](#6-usuários-iniciais-e-senhas)
7. [Execução](#7-execução)
8. [Perfis e permissões (RF09)](#8-perfis-e-permissões-rf09)
9. [Funcionalidades](#9-funcionalidades)
10. [Endpoints](#10-endpoints)
11. [Contratos](#11-contratos)
12. [Transações, locks e invariantes](#12-transações-locks-e-invariantes)
13. [Testes e evidências](#13-testes-e-evidências)
14. [Scripts SQL históricos](#14-scripts-sql-históricos)
15. [Limitações e pendências](#15-limitações-e-pendências)

## 1. Arquitetura

```text
Páginas HTML/CSS/JS (sem framework)  ──fetch, credentials: include──▶  Flask (backend/app.py, 127.0.0.1:5000)
   js/api.js · js/common.js (permissões vindas de /session)                │
                                                                          ├─ Model → DTO → Repository → Service → Controller
                                                                          ├─ PostgreSQL (psycopg2, sem ORM; banco inventaire)
                                                                          └─ Redis + Flask-Session (sessão de 30 min)
```

- **Frontend estático:** Login, Dashboard, Inventário, Movimentações, Localizações, Contagens, Fornecedores, Relatórios, Administração, Configurações e Meu Perfil. `js/api.js` centraliza as chamadas (URL base `http://localhost:5000`, cookies com `credentials: include`).
- **Backend:** `backend/app.py` registra 7 blueprints: `user_bp`, `produto_bp`, `movimentacao_bp`, `rastreamento_bp`, `contagem_bp`, `relatorio_bp` e `fornecedor_bp`. Cada módulo segue **Model → DTO (`schemas/`) → Repository → Service → Controller**.
- **Banco:** PostgreSQL acessado com SQL parametrizado (psycopg2, sem ORM). A conexão é preguiçosa, por contexto Flask, com autocommit desligado, e o teardown faz rollback e fechamento. O isolamento padrão é READ COMMITTED; relatórios e leituras de fornecedores usam REPEATABLE READ READ ONLY.
- **Sessão:** Redis, 30 minutos, cookie HttpOnly e SameSite=Lax, Secure fora do desenvolvimento. O login grava só a identidade; `/session` relê o usuário no banco.
- **Nenhum SQL, migração ou bootstrap é executado ao iniciar o servidor.**
- **Legado do SPI:** os módulos de EPI, câmeras, visão computacional, Socket.IO e estatísticas (`epi_*`, `estatisticas_*`), assim como os decorators `login_required`/`perfil_required`, **não estão registrados** e não são funcionalidades ativas. Os arquivos permanecem só como histórico.

| Módulo | Arquivos principais |
| --- | --- |
| Usuários/autorização | `core/permissoes.py` (fonte única da matriz), `core/auth.py` (`requer`, `requer.todas`), `*/usuario*` |
| Produto | `*/produto*` |
| Movimentações | `*/movimentacao*`; `MovimentacaoRepository.aplicar_no_cursor` é a única rotina que altera quantidades |
| Rastreamento (RF04) | `*/rastreamento*` (Corredor, PosicaoEstoque, ItemEstoque) |
| Contagens (RF11) | `*/contagem*` |
| Relatórios (RF06/RF08/RF10) | `*/relatorios*` (somente leitura) |
| Fornecedores (RF12) | `models/fornecedores.py`, `schemas/fornecedor_dto.py`, `repository/fornecedor_schema.py` (detecção do schema e lock do recebimento), `repository/fornecedor_repository.py`, `services/fornecedor_service.py`, `controller/fornecedor_routes.py` |

## 2. Estrutura de diretórios

```text
./                         Páginas *.html do frontend, README e relatórios das etapas (*.md)
├─ js/  css/  assets/      Scripts, estilos e imagens do frontend
├─ backend/
│  ├─ app.py               Composição Flask (blueprints, sessão, CORS)
│  ├─ controller/ services/ repository/ schemas/ models/ core/ connection/
│  ├─ tests/               Testes isolados (unittest), sem PostgreSQL real
│  ├─ assets/
│  │  ├─ 01_estrutura_inventaire.sql      SQL oficial: estrutura completa (instalação nova)
│  │  ├─ 02_dados_iniciais_inventaire.sql SQL oficial: modelo dos 4 usuários iniciais
│  │  └─ historico/        Incrementais 2B–2G, schemas antigos e legado SPI (seção 14)
│  ├─ gerar_hash_senha.py  Gera localmente o hash bcrypt de uma senha (não conecta ao banco)
│  ├─ bootstrap_admin.py   Alternativa histórica/manual para criar um administrador
│  ├─ requirements-core.txt  Dependências do núcleo
│  ├─ requirements.txt     Manifesto legado/completo (não necessário)
│  └─ exemple.env          Modelo de configuração, sem valores reais
└─ .validation/            Evidências locais das homologações (não versionado)
```

## 3. Pré-requisitos e versões testadas

| Componente | Versão |
| --- | --- |
| Python | 3.13 (testado: **3.13.15**) |
| PostgreSQL | **12 ou superior** (coluna gerada); homologado em **18.6** |
| Redis | servidor local acessível por `REDIS_URL`; a versão não foi registrada nos relatórios |
| Navegador | Chrome (homologações reais) |
| Bibliotecas Python | `backend/requirements-core.txt`: Flask 3.1.3, Flask-CORS 6.0.5, Flask-Session 0.8.0, bcrypt 5.0.0, python-dotenv 1.2.3, psycopg2-binary 2.9.13, redis 8.1.0 |

## 4. Dependências e configuração local

Na raiz do repositório, no PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-core.txt
```

`backend/requirements.txt` é o manifesto legado/completo e não é necessário para o Inventaire.

**Configuração:** copie `backend/exemple.env` para `backend/.env` e preencha **localmente**:

| Variável | Uso |
| --- | --- |
| `SECRET_KEY` | Obrigatória (o servidor não inicia sem ela). Valor longo e aleatório |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` | Conexão PostgreSQL |
| `DB_NAME` | **`inventaire`**. O modelo traz `spi_database`, herdado do SPI: troque-o |
| `REDIS_URL` | Ex.: `redis://localhost:6379/0` |
| `DEV_INSECURE` | `true` só em desenvolvimento local: libera HTTP e as origens `localhost`/`127.0.0.1` |
| `CORS_ORIGINS` | Fora do modo de desenvolvimento: origens permitidas, separadas por vírgula |

As variáveis do processo têm precedência sobre `backend/.env`. As demais chaves do modelo (e-mail, broker, alertas) são do SPI e não são usadas pelo Inventaire.

> **Nunca publique `backend/.env`.** Ele está no `.gitignore`. Nada neste README exige valores reais versionados.

## 5. Banco de dados: instalação nova

Os **únicos SQLs de instalação** são:

| Ordem | Arquivo | O que faz |
| --- | --- | --- |
| 1 | [backend/assets/01_estrutura_inventaire.sql](backend/assets/01_estrutura_inventaire.sql) | Cria a estrutura final completa: 8 tabelas, constraints, FKs RESTRICT, índices e comentários. Não insere dados |
| 2 | [backend/assets/02_dados_iniciais_inventaire.sql](backend/assets/02_dados_iniciais_inventaire.sql) | Insere os 4 usuários iniciais, depois de preenchido localmente (seção 6) |

> ⚠️ **NÃO aplique esses dois arquivos na base `inventaire` já instalada e homologada.** Ela já tem a estrutura (2B, 2C, 2D, 2E e 2G aplicados; 2F sem SQL) e usuários reais. Nenhum SQL está pendente nela. O 01 aborta em base não vazia; mesmo assim, não o execute lá. Também **não reaplique** os incrementais de [backend/assets/historico/](backend/assets/historico/).

**Fluxo do avaliador (base nova):**

1. **Criar manualmente uma base vazia chamada `inventaire`**, por exemplo no psql ou no Query Tool do pgAdmin, conectado à base `postgres`:
   ```sql
   CREATE DATABASE inventaire ENCODING 'UTF8';
   ```
   Use um servidor/instância em que essa base ainda não exista. Os scripts não criam a base. Execute-os com o usuário dono da base, que tem permissão de criar objetos no schema `public`.
2. **Executar `01_estrutura_inventaire.sql`** conectado à base `inventaire`:
   ```powershell
   psql -h localhost -U <usuario> -d inventaire -v ON_ERROR_STOP=1 -f backend/assets/01_estrutura_inventaire.sql
   ```
   No pgAdmin: Query Tool na base `inventaire`, abrir o arquivo e executar **inteiro**.
3. **Preencher nomes, sobrenomes e hashes** numa cópia local do modelo 02 (seção 6).
4. **Executar a cópia preenchida do 02** na mesma base:
   ```powershell
   psql -h localhost -U <usuario> -d inventaire -v ON_ERROR_STOP=1 -f backend/assets/02_dados_iniciais_inventaire.local.sql
   ```
5. **Iniciar os serviços** (seção 7) e entrar com um dos quatro e-mails e a senha escolhida para ele.

Nesse fluxo **não execute `backend/bootstrap_admin.py`**: o administrador já vem do 02.

**Garantias do 01:**
- é transacional;
- confere `current_database() = 'inventaire'` e o PostgreSQL 12+;
- aborta se a base tiver qualquer objeto de aplicação;
- não usa `DROP`, `TRUNCATE`, `CASCADE` nem `IF NOT EXISTS`;
- não cria base nem extensão e não insere dados;
- confere ao final as 8 tabelas;
- não depende dos incrementais antigos.

**Garantias do 02:**
- é transacional;
- confere a base e a estrutura de `usuarios`;
- preserva os usuários já existentes (sem `UPDATE`/`DELETE`) e aborta se algum dos 4 e-mails já estiver cadastrado;
- valida as quatro configurações **antes** de inserir qualquer uma;
- insere os quatro usuários de forma atômica, com IDs gerados pela identity;
- não usa `ON CONFLICT`, `UPDATE` nem `DELETE`.

**Alternativa histórica (não usar no fluxo acima):** `.\.venv\Scripts\python.exe -B backend/bootstrap_admin.py --database inventaire` cria **um** administrador de forma interativa, com senha lida sem eco. Foi o caminho das etapas 2A–2G e continua disponível só para instalação manual sem o 02. Nunca é chamado pelo servidor e não serve como atualização.

## 6. Usuários iniciais e senhas

O 02 cria quatro contas, todas com `ativo = true` e perfil canônico:

| Perfil | E-mail |
| --- | --- |
| OPERADOR | operador@empresa.com |
| GESTOR | gestor@empresa.com |
| AUDITOR | auditor@empresa.com |
| ADMINISTRADOR | administrador@empresa.com |

Os e-mails são **contas de exemplo para avaliação**, não caixas postais verificadas. O login aceita qualquer e-mail cadastrado; não há envio de e-mail.

**Não existe senha padrão.** O repositório não contém senha, hash ou credencial compartilhada. Cada pessoa que instala escolhe as quatro senhas.

**Passo a passo:**

1. Copie o modelo para um nome ignorado pelo Git:
   ```powershell
   Copy-Item backend/assets/02_dados_iniciais_inventaire.sql backend/assets/02_dados_iniciais_inventaire.local.sql
   ```
2. Na **cópia**, no bloco `CONFIGURAÇÃO`, cada perfil tem três linhas, marcadas pelos comentários `-- Coloque o nome do usuário aqui.`, `-- Coloque o sobrenome do usuário aqui.` e `-- Coloque o hash bcrypt da senha deste usuário aqui.`:
   ```sql
   -- Coloque o nome do usuário aqui.
   nome_operador          TEXT := 'PREENCHER_NOME_OPERADOR';
   ```
   Substitua os 12 placeholders `PREENCHER_*` (nome, sobrenome e hash de OPERADOR, GESTOR, AUDITOR e ADMINISTRADOR). Mantenha as aspas simples. Escreva um apóstrofo duplicado (`'D''Ávila'`) e não deixe espaços nas pontas. Limites da tabela: nome até 40 caracteres e sobrenome até 90.
3. Gere **um hash por usuário**, localmente:
   ```powershell
   .\.venv\Scripts\python.exe -B backend/gerar_hash_senha.py
   ```
   O utilitário pede a senha e a confirmação **sem eco**. Ele usa a mesma rotina bcrypt da aplicação (custo 12) e imprime só o hash (`$2b$12$…`, 60 caracteres) neste terminal. Ele não conecta ao banco, não lê `.env`, não grava arquivo e não registra log. A senha precisa ter até 72 bytes UTF-8 e não pode ter espaço nas pontas, porque o login os remove. Cole cada hash na linha do respectivo perfil.
4. Execute a cópia (seção 5, passo 4). Enquanto restar qualquer placeholder, campo vazio, valor fora do limite, hash inválido (fora do formato da constraint `usuarios_senha_bcrypt_check` ou com custo fora de 04–31) ou hash repetido, o script **aborta sem inserir nenhum usuário** e lista o que corrigir.
5. Depois da instalação, **apague a cópia preenchida**.

**Mantendo a versão pública sem credenciais:**
- a cópia preenchida contém material de autenticação (hashes bcrypt) e **não deve ser publicada**;
- `*.local.sql` e `*.preenchido.sql` estão no `.gitignore`;
- o modelo versionado `02_dados_iniciais_inventaire.sql` deve continuar com os placeholders. Se ele for editado por engano, desfaça a edição antes de qualquer commit. O teste `backend/tests/test_sql_entrega.py` falha se encontrar um hash bcrypt no modelo.

## 7. Execução

**Backend** (127.0.0.1:5000, sem debug nem reloader), a partir da raiz:

```powershell
.\.venv\Scripts\python.exe -B backend/app.py
```

PostgreSQL e Redis precisam estar ativos. `app.py` carrega `backend/.env`.

**Frontend.** Sirva **somente os arquivos públicos**, numa origem permitida pelo CORS. **Não** publique a raiz do repositório com `python -m http.server`: isso exporia `backend/.env`, o código, os SQLs e `.validation/`. Um modo suportado, só com a biblioteca padrão:

```powershell
# Copia apenas o que é público para uma pasta separada e serve essa pasta.
$pub = "$env:TEMP\inventaire_public"
Remove-Item $pub -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $pub | Out-Null
Copy-Item *.html $pub; Copy-Item js, css, assets $pub -Recurse
.\.venv\Scripts\python.exe -m http.server 8080 --bind 127.0.0.1 --directory $pub
```

Abra `http://localhost:8080/login.html` (não use `file://`). Com `DEV_INSECURE=true` o CORS aceita `localhost`/`127.0.0.1` em qualquer porta.

## 8. Perfis e permissões (RF09)

Perfis canônicos persistidos: `OPERADOR`, `GESTOR`, `AUDITOR` e `ADMINISTRADOR`. A CHECK `usuarios_perfil_check` só aceita esses quatro. A API aceita aliases e grava sempre o canônico:
- `admin`/`administrador` → ADMINISTRADOR;
- `supervisor`/`gestor` → GESTOR;
- `operador` → OPERADOR;
- `auditor` → AUDITOR.

Perfil desconhecido não recebe permissão.

| Ação | OPER | GEST | AUD | ADM |
| --- | :-: | :-: | :-: | :-: |
| Consultar produtos, localizações, movimentações e contagens; registrar contagem; Dashboard | ✅ | ✅ | ✅ | ✅ |
| Editar os próprios dados pessoais em Meu Perfil (`perfil:editar_proprio`) | ✅ | ✅ | ✅ | ✅ |
| ENTRADA e SAIDA | ✅ | ✅ | ❌ | ✅ |
| Escolher fornecedor ativo na ENTRADA (`fornecedores:selecionar`, lista mínima) | ✅ | ✅ | ❌ | ✅ |
| AJUSTE manual; aplicar contagem; gerenciar produtos e localizações | ❌ | ✅ | ❌ | ✅ |
| Página e APIs de Relatórios, inclusive exportação (`relatorios:consultar`) | ❌ | ✅ | ✅ | ✅ |
| Página Fornecedores, consulta e recebimentos (`fornecedores:consultar`) | ❌ | ✅ | ✅ | ✅ |
| Cadastrar/editar/inativar/excluir fornecedores (`fornecedores:gerenciar`) | ❌ | ✅ | ❌ | ✅ |
| Administrar usuários | ❌ | ❌ | ❌ | ✅ |

**Permissões por perfil:**
- **Operacional (4 perfis):** `produtos:consultar`, `localizacoes:consultar`, `movimentacoes:consultar`, `contagens:consultar`, `contagens:registrar` e `perfil:editar_proprio`.
- **OPERADOR:** operacional + `movimentacoes:entrada|saida` + `fornecedores:selecionar`.
- **AUDITOR:** operacional + `fornecedores:consultar` + `relatorios:consultar`.
- **GESTOR:** AUDITOR + `produtos:gerenciar`, `localizacoes:gerenciar`, `movimentacoes:entrada|saida|ajuste`, `contagens:aplicar`, `fornecedores:gerenciar` e `fornecedores:selecionar`.
- **ADMINISTRADOR:** GESTOR + `usuarios:gerenciar`.

**Histórico da matriz:** `fornecedores:consultar` e `fornecedores:gerenciar` vieram da 2G. Em 05/10/2026, um **pedido posterior do usuário** mudou a matriz. Os relatórios 2E/2G **não foram reescritos**; eles registram a matriz da época. A mudança:
- **OPERADOR não acessa as páginas Fornecedores e Relatórios.** Os links saem do menu, as páginas somem da busca global, a URL direta é negada e as APIs respondem 403, inclusive `/relatorios/*/exportacao`.
- Ele mantém as consultas operacionais e o Dashboard (`/dashboard/resumo` não exige `relatorios:consultar`).
- No Dashboard do OPERADOR, os cards continuam, mas sem links para Relatórios.
- Novas permissões: `relatorios:consultar` (exigida **além** das permissões de domínio já existentes), `fornecedores:selecionar` e `perfil:editar_proprio`.

**Avaliação e garantias:**
- **Fonte única:** `backend/core/permissoes.py`. O frontend usa só as permissões calculadas pelo servidor (`GET /session` devolve `permissoes` e `perfil_reconhecido`); não há matriz no JavaScript.
- **Decorator:** a cada requisição, relê `ativo, perfil` no banco.
  - Sem sessão → 401.
  - Inativo ou removido → 403 "Usuário inativo"; a sessão é limpa e a requisição seguinte dá 401.
  - Sem permissão → 403.
- **Composição:** `@requer(a, b)` basta uma permissão; `@requer.todas(a, b)` exige todas. Sessão, query string e payload nunca concedem permissão.
- **Revalidação transacional:** `MovimentacaoRepository.autorizar_usuario` roda dentro da transação; nas escritas usa `SELECT … FOR SHARE` do próprio usuário. Rebaixamento ou inativação valem **na requisição seguinte**. Uma operação já autorizada sob lock conclui; não há cancelamento retroativo.
- **Proteções administrativas:** sem autoalteração de perfil ou atividade, sem autoexclusão e sempre ao menos um ADMINISTRADOR ativo.
- **Meu Perfil (edição própria):** os quatro perfis editam **apenas** nome, sobrenome, e-mail e telefone da própria conta, por `PATCH /me/perfil` (seção 11).
  - **Perfil/cargo, atividade e unidade são somente leitura.** Permissões e senha não são alteráveis por ali.
  - A administração de usuários (`/users`) continua exclusiva do ADMINISTRADOR.
  - O ADMINISTRADOR também edita os próprios dados pessoais; as proteções acima continuam valendo.
- **Sessão compartilhada entre abas (limitação):** abas e janelas do mesmo perfil do navegador compartilham **um** cookie de sessão. Um login em outra aba troca a identidade de todas.
  - A interface detecta a troca de três formas: evento de storage, nova leitura de `/session` ao voltar à aba e página restaurada do cache de voltar/avançar.
  - Ao detectar, a interface **bloqueia a página antiga e pede recarga**, sem exibir nem salvar dados da conta anterior.
  - Contas simultâneas exigem **contextos independentes** (outro perfil do navegador ou janela anônima). Não existe sessão independente por aba.
- **Respostas:** SQL parametrizado e erros JSON `{message}` sem detalhes internos. Nenhum segredo, hash, cookie ou ID de sessão aparece nas respostas.

## 9. Funcionalidades

| Área | Resumo |
| --- | --- |
| Produto | Cadastro, edição e exclusão (exclusão bloqueada com histórico). PUT não altera estoque, posição, lote nem fornecedor. POST e PUT recusam campos desconhecidos (400) |
| Movimentações (RF02/RF03) | ENTRADA, SAIDA e AJUSTE por lote/posição, com histórico imutável e saldos anterior/posterior. O responsável vem da sessão. Invariante `estoque = soma dos itens` |
| Localizações (RF04) | Corredor (capacidade em unidades) → Posição → ItemEstoque (lote, status DISPONIVEL/RESERVADO/BLOQUEADO) |
| Contagens (RF11) | Snapshot do saldo; divergência = físico − sistêmico. Aplicação explícita e única como AJUSTE vinculado; contagem desatualizada → 409 |
| Fornecedores (RF12) | Cadastro, pesquisa, paginação, edição, inativação/reativação e exclusão só sem recebimentos. Fornecedor **opcional** em cada ENTRADA (inclusive no estoque inicial), com **snapshot** de razão social e CNPJ. Consulta de recebimentos por fornecedor |
| Reposição (RF06) | Estoque baixo: `estoque <= quantidade_min`. Sugestão `max(0, mínimo − saldo)`; no limite mínimo não há "repor 0". Sem pedido nem compra automática |
| Dashboard e Relatórios (RF08/RF10) | Posição de estoque, reposição, movimentações, divergências e **Saídas no período (não é giro)** |

**Fornecedores e CNPJ:**
- Campos: razão social obrigatória (até 200 caracteres), CNPJ obrigatório e único, contato opcional (até 200) e ativo.
- **CNPJ:** o servidor remove `. / -` e espaços, converte para maiúsculas e exige `^[0-9A-Z]{12}[0-9]{2}$` (aceita o formato alfanumérico).
  - **É só validação de formato:** não verifica dígitos verificadores, existência nem situação cadastral.
  - Um CNPJ aceito não está fiscalmente validado.
- **Origem histórica:** cada ENTRADA guarda `id_fornecedor`, razão social e CNPJ do momento. Editar ou renomear o fornecedor não reescreve o histórico. Não existe fornecedor padrão no Produto.
- SAIDA e AJUSTE recusam `id_fornecedor` (400). Snapshots enviados pelo cliente → 400. Fornecedor inativo → 409 em novas ENTRADAs, mas continua consultável.
- **Seletor de fornecedor na ENTRADA (opção B aprovada em 05/10/2026):** a tela Movimentações usa `GET /fornecedores/selecao-entrada`, e não a API de cadastro.
  - Devolve só `id_fornecedor`, `razao_social` e `cnpj_formatado` de até 100 fornecedores **ativos**; acima disso, a tela avisa que a lista está parcial.
  - Exige `fornecedores:selecionar` e `movimentacoes:entrada`.
  - Assim o OPERADOR escolhe a origem sem acessar a página Fornecedores, o contato, os inativos ou os recebimentos.
  - O fornecedor já gravado numa ENTRADA continua visível a quem consulta movimentações (snapshot do histórico, via `movimentacoes:consultar`).
- Relatórios, Dashboard e CSV **não** foram estendidos com fornecedor (contratos 2F idênticos).

## 10. Endpoints

Erros: JSON `{"message"}` com 400 (validação), 401, 403, 404, 405, 409 (conflito/duplicidade/referência), 503 (schema pendente) e 500 genérico. Os logs registram apenas o tipo da exceção.

| Método | Rota | Permissão |
| --- | --- | --- |
| POST | `/login`, `/logout` | pública |
| GET | `/session` → usuário + `permissoes` + `perfil_reconhecido` (`Cache-Control: no-store`) | sessão |
| PATCH | `/me/perfil` `{nome?, sobrenome?, email?, telefone?}`: alvo = usuário da sessão | perfil:editar_proprio |
| GET | `/users`, `/users/ativos` | usuarios:gerenciar |
| POST / PUT | `/signup`, `/users` | usuarios:gerenciar |
| PATCH | `/users/<id>/acesso` `{perfil?, ativo?, unidade?}` | usuarios:gerenciar |
| DELETE | `/users/<id>` (com histórico → 409) | usuarios:gerenciar |
| GET | `/produtos`, `/produtos/<id>` | produtos:consultar |
| POST / PUT / DELETE | `/produtos`, `/produtos/<id>` | produtos:gerenciar |
| GET | `/movimentacoes`, `/movimentacoes/<id>`, `/produtos/<id>/movimentacoes` | movimentacoes:consultar |
| POST | `/produtos/<id>/movimentacoes` | a permissão do `tipo`, revalidada na transação |
| GET | `/corredores[/<id>]`, `/corredores/<id>/posicoes`, `/posicoes[/<id>]`, `/itens-estoque[/<id>]`, `/produtos/<id>/itens-estoque`, `/posicoes/<id>/itens-estoque` | localizacoes:consultar |
| POST / PUT | `/corredores[/<id>]`, `/posicoes[/<id>]` | localizacoes:gerenciar |
| GET | `/contagens[/<id>]`, `/produtos/<id>/contagens`, `/itens-estoque/<id>/contagens` | contagens:consultar |
| POST | `/contagens` / `/contagens/<id>/aplicar` | contagens:registrar / contagens:aplicar |
| GET | `/dashboard/resumo` | produtos + movimentacoes + contagens :consultar (todas) |
| GET | `/relatorios/posicao-estoque[/exportacao]` | relatorios + produtos + localizacoes :consultar (todas) |
| GET | `/relatorios/estoque-baixo[/exportacao]` | relatorios + produtos :consultar (todas) |
| GET | `/relatorios/movimentacoes[/exportacao]`, `/relatorios/saidas-periodo[/exportacao]` | relatorios + movimentacoes :consultar (todas) |
| GET | `/relatorios/divergencias[/exportacao]` | relatorios + contagens :consultar (todas) |
| GET | `/fornecedores/selecao-entrada` (lista mínima de ativos, sem parâmetros) | fornecedores:selecionar + movimentacoes:entrada (todas) |
| GET | `/fornecedores`, `/fornecedores/<id>` | fornecedores:consultar |
| POST / PUT / DELETE | `/fornecedores`, `/fornecedores/<id>` | fornecedores:gerenciar |
| GET | `/fornecedores/<id>/recebimentos` | fornecedores + movimentacoes :consultar (todas) |

## 11. Contratos

**Usuários (2E):**
- `PATCH /users/<id>/acesso` aceita só `perfil`, `ativo` e/ou `unidade` (05/10/2026: unidade editável na tela Administração; texto até 45 caracteres, `null`/vazio = sem unidade; ausente = preservada). Nome, e-mail, senha e telefone ficam intocados.
- `PUT /users`: `senha` ausente ou `null` preserva o hash; `ativo` ausente preserva a atividade; e-mail duplicado → 409.
- Proteções em PUT, PATCH e DELETE, conforme a seção 8.
- Lock administrativo: uma instrução `FOR UPDATE` ordenada por id (responsável, alvo e administradores ativos).

**Meu Perfil (`PATCH /me/perfil`):**
- **Alvo:** sempre o `user_id` da sessão. Não existe ID na URL e a query string é ignorada.
- **Campos:** aceita só `nome`, `sobrenome`, `email` e `telefone`, todos opcionais, mas ao menos um.
  - Qualquer outro campo → **400 com a lista dos campos**, mesmo com o valor atual. Isso inclui `id`, `id_usuario`, `perfil`, `ativo`, `unidade`, `senha`, `admin`, `permissoes` e `acesso`.
  - Payload vazio ou JSON inválido → 400.
- **Validação:** espaços das pontas removidos; nome, sobrenome e e-mail obrigatórios quando enviados; telefone vazio ou `null` = sem telefone.
  - Limites das colunas: nome 40, sobrenome 90, e-mail 60 e telefone 45.
  - O e-mail é normalizado como no login (`strip` + minúsculas).
- **Erros:**
  - sem sessão → 401;
  - perfil desconhecido → 403;
  - inativo ou removido (inclusive sob lock) → 403 "Usuário inativo", com a sessão revogada;
  - e-mail de outra conta → 409 com rollback;
  - erro inesperado → 500 genérico.
- **Cabeçalho opcional `X-Inventaire-Usuario`:** a tela sempre envia o id que estava exibindo. Se ele diferir da sessão (troca de conta em outra aba), a resposta é **409 e nada é gravado**. Isso não é autorização.
- **Preservação:** senha (hash), perfil, atividade, unidade, `acesso`, permissões e IDs do histórico ficam intocados.
- **Resposta:** `{message, user}`, no mesmo formato de `/session`.
- **Sessão e login:** os campos redundantes da sessão (`user_email`, `user_nome`, `user_sobrenome`) são atualizados.
  - A autorização continua pelo id persistido e não exige novo login.
  - O próximo login usa o e-mail novo com a mesma senha.

**Produto:**
- **POST** aceita somente `nome, categoria, codigo, localizacao, validade, estoque, quantidade_min, id_posicao, lote, id_fornecedor`.
- **PUT** aceita somente `nome, categoria, codigo, localizacao, validade, quantidade_min`.
- Qualquer outro campo → 400, com a lista dos campos. PUT com `estoque`, posição, lote ou qualquer campo de fornecedor → 400.
- Estoque inicial positivo exige `id_posicao` + `lote` e gera a ENTRADA "Estoque inicial" no mesmo cursor. `id_fornecedor` é opcional e só é aceito com estoque positivo e destino.

**Movimentação** (`POST /produtos/<id>/movimentacoes`):

```json
{"tipo":"ENTRADA","quantidade":5,"id_posicao":1,"lote":"L-1","motivo":"Recebimento","id_fornecedor":3}
{"tipo":"SAIDA","quantidade":2,"id_posicao":1,"lote":"L-1"}
{"tipo":"AJUSTE","novo_saldo_item":3,"id_posicao":1,"lote":"L-1","motivo":"Contagem"}
```

- **`id_fornecedor`:** ausente ou `null` = sem fornecedor; inteiro de 1 a 2147483647 = ID.
  - Zero, negativo, booleano, fração ou texto → 400.
  - Só é aceito em ENTRADA; em SAIDA ou AJUSTE dá 400 explícito, mesmo com `null`.
- `fornecedor_razao_social` e `fornecedor_cnpj` são registrados pelo servidor; se o cliente os enviar → 400.
- O histórico devolve `id_fornecedor`, `fornecedor_razao_social` e `fornecedor_cnpj` (snapshot da ENTRADA; `null` sem fornecedor).
- Em AJUSTE, a `quantidade` persistida é o alvo **global**; o alvo físico é `quantidade_item_posterior`.

**Fornecedor:**
- Corpo: `{"razao_social","cnpj","contato"?,"ativo"?}`.
- POST: `ativo` padrão `true`. PUT é o estado completo e **exige `ativo`** (nunca reativa implicitamente); `contato` omitido = `null`.
- Campos extras → 400. Resposta: `id_fornecedor, razao_social, cnpj (normalizado), cnpj_formatado, contato, ativo`. CNPJ duplicado → 409.
- **Listagem:**
  - parâmetros `q` (razão social, contato, ou CNPJ comparado sem separadores; ILIKE com curingas escapados), `ativo=sim|nao`, `page` (1..100000) e `page_size` (1..100, padrão 20);
  - ordem `razao_social, id_fornecedor`;
  - resposta `{itens,total,page,page_size,paginas}`;
  - parâmetro desconhecido ou repetido → 400.
- **Recebimentos:** só ENTRADAs vinculadas, com snapshots, paginadas até 100, em ordem `data_hora DESC, id_movimentacao DESC`. Fornecedor inexistente → 404; sem recebimentos → lista vazia; inativo continua consultável.
- **DELETE:** só sem referências. A FK `movimentacoes_fornecedor_fk` (RESTRICT) gera 409, com rollback; o caminho é inativar. Outra violação inesperada continua 500.

**Relatórios e CSV (2F/2F.2; inalterados na 2G):**
- **Período:** `[data_inicio, data_fim)`, instantes ISO 8601 **com fuso**, até 366 dias. A tela converte "até o dia D" em meia-noite local de D+1.
- **Paginação comum:** `page_size` até 100, em ordem determinística, com desempate pela chave primária.
- **Exportação CSV** (`GET /relatorios/<nome>/exportacao`):
  - **uma requisição e uma transação** REPEATABLE READ READ ONLY;
  - até **1.000 linhas**; `page`/`page_size` são recusados (400);
  - `total`, linhas e `truncado` saem do **mesmo snapshot**;
  - o CSV é montado no navegador.
- Os campos de fornecedor são excluídos explicitamente em `services/relatorios_service._movimentacao`; o SQL dos relatórios não seleciona colunas 2G.
- **Saídas no período** soma as SAIDAs do intervalo e **não equivale a giro** (não considera estoque médio, custo nem janela de consumo).

## 12. Transações, locks e invariantes

Ordem global de locks, em todas as rotinas que combinam esses recursos:

**Usuário (FOR SHARE) → Fornecedor (FOR SHARE, só ENTRADA com fornecedor) → Produto (FOR UPDATE) → Contagem → Corredor → Posição → Item**

- **ENTRADA com fornecedor:**
  1. `bloquear_para_recebimento` confere o schema 2G, lê o fornecedor `FOR SHARE` e revalida existência (404) e atividade (409);
  2. o snapshot (id, razão social, CNPJ) é lido sob esse lock;
  3. item, Produto, INSERT da movimentação, contexto físico e `UPDATE movimentacoes SET id_fornecedor…` seguem no **mesmo cursor**;
  4. **commit único**: qualquer falha reverte saldo, item, histórico e vínculo.
- **Estoque inicial:** o fornecedor é bloqueado **antes** do INSERT do Produto.
- **Meu Perfil:** bloqueia **só a linha do próprio usuário** (`FOR UPDATE`), revalida atividade e perfil sob esse lock e faz o UPDATE no mesmo cursor. Não há ciclo:
  - um único lock de linha em `usuarios`;
  - a administração bloqueia linhas numa só instrução ordenada;
  - o estoque faz só FOR SHARE da própria linha;
  - no pior caso, uma transação espera a outra.
  - Deadlock detectado pelo PostgreSQL (ex.: duas contas trocando e-mails ao mesmo tempo) → 409 com rollback.
- **Cadastro de fornecedor:** Usuário (SHARE) → UPDATE/DELETE de uma linha de `fornecedores`, sem lock posterior. Não há inversão com estoque nem com a administração de usuários, que só bloqueia linhas de `usuarios`.
- **Concorrência:**
  - inativação, edição ou exclusão esperam a ENTRADA em curso;
  - se vieram antes, a ENTRADA enxerga o estado confirmado: inativo → 409, removido → 404, renomeado → novo snapshot;
  - uma ENTRADA confirmada torna o DELETE 409.
- Editar fornecedor nunca altera estoque nem histórico; editar Produto não altera a origem.
- **Invariantes:** `produtos.estoque = SUM(itens_estoque.quantidade)`, capacidade por corredor e status do lote (regras da 2C). Também são garantidas pelas constraints do banco:
  - saldo anterior/posterior coerente por tipo;
  - contexto físico completo ou nulo;
  - vínculo contagem → AJUSTE do mesmo item;
  - fornecedor só em ENTRADA.
- **Detecção de schema:** só por SELECT, sem DDL.
  - 2C usa `to_regclass('public.itens_estoque')`; 2D usa `contagens_inventario`.
  - 2G exige a tabela `fornecedores` **e** as três colunas de `movimentacoes`.
  - Instalação parcial → 503, sem SELECT de colunas inexistentes. Uma base instalada pelo 01 já tem tudo.

## 13. Testes e evidências

Os testes isolados ficam em `backend/tests` e usam doubles: **nunca conectam ao PostgreSQL**. Exigem `SECRET_KEY` configurada (seção 4). O Redis real só entra nos 5 testes opt-in de `test_redis_session.py`, com chaves próprias.

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -B -m unittest discover -s tests -v          # Redis opt-in ficam "skipped"
$env:INVENTAIRE_TEST_REDIS = '1'; ..\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

| Marco | Testes Python isolados | Homologação real |
| --- | --- | --- |
| 2F / 2F.2 | 271 → **288** | 2F.2: SIM com exceções; RNF01 PARCIAL |
| 2G / 2G.2 | 288 → **349** (61 novos em `test_fornecedores.py`); 349/349 na inicial e na final da 2G.2; 0 tentativas de PostgreSQL | **2G.2: SIM** com exceções |
| Consolidação da entrega | 349 → **364** (15 verificações estáticas em `test_sql_entrega.py`) | **Instalação nova pelos SQLs 01/02: não comprovada em PostgreSQL real** |
| Correção de perfil e acessos (05/10/2026) | Baseline real conferida: **365** (não 364). Final: **398** (+33 em `test_correcao_perfil.py`, incluindo a unidade na Administração); **396 aprovados e 2 falhas preexistentes** em `test_sql_entrega.py`, porque o modelo `02_dados_iniciais_inventaire.sql` versionado está preenchido com nome e hash reais (seção 15). Harness do frontend no Chrome headless com `fetch` simulado: 20 cenários e 141 checks | **Não homologada em ambiente real** |

**Três níveis distintos, nunca somados:**
- **revisão estática** dos SQLs (texto, ordem, nomes);
- **testes isolados** (doubles; não homologam constraints, locks nem isolamento reais do PostgreSQL);
- **homologação real** (Chrome → Flask → Redis → PostgreSQL), feita em cada etapa sobre a base existente.

Os auxiliares de `.validation/` usados nas homologações são **evidências locais**: não são versionados nem são dependência de produção.

**Relatórios por etapa:**
- [Diagnóstico](DIAGNOSTICO_BACKEND_INVENTAIRE.md), [correção](CORRECAO_BACKEND_INVENTAIRE.md) e [refatoração](REFATORACAO_INVENTAIRE.md);
- [2A](ETAPA_2A_PRODUTO_INVENTAIRE.md), [2A.1](ETAPA_2A1_POSTGRESQL_INVENTAIRE.md) e [2A.2](ETAPA_2A2_HOMOLOGACAO_INVENTAIRE.md);
- [2B](ETAPA_2B_MOVIMENTACOES_INVENTAIRE.md) e [2B.2](ETAPA_2B2_HOMOLOGACAO_MOVIMENTACOES.md);
- [2C](ETAPA_2C_RASTREAMENTO_INVENTAIRE.md) e [2C.2](ETAPA_2C2_HOMOLOGACAO_RASTREAMENTO.md);
- [2D](ETAPA_2D_CONTAGENS_INVENTAIRE.md) e [2D.2](ETAPA_2D2_HOMOLOGACAO_CONTAGENS.md);
- [2E](ETAPA_2E_PERFIS_INVENTAIRE.md) e [2E.2](ETAPA_2E2_HOMOLOGACAO_PERFIS.md);
- [2F](ETAPA_2F_REPOSICAO_RELATORIOS_INVENTAIRE.md) e [2F.2](ETAPA_2F2_HOMOLOGACAO_RELATORIOS.md);
- [2G](ETAPA_2G_FORNECEDORES_DOCUMENTACAO_INVENTAIRE.md) e [2G.2](ETAPA_2G2_HOMOLOGACAO_FORNECEDORES.md);
- [Consolidação da entrega](CONSOLIDACAO_ENTREGA_INVENTAIRE.md).

Os relatórios são registros históricos: citam os caminhos da época (por exemplo `backend/assets/etapa_2g_fornecedores.sql` e `backend/README.md`). O mapa para os caminhos atuais está na seção 14. O relatório 2G original registra o SQL como "não executado" (estado daquela fase); a aplicação e a conferência estão na 2G.2. O relatório 2F original descreve o CSV multipágina antigo; o contrato vigente é o da 2F.2.

## 14. Scripts SQL históricos

Os scripts abaixo representam a **evolução incremental** da base homologada. Foram preservados sem alteração de conteúdo em [backend/assets/historico/](backend/assets/historico/). **Não são necessários para instalação nova e não devem ser reaplicados** na base existente.

| Caminho antigo | Caminho arquivado | Natureza |
| --- | --- | --- |
| `backend/assets/etapa_2b_movimentacoes.sql` | `backend/assets/historico/etapa_2b_movimentacoes.sql` | Incremental 2B (aplicado na base existente) |
| `backend/assets/etapa_2c_rastreamento.sql` | `backend/assets/historico/etapa_2c_rastreamento.sql` | Incremental 2C (aplicado) |
| `backend/assets/etapa_2d_contagens.sql` | `backend/assets/historico/etapa_2d_contagens.sql` | Incremental 2D (aplicado) |
| `backend/assets/etapa_2e_perfis.sql` | `backend/assets/historico/etapa_2e_perfis.sql` | Incremental 2E com normalização de aliases (aplicado) |
| `backend/assets/etapa_2g_fornecedores.sql` | `backend/assets/historico/etapa_2g_fornecedores.sql` | Incremental 2G (aplicado e conferido na 2G.2) |
| `backend/assets/inventaire_schema.sql` | `backend/assets/historico/inventaire_schema.sql` | Schema completo anterior; substituído pelo 01 |
| `backend/assets/tabelas_spi-postgres.sql` | `backend/assets/historico/tabelas_spi-postgres.sql` | Duplicata de nome histórico do schema completo (sem guardas, com `IF NOT EXISTS`) |
| `backend/assets/inserções_spi-postgres.sql` | `backend/assets/historico/inserções_spi-postgres.sql` | Arquivo histórico de inserções; só comentários, sem dados |
| `backend/assets/modelo_DB.mwb` | `backend/assets/historico/legado_spi/modelo_DB.mwb` | **Legado do SPI** (MySQL Workbench); **não** é o modelo atual do Inventaire |
| `backend/assets/modelo_DB.mwb.bak` | `backend/assets/historico/legado_spi/modelo_DB.mwb.bak` | Backup do mesmo modelo legado do SPI |

A mensagem 503 de fornecedores (`backend/repository/fornecedor_schema.py`) aponta para o caminho arquivado do incremental 2G, que só se aplica a uma base pré-2G existente.

## 15. Limitações e pendências

- **RNF01 PARCIAL.** As medições da 2F.2 valem para uma massa própria (~1.000 produtos, 10.000 movimentações) e para aquele hardware:
  - pior caso sequencial aprovado: **259,8 ms**;
  - cenário concorrente 4 × 15: máximo de **294 ms** na 2ª medição;
  - exportação até 335 ms.
- Houve episódios acima de 2 s **sem causa identificada**:
  - um timeout de 10 s;
  - uma requisição de 2,1 s;
  - um PING Redis de 2 s;
  - resets de transporte de ~19 s em clientes que não eram o navegador.
- Também ficaram sem causa identificada quedas dos processos de backend/frontend iniciados pelo agente durante a 2F.2.
- **RF08 (giro de produtos):** não atendido integralmente; "Saídas no período" é um substituto aprovado e **não é giro**.
- **TRANSFERÊNCIA** entre posições: decisão pendente, **não implementada**.
- **Fornecedores:** CNPJ limitado ao formato (sem dígitos verificadores, existência ou situação cadastral).
- **Correção de perfil e acessos (05/10/2026): sem homologação real.** Faltam login e edição com os quatro perfis em Chrome → Flask → Redis → PostgreSQL, a troca de conta entre abas e o 403 do OPERADOR nas APIs reais (plano no [relatório](CORRECAO_PERFIL_ACESSOS_INVENTAIRE.md)).
- **Sessão compartilhada entre abas** do mesmo contexto do navegador (seção 8): a troca é detectada e bloqueada, mas não existem contas simultâneas por aba.
- **`backend/assets/02_dados_iniciais_inventaire.sql` está preenchido com dados reais** (nome e hash bcrypt) no workspace, e por isso 2 testes de `test_sql_entrega.py` falham. Antes de publicar, restaure os placeholders e use uma cópia `.local.sql` (seção 6). Esta correção não alterou o arquivo.
- **Instalação nova (01 + 02): não validada em PostgreSQL real.** A equivalência com o catálogo homologado foi conferida por revisão estática; as guardas PL/pgSQL, a recusa de placeholders e a atomicidade do seed ainda precisam ser exercitadas numa base vazia descartável.
- **Critérios cobertos apenas por testes isolados**, entre outros:
  - o 503 real com schema ausente ou parcial (inclusive o 2G);
  - permissões parciais e perfil desconhecido em sessão real;
  - "último administrador";
  - divergência global × física persistida;
  - falha de commit por perda de conexão.
- **Fora do escopo:** compras, pedidos, notas fiscais, custos, consulta externa de CNPJ, integrações, reposição automática e múltiplos depósitos.
- **Registros históricos preservados:**
  - na 2F.2, a restauração do status técnico de dois itens não foi executada (foram excluídos pela limpeza, sem estado residual);
  - nas sessões antigas sem medição de TTL/replay, a expiração por inatividade em 30 minutos é o comportamento esperado pela configuração, não uma medição.
- **O projeto não está declarado integralmente concluído.**
