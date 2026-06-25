# EDP v5 — Resultado Completo de Auditoria e Curadoria

> **Repositório:** `devborgesr/edp_v5`
> **Branch de trabalho:** `auditoria-curadoria`
> **Período:** 2026-06-24
> **Commits desta sessão:** `feb0db9` (baseline) → `3dddf3c` → `1c3d893` → `f99835c` (estado final)
> **main:** intacta, zero merges feitos
> **Ambiente:** Python 3.11, sem `.venv` no repo (torch/sentence-transformers excluídos pelo .gitignore); dependências instaladas no ambiente de nuvem para validação de runtime

---

## 1. O que é o EDP v5

**EDP (Episodic-to-Declarative Pipeline)** é um sistema de memória de longo prazo para LLMs. Ele:

1. Recebe mensagens do usuário via WebSocket
2. Comprime/processa o texto em chunks com embeddings (`pipeline.py`)
3. Armazena memórias episódicas com score, prioridade e metadados (`memory.py`)
4. Promove automaticamente memórias frequentemente acessadas (acessos ≥ threshold) para memória semântica (`auto_consolidation.py`)
5. Recupera contexto relevante antes de cada resposta do LLM (`memory.retrieve()`)
6. Persiste lineage de qualidade por turno (`lineage.py`)

**Caminho vivo em produção:**
```
run.py:serve() → uvicorn → edp/api/main.py:lifespan()
  → [background jobs: cognitive_decisions, contradiction_flagger, auto_consolidation, CHI]
  → websocket.py:per-turn handler
    → run_pipeline(message, message, session_id)      [websocket.py:629]
    → memory.retrieve(message, top_k=5, min_score=0.20) [websocket.py:716]
    → LLM stream
    → memory.add(combined, score=0.65, ...)            [websocket.py:1210]
    → lineage persist                                   [websocket.py:1305-1313]
```

**Estatísticas do repo:**
- 128 arquivos `.py` / 42.244 linhas (antes da curadoria)
- Linguagem: Python 3.11
- Modelo de embedding: `sentence-transformers/all-MiniLM-L6-v2` (384 dims)
- Armazenamento: JSON em disco (`EDP_BASE_DIR`, default `/content/edp_v3_memory`)

---

## 2. Arquitetura central verificada

### 2.1 Fórmula de ranking de memória (`memory.py:723-726`)

```python
rank_score = (
    sim                  # similaridade cosine com a query
    × d                  # decaimento temporal (exponencial)
    × prio               # prioridade: alta=1.5, media=1.0, baixa=0.7
    × ab                 # abstraction boost (entries abstratas sobem)
    × epi_multiplier     # multiplicador episódico
    × src_weight         # peso por source_type (llm_response, user_input, etc.)
    × dom_penalty        # penalidade de dominância (top-3 por acessos, >12% concentração → ×0.70)
    × anchor_boost       # 1.20 se is_epistemic_anchor, else 1.0
    × session_boost      # 1.60 se mesma sessão, 0.85 se fora
)
```

**Dois exocórtices:** cada sessão tem escopo `cognitive` (uso real) e `sprint` (testes). O `auto_consolidation` opera no scope ativo.

### 2.2 Loop de aprendizado principal (FECHADO)

```
memory.add(entry, acessos=0)
  → retrieve() incrementa entry["acessos"] += 1 [memory.py:855]
  → auto_consolidation job: acessos >= 3 → consolidate_promote_only()
  → entry promovida para memória semântica
  → retrieve() futuro encontra a entry no scope semântico
```

Este é o único loop de feedback funcional confirmado em runtime.

---

## 3. Achados da auditoria (19 confirmados, 1 corrigido)

### 3.1 Dois sistemas de memória paralelos — ponte nunca ligada

