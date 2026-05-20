"""
config.py — Configuração centralizada do EDP v3.
Todos os parâmetros sobrescrevíveis via variáveis de ambiente.
"""
import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(os.environ.get("EDP_BASE_DIR",   "/content/edp_v3_memory"))
CACHE_DB    = BASE_DIR / "embed_cache.sqlite"
MEMORY_DIR  = BASE_DIR / "sessions"
METRICS_LOG = BASE_DIR / "metrics.jsonl"

# ── Pipeline ───────────────────────────────────────────────────────────────────
CHUNK_SIZE   = int(os.environ.get("EDP_CHUNK_SIZE",   "40"))
HIGH_SCORE   = float(os.environ.get("EDP_HIGH_SCORE", "0.65"))
MID_SCORE    = float(os.environ.get("EDP_MID_SCORE",  "0.40"))
MIN_WORDS    = int(os.environ.get("EDP_MIN_WORDS",    "5"))
DEDUP_THRESH = float(os.environ.get("EDP_DEDUP",      "0.75"))

# ── Embeddings ─────────────────────────────────────────────────────────────────
EMBED_MODEL          = os.environ.get("EDP_EMBED_MODEL",    "sentence-transformers/all-MiniLM-L6-v2")
EMBED_MODEL_FALLBACK = os.environ.get("EDP_EMBED_FALLBACK", "sentence-transformers/paraphrase-MiniLM-L3-v2")
EMBED_DIM            = int(os.environ.get("EDP_EMBED_DIM",   "384"))
EMBED_NORMALIZE      = True
EMBED_BATCH_SIZE     = int(os.environ.get("EDP_BATCH_SIZE",  "64"))
EMBED_MODEL_VERSION  = "minilm-l6-v2-v3"

# ── Cache ──────────────────────────────────────────────────────────────────────
CACHE_MAX = int(os.environ.get("EDP_CACHE_MAX", "100000"))

# ── Retrieval ──────────────────────────────────────────────────────────────────
RETRIEVAL_BACKEND = os.environ.get("EDP_RETRIEVAL_BACKEND", "faiss_flat")  # cosine|faiss_flat|faiss_ivf|hnsw
RETRIEVAL_TOP_K   = int(os.environ.get("EDP_TOP_K",          "10"))
RETRIEVAL_MIN_SIM = float(os.environ.get("EDP_MIN_SIM",      "0.20"))
ANN_NPROBE        = int(os.environ.get("EDP_NPROBE",         "8"))
HNSW_EF_SEARCH    = int(os.environ.get("EDP_HNSW_EF",        "50"))
HNSW_M            = int(os.environ.get("EDP_HNSW_M",         "16"))

# ── Memória ────────────────────────────────────────────────────────────────────
DECAY_LAMBDA      = float(os.environ.get("EDP_DECAY_LAMBDA",  "0.1"))
MAX_MEMORY        = int(os.environ.get("EDP_MAX_MEMORY",       "500"))
WORKING_MEM_SIZE  = int(os.environ.get("EDP_WORKING_SIZE",    "20"))
EPISODIC_MEM_SIZE = int(os.environ.get("EDP_EPISODIC_SIZE",   "200"))
PRIORIDADE_PESO   = {"alta": 1.3, "media": 1.0, "baixa": 0.7}

# ── Consolidação ───────────────────────────────────────────────────────────────
CONSOLIDATION_CLUSTER_MIN = int(os.environ.get("EDP_CLUSTER_MIN",    "2"))
CONSOLIDATION_SIM_THRESH  = float(os.environ.get("EDP_CLUSTER_THRESH","0.80"))

# ── Scoring ────────────────────────────────────────────────────────────────────
SCORE_WEIGHTS = {
    "entropy":    float(os.environ.get("EDP_W_ENTROPY",    "0.20")),
    "diversity":  float(os.environ.get("EDP_W_DIVERSITY",  "0.15")),
    "relevance":  float(os.environ.get("EDP_W_RELEVANCE",  "0.25")),
    "novelty":    float(os.environ.get("EDP_W_NOVELTY",    "0.15")),
    "decay":      float(os.environ.get("EDP_W_DECAY",      "0.15")),
    "confidence": float(os.environ.get("EDP_W_CONFIDENCE", "0.10")),
}

# ── Temporal ───────────────────────────────────────────────────────────────────
TEMPORAL_DECAY_TYPE   = os.environ.get("EDP_DECAY_TYPE", "exponential")  # exponential|gaussian
TEMPORAL_GAUSSIAN_STD = float(os.environ.get("EDP_GAUSS_STD", "7.0"))    # dias

# ── Compression ────────────────────────────────────────────────────────────────
COMPRESSION_MAX_RATIO = float(os.environ.get("EDP_COMPRESS_RATIO", "0.5"))

# ── API ────────────────────────────────────────────────────────────────────────
API_HOST    = os.environ.get("EDP_API_HOST", "0.0.0.0")
API_PORT    = int(os.environ.get("EDP_API_PORT", "8000"))
API_VERSION = "v3"

