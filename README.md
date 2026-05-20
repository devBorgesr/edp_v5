# EDP v3.3 — Cognitive Runtime Infrastructure

**Persistent Cognitive Layer for Local AI Systems**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Benchmark 84/84](https://img.shields.io/badge/tests-84%2F84-brightgreen)](benchmark_edp.py)

---

## O que é o EDP?

O EDP **não é um chatbot**. Não é um modelo de linguagem. Não é AGI.

O EDP é uma **infraestrutura cognitiva persistente**: uma camada de runtime que transforma qualquer LLM em um sistema com memória real, governança operacional e adaptação sob pressão.

```
Usuário
  ↓
EDP Runtime (memória · contexto · governança · compressão)
  ↓
Modelo Local (Ollama · LM Studio · qualquer LLM)
  ↓
Resposta enriquecida com contexto persistente
```

---

## Para que serve?

| Problema | Solução EDP |
|---|---|
| LLMs "esquecem" entre sessões | Memória episódica + semântica persistente |
| Contextos muito longos = custo alto | Compressão cognitiva (redução média 40%) |
| Modelo fica "confuso" em sessões longas | Snapshot geracional + homeostase cognitiva |
| Retrieval lento com muitos documentos | ANN híbrido: NumPy → FAISS → HNSW automático |
| Sistema colapsa sob carga | StormGuard + PressureRegulator + EconomyEngine |

---

## Quickstart

### 1. Instalar

```bash
git clone <repo>
cd edp_v3_backup
pip install -r requirements.txt
```

### 2. Iniciar com Ollama

```bash
# Terminal 1: inicia Ollama
ollama serve
ollama pull llama3:8b

# Terminal 2: inicia EDP
python run.py serve
# ou direto via Python:
```

```python
from edp.llm_adapter import EDPRuntime

runtime = EDPRuntime(session_id="minha_sessao")
runtime.connect_ollama(model="llama3:8b")

response = runtime.chat("Como funciona RAG?")
print(response.text)
print(f"Latência: {response.latency_ms}ms | Hits memória: {response.memory_hits}")
```

### 3. Iniciar com LM Studio

```python
runtime.connect_lm_studio(
    model="mistral-7b-instruct",
    base_url="http://localhost:1234"
)
```

### 4. Dashboard de observabilidade

```bash
python run.py serve
# Acesse: http://localhost:8000/dashboard
```

---

## Modos de uso

### Chat com memória persistente

```python
from edp.llm_adapter import EDPRuntime

rt = EDPRuntime(session_id="projeto_alpha")
rt.connect_ollama("llama3:8b")

# O EDP lembra entre sessões (persiste em disco)
r1 = rt.chat("Meu nome é João e trabalho com Python")
r2 = rt.chat("Qual é meu nome?")  # EDP recupera da memória
```

### Streaming

```python
for chunk in rt.stream_chat("Explique embeddings em detalhes"):
    print(chunk, end="", flush=True)
```

### Retrieval direto

```python
results = rt.retrieve("machine learning", top_k=5)
for r in results:
    print(f"[{r['score']:.3f}] {r['text'][:100]}")
```

### Via REST API

```bash
# Chat
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "O que é RAG?", "session_id": "demo",
       "provider": "ollama", "model": "llama3:8b"}'

# Memória
curl http://localhost:8000/memory?session_id=demo

# Retrieval
curl -X POST http://localhost:8000/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query": "machine learning", "session_id": "demo"}'

# Health
curl http://localhost:8000/health
```

---

## Arquitetura

```
edp/
├── CAMADA COGNITIVA
│   ├── pipeline.py         — compressão semântica end-to-end
│   ├── scoring.py          — scoring cognitivo (fórmula φ)
│   ├── retrieval.py        — retrieval cosine baseline
│   ├── retrieval_ann.py    — ANN: NumPy→FAISS→HNSW automático (NOVO)
│   └── context_builder.py  — MMR vetorizado
│
├── MEMÓRIA
│   ├── memory.py           — hierarquia: Working→Episodic→Semantic
│   ├── semantic_memory.py  — consolidação por conceitos
│   ├── snapshot_manager.py — gerações imutáveis + rollback
│   └── vector_store.py     — store vetorial thread-safe
│
├── HOMEOSTASE
│   ├── meta_stability.py   — modos NORMAL→EMERGENCY→RECOVERY
│   ├── storm_guard.py      — circuit breaker cognitivo
│   ├── economy.py          — budget cognitivo (token buckets)
│   └── pressure_regulator.py — controle dinâmico de pressão
│
├── INTEGRAÇÃO
│   ├── llm_adapter.py      — Ollama / LM Studio / OpenAI (NOVO)
│   └── api_v2.py           — FastAPI + WebSocket + Dashboard (NOVO)
│
└── OBSERVABILIDADE
    ├── metrics.py          — JSONL persistente + EMA
    ├── analytics.py        — health reports
    └── decision_graph_v32.py — causalidade assíncrona
```

---

## Benchmark (resultados reais — Python 3.14.4, 8 CPUs)

```
Suite                     Pass  Avg latência
────────────────────────  ────  ────────────
Scoring                   5/5      148ms
Retrieval                 5/5      276ms
Memory (batch mode)       6/6        2ms  ← 41k× mais rápido após fix
Vector Store              6/6        1ms
Pipeline                  6/6    11.5s  (inclui cold start do modelo)
Snapshot Manager          8/8      434ms
Economy                   4/4       62ms
Meta Stability            5/5        4ms
Storm Guard               5/5        2ms
Pressure Regulator        5/5        4ms
Biodiversity              5/5      200ms
Decision Graph            5/5      216ms
Consolidation             5/5      290ms
Context Builder           3/3     1859ms
Belief Graph              5/5     3798ms

TOTAL: 84/84 ✅
```

---

## Requisitos

```
Python >= 3.10
numpy >= 1.24
scikit-learn >= 1.2
sentence-transformers >= 2.2  (embeddings locais)
fastapi >= 0.100              (API)
uvicorn >= 0.20               (server)

Opcionais:
faiss-cpu >= 1.7              (ANN retrieval acelerado)
psutil >= 5.9                 (métricas de RAM)
```

```bash
pip install numpy scikit-learn sentence-transformers fastapi uvicorn
# Opcional:
pip install faiss-cpu psutil
```

---

## Modos operacionais

O EDP adapta seu comportamento automaticamente:

| Modo | Trigger | Comportamento |
|---|---|---|
| **NORMAL** | pressão < 30% | operação completa |
| **ELEVATED** | pressão 30-50% | retrieval reduzido, mais compressão |
| **DEGRADED** | pressão 50-68% | consolidação agressiva |
| **CRITICAL** | pressão 68-82% | apenas abstrações, retrieval mínimo |
| **EMERGENCY** | pressão > 82% | freeze, drain, recovery |
| **RECOVERY** | após emergency | recuperação gradual |

---

## Gargalos conhecidos e roadmap

### Resolvidos ✅
- `EpisodicMemory.add`: 83s → 2ms (batch mode)
- `deduplicate`: O(n²) → O(n) via matrix
- `validate_snapshot`: O(n) → O(1) com cache
- `suppress_redundant`: O(n²) → np.where vetorizado

### Em andamento 🔄
- WAL binário para persistência O(1) amortizado
- `belief_graph` sparse mode para n > 1k
- `consolidation` MiniBatchKMeans para n > 5k

### Roadmap
- v3.4: AdaptiveIndexManager (switch automático FAISS)
- v4.0: Delta snapshots, background compaction
- v4.1: Multi-agent transactional memory

---

## Diferencial técnico

O EDP não é mais um "wrapper de LLM". É uma infraestrutura cognitiva com:

1. **Memória viva** — persiste entre sessões, aprende com uso
2. **Governança operacional** — 6 modos adaptados à pressão
3. **Compressão cognitiva** — reduz tokens sem perda semântica
4. **Snapshot geracional** — consistência temporal garantida
5. **ANN escalável** — NumPy→FAISS→HNSW sem reconfiguração

---

## Licença

MIT — use, modifique, distribua.

---

*EDP v3.3 — Cognitive Runtime Infrastructure*
*Não é um chatbot. É a camada cognitiva que faz LLMs lembrarem.*