| Campo | Valor | Linha |
|-------|-------|-------|
| `MemoryBridgeV32._v32_store` | sempre `None` | `pipeline.py:90` |
| `register_v32_store()` callers | **zero** em todo o repositório | `pipeline.py:93` |
| Ramo v3.2 em `consolidate()` | `if self._v32_store is not None:` — nunca True | `pipeline.py:107` |

**Evidência git:** os 9 arquivos v3.2 chegaram no mesmo commit de upload em massa (`14179eb`, 2026-05-20). O caminho vivo teve 15+ commits após essa data. `register_v32_store` nunca teve chamador em nenhum commit da história do repositório (`git log -S "register_v32_store("` → só o commit de criação).

### 3.2 O bloco v3.2 é governança, não qualidade

| Módulo | O que faz | Relevância para o gap de memória |
|--------|-----------|----------------------------------|
| `economy.py` | Budget cognitivo (tokens, recursão) | Nenhuma |
| `meta_stability.py` | Modo NORMAL→EMERGENCY | Nenhuma |
| `biodiversity.py` | Diversidade semântica | Nenhuma |
| `storm_guard.py` | Circuit breaker para retrieval storms | Nenhuma |
| `pressure_regulator.py` | Pressão global com hysteresis | Nenhuma |
| `decision_graph_v32.py` | Causalidade de decisões | Nenhuma |
| `snapshot_manager.py` | Consistência temporal geracional | Nenhuma |
| `orchestrator_v32.py` | Coordena os 7 anteriores | Nenhuma |

Nenhum módulo v3.2 avalia qualidade da resposta do LLM, classifica memórias por utilidade, ou influencia `memory.add`. O v3.2 resolveria um problema de **governança de recursos** num sistema que tem um gap de **qualidade de sinal de memória**. Problemas diferentes.

### 3.3 Loops abertos (sinais produzidos e descartados)

| ID | Sinal | Onde é produzido | Onde deveria chegar | Status atual |
|----|-------|------------------|---------------------|--------------|
| A1 | `cognitive_decisions` (key_assertion, concepts, domain) | Background LLM job, `api/main.py:177` | Fórmula de ranking `memory.py:723-726` | **Nunca lido** — campo gravado em entry dict mas ausente dos 9 fatores de ranking; destruído por `merge_cluster()` em `consolidation.py:140-149` |
| A2 | `scan_results()` do contradiction_flagger | `memory.py:1647` após cada retrieve | `epistemic_status` das entries detectadas | **Return descartado** — `get_flagger().scan_results(final_top)` sem atribuição, dentro de `try/except: pass` |
| A3 | `adaptive_decision` (AdaptiveController) | `pipeline.py:448` | `top_k` ou `min_score` do retrieve | **Só logado** — `_trace("adaptive_controller", ...)` com debug=False; variável nunca lida depois |
| A4 | `reflection.reweights` (MetaReasoner) | `pipeline.py:460` | Estágio Keep/Summarize/Drop | **Variável nunca lida** — `reflection.` não aparece no arquivo após linha 472 |
| A5 | `quality_score` / `aggregate_score` | `websocket.py:645` | `memory.add(score=...)` | **Hardcoded** — `memory.add(combined, score=0.65, ...)` em `websocket.py:1210`; `quality_score` só vai para `lineage_quality` |

**Nota sobre A5:** `run_pipeline(message, message)` em `websocket.py:629` passa a mensagem do usuário como **contexto e query simultaneamente**. Portanto `quality_score` mede qualidade do **processamento do input**, não da **resposta do LLM**. Conectar este score a `memory.add` seria um mismatch semântico — confirmado e descartado.

**Nota sobre A2 (scan_results):** verificado em runtime — retorna `int` (contagem), não lista de flags. O patch proposto originalmente estava errado. Para fechar este loop, `scan_results()` precisa ser modificado primeiro para retornar os IDs das entries sinalizadas.

### 3.4 Gargalo G1 — re-embedding após deduplicação

