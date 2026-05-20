"""
FRACTAL EDP v3.2 — EMBEDDING CACHE (P1)

PROBLEM:
  Every retrieval call previously regenerated embeddings from scratch.
  At 10k queries/hour with DIM=1536, this is 15M float operations wasted
  on cache-able content.

DESIGN — DETERMINISTIC LRU WITH MEMORY PRESSURE AWARENESS:
  Key:   SHA-256(text[:512])[:16]  — deterministic, bounded, collision-resistant
         Text is truncated to 512 chars before hashing: prevents O(n) hash cost
         on large documents.
  Value: Tuple[float, ...] — immutable, safely shared across threads

  Eviction: LRU via OrderedDict (O(1) get/put/evict).
  Memory pressure: if system signals HIGH pressure, cache shrinks by 50%.
  Hard cap: max_entries enforced unconditionally.

COMPLEXITY:
  get():    O(1) average (dict lookup + OrderedDict move)
  put():    O(1) amortized (dict insert + possible evict)
  evict():  O(k) where k = eviction_batch_size (default 100)
  Memory:   O(max_entries * DIM * 4 bytes) = ~600MB at 100k entries, DIM=1536

THREAD SAFETY:
  Single RLock covers all operations.
  Lock is held for O(1) time per operation — no contention risk.

METRICS:
  hit_rate, miss_rate, eviction_count, current_size
  These are read by MetaStabilityController to detect cache instability.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple


@dataclass
class EmbeddingCacheConfig:
    max_entries: int = 10_000
    eviction_batch_size: int = 100       # evict this many on overflow
    pressure_shrink_factor: float = 0.5  # shrink to 50% under HIGH pressure
    key_text_max_chars: int = 512        # truncate before hashing


@dataclass
class CacheMetrics:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    pressure_shrinks: int = 0
    current_size: int = 0

    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def miss_rate(self) -> float:
        return 1.0 - self.hit_rate()

    def instability_score(self) -> float:
        """
        High miss_rate + high eviction_count = cache instability.
        Used by MetaStabilityController as cache_instability signal.
        Range: [0, 1].
        """
        miss_contrib = self.miss_rate() * 0.7
        evict_norm = min(self.evictions / max(self.hits + self.misses, 1), 1.0) * 0.3
        return min(miss_contrib + evict_norm, 1.0)


class EmbeddingCache:
    """
    Deterministic LRU cache for text embeddings.
    Thread-safe. Memory-pressure aware.
    """

    def __init__(
        self,
        config: Optional[EmbeddingCacheConfig] = None,
        embedding_fn: Optional[Callable[[str], Tuple[float, ...]]] = None,
    ) -> None:
        self._cfg = config or EmbeddingCacheConfig()
        self._embedding_fn = embedding_fn  # injected — cache never calls model directly
        self._lock = threading.RLock()
        self._store: OrderedDict[str, Tuple[float, ...]] = OrderedDict()
        self._metrics = CacheMetrics()

    def get(self, text: str) -> Optional[Tuple[float, ...]]:
        """O(1). Returns cached embedding or None."""
        key = self._key(text)
        with self._lock:
            emb = self._store.get(key)
            if emb is not None:
                self._store.move_to_end(key)  # LRU refresh
                self._metrics.hits += 1
                return emb
            self._metrics.misses += 1
            return None

    def put(self, text: str, embedding: Tuple[float, ...]) -> None:
        """O(1) amortized. Stores embedding, evicts if over capacity."""
        key = self._key(text)
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                self._store[key] = embedding
                return
            self._store[key] = embedding
            self._store.move_to_end(key)
            if len(self._store) > self._cfg.max_entries:
                self._evict(self._cfg.eviction_batch_size)
            self._metrics.current_size = len(self._store)

    def get_or_compute(self, text: str) -> Optional[Tuple[float, ...]]:
        """
        Try cache first. If miss and embedding_fn is set, compute and store.
        Returns None if no embedding_fn and cache miss.
        """
        cached = self.get(text)
        if cached is not None:
            return cached
        if self._embedding_fn is None:
            return None
        emb = self._embedding_fn(text)
        if emb is not None:
            self.put(text, emb)
        return emb

    def on_pressure_high(self) -> int:
        """
        Called by MetaStabilityController under HIGH/EMERGENCY pressure.
        Shrinks cache to free memory. Returns number of entries evicted.
        """
        with self._lock:
            target = max(
                1,
                int(len(self._store) * self._cfg.pressure_shrink_factor)
            )
            to_evict = len(self._store) - target
            if to_evict > 0:
                self._evict(to_evict)
                self._metrics.pressure_shrinks += 1
                self._metrics.current_size = len(self._store)
            return to_evict

    def metrics(self) -> CacheMetrics:
        with self._lock:
            self._metrics.current_size = len(self._store)
            return CacheMetrics(
                hits=self._metrics.hits,
                misses=self._metrics.misses,
                evictions=self._metrics.evictions,
                pressure_shrinks=self._metrics.pressure_shrinks,
                current_size=self._metrics.current_size,
            )

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._metrics.current_size = 0

    def size(self) -> int:
        with self._lock:
            return len(self._store)

    def _key(self, text: str) -> str:
        """Deterministic, O(1)-bounded key. Truncate before hash."""
        truncated = text[: self._cfg.key_text_max_chars]
        return hashlib.sha256(truncated.encode("utf-8", errors="replace")).hexdigest()[:16]

    def _evict(self, count: int) -> None:
        """Evict `count` LRU entries. Must be called inside lock."""
        evicted = 0
        while evicted < count and self._store:
            self._store.popitem(last=False)  # FIFO = LRU (oldest first)
            evicted += 1
        self._metrics.evictions += evicted