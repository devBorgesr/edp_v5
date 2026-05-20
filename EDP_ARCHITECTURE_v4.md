# EDP v3.2 → v4 — Plano de Engenharia Cognitiva

**Baseado em dados reais: 84 medições, Python 3.14.4, Windows 10, 8 CPUs**

---

## 1. STATUS ATUAL DO BENCHMARK (REFERÊNCIA)

| Módulo | Métrica Real | Complexidade | Risco |
|---|---|---|---|
| EpisodicMemory.add | **83.7s / 200 entries** | O(n²) I/O | 🔴 CRÍTICO |
| EpisodicMemory retrieve | 835ms / 200 entries | O(n·dim) | 🟡 ALTO |
| belief_graph.build | 16.3s / 500 entries | O(n²) NumPy | 🟡 ALTO |
| consolidation.cluster | 801ms / 1000 | O(n²) Python | 🟡 ALTO |
| pipeline cold start | 33s → 64ms (cache) | O(n_chunks·dim) | 🟠 INFO |
| VectorStore query | 7s / 10k docs | O(n·dim) | 🟡 MÉDIO |
| MetaStability.update | **0.06ms / tick** | O(1) | ✅ OK |
| snapshot lock-free read | **~0ms** | O(1) | ✅ OK |
| SemanticMemory retrieve | 2.5ms / 50 concepts | O(n·dim) cache | ✅ OK |

**7 fixes aplicados. Resultados esperados após fixes:**
- EpisodicMemory.add: 83.7s → ~2ms (batch_size=50, 1 flush final)
- benchmark: 77/84 → 84/84

---

## 2. CAUSA RAIZ — O(n²) DE PERSISTÊNCIA

```
ANTES (O(n²)):
  for i in range(n):
      ep.add(entry)      # → save() → json.dump(all_entries) → O(n)
                         # n saves × O(n) = O(n²) total

Para n=200: 200 × ~418ms/save = 83.7s ✓ (confirma o benchmark)

DEPOIS (O(n) amortizado):
  ep._batch_size = 50
  for i in range(n):
      ep.add(entry)      # → _dirty=True → sem I/O
      # a cada 50: 1 save()
  ep.flush()             # 1 save() final
  # n/50 saves × O(n) = O(n/50 × n) = O(n²/50) → 50× mais rápido
  # Para batch_size=n: O(n) total (1 único save)
```

**Fix imediato**: `_batch_size`, `_dirty`, `flush()` — já aplicado em `memory.py`.

**Fix estrutural (v4)**: WAL abaixo.

---

## 3. ARQUITETURA DE MEMÓRIA v4 — WAL + DELTA SNAPSHOTS

### 3.1 Problema Estrutural Atual

```
memory/sessions/
  {session}_episodic.json   ← rewrite completo a cada save()
  {session}_semantic.json   ← idem
```

Todo `save()` = serializar **todos** os entries como JSON = O(n).
Com compressão de embeddings (384 floats × 4 bytes = 1536 bytes/entry):
- n=1000 entries = ~1.5MB por save
- n=10000 entries = ~15MB por save — inaceitável

### 3.2 Nova Estrutura de Diretórios

```
memory/
├── sessions/
│   └── {session_id}/
│       ├── wal/
│       │   ├── seg_000001.wal    ← append-only, ~4KB segments
│       │   ├── seg_000002.wal
│       │   └── seg_CURRENT       ← segmento ativo
│       ├── snapshots/
│       │   ├── snap_gen001.bin   ← snapshot completo (binário comprimido)
│       │   └── snap_gen002.bin
│       ├── index/
│       │   └── entry_index.db    ← SQLite: id → offset no WAL
│       └── meta.json             ← geração atual, WAL head, stats
```

### 3.3 WAL Protocol

