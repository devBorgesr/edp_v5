# EDP v5 — Análise de Valor e Custo de Integração dos Satélites v3.2

> **Objetivo:** Mapear valor potencial × custo de integração dos 7 satélites v3.2 para decidir
> QUAIS valeriam ser integrados e A QUE CUSTO — ANTES de qualquer construção.
>
> **Modo:** análise pura. Nenhum código foi alterado. Nenhuma integração foi construída.
>
> **Fonte de código:** ANTIGO (`feb0db9`) — 11 arquivos v3.2 ainda presentes.
> **Referência de caminho vivo:** CURADO (`cbacac4`) — branch `auditoria-curadoria`.
> **Dados reais de uso:** fornecidos pelo usuário (36 dias de produção).

---

## Dados Reais de Uso (evidência de necessidade)

| Métrica | Valor | Implicação |
|---------|-------|------------|
| Memórias episódicas | 594 | Store alto — próximo/acima de EPISODIC_MEM_SIZE=200 (default) |
| Memórias semânticas | 134 | Promoção funcionando |
| Dias de uso | 36 | Sistema estável, sem crash |
| Entry mais acessada | 2.9% do total | Baixíssima concentração |
| Top 3 entradas | 8.5% do total | Dom_penalty (12%) NUNCA dispara |
| Entries com zero acessos | 49.7% | Alta diversidade — sem convergência forçada |
| promote_threshold=3 | promove 32% | Funcionando adequadamente |
| cognitive_decisions populado | 6% das entries | Análise semântica raramente ocorre |
| epistemic_status='hypothesis' | 87% das entries | Maioria não verificada |
| epistemic_status='verified' | 12% das entries | Verificação ocorre pouco |

---

## Caminho Vivo Atual (referência)

```
run.py:serve()
  └─ uvicorn → edp/api/main.py:lifespan()
       ├─ [background jobs: cognitive_decisions, contradiction_flagger,
       │    auto_consolidation, CHI]
       └─ websocket.py (per-turn)
            ├─ run_pipeline(message, session_id)           [websocket.py:629]
            │    ├─ chunk + embed()                        [pipeline.py]
            │    ├─ deduplicate(chunks, thresh, embs)      [embeddings.py]
            │    └─ memory.add(chunks, embs, ...)          [memory.py]
            ├─ memory.retrieve(query, top_k=5, min_score=0.20) [websocket.py:716]
            │    ├─ fórmula 9-factor                       [memory.py:723-726]
            │    ├─ contradiction_flagger.scan_results()   [memory.py:1647]
            │    └─ retrieval_monitor.record_turn()        [memory.py:1638]
            └─ LLM stream → resposta
```

**Parâmetros fixos atuais:** `top_k=5`, `promote_threshold=3`, `DEDUP_THRESH` fixo, `EPISODIC_MEM_SIZE=200`.

---

## Satélite 1 — SemanticBiodiversityEngine (`biodiversity.py`)

### A) O que produz

Método: `measure_and_rebalance(nodes, policy)` → `(BiodiversityReport, List[updated_nodes])` (`biodiversity.py:130`).

1. Calcula **MRD** (Mean Radial Distance) = média de `1 − cosseno` de cada embedding ao centróide global (`biodiversity.py:163-171`). MRD < 0.15 → **collapse**; > 0.35 → saudável.
2. K-means lite por nível de abstração (5 iterações, k ≤ 8) → detecta **dominance** quando cluster > 60% dos nós do nível (`biodiversity.py:237-260`).
3. Em collapse: nós muito centrais (`dist < median × 0.5`) recebem `pressure_score += 0.12` — decay mais rápido (`biodiversity.py:309`).
4. Em dominance: nós periféricos recebem `novelty_score += 0.08` (`biodiversity.py:313`).

Retorna: `BiodiversityReport.snapshot()` = `{mrd, entropy, collapse, dominance, updated, n_analyzed}` (`biodiversity.py:94-102`).

### B) Lacuna que endereçaria

`deduplicate()` atual remove duplicatas próximas (threshold ≈ 0.95), mas não detecta **convergência semântica gradual** onde muitos chunks ficam próximos (MRD baixo) sem serem duplicatas. O `dom_penalty` atual penaliza acesso excessivo, não redundância semântica.

Ponto potencial de consumo: `memory.py:723-726` — multiplicador `diversity_penalty` extra no `rank_score` para entries muito próximas ao centróide do store. Alternativa: ajustar `CONSOLIDATION_SIM_THRESH` em `consolidation.py:29` dinamicamente com base no MRD.

