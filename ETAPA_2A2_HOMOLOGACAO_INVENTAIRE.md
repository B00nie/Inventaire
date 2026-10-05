# Etapa 2A.2 — Homologação integrada do Inventaire

Concluída em 28/09/2026, após login manual do usuário na janela dedicada.

**O Inventaire está homologado e pronto para a Etapa 2B — Movimentações de Estoque e Auditoria? SIM.**

Decisão referente ao escopo atual: autenticação/usuários e Produto, em ambiente local de desenvolvimento. Nenhuma funcionalidade de Movimentações ou Auditoria foi implementada.

## PostgreSQL

Conexão real estabelecida pela classe `Connection` da aplicação, usando exclusivamente `backend/.env`. Os auxiliares retiram variáveis herdadas de conexão antes de carregar esse arquivo; nenhum valor secreto é impresso. Destino conferido antes das consultas: `current_database() = inventaire`. Não houve acesso a outro banco como fallback.

| Verificação | Resultado |
| --- | --- |
| Banco | `inventaire` |
| Tabelas | `public.usuarios` e `public.produtos` |
| SELECT real | Aprovado |
| Transação/commit de leitura | Aprovado; conexão retorna ao estado idle |
| Erro controlado | `SELECT 1 / 0`, com reconhecimento de transação abortada |
| Rollback e recuperação | Aprovados; SELECT posterior retorna 42 |
| Mesmo contexto Flask | Reutiliza a mesma conexão |
| Saída do contexto | Rollback de leitura pendente e fechamento |
| Novo contexto | Nova conexão funcional, posteriormente fechada |
| Escritas reais | POST/PUT/DELETE da API, conferidos por SELECT independente |
| Produtos iniciais/finais | 0 / 0 |

Os testes de transação sem alteração de dados foram repetidos ao final. O conflito UNIQUE real seguido de consultas e mutações bem-sucedidas também confirmou recuperação do fluxo HTTP/repository após erro. Não houve DDL nem INSERT/UPDATE/DELETE direto pelos auxiliares SQL: todas as mutações de Produto ocorreram pela API real.

## Schema

Schema real comparado com `backend/assets/tabelas_spi-postgres.sql`, `backend/assets/inventaire_schema.sql`, models, repositories e DTOs atuais, antes do CRUD. **Sem divergência estrutural relevante.** Metadados reconferidos ao final.

| Tabela/campo | Tipo | Nulabilidade/default/restrições |
| --- | --- | --- |
| usuarios.id_usuario | integer | NOT NULL, identity ALWAYS, PK |
| usuarios.nome | varchar(40) | NOT NULL |
| usuarios.sobrenome | varchar(90) | NOT NULL |
| usuarios.email | varchar(60) | NOT NULL, UNIQUE |
| usuarios.senha | varchar(255) | NOT NULL, CHECK de formato bcrypt |
| usuarios.perfil | varchar(20) | NOT NULL |
| usuarios.unidade, telefone | varchar(45) | NULL permitido |
| usuarios.ativo | boolean | NOT NULL, DEFAULT true |
| usuarios.acesso | timestamp sem fuso | NULL permitido |
| produtos.id_produto | integer | NOT NULL, identity ALWAYS, PK |
| produtos.nome, categoria, localizacao | text | NOT NULL, CHECK contra vazio/somente branco |
| produtos.codigo | text | NOT NULL, UNIQUE, CHECK contra vazio/branco e espaço nas extremidades |
| produtos.validade | date | NULL permitido |
| produtos.estoque, quantidade_min | integer | NOT NULL, DEFAULT 0, CHECK >= 0 |

Os demais campos não têm default explícito. PKs: `usuarios_pkey` e `produtos_pkey`. UNIQUEs: `usuarios_email_key` e `produtos_codigo_key`, com nomes compatíveis com o tratamento de conflito dos repositories. Os quatro índices de PK/UNIQUE foram conferidos. CHECKs e NOT NULL constam validados no catálogo; não há FK nas duas tabelas.

O DTO de Produto normaliza textos, exige inteiros não negativos e valida datas ISO. A rejeição dos cinco tipos de payload inválido foi testada por POST e PUT; os CHECKs foram inspecionados no catálogo, sem tentar contornar o DTO com escritas SQL diretas. A UNIQUE do SKU foi efetivamente exercitada no PostgreSQL pelo HTTP 409.