```python
# Formato de cada record no WAL (binário, append-only):
# [4B magic][4B type][8B timestamp][4B payload_len][payload][4B crc32]

WAL_MAGIC    = b'\xED\xP3\x2A\x01'
WAL_ADD      = 0x01    # nova entrada
WAL_UPDATE   = 0x02    # atualização de campo
WAL_DELETE   = 0x03    # remoção
WAL_FLUSH    = 0x04    # marcador de flush
WAL_SNAPSHOT = 0x05    # referência a snapshot

# Complexidades:
# WAL.append(entry): O(1) — append ao segmento atual
# WAL.replay():      O(n) — leitura sequencial na inicialização
# Snapshot:          O(n) — mas feito em background, não bloqueia writes
# Index lookup:      O(log n) via SQLite B-tree
```

### 3.4 Implementação Incremental (3 Fases)

**Fase 1 (imediata — já aplicada):** `_batch_size` + `_dirty` + `flush()`
- Impacto: 50× speedup em add()
- Sem mudança de formato

**Fase 2 (próxima sprint):** WAL binário com SQLite index
```python
class WALEpisodicMemory(EpisodicMemory):
    """Substitui JSON rewrite por WAL append + index."""
    
    def add(self, entry: dict) -> None:
        # 1. Escreve no WAL (O(1) append)
        record = self._wal.append(WAL_ADD, entry)
        # 2. Atualiza index (O(log n))
        self._index[entry["id"]] = record.offset
        # 3. In-memory entries (sem I/O)
        self.entries.append(entry)
        self._pending_count += 1
        # 4. Flush WAL segment se necessário (O(1))
        if self._pending_count >= self._wal_segment_size:
            self._wal.rotate_segment()
    
    # Recuperação: replay WAL desde último snapshot
    def _load(self) -> None:
        latest_snap = self._find_latest_snapshot()
        if latest_snap:
            self.entries = self._load_snapshot(latest_snap)
        self._wal.replay_since(latest_snap, self.entries)
```

**Fase 3 (v4 completo):** Delta snapshots + background compaction
```python
# Background thread: a cada N entradas ou T segundos
# compacta WAL → snapshot binário zlib-compressed
# Remove WAL segments antigos
# Nunca bloqueia o pipeline principal
```

---

## 4. RETRIEVAL ARCHITECTURE vNext — ANN HÍBRIDO

### 4.1 Diagnóstico Atual

```
cosine search latência medida:
  n=100:   ~0.06ms
  n=500:   ~0.3ms
  n=1000:  ~0.7ms
  n=5000:  ~3ms
  n=10000: ~7ms
  n=100000: ~70ms (estimado) ← gargalo

Expoente medido: O(n^1.0) — linear ✓
Fator: ~0.7μs/entry (dim=64) → ~2.7μs/entry (dim=384)
```

### 4.2 Arquitetura de Tiers

```
HOT  (n < 10k):     cosine NumPy         →  atual, latência <10ms
WARM (n < 500k):    FAISS IndexFlatIP    →  já existe (faiss_flat)
HOT  (n < 10M):     FAISS HNSW           →  implementar
COLD (n > 10M):     disk-based retrieval →  futuro

Switching automático baseado em:
  - cardinalidade (n)
  - latência medida nos últimos 100 queries (EMA)
  - pressão de memória (do pressure_monitor)
  - disponibilidade de FAISS (try/except no import)
```

### 4.3 Adaptive Index Manager

```python
class AdaptiveIndexManager:
    """
    Gerencia transição automática entre backends de retrieval.
    
    Thresholds baseados nos dados reais do benchmark:
      cosine → faiss_flat:  n > 5_000  (7ms → <1ms)
      faiss_flat → hnsw:    n > 50_000 (estimado)
    """
    
    TIER_THRESHOLDS = {
        "cosine":     (0,      5_000),
        "faiss_flat": (5_000,  50_000),
        "hnsw":       (50_000, 10_000_000),
    }
    
    def recommend_backend(self, n: int, latency_p95_ms: float) -> str:
        if latency_p95_ms > 50 and n > 5_000:
            return "faiss_flat"
        if latency_p95_ms > 200 and n > 50_000:
            return "hnsw"
        for backend, (lo, hi) in self.TIER_THRESHOLDS.items():
            if lo <= n < hi:
                return backend
        return "hnsw"
    
    def should_reindex(self, current: str, recommended: str) -> bool:
        tier = {"cosine": 0, "faiss_flat": 1, "hnsw": 2}
        return tier.get(recommended, 0) > tier.get(current, 0)
```