### C) Dados reais confirmam necessidade?

MRD real do store não foi medido. Indicadores indiretos: 49.7% das entries com zero acessos sugere memórias diversas não reforçadas (sem convergência por acesso). `cognitive_decisions` em 6% → análise semântica raramente ocorre. Baixa concentração de acessos é inconsistente com colapso semântico (colapso geraria acesso concentrado nas memórias similares).

**Veredito C: INDETERMINADO** — dados não contradizem nem confirmam. MRD real precisa ser medido antes de qualquer decisão.

### D) Custo de integração

O satélite opera sobre `ImmutableCognitiveNode` (`types_v32.py`). Entries do caminho vivo são `dict`. Requer adaptação:

- `edp/cognitive_diversity.py` (novo): adapta `measure_and_rebalance()` para `List[dict]`. **~70 linhas**
- `edp/memory.py`: adiciona `diversity_penalty` consumido no retrieve. **~25 linhas**
- `edp/api/main.py`: background task periódico. **~15 linhas**

**Total: ~110 linhas em 3 arquivos.**

### E) Tipo de ponte

**ESCRITA** — modifica ranking de entries específicas.

Fluxo: `background_job` (periódico) → lê `episodic.entries` → calcula MRD → entries centrais recebem `"diversity_penalty": 0.88` → `memory.retrieve()` consome o campo como multiplicador na linha 723-726.

Decisões de design abertas:
1. Penalty persiste no JSON ou é recalculado a cada retrieve? (trade-off: performance vs defasagem)
2. `pressure_score += 0.12` do satélite → que campo real das entries? (`prioridade`? campo novo?)
3. Com que frequência rodar? A cada retrieve é inviável (O(n·dim) extra por query).

### F) Veredito por Satélite

**TALVEZ** — condicional a medir MRD real do store. Se MRD > 0.35 (store diverso), o satélite nunca ativaria e seria dead code novamente. Medir MRD primeiro tem custo ~5 linhas e resolve a incerteza.

---

## Satélite 2 — CognitiveEconomyEngine (`economy.py`)

### A) O que produz

6 token buckets independentes (retrieval, recursion, embedding, consolidation, scheduler, snapshot) com regeneração por tempo (`economy.py:189-196`).

API: `request_retrieval(levels, is_critical)` → raise `EconomyBudgetExceeded` se orçamento esgotado (`economy.py:206-212`). `update_pressure(global_pressure)` → EMA → ajusta `ThrottleMode`: NORMAL → REDUCED → MINIMAL → FROZEN (`economy.py:230-237`, `economy.py:325-354`). Em FROZEN: operações não-críticas rejeitadas (`economy.py:289-296`).

Budget padrão: 100 tokens retrieval, custo 0.10/retrieval → esgota em 1000 retrievals. Regeneração: 5 tokens/s (`economy.py:55-65`).

Produz: **gate binário** (permite/rejeita operação) + modo global de throttle.

### B) Lacuna que endereçaria

O caminho vivo não tem rate limiting. Ponto de consumo: `websocket.py:716` (antes de retrieve) e `pipeline.py:~390` (antes de embed). O engine bloquearia operações com `EconomyBudgetExceeded`.

### C) Dados reais confirmam necessidade?

36 dias, conversacional, single-user. O LLM é gargalo natural (2-5s/turno). Retrievals: ~1/turno, <<1/s. Budget de 100 tokens regenera em 20s. Impossível esgotar em uso conversacional.

Em 36 dias nunca houve overload, degradação, ou rate limiting necessário. O engine operaria permanentemente em NORMAL, modes REDUCED/MINIMAL/FROZEN jamais atingidos.

**Veredito C: NECESSIDADE INEXISTENTE NOS DADOS** — sistema single-user conversacional não sofre overload cognitivo. Projetado para cenários multi-tenant/batch.

### D) Custo de integração

~40-50 linhas: instância singleton, `try: economy.request_retrieval()` em `websocket.py`, `try: economy.request_embedding()` em `pipeline.py`. Mais thread daemon de starvation prevention embutida no `__init__` (`economy.py:199-203`) — sempre rodando.

### E) Tipo de ponte

**GATE** — intercepta antes da operação, zero impacto em resultado se aprovado.

Fluxo: `websocket.py:716` → `economy.request_retrieval()` → [aprovado: continua] / [negado: return []]. Análogo a circuit breaker.