**Bug em `pipeline.py:388-391` (antes da correção):**
```python
chunks_deduped = deduplicate(chunks, DEDUP_THRESH, chunk_embs)
if len(chunks_deduped) < len(chunks):
    chunk_embs = embed(chunks_deduped)   # BUG: descarta embeddings já computados
    chunks = chunks_deduped
```

`deduplicate()` em `embeddings.py:212-231` já recebe `chunk_embs` pré-computados, calcula a matrix de similaridade, e mantém os índices dos items a preservar — mas retornava apenas os textos, perdendo os índices.

### 3.5 Thresholds não calibrados

| Threshold | Valor | Arquivo:Linha | Calibração documentada? |
|-----------|-------|---------------|------------------------|
| `SESSION_BOOST_FACTOR` | `1.60` | `memory.py:76` | ✅ Incidente Docker/Redis 04/06/2026 |
| `OUT_OF_SESSION_PENALTY` | `0.85` | `memory.py:77` | ✅ Mesmo incidente |
| `promote_threshold` | `3` | `auto_consolidation.py:47` | ❌ "mesmo default do endpoint" |
| `SIMILARITY_THRESHOLD` | `0.85` | `contradiction_flagger.py:65` | ❌ "palpite inicial. Commit 4 (Gauss) calibrará" — nunca executado |
| `anchor_boost` | `1.20` | `memory.py:699` | ❌ Por analogia |
| Dominância trigger | `12%` | `memory.py:664` | ❌ Sem documentação |
| Dominância penalidade | `×0.70` | `memory.py:691` | ❌ Sem documentação |
| `DEDUP_THRESH` | `0.75` | `config.py:19` | ❌ Sem documentação |

**Regra aplicada:** thresholds em loops abertos não devem ser calibrados antes de o loop ser fechado. `SIMILARITY_THRESHOLD = 0.85` é o caso mais claro — calibrar com `scan_results()` descartado produz efeito observável zero.

---

## 4. Classificação dos módulos

### MÚSCULO (caminho vivo, manter)

| Módulo | Evidência de uso no serve path |
|--------|-------------------------------|
| `edp/memory.py` | `websocket.py:716, 1210` — retrieve e add |
| `edp/api/routes/websocket.py` | `run.py:50-53` — handler principal |
| `edp/pipeline.py` | `websocket.py:629` — chamado por turno |
| `edp/llm_adapter.py` | `api/main.py:81-83` |
| `edp/echo_chamber.py` | `websocket.py:951` |
| `edp/consolidation.py` | `auto_consolidation.py:93` |
| `edp/runtime/auto_consolidation.py` | `api/main.py:213` — background job |
| `edp/runtime/lineage.py` | `websocket.py:1305-1313` |
| `edp/affective_calibration.py` | `llm_adapter.py:2447`, `api/routes/memory.py:302` |
| `edp/co_occurrence.py` | `llm_adapter.py:1842`, `api/routes/memory.py:637` |
| `edp/belief_graph.py` | `scheduler.py:6`, importado por módulos ativos |

### INERTE (ativo mas efeito nulo — requer decisão)

| Módulo | Problema |
|--------|---------|
| `edp/runtime/cognitive_decisions.py` | Campo gravado nunca lido em ranking (`memory.py:723-726`); destruído por `merge_cluster()` (`consolidation.py:140-149`) |
| `edp/runtime/contradiction_flagger.py` | Chamado em `memory.py:1647`, retorno descartado silenciosamente |
| `edp/adaptive_controller.py` | Decisão calculada em `pipeline.py:448`, só vai para `_trace(debug=False)` |
| `edp/meta_reasoner.py` | `reflection.reweights` calculados em `pipeline.py:460`, nunca aplicados |
| `edp/runtime/health_index.py` | Escreve `health_history.jsonl`, nenhum módulo lê para decisão |
| `edp/runtime/pareto_store.py` | Escreve eventos (`memory.py:881`), zero leitores para decisão |

### GORDURA (código morto, podado)

11 arquivos do bloco v3.2 — **removidos nesta sessão** (ver seção 5).

