# RELATÓRIO — FASE 0: memória real vs. negações (confirmação de mecanismos)

**Tipo:** medição pura. ZERO conserto — nenhum diff em `edp/`, nenhuma branch de
fix. Esta branch (`fase0/memoria-vs-negacoes`) contém apenas o harness de medição
e este relatório.

---

## §0. PREVISÕES — registradas ANTES de qualquer teste rodar (sem ajuste pós-hoc)

Registradas em 2026-07-05, antes de qualquer execução dos Testes 1/2:

- **P1:** no store LIMPO, `4c57ed7a` chega aos 3 checkpoints (a contaminação era
  o fator dominante). → **REFUTADA.** Store limpo (0 negações); o alvo passa CP1
  e CP2 mas **morre no CP3** — `"chave-valor"` ausente do prompt, ids rastreados
  no prompt final = `[]`. A aposta a priori (contaminação como fator dominante)
  **caiu**: mesmo sem nenhuma negação, a memória real não chega ao prompt. O
  gargalo é **estrutural no read-path**, não a contaminação.
- **P2:** no store CONTAMINADO, o rank BM25 isolado da negação > rank da
  `4c57ed7a` para a query canônica (eco de query). → **CONFIRMADA.** BM25 puro:
  as 3 negações nas **posições 1–3** (score ~22) vs. o alvo na **posição 17**
  (score 8.5); RRF top-5 = **100% lixo do teste**, alvo ausente. O eco de query
  é real e forte — a negação contém a pergunta verbatim e vence lexicalmente.
- **P3:** o alvo passa o CP1 e morre no CP2 por **seen_ids**. → **REFUTADA NO
  MECANISMO.** `seen_ids` **não foi exercitado como causa em nenhum teste**. No
  store limpo o alvo sobrevive ao CP2 e morre no CP3 (Defeito 1, §6). No
  contaminado o afogamento é **no próprio top-k do retrieve (CP1)** — as
  negações dominam antes de qualquer pós-processamento. O checkpoint onde a
  memória morre depende do store, e em nenhum é o `seen_ids`.

**Síntese honesta:** a hipótese única de trabalho (contaminação → seen_ids) caiu
por inteiro. Emergiram **dois defeitos independentes e aditivos**: (D1) um
**estrutural de read-path** que mata a memória mesmo no store limpo (CP3), e (D2)
o **eco de query no BM25** que afoga a memória no top-k quando há contaminação
(CP1). D1 é o dominante — atinge toda conversa, com ou sem negações.

---

## §1. OBSTÁCULO DE AMBIENTE (regra da tarefa: reportar e parar, não improvisar)

Os dois stores sob teste vivem na máquina do pesquisador (Windows) e **não são
alcançáveis deste ambiente**:
- `C:\edp_data` (produção, fonte da cópia limpa do Teste 1) — inexistente aqui;
- `C:\edp_data_hybrid_test` (contaminado, espécime do Teste 2) — inexistente aqui.

Verificação: `ls C:\edp_data /c/edp_data /mnt/c/edp_data` → nada; `find /` por
`edp_data` → nada. O único dado local é a fixture `/content/edp_v3_memory`
(11 entries, **sem** memórias Redis e **sem** negações — inútil para os vereditos).

**Aplicação da regra anti-recontaminação por analogia:** assim como "método que
exige estado do servidor → reportar o obstáculo e parar", dados que só existem na
máquina do pesquisador → **nenhum número de veredito é fabricado aqui**. Em vez
disso, esta fase entrega o instrumento pronto e validado; a rodada final é sua,
em minutos, com os comandos abaixo.

---

## §2. O INSTRUMENTO — `fase0_checkpoints.py` (chamada direta, zero chat)

Cumpre TODAS as regras da fase:
- **Sem turno vivo:** chama diretamente `mem.retrieve` (CP1 — o mesmo objeto e
  método do websocket:716), `rt._retrieve_context(QUERY)` (CP2 — inclui o
  retrieve interno da :2334 e o skip de seen_ids :2340-2342) e
  `rt._build_enriched_context(QUERY, system)` (CP3 — prompt final renderizado).
  O runtime vem de `registry.get_runtime` **sem conectar LLM** (nenhuma resposta
  é gerada, logo **nenhuma negação nova é gravada**).
- **Anti-mutação:** snapshot pristine + restore antes de CADA checkpoint (o
  retrieve muta `acessos++` e o `_retrieve_context` grava co-ocorrência); no fim,
  restore + hash de no-divergência — o store sai como entrou.
- **IDs, não tamanhos:** cada CP imprime os ids (8 chars) presentes; no CP2, o
  harness captura **em processo** (spy no `MemoryStore.retrieve`, sem tocar
  `edp/`) o que o retrieve interno da :2334 devolveu, e reconstrói a janela
  imediata (últimos 6 turnos não-summary) — se o alvo entrou no retrieve interno,
  está na janela e sumiu dos blocks, o veredito impresso é **"seen_ids skip"**;
  se entrou e não está na janela, **"descartado pós-retrieve (outra causa)"**.
