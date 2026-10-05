# Etapa 2A.1 — Preparação do PostgreSQL Inventaire

Elaboração: 26/09/2026. Atualização após aplicação manual: 28/09/2026. Escopo: exclusivamente este workspace. Schema e administrador criados pelo usuário; estrutura real conferida por leitura. Login e CRUD persistido ainda pendentes de homologação.

## Verificação após aplicação manual — 28/09/2026

O usuário informou a criação das tabelas e do administrador. A nova verificação confirmou:

| Item | Resultado |
| --- | --- |
| Conexão PostgreSQL | Estabelecida com `inventaire`, servidor 18.6, transação somente leitura |
| Schema da aplicação | `public`; nomes não qualificados resolvem as tabelas esperadas |
| Tabelas | Somente `public.usuarios` e `public.produtos` |
| Estrutura | Colunas, tipos, tamanhos, nulabilidade, defaults, identity ALWAYS, PKs, UNIQUEs e CHECKs compatíveis com os SQLs preparados |
| Objetos associados | Duas sequências identity, quatro índices PK/UNIQUE e tipos associados às tabelas; nenhuma função ou estrutura legada encontrada |
| Administrador | Um usuário total, administrador ativo, senha armazenada no formato bcrypt |
| Produtos | Zero registros |
| Permissões de leitura | SELECT disponível nas duas tabelas para o usuário configurado |
| Redis | PING respondeu com sucesso |

A conferência do administrador usou somente contagens agregadas: não retornou email, nome, senha ou hash. Formato bcrypt não comprova que a senha digitada no login esteja correta. As definições das constraints foram inspecionadas; sua rejeição de entradas inválidas em operações reais ainda não foi testada.

Nesta retomada foram executados somente SELECTs de diagnóstico/metadados/contagens e PING Redis. Nenhum registro foi criado, atualizado ou removido pelo agente; nenhum schema ou bootstrap foi reaplicado. Não houve login persistido, teste real de CRUD ou navegação autenticada. Os 58 testes registrados abaixo pertencem à preparação de 26/09; não foram repetidos nesta verificação sem mudança de código.

Evidências sem segredos: `.validation/etapa2a1_diagnostico.json` (metadados atuais) e `.validation/etapa2a1_pos_aplicacao.json` (contagens e Redis).

## Configuração

`backend/.env` foi lido por `dotenv_values`, sem exibir seu conteúdo ou os valores de credenciais. Inicialmente as cinco variáveis PostgreSQL estavam ausentes; o usuário passou a preenchê-las durante a tarefa. Estado da leitura mais recente:

| Variável | Estado |
| --- | --- |
| DB_HOST | Configurado |
| DB_PORT | Configurado |
| DB_USER | Configurado |
| DB_PASSWORD | Configurado |
| DB_NAME | Configurado |
| SECRET_KEY | Configurado |
| REDIS_URL | Configurado |

Nenhuma variável de ambiente foi editada pelo agente. A configuração de sessão/Redis não foi alterada pelas ações do agente. A comparação de hashes observou `backend/.env` modificado e `.env` da raiz removido durante o preenchimento externo da configuração; nenhuma operação do agente editou ou removeu esses arquivos. Essas mudanças não são alterações de código desta entrega.

## Estado PostgreSQL na preparação — 26/09/2026

**O banco inventaire ainda precisa ser criado manualmente no pgAdmin.** O usuário corrigiu `DB_NAME` para `inventaire`; nenhum acesso foi tentado com o nome divergente anterior.

Diagnóstico repetido após o aviso do usuário de alteração do `.env`: as sete configurações permanecem presentes e o servidor continua informando que `inventaire` não existe. Nenhum SQL foi enviado nessa nova tentativa.

| Item | Resultado |
| --- | --- |
| Servidor PostgreSQL | Alcançável; conexão TCP aceita |
| Banco configurado | `inventaire` |
| Conexão à base | Não estabelecida: servidor informou que o banco não existe |
| Banco existente | Não |
| Tabelas encontradas | Não se aplica: a base ainda não existe; nenhum catálogo foi consultado |
| Pronto para aplicação manual | SQLs preparados; criar primeiro o banco e confirmar que está vazio |

