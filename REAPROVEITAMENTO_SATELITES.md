# REAPROVEITAMENTO DOS SATÉLITES v3.2 — Mapa de Decisão

**Data**: 2026-06-25  
**Branch de análise**: `auditoria-curadoria`  
**Repositório**: devborgesr/edp_v5  
**Modo**: análise pura — nenhum código alterado, nenhuma conexão construída

---

## Contexto

O bloco v3.2 (11 arquivos em `edp/`) chegou num único commit (`14179eb`) e nunca foi conectado ao caminho vivo do EDP. Os 7 satélites operam sobre tipos (`ImmutableCognitiveNode`, `CognitiveTick`, `DecisionEvent`) que não existem no EDP v5. A análise responde: **algum desses satélites resolve uma necessidade real — no EDP vivo ou no lab experimental?**

**Ônus da prova é do satélite.** "Poderia ser útil" não passa. Só passa evidência no código (`file:line`).

---

## Dados reais de referência

| Métrica | Valor medido | Fonte |
|---|---|---|
| Entradas no store | 594 | `memory.py` (episodic store) |
| Limite padrão | 200 (`EPISODIC_MEM_SIZE`) | `memory.py` |
| Dias sem degradação | 36 | histórico de uso |
| Usuários simultâneos | 1 (single-user) | arquitetura |
| Chamadas/turno ao retrieve | 1 | `websocket.py:629` |
| Threshold storm (StormGuard) | 10 q/s | `storm_guard.py:50` |
| Threshold alert (PressureRegulator) | 0.75 | `pressure_regulator.py:36` |

---

## FASE 1 — Necessidades reais do lab (`edp/lab/`)

O lab usa a pilha de memória do EDP como ambiente controlado para medir comportamento do LLM sob diferentes configurações de janela de contexto. **Não testa o EDP — usa o EDP como substrato.**

### Necessidades explícitas (citadas com file:line)

| ID | Necessidade | file:line |
|---|---|---|
| E1 | Janela-esqueleto real capturada (`context_debug.jsonl`) | `run_once.py:290-295`, `repeater.py:155-161` |
| E2 | Catálogo de distorções puras de seção (8 formatos) | `window_formats.py:84-283` |
| E3 | Amostragem estatística N-vezes (mesma janela, N disparos) | `sampler.py:90-150` |
| E4 | Guarda ARMED contra disparo acidental (EDP_LAB_ARMED=1) | `sampler.py:43-48`, `run_once.py:206-211` |
| E5 | Modo dry-run (pipeline completo sem chamar modelo) | `run_once.py:131-150`, `run_once.py:201-204` |
| E6 | Sessão de lab fisicamente isolada (`__lab__<uuid>`) | `isolation.py:53-55`, `isolation.py:127-167` |
| E7 | Fingerprint de produção antes/depois (SHA-256 de disco) | `isolation.py:176-208`, `repeater.py:129-136` |
| E8 | Purga de sessão após experimento (descartável) | `isolation.py:91-124` |
| E9 | Store longitudinal de runs (índice fino + blob pesado) | `prontuario.py:144-214` |
| E10 | Hash de comensurabilidade entre runs (`andaime_hash`) | `prontuario.py:76-100` |
| E11 | Leitura de índice rotacionado (histórico completo) | `prontuario.py:243-254` |
| E12 | Freio de orçamento por condição (USD, refinamento progressivo) | `rodizio.py:103-112` |
| E13 | Rastreamento por variante (n_run, n_ok, distinct, cost_usd, leak_ok) | `repeater.py:140`, `rodizio.py:72-88` |
| E14 | Atribuição por experimento no scorer (exp001→003→004→006→007) | `scorer.py:175-193`, `exp003.py:32-36` |
| E15 | Regra de precedência para extração de valor (última sentença vence) | `scorer.py:804` |
| E16 | Intervalos de confiança de Wilson para taxas de fidelidade | `scorer.py:71` |
| E17 | Setup de competição semântica (itens quase idênticos por construção) | `exp003.py:32-36` |
| E18 | Injeção de fato em camadas distintas com valores diferentes | `window_formats.py:239-267`, `exp004.py:54` |
| E19 | Lista de retrieval controlada (substitui retrieval inteiro) | `window_formats.py:270-283` |
| E20 | Medição de resistência a injeção maliciosa | `exp007.py:55-57` |
| E21 | Proveniência real no andaime (code_sha, embedding_version) | `run_once.py:81-93` |
| E22 | Flag de verificação de relógio por registro | `prontuario.py:56-63` |
| E23 | Toggle de ceticismo (ablação do bloco de ceticismo) | `sampler.py:169-195`, `run_once.py:392` |

