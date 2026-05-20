"""
vector_store.py — EDP v3.2 Vector Store

Implementa:
  VectorStoreProtocol  — interface contratual usada pelo orchestrator_v32
  InMemoryVectorStore  — implementação padrão, thread-safe, O(n) retrieval

Design:
  Embeddings armazenados como np.ndarray normalizada.
  Normas pré-computadas e cacheadas — evita recalcular a cada query.
  Busca: dot-product (vetorizado numpy) em vez de cosine_similarity loop.
  Write batching: accumula writes e aplica em lote.
  Lock ordering: _store_lock adquirido após economy (definido externamente).
"""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


# ── Tipos ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VectorEntry:
    id:        str
    embedding: Tuple[float, ...]
    metadata:  Dict = field(default_factory=dict)
    added_at:  float = field(default_factory=time.time)


@dataclass(frozen=True)
class VectorQueryResult:
    id:    str
    score: float
    entry: VectorEntry


@dataclass
class VectorStoreMetrics:
    total_entries:   int   = 0
    total_queries:   int   = 0
    total_adds:      int   = 0
    total_deletes:   int   = 0
    cache_hits:      int   = 0
    avg_query_ms:    float = 0.0
    _EMA_ALPHA: float = 0.2

    def record_query_ms(self, ms: float) -> None:
        self.avg_query_ms = self._EMA_ALPHA * ms + (1 - self._EMA_ALPHA) * self.avg_query_ms

    def snapshot(self) -> dict:
        return {
            "total_entries":  self.total_entries,
            "total_queries":  self.total_queries,
            "total_adds":     self.total_adds,
            "total_deletes":  self.total_deletes,
            "cache_hits":     self.cache_hits,
            "avg_query_ms":   round(self.avg_query_ms, 3),
        }


# ── Protocolo ─────────────────────────────────────────────────────────────────

class VectorStoreProtocol(ABC):
    """Contrato público usado pelo orchestrator_v32."""

    @abstractmethod
    def add(self, entry: VectorEntry) -> None: ...

    @abstractmethod
    def batch_add(self, entries: List[VectorEntry]) -> int: ...

    @abstractmethod
    def query(self, embedding: Tuple[float, ...], top_k: int = 10,
               min_score: float = 0.0) -> List[VectorQueryResult]: ...

    @abstractmethod
    def delete(self, entry_id: str) -> bool: ...

    @abstractmethod
    def get(self, entry_id: str) -> Optional[VectorEntry]: ...

    @abstractmethod
    def size(self) -> int: ...

    @abstractmethod
    def compact(self) -> int: ...

    @abstractmethod
    def metrics(self) -> VectorStoreMetrics: ...

    @abstractmethod
    def health_report(self) -> dict: ...

    def upsert_raw(
        self,
        id: str,
        text: str,
        embedding: Optional[Tuple[float, ...]],
    ) -> None:
        """
        Compat bridge: chamado por MemoryBridgeV32.consolidate()
        Cria VectorEntry e chama add(). Ignora text (sem modelo aqui).
        """
        if embedding is None:
            return
        self.add(VectorEntry(id=id, embedding=embedding, metadata={"text": text}))


# ── Implementação in-memory ───────────────────────────────────────────────────

