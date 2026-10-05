# Etapa 2B — Movimentações de Estoque e Auditoria

Data: 28/09/2026. Escopo: exclusivamente o workspace Inventaire.

**Implementação preparada, aguardando aplicação manual do schema da Etapa 2B.**

Primeira fase concluída no código, SQL e testes isolados/simulados. **Nenhum SQL foi enviado ao PostgreSQL nesta fase.** A homologação real das movimentações depende da aplicação manual do incremental e de uma continuação separada.

## Resultado

| Critério | Resultado nesta fase |
| --- | --- |
| Movimentações implementadas no código | SIM |
| Entrada | SIM |
| Saída | SIM |
| Ajuste | SIM |
| Histórico | SIM |
| Transação atômica | SIM, implementada e testada com double |
| Proteção contra concorrência | SIM, FOR UPDATE; demonstração concorrente simulada |
| Alteração direta de estoque pelo PUT bloqueada | SIM |
| Estoque inicial auditável | SIM |
| Frontend Movimentações | SIM |
| Navegação atualizada | SIM |
| Testes anteriores aprovados | 58 |
| Novos testes Python aprovados | 33 |
| Total de testes Python | 91, zero falhas/erros/pulados |
| Verificações adicionais no navegador | 35, API simulada |
| SQL incremental executado | NÃO |
| PostgreSQL real alterado | NÃO |
| Homologação real de Movimentações | NÃO executada |

## Base examinada

Foram lidos os seis relatórios históricos solicitados, os dois READMEs, os três SQLs existentes, a cadeia Produto/Usuário/autenticação/sessão/conexão, DTOs, controllers, services, repositories, frontend e testes existentes. A configuração de `backend/.env` foi lida sem exibir valores secretos; o destino configurado foi confirmado como `inventaire`. O arquivo permaneceu inalterado.

A Etapa 2A.2 é a referência de homologação da base. A diferença entre os procedimentos dos schemas foi preservada: `inventaire_schema.sql` exige instalação nova/vazia com guardas; `tabelas_spi-postgres.sql` usa `IF NOT EXISTS` e não compara a estrutura dos objetos existentes. As definições de tabelas continuam equivalentes. Relatórios históricos não foram alterados.

## Arquitetura

```text
Produto → Movimentação → Histórico

movimentacoes.html → js/movimentacoes.js → js/api.js (apiMovimentacao)
  → controller/movimentacao_routes.py
  → services/movimentacao_service.py
  → repository/movimentacao_repository.py
  → PostgreSQL: produtos + movimentacoes + usuarios
```

O DTO valida tipos, campos e motivo. O service valida/serializa e delega a operação transacional ao repository, onde saldo, lock, autorização persistida e writes precisam usar o mesmo cursor. O controller cuida de HTTP e extrai o responsável da sessão. O model é uma dataclass do registro com campos auxiliares de JOIN.

`movimentacoes` é o próprio histórico auditável. Não foi criada tabela genérica `auditoria`. Não existem rotas de atualização ou exclusão do histórico. Correções exigem nova movimentação AJUSTE.

## Banco e constraints

| Coluna | Definição |
| --- | --- |
| id_movimentacao | INTEGER GENERATED ALWAYS AS IDENTITY, PRIMARY KEY |
| id_produto | INTEGER NOT NULL, FK produtos(id_produto), ON DELETE RESTRICT |
| id_usuario | INTEGER NOT NULL, FK usuarios(id_usuario), ON DELETE RESTRICT |
| tipo | TEXT NOT NULL, CHECK ENTRADA/SAIDA/AJUSTE |
| quantidade | INTEGER NOT NULL; positiva para Entrada/Saída, não negativa para Ajuste |
| estoque_anterior | INTEGER NOT NULL, >= 0 |
| estoque_posterior | INTEGER NOT NULL, >= 0 |
| motivo | TEXT nullable; Ajuste exige motivo no DTO |
| data_hora | TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP |

CHECK adicional mantém coerência aritmética dos saldos com o tipo. Operações na expressão SQL usam BIGINT para evitar overflow na própria verificação; as colunas continuam INTEGER. O backend rejeita payloads fora de 0..2147483647 (mínimo 1 para Entrada/Saída) e saldo resultante acima desse limite.

TIMESTAMPTZ foi escolhido para o evento auditável, sem alterar `usuarios.acesso`, que mantém o contrato anterior. A resposta usa ISO 8601 com offset; a interface apresenta o instante no fuso local do navegador.

Índices novos:

- `movimentacoes_produto_data_idx (id_produto, data_hora DESC, id_movimentacao DESC)`: histórico por Produto e suporte à FK; evita índice redundante apenas por Produto.
- `movimentacoes_usuario_idx (id_usuario)`: suporte à FK de responsável.
- `movimentacoes_data_idx (data_hora DESC, id_movimentacao DESC)`: ordenação do histórico global.

Não foi criado índice isolado de tipo: são três valores e os filtros desta versão são no frontend. A PK gera seu índice e a identity gera sua sequência. Não foram adicionados ENUMs, triggers, dependências ou alterações estruturais em `usuarios`/`produtos`.

Nome/SKU do Produto e nome completo do responsável são obtidos por JOIN, sem duplicação na tabela. Esses textos acompanham mudanças futuras no cadastro; IDs, operação, saldos, motivo e instante permanecem no registro. Não há snapshot histórico de nomes nesta etapa, conforme o escopo solicitado.

As FKs impedem exclusão de Produto/Usuário referenciados. DELETE de Produto converte a violação em HTTP 409 com mensagem de histórico. A exclusão de Usuário mantém o tratamento genérico preexistente (500 diante de falha de FK); o banco ainda impede a remoção e o histórico não desaparece. A interface administrativa não recebeu novas operações.

## Transação e concorrência

A infraestrutura existente entrega uma conexão por contexto Flask, com autocommit desligado, rollback e fechamento no teardown. Não há conexão global compartilhada entre requisições.

Registro de movimentação:

1. Verifica disponibilidade da tabela nova.
2. Consulta atividade/perfil do usuário indicado pela sessão, com `FOR SHARE`, mantendo essa condição estável até o término da escrita.
3. Obtém a linha do Produto com `SELECT estoque ... FOR UPDATE`.
4. Valida Produto, saldo disponível e limite INTEGER.
5. Calcula saldo posterior no backend.
6. Executa UPDATE do estoque.
7. Executa INSERT da movimentação, deixando data/hora para o PostgreSQL.
8. Consulta a resposta por JOIN ainda na mesma transação.
9. Faz um único commit.

Falha em qualquer ponto, inclusive INSERT após UPDATE, consulta da resposta ou commit, leva ao rollback. Não há commit intermediário. Falha no UPDATE impede o INSERT. Saída sem saldo retorna 409 antes das escritas.

Com duas saídas concorrentes do mesmo Produto, a segunda aguarda a primeira liberar o lock e só então calcula usando o saldo atualizado. O teste isolado usa duas threads, conexões independentes, eventos de coordenação e um double que adquire lock apenas ao executar o SELECT FOR UPDATE. Partindo de 5, duas saídas de 4 produzem uma operação aceita, uma rejeição e saldo 1, com um único histórico.

Esse teste demonstra a estratégia de sincronização do código. **Não substitui o teste de locks/isolation no PostgreSQL real**, expressamente adiado pelo pedido.

## Estoque inicial e edição de Produto

Estratégia preferida implementada: POST aceita estoque inicial, insere Produto com zero e usa a mesma rotina de movimentação no cursor dessa transação. Se positivo, registra ENTRADA de 0 até o saldo inicial, com motivo `Estoque inicial` e usuário da sessão. Apenas depois faz commit. Falha desfaz Produto e histórico.

Estoque inicial zero não gera evento, mas o cadastro também verifica o schema novo. Antes de aplicar o SQL, o cadastro retorna 503 sem criar Produto, evitando uma disponibilização parcial enganosa. Leituras e edição de campos cadastrais continuam independentes do histórico.