### Necessidades inferidas (ausentes no código)

| ID | Necessidade | Observação |
|---|---|---|
| I1 | Medição de diversidade semântica do retrieval (MRD) | Lab constrói competição por design; não mede o que sabe |
| I2 | Alertas de pressão de memória da sessão de lab | Sessões são curtas e descartáveis; não há monitoramento |

### O que o lab NÃO precisa (relevante para FASE 2)

| Pergunta | Evidência |
|---|---|
| Lab precisa detectar storm de retrieval? | Não — controla retrieval explicitamente (`E19`) |
| Lab precisa de rate limiting de tokens? | Não — tem freio financeiro próprio (`E12`) |
| Lab precisa de causalidade assíncrona? | Não — atribuição via `formato_id` + `andaime_hash` (`E10`) |
| Lab precisa de meta-estabilidade adaptativa? | Não — parâmetros fixados antes da coleta (design intencional) |
| Lab precisa de PressureMonitor? | Não — sessões descartáveis e curtas |

---

## FASE 2 — Cruzamento: 7 satélites × necessidades reais

---

### Satélite 1: SemanticBiodiversityEngine (`biodiversity.py`)

**A — Output**  
`measure_and_rebalance(nodes, policy)` → `(BiodiversityReport, List[updated_nodes])`. Calcula MRD (mean radial distance entre embeddings no store); MRD < 0.15 → colapso semântico. Pode rebalancear por evicção seletiva.

**B — EDP vivo tem essa necessidade?**  
Store com 594 entradas vs limite 200. Evicção hoje é cega. Nenhuma chamada a MRD, biodiversidade ou diversidade semântica em nenhum arquivo do caminho vivo (`memory.py`, `pipeline.py`, `websocket.py`). Necessidade potencial existe (store saturado pode ter colapsado), mas **MRD real desconhecido** — hipótese não confirmada por dado.

**C — Lab tem essa necessidade?**  
`[I1]`: lab não mede MRD dos itens injetados. Experimentos 001, 003, 006 constroem a competição por construção — sabem que itens são similares. Nenhuma `file:line` no lab requerendo MRD. **Ausente.**

**D — Custo de integração**  
`ImmutableCognitiveNode` ≠ `MemoryEntry`. Adaptador necessário: extrair embeddings de `MemoryEntry`, empacotar, chamar `measure_and_rebalance`. ~50-70 linhas. Rebalanceamento precisaria se integrar com evicção de `memory.py`.

**E — Fragmento ou satélite inteiro?**  
Fragmento viável: `_compute_mrd(embeddings)` — ~15 linhas, matemática pura sobre matriz de embeddings, zero deps de tipo. Rebalanceamento é separável.

**F — Veredito**  
- EDP vivo: **TALVEZ** — fragmento MRD tem valor diagnóstico real, condicionado a medir MRD real do store primeiro. Sem dado, é especulação.  
- Lab: **NÃO** — lab controla conteúdo explicitamente; validação de MRD seria redundante.

---

### Satélite 2: CognitiveEconomyEngine (`economy.py`)

**A — Output**  
6 token buckets com regen. `request_retrieval()` → `EconomyBudgetExceeded` se esgotado. Budget 100 tokens, custo 0.10/retrieval, regen 5/s. Thread de starvation prevention rodando permanentemente.

**B — EDP vivo tem essa necessidade?**  
`websocket.py:629`: `run_pipeline(message, message)` — um retrieve por mensagem do usuário. 100 tokens / 0.10 = 1000 retrievals contínuos para esgotar — impossível em uso conversacional. `EconomyBudgetExceeded` não importado em nenhum arquivo do caminho vivo. Não há multi-tenant, não há alta frequência. **Necessidade completamente ausente.**

**C — Lab tem essa necessidade?**  
`[E12]` `rodizio.py:103-112`: lab tem freio de orçamento próprio, financeiro (USD), por condição experimental. Não é rate limiting de tokens. Lab nunca chama retrieval em loop. **Ausente.**

