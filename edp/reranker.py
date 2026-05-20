"""
reranker.py — Reranking do EDP v3.
Reranking por relevância semântica, diversidade e MMR.
Opera sobre listas de candidatos já recuperados.
"""
from dataclasses import dataclass, field

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from .config import DEDUP_THRESH


@dataclass
class RerankResult:
    indices: list[int]
    scores:  list[float]
    texts:   list[str]   = field(default_factory=list)
    method:  str         = "semantic"


class Reranker:
    """
    Reranker multi-estratégia para listas de candidatos.

    Estratégias:
        semantic   → cosine com query
        diversity  → penaliza redundância entre candidatos
        mmr        → Maximal Marginal Relevance (balanço rel/div)
        hybrid     → semantic + diversity ponderados
    """

    def __init__(self, diversity_penalty: float = 0.3):
        self.diversity_penalty = diversity_penalty  # peso da penalidade de redundância

    # ── API principal ──────────────────────────────────────────────────────────

    def rerank(
        self,
        candidates: list[str],
        embeddings: np.ndarray,
        query_emb: np.ndarray,
        top_k: int | None = None,
        method: str = "mmr",
        mmr_lambda: float = 0.6,
    ) -> RerankResult:
        """
        Reordena candidatos.

        method in {"semantic", "diversity", "mmr", "hybrid"}
        mmr_lambda: 1.0=relevância pura, 0.0=diversidade pura
        """
        if not candidates or embeddings.size == 0:
            return RerankResult([], [], [])

        top_k  = top_k or len(candidates)
        top_k  = min(top_k, len(candidates))

        if method == "semantic":
            return self._semantic(candidates, embeddings, query_emb, top_k)
        elif method == "diversity":
            return self._diversity(candidates, embeddings, query_emb, top_k)
        elif method == "mmr":
            return self._mmr(candidates, embeddings, query_emb, top_k, mmr_lambda)
        elif method == "hybrid":
            return self._hybrid(candidates, embeddings, query_emb, top_k)
        else:
            raise ValueError(f"method deve ser semantic|diversity|mmr|hybrid, got '{method}'")

    # ── Semantic ──────────────────────────────────────────────────────────────

    def _semantic(self, texts, embs, q_emb, top_k) -> RerankResult:
        sims = cosine_similarity(embs, [q_emb]).flatten()
        order = np.argsort(sims)[::-1][:top_k]
        idx   = [int(i) for i in order]
        sc    = [round(float(sims[i]), 4) for i in idx]
        return RerankResult(idx, sc, [texts[i] for i in idx], "semantic")

    # ── Diversity ─────────────────────────────────────────────────────────────

    def _diversity(self, texts, embs, q_emb, top_k) -> RerankResult:
        """
        Penaliza candidatos similares entre si.
        score[i] = sim_query[i] - penalty * max_sim_with_higher_ranked
        """
        sims_q    = cosine_similarity(embs, [q_emb]).flatten()
        sim_mat   = cosine_similarity(embs)
        n         = len(texts)
        final_sc  = sims_q.copy()

        for i in range(n):
            for j in range(i):
                redundancy     = sim_mat[i, j]
                final_sc[i]   -= self.diversity_penalty * redundancy

        order = np.argsort(final_sc)[::-1][:top_k]
        idx   = [int(i) for i in order]
        sc    = [round(float(final_sc[i]), 4) for i in idx]
        return RerankResult(idx, sc, [texts[i] for i in idx], "diversity")

    # ── MMR ───────────────────────────────────────────────────────────────────

    def _mmr(self, texts, embs, q_emb, top_k, lam) -> RerankResult:
        """
        Maximal Marginal Relevance.
        Greedy selection: max(λ·rel - (1-λ)·max_red)
        """
        sims_q     = cosine_similarity(embs, [q_emb]).flatten()
        candidates = list(range(len(texts)))
        selected:  list[int] = []
        scores:    list[float] = []

        while candidates and len(selected) < top_k:
            if not selected:
                best = int(np.argmax([sims_q[i] for i in candidates]))
                best = candidates[best]
                scores.append(round(float(sims_q[best]), 4))
            else:
                sel_embs   = embs[selected]
                best_score = -np.inf
                best       = candidates[0]
                for i in candidates:
                    rel = float(sims_q[i])
                    red = float(cosine_similarity([embs[i]], sel_embs).max())
                    s   = lam * rel - (1 - lam) * red
                    if s > best_score:
                        best_score = s
                        best       = i
                scores.append(round(best_score, 4))

            selected.append(best)
            candidates.remove(best)

        return RerankResult(selected, scores, [texts[i] for i in selected], "mmr")

    # ── Hybrid ────────────────────────────────────────────────────────────────

    def _hybrid(self, texts, embs, q_emb, top_k) -> RerankResult:
        """
        Combina semantic score e diversidade (penalidade cruzada).
        """
        sims_q  = cosine_similarity(embs, [q_emb]).flatten()
        sim_mat = cosine_similarity(embs)
        n       = len(texts)

        # diversity score = 1 - avg_similarity_with_others
        div_sc = np.array([
            1.0 - (sim_mat[i].sum() - 1.0) / max(n - 1, 1)
            for i in range(n)
        ])

        # normaliza ambos
        def _norm(v):
            mn, mx = v.min(), v.max()
            return (v - mn) / (mx - mn) if mx - mn > 1e-8 else np.ones_like(v)

        combined = 0.7 * _norm(sims_q) + 0.3 * _norm(div_sc)
        order    = np.argsort(combined)[::-1][:top_k]
        idx      = [int(i) for i in order]
        sc       = [round(float(combined[i]), 4) for i in idx]
        return RerankResult(idx, sc, [texts[i] for i in idx], "hybrid")

    # ── Dedup post-rerank ──────────────────────────────────────────────────────

    def deduplicate(
        self,
        result: RerankResult,
        embeddings: np.ndarray,
        threshold: float = DEDUP_THRESH,
    ) -> RerankResult:
        """Remove near-duplicatas do resultado rerankeado."""
        if not result.indices:
            return result
        keep: list[int] = []
        for i, orig_i in enumerate(result.indices):
            emb = embeddings[orig_i]
            if not any(
                float(cosine_similarity([emb], [embeddings[result.indices[j]]])[0][0]) > threshold
                for j in keep
            ):
                keep.append(i)

        return RerankResult(
            indices=[result.indices[i] for i in keep],
            scores=[result.scores[i]   for i in keep],
            texts=[result.texts[i]     for i in keep],
            method=result.method,
        )