Nota documental: os dois arquivos SQL têm definições de tabelas equivalentes, mas procedimentos de instalação diferentes. `tabelas_spi-postgres.sql` usa `IF NOT EXISTS` e não contém a guarda de banco vazio de `inventaire_schema.sql`. Portanto, a afirmação antiga de conteúdo executável idêntico no relatório 2A.1 não representa o estado atual. Nenhum script foi reaplicado ou alterado.

## Administrador

| Verificação | Resultado |
| --- | --- |
| Existente | SIM |
| Ativo | SIM |
| Perfil administrativo | SIM |
| Compatibilidade com autenticação | SIM |
| Autenticação real validada | SIM |

O usuário digitou a própria senha na interface. POST `/login` 200 confirmado no log HTTP sem payload. GET `/session` retornou usuário administrativo ativo; identidade e campos não sensíveis foram comparados internamente com PostgreSQL e com a sessão Redis. Nenhuma senha foi lida pelo agente ou inserida em script/comando/arquivo.

Nenhum administrador criado, editado ou excluído. A comparação dos campos cadastrais antes/depois confirmou preservação. O login normal da aplicação atualiza `acesso`; essa atualização prevista não foi tratada como edição cadastral. O hash não foi exibido nem recuperado para os auxiliares.

## Redis e sessão

PING real aprovado. Cookie de sessão presente e HttpOnly. A sessão utilizada pelo navegador foi localizada pelo cookie existente, apenas em memória, sem registrar cookie, ID de sessão ou chave Redis. Correspondência entre sessão, resposta HTTP e administrador no PostgreSQL confirmada.

- TTL medido após autenticação: **1797 segundos**, dentro dos 1800 configurados.
- Recuperação da sessão entre requisições e navegação/recarregamento: aprovada.
- TTL imediatamente antes do logout: **1797 segundos**.
- Logout real pela interface: POST 200 e redirecionamento ao login.
- Chave Redis da sessão utilizada ausente após logout: TTL **-2**.
- Reenvio do cookie antigo para `/session`, `/produtos` e `/users`: **401** nos três casos.
- Cookie removido do navegador; replay não recriou a sessão Redis.

Os cinco testes existentes com Redis real também passaram criação/recuperação, TTL, rotação, logout, revogação de usuário inativo e restrições de perfil, usando usuários/repositories isolados. Cada teste limpou exclusivamente seu prefixo UUID e confirmou ausência de suas chaves. Nenhum FLUSHDB/FLUSHALL. Chaves/sessões desconhecidas foram preservadas.

## Backend e autorização

Flask real executado com `.venv` em `http://127.0.0.1:5000`, sem debug/reloader. Apenas `user_bp` e `produto_bp` carregados. EPI, visão computacional, câmeras, workers, YOLO, Active Learning, MQTT e Socket.IO não carregados. Arquivos legados preservados.

Antes da autenticação, GET `/session`, `/produtos` e `/users` retornaram 401; GET `/epis` retornou 404. Com administrador real, os acessos autorizados passaram. Depois do logout, as três leituras e POST/PUT/DELETE de Produto retornaram 401. A tentativa de abrir Inventário sem sessão redirecionou ao login.

As restrições de operador foram cobertas pela regressão com Redis real e usuário isolado. Nenhum usuário adicional foi criado para testar perfis no PostgreSQL, e o perfil do administrador não foi alterado.

## API real

| Fluxo | Resultado |
| --- | --- |
| Login | POST 200, manual, sem captura de senha |
| Session | 200 autenticado; usuário correto, admin e ativo; 401 sem sessão |
| Logout | POST 200; cookie e sessão Redis invalidados |
| GET Produtos | 200; lista comparada integralmente com PostgreSQL |
| POST Produto | 201; todos os campos conferidos no PostgreSQL |
| GET Produto | 200; resposta corresponde ao registro real |
| PUT Produto | 200; nome, localização, estoque, mínimo e validade conferidos |
| SKU duplicado | 409; apenas um registro com o SKU, sem duplicação |
| Payload inválido | 400 em 10 cenários; banco sem alteração indevida |
| DELETE Produto | 200; ausência no banco e GET posterior 404 |
| GET Users | 200; administrador correto; resposta sem campos sensíveis |