**D — Custo de integração**  
Thread de background permanente + exceção sem handler em nenhum caller + 6 buckets sem consumidores reais.

**E — Fragmento ou satélite inteiro?**  
Nenhum fragmento útil. O valor do satélite é a limitação integrada — fragmento parcial é código morto.

**F — Veredito**  
- EDP vivo: **NÃO** — single-user conversacional, LLM API é o gatekeeper natural de custo.  
- Lab: **NÃO** — freio de orçamento próprio em `rodizio.py:103-112`.

---

### Satélite 3: MetaStabilityController (`meta_stability.py`)

**A — Output**  
`update(StabilitySignals)` → `OperationalParams(retrieval_breadth, decay_rate_modifier, consolidation_mode)`. Composite = `0.35×pressure + 0.25×storm + 0.15×cache + 0.15×entropy + 0.05×abstraction + 0.05×obs`.

**B — EDP vivo tem essa necessidade?**  
`memory.py` usa k fixo em retrieve — sem parâmetros adaptativos em nenhum arquivo do caminho vivo. `StabilitySignals` exige: pressure (FractalPressureRegulator), storm (StormGuard), cache (embedding cache), entropy (BiodiversityEngine), abstraction (StormGuard), obs (AsyncDecisionGraph). **5 de 6 inputs requerem outros satélites não integrados.** Sem inputs reais → composite = 0.0 → modo NORMAL permanente → código morto.

**C — Lab tem essa necessidade?**  
Lab fixa parâmetros antes da coleta por design deliberado. Parâmetros adaptativos quebrariam reproducibilidade dos experimentos. **Estruturalmente contraindicado.**

**D — Custo de integração**  
Requer 5 outros satélites como pré-condição. Custo transitivo = integrar os 5 + MetaStabilityController.

**E — Fragmento ou satélite inteiro?**  
Nenhum fragmento útil. Valor é no adaptive tuning — sem inputs reais, é função constante retornando NORMAL.

**F — Veredito**  
- EDP vivo: **NÃO** — outputs sempre NORMAL sem os 5 inputs.  
- Lab: **NÃO** — contradiz design de parâmetros fixos por experimento.

---

### Satélite 4: FractalPressureRegulator (`pressure_regulator.py`)

**A — Output**  
`absorb_tick(CognitiveTick)` → `{global, local, cluster, ema, spike}`. Propaga LOCAL→CLUSTER→GLOBAL com EMA + anti-spike. Raise `PressureSaturationError` em 0.92.

**B — EDP vivo tem essa necessidade?**  
`CognitiveTick` não existe no EDP vivo — zero importações, zero criações em `memory.py`, `pipeline.py`, `websocket.py`. `PressureSaturationError` sem handler. Propagação LOCAL→CLUSTER→GLOBAL pressupõe topologia multi-node inexistente. **Necessidade completamente ausente.**

**C — Lab tem essa necessidade?**  
Nenhuma. Lab não tem conceito de pressão de tick, zona local/cluster/global, ou saturação de propagação. **Ausente.**

**D — Custo de integração**  
Criação do tipo `CognitiveTick` do zero + handler para `PressureSaturationError` + wiring em cada turno. Custo alto, benefício zero.

**E — Fragmento ou satélite inteiro?**  
Nenhum fragmento útil. Plumbing sem source e sem consumer.

**F — Veredito**  
- EDP vivo: **NÃO**  
- Lab: **NÃO**

---

### Satélite 5: RetrievalStormGuard (`storm_guard.py`)

**A — Output**  
Composite storm score: `0.30×rate + 0.25×similarity_sat + 0.20×recursion + 0.15×abstraction_entropy + 0.10×scheduler`. Storm threshold: 10 q/s. `effective_k(k)` reduz k sob storm. `record(results)` → `StormDetected`.  
Fragmento isolável: `_update_similarity_saturation()` (`storm_guard.py:284-294`) — mede fração de resultados com score ≥ 0.88.

**B — EDP vivo tem essa necessidade?**  
10 q/s impossível para single-user conversacional. `StormDetected` sem handler em `memory.py`. `effective_k()` nunca chamado. Satélite inteiro: inaplicável.  
Fragmento similarity_saturation: `memory.py` retorna scores de similaridade nos resultados de retrieval. Câmara de eco (retrieval repetindo sempre os mesmos itens) é fenômeno possível em store com 594 entradas, mas **não confirmado por evidência de uso atual**.