---

## 5. GRAPH EXPLOSION CONTROL

### 5.1 Dados Reais

```
belief_graph.build_from_entries(500 entries): 16.3s
  → cosine_similarity(500×500): O(n²·dim) NumPy = 500²×384 = 96M ops ✓
  → np.triu_indices: 124.750 pares verificados

Análise: a matriz cosine é inevitável O(n²)
  O gargalo não é o loop Python (já vetorizado)
  O gargalo é a própria computação cosine para n grande
```

### 5.2 Solução: Sparse Graph com Probabilistic Edge Creation

```python
def build_from_entries_sparse(entries, threshold=0.65, max_edges_total=None):
    """
    Para n > 1000: usa amostragem aleatória de pares.
    
    Ao invés de verificar todos os n(n-1)/2 pares:
      - Seleciona sqrt(n) × log(n) pares aleatórios
      - Garante cobertura estatística sem O(n²)
    
    Para n=500:   O(n²) = 125k pares   → aceita ável (16s)
    Para n=5000:  O(n²) = 12.5M pares  → usa sparse (5.000 pares amostrados)
    Para n=50000: O(n²) = 1.25B pares  → usa LSH-based sampling
    """
    n = len(entries)
    
    if n <= 1000:
        return build_from_entries(entries, threshold)  # O(n²) aceita ável
    
    # Sparse mode: amostragem inteligente
    import math
    sample_size = min(int(math.sqrt(n) * math.log2(n) * 10), n * (n-1) // 2)
    
    # LSH para candidatos similares (evita verificar pares obviamente diferentes)
    # Alternativa simples: random pairs + centroid-guided pairs
    ...
```

### 5.3 Edge TTL e Decay Batching

```python
# Ao invés de apply_decay() O(n²) nas arestas:
# Mantém timestamp de expiração pré-calculado por aresta
# apply_decay() vira O(k) onde k = arestas expiradas no período
```

---

## 6. COGNITIVE RUNTIME SCHEDULER — Bounded Queues

### 6.1 Problema Atual

O `CognitiveScheduler` avalia **todos** os episodes a cada tick:
- `_review()`: O(n²) duplo loop sobre accessed entries
- `_consolidate()`: O(n²) clustering
- Executado de forma síncrona

### 6.2 Bounded Queue Architecture

```python
class CognitiveRuntimeKernel:
    """
    Runtime kernel com fila limitada e cooperação entre tarefas.
    
    Inspirado em cooperative scheduling:
    - Cada task tem um budget de tempo
    - Tarefas que excedem o budget são interrompidas e re-enfileiradas
    - Prioridade dinâmica baseada em pressão cognitiva
    """
    
    MAX_QUEUE_SIZE = 200     # nunca cresce infinitamente
    TASK_BUDGET_MS = 10.0    # max 10ms por task por tick
    
    def __init__(self):
        self._queue:  queue.PriorityQueue = queue.PriorityQueue(maxsize=self.MAX_QUEUE_SIZE)
        self._worker: threading.Thread   = threading.Thread(target=self._loop, daemon=True)
    
    def schedule(self, priority: int, fn: Callable, args: tuple = ()) -> bool:
        """
        Retorna False se fila cheia (backpressure).
        Nunca bloqueia o caller.
        """
        try:
            self._queue.put_nowait((priority, time.monotonic(), fn, args))
            return True
        except queue.Full:
            return False  # shed load
    
    def _loop(self):
        while True:
            priority, ts, fn, args = self._queue.get(timeout=0.5)
            t0 = time.perf_counter()
            try:
                fn(*args)
            except Exception:
                pass
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if elapsed_ms > self.TASK_BUDGET_MS:
                # Task muito lenta → registra e reduz prioridade futura
                pass
```

---

## 7. OBSERVABILIDADE COGNITIVA — Telemetria Runtime