---

## 5. O que foi executado nesta sessão

### 5.1 C1 — Fix do gargalo G1 (`commit 1c3d893`)

**`edp/embeddings.py`** — `deduplicate()` ganhou parâmetro `return_indices=False` (backward-compatible):

```python
def deduplicate(
    items: list[str],
    threshold: float,
    embeddings: np.ndarray | None = None,
    return_indices: bool = False,          # NOVO — backward-compatible
) -> list[str] | tuple[list[str], list[int]]:
    if not items:
        return ([], []) if return_indices else []
    emb = embeddings if embeddings is not None else embed(items)
    sim_matrix = cosine_similarity(emb, emb)
    keep: list[int] = []
    for i in range(len(items)):
        if not any(float(sim_matrix[i, j]) > threshold for j in keep):
            keep.append(i)
    kept_items = [items[i] for i in keep]
    return (kept_items, keep) if return_indices else kept_items
```

**`edp/pipeline.py:388-391`** — usa índices para fatiar embeddings existentes:

```python
# ANTES (bug)
chunks_deduped = deduplicate(chunks, DEDUP_THRESH, chunk_embs)
if len(chunks_deduped) < len(chunks):
    chunk_embs = embed(chunks_deduped)   # re-embeds do zero

# DEPOIS (fix)
chunks_deduped, kept_idx = deduplicate(
    chunks, DEDUP_THRESH, chunk_embs, return_indices=True
)
if len(chunks_deduped) < len(chunks):
    chunk_embs = chunk_embs[kept_idx]   # fatia embeddings existentes — sem re-embed
    chunks     = chunks_deduped
```

**Validado em runtime:**
```
C1 ok — dedup: 3 → 2 | shape embs: (3, 384) → (2, 384)
backward compat ok — type: list
```

### 5.2 P1 — Remoção do bloco v3.2 (`commit f99835c`)

**13 arquivos alterados — 5.529 linhas deletadas:**

```
edp/biodiversity.py        (deletado)
edp/decision_graph_v32.py  (deletado)
edp/economy.py             (deletado)
edp/embedding_cache.py     (deletado)
edp/meta_stability.py      (deletado)
edp/orchestrator_v32.py    (deletado)
edp/pressure_monitor.py    (deletado)
edp/pressure_regulator.py  (deletado)
edp/snapshot_manager.py    (deletado)
edp/storm_guard.py         (deletado)
edp/types_v32.py           (deletado)
run.py                     (modificado — removidos: test_v32(), _query_v32(), dispatch test_v32, flag --v32)
benchmark_edp.py           (modificado — removidas: 8 funções de bench v3.2, entradas em SUITES/QUICK_SUITES/STRESS_SUITES)
```

**`MemoryBridgeV32` em `pipeline.py:67-124` foi mantida intocada** — commit separado pendente de decisão do autor.

**Validação de runtime pós-poda:**

| Teste | Resultado |
|-------|-----------|
| `from edp.api.main import app` | ✅ `api ok` |
| `from edp.pipeline import run_pipeline` | ✅ `pipeline ok` |
| `import run` | ✅ `run ok` |
| `import benchmark_edp` | ✅ `benchmark importa ok` |
| Smoke test `run_pipeline('texto...', 'query?')` | ✅ `pipeline executou ok` — context chunks: 1, reduction: 0.0% |
| `MemoryStore + retrieve + consolidate_promote_only` | ✅ retrieve: 3 resultados, consolidate: scanned=3 promoted=3 |
| Import sweep de 13 módulos do caminho vivo | ✅ 12/12 (1 fail: path errado no sweep, correto é `edp.runtime.lineage`) |

**Referências v3.2 residuais:** zero imports. Cinco menções apenas em comentários de docstring:
- `edp/retrieval_ann.py:12` — comentário descritivo
- `edp/runtime/health_index.py:21` — comentário explicativo
- `edp/exceptions.py:5,11,12,90` — docstrings
- `edp/vector_store.py:5,70` — docstrings
- `edp/pipeline.py:74` — docstring de `MemoryBridgeV32` (intocada por instrução)