PUT rejeita qualquer presença de `estoque`, inclusive valor igual ao atual, com erro 400 orientando Movimentações. O repository de edição nem recebe esse argumento e seu UPDATE não altera estoque, evitando sobrescrever saldo concorrente. O formulário deixa saldo somente leitura e exclui `estoque` do payload de edição.

Produtos preexistentes não são alterados nem recebem histórico retroativo inventado pelo incremental. Saldos anteriores à instalação, se existirem, serão preservados; a primeira movimentação registra o saldo encontrado como anterior. Não foi presumido que o banco continua vazio.

## API e contratos

| Método | Endpoint | Resultado |
| --- | --- | --- |
| GET | `/movimentacoes` | 200, histórico global |
| GET | `/movimentacoes/<id>` | 200, registro; 404 se ausente |
| GET | `/produtos/<id>/movimentacoes` | 200, histórico do Produto; 404 se Produto ausente |
| POST | `/produtos/<id>/movimentacoes` | 201, movimentação persistida |

Entrada (soma 5):

```json
{"tipo":"ENTRADA","quantidade":5,"motivo":"Recebimento de mercadoria"}
```

Saída (retira 4; motivo opcional):

```json
{"tipo":"SAIDA","quantidade":4}
```

Ajuste (define saldo final em 8):

```json
{"tipo":"AJUSTE","novo_estoque":8,"motivo":"Contagem física do depósito"}
```

Ajuste usa exclusivamente `novo_estoque` no payload, inclusive zero. `quantidade` é rejeitada nesse payload, isoladamente ou junto de `novo_estoque`. Na tabela e resposta, `quantidade` guarda o saldo alvo para Ajuste. Ajuste pode aumentar, diminuir ou confirmar saldo igual; motivo é obrigatório.

A resposta contém: `id_movimentacao`, `id_produto`, `id_usuario`, `tipo`, `quantidade`, `estoque_anterior`, `estoque_posterior`, `motivo`, `data_hora`, `produto_nome`, `produto_codigo`, `usuario_nome`.

Listas ordenam por `data_hora DESC, id_movimentacao DESC`. Produto existente sem histórico retorna lista vazia. Não há PUT, PATCH ou DELETE de movimentações (405 nas rotas existentes).

HTTP: 400 validação/JSON, 401 sem autenticação, 403 inatividade/perfil, 404 ausente, 409 saldo insuficiente/limite excedido, 503 schema indisponível e 500 genérico para falha inesperada. JSON de erro usa `message`; logs de falha expõem apenas o tipo da exceção.

## Segurança e autorização

- Todas as rotas exigem autenticação. Responsável vem de `session['user_id']`, nunca do payload.
- DTO rejeita campos extras, incluindo IDs, data/hora e saldos calculados, além de booleanos, tipos indevidos, valores negativos/zero onde proibidos e motivo de Ajuste vazio.
- Administrador (`admin`/`administrador`), supervisor e operador ativos podem registrar. Nenhum novo perfil foi criado.
- Atividade é revalidada no banco para todo acesso ao histórico. Escritas também revalidam perfil. Sessão antiga com usuário desativado não autoriza movimentação.
- Cadastro de Produto mantém exigência administrativa e revalida atividade/perfil persistidos.
- SQL parametrizado; dados exibidos no histórico passam por escape de HTML.
- Login, Logout, Redis, Flask-Session, CORS, duração de sessão e `credentials: 'include'` foram preservados.
- Sem garantia adicional de imutabilidade contra edição SQL manual por administrador do banco: a garantia desta etapa é da API, das transações e das FKs.

## Frontend

Nova página `movimentacoes.html`, usando a identidade visual existente e CSS compartilhado, com `js/movimentacoes.js` e `css/movimentacoes.css`.

Cadastro oferece Produto/nome/SKU/saldo, tipo, quantidade ou novo saldo e motivo. Histórico mostra data/hora, Produto, SKU, tipo com badges distintos, quantidade/saldo alvo, saldo anterior/posterior, responsável e motivo. Filtros por Produto, tipo e nome/SKU; paginação de dez linhas. Filtro de período não foi incluído por ser opcional. Listagem é carregada integralmente; paginação/filtros são locais nesta versão.