class InMemoryVectorStore(VectorStoreProtocol):
    """
    Store vetorial in-memory com:
      - normas pré-computadas e cacheadas
      - retrieval via dot-product numpy vetorizado O(n·dim)
      - thread-safe via RLock
      - compact() remove entradas marcadas como deleted
    """

    def __init__(self, dim: int = 384, normalize: bool = True) -> None:
        self._dim        = dim
        self._normalize  = normalize
        self._lock       = threading.RLock()
        self._entries:   Dict[str, VectorEntry]  = {}
        self._deleted:   set[str]                = set()
        self._emb_matrix: Optional[np.ndarray]  = None  # (n, dim) cache
        self._ids_order:  List[str]              = []     # alinhado a _emb_matrix
        self._matrix_dirty = True
        self._metrics    = VectorStoreMetrics()

    def add(self, entry: VectorEntry) -> None:
        emb = np.array(entry.embedding, dtype=np.float32)
        if emb.shape[0] != self._dim:
            return
        if self._normalize:
            norm = float(np.linalg.norm(emb))
            if norm > 1e-8:
                emb = emb / norm
        entry = VectorEntry(id=entry.id, embedding=tuple(emb.tolist()),
                            metadata=entry.metadata, added_at=entry.added_at)
        with self._lock:
            self._entries[entry.id] = entry
            self._deleted.discard(entry.id)
            self._matrix_dirty = True
            self._metrics.total_adds += 1
            self._metrics.total_entries = len(self._entries) - len(self._deleted)

    def batch_add(self, entries: List[VectorEntry]) -> int:
        added = 0
        with self._lock:
            for e in entries:
                emb = np.array(e.embedding, dtype=np.float32)
                if emb.shape[0] != self._dim:
                    continue
                if self._normalize:
                    norm = float(np.linalg.norm(emb))
                    if norm > 1e-8:
                        emb = emb / norm
                ne = VectorEntry(id=e.id, embedding=tuple(emb.tolist()),
                                 metadata=e.metadata, added_at=e.added_at)
                self._entries[ne.id] = ne
                self._deleted.discard(ne.id)
                added += 1
            if added:
                self._matrix_dirty = True
                self._metrics.total_adds += added
                self._metrics.total_entries = len(self._entries) - len(self._deleted)
        return added

    def query(self, embedding: Tuple[float, ...], top_k: int = 10,
               min_score: float = 0.0) -> List[VectorQueryResult]:
        t0 = time.perf_counter()
        q  = np.array(embedding, dtype=np.float32)
        if self._normalize:
            qn = float(np.linalg.norm(q))
            if qn > 1e-8:
                q = q / qn

        with self._lock:
            self._metrics.total_queries += 1
            if not self._entries:
                return []
            # Reconstrói matrix se dirty
            if self._matrix_dirty:
                live = [(eid, e) for eid, e in self._entries.items()
                        if eid not in self._deleted]
                if not live:
                    return []
                self._ids_order  = [eid for eid, _ in live]
                self._emb_matrix = np.vstack(
                    [np.array(e.embedding, dtype=np.float32) for _, e in live]
                )
                self._matrix_dirty = False
            else:
                self._metrics.cache_hits += 1

            if self._emb_matrix is None or len(self._ids_order) == 0:
                return []

            # Dot-product vetorizado (embeddings normalizados → equivalente a cosine)
            scores = self._emb_matrix @ q  # (n,)
            k      = min(top_k, len(scores))
            # nlargest via argpartition O(n) melhor que argsort O(n log n)
            if k < len(scores):
                top_idx = np.argpartition(scores, -k)[-k:]
                top_idx = top_idx[np.argsort(scores[top_idx])[::-1]]
            else:
                top_idx = np.argsort(scores)[::-1]

            results = []
            for i in top_idx:
                sc = float(scores[i])
                if sc < min_score:
                    continue
                eid   = self._ids_order[i]
                entry = self._entries.get(eid)
                if entry and eid not in self._deleted:
                    results.append(VectorQueryResult(id=eid, score=round(sc, 4), entry=entry))

        ms = (time.perf_counter() - t0) * 1000
        self._metrics.record_query_ms(ms)
        return results

    def delete(self, entry_id: str) -> bool:
        with self._lock:
            if entry_id in self._entries:
                self._deleted.add(entry_id)
                self._matrix_dirty = True
                self._metrics.total_deletes += 1
                self._metrics.total_entries = len(self._entries) - len(self._deleted)
                return True
        return False

    def get(self, entry_id: str) -> Optional[VectorEntry]:
        with self._lock:
            if entry_id not in self._deleted:
                return self._entries.get(entry_id)
        return None

    def size(self) -> int:
        with self._lock:
            return len(self._entries) - len(self._deleted)

    def compact(self) -> int:
        with self._lock:
            n_removed = len(self._deleted)
            for eid in self._deleted:
                self._entries.pop(eid, None)
            self._deleted.clear()
            self._matrix_dirty = True
            self._metrics.total_entries = len(self._entries)
            return n_removed

    def metrics(self) -> VectorStoreMetrics:
        with self._lock:
            self._metrics.total_entries = len(self._entries) - len(self._deleted)
            return VectorStoreMetrics(
                total_entries=self._metrics.total_entries,
                total_queries=self._metrics.total_queries,
                total_adds=self._metrics.total_adds,
                total_deletes=self._metrics.total_deletes,
                cache_hits=self._metrics.cache_hits,
                avg_query_ms=self._metrics.avg_query_ms,
            )

    def health_report(self) -> dict:
        m = self.metrics()
        return {
            "size":         m.total_entries,
            "queries":      m.total_queries,
            "cache_hits":   m.cache_hits,
            "avg_query_ms": m.avg_query_ms,
            "dirty":        self._matrix_dirty,
        }