**C — Lab tem essa necessidade?**  
`[E17]` `exp003.py:32-36`: lab constrói competição semântica deliberadamente — sabe que inseriu itens quase idênticos. StormGuard adicionaria validação redundante de algo já controlado. **Ausente.**

**D — Custo de integração**  
Satélite inteiro: `RetrievalResult` com `score` e `retrieval_depth`; threading; EMA state.  
Fragmento: ~15 linhas, opera sobre lista de `float` (scores) — extração mínima.

**E — Fragmento ou satélite inteiro?**  
Satélite inteiro: **NÃO** — rate storm impossível, múltiplos inputs não fornecidos.  
Fragmento similarity_saturation: **POSSÍVEL** — 15 linhas, zero deps, detecta echo chamber. Sem consumidor hoje.

**F — Veredito**  
- EDP vivo: **TALVEZ** — fragmento similarity_saturation útil como diagnóstico de echo chamber se consumidor for criado. Necessidade de echo chamber não confirmada por dado real.  
- Lab: **NÃO** — lab controla retrieval explicitamente; detecção seria redundante.

---

### Satélite 6: AsyncDecisionGraph (`decision_graph_v32.py`)

**A — Output**  
DAG assíncrono de `DecisionEvent` com worker thread. `record(event)` enfileira O(1). `obs_pressure()` → sinal para PressureMonitor. Causal path tracing. Até 5000 nós, pruning por idade.

**B — EDP vivo tem essa necessidade?**  
`lineage.py` persiste lineagem de decisão no EDP vivo. Sistema linear single-user — sem ramificação causal paralela. Thread de worker + 5000 nós para conversa com um usuário = overhead sem proporcional. `obs_pressure()` só tem valor se PressureMonitor estiver rodando. **Necessidade já coberta por `lineage.py`.**

**C — Lab tem essa necessidade?**  
`[E9]` `prontuario.py:144-214`: atribuição via `run_id`, `formato_id`, `andaime_hash`. `[E14]` `scorer.py:175-193`: experimentos encadeados via importação direta. **Ausente.**

**D — Custo de integração**  
`DecisionEvent`, `DecisionEventType` — tipos não presentes no EDP vivo. Thread worker permanente. `obs_pressure()` só tem valor em conjunto com PressureMonitor.

**E — Fragmento ou satélite inteiro?**  
Nenhum fragmento isolado útil. Causal tracing é o valor central; fragmento de enqueue sem DAG = lista simples.

**F — Veredito**  
- EDP vivo: **NÃO** — `lineage.py` já cobre; sistema linear não precisa de DAG.  
- Lab: **NÃO** — `prontuario.py` cobre proveniência com mais fidelidade experimental.

---

### Satélite 7: PressureMonitor (`pressure_monitor.py`)

**A — Output**  
6 dimensões tipadas: eviction×0.25, consolidation×0.20, entropy×0.20, retrieval×0.15, embedding×0.10, graph×0.10. Composite ponderado. Hysteresis + EMA por dimensão. `PressureSurface` imutável snapshot. **Zero imports do ecossistema v3.2** — único satélite completamente standalone.

**B — EDP vivo tem essa necessidade?**  
Dimensão por dimensão:

| Dimensão | Necessidade | Sinal disponível hoje |
|---|---|---|
| eviction | REAL — 594 entradas vs limite 200 → pressão = 1.0 | Sim: `len(memory._entries)/EPISODIC_MEM_SIZE` |
| consolidation | PLAUSÍVEL — fila pode acumular | Sim: `len(consolidation_queue)/MAX_QUEUE` |
| entropy | CONDICIONAL — requer MRD (BiodiversityEngine) | Não — dependência de outro satélite |
| retrieval | CONDICIONAL — requer StormGuard composite score | Não — dependência de outro satélite |
| embedding | POSSÍVEL — cache miss rate | Não medido atualmente |
| graph | SEM VALOR — requer AsyncDecisionGraph | Não — dependência de outro satélite |

2 de 6 dimensões têm sinal real disponível imediatamente.