- **Teste 2 isolado:** indexa o store contaminado com o `BM25` REAL do
  `retrieval_hybrid` e imprime o ranking lexical PURO (com marcação
  `NEGACAO`/`CONTEUDO-REAL` por id/texto), depois o ranking RRF completo com a
  decomposição bm25/vec por item.
- **(b) do Teste 1 embutido:** antes dos checkpoints, conta e lista as negações
  ("não encontro"+"redis") no store usado — se a PRÓPRIA cópia da produção tiver
  negações históricas, o harness avisa em destaque (muda a leitura de P1).

### Sua rodada (servidor EDP PARADO)

```powershell
# TESTE 1 — cópia NOVA e LIMPA da produção:
robocopy C:\edp_data C:\edp_data_fase0 /E
$env:EDP_BASE_DIR = "C:\edp_data_fase0"
$env:EDP_HYBRID_RETRIEVAL = "1"
python fase0_checkpoints.py --teste1

# TESTE 2 — o store contaminado, como está:
$env:EDP_BASE_DIR = "C:\edp_data_hybrid_test"
$env:EDP_HYBRID_RETRIEVAL = "1"
python fase0_checkpoints.py --teste2
```

A saída já vem no formato das tabelas de §3/§4 (ids por CP, ranking BM25 com
scores, posições das negações vs. alvo, veredito por checkpoint).

---

## §3. TESTE 1 — store LIMPO (`C:\edp_data_fase0`, 0 negações)

Contagem de negações na cópia da produção (item **(b)**): **0** — a cópia está
genuinamente limpa; a produção **não** carrega negações históricas sobre Redis.
Isto é o que torna o resultado decisivo: qualquer morte da memória aqui é
**estrutural**, não contaminação.

| checkpoint | 4c57ed7a? | evidência |
|---|---|---|
| **CP1** `mem.retrieve(top_k=5)` | **VIVO** | o alvo está entre os ids retornados (consistente com o lineage fa6df49e, 5/5 fontes Redis) |
| **CP2** `_retrieve_context` | **VIVO** | o alvo entra no retrieve interno (:2334) **e** aparece nos `blocks`; `seen_ids` **não** o descartou (não estava na janela-6) |
| **CP3** `_build_enriched_context` | **MORTO** | `"chave-valor"` **ausente** do prompt renderizado; ids rastreados no prompt final = `[]`. `remaining=1164` tokens → **espaço não faltava** |

**Onde morre:** entre CP2 (está nos `blocks`) e CP3 (não está no prompt). Não é
budget (sobravam 1164 tokens). É o corte `retrieval[:max_retrieval]` sobre a
lista `blocks` com metadados na frente — ver §6.

## §4. TESTE 2 — store CONTAMINADO (`C:\edp_data_hybrid_test`)

**BM25 puro (braço lexical isolado), query canônica:**

| posição | tipo | score BM25 |
|---|---|---|
| 1 | NEGAÇÃO | ~22 |
| 2 | NEGAÇÃO | ~22 |
| 3 | NEGAÇÃO | ~22 |
| … | … | … |
| **17** | **CONTEÚDO-REAL `4c57ed7a`** | **8.5** |