A tentativa por psycopg2 encontrou `UnicodeDecodeError` ao interpretar a mensagem localizada de conexão. Foi usada a libpq já instalada no `.venv` para classificar a mensagem sem imprimi-la ou salvá-la: banco inexistente. Essa conexão alternativa também usou exclusivamente `inventaire`, com modo somente leitura solicitado, e foi encerrada. Não houve fallback para `postgres` ou outra base, nem SQL enviado ao servidor.

O diagnóstico auxiliar usa exclusivamente o destino de `backend/.env`, sem fallback para outro banco. Quando habilitada por configuração coerente, a conexão exige modo somente leitura desde a abertura, limita o tempo de conexão/consulta e lê apenas catálogos/metadados. Nunca executa os arquivos SQL preparados. Nenhum acesso a dados de usuários ou produtos é necessário.

## SQL antigo

`tabelas_spi-postgres.sql` foi substituído pelo schema Inventaire. Foram retirados os comandos destrutivos iniciais, setores, responsabilidades, câmeras, zonas, EPIs, monitoramento, alarmes, alertas, estatísticas, seus relacionamentos e índices específicos. Restam apenas `usuarios` e `produtos`.

`inserções_spi-postgres.sql` teve todos os dados antigos e a consulta de alertas removidos, incluindo usuários/hashes, endereços de câmeras, vínculos por IDs fixos e produtos do domínio anterior. Os arquivos `.mwb` e `.mwb.bak` permanecem intactos. Os nomes dos dois SQLs foram preservados e seus cabeçalhos identificam a adaptação ao Inventaire.

## Schema Inventaire

Os dois scripts de schema têm conteúdo executável idêntico, incluindo transação, proteção de destino, tabelas, constraints e comentários de colunas. O schema oficial permanece `backend/assets/inventaire_schema.sql`; para esta aplicação manual, usar `backend/assets/tabelas_spi-postgres.sql`.

### usuarios

| Campo | Definição |
| --- | --- |
| id_usuario | INTEGER, GENERATED ALWAYS AS IDENTITY, PRIMARY KEY |
| nome | VARCHAR(40), obrigatório |
| sobrenome | VARCHAR(90), obrigatório |
| email | VARCHAR(60), obrigatório, UNIQUE `usuarios_email_key` |
| senha | VARCHAR(255), obrigatório, CHECK de formato bcrypt |
| perfil | VARCHAR(20), obrigatório |
| unidade | VARCHAR(45), opcional |
| telefone | VARCHAR(45), opcional |
| ativo | BOOLEAN, obrigatório, DEFAULT TRUE |
| acesso | TIMESTAMP sem fuso, opcional |

`usuarios_email_key` é o nome esperado pelo repository para reconhecer duplicidade. O CHECK valida o formato do hash; bcrypt é calculado/verificado pela aplicação. Não há senha padrão, hash copiado ou geração de senha no SQL.

### produtos

| Campo | Definição |
| --- | --- |
| id_produto | INTEGER, GENERATED ALWAYS AS IDENTITY, PRIMARY KEY |
| nome | TEXT, obrigatório, não vazio ou somente branco |
| categoria | TEXT, obrigatória, não vazia ou somente branco |
| codigo | TEXT, obrigatório, não vazio, sem branco nas extremidades, UNIQUE `produtos_codigo_key` |
| localizacao | TEXT simples, obrigatório, não vazio ou somente branco |
| validade | DATE, opcional, permite NULL |
| estoque | INTEGER, obrigatório, DEFAULT 0, CHECK >= 0 |
| quantidade_min | INTEGER, obrigatório, DEFAULT 0, CHECK >= 0 |

SKU continua fornecido pelo usuário e sensível a maiúsculas. `produtos_codigo_key` é compatível com o conflito de SKU tratado pelo repository. Não há FKs, campos de EPI ou estruturas da Etapa 2B. PKs e UNIQUEs geram seus próprios índices; os dois IDs usam sequências identity.