**C — Lab tem essa necessidade?**  
`[I2]`: sessões de lab são curtas, escopo `sprint`, descartáveis. Pressão de memória em sessão temporária é irrelevante. **Ausente.**

**D — Custo de integração**  
Fragmento scoped (eviction + consolidation): ~35-40 linhas. Cálculo direto, zero deps externas. As 4 dimensões restantes ficam em 0.0 com EMA partindo de 0.0 — comportamento aceito pela implementação.

**E — Fragmento ou satélite inteiro?**  
Fragmento scoped (eviction + consolidation): **VIÁVEL**. `PressureMonitor` aceita atualizações parciais — `update_eviction()` e `update_consolidation()` sem wiring dos outros 4. Dimensões não alimentadas permanecem em 0.0 com hysteresis correta.

**F — Veredito**  
- EDP vivo: **VALE** — única necessidade confirmada com dado real (594 > 200). Custo mínimo, zero deps, fragmento de 35-40 linhas.  
- Lab: **NÃO** — sessões descartáveis, sem necessidade de monitoramento.

---

## FASE 3 — Tabela de decisão

| Satélite | EDP Vivo | Lab | Decisão final |
|---|---|---|---|
| SemanticBiodiversityEngine | TALVEZ (fragmento MRD, condicionado) | NÃO | Medir MRD real antes de decidir |
| CognitiveEconomyEngine | NÃO | NÃO | **Descartar** |
| MetaStabilityController | NÃO | NÃO | **Descartar** |
| FractalPressureRegulator | NÃO | NÃO | **Descartar** |
| RetrievalStormGuard | TALVEZ (fragmento 15 linhas, sem consumidor) | NÃO | Aguardar confirmação de echo chamber |
| AsyncDecisionGraph | NÃO | NÃO | **Descartar** |
| PressureMonitor | VALE (fragmento scoped, 35-40 linhas) | NÃO | **Extrair fragmento para EDP vivo** |

### Classificação por categoria

**Categoria 1 — Descartar (4 satélites)**  
`CognitiveEconomyEngine`, `MetaStabilityController`, `FractalPressureRegulator`, `AsyncDecisionGraph`  
Razão comum: necessidade ausente no caminho vivo E no lab. Sem fragmento útil isolável sem custo proporcional ao benefício.

**Categoria 2 — Aguardar dado real (2 satélites)**  
`SemanticBiodiversityEngine`: medir MRD do store de produção (~5 linhas de script). Se MRD < 0.15 → extrair fragmento. Se MRD normal → hipótese não confirmada, descartar.  
`RetrievalStormGuard`: confirmar se retrieval está em câmara de eco (análise de scores retornados). Se echo chamber confirmado → extrair fragmento similarity_saturation (15 linhas). Sem confirmação → descartar.

**Categoria 3 — Extrair fragmento para EDP vivo (1 satélite)**  
`PressureMonitor` (eviction + consolidation): necessidade confirmada por dado real (594 > 200). Custo mínimo (~35-40 linhas). Zero deps externas. Dimensões restantes (entropy, retrieval, embedding, graph) adicionáveis progressivamente quando seus sinais estiverem disponíveis.

**Categoria 4 — Extrair fragmento para lab**  
**Nenhum.** O lab tem todas as suas necessidades cobertas internamente (E1-E23). Nenhum satélite resolve uma necessidade real do lab.

---

## Conclusão

Dos 7 satélites v3.2, **apenas 1** resolve uma necessidade confirmada por dado real no EDP vivo (`PressureMonitor`, fragmento scoped). Os outros 6 foram projetados para um cenário de carga (multi-tenant, alta frequência, topologia multi-node) que não existe no sistema real.

O lab experimental é completamente auto-suficiente — construiu seu próprio stack (isolation, sampler, prontuário, rodízio, scorer, window_formats) que cobre exatamente suas necessidades sem sobreposição com qualquer satélite v3.2.

**Próximos passos possíveis** (nenhum decidido aqui):
1. Medir MRD real do store de produção antes de qualquer decisão sobre `SemanticBiodiversityEngine`
2. Analisar scores de retrieval históricos antes de qualquer decisão sobre `RetrievalStormGuard`
3. Extrair fragmento `PressureMonitor` (eviction + consolidation) — único com ônus de prova cumprido