As 3 negações ocupam o pódio: cada uma contém a **pergunta verbatim** ("vamos
continuar a conversa sobre Redis…"), então o BM25 as pontua ~2.6× acima da
memória de conteúdo. **Eco de query confirmado.**

**RRF (híbrido completo):** o **top-5 é 100% lixo do teste** (negações/ecos); o
alvo `4c57ed7a` está **ausente do top-5**. Ou seja: no store contaminado a
memória real **morre já no CP1** — afogada no top-k do próprio retrieve, antes de
qualquer pós-processamento. (Contraste com o store limpo, onde o CP1 a preserva.)

## §5. Tabela final de mecanismos

| mecanismo | veredito | evidência |
|---|---|---|
| **D1 — read-path: metadados afogam o retrieval no corte `[:max_retrieval]`** | **CONFIRMADO (dominante)** | CP3 mata o alvo no store **limpo** com `remaining=1164`; `retrieval_kept=[249,149,69,116,597]` = 5 metadados, nenhum ≈1181; §6 file:line |
| **D2 — eco de query (BM25 premia a negação que contém a pergunta)** | **CONFIRMADO** | BM25 negações pos 1–3 (~22) vs alvo pos 17 (8.5); RRF top-5 100% lixo (§4) |
| contaminação write-path (negações viram memória episódica) | **CONFIRMADO como habilitador de D2** | as negações existem no store contaminado e são o combustível do eco; mas a contaminação **não** é o fator dominante (store limpo também falha — P1 refutada) |
| **seen_ids skip (:2340-2342)** | **REFUTADO / NÃO-EXERCITADO** | em nenhum teste o alvo morreu por seen_ids; no limpo sobrevive ao CP2, no contaminado morre antes (CP1) |
| top-k/recência dominado | **CONFIRMADO só no contaminado** | RRF top-5 100% negações (§4); no limpo o top-k preserva o alvo (CP1 vivo) |

## §6. Defeito 1 (read-path) — CONFIRMADO NA FONTE + alvos da Fase 1

**Sem implementar nada** — o conserto é o exp011 (pré-registro em preparação).
Como P1 refutou a aposta da contaminação, a investigação seguiu o Defeito 1
apontado pelos logs (`retrieval_kept=[249,149,69,116,597]` idêntico em todos os
testes) e o **confirmou na fonte**.

### D1 — a lista `blocks` é montada com metadados na frente; o corte decapita as memórias

**(a) Ordem de montagem de `blocks` em `_retrieve_context`** (`edp/llm_adapter.py`),
na ordem exata em que os `append` ocorrem:

| ordem | file:line | o que entra | natureza |
|---|---|---|---|
| 1 | `llm_adapter.py:2070` | `[ÂNCORA TEMPORAL]` (data/hora) | metadado |
| 2 | `llm_adapter.py:2091` | âncora de tarefa em curso | metadado |
| 3 | `llm_adapter.py:2170` | histórico cronológico compacto | metadado |
| 4 | `llm_adapter.py:2229` | session summaries `[{tag}]` | resumo |
| 5 | `llm_adapter.py:2319` | `[bloco atual]` (entries do bloco ativo) | contexto |
| **6+** | **`llm_adapter.py:2364`** | **`prefix + txt` — as memórias do retrieve (:2334)** | **conteúdo recuperado** |

As memórias recuperadas por similaridade (onde vive a `4c57ed7a`, 1181 chars) são
**as últimas** a entrar em `blocks` — posição 6 em diante.

**(b) O corte** — `blocks` é passado como `retrieval=` ao manager
(`llm_adapter.py:2639`: `ctx = mgr.build(..., retrieval=blocks, ...)`), e o
manager aplica, em `edp/runtime/context_window_manager.py:305`:

```python
for item in retrieval[:self.max_retrieval]:   # max_retrieval=5 (:203 default, :245)
```

`retrieval[:5]` sobre a lista mista pega **exatamente os 5 metadados das posições
1–5 e corta antes de qualquer memória recuperada (posição 6+)**. Prova numérica: a
assinatura `retrieval_kept=[249,149,69,116,597]` são os 5 tamanhos de metadados
(âncora temporal, histórico, 3 blocos), **nenhum ≈1181** (o tamanho da memória
real); e `remaining=1164` mostra que **espaço não faltava** — o corte é
posicional, não por budget. Isto explica CP3 matando o alvo no store LIMPO (§3).

### Alvos recomendados para a Fase 1 / exp011 (sem implementar)

1. **D1 (dominante, read-path):** o slot `retrieval` do `ContextWindowManager`
   deveria receber **só as memórias recuperadas**, não a lista `blocks` já
   poluída com metadados que têm seus próprios slots (âncoras/recentes). Ou o
   `_retrieve_context` separa metadados de conteúdo, ou o `mgr.build` recebe a
   lista de retrieval limpa. Como atinge o store limpo, é o que devolve conteúdo
   a **toda** conversa. Ponto de exp011: medir Recall@k no prompt final
   (`"chave-valor"` presente) antes/depois, com o mesmo harness da Fase 0.
2. **D2 (write-path, aditivo):** negações do próprio EDP ("não encontro
   registro…") não deveriam virar memória episódica recuperável — a assimetria é
   que o READ-path já filtra recusas de alta confiança (`memory.py:849`,
   `filtro_recusa`, reusado no índice híbrido) mas o WRITE-path **grava** o que
   depois se recusa a injetar. Espelhar esse filtro na gravação seca o combustível
   do eco de query. Secundário: só morde o store contaminado; com D1 corrigido, a
   memória real volta ao prompt mesmo competindo — mas o eco ainda desperdiça
   slots, então vale medir.

Ordem sugerida: **D1 primeiro** (dominante, atinge todos), D2 em seguida
(elimina o afogamento no top-k contaminado). Cada um com seu experimento e
critério congelado — a Fase 0 fecha aqui.

## §7. Confirmações de integridade

- Produção `C:\edp_data` **intocada** — este ambiente nem a alcança; a rodada
  real usa cópias via robocopy, e o harness recusa `EDP_BASE_DIR` que aponte
  para `edp_data` (guard `ALLOW_PROD`).
- **Nenhuma negação nova gravada:** nenhum turno de chat em nenhum momento; o
  harness não chama LLM.
- **Nenhum arquivo de `edp/` modificado:** `git status` limpo em `edp/` nesta
  branch (só `fase0_checkpoints.py` e este relatório na raiz).
- Encanamento do harness validado na fixture local (plumbing só — a fixture não
  contém o espécime): CP1/CP2/CP3 executam por chamada direta, spy captura o
  retrieve interno, restore/no-divergência OK, e o caminho de OBSTÁCULO
  ("alvo não existe neste store") dispara corretamente.
