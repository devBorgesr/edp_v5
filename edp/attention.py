"""
attention.py (PATCHED)
[P-F6] suppress_redundant: duplo loop Python → np.where(triu) vetorizado
       Loop Python só sobre pares redundantes encontrados (geralmente << n²)
"""
"""
attention.py — Sistema de atenção seletiva do EDP v3.
Simula seleção cognitiva de contexto via saliência,
boosting/suppression contextual e foco dinâmico.
"""
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from .config import PRIORIDADE_PESO

# ── Saliência contextual ──────────────────────────────────────────────────────

def saliency(
    embeddings: np.ndarray,
    query_emb: np.ndarray,
) -> np.ndarray:
    """
    Calcula saliência de cada chunk em relação à query.
    Retorna vetor de scores [0, 1] por chunk.
    """
    if embeddings.size == 0:
        return np.array([])
    sims = cosine_similarity(embeddings, [query_emb]).flatten()
    # normaliza para [0, 1]
    s_min, s_max = sims.min(), sims.max()
    if s_max - s_min < 1e-8:
        return np.ones(len(sims))
    return (sims - s_min) / (s_max - s_min)

# ── Boosting contextual ───────────────────────────────────────────────────────

def contextual_boost(
    scores: np.ndarray,
    prioridades: list[str],
    n_acessos: list[int],
) -> np.ndarray:
    """
    Aplica boost por prioridade e frequência de acesso.
    scores_boosted[i] = scores[i] × priority_weight × log_access_boost
    """
    import math
    boosted = scores.copy()
    for i, (prio, acessos) in enumerate(zip(prioridades, n_acessos)):
        pw    = PRIORIDADE_PESO.get(prio, 1.0)
        ab    = 1.0 + 0.05 * math.log1p(max(acessos, 0))
        boosted[i] = scores[i] * pw * ab
    return boosted

# ── Suppression contextual ────────────────────────────────────────────────────

def suppress_redundant(
    scores: np.ndarray,
    embeddings: np.ndarray,
    threshold: float = 0.85,
) -> np.ndarray:
    """
    Suprime chunks redundantes entre si.
    Se cos_sim(i, j) > threshold e score[j] < score[i],
    zera o score de j (supressão lateral).
    """
    if embeddings.size == 0 or len(scores) < 2:
        return scores

    suppressed = scores.copy()
    sim_matrix = cosine_similarity(embeddings)

    # [P-F6] Vetoriza detecção de pares redundantes via np.where
    # Antes: for i × for j → O(n²) iterações Python
    # Depois: np.where(upper_triangle > threshold) → índices em 1 op NumPy
    #         Loop Python apenas sobre pares encontrados (geralmente << n²)
    rows, cols = np.where(np.triu(sim_matrix > threshold, k=1))
    for i, j in zip(rows.tolist(), cols.tolist()):
        if suppressed[i] == 0 or suppressed[j] == 0:
            continue
        if suppressed[i] >= suppressed[j]:
            suppressed[j] = 0.0
        else:
            suppressed[i] = 0.0

    return suppressed

# ── Foco dinâmico ─────────────────────────────────────────────────────────────

def dynamic_focus(
    embeddings: np.ndarray,
    query_emb: np.ndarray,
    scores: np.ndarray,
    top_k: int,
    prioridades: list[str] | None = None,
    n_acessos: list[int] | None = None,
    suppress_threshold: float = 0.85,
) -> list[int]:
    """
    Pipeline completo de atenção seletiva.

    1. Calcula saliência
    2. Aplica boost (se prioridades/acessos fornecidos)
    3. Suprime redundâncias
    4. Retorna top_k índices por score final

    Retorna lista de índices ordenados por score DESC.
    """
    if embeddings.size == 0:
        return []

    sal = saliency(embeddings, query_emb)

    # combina saliência com score externo
    combined = (sal + scores) / 2.0

    if prioridades and n_acessos:
        combined = contextual_boost(combined, prioridades, n_acessos)

    combined = suppress_redundant(combined, embeddings, suppress_threshold)

    # top_k por score
    k       = min(top_k, len(combined))
    top_idx = np.argsort(combined)[::-1][:k]

    return [int(i) for i in top_idx if combined[i] > 0]

# ── Prioridade adaptativa ─────────────────────────────────────────────────────

def adaptive_priority(
    entry: dict,
    query_emb: np.ndarray,
    current_priority: str,
) -> str:
    """
    Atualiza prioridade de uma entrada com base em sua
    similaridade atual com a query.
    Alta sim → promove para 'alta'
    Baixa sim → demove para 'baixa'
    """
    emb = np.array(entry.get("embedding", []), dtype=np.float32)
    if emb.size == 0:
        return current_priority

    sim = float(cosine_similarity([emb], [query_emb])[0][0])

    if sim >= 0.75:
        return "alta"
    if sim >= 0.40:
        return "media"
    return "baixa"