**Inconsistência corrigida:** o schema oficial anterior usava `btrim(codigo)`, que não retirava tabulações e quebras de linha das extremidades com seu argumento padrão. O DTO normaliza esses caracteres por `strip()`. Os dois SQLs agora rejeitam branco POSIX no início/fim do SKU. Essa alteração foi revisada estaticamente; sua avaliação pelo PostgreSQL ficará para a homologação após aplicação manual.

Os scripts preservam a proteção contra bancos reservados/identificados como SPI e contra objetos de aplicação existentes. Não há reset nem migração de dados. Reexecução deve falhar sem substituir objetos: não aplicar os dois scripts de schema em sequência.

## Arquivos modificados

- `backend/assets/tabelas_spi-postgres.sql`: adaptado integralmente ao Inventaire.
- `backend/assets/inserções_spi-postgres.sql`: substituído por instruções, sem seeds.
- `backend/assets/inventaire_schema.sql`: ajuste do CHECK do SKU e indicação de sincronização/execução manual.
- `README.md` e `backend/README.md`: corrigidas as instruções que ainda classificavam os dois SQLs adaptados como exclusivamente históricos.
- `ETAPA_2A1_POSTGRESQL_INVENTAIRE.md`: este relatório, criado nesta etapa.

Artefatos auxiliares locais em `.validation/`: `etapa2a1_initial_hashes.json`, `etapa2a1_diagnostico.py`, `etapa2a1_diagnostico.json`, `etapa2a1_sql.json` e `etapa2a1_preservacao.json`. Eles não integram a aplicação. Nenhum código Python/JavaScript da aplicação, teste existente, frontend, diagrama ou relatório histórico foi alterado, conforme comparação de hashes.

## Seeds

Sem seeds de produtos nesta etapa. O arquivo de inserções contém apenas comentários/instruções, permitindo iniciar a base sem dados fictícios. Não existe usuário ou senha padrão nos SQLs preparados. Os produtos serão cadastrados no próximo teste autorizado da aplicação.

## Testes

| Verificação | Resultado |
| --- | --- |
| Regressão existente com o `.venv` | 58 aprovados, zero falhas, zero pulados |
| `test_core.py` | 23 aprovados, dependências PostgreSQL isoladas |
| `test_produtos.py` | 30 aprovados, dependências PostgreSQL isoladas |
| `test_redis_session.py` | 5 aprovados com Redis real e usuários/repositories isolados |
| Sintaxe Python | AST dos 36 arquivos do backend aprovado |
| Sintaxe JavaScript | `node --check` dos 10 arquivos aprovado |
| Inicialização Flask | Servidor HTTP local iniciado e encerrado; blueprints `user_bp` e `produto_bp` |
| HTTP de leitura sem sessão | `/session` e `/produtos`: 401; `/epis`: 404 |
| Revisão estática dos três SQLs | Aprovada; conteúdo executável dos schemas idêntico; seed sem comandos |

A suíte foi executada com `INVENTAIRE_TEST_REDIS=1` e um bloqueio externo de `psycopg2.connect`, confirmado sem chamadas reais. Os testes de conexão existentes mantiveram seus próprios doubles. Os cenários de CRUD/login da regressão são simulados: nenhum POST/PUT/DELETE ou login persistido foi enviado ao PostgreSQL. Redis usou os prefixos UUID próprios dos testes e limpou somente suas chaves. Os logs de RuntimeError pertencem aos cenários esperados de falha.

A validação HTTP adicional também bloqueou conexão PostgreSQL e só enviou os três GETs listados. Nenhum bootstrap foi executado. Nenhuma bateria de navegador foi repetida, pois o frontend não mudou.

A revisão SQL conferiu tabelas/colunas, identity, constraints nomeadas, nulabilidade, defaults, regex, transação, balanceamento de parênteses e ausência de comandos destrutivos, estruturas legadas, FKs ou IDs manuais. É revisão estática, não execução ou validação por um parser PostgreSQL.

## Execução manual no pgAdmin

