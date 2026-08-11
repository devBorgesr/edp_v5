# Pré-registro — 3 frentes de estabilização do ecossistema

**Data de pré-registro: 06/08/2026**, antes de qualquer implementação das
três frentes. Estrutura seguindo
[`TEMPLATE_PREREGISTRO.md`](https://github.com/devBorgesr/lab_edp/blob/main/docs/TEMPLATE_PREREGISTRO.md)
(lab_edp), adaptada para trabalho de engenharia como já foi feito em
[`preregistro_fix_corrupcao_json.md`](preregistro_fix_corrupcao_json.md).

> **Régua de método.** Hipóteses, critérios de decisão e constantes abaixo
> são congelados ANTES de escrever qualquer linha das três frentes. Não se
> descongela para ajustar limiar depois de ver resultado — se o limiar
> estiver errado, o registro é uma frente NOVA, não uma edição desta.
> Cada frente tem um critério que pode REPROVÁ-LA por completo; "H0 vencer"
> (a frente não valer a pena) é achado válido, não fracasso.

**Escopo.** Três frentes independentes, sem ordem obrigatória entre si,
exceto onde declarado:

| ID | Frente | Repo | Status NORTE.md |
|---|---|---|---|
| **PR-A** | CI mínima (lint + smoke) | `sf_exportador` | não passa no teste de escopo — ver §0.3 |
| **PR-B** | "Sala de Espelhos" | os três | **FILA_FUTURO** (fora de escopo) + gate de ToS REPROVADO — ver §B |
| **PR-C** | CHANGELOG do kernel | `edp_v5_main` | não passa no teste de escopo — ver §0.3 |

---

## §0 — Passo 0: verificação de premissas (feita ANTES de desenhar)

Toda alegação abaixo foi verificada em 06/08/2026 contra o código/estado
real, não herdada de documento. Premissas que falharam estão marcadas.

### 0.1 — Premissas de PR-A (exportador)

| Premissa | Verificação | Status |
|---|---|---|
| Não existe tooling JS no repo | `ls package.json .eslintrc* eslint.config.*` → nada | ✅ confirmado |
| Não existe CI | `ls .github/` → não existe o diretório | ✅ confirmado |
| Node disponível para rodar algo | `node --version` → v20.19.0 | ✅ confirmado |
| Volume de código a cobrir | 17 arquivos `.js` próprios, 5.561 linhas | ✅ medido |
| Há libs vendored que NÃO devem ser lintadas | `jspdf.min.js` (420 KB), `jszip.min.js` (97 KB) | ✅ confirmado |
| Código é ES module | `grep "^import \|^export "` → **zero ocorrências** | ❌ **FALSA** — são scripts clássicos; `service_worker` não declara `type: module` |
| Globais de plataforma usados | `chrome.{alarms,debugger,permissions,runtime,scripting,storage,tabs}` | ✅ inventariado |

**Consequência da premissa falsa:** qualquer config de lint precisa
declarar `sourceType: "script"` (não `module`) e os globais `chrome`/
`window`, senão a CI reprova o código correto — falso positivo que
destruiria a confiança na CI logo no primeiro uso.

### 0.2 — Premissas de PR-C (CHANGELOG)

| Premissa | Verificação | Status |
|---|---|---|
| Kernel não tem CHANGELOG | `ls CHANGELOG*` → não existe | ✅ confirmado |
| Não há âncoras de versão (folha em branco) | `git tag` → **7 tags**: `v3.15-stable`, `v3.16-quarantine-stable`, `v3.17-hardening-fase1`, `v3.18-hardening-fase2`, `v3.19-hardening-fase3`, `v3.20-hardening-fase4`, `v3.21-hardening-closed` | ❌ **FALSA** — há âncoras reais |
| Versão declarada é única | `pyproject.toml` → `3.21.0`; título do `README.md` → `EDP v3.3` | ❌ **FALSA** — duas versões divergentes no mesmo repo |
| Profundidade do histórico | 194 commits, de 20/05/2026 a 06/08/2026 | ✅ medido |

**Consequência:** PR-C não é "escrever do zero", é **reconstruir a partir
de âncoras existentes** — o que é mais barato e mais verificável. E a
divergência `3.21.0` vs `v3.3` é um achado que o CHANGELOG precisa
resolver, não herdar.

### 0.3 — Premissa transversal: as três frentes passam no NORTE.md?

Verificado contra o [`NORTE.md`](../NORTE.md) atualizado em 06/08/2026.

- **PR-A e PR-C: NÃO passam no teste de escopo.** Nenhuma das duas
  aproxima demonstravelmente o primeiro cliente pagante dentro do prazo
  (02/09/2026), e nenhuma cai nas exceções permanentes (não são perda de
  dados, segurança nem obrigação legal). Registro isto explicitamente em
  vez de silenciar: **as duas só têm justificativa porque foram pedidas
  diretamente pelo pesquisador**, que é quem pode suspender a própria
  régua. Não são exceções — são decisões dele.
- **PR-B: fora de escopo por dois motivos independentes** — está em
  `FILA_FUTURO.md` desde 06/08, e o gate de ToS (§B.1) reprovou.

---

## §A — PR-A: CI mínima do exportador (lint + smoke)

### A.1 Pergunta de pesquisa

> Uma CI mínima (lint + smoke, zero dependências de npm) no
> `sf_exportador` captura pelo menos uma classe de defeito real que hoje
> chega ao disco sem ser notada — ou é teatro de processo?

### A.2 Motivação / contexto provado

- O repo tem **zero teste automatizado e zero CI** (§0.1), contra
  `pytest` + `tests.yml` nos outros dois repos do ecossistema. É a
  divergência de disciplina mais forte entre os três, já registrada na
  auditoria de 06/08.
- Dois defeitos reais neste mesmo repo chegaram a produção sem detecção,
  ambos no mesmo campo, ambos invisíveis do lado do emissor:
  `toISOString()` (03/08) e escala em milissegundos (06/08, corrigido em
  `64190e2`). **Nenhum dos dois seria pego por lint** — são erros
  semânticos, não sintáticos. Isso é evidência a favor de H0 e está
  registrado aqui *antes* de medir, não depois.
- A verificação atual é um botão de self-test dentro do popup
  (`popup.js`) — real, mas exige um humano clicar; não roda em PR.

### A.3 Hipóteses

- **H1** — A CI mínima captura ≥ 4 dos 6 defeitos semeados (§A.6),
  incluindo **pelo menos 1 defeito de comportamento** (não só sintaxe).
  Nesse caso a CI vale o custo e entra permanentemente.
- **H0** — A CI captura ≤ 3 dos 6, ou captura só defeitos de sintaxe.
  Nesse caso **a CI não entra como está**: ou vira só `node --check`
  (custo ~zero, valor ~zero, honestamente rotulado como "verificação de
  sintaxe", não "CI"), ou a frente é abandonada e registrada como
  refutada. **H0 vencer é achado válido** — significa que o valor real
  está em teste de comportamento, não em CI, e o esforço deve ir para lá.

### A.4 Desenho — o que a CI faz

Três camadas, todas sem `npm install` (Node 20 traz tudo embutido — evita
`package-lock.json`, supply chain e manutenção de dependência num repo que
hoje tem zero):

| Camada | Rótulo | Mecanismo | Custo |
|---|---|---|---|
| 1 | `syntax` | `node --check` em cada `.js` próprio | ~0s |
| 2 | `manifest` | valida `manifest.json` (JSON parseável; `version` bate com o topo do `CHANGELOG.md`; toda permissão declarada é usada em algum `.js`; todo `chrome.*` usado tem permissão declarada) | ~0s |
| 3 | `unit` | `node --test` sobre funções puras extraíveis (`sandbox.normalizePath`, `har_analyzer` stats, a normalização de timestamp de `live_feed.js`) | ~1s |

**Controle negativo obrigatório:** a CI roda contra o `HEAD` atual
(`64190e2`) e **deve passar verde**. Se acusar erro no código que hoje
funciona, é falso positivo — a CI é rejeitada e reescrita, não o código.

**Fora de escopo desta frente** (registrado para não virar creep):
Playwright, Jest, headless Chrome, cobertura, e qualquer coisa que exija
`npm install`. Se H1 passar e depois quisermos e2e, é uma frente nova.

### A.5 Métricas

- **Taxa de captura** = (defeitos semeados detectados) / 6. A CI
  "detecta" um defeito sse o job **falha** (exit ≠ 0) com o defeito
  aplicado e **passa** sem ele.
- **Taxa de falso positivo** = job falha no `HEAD` limpo. Alvo: 0.
  Qualquer valor > 0 reprova a frente independentemente da taxa de
  captura.
- **Tempo de execução** do workflow completo. Teto: 90s.

### A.6 Dataset CONGELADO — os 6 defeitos semeados

Aplicados **um de cada vez** sobre `64190e2`, em branch descartável,
revertidos após medir. Congelados aqui antes de escrever a CI, para que a
CI não seja desenhada para passar no próprio teste:

| # | Classe | Defeito concreto |
|---|---|---|
| D1 | sintaxe | parêntese não fechado em `live_feed.js` |
| D2 | sintaxe | `const` redeclarado no mesmo escopo em `panel.js` |
| D3 | manifesto | `manifest.json` com vírgula sobrando (JSON inválido) |
| D4 | manifesto | permissão `debugger` removida do manifesto, mas `chrome.debugger` continua usado em `debugger_capturer.js` |
| D5 | **comportamento** | `sandbox.normalizePath` deixa de rejeitar `..` (regressão de contenção do sandbox) |
| D6 | **comportamento** | a normalização de timestamp de `live_feed.js` volta a `Date.now()` em ms (regressão exata do fix de 06/08) |

D5 e D6 são os que decidem H1 vs H0: são os únicos que exigem a camada
`unit`. Se a CI pegar só D1–D4, H0 venceu por definição.

### A.7 Critério de decisão (PASSA/FALHA) — congelado

| # | Critério | Limiar |
|---|---|---|
| (a) | Falso positivo no `HEAD` limpo | **0** — qualquer falha aqui reprova a frente |
| (b) | Defeitos capturados | **≥ 4 de 6** |
| (c) | Defeitos de comportamento capturados | **≥ 1** (D5 ou D6) |
| (d) | Tempo do workflow | **≤ 90s** |
| (e) | Dependências instaladas via npm | **0** |
| (f) | Nenhum arquivo vendored lintado | `jspdf.min.js`/`jszip.min.js` excluídos por caminho explícito |

**PASSA H1 sse (a)∧(b)∧(c)∧(d)∧(e)∧(f).** Falhar (c) mas passar (b) ⇒
H0: a CI vira só verificação de sintaxe, rotulada honestamente como tal.

### A.8 Constantes congeladas

| Constante | Valor |
|---|---|
| `NODE_VERSION` | `20` |
| `DEFEITOS_SEMEADOS` | 6 (D1–D6, §A.6) |
| `MIN_CAPTURA` | 4 |
| `MIN_CAPTURA_COMPORTAMENTO` | 1 |
| `MAX_TEMPO_WORKFLOW_S` | 90 |
| `MAX_FALSO_POSITIVO` | 0 |
| `EXCLUIDOS_DO_LINT` | `jspdf.min.js`, `jszip.min.js` |
| `SHA_BASE` | `64190e2` |

### A.9 Riscos a priori

1. **Funções puras não são extraíveis sem refatorar.** `sandbox.js` e
   `live_feed.js` são IIFEs que dependem de `chrome.*`. Mitigação: a
   camada `unit` testa só o que já é extraível sem tocar no código de
   produção. Se D5/D6 exigirem refatorar produção para serem testáveis,
   **isso reprova a frente nesta rodada** (refatorar produção para caber
   no teste é o rabo abanando o cachorro) e vira uma frente separada.
2. **Falso positivo por config de lint errada** (premissa falsa §0.1).
   Mitigação: critério (a) com limiar 0.
3. **CI verde vira selo de qualidade falso.** Uma CI que só pega sintaxe
   pode dar a impressão de que o repo está coberto. Mitigação: se H0
   vencer, o README declara explicitamente "verificação de sintaxe, não
   cobertura de comportamento".

---

## §B — PR-B: "Sala de Espelhos" — GATE REPROVADO

### B.1 Gate 0 (bloqueante): revisão dos Termos de Uso

Na análise de 06/08 eu registrei que a afirmação "não viola ToS", repetida
várias vezes na conversa de origem, era **inferência por analogia, não
pesquisa** — e que exigia leitura do texto real antes de qualquer código.
Fiz essa leitura agora. **O gate não passa.** Evidência textual:

**Consumer Terms of Service, Seção 3 — "Use of our Services"**, citação
verbatim:

> "Except when you are accessing our Services via an Anthropic API Key or
> where we otherwise explicitly permit it, to access the Services through
> automated or non-human means, whether through a bot, script, or
> otherwise."

> "To crawl, scrape, or otherwise harvest data or information from our
> Services other than as permitted under these Terms."

**Achado que não estava previsto — o exportador ATUAL já opera nessa
tensão, não só a ideia nova.** Verificado em código, não suposto:

- [`content.js:44`](https://github.com/devBorgesr/Exportador_Edp) —
  `await fetch('/api/account_profile', { credentials: 'include' })`
- [`content.js:74`](https://github.com/devBorgesr/Exportador_Edp) —
  `await fetch(.../api/organizations/${orgUUID}/chat_conversations/${convUUID}?tree=True&rendering_mode=messages&render_all_tools=true...)`

Isso **não é** o sensor passivo descrito em `COPILOT_ARCHITECTURE.md` e na
conversa de origem. É um script fazendo requisição programática a
endpoints internos do serviço, autenticado com o cookie de sessão do
usuário — exatamente a forma descrita na cláusula acima. O `interceptor.js`
(que só observa `window.fetch` que a página já faz) realmente é passivo;
o caminho de extração principal (`getSession()` → API) não é.

**Usage Policy — "Do Not Abuse our Platform"**, verbatim:

> "Coordinate malicious activity across multiple accounts to avoid
> detection or circumvent product guardrails"

> "Utilization of inputs and outputs to train an AI model (e.g., 'model
> scraping' or 'model distillation') without prior authorization from
> Anthropic"

### B.2 Veredito do gate, item a item

| Sub-ideia | Veredito | Base |
|---|---|---|
| Capturar o próprio raciocínio via `interceptor.js` (observação de tráfego que a página já gera) | **Zona cinzenta defensável** — é observação, não acesso automatizado | Nenhuma cláusula encontrada proíbe observar o que a própria página exibe |
| Extrair sessões via `fetch()` à API interna (`content.js`) | **Em tensão direta** com a cláusula de acesso automatizado | Seção 3, verbatim acima |
| **Abrir 3 contas gratuitas e agregá-las para multiplicar capacidade** | **Não vou projetar isto** | É circunvenção de limite de produto por desenho; o valor da ideia *depende* de contornar a capacidade da conta gratuita |
| Alimentar a memória do EDP com inputs/outputs capturados | **Requer decisão informada** | Depende de a operação do EDP contar ou não como "train an AI model"; o EDP tem calibradores que aprendem com uso |

### B.3 Por que não há H1/H0 nesta frente

Um pré-registro exige que o experimento possa rodar. Aqui o gate
bloqueante reprovou, então **não há desenho experimental a congelar** —
congelar hipóteses para algo que não deve ser executado seria teatro de
método. O que fica registrado é o gate e sua evidência.

Ressalva honesta sobre os limites desta análise: **não sou advogado, e
esta é leitura de texto público, não parecer jurídico.** Além disso os
termos mudam; a leitura vale para 06/08/2026. O que afirmo é factual e
verificável: as cláusulas acima existem com esse texto, e o código em
`content.js:44,74` faz o que descrevi.

### B.4 Caminhos legítimos, se o pesquisador quiser prosseguir

Registrados porque recusar sem alternativa não é útil:

1. **Perguntar à Anthropic.** A própria cláusula prevê "where we
   otherwise explicitly permit it". Um pedido de autorização descrevendo
   o uso (memória pessoal, dado próprio, uso não comercial) é o caminho
   limpo — e transforma uma zona cinzenta em um "sim" ou "não" registrado.
2. **Trocar o caminho de extração pela API oficial** (Anthropic API com
   chave própria), que a cláusula isenta explicitamente. Isso resolve a
   tensão do `content.js` de raiz, ao custo de não capturar conversas da
   interface web.
3. **Usar exportação manual oficial** (a exportação de dados que a
   própria conta oferece) como fonte, em vez de `fetch()` programático.
4. **Restringir a uma conta só**, abandonando a parte de multiplicar
   contas — remove o item que eu não projetaria e mantém a maior parte do
   valor de "ver o próprio raciocínio ao longo do tempo".

Recomendação: **(1) antes de qualquer código**, e (2) ou (3) como desenho
técnico. A frente permanece em `FILA_FUTURO.md` até isso.

### B.5 Ação imediata recomendada (independente da Sala de Espelhos)

O achado do `content.js` **não é sobre a ideia futura, é sobre o que já
roda hoje**. Recomendo tratar como item próprio, com prioridade acima de
PR-A e PR-C: decidir se o caminho `fetch()` à API interna continua,
migra para (2)/(3), ou é documentado como risco aceito e conhecido — hoje
ele está descrito na documentação do projeto como "sensor passivo", o que
**não corresponde ao código**. Corrigir essa descrição é, no mínimo,
dívida de documentação honesta.

---

## §C — PR-C: CHANGELOG do kernel

### C.1 Pergunta de pesquisa

> É possível reconstruir um `CHANGELOG.md` do `edp_v5_main` em que **toda
> entrada** rastreia a uma tag ou SHA verificável — ou o histórico é
> irregular demais e o documento viraria narrativa retroativa?

### C.2 Motivação / contexto provado

- Não existe `CHANGELOG.md` (§0.2), enquanto o `sf_exportador` tem um
  mantido e datado. Assimetria dentro do mesmo ecossistema.
- Existem **7 tags** de versão, todas com nome semântico de fase
  (`v3.15-stable` → `v3.21-hardening-closed`) — âncoras reais.
- Há **divergência de versão viva**: `pyproject.toml` = `3.21.0`,
  título do `README.md` = `EDP v3.3`. Um CHANGELOG que não resolva isso
  propaga a confusão em vez de fechá-la.

### C.3 Hipóteses

- **H1** — ≥ 90% das entradas do CHANGELOG rastreiam a uma tag ou SHA, e
  a divergência de versão fica resolvida (uma fonte única declarada).
- **H0** — < 90% rastreiam. Nesse caso **não se escreve um CHANGELOG
  completo**: escreve-se um que começa em `v3.15-stable` (a primeira tag)
  e declara explicitamente "histórico anterior não reconstruído — ver
  `git log`". Documento menor e honesto vence documento completo e
  parcialmente inventado. **H0 vencer é achado válido.**

### C.4 Desenho

1. Extrair `git tag` + data + SHA de cada tag.
2. Para cada intervalo entre tags, listar commits (`git log tagN..tagN+1
   --oneline`).
3. Agrupar por tipo a partir do prefixo convencional já usado no repo
   (`feat:`, `fix:`, `docs:`, `refactor:`) — o repo já usa Conventional
   Commits de forma majoritária (verificado: os 15 commits mais recentes
   seguem).
4. Formato **Keep a Changelog** (mesmo do exportador, consistência
   dentro do ecossistema).
5. Resolver a divergência de versão: declarar `pyproject.toml` como fonte
   única (é o que a ferramenta de build lê) e registrar no CHANGELOG que
   o título do README estava defasado — **sem editar o README nesta
   frente** (mudança em outro arquivo = outra frente; registro aqui e
   deixo a decisão).

### C.5 Métricas

- **Rastreabilidade** = (entradas com tag ou SHA citado) / (total de
  entradas). Contada manualmente sobre o documento final.
- **Cobertura de commits** = (commits representados em alguma entrada) /
  194. Sem limiar — só medida e reportada, porque agrupar commits é
  legítimo (10 commits de uma fase podem virar 1 entrada).

### C.6 Critério de decisão — congelado

| # | Critério | Limiar |
|---|---|---|
| (a) | Rastreabilidade | **≥ 90%** |
| (b) | Divergência de versão resolvida e declarada | binário: sim/não |
| (c) | Nenhuma entrada afirma comportamento não verificável no diff | binário |
| (d) | Suíte continua verde (o CHANGELOG não toca código) | 234 passed |

**PASSA H1 sse (a)∧(b)∧(c)∧(d).** Falhar (a) ⇒ H0: CHANGELOG começa em
`v3.15-stable`, com a lacuna declarada em vez de preenchida.

### C.7 Constantes congeladas

| Constante | Valor |
|---|---|
| `TAGS_ANCORA` | 7 (`v3.15-stable` … `v3.21-hardening-closed`) |
| `COMMITS_TOTAIS` | 194 |
| `MIN_RASTREABILIDADE` | 0.90 |
| `FORMATO` | Keep a Changelog |
| `FONTE_DE_VERSAO` | `pyproject.toml` (`3.21.0`) |
| `SUITE_BASELINE` | 234 passed, 1 deselected |

### C.8 Riscos a priori

1. **Narrativa retroativa.** O maior risco é escrever "o que deveria ter
   acontecido" em vez do que o diff mostra. Mitigação: critério (c) e a
   exigência de SHA por entrada.
2. **Editar o README junto.** Tentação de "já que estou aqui". Mitigação:
   declarado fora de escopo em §C.4 item 5.
3. **CHANGELOG que ninguém mantém.** Um documento que congela hoje e
   apodrece é pior que nenhum. Mitigação: não mitigado nesta frente —
   registrado como risco aceito; a manutenção depende de disciplina
   humana, não de mecanismo.

---

## Checklist do template (auto-avaliação honesta)

| # | Seção | PR-A | PR-B | PR-C |
|---|---|---|---|---|
| 1 | Título + pergunta | ✅ | ⊘ gate reprovado | ✅ |
| 2 | Régua/compromisso | ✅ (topo) | ✅ | ✅ |
| 3 | Contexto provado | ✅ | ✅ | ✅ |
| 4 | H1 + H0 | ✅ | ⊘ §B.3 explica | ✅ |
| 5 | Condições/desenho + controle negativo | ✅ | ⊘ | ✅ |
| 6 | Critério de decisão numérico | ✅ | ⊘ | ✅ |
| 7 | Data de pré-registro | ✅ | ✅ | ✅ |
| 8 | Dataset congelado | ✅ (6 defeitos) | ⊘ | ✅ (tags/commits) |
| 9 | Métricas com fórmula | ✅ | ⊘ | ✅ |
| 10 | Anti-mock / isolamento | ✅ (branch descartável, revert) | ⊘ | n/a (não toca código) |
| 11 | Constantes congeladas | ✅ | ⊘ | ✅ |

`⊘` = não aplicável porque a frente foi bloqueada no gate, com a razão
registrada em §B.3 — não é seção esquecida.

---

## Ordem recomendada de execução

1. **§B.5** (decidir o que fazer com o `fetch()` do `content.js` e com a
   descrição "sensor passivo" que não corresponde ao código) — é o único
   item com consequência fora do repositório.
2. **PR-C** (CHANGELOG) — menor, sem risco, fecha lacuna de documentação.
3. **PR-A** (CI) — maior esforço, e o único que pode ser refutado por
   completo pelo próprio critério.
4. **PR-B** — bloqueado até §B.4 item (1) ter resposta.

Nada acima é executado sem autorização explícita, frente a frente.