Budget de 100 tokens, custo 0.10, regen 5/s → impossível atingir negação em uso conversacional. O gate sempre aprovaria.

### F) Veredito por Satélite

**NÃO VALE** — necessidade inexistente. Thread extra permanente + complexidade de 4 modos que nunca serão atingidos. Se rate limiting fosse necessário no futuro (cenário multi-tenant), `asyncio.Semaphore` de 3 linhas seria suficiente.

---

## Satélite 3 — MetaStabilityController (`meta_stability.py`)

### A) O que produz

`update(signals: StabilitySignals)` → `OperationalParams` (`meta_stability.py:149`).

Integra 6 sinais via EMA ponderada (`meta_stability.py:155-178`):
```
composite = 0.35 × global_pressure  +  0.25 × storm_score
           + 0.15 × cache_instability  +  0.15 × entropy_drift
           + 0.05 × abstraction_risk   +  0.05 × obs_pressure
```

Modos: NORMAL → ELEVATED → DEGRADED → CRITICAL → EMERGENCY → RECOVERY, com hysteresis=0.08 e anti-oscillation `min_ticks_per_mode=3` (`meta_stability.py:241-278`).

`OperationalParams` retornado (`meta_stability.py:301-404`):

| Modo | retrieval_breadth | decay_rate_modifier | consolidation_mode |
|------|-------------------|---------------------|--------------------|
| NORMAL | 10 | 1.0 | NORMAL |
| DEGRADED | 5 | 1.3 | AGGRESSIVE |
| EMERGENCY | 2 | 2.0 | AGGRESSIVE |

### B) Lacuna que endereçaria

O caminho vivo usa `top_k=5` fixo (`websocket.py:716`) e `promote_threshold=3` fixo. `OperationalParams.retrieval_breadth` poderia tornar `top_k` dinâmico; `consolidation_mode` poderia ajustar agressividade de promoção.

**Problema crítico:** os 6 sinais de entrada dependem diretamente dos outros satélites:
- `storm_score` → RetrievalStormGuard (Satélite 5)
- `cache_instability` → EmbeddingCache v3.2 (deletado)
- `entropy_drift` → SemanticBiodiversityEngine (Satélite 1)
- `obs_pressure` → AsyncDecisionGraph (Satélite 6)

Sem eles, `StabilitySignals` fica com 5 campos em zero → composite ≈ 0.0 → modo sempre NORMAL → `retrieval_breadth=10` fixo (muda de k=5 para k=10 mas sem adaptação real).

### C) Dados reais confirmam necessidade?

594 entries com k=5 fixo não causa problemas reportados. Promoção de 32% (acessos ≥ 3) funciona adequadamente. Baixa concentração de acessos indica que k=5 retorna entries suficientemente variadas.

Modos DEGRADED/CRITICAL/EMERGENCY requerem composite ≥ 0.50/0.68/0.82. Com 5 dos 6 sinais em zero (ausência de satélites), composite jamais chegaria a 0.50.

**Veredito C: NECESSIDADE REAL PARCIAL** — adaptação de parâmetros tem valor teórico, mas dados não mostram problema que a justifique agora. E o controlador é incompatível com o caminho vivo sem reformulação de sinais.

### D) Custo de integração

**Integração fiel** (com ecossistema mínimo, precisando de Satélites 1 e 5 para ter sinais reais): ~250-300 linhas incluindo dependências.

**Extração do núcleo** (modo simples com sinal = store fill ratio): ~60 linhas — mas perde 80% da lógica e o resultado seria equivalente a `top_k = min(5 + len(entries) // 150, 15)` em 2 linhas.

### E) Tipo de ponte

**ESCRITA** — modifica parâmetros operacionais do retrieve e consolidação.

Fluxo: background job coleta sinais → `meta.update(signals)` → `OperationalParams` em singleton → `websocket.py:716` lê `params.retrieval_breadth` como `top_k`.

Nota: `abstraction_restriction=True` em modos DEGRADED+ restringe retrieval a `AbstractionLevel.ABSTRACTION` — campo inexistente nas entries do caminho vivo. Seria dead code.

### F) Veredito por Satélite

**NÃO VALE na forma atual** — incompatível com o caminho vivo sem os outros satélites. Com sinais reais ausentes, opera sempre em NORMAL. O núcleo útil (k dinâmico) é expresível em 2 linhas sem o framework.

---

## Satélite 4 — FractalPressureRegulator (`pressure_regulator.py`)