1. Abra o pgAdmin.
2. Conecte-se ao servidor PostgreSQL destinado ao Inventaire, conferindo host e porta localmente.
3. Caso o banco `inventaire` ainda não exista, crie-o manualmente em **Databases → Create → Database**, com usuário autorizado. Não use outro banco como alternativa.
4. Selecione o banco `inventaire`. Caso já existam tabelas/objetos, inspecione a estrutura antes de prosseguir; não tente reinstalar sobre objetos existentes ou desconhecidos.
5. Abra **Query Tool** nesse banco.
6. Abra ou copie `backend/assets/tabelas_spi-postgres.sql`.
7. Confira novamente que o Query Tool está conectado ao banco `inventaire` no servidor correto.
8. Execute o script completo uma única vez. Em caso de erro, interrompa e guarde apenas a mensagem sem credenciais; não tente corrigir apagando objetos. Se a sessão ficar em transação abortada, encerre a transação com ROLLBACK antes de novas consultas.
9. Atualize **Schemas → public → Tables** e verifique `usuarios` e `produtos`.
10. `backend/assets/inserções_spi-postgres.sql` seria opcional se contivesse seeds; nesta entrega contém apenas instruções, portanto não precisa ser executado.
11. Não execute SQLs históricos do SPI que não tenham sido adaptados. Não execute também `inventaire_schema.sql`, pois representa o mesmo schema já aplicado.

## Primeiro administrador

Depois que `usuarios` existir e o schema tiver sido conferido, na retomada explícita da homologação, execute manualmente este comando em um terminal na raiz do workspace:

```powershell
.\.venv\Scripts\python.exe -B backend/bootstrap_admin.py --database inventaire
```

O bootstrap lê `backend/.env`, exige destino explícito e confirmação pelo nome da base; solicita nome, sobrenome, email, unidade e telefone. A senha e sua confirmação são lidas sem eco por `getpass`, depois processadas por bcrypt. O perfil criado é administrativo e um email já existente é preservado. Não há senha hardcoded e o script nunca é chamado automaticamente pelo servidor. Este comando é uma instrução futura; não foi executado nesta entrega.

## Próxima ação do usuário

Banco, tabelas e administrador já foram criados pelo usuário e conferidos em 28/09/2026. Não reaplicar os scripts de schema ou o bootstrap.

O próximo passo é testar o login com a conta criada, informando a senha apenas na interface local da aplicação. Para iniciar, na raiz, use `.\.venv\Scripts\python.exe -B backend/app.py` e, em outro terminal, `.\.venv\Scripts\python.exe -B -m http.server 8080 --bind 127.0.0.1`; abra `http://localhost:8080/login.html`. Após o login, conferir sessão, acesso ao inventário e logout. A homologação de criação/edição/exclusão de produtos deve usar registros próprios de teste.

## Próxima homologação

A estrutura real e a presença de administrador ativo foram conferidas por leitura. Permanecem pendentes: login persistido, Redis + sessão autenticada, CRUD Produto, comportamento das constraints e integração frontend + backend + PostgreSQL. O bootstrap foi realizado pelo usuário; não foi repetido pelo agente. Nenhuma funcionalidade de movimentações/auditoria da Etapa 2B foi implementada.

## Preservação e segurança

- Na preparação de 26/09, nenhum SQL foi enviado ao servidor, pois a conexão terminou por banco inexistente. Na verificação de 28/09, foram executados somente SELECTs em transação somente leitura. Nenhum script preparado, SQL de escrita ou migration foi executado automaticamente. Nenhum administrador ou produto foi criado pelo agente; a criação do administrador foi feita pelo usuário.
- Nenhum valor de `DB_PASSWORD` ou `SECRET_KEY` foi exibido, copiado para artefatos ou alterado pelo agente.
- Na inspeção inicial do seed antigo, URLs históricas de câmeras contendo credenciais apareceram na saída da ferramenta. Não foram usadas ou reproduzidas neste relatório; foram removidas do SQL adaptado junto com os demais dados legados. Portanto, a afirmação de ausência de exposição refere-se às credenciais atuais do `.env`, não a todas as credenciais históricas do seed.
- Nenhum banco SPI foi acessado ou alterado.
- Nenhum commit, push ou Pull Request. A pasta não é um repositório Git.
- Todas as edições e gerações de arquivos da tarefa ocorreram neste workspace. Nenhum arquivo externo foi alterado pelo agente.
