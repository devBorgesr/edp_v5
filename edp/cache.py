"""
cache.py (PATCHED)
[P-F2] invalidate_retrieval: usa _RETRIEVAL_SESSION_INDEX — nunca mais vaza
[P-F3] get/put_retrieval e get/put_context: threading.Lock adicionado
"""
"""
cache.py — Persistência de embeddings via SQLite.
v3: WAL mode, LRU eviction, batch get/set, hit rate,
    model versioning, dimension validation, thread-safe.
"""
import sqlite3
import hashlib
import time
import threading
import numpy as np

from .config import CACHE_DB, CACHE_MAX, EMBED_MODEL_VERSION, EMBED_DIM

_lock = threading.Lock()

_CREATE = """
CREATE TABLE IF NOT EXISTS embed_cache (
    hash        TEXT PRIMARY KEY,
    text        TEXT NOT NULL,
    model_ver   TEXT NOT NULL,
    vector      BLOB NOT NULL,
    dim         INTEGER NOT NULL,
    created_at  REAL NOT NULL,
    hits        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_model   ON embed_cache(model_ver);
CREATE INDEX IF NOT EXISTS idx_hits    ON embed_cache(hits);
CREATE INDEX IF NOT EXISTS idx_created ON embed_cache(created_at);
"""

def _conn() -> sqlite3.Connection:
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(CACHE_DB), check_same_thread=False, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA cache_size=-32000")  # 32 MB page cache
    c.executescript(_CREATE)
    return c

def _hash(text: str) -> str:
    return hashlib.sha256(f"{EMBED_MODEL_VERSION}::{text}".encode()).hexdigest()

# ── Single ops ────────────────────────────────────────────────────────────────

def get(text: str) -> np.ndarray | None:
    h = _hash(text)
    with _lock:
        with _conn() as c:
            row = c.execute(
                "SELECT vector, dim FROM embed_cache WHERE hash=?", (h,)
            ).fetchone()
            if row:
                vec, dim = row
                if dim != EMBED_DIM:
                    return None  # stale — dimensão errada
                c.execute("UPDATE embed_cache SET hits=hits+1 WHERE hash=?", (h,))
                return np.frombuffer(vec, dtype=np.float32).copy()
    return None

def put(text: str, vector: np.ndarray) -> None:
    h   = _hash(text)
    vec = vector.astype(np.float32)
    if vec.shape[0] != EMBED_DIM:
        raise ValueError(f"Dimensão inválida: {vec.shape[0]} != {EMBED_DIM}")
    with _lock:
        with _conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO embed_cache
                   (hash, text, model_ver, vector, dim, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (h, text[:500], EMBED_MODEL_VERSION,
                 vec.tobytes(), EMBED_DIM, time.time())
            )

# ── Batch ops ─────────────────────────────────────────────────────────────────

def get_batch(texts: list[str]) -> tuple[list[np.ndarray | None], list[int]]:
    """
    Recupera lote do cache.
    Retorna (lista_de_vetores_ou_None, indices_ausentes).
    """
    if not texts:
        return [], []

    hashes  = [_hash(t) for t in texts]
    h2idx   = {h: i for i, h in enumerate(hashes)}
    results = [None] * len(texts)
    missing = list(range(len(texts)))
    hit_hashes: list[str] = []

    with _lock:
        with _conn() as c:
            placeholders = ",".join("?" * len(hashes))
            rows = c.execute(
                f"SELECT hash, vector, dim FROM embed_cache "
                f"WHERE hash IN ({placeholders})",
                hashes
            ).fetchall()

            for h, blob, dim in rows:
                if dim != EMBED_DIM:
                    continue  # stale vector — skip
                idx = h2idx[h]
                results[idx] = np.frombuffer(blob, dtype=np.float32).copy()
                if idx in missing:
                    missing.remove(idx)
                hit_hashes.append(h)

            if hit_hashes:
                c.executemany(
                    "UPDATE embed_cache SET hits=hits+1 WHERE hash=?",
                    [(h,) for h in hit_hashes]
                )

    return results, missing

def put_batch(texts: list[str], vectors: list[np.ndarray]) -> None:
    rows = []
    for t, v in zip(texts, vectors):
        vec = v.astype(np.float32)
        if vec.shape[0] != EMBED_DIM:
            continue  # skip dimensão errada silenciosamente
        rows.append((
            _hash(t), t[:500], EMBED_MODEL_VERSION,
            vec.tobytes(), EMBED_DIM, time.time()
        ))
    if not rows:
        return
    with _lock:
        with _conn() as c:
            c.executemany(
                """INSERT OR REPLACE INTO embed_cache
                   (hash, text, model_ver, vector, dim, created_at)
                   VALUES (?,?,?,?,?,?)""",
                rows
            )

# ── Stats & Eviction ──────────────────────────────────────────────────────────