Payloads inválidos: estoque negativo, quantidade mínima negativa, SKU vazio, localização vazia e data impossível, cada um por POST e PUT. A ausência de alteração foi conferida no PostgreSQL após cada caso. Cadastro testou data válida; atualização e formulário testaram validade opcional nula.

A resposta de `/users` foi validada contra a lista permitida de campos: id, nome, sobrenome, email, perfil, unidade, telefone, admin, ativo e acesso. Não contém senha, hash bcrypt, SECRET_KEY, DB_PASSWORD, cookie ou sessão. Emails não são reproduzidos nas evidências.

## Frontend + backend + PostgreSQL

Navegador Chrome real, frontend em `http://localhost:8080`, API real em `http://localhost:5000`. Nenhuma resposta de API simulada ou substituição do fetch para fornecer dados fictícios. Botões acionados com eventos de mouse; formulários preenchidos e submetidos na página. Dados gravados e exibidos comparados com HTTP e SELECTs reais.

| Tela/fluxo | Classificação | Evidência |
| --- | --- | --- |
| Login | Homologado com API real | Entrada manual; POST 200; sessão administrativa persistida |
| Dashboard | Homologado com API real | Sessão real, identificação e navegação; funcionalidades futuras corretamente indicadas |
| Inventário | Homologado com API real | Listagem, cadastro, recarga persistida, edição, pesquisas e exclusão |
| Administração | Homologado com API real | Lista/contadores correspondem a `/users`; atualização da lista |
| Meu Perfil | Homologado com API real | Nome/email correspondem à sessão real; edição desabilitada como previsto |
| Configurações | Homologado com API real | Acesso autenticado; tema local salvo e preservado na recarga; preferência original restaurada |
| Logout | Homologado com API real | Clique em Sair, POST 200, redirecionamento e revogação comprovada |

Inventário: pesquisas por nome, SKU e localização testadas também com valores distintos; caso sem resultado confirmado. Badge `Estoque Baixo`, total de unidades e contador de estoque baixo conferidos. Após edição, badge mudou para `Disponível`. Produto persistiu após recarregamento; exclusão pela interface confirmada no banco e por GET 404.

`credentials: 'include'` permanece na camada central `js/api.js`. CORS permite a origem local com credenciais. Sessão HttpOnly recuperada nas requisições entre origens. O servidor auxiliar disponibiliza somente HTML/assets públicos e rejeita `backend/.env` com 404.

Limites próprios do produto atual: Dashboard apresenta atalhos e informações; os indicadores estão no Inventário. Meu Perfil é somente leitura. Configurações alteram tema local, sem persistência PostgreSQL. Esses comportamentos são os previstos nesta etapa; não foram apresentados como funcionalidades de escrita no banco.

## Testes e evidências

| Categoria | Resultado |
| --- | --- |
| Isolados | 53 aprovados |
| Redis real com usuário/repository isolados | 5 aprovados |
| Total da suíte existente | **58 aprovados, zero falhas, erros ou pulados** |
| PostgreSQL durante a suíte | Bloqueado externamente; zero tentativas reais |
| PostgreSQL real fora da suíte | Schema, administrador, transações, dados de CRUD e limpeza aprovados |
| HTTP real | Autenticação, sessão, CRUD, validações, autorização e logout aprovados |
| Navegador real | Fluxos da tabela anterior aprovados |

A suíte foi repetida nesta conclusão. Os logs de RuntimeError nela pertencem aos cenários simulados de erro. Os auxiliares reais são opt-in por execução explícita em `.validation/`, fora da descoberta normal de testes; nenhuma execução normal da suíte foi configurada para modificar banco real. Não se somam os checkpoints de integração ao total de 58 testes unittest.

Evidências finais, sem segredos:

- `.validation/etapa2a2_preflight.json`: metadados e transações reais, reconferidos ao final.
- `.validation/etapa2a2_api_real.json`: sessão administrativa, CRUD, conflito, dez rejeições, usuários e limpeza.
- `.validation/etapa2a2_ui_real.json`: execução completa aprovada no navegador e limpeza final.
- `.validation/etapa2a2_logout_real.json`: logout, replay negado, endpoints protegidos e banco limpo.
- `.validation/etapa2a2_suite.json`: 58 aprovados, PostgreSQL bloqueado na regressão.
- `.validation/etapa2a2_http.json`: verificações sem autenticação, CORS e arquivos privados.

