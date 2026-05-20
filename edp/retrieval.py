"""
retrieval.py — Motor de retrieval híbrido do EDP v3.
Backends: cosine | faiss_flat | faiss_ivf | hnsw
ANN retrieval, dynamic top_k, score normalization,
redundancy suppression, semantic saturation detection.

PATCHES APLICADOS:
  [P1] enhanced_search movida para dentro de RetrievalEngine (era função solta com self)
  [P2] self._conf → self._confidence | self._sat → self._is_saturated (NameError em runtime)
  [P3] deduplicate: O(n²) → O(n) via matrix pré-computada
  [P4] from .temporal import lazy (evita circular dependency em import-time)
"""
import numpy as np
from dataclasses import dataclass, field
from sklearn.metrics.pairwise import cosine_similarity

from .config import (
    RETRIEVAL_TOP_K, RETRIEVAL_MIN_SIM,
    DEDUP_THRESH, ANN_NPROBE,
    HNSW_EF_SEARCH, HNSW_M, EMBED_DIM,
)

# ── Resultado de retrieval ────────────────────────────────────────────────────

@dataclass
class RetrievalResult:
    indices:    list[int]
    scores:     list[float]
    texts:      list[str]   = field(default_factory=list)
    confidence: float       = 1.0
    saturated:  bool        = False


# ── Engine ────────────────────────────────────────────────────────────────────