### A) O que produz

`absorb_tick(tick: CognitiveTick)` → `dict {"global", "local", "cluster", "ema", "spike", "in_alert", "in_critical"}` (`pressure_regulator.py:125`).

Mecânica: anti-spike (limita delta a 0.15/tick, `pressure_regulator.py:146`) → propagação LOCAL→CLUSTER→GLOBAL (fatores 0.40/0.30, `pressure_regulator.py:151-156`) → EMA alpha=0.20 (`pressure_regulator.py:159`) + spike detector alpha=0.60 (`pressure_regulator.py:165`) → pressão efetiva = `max(ema, spike × 0.5)` (`pressure_regulator.py:170`). Alert ≥ 0.75, critical ≥ 0.85, saturação ≥ 0.92 → raise `PressureSaturationError` com cooldown 1s (`pressure_regulator.py:186-195`).

Produz: `float global_pressure ∈ [0,1]` — sinal primário de entrada para MetaStabilityController.

### B) Lacuna que endereçaria

Componente de **infraestrutura de medição de pressão** dentro do ecossistema v3.2. Sozinho, produz um float que ninguém consome: o consumidor natural é o MetaStabilityController (Satélite 3), que por sua vez depende de outros satélites.

`CognitiveTick` é um tipo de `types_v32.py` com `pressure_delta`, `semantic_load`, `affected_node_ids`, `source_operation` — não existe no caminho vivo. Teria que ser construído artificialmente.

### C) Dados reais confirmam necessidade?

Uso conversacional ~1 tick/30s. `CognitiveTick.pressure_delta = 0.05` (valor do orchestrator). Decay de 0.02/s. Com 30s entre ticks: `local = 0.05 × exp(-0.02 × 30) ≈ 0.027`. EMA sobre global converge para ~0.002. Alert em 0.75 nunca atingido.

Em 36 dias de uso sem degradação: pressão cognitiva nunca foi um problema real.

**Veredito C: NECESSIDADE INEXISTENTE NOS DADOS** — saturação cognitiva impossível para chatbot single-user. O regulador seria permanentemente abaixo de qualquer threshold.

### D) Custo de integração

~50-60 linhas: instância singleton, adaptador de `CognitiveTick` construído em `pipeline.py` e `memory.py`, `absorb_tick()` chamado após cada operação principal.

Sem MetaStabilityController como consumidor: o `global_pressure` retornado seria descartado. Custo real sem benefício.

### E) Tipo de ponte

**LEITURA PURA na prática** (sem consumidor do resultado).

Fluxo: `pipeline.py:embed()` → constrói CognitiveTick → `regulator.absorb_tick()` → `global_pressure ≈ 0.002` → [sem consumidor → descartado].

Com MetaStabilityController: pressão → `meta.update()` → `OperationalParams` → ajuste de parâmetros. Mas requer Satélite 3 que requer outros satélites.

### F) Veredito por Satélite

**NÃO VALE** — peça de plombing do ecossistema v3.2 sem valor isolado. Dependência em cadeia. O único sinal útil derivável do caminho vivo (`len(entries)/MAX_MEMORY`) já está disponível sem abstração de CognitiveTick.

---

## Satélite 5 — RetrievalStormGuard (`storm_guard.py`)

### A) O que produz

`effective_k(k)` → int (k potencialmente reduzido) (`storm_guard.py:136`).
`record(results)` → None ou raise `StormDetected` (`storm_guard.py:153`).

Composite storm score (`storm_guard.py:259-267`):
```
score = 0.30 × retrieval_rate  +  0.25 × similarity_saturation
      + 0.20 × recursion       +  0.15 × abstraction_entropy
      + 0.10 × scheduler
```

- `retrieval_rate`: queries/s, normalizado (10 q/s → 1.0), sliding window 50 eventos (`storm_guard.py:269-282`)
- `similarity_saturation`: fração de resultados com score ≥ 0.88 (`storm_guard.py:284-294`)
- `abstraction_entropy`: concentração de resultados num único level (`storm_guard.py:296-320`)

Storm ativo (score ≥ 0.70) → `effective_k()` reduz k proporcionalmente + cooldown 2s. Auto-recovery (score < 0.35) (`storm_guard.py:172-189`).

### B) Lacuna que endereçaria

`top_k=5` fixo → StormGuard poderia reduzir k sob carga. Mas o componente mais interessante é `similarity_saturation`: detecta quando os resultados do retrieve estão sempre com score muito alto (≥ 0.88) — sinal de "retrieval em loop" onde o modelo sempre vê as mesmas memórias.