---

## 6. O que está pendente (requer decisão ou dados)

### 6.1 Requer decisão do autor

**P2-parcial — `MemoryBridgeV32` em `pipeline.py:67-124`**

A classe inteira pode ser removida — `register_v32_store()` tem zero callers no histórico git inteiro. Porém `pipeline.py:82` instancia a classe, e `consolidate()` roda o caminho v3.1 sempre (o ramo v3.2 nunca executa mas a classe não pode ser removida sem também remover a instanciação). Deve ser um commit cuidadoso separado.

**Decisão pendente sobre módulos INERTE:**

- `cognitive_decisions`: **CONECTAR** (adicionar `concepts_boost` ao ranking) vs. **PODAR** (custo LLM pago sem benefício). Requer medir cobertura real — ≤30% atual porque só cobre entries episódicas de sessões 60s-24h.
- `scan_results()`: para fechar o loop, `scan_results()` precisa ser refatorado para retornar IDs das entries sinalizadas (atualmente retorna `int`). Então conectar ao `epistemic_status`.
- `adaptive_decision`: conectar `memory_mode` ao `top_k` do retrieve interno — verificar primeiro os valores possíveis de `adaptive_decision.memory_mode` em `edp/adaptive_controller.py`.

### 6.2 Bloqueado por falta de dados reais

Os dados de produção vivem em `/content/edp_v3_memory` (ou `EDP_BASE_DIR`), fora deste repositório. Análises bloqueadas:

| Item | O que precisa | Por quê está bloqueado |
|------|--------------|----------------------|
| Calibrar `promote_threshold=3` | Distribuição de `acessos` por entry | Precisa de `episodic.json` das sessões reais |
| Calibrar `SIMILARITY_THRESHOLD=0.85` | Flags revisadas por operador | Só faz sentido após fechar o loop de `scan_results()` |
| Calibrar dominância (12%, ×0.70) | Distribuição de concentração de acessos | Precisa de dados reais |
| Medir cobertura real de `cognitive_decisions` | Percentual de entries com o campo | Precisa de `episodic.json` |
| Validar CHI como gate de decisão | Histórico de saúde vs. qualidade percebida | Precisa de `health_history.jsonl` com volume suficiente |

**Procedimento sugerido para calibrar `promote_threshold` quando os dados estiverem disponíveis:**
```python
# Ler episodic.json de todas as sessões
# Plotar distribuição de entry["acessos"]
# Identificar bimodal: ruído (acessos=1-2) vs. sinal (acessos>=N)
# Testar via: EDP_CONSOLIDATE_THRESHOLD=N (variável de ambiente já suportada)
```

---

## 7. O que NÃO deve ser feito

### 7.1 Conectar `quality_score` → `memory.add`

`run_pipeline(message, message)` em `websocket.py:629` passa a mensagem do usuário como contexto e query simultaneamente. Portanto `aggregate_score` / `quality_score` mede qualidade do **processamento do input**, não da **resposta do LLM**. `memory.add(score=0.65)` é fixo porque ainda não existe uma métrica de qualidade da resposta. Conectar o score existente seria um mismatch semântico que introduziria um sinal errado como proxy de qualidade.

### 7.2 Calibrar `SIMILARITY_THRESHOLD` antes de fechar C2

Com `scan_results()` retornando um inteiro ignorado, ajustar 0.85 para qualquer valor produz o mesmo efeito observável: zero.

### 7.3 Ligar a ponte v3.2 (`register_v32_store`)

O v3.2 resolveria governança de recursos (budget, homeostase, circuit-breaking). O gap real é qualidade de sinal de memória (qual sinal indica que uma memória é útil?). São problemas diferentes. Se governança for relevante no futuro, o v3.2 deve ser reescrito com integração nativa ao serve path.