### Intercorrências da automação

O primeiro navegador dedicado ficou indisponível antes da retomada; foi necessária a nova janela em que o usuário confirmou login. Durante os testes, uma tentativa não alcançou o botão de edição por rolagem; o auxiliar passou a verificar o ponto clicável. Outra aba parou de responder durante a exclusão. A sessão existente foi transferida apenas em memória para um contexto efêmero do mesmo navegador, sem novo login, fabricação de sessão ou escrita de cookie em arquivo pelo auxiliar. A execução completa posterior passou, incluindo a confirmação de exclusão.

Os produtos das tentativas interrompidas foram removidos somente após conferir seus IDs e SKUs exclusivos. `.validation/etapa2a2_ui_first_attempt.json` e `etapa2a2_ui_second_attempt.json` preservam as falhas e as confirmações de limpeza. Nenhuma falha intermediária foi contada como teste aprovado. Os ajustes foram apenas nos auxiliares; o código da aplicação não precisou de correção.

## Limpeza

**Produtos temporários removidos: SIM. Chaves temporárias Redis removidas: SIM. Administrador preservado: SIM.**

Quatro produtos foram efetivamente criados: um no CRUD HTTP e três nas tentativas de navegador. Todos foram removidos; a duplicação de SKU não persistiu. A consulta final encontrou **zero produtos totais e zero registros `TEST-HOMOLOG-*`**, buscando o prefixo em nome e SKU. O contador identity pode avançar mesmo após exclusão/conflito; nenhuma sequência foi reiniciada.

Chaves dos testes Redis removidas pelos respectivos testes; chave da sessão real utilizada removida pelo logout e impossível de reutilizar. Nenhuma chave desconhecida foi apagada. Dados cadastrais do administrador permaneceram iguais ao início dos testes autenticados.

## Arquivos e preservação

Criado/concluído: `ETAPA_2A2_HOMOLOGACAO_INVENTAIRE.md`.

Auxiliares criados/ajustados somente em `.validation/`:

- Python: `etapa2a2_preflight.py`, `etapa2a2_verify.py`, `etapa2a2_suite.py`, `etapa2a2_server.py`, `etapa2a2_frontend.py`, `etapa2a2_http.py`, `etapa2a2_report.py`.
- JavaScript: `etapa2a2_browser_start.cjs`, `etapa2a2_cdp.cjs`, `etapa2a2_api_real.cjs`, `etapa2a2_ui_real.cjs`, `etapa2a2_dialog_recover.cjs`, `etapa2a2_recover_session.cjs`, `etapa2a2_logout_real.cjs`.
- Evidências JSON citadas acima, metadados de processos, logs HTTP sem payload e perfil temporário dedicado do Chrome.

Nenhum arquivo de código da aplicação, teste existente, schema ou `.env` foi editado nesta etapa. Nenhum arquivo EPI legado removido.

- Nenhuma senha, hash bcrypt, DB_PASSWORD, SECRET_KEY, cookie ou ID de sessão foi exposto nos resultados ou relatório.
- Nenhum schema, bootstrap ou novo administrador. Nenhum CREATE/ALTER/DROP/TRUNCATE, reset ou exclusão de dado preexistente. Os únicos DELETEs foram os pontuais de produtos temporários via API, exigidos pelo CRUD e pela limpeza.
- Nenhum banco SPI alterado.
- Nenhum commit, push ou Pull Request; a pasta de trabalho não é repositório Git.
- Nenhum arquivo fora do workspace foi alterado pelas operações de arquivo dos auxiliares/agente. Perfil e temporários do navegador foram direcionados para `.validation/`.
- Backend/frontend auxiliares permanecem disponíveis localmente; a sessão testada foi encerrada.

## Decisão final

**SIM.** PostgreSQL, schema, Redis, autenticação, sessão, CRUD Produto, autorização, frontend com backend real, limpeza e regressão automatizada foram validados. Não há bloqueio restante dentro do escopo solicitado para iniciar a Etapa 2B.