Após sucesso, saldo e histórico são recarregados. Estados de carregamento, lista vazia, falha, payload inválido e saldo insuficiente são explícitos. Registro é bloqueado durante envio/carregamento. Falha de rede/servidor não dispara repetição automática e exige atualizar antes de nova tentativa, pois a resposta pode falhar após commit. Não foi introduzida idempotência de requisições nesta etapa.

Sidebar de todas as páginas autenticadas e busca global incluem Movimentações. Dashboard e Login tiveram os textos de recurso futuro corrigidos. Inventário ganhou atalhos por Produto (`?produto=<id>`) e dentro do modal. Administração, Perfil e Configurações só receberam o link de navegação.

## Testes e evidências

| Conjunto | Quantidade | Resultado/natureza |
| --- | ---: | --- |
| `test_core.py` | 23 | Anteriores, isolados |
| `test_produtos.py` | 30 | Anteriores, isolados |
| `test_redis_session.py` | 5 | Anteriores; Redis real, usuário/repository isolados |
| `test_movimentacoes.py` | 33 | Novos, DTO/service/repository/HTTP, banco em memória |
| **Total Python** | **91** | **Zero falhas/erros/pulados** |
| Chrome headless | 35 | API simulada; não somado aos testes Python |
| Sintaxe Python | 42 arquivos | AST aprovado |
| Sintaxe JavaScript | 11 arquivos | node --check aprovado |
| HTML | 7 páginas | IDs únicos, links locais existentes e UTF-8 conferidos |
| SQL | 3 arquivos | Revisão estática; tabelas equivalentes, sem execução |

Regressões anteriores só foram adaptadas ao responsável obrigatório no cadastro, à remoção de estoque no PUT e ao novo blueprint/rota. Casos de validação, SQL parametrizado, conflitos, erros internos e autenticação foram mantidos. Nenhum teste anterior foi removido.

Novos cenários incluem Entrada 10→15, Saída 10→6, rejeição de Saída 3−5 sem persistência, Ajuste 10→8, aumento, saldo igual e zero; todos os tipos inválidos, campos de servidor e contratos ambíguos; inexistência, autenticação e revogação; imutabilidade; estoque inicial e bloqueio antes do schema; FK de Produto; rollback por falha de UPDATE/INSERT/commit; duas saídas concorrentes coordenadas.

A suíte foi executada pelo `.venv`, com `INVENTAIRE_TEST_REDIS=1` e bloqueio externo de `psycopg2.connect`: **zero tentativas PostgreSQL**. Os testes de conexão existentes conservaram seus próprios doubles. Redis criou/removeu somente chaves de UUID próprias dos cinco testes; não houve alteração de sessões desconhecidas.

Navegador verificou os fluxos, contrato de Ajuste, seleção por URL, permissão de operador/leitor, escape HTML, filtros, mensagens de erro, recuperação, estoque somente leitura, omissão de saldo no PUT e credentials. Inspeção visual em 1440×1000 e verificação de layout em 390×844. Recursos CDN foram bloqueados: ícones externos e Chart.js real não foram homologados nesta fase; fallback existente preservado. Nenhuma resposta do backend real foi usada pelo navegador de validação.

Evidências locais, sem segredos:

- `.validation/etapa2b_suite.py` e `etapa2b_suite.json`: execução e totais da suíte com PostgreSQL bloqueado.
- `.validation/etapa2b_browser.cjs`, `etapa2b_mock.js`, `etapa2b_ui_checks.js`, `etapa2b_inventory_checks.js` e `etapa2b_ui.json`: navegador e API simulada.
- `.validation/etapa2b_movimentacoes.png`: captura da tela com dados exclusivamente simulados.
- `.validation/etapa2b_static.py` e `etapa2b_static.json`: sintaxe, links, equivalência estática SQL e lista de alterações.
- `.validation/etapa2b_initial_hashes.json`: comparação de preservação; nenhum conteúdo de ambiente.
- `.validation/etapa2b_fix_text.py`: correção local de acentuação detectada na inspeção visual; não é dependência de execução.