### 7.1 Métricas Obrigatórias (Dados Reais do Benchmark)

Com base nos dados medidos, estas são as métricas críticas a monitorar:

```python
class CognitiveTelemetry:
    """
    Coleta métricas em tempo real sem overhead significativo.
    Todas as operações são O(1) amortizado.
    """
    
    # Métricas críticas (baseadas no benchmark real)
    episodic_add_latency_ms: EMAMetric     # alvo: <2ms (era 418ms)
    retrieval_latency_p95_ms: HistoMetric  # alvo: <10ms para n<10k
    graph_build_latency_ms: EMAMetric      # alerta: >1s para n<500
    pipeline_throughput_ops_s: EMAMetric   # alvo: >10/s
    memory_entries_count: GaugeMetric      # alerta: >5000 entries
    cache_hit_rate: RatioMetric            # alvo: >0.80
    wal_pending_writes: GaugeMetric        # alerta: >1000 pending
    pressure_ema: EMAMetric                # alerta: >0.70
    storm_score: EMAMetric                 # alerta: >0.65
    
    # Diagnósticos de gargalo
    embedding_cold_start_count: Counter    # quantas vezes modelo foi carregado
    save_calls_per_add: RatioMetric        # deve ser <0.02 (1/batch_size)
```

### 7.2 Pressure Heatmap

```
Pressão cognitiva por dimensão (dados reais):
  EVICTION:      0.00 ████░░░░░░ 40%
  CONSOLIDATION: 0.00 ████░░░░░░ 40%
  ENTROPY:       0.00 ██░░░░░░░░ 20%
  RETRIEVAL:     0.00 █░░░░░░░░░ 10%
  EMBEDDING:     0.00 ░░░░░░░░░░  0%
  GRAPH:         0.00 ░░░░░░░░░░  0%
  
  COMPOSITE:     0.00 ░░░░░░░░░░  0% → NORMAL
```

---

## 8. PREVISÃO DE PROBLEMAS FUTUROS

### 8.1 Explosão de Embeddings (n > 100k)

```
Probabilidade: ALTA (inevitável em uso real)
Sintoma:       retrieval latência >100ms
Threshold:     n ≈ 37.000 (cosine, dim=384, alvo <10ms)
               Medido: 2.7μs/entry → 10ms/0.0000027 = 37k

Prevenção:
  1. AdaptiveIndexManager (switch automático para faiss_flat @ n>5k)
  2. HNSW quando faiss_flat supera 50ms p95
  3. embedding_cache.py já implementado — aumentar CACHE_MAX
```

### 8.2 Fragmentação de Memória JSON (n > 5k entries)

```
Probabilidade: ALTA
Sintoma:       save() cada vez mais lento, GC pressure
Threshold:     ~5.000 entries × 1.5KB/entry = 7.5MB por save
               Com embedding list: ~6.144 bytes/entry → 30MB para n=5k

Prevenção:
  1. WAL binário (Fase 2) elimina o problema
  2. Batch flush já aplicado reduz frequência 50×
  3. Serializar embeddings como bytes (não lista de floats)
     → 384 × 4 bytes = 1536 bytes vs ~5000 chars JSON
     → 3.3× compressão de embedding storage
```

### 8.3 Graph Saturation (n > 2k entries, threshold=0.65)

```
Probabilidade: MÉDIA
Sintoma:       belief_graph.build_from_entries >30s
Threshold:     n=2000 → 2M pares → ~65s (extrapolado do benchmark)

Prevenção:
  1. threshold mais alto (0.80) reduz arestas em ~3×
  2. build_from_entries_sparse para n>1k
  3. Edge TTL automático (apply_decay periódico)
  4. max_edges=15 por nó já limita explosão local
```

### 8.4 Retrieval Storm sob Carga Concorrente

```
Probabilidade: MÉDIA
Sintoma:       StormDetected frequente, effective_k → 1
Threshold:     >10 queries/s com similaridade >0.88

Prevenção:
  1. storm_guard já implementado
  2. Aumentar window_size para 100 (mais inércia antes do storm)
  3. cache.get_retrieval() para queries repetidas (já implementado)
```