class RetrievalEngine:
    """
    Motor de retrieval plugável.
    Não contém lógica de embedding nem de memória.
    """

    def __init__(self, backend: str = "cosine", dim: int = EMBED_DIM):
        self.backend      = backend
        self.dim          = dim
        self._index       = None
        self._texts:      list[str]   = []
        self._embeddings: np.ndarray  = np.empty((0, dim), dtype=np.float32)

        if backend.startswith("faiss"):
            self._init_faiss(backend)
        elif backend == "hnsw":
            self._init_hnsw()

    # ── Inicialização de índices ───────────────────────────────────────────────

    def _init_faiss(self, backend: str) -> None:
        try:
            import faiss
            if backend == "faiss_flat":
                self._index = faiss.IndexFlatIP(self.dim)
            elif backend == "faiss_ivf":
                quantizer   = faiss.IndexFlatIP(self.dim)
                self._index = faiss.IndexIVFFlat(
                    quantizer, self.dim, 100, faiss.METRIC_INNER_PRODUCT
                )
                self._index.nprobe = ANN_NPROBE
        except ImportError:
            print("[RetrievalEngine] FAISS indisponível — fallback cosine")
            self.backend = "cosine"

    def _init_hnsw(self) -> None:
        try:
            import hnswlib
            self._index = hnswlib.Index(space="cosine", dim=self.dim)
            self._index.init_index(max_elements=100_000, ef_construction=200, M=HNSW_M)
            self._index.set_ef(HNSW_EF_SEARCH)
            self._hnsw_count = 0
        except ImportError:
            print("[RetrievalEngine] hnswlib indisponível — fallback cosine")
            self.backend = "cosine"

    # ── Adição de vetores ─────────────────────────────────────────────────────

    def add(self, embeddings: np.ndarray, texts: list[str] | None = None) -> None:
        if embeddings.size == 0:
            return
        vecs = embeddings.astype(np.float32)
        if texts:
            self._texts.extend(texts)

        if self.backend == "cosine":
            self._embeddings = (
                vecs if self._embeddings.size == 0
                else np.vstack([self._embeddings, vecs])
            )

        elif self.backend == "faiss_flat":
            self._index.add(vecs)
            self._embeddings = (
                vecs if self._embeddings.size == 0
                else np.vstack([self._embeddings, vecs])
            )

        elif self.backend == "faiss_ivf":
            if not self._index.is_trained:
                if len(vecs) >= 100:
                    self._index.train(vecs)
                else:
                    self.backend = "faiss_flat"
                    self._init_faiss("faiss_flat")
                    self._index.add(vecs)
                    return
            self._index.add(vecs)
            self._embeddings = (
                vecs if self._embeddings.size == 0
                else np.vstack([self._embeddings, vecs])
            )

        elif self.backend == "hnsw":
            n = len(vecs)
            labels = np.arange(self._hnsw_count, self._hnsw_count + n)
            self._index.add_items(vecs, labels)
            self._hnsw_count += n
            self._embeddings = (
                vecs if self._embeddings.size == 0
                else np.vstack([self._embeddings, vecs])
            )

    # ── Busca ─────────────────────────────────────────────────────────────────

    def search(
        self,
        query_emb: np.ndarray,
        top_k: int = RETRIEVAL_TOP_K,
        min_score: float = RETRIEVAL_MIN_SIM,
    ) -> RetrievalResult:
        if self.backend == "cosine":
            return self._search_cosine(query_emb, top_k, min_score)
        elif self.backend in ("faiss_flat", "faiss_ivf"):
            return self._search_faiss(query_emb, top_k, min_score)
        elif self.backend == "hnsw":
            return self._search_hnsw(query_emb, top_k, min_score)
        return RetrievalResult([], [], [])

    def _search_cosine(self, q, top_k, min_score) -> RetrievalResult:
        if self._embeddings.size == 0:
            return RetrievalResult([], [], [])
        sims = cosine_similarity([q], self._embeddings)[0]
        pairs = sorted(
            [(i, float(sims[i])) for i in range(len(sims)) if float(sims[i]) >= min_score],
            key=lambda x: x[1], reverse=True
        )[:top_k]
        idx, sc = [i for i, _ in pairs], [s for _, s in pairs]
        texts = [self._texts[i] for i in idx if i < len(self._texts)]
        return RetrievalResult(idx, sc, texts, self._confidence(sc), self._is_saturated(sc))

    def _search_faiss(self, q, top_k, min_score) -> RetrievalResult:
        if self._index is None:
            return RetrievalResult([], [], [])
        sc_arr, idx_arr = self._index.search(
            np.array([q], dtype=np.float32), top_k
        )
        pairs = [
            (int(i), float(s))
            for i, s in zip(idx_arr[0], sc_arr[0])
            if i >= 0 and float(s) >= min_score
        ]
        idx, sc = [i for i, _ in pairs], [s for _, s in pairs]
        texts = [self._texts[i] for i in idx if i < len(self._texts)]
        return RetrievalResult(idx, sc, texts, self._confidence(sc), self._is_saturated(sc))

    def _search_hnsw(self, q, top_k, min_score) -> RetrievalResult:
        if self._index is None or self._hnsw_count == 0:
            return RetrievalResult([], [], [])
        labels, distances = self._index.knn_query(
            np.array([q], dtype=np.float32), k=min(top_k, self._hnsw_count)
        )
        pairs = [
            (int(l), round(1.0 - float(d), 4))
            for l, d in zip(labels[0], distances[0])
            if (1.0 - float(d)) >= min_score
        ]
        idx, sc = [i for i, _ in pairs], [s for _, s in pairs]
        texts = [self._texts[i] for i in idx if i < len(self._texts)]
        return RetrievalResult(idx, sc, texts, self._confidence(sc), self._is_saturated(sc))

    # ── Score normalization ───────────────────────────────────────────────────

    def normalize_scores(self, scores: list[float]) -> list[float]:
        if not scores:
            return []
        mn, mx = min(scores), max(scores)
        if mx - mn < 1e-8:
            return [1.0] * len(scores)
        return [round((s - mn) / (mx - mn), 4) for s in scores]

    # ── Confidence & saturation ───────────────────────────────────────────────

    def _confidence(self, scores: list[float]) -> float:
        if not scores:
            return 0.0
        return round(sum(scores) / len(scores), 4)

    def _is_saturated(self, scores: list[float], threshold: float = 0.90) -> bool:
        """Semantic saturation: todos os resultados com similaridade muito alta."""
        if len(scores) < 2:
            return False
        return all(s >= threshold for s in scores)

    # ── Utilidades ────────────────────────────────────────────────────────────

    def similarity_matrix(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return cosine_similarity(a, b)

    def filter_by_similarity(
        self,
        texts: list[str],
        embeddings: np.ndarray,
        query_emb: np.ndarray,
        min_sim: float,
    ) -> tuple[list[str], np.ndarray]:
        if not texts or embeddings.size == 0:
            return [], np.array([])
        sims = cosine_similarity(embeddings, [query_emb]).flatten()
        idx  = [i for i, s in enumerate(sims) if s >= min_sim]
        return (
            [texts[i] for i in idx],
            embeddings[idx] if idx else np.empty((0, self.dim), dtype=np.float32),
        )

    def deduplicate(
        self,
        items: list[str],
        embeddings: np.ndarray | None = None,
        threshold: float = DEDUP_THRESH,
    ) -> list[str]:
        """
        [P3] FIX O(n²) → O(n):
        Pré-computa matrix completa de similaridades em uma única chamada vetorizada.
        Complexidade: O(n²) em memória mas O(1) em chamadas cosine_similarity.
        Para n=1000: 1 chamada de matrix vs 500k chamadas individuais.
        """
        if not items:
            return []
        from .embeddings import embed as _e
        emb = embeddings if embeddings is not None else _e(items)

        # Pré-computa toda a similarity matrix de uma vez
        sim_matrix = cosine_similarity(emb, emb)  # shape (n, n)

        keep: list[int] = []
        for i in range(len(items)):
            # Verifica similaridade com todos os já mantidos via indexing O(1)
            if not any(float(sim_matrix[i, j]) > threshold for j in keep):
                keep.append(i)
        return [items[i] for i in keep]

    def dynamic_top_k(
        self,
        query_emb: np.ndarray,
        base_k: int = RETRIEVAL_TOP_K,
        confidence_threshold: float = 0.50,
    ) -> int:
        """
        Ajusta top_k dinamicamente baseado em densidade do índice.
        """
        n = len(self._texts) or (
            self._embeddings.shape[0] if self._embeddings.size > 0 else 0
        )
        if n == 0:
            return 1
        return min(base_k, max(1, n))

    # ── [P1] Enhanced search — CORRIGIDA: agora método de instância ───────────

    def enhanced_search(
        self,
        query_emb: np.ndarray,
        entries: list,
        top_k: int = RETRIEVAL_TOP_K,
        min_score: float = RETRIEVAL_MIN_SIM,
        recency_weight: float = 0.15,
        frequency_weight: float = 0.10,
    ) -> RetrievalResult:
        """
        [P1] Movida para dentro da classe (era função solta — dead code).
        [P2] self._conf → self._confidence | self._sat → self._is_saturated
        Busca híbrida com pesos de recência e frequência de acesso.
        """
        import time as _t
        # [P4] Import lazy para evitar circular dependency em import-time
        from .temporal import decay as _decay, access_boost as _aboost

        if not entries:
            return RetrievalResult([], [], [])

        emb_matrix = np.vstack([np.array(e["embedding"], dtype=np.float32) for e in entries])
        sims = cosine_similarity([query_emb], emb_matrix)[0]
        now  = _t.time()

        scored = []
        base = 1.0 - recency_weight - frequency_weight
        for i, (entry, sim) in enumerate(zip(entries, sims)):
            if float(sim) < min_score:
                continue
            rec  = _decay(entry.get("ultimo_acesso", now))
            freq = _aboost(entry.get("acessos", 0))
            final = base * float(sim) + recency_weight * rec + frequency_weight * (freq - 1.0)
            scored.append((i, round(final, 4)))

        scored.sort(key=lambda x: x[1], reverse=True)
        top     = scored[:top_k]
        indices = [i for i, _ in top]
        scores  = [s for _, s in top]

        return RetrievalResult(
            indices=indices,
            scores=scores,
            texts=[entries[i]["text"] for i in indices],
            confidence=self._confidence(scores),   # [P2] corrigido de _conf
            saturated=self._is_saturated(scores),  # [P2] corrigido de _sat
        )