Ponto de consumo: após `memory.retrieve()` em `memory.py:1631` → `storm_guard.record(final_top)` → k da próxima query possivelmente reduzido via `effective_k()` em `websocket.py:716`.

**Adaptador necessário:** `record()` espera `List[RetrievalResult]` (tipo v3.2 com `.score` e `.retrieval_depth`). O caminho vivo retorna `List[dict]` com `"ranking_score"` e sem `retrieval_depth`.

### C) Dados reais confirmam necessidade?

- Concentração de acessos baixa (top entry 2.9%, top 3 = 8.5%) → contradiz diretamente retrieval em loop. Se houvesse loop, as mesmas entries acumulariam muito mais acessos.
- Taxa de retrieval: <<1 query/s (conversacional). `retrieval_rate` para atingir score=1.0 requer 10 q/s — impossível fisicamente num chatbot.
- `abstraction_entropy` seria inoperante: entries do caminho vivo não têm `abstraction_level`.
- 2 dos 5 componentes do score (recursion, scheduler) nunca são alimentados no caminho vivo → ≤ 55% do score potencial disponível.

**Veredito C: NECESSIDADE INEXISTENTE NOS DADOS** — baixa concentração de acessos prova que não há loop de retrieval. Storm impossível no perfil de uso real.

### D) Custo de integração

O mais barato dos 7 satélites:
- Instância singleton em `api/main.py`: ~5 linhas
- `k = storm_guard.effective_k(5)` em `websocket.py:716`: ~5 linhas
- `storm_guard.record(results)` em `memory.py:1631`: ~10 linhas
- Adaptador de tipo (`List[dict]` → campos compatíveis com `record()`): ~20 linhas

**Total: ~40-50 linhas.** Mais barato dos 7.

### E) Tipo de ponte

**ESCRITA SOFT** — reduz k do retrieve, nunca bloqueia.

Fluxo: `websocket.py:716` → `k = storm_guard.effective_k(5)` → `memory.retrieve(query, top_k=k)` → `storm_guard.record(results)` → [storm ativo: k reduzido na próxima query].

Em uso real: `effective_k()` sempre retornaria 5 (score << 0.70). O satélite seria um **no-op observável** — rodando sem efeito.

**Componente isolável:** a lógica de `_update_similarity_saturation()` (`storm_guard.py:284-294`) é extraível em ~15 linhas independentes como heurística de "loop detection" sem o framework completo.

### F) Veredito por Satélite

**NÃO VALE na forma completa.** Custo mais baixo (40-50 linhas) mas necessidade inexistente. Em uso real seria no-op permanente.

A lógica de `similarity_saturation` merece extração separada (~15 linhas) como detector de loop de memória — mais sensato que integrar o satélite inteiro.

---

## Satélite 6 — AsyncDecisionGraph (`decision_graph_v32.py`)

### A) O que produz

DAG de `DecisionEvent` com links causais automáticos. `record(event)` → bool (enfileirado/descartado) — não-bloqueante, O(1) (`decision_graph_v32.py:118-129`).

Worker thread processa em batches de 20 (`decision_graph_v32.py:195-228`): conecta cada evento aos últimos N eventos como "causas" com DFS anti-ciclo (`decision_graph_v32.py:278-296`). Pruning por idade (max_age=3600s) e tamanho (max_nodes=5000) (`decision_graph_v32.py:306-315`).

`stats()` → `{n_nodes, n_edges, dropped, dominant_causes: [{id, type, weight}]}` (`decision_graph_v32.py:142-162`). `obs_pressure()` → fill ratio da fila interna para consumo pelo PressureMonitor.

Produz: **grafo de causalidade de decisões** — rastreabilidade de por que o sistema tomou cada decisão.

### B) Lacuna que endereçaria

`lineage.py` persiste qualidade por turno mas não rastreia causalidade entre operações. O grafo adicionaria: "este retrieve de baixa qualidade foi causado por aquele ingest 3 turnos atrás quando a pressão estava alta".

Ferramenta de **observabilidade de debugging**, não de decisão — não altera retrieve nem ranking. Consumo: endpoint `/health/graph` para diagnóstico post-mortem.

### C) Dados reais confirmam necessidade?

36 dias, single-user, sistema linear: turno N → retrieve → pipeline → resposta → memória. A cadeia causal tem um único caminho — rastreamento trivial. `lineage.py` já cobre o caso de uso: qualidade, contexto e session por turno.