def stats() -> dict:
    with _lock:
        with _conn() as c:
            total    = c.execute("SELECT COUNT(*) FROM embed_cache").fetchone()[0]
            hits_sum = c.execute("SELECT SUM(hits) FROM embed_cache").fetchone()[0] or 0
            by_model = dict(c.execute(
                "SELECT model_ver, COUNT(*) FROM embed_cache GROUP BY model_ver"
            ).fetchall())
            stale = c.execute(
                "SELECT COUNT(*) FROM embed_cache WHERE dim != ?", (EMBED_DIM,)
            ).fetchone()[0]
    return {
        "total":       total,
        "total_hits":  hits_sum,
        "hit_rate":    round(hits_sum / max(total, 1), 4),
        "stale":       stale,
        "by_model":    by_model,
        "max":         CACHE_MAX,
        "model_ver":   EMBED_MODEL_VERSION,
        "embed_dim":   EMBED_DIM,
    }

def evict_lru(keep: int | None = None) -> int:
    """Remove entradas menos usadas até manter `keep` entradas. Retorna total antes."""
    keep = keep or CACHE_MAX
    with _lock:
        with _conn() as c:
            total = c.execute("SELECT COUNT(*) FROM embed_cache").fetchone()[0]
            if total > keep:
                c.execute(
                    """DELETE FROM embed_cache WHERE hash IN (
                       SELECT hash FROM embed_cache
                       ORDER BY hits ASC, created_at ASC LIMIT ?)""",
                    (total - keep,)
                )
    return total

def purge_stale() -> int:
    """Remove vetores com dimensão diferente da atual."""
    with _lock:
        with _conn() as c:
            n = c.execute(
                "SELECT COUNT(*) FROM embed_cache WHERE dim != ?", (EMBED_DIM,)
            ).fetchone()[0]
            c.execute("DELETE FROM embed_cache WHERE dim != ?", (EMBED_DIM,))
    return n

# ── Retrieval cache & context cache (upgrade) ─────────────────────────────────
import hashlib as _hl
from collections import OrderedDict as _OD

_retrieval_cache: "_OD" = _OD()
_RETRIEVAL_CACHE_MAX = 200
_context_cache: "_OD" = _OD()
_CONTEXT_CACHE_MAX = 50

# [P-F2] Lock para _retrieval_cache e _context_cache (eram sem lock)
_cache_lock = threading.Lock()
# [P-F2] Índice session_id → set[key] para invalidate_retrieval
_RETRIEVAL_SESSION_INDEX: "dict[str, set]" = {}


def get_retrieval(query: str, session_id: str):
    """[P-F3] Adicionado _cache_lock."""
    key = _hl.md5(f"{session_id}::{query}".encode()).hexdigest()
    with _cache_lock:
        if key in _retrieval_cache:
            _retrieval_cache.move_to_end(key)
            return _retrieval_cache[key]
    return None

def put_retrieval(query: str, session_id: str, result: list) -> None:
    """[P-F2][P-F3] Adicionado lock + registro em _RETRIEVAL_SESSION_INDEX."""
    key = _hl.md5(f"{session_id}::{query}".encode()).hexdigest()
    with _cache_lock:
        _retrieval_cache[key] = result
        _retrieval_cache.move_to_end(key)
        _RETRIEVAL_SESSION_INDEX.setdefault(session_id, set()).add(key)
        if len(_retrieval_cache) > _RETRIEVAL_CACHE_MAX:
            oldest_key, _ = next(iter(_retrieval_cache.items()))
            _retrieval_cache.pop(oldest_key)

def invalidate_retrieval(session_id: str) -> int:
    """
    [P-F2] FIX: a key é md5(session_id::query), não contém md5(session_id::)[:8].
    Corrigido para usar _RETRIEVAL_SESSION_INDEX que mapeia session_id → set[key].
    """
    with _cache_lock:
        keys_to_del = list(_RETRIEVAL_SESSION_INDEX.get(session_id, set()))
        for k in keys_to_del:
            _retrieval_cache.pop(k, None)
        _RETRIEVAL_SESSION_INDEX.pop(session_id, None)
    return len(keys_to_del)

def get_context(query: str, session_id: str, pipeline_hash: str = ""):
    """[P-F3] Adicionado _cache_lock."""
    key = _hl.md5(f"{session_id}::{query}::{pipeline_hash}".encode()).hexdigest()
    with _cache_lock:
        if key in _context_cache:
            _context_cache.move_to_end(key)
            return _context_cache[key]
    return None

def put_context(query: str, session_id: str, result: dict, pipeline_hash: str = "") -> None:
    """[P-F3] Adicionado _cache_lock."""
    key = _hl.md5(f"{session_id}::{query}::{pipeline_hash}".encode()).hexdigest()
    with _cache_lock:
        _context_cache[key] = result
        _context_cache.move_to_end(key)
        if len(_context_cache) > _CONTEXT_CACHE_MAX:
            _context_cache.popitem(last=False)

def cache_sizes() -> dict:
    return {"retrieval_cache": len(_retrieval_cache), "context_cache": len(_context_cache),
            "retrieval_max": _RETRIEVAL_CACHE_MAX, "context_max": _CONTEXT_CACHE_MAX}

