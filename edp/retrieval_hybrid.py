"""
retrieval_hybrid.py — Retrieval híbrido BM25 + vetorial do EDP v3.
BM25 léxico + cosine semântico + Reciprocal Rank Fusion.
Sem dependências externas além de numpy/sklearn.
"""
import re
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from .config import RETRIEVAL_TOP_K, RETRIEVAL_MIN_SIM, DEDUP_THRESH, EMBED_DIM

# ── BM25 ──────────────────────────────────────────────────────────────────────

class BM25:
    """
    BM25 Okapi puro — sem dependências externas.
    k1=1.5, b=0.75 (valores padrão da literatura).
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1     = k1
        self.b      = b
        self.corpus: list[list[str]] = []
        self.idf:    dict[str, float] = {}
        self.avgdl:  float = 0.0
        self._built = False

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"\b\w+\b", text.lower())

    def fit(self, documents: list[str]) -> None:
        self.corpus = [self._tokenize(d) for d in documents]
        N      = len(self.corpus)
        avgdl  = sum(len(d) for d in self.corpus) / max(N, 1)
        self.avgdl = avgdl

        # IDF com suavização
        df: dict[str, int] = defaultdict(int)
        for doc in self.corpus:
            for term in set(doc):
                df[term] += 1

        self.idf = {
            term: math.log((N - n + 0.5) / (n + 0.5) + 1)
            for term, n in df.items()
        }
        self._built = True

    def score(self, query: str, top_k: int = RETRIEVAL_TOP_K) -> list[tuple[int, float]]:
        """Retorna (idx, score) ordenado desc."""
        if not self._built or not self.corpus:
            return []

        q_terms  = self._tokenize(query)
        scores   = np.zeros(len(self.corpus), dtype=np.float64)
        k1, b    = self.k1, self.b

        for term in q_terms:
            idf_t = self.idf.get(term, 0.0)
            if idf_t == 0:
                continue
            for i, doc in enumerate(self.corpus):
                tf = doc.count(term)
                if tf == 0:
                    continue
                dl   = len(doc)
                denom = tf + k1 * (1 - b + b * dl / self.avgdl)
                scores[i] += idf_t * (tf * (k1 + 1)) / denom

        pairs = sorted(
            [(i, float(scores[i])) for i in range(len(scores)) if scores[i] > 0],
            key=lambda x: x[1], reverse=True,
        )
        return pairs[:top_k]

    def normalize_scores(self, scored: list[tuple[int, float]]) -> list[tuple[int, float]]:
        if not scored:
            return []
        max_s = max(s for _, s in scored)
        if max_s == 0:
            return scored
        return [(i, round(s / max_s, 4)) for i, s in scored]


# ── Resultado ─────────────────────────────────────────────────────────────────

@dataclass
class HybridResult:
    indices:    list[int]
    scores:     list[float]
    bm25_scores:   list[float]
    vector_scores: list[float]
    texts:      list[str]        = field(default_factory=list)
    method:     str              = "rrf"
    confidence: float            = 0.0


# ── HybridRetriever ───────────────────────────────────────────────────────────

class HybridRetriever:
    """
    Retriever híbrido: BM25 léxico + cosine vetorial.
    Fusão por Reciprocal Rank Fusion (RRF) ou weighted sum.

    Suporta reranking por MMR para diversidade.
    """

    def __init__(
        self,
        alpha: float = 0.5,   # peso do vetor vs BM25 (0=só BM25, 1=só vetor)
        rrf_k: int   = 60,    # constante RRF (padrão da literatura)
        dim:   int   = EMBED_DIM,
    ):
        self.alpha   = alpha
        self.rrf_k   = rrf_k
        self.dim     = dim

        self._texts:      list[str]    = []
        self._embeddings: np.ndarray   = np.empty((0, dim), dtype=np.float32)
        self._bm25:       BM25         = BM25()
        self._fitted      = False

    # ── Indexação ─────────────────────────────────────────────────────────────

    def add(self, texts: list[str], embeddings: np.ndarray) -> None:
        if not texts or embeddings.size == 0:
            return
        self._texts.extend(texts)
        self._embeddings = (
            embeddings.astype(np.float32)
            if self._embeddings.size == 0
            else np.vstack([self._embeddings, embeddings.astype(np.float32)])
        )
        self._bm25.fit(self._texts)
        self._fitted = True

    def reset(self) -> None:
        self._texts      = []
        self._embeddings = np.empty((0, self.dim), dtype=np.float32)
        self._bm25       = BM25()
        self._fitted     = False

    # ── Search ────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        query_emb: np.ndarray,
        top_k: int = RETRIEVAL_TOP_K,
        min_score: float = RETRIEVAL_MIN_SIM,
        method: str = "rrf",   # "rrf" | "weighted"
        mmr: bool  = False,
        mmr_lambda: float = 0.5,
    ) -> HybridResult:
        """
        Busca híbrida.

        method="rrf"      → Reciprocal Rank Fusion
        method="weighted" → alpha*vetor + (1-alpha)*BM25
        mmr=True          → reranking por Maximal Marginal Relevance
        """
        if not self._fitted or not self._texts:
            return HybridResult([], [], [], [], [])

        k = min(top_k * 3, len(self._texts))  # busca ampliada para fusão

        # ── BM25 ranking ──────────────────────────────────────────────────────
        bm25_raw      = self._bm25.score(query, top_k=k)
        bm25_norm     = dict(self._bm25.normalize_scores(bm25_raw))
        bm25_rank     = {idx: rank for rank, (idx, _) in enumerate(bm25_raw)}

        # ── Vetorial ranking ──────────────────────────────────────────────────
        if self._embeddings.size > 0:
            sims       = cosine_similarity([query_emb], self._embeddings)[0]
            vec_pairs  = sorted(
                [(i, float(sims[i])) for i in range(len(sims)) if float(sims[i]) > 0],
                key=lambda x: x[1], reverse=True,
            )[:k]
        else:
            vec_pairs = []

        vec_norm  = {i: round(s, 4) for i, s in vec_pairs}
        vec_rank  = {i: rank for rank, (i, _) in enumerate(vec_pairs)}

        # ── Fusão ─────────────────────────────────────────────────────────────
        all_idx = set(bm25_rank.keys()) | set(vec_rank.keys())

        if method == "rrf":
            fused = self._rrf(all_idx, bm25_rank, vec_rank)
        else:
            fused = self._weighted(all_idx, bm25_norm, vec_norm)

        # filtra por min_score e pega top_k
        fused = sorted(
            [(i, s) for i, s in fused.items() if s >= min_score],
            key=lambda x: x[1], reverse=True,
        )[:top_k]

        if not fused:
            return HybridResult([], [], [], [], [], method)

        idx_list = [i for i, _ in fused]
        sc_list  = [s for _, s in fused]

        # ── MMR reranking ──────────────────────────────────────────────────────
        if mmr and len(idx_list) > 1:
            idx_list = self._mmr_rerank(idx_list, query_emb, mmr_lambda, top_k)
            sc_list  = [fused[idx_list.index(i)] if i in idx_list else 0.0
                        for i in idx_list]
            # recalcula scores na nova ordem
            sc_list = [
                next((s for ii, s in fused if ii == i), 0.0)
                for i in idx_list
            ]

        texts      = [self._texts[i] for i in idx_list if i < len(self._texts)]
        bm25_sc    = [bm25_norm.get(i, 0.0)  for i in idx_list]
        vec_sc     = [vec_norm.get(i, 0.0)   for i in idx_list]
        confidence = round(sum(sc_list) / max(len(sc_list), 1), 4)

        return HybridResult(
            indices=idx_list,
            scores=sc_list,
            bm25_scores=bm25_sc,
            vector_scores=vec_sc,
            texts=texts,
            method=method,
            confidence=confidence,
        )

    # ── RRF ───────────────────────────────────────────────────────────────────

    def _rrf(
        self,
        all_idx: set[int],
        bm25_rank: dict[int, int],
        vec_rank:  dict[int, int],
    ) -> dict[int, float]:
        """
        Reciprocal Rank Fusion:
        score(d) = Σ 1 / (k + rank(d))
        """
        k = self.rrf_k
        scores: dict[int, float] = {}
        for i in all_idx:
            s = 0.0
            if i in bm25_rank:
                s += (1 - self.alpha) / (k + bm25_rank[i] + 1)
            if i in vec_rank:
                s += self.alpha / (k + vec_rank[i] + 1)
            scores[i] = round(s, 6)
        return scores

    # ── Weighted ──────────────────────────────────────────────────────────────

    def _weighted(
        self,
        all_idx: set[int],
        bm25_norm: dict[int, float],
        vec_norm:  dict[int, float],
    ) -> dict[int, float]:
        scores: dict[int, float] = {}
        for i in all_idx:
            b = bm25_norm.get(i, 0.0)
            v = vec_norm.get(i, 0.0)
            scores[i] = round(self.alpha * v + (1 - self.alpha) * b, 4)
        return scores

    # ── MMR ───────────────────────────────────────────────────────────────────

    def _mmr_rerank(
        self,
        idx_list: list[int],
        query_emb: np.ndarray,
        lam: float,
        top_k: int,
    ) -> list[int]:
        """
        Maximal Marginal Relevance.
        lam=1.0 → pura relevância, lam=0.0 → pura diversidade.
        """
        if self._embeddings.size == 0 or not idx_list:
            return idx_list

        candidates = idx_list[:]
        selected:  list[int] = []

        embs = {i: self._embeddings[i] for i in candidates if i < len(self._embeddings)}
        sims_query = {i: float(cosine_similarity([embs[i]], [query_emb])[0][0])
                      for i in embs}

        while candidates and len(selected) < top_k:
            if not selected:
                # primeiro: maior similaridade com query
                best = max(candidates, key=lambda i: sims_query.get(i, 0.0))
            else:
                # MMR: max(λ·sim_query - (1-λ)·max_sim_selected)
                sel_embs = np.vstack([embs[s] for s in selected if s in embs])
                best, best_score = None, -np.inf
                for i in candidates:
                    if i not in embs:
                        continue
                    rel  = sims_query.get(i, 0.0)
                    red  = float(cosine_similarity([embs[i]], sel_embs).max())
                    mmr  = lam * rel - (1 - lam) * red
                    if mmr > best_score:
                        best_score = mmr
                        best       = i

            if best is None:
                break
            selected.append(best)
            candidates.remove(best)

        return selected

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "n_docs":   len(self._texts),
            "alpha":    self.alpha,
            "rrf_k":    self.rrf_k,
            "bm25_fitted": self._fitted,
            "vocab_size": len(self._bm25.idf),
        }