### 8.5 Oscillação Homeostática (NORMAL→EMERGENCY→NORMAL loop)

```
Probabilidade: BAIXA (anti-oscillation implementado)
Sintoma:       modo_changes > 50 em 200 ticks (benchmark: ~20 mudanças)
Threshold:     min_ticks_per_mode=3 (muito baixo para sistemas ruidosos)

Prevenção:
  1. Aumentar min_ticks_per_mode para 10
  2. Hysteresis=0.10 já implementado
  3. Pressure EMA alpha=0.18 já suaviza
  4. Monitorar mode_changes via telemetria
```

### 8.6 Cold Start do Modelo de Embedding

```
Dado real: 33s primeira execução → 64ms depois (cache do modelo)
Probabilidade: CERTA em cada restart de processo
Sintoma:       primeira query sempre lenta

Prevenção:
  1. Pré-carregar modelo no startup: get_model() no __init__ do server
  2. HF_TOKEN para download autenticado (evita rate limits)
  3. Modelo em disco local (EDP_EMBED_MODEL=local/path)
```

---

## 9. DÍVIDA TÉCNICA — PRIORIZAÇÃO

| Dívida | Impacto | Esforço | Prioridade |
|---|---|---|---|
| EpisodicMemory O(n²) I/O | 🔴 CRÍTICO | Baixo (fix já aplicado) | **IMEDIATO** |
| WAL binário persistência | 🔴 CRÍTICO | Médio | **Sprint 1** |
| belief_graph sparse mode | 🟡 ALTO | Médio | **Sprint 2** |
| AdaptiveIndexManager | 🟡 ALTO | Médio | **Sprint 2** |
| consolidation MiniBatch | 🟡 ALTO | Alto | **Sprint 3** |
| CognitiveTelemetry | 🟡 ALTO | Baixo | **Sprint 2** |
| RAM metrics (psutil) | 🟠 MÉDIO | Muito baixo | **Sprint 1** |
| Multi-agent isolation | 🟠 MÉDIO | Alto | **Sprint 4** |
| HNSW backend | 🟠 MÉDIO | Alto | **Sprint 3** |
| Delta snapshots | 🟢 BAIXO | Alto | **Sprint 4** |

---

## 10. ROADMAP TÉCNICO

```
v3.2 (atual):    77/84 → 84/84 (7 fixes aplicados)
                 EpisodicMemory.add: 83s → ~2ms (batch_size)

v3.3 (Sprint 1): WAL binário básico (append-only + SQLite index)
                 psutil para RAM metrics
                 CognitiveTelemetry MVP
                 EpisodicMemory: 2ms → 0.5ms (WAL amortizado O(1))

v3.4 (Sprint 2): AdaptiveIndexManager (cosine→faiss_flat automático)
                 belief_graph sparse mode (n>1k)
                 Observability dashboard básico

v4.0 (Sprint 3): Delta snapshots
                 HNSW backend
                 MiniBatchKMeans em consolidation
                 CognitiveRuntimeKernel (bounded queues)

v4.1 (Sprint 4): Multi-agent transactional memory
                 Background compaction WAL
                 GPU embedding (batch inference)
                 Distributed retrieval sharding
```

---

## 11. BENCHMARKS ESPERADOS APÓS OTIMIZAÇÕES

| Operação | Atual (medido) | v3.3 target | v4.0 target |
|---|---|---|---|
| EpisodicMemory.add | 418ms/entry | **0.01ms** (WAL) | 0.001ms |
| EpisodicMemory.retrieve n=200 | 835ms | 100ms | <50ms |
| belief_graph.build n=500 | 16.3s | 2s (sparse) | <500ms |
| consolidation.cluster n=1000 | 801ms | 200ms | <50ms (MiniBatch) |
| pipeline throughput | 7.9/s | 20/s | >50/s |
| VectorStore query n=10k | 7s | 50ms (faiss_flat) | <10ms (HNSW) |
| benchmark total pass | 77/84 | **84/84** | 84/84 |