---

## 8. Histórico de commits desta branch

```
feb0db9  baseline antes da curadoria
3dddf3c  docs: AUDITORIA_CURADORIA.md — registro completo de auditoria (Fases 0-5)
1c3d893  fix(C1): elimina re-embed desnecessário após deduplicação (G1)
f99835c  prune(P1): remove bloco v3.2 completo — 11 arquivos + referências órfãs
```

---

## 9. Estado atual do repositório

**Arquivos de código no caminho vivo (pós-curadoria):**

```
edp/
  api/
    main.py              — FastAPI app + lifespan + background jobs
    routes/
      websocket.py       — handler de turno (WebSocket)
      memory.py          — endpoints REST de memória
      lineage.py         — endpoints de lineage
      flags.py           — endpoints de contradiction flags
      ...
  memory.py              — MemoryStore, retrieve, add, ranking 9-fatores
  pipeline.py            — run_pipeline, chunking, dedup(CORRIGIDO), scoring
                           MemoryBridgeV32 (PENDENTE remoção)
  embeddings.py          — embed, deduplicate(CORRIGIDO return_indices)
  consolidation.py       — cluster_entries, merge_cluster, consolidate_promote_only
  llm_adapter.py         — LLM streaming, echo_chamber, affective_calibration
  adaptive_controller.py — INERTE: decisão calculada, nunca lida
  meta_reasoner.py       — INERTE: reweights calculados, nunca aplicados
  runtime/
    auto_consolidation.py  — background job: episódica → semântica (threshold=3)
    contradiction_flagger.py — INERTE: scan_results() retorno descartado
    cognitive_decisions.py  — INERTE: campo nunca lido em ranking
    lineage.py             — persist de lineage por turno
    health_index.py        — CHI: observabilidade (não influencia decisões)
    pareto_store.py        — eventos: observabilidade (não influencia decisões)
    background_loop.py     — job scheduler
    registry.py            — session registry
```

**Removidos nesta curadoria:** `edp/biodiversity.py`, `edp/decision_graph_v32.py`, `edp/economy.py`, `edp/embedding_cache.py`, `edp/meta_stability.py`, `edp/orchestrator_v32.py`, `edp/pressure_monitor.py`, `edp/pressure_regulator.py`, `edp/snapshot_manager.py`, `edp/storm_guard.py`, `edp/types_v32.py` — 11 arquivos, ~5.500 linhas.

---

## 10. Resumo executivo para outra IA

**O que funciona bem:**
- Loop de aprendizado por acessos: episódica → semântica (fechado, testado em runtime)
- Ranking multiplicativo 9-fatores com session boost documentado por incidente real
- Lineage por turno (observabilidade completa)
- Deduplicação de chunks agora sem re-embedding (C1 aplicado)

**O maior problema estrutural:**
- 4 módulos computam sinais ricos (cognitive_decisions, contradiction_flagger, adaptive_controller, meta_reasoner) que nunca retroalimentam nenhuma decisão. O custo computacional é pago, o benefício é zero.

**A correção mais barata disponível agora:**
- `scan_results()` em `contradiction_flagger.py`: modificar para retornar IDs das entries sinalizadas (atualmente retorna `int`), então conectar a `epistemic_status` das entries em `memory.py:1647`. Estimativa: ~20 linhas em 2 arquivos.

**O que precisa de dados reais antes de qualquer ação:**
- Todos os thresholds de calibração (promote_threshold, SIMILARITY_THRESHOLD, dominância)
- Decisão sobre cognitive_decisions (conectar vs. podar depende da cobertura real)

**O que não deve ser tocado:**
- `quality_score` → `memory.add`: mismatch semântico fundamental
- `SIMILARITY_THRESHOLD` antes de fechar o loop de `scan_results()`
- Ponte v3.2: resolve o problema errado

---

*Documento gerado em 2026-06-24. Branch `auditoria-curadoria`. main intacta.*