Complexidade causal que justifica DAG assíncrono (múltiplos agentes, ramificações causais, competição de recursos) não existe neste sistema.

**Veredito C: NECESSIDADE INEXISTENTE NOS DADOS** — `lineage.py` cobre o caso de uso de rastreabilidade. DAG assíncrono é overengineering para sistema linear single-user.

### D) Custo de integração

~90-110 linhas: instância singleton, emissão de `DecisionEvent` nas operações principais, adaptação de schema (`DecisionEventType` do tipo v3.2 vs operações do caminho vivo), `graph.shutdown()` no lifespan.

Thread worker daemon permanente. Memória: até 5000 nós × ~200 bytes ≈ 1MB.

### E) Tipo de ponte

**LEITURA PURA** — observabilidade, zero impacto em decisões.

Fluxo: `pipeline.run_pipeline()` → emite `DecisionEvent` → `graph.record()` → worker thread processa → `graph.stats()` em endpoint de health.

`record()` é O(1) não-bloqueante: zero impacto na latência do caminho vivo.

### F) Veredito por Satélite

**NÃO VALE** — `lineage.py` já rastreia o que importa. DAG assíncrono com thread worker e pruning periódico é overengineering para sistema linear single-user. Custo médio (90-110 linhas + thread) sem benefício funcional sobre o que já existe.

---

## Satélite 7 — PressureMonitor (`pressure_monitor.py`)

### A) O que produz

Superfície de pressão com 6 dimensões tipadas: eviction, consolidation, entropy, retrieval, embedding, graph (`pressure_monitor.py:53-59`).

Cada dimensão: EMA smoothing alpha=0.20 + hysteresis para alert (≥ 0.65) e critical (≥ 0.82) (`pressure_monitor.py:187-206`). Composite ponderado: eviction×0.25 + consolidation×0.20 + entropy×0.20 + retrieval×0.15 + embedding×0.10 + graph×0.10 (`pressure_monitor.py:63-70`).

`read()` → `PressureSurface` (imutável, thread-safe) com `composite`, `alert_dimensions`, `critical_dimensions` (`pressure_monitor.py:169-179`).

**Nota importante:** `pressure_monitor.py` tem zero imports do ecossistema v3.2 — importa apenas `threading`, `time`, `dataclasses`, `enum`. É o único satélite completamente independente.

### B) Lacuna que endereçaria

É a camada de agregação do ecossistema v3.2. Isolado, as dimensões calculáveis com o caminho vivo atual são:

- **`update_eviction(len(episodic.entries) / EPISODIC_MEM_SIZE)`** — calculável agora. `EPISODIC_MEM_SIZE=200` (`config.py:44`). Com 594 entries, o fill ratio real é ≥ 100% (se limite não foi aumentado) ou ~74% (se configurado para 800).
- **`update_consolidation(backlog / target)`** — aproximável via entries não consolidadas.

As outras 4 dimensões (entropy, retrieval, embedding, graph) dependem dos Satélites 1, 5, 4, 6.

### C) Dados reais confirmam necessidade?

594 episódicas com `EPISODIC_MEM_SIZE=200` (default): `eviction_pressure = min(594/200, 1.0) = 1.0` se o limite não foi ajustado — **saturação confirmada**. Se ajustado para 800: fill_ratio = 74% — já em zona de alert (threshold 65%).

**Esta é a única dimensão com necessidade real confirmada pelos dados.** O store episódico está próximo ou acima do limite, sem nenhum mecanismo de alerta atual no caminho vivo.

**Veredito C: NECESSIDADE REAL PARCIAL** — dimensão `eviction` é comprovadamente relevante (fill ratio alto). Outras 5 dimensões: INDETERMINADO ou INEXISTENTE.

### D) Custo de integração

Para as 2 dimensões calculáveis (escopo reduzido):
- Instância singleton em `api/main.py`: **~5 linhas**
- `pressure_mon.update_eviction(len(episodic.entries) / EPISODIC_MEM_SIZE)` após `memory.add()` em `memory.py`: **~5 linhas**
- `pressure_mon.update_consolidation(...)` após `consolidate_promote_only()`: **~10 linhas**
- Endpoint `/health/pressure` em `api/main.py`: **~15 linhas**

**Total (escopo reduzido, 2 dimensões): ~35-40 linhas.**

Módulo completo com todos os satélites: ~200 linhas incluindo dependências.