Perfis temporários do Chrome ficaram em `.validation/etapa2b_chrome_*`; processos de navegador e servidor de validação foram encerrados. Não foram iniciados novos servidores da aplicação contra o banco real.

## Arquivos modificados

21 arquivos existentes modificados, nenhum excluído:

| Grupo | Arquivos |
| --- | --- |
| Backend | `backend/app.py`, `backend/core/errors.py`, `backend/controller/produto_routes.py`, `backend/services/produto_service.py`, `backend/repository/produto_repository.py`, `backend/schemas/produto_dto.py` |
| SQL completo | `backend/assets/inventaire_schema.sql`, `backend/assets/tabelas_spi-postgres.sql` |
| Testes anteriores | `backend/tests/test_core.py`, `backend/tests/test_produtos.py` |
| JavaScript | `js/api.js`, `js/common.js`, `js/inventario.js` |
| HTML | `inventario.html`, `dashboard.html`, `login.html`, `administracao.html`, `perfil.html`, `configuracao.html` |
| Documentação | `README.md`, `backend/README.md` |

## Arquivos criados

11 arquivos de entrega, além dos auxiliares de validação listados acima:

- `backend/models/movimentacoes.py`
- `backend/schemas/movimentacao_dto.py`
- `backend/repository/movimentacao_repository.py`
- `backend/services/movimentacao_service.py`
- `backend/controller/movimentacao_routes.py`
- `backend/tests/test_movimentacoes.py`
- `backend/assets/etapa_2b_movimentacoes.sql`
- `movimentacoes.html`
- `js/movimentacoes.js`
- `css/movimentacoes.css`
- `ETAPA_2B_MOVIMENTACOES_INVENTAIRE.md`

## SQL manual — próxima ação

O único script incremental a executar é:

**`backend/assets/etapa_2b_movimentacoes.sql`**

1. Abra o Query Tool no pgAdmin conectado ao banco **inventaire**, no servidor destinado a este projeto.
2. Confira o destino e abra o arquivo incremental completo.
3. Execute uma única vez. Não execute schemas completos nem bootstrap novamente.
4. Caso ocorra erro, preserve a mensagem sem segredos e não tente apagar/recriar tabelas. Encerre eventual transação abortada com ROLLBACK antes de novas consultas.
5. Confirme a aplicação manual para iniciar uma homologação real separada.

O arquivo **não foi executado pelo agente**. Nenhuma homologação PostgreSQL real foi iniciada nesta fase.

## Preservação e pendência

- Nenhum segredo foi exposto: sem SECRET_KEY, DB_PASSWORD, cookies, IDs de sessão, hashes bcrypt ou connection strings com senha nas saídas/evidências.
- `backend/.env`, relatórios históricos, seed de instruções, diagramas e arquivos legados permaneceram inalterados, conferidos por hash.
- Nenhum PostgreSQL foi conectado ou alterado; nenhum banco SPI foi acessado/alterado.
- Nenhum CREATE/ALTER/DROP/TRUNCATE/migration foi executado. SQL preparado apenas em arquivos.
- Nenhum Produto/Usuário preexistente foi alterado ou removido pelo agente.
- Nenhum commit, push ou Pull Request. A pasta continua sem repositório Git.
- Nenhum arquivo fora deste workspace foi alterado pelas operações da tarefa; temporários, perfis e evidências ficaram em `.validation/`.

**Pendência: Aplicação manual do SQL no pgAdmin + homologação real.**

Na continuação, após confirmação explícita, inspecionar primeiro os dados existentes e validar tabela, FKs, Entrada/Saída/Ajuste, responsáveis, rollback, concorrência real, histórico, frontend integrado e estratégia de limpeza dos registros exclusivos de teste. A limpeza deve respeitar a imutabilidade/FKs e não apagar dados preexistentes. Esta entrega termina antes dessa continuação.
