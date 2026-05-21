"""
context_builder.py — Contexto híbrido MMR-like para LLMs (PATCHED)

PATCHES APLICADOS:
  [P-C3-13] MMR loop: O(n×k) chamadas cosine individuais →
            pré-computa matrix (n_remaining × n_selected) a cada iteração
            usando np.dot vetorizado. Reduz alocações intermediárias.
  [P-C3-14] Snapshot defensivo de candidates antes do loop MMR:
            memory_store.retrieve() pode ser chamado concorrentemente por scheduler.
            Copia a lista antes de iterar para evitar mutação durante loop.
  [P-C3-15] min_score passado para memory_store.retrieve():
            SemanticMemory.retrieve() esperava min_conf, não min_score.
            Agora passa como kwarg nomeado — semantic_memory_patched.py aceita ambos.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from .embeddings import embed_one, embed
from .temporal import decay
from .clock import now as _now  # Peça 0.2a — relógio interno robusto


@dataclass
class ContextResult:
    blocks:      List[str]  = field(default_factory=list)
    sources:     List[dict] = field(default_factory=list)
    token_count: int        = 0
    diversity:   float      = 0.0
    coverage:    float      = 0.0

    @property
    def context_str(self) -> str:
        return "\n".join(self.blocks)


def _tc(text: str) -> int:
    return len(re.findall(r"\w+", text))


def _div(embs: np.ndarray) -> float:
    if len(embs) < 2:
        return 1.0
    sim = cosine_similarity(embs)
    n   = len(embs)
    return round(float(1.0 - (sim.sum() - n) / max(n * (n - 1), 1)), 4)


def build_context(
    query:            str,
    memory_store,
    pipeline_blocks:  Optional[List[str]] = None,
    top_k:            int   = 8,
    max_tokens:       int   = 600,
    min_sim:          float = 0.20,
    diversity_lambda: float = 0.30,
    recency_lambda:   float = 0.15,
    dedup_threshold:  float = 0.82,
) -> ContextResult:
    """
    Seleção MMR-like de blocos de contexto.

    [P-C3-13] Loop MMR otimizado:
        Antes (por iteração do while):
            for i, cand in enumerate(remaining):
                cosine_similarity([cand_emb], selected_embs)  ← 1 chamada por candidato

        Depois (por iteração do while):
            cand_matrix = np.vstack(remaining_embs)           # (n_rem, dim)
            sel_matrix  = np.vstack(selected_embs)            # (n_sel, dim)
            # dot product vetorizado: (n_rem, n_sel) em 1 operação
            red_matrix  = cand_matrix @ sel_matrix.T / (norms...)
            max_red     = red_matrix.max(axis=1)              # (n_rem,)
            scores      = relevance - λ·max_red + μ·recency  # vetorizado
            best_idx    = scores.argmax()                     # O(1)

        Redução: de n_rem chamadas sklearn por iteração → 1 operação NumPy.

    [P-C3-14] Snapshot defensivo: list(candidates) antes do loop.
    [P-C3-15] min_score passado como kwarg nomeado para retrieve().
    """
    query_emb  = embed_one(query)
    candidates: List[dict] = []

    # Pipeline blocks
    if pipeline_blocks:
        block_embs = embed(pipeline_blocks)
        for text, emb in zip(pipeline_blocks, block_embs):
            if not text.strip():
                continue
            sim = float(cosine_similarity([query_emb], [emb])[0][0])
            candidates.append({
                "text":           text,
                "embedding":      emb,
                "relevance":      sim,
                "recency":        1.0,
                "source":         "pipeline",
                "ultimo_acesso":  _now(),
            })

    # Memory retrieval
    # [P-C3-15] min_score como kwarg nomeado — compatível com ambas as SemanticMemory
    mem_results = memory_store.retrieve(query, top_k=top_k * 2, min_score=min_sim)
    for r in mem_results:
        emb = np.array(r["embedding"], dtype=np.float32)
        candidates.append({
            "text":           r["text"],
            "embedding":      emb,
            "relevance":      float(r.get("ranking_score", r.get("score", 0.5))),
            "recency":        float(decay(r.get("ultimo_acesso", _now()))),
            "source":         r.get("layer", "episodic"),
            "ultimo_acesso":  r.get("ultimo_acesso", _now()),
        })

    if not candidates:
        return ContextResult()

    # [P-C3-14] Snapshot defensivo — evita mutação da lista durante loop MMR
    remaining      = list(candidates)
    selected:      List[dict]      = []
    selected_embs: List[np.ndarray] = []
    total_tokens   = 0

    while remaining and total_tokens < max_tokens:
        # [P-C3-13] Vetoriza cálculo de redundância para todos os candidatos de uma vez
        rem_embs = np.vstack([c["embedding"] for c in remaining])  # (n_rem, dim)

        if selected_embs:
            sel_matrix = np.vstack(selected_embs)                   # (n_sel, dim)
            # Normalização L2 para cosine via dot product
            rem_norms  = np.linalg.norm(rem_embs,  axis=1, keepdims=True).clip(min=1e-8)
            sel_norms  = np.linalg.norm(sel_matrix, axis=1, keepdims=True).clip(min=1e-8)
            rem_normed = rem_embs  / rem_norms
            sel_normed = sel_matrix / sel_norms
            # (n_rem, n_sel) cosine similarities
            red_matrix = rem_normed @ sel_normed.T
            max_red    = red_matrix.max(axis=1)     # (n_rem,) — redundância máxima
        else:
            max_red = np.zeros(len(remaining))

        relevances = np.array([c["relevance"] for c in remaining])
        recencies  = np.array([c["recency"]   for c in remaining])
        scores     = relevances - diversity_lambda * max_red + recency_lambda * recencies

        best_idx = int(scores.argmax())
        chosen   = remaining.pop(best_idx)

        # Verifica limite de tokens
        t_count = _tc(chosen["text"])
        if total_tokens + t_count > max_tokens and selected:
            break

        # Dedup por threshold
        if selected_embs:
            chosen_emb = chosen["embedding"].reshape(1, -1)
            sel_mat    = np.vstack(selected_embs)
            max_sim    = float(cosine_similarity(chosen_emb, sel_mat)[0].max())
            if max_sim > dedup_threshold:
                continue

        selected.append(chosen)
        selected_embs.append(chosen["embedding"])
        total_tokens += t_count

    if not selected:
        return ContextResult()

    emb_mat  = np.vstack(selected_embs)
    coverage = round(sum(c["relevance"] for c in selected) / len(selected), 4)

    return ContextResult(
        blocks=[c["text"] for c in selected],
        sources=[
            {k: v for k, v in c.items() if k != "embedding"}
            for c in selected
        ],
        token_count=total_tokens,
        diversity=_div(emb_mat),
        coverage=coverage,
    )