### E) Tipo de ponte

**LEITURA PURA** — agrega sinais, expõe superfície de pressão. Não altera decisões.

Fluxo: `memory.add()` → `pressure_mon.update_eviction()` → `pressure_mon.read()` → endpoint `/health/pressure` → observabilidade externa ou gatilho de alerta.

Zero dependências externas: pode ser usado com 2 dimensões sem o ecossistema v3.2.

### F) Veredito por Satélite

**VALE INTEGRAR (escopo reduzido)** — único satélite com necessidade real confirmada pelos dados. Fill ratio do store episódico está alto e não há nenhum mecanismo de alerta atual. Custo mínimo (~35-40 linhas). Módulo sem dependências do ecossistema v3.2.

Integrar apenas dimensões `eviction` + `consolidation`. As outras 4 ficam com valor 0.0 até que os satélites respectivos sejam integrados (se forem).

---

## Orchestrator (`orchestrator_v32.py`) — Análise Estrutural

`OrchestratorV32` (`orchestrator_v32.py:140`) não é um satélite a integrar — é um **sistema paralelo completo** com seu próprio `ingest()` (`orchestrator_v32.py:212`) e `retrieve()` (`orchestrator_v32.py:322`) que **substituiriam** `memory.retrieve()` e `pipeline.run_pipeline()`.

Integrar o orquestrador = migrar o sistema de memória de `dict + JSON` para `ImmutableCognitiveNode + CognitiveSnapshotManager` (copy-on-write imutável). Seria uma reescrita do sistema de memória, não uma integração de módulo.

**Conclusão:** É o blueprint de uma v4 hipotética. Fora do escopo desta análise de integração incremental.

---

## Tabela Resumo — Ordenada por Valor/Custo

| Satélite | Output principal | Necessidade real? (dados) | Custo (linhas) | Tipo de ponte | Veredito |
|----------|-----------------|--------------------------|----------------|---------------|----------|
| **PressureMonitor** (escopo reduzido) | Fill ratio eviction + consolidation | ✅ REAL (594 entries, fill alto) | ~35-40 | LEITURA pura | **VALE** |
| **SemanticBiodiversityEngine** | MRD + collapse/dominance detection | ❓ INDETERMINADO (MRD não medido) | ~110 | ESCRITA (diversity_penalty no ranking) | **TALVEZ** (medir MRD primeiro) |
| **RetrievalStormGuard** (extração mínima) | Similarity saturation (loop detection) | ❌ INEXISTENTE (acesso low-concentration) | ~15 (extração) | ESCRITA SOFT (k dinâmico) | **TALVEZ** (extração de 15 linhas, não o satélite completo) |
| **MetaStabilityController** | OperationalParams (k dinâmico, consolidation mode) | ❓ PARCIAL (adaptação tem valor, mas dados não mostram problema atual) | ~250-300 (com deps) | ESCRITA (modifica top_k e consolidation) | **NÃO VALE** (incompatível sem outros satélites; núcleo em 2 linhas) |
| **RetrievalStormGuard** (completo) | Circuit breaker de retrieval + k dinâmico | ❌ INEXISTENTE (não há storm em single-user) | ~40-50 | ESCRITA SOFT | **NÃO VALE** |
| **CognitiveEconomyEngine** | Gate de budget por operação (rate limiting) | ❌ INEXISTENTE (single-user, LLM é gargalo) | ~40-50 | GATE (permite/rejeita) | **NÃO VALE** |
| **FractalPressureRegulator** | global_pressure float (plombing interno) | ❌ INEXISTENTE (sem consumidor sem MetaStability) | ~50-60 | LEITURA pura (sem consumidor) | **NÃO VALE** |
| **AsyncDecisionGraph** | DAG causal de decisões (observabilidade) | ❌ INEXISTENTE (lineage.py cobre; sistema linear) | ~90-110 | LEITURA pura | **NÃO VALE** |

---

## Mapa de Dependências entre Satélites

```
PressureMonitor ←── eviction/consolidation (CAMINHO VIVO — independente)
                ←── entropy         (SemanticBiodiversityEngine)
                ←── retrieval       (RetrievalStormGuard)
                ←── embedding       (EmbeddingCache v3.2 — deletado)
                ←── graph           (AsyncDecisionGraph)
                         ↓
              MetaStabilityController
                         ↓ composite pressure
               OperationalParams (top_k, consolidation_mode)
                         ↑
              FractalPressureRegulator ←── CognitiveTick do caminho vivo
```

