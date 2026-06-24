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
import logging
import os as _os
import traceback as _tb
import numpy as np
from functools import lru_cache
from sklearn.metrics.pairwise import cosine_similarity

from .config import (
    EMBED_MODEL, EMBED_MODEL_FALLBACK,
    EMBED_NORMALIZE, EMBED_BATCH_SIZE, EMBED_DIM,
)
from . import cache as _cache

logger = logging.getLogger("edp.embeddings")


def _diag_load(kind: str, model_name: str) -> None:
    """
    Dívida #42 (10/06/2026) — diagnóstico de instanciação dupla.
    Loga pid + nome do módulo + quem chamou. Se em produção aparecerem
    DOIS loads do mesmo kind: __name__ diferente = módulo duplicado
    (import sob dois nomes); pid diferente = dois processos.
    Remover após Dívida #42 resolvida.
    """
    try:
        caller = " <- ".join(
            f"{f.name}@{_os.path.basename(f.filename)}:{f.lineno}"
            for f in _tb.extract_stack(limit=6)[:-2][-3:]
        )
        logger.warning(
            "[embed][diag#42] CARREGANDO %s model=%s | pid=%s module=%s | caller: %s",
            kind, model_name, _os.getpid(), __name__, caller,
        )
    except Exception:
        pass

_MAX_RETRIES    = 3
_RETRY_DELAY    = 1.0   # segundos
_OOM_BATCH_MIN  = 8     # batch mínimo antes de desistir

# ── Singleton ─────────────────────────────────────────────────────────────────

import threading as _threading

# ── Singletons com lock explícito ─────────────────────────────────────────────
# Dívida #42 (10/06/2026) — FIX: lru_cache NÃO serializa a execução da
# função, só o dict. Duas threads com cache frio executavam o corpo em
# paralelo → DUAS instâncias do modelo (provado pelo diag#42 em produção:
# mesmo pid, mesmo module, 7ms de diferença, callers distintos).
# Double-checked locking garante carga única. Padrão idêntico ao
# usado em todos os singletons de edp/runtime/.

_model = None
_fallback_model = None
_model_lock = _threading.Lock()


def get_model():
    """Carrega o modelo principal (lazy, singleton thread-safe)."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                _diag_load("PRINCIPAL", EMBED_MODEL)
                _model = SentenceTransformer(EMBED_MODEL)
    return _model


def get_fallback_model():
    """Carrega modelo de fallback (lazy, singleton thread-safe)."""
    global _fallback_model
    if _fallback_model is None:
        with _model_lock:
            if _fallback_model is None:
                from sentence_transformers import SentenceTransformer
                _diag_load("FALLBACK", EMBED_MODEL_FALLBACK)
                _fallback_model = SentenceTransformer(EMBED_MODEL_FALLBACK)
    return _fallback_model


def warmup_model() -> bool:
    """
    Dívida #42 (10/06/2026): warmup REAL do modelo no startup.

    O warmup antigo (embed_one("warmup")) dava cache HIT no embedding
    cache em disco e retornava SEM carregar o modelo — o log
    "pré-carregado" era falso. Este chama get_model() diretamente e
    encoda 1 texto SEM passar pelo cache, garantindo modelo em RAM e
    caminhos compilados antes do primeiro turno real.
    """
    try:
        model = get_model()
        model.encode(
            ["__edp_warmup__"],
            batch_size=1,
            convert_to_numpy=True,
            normalize_embeddings=EMBED_NORMALIZE,
            show_progress_bar=False,
        )
        return True
    except Exception as e:
        logger.warning("[embed] warmup_model falhou: %s", e)
        return False

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
    return_indices: bool = False,
) -> list[str] | tuple[list[str], list[int]]:
    """
    Remove near-duplicatas. Aceita embeddings pré-computados.
    [P-F7] FIX O(n²): pré-computa similarity matrix completa (consistente
    com retrieval_patched.py e belief_graph_patched.py).

    return_indices=True → retorna (items_kept, indices_kept) para que o
    chamador possa fatiar embeddings já computados sem re-embed.
    """
    if not items:
        return ([], []) if return_indices else []
    emb = embeddings if embeddings is not None else embed(items)
    # Pré-computa matrix completa — 1 chamada sklearn vs n×k individuais
    sim_matrix = cosine_similarity(emb, emb)  # (n, n)
    keep: list[int] = []
    for i in range(len(items)):
        if not any(float(sim_matrix[i, j]) > threshold for j in keep):
            keep.append(i)
    kept_items = [items[i] for i in keep]
    return (kept_items, keep) if return_indices else kept_items

