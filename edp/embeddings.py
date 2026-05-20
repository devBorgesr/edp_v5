"""
embeddings.py (PATCHED)
[P-F7] deduplicate(): O(n²) cosine individual → matrix pré-computada
       Consistente com retrieval_patched, belief_graph_patched, memory_patched
"""
"""
embeddings.py — Motor de embeddings do EDP v3.
v3: singleton com fallback, cache integrado, retry,
    OOM protection, batch auto-split, embed_one/embed_many.
"""
import time
import numpy as np
from functools import lru_cache
from sklearn.metrics.pairwise import cosine_similarity

from .config import (
    EMBED_MODEL, EMBED_MODEL_FALLBACK,
    EMBED_NORMALIZE, EMBED_BATCH_SIZE, EMBED_DIM,
)
from . import cache as _cache

_MAX_RETRIES    = 3
_RETRY_DELAY    = 1.0   # segundos
_OOM_BATCH_MIN  = 8     # batch mínimo antes de desistir

# ── Singleton ─────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_model():
    """Carrega o modelo principal (lazy, singleton)."""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL)

@lru_cache(maxsize=1)
def get_fallback_model():
    """Carrega modelo de fallback (lazy, singleton)."""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL_FALLBACK)

# ── Encode interno ────────────────────────────────────────────────────────────

def _encode_with_retry(
    texts: list[str],
    batch_size: int,
    use_fallback: bool = False,
) -> np.ndarray:
    """
    Encoda textos com retry e OOM protection.
    Divide o batch pela metade em caso de MemoryError.
    """
    model = get_fallback_model() if use_fallback else get_model()

    for attempt in range(_MAX_RETRIES):
        try:
            vecs = model.encode(
                texts,
                batch_size=batch_size,
                convert_to_numpy=True,
                normalize_embeddings=EMBED_NORMALIZE,
                show_progress_bar=False,
            )
            return vecs.astype(np.float32)

        except (MemoryError, RuntimeError) as e:
            if "out of memory" in str(e).lower() or isinstance(e, MemoryError):
                batch_size = max(batch_size // 2, _OOM_BATCH_MIN)
                if batch_size < _OOM_BATCH_MIN:
                    raise RuntimeError("OOM: batch mínimo atingido") from e
                continue
            raise

        except Exception:
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAY)
            else:
                if not use_fallback:
                    # tenta fallback antes de desistir
                    return _encode_with_retry(texts, batch_size, use_fallback=True)
                raise

    raise RuntimeError("Falha ao encodar após todas as tentativas")

# ── API pública ───────────────────────────────────────────────────────────────

def embed(texts: list[str]) -> np.ndarray:
    """
    Encoda lista de textos com cache integrado.
    Retorna matriz (N, EMBED_DIM) float32 normalizada.
    """
    if not texts:
        return np.empty((0, EMBED_DIM), dtype=np.float32)

    cached, missing_idx = _cache.get_batch(texts)

    if missing_idx:
        missing_texts = [texts[i] for i in missing_idx]
        new_vecs      = _encode_with_retry(missing_texts, EMBED_BATCH_SIZE)
        _cache.put_batch(missing_texts, list(new_vecs))
        for local_i, global_i in enumerate(missing_idx):
            cached[global_i] = new_vecs[local_i]

    # monta matriz final
    result = np.vstack(cached).astype(np.float32)

    # renormaliza (cache pode ter perdido precisão)
    if EMBED_NORMALIZE:
        norms  = np.linalg.norm(result, axis=1, keepdims=True)
        result = result / np.where(norms == 0, 1.0, norms)

    return result

def embed_one(text: str) -> np.ndarray:
    """Encoda um único texto. Retorna vetor (EMBED_DIM,)."""
    return embed([text])[0]

def embed_many(texts: list[str], batch_size: int | None = None) -> np.ndarray:
    """
    Encoda lista com controle de batch_size explícito.
    Útil para grandes volumes onde se quer controle fino.
    """
    if not texts:
        return np.empty((0, EMBED_DIM), dtype=np.float32)
    bs = batch_size or EMBED_BATCH_SIZE
    results = []
    for i in range(0, len(texts), bs):
        chunk = texts[i:i+bs]
        results.append(embed(chunk))
    return np.vstack(results)

# ── Utilitários ───────────────────────────────────────────────────────────────

def similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return cosine_similarity(a, b)

def deduplicate(
    items: list[str],
    threshold: float,
    embeddings: np.ndarray | None = None,
) -> list[str]:
    """
    Remove near-duplicatas. Aceita embeddings pré-computados.
    [P-F7] FIX O(n²): pré-computa similarity matrix completa (consistente
    com retrieval_patched.py e belief_graph_patched.py).
    """
    if not items:
        return []
    emb = embeddings if embeddings is not None else embed(items)
    # Pré-computa matrix completa — 1 chamada sklearn vs n×k individuais
    sim_matrix = cosine_similarity(emb, emb)  # (n, n)
    keep: list[int] = []
    for i in range(len(items)):
        if not any(float(sim_matrix[i, j]) > threshold for j in keep):
            keep.append(i)
    return [items[i] for i in keep]