**Conclusão do mapa:** PressureMonitor é o único satélite que pode ser integrado de forma standalone (sem deps). Todos os outros formam uma rede de dependências mútuas — integrar um pede o próximo.

---

## Recomendações por Prioridade

### 1. FAZER AGORA — PressureMonitor (escopo reduzido)
**~35-40 linhas, 2 dimensões, zero dependências.**

```python
# api/main.py — instância singleton
pressure_mon = PressureMonitor()

# memory.py — após memory.add()
pressure_mon.update_eviction(len(self.episodic.entries) / EPISODIC_MEM_SIZE)

# consolidation.py — após consolidate_promote_only()
pressure_mon.update_consolidation(promoted / max(scanned, 1))

# api/main.py — endpoint
@app.get("/health/pressure")
async def health_pressure():
    return pressure_mon.read()
```

Entrega: monitoramento real do fill ratio do store episódico (que tem necessidade confirmada pelos dados).

---

### 2. MEDIR PRIMEIRO — SemanticBiodiversityEngine (condicionada)
**Antes de integrar, medir MRD real do store:**

```python
# ~5 linhas, pode rodar como script standalone
from edp.embeddings import embed
import numpy as np
entries_with_emb = [e for e in episodic.entries if e.get("embedding")]
emb_matrix = np.array([e["embedding"] for e in entries_with_emb], dtype=np.float32)
centroid = emb_matrix.mean(axis=0)
sims = (emb_matrix @ centroid) / (np.linalg.norm(emb_matrix, axis=1) * np.linalg.norm(centroid))
mrd = float((1 - sims).mean())
print(f"MRD: {mrd:.4f}  (< 0.15 = collapse, > 0.35 = diverso)")
```

Se MRD < 0.20 → integrar BiodiversityEngine (valor confirmado).
Se MRD > 0.35 → não integrar (problema não existe).

---

### 3. EXTRAÇÃO MÍNIMA — Similarity Saturation (opcional, 15 linhas)

Se o usuário quiser detectar retrieval em loop sem integrar o StormGuard completo:

```python
# memory.py — após scored[:top_k], antes de return
sat_count = sum(1 for r in final_top if r["ranking_score"] >= 0.88)
if len(final_top) > 0 and sat_count / len(final_top) >= 0.80:
    logger.warning("[retrieve] similarity_saturation=%.2f — possível loop de memória",
                   sat_count / len(final_top))
```

**~5 linhas reais** de valor extraído do StormGuard sem o framework.

---

### 4. NÃO FAZER (em ordem de custo desperdiçado)

| Satélite | Por quê não |
|----------|-------------|
| CognitiveEconomyEngine | Rate limiting sem uso real; thread extra permanente; single-user |
| FractalPressureRegulator | Plombing sem consumidor; pressão < 0.75 impossível em uso real |
| AsyncDecisionGraph | lineage.py já cobre; overhead de thread + 1MB de RAM sem valor |
| MetaStabilityController | Sinais de entrada ausentes; composite sempre 0; top_k=10 fixo não é melhor que dinâmico |
| RetrievalStormGuard (completo) | No-op permanente; acesso low-concentration contradiz a premissa |

---

## Veredito Final

De 7 satélites:
- **1 vale integrar agora** (PressureMonitor, escopo reduzido — necessidade real, custo mínimo, zero dependências)
- **1 vale após medição** (SemanticBiodiversityEngine — valor condicional a MRD real)
- **1 tem núcleo extraível** (StormGuard → similarity saturation em 15 linhas)
- **4 não valem** (Economy, MetaStability, PressureRegulator, DecisionGraph — necessidade inexistente ou dependência em cadeia sem consumidor)

O ecossistema v3.2 foi projetado para um cenário de pressão cognitiva real (multi-tenant, alta taxa de queries, risco de overload) que **não existe no perfil de uso atual** (single-user conversacional, 36 dias sem degradação). Os satélites são coerentes internamente mas endereçam problemas que os dados reais mostram que não acontecem.

---

## Metadados

```
Repositório:     devborgesr/edp_v5
Branch de trabalho: auditoria-curadoria
Branch main:     intacta
Data:            2026-06-24
Commits analisados: feb0db9 (ANTIGO, 11 arquivos v3.2) | cbacac4 (CURADO)
Dados de uso:    36 dias de produção, single-user, fornecidos pelo usuário
Modo:            análise pura — nenhum código alterado, nenhuma integração construída
```
