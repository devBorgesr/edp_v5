"""
compression.py — Compressão cognitiva do EDP v3.
Sumarização adaptativa, chunk fusion, abstração semântica,
remoção de redundância, compressão hierárquica.
"""
import re
import math
from collections import Counter

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from .config import DEDUP_THRESH, COMPRESSION_MAX_RATIO

# ── Utilitários ───────────────────────────────────────────────────────────────

def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text)

def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]

def token_count(text: str) -> int:
    return len(_tokens(text))

# ── Sumarização extrativa ─────────────────────────────────────────────────────

def extractive_summarize(
    sentences: list[str],
    embeddings: np.ndarray,
    query_emb: np.ndarray,
    max_sentences: int = 2,
    dedup_threshold: float = DEDUP_THRESH,
) -> str:
    """
    Sumarização extrativa: seleciona as frases mais relevantes
    para a query após deduplicação interna.
    """
    if not sentences or embeddings.size == 0:
        return ""

    # deduplicar internamente
    keep: list[int] = []
    for i in range(len(sentences)):
        if not any(
            float(cosine_similarity([embeddings[i]], [embeddings[j]])[0][0]) > dedup_threshold
            for j in keep
        ):
            keep.append(i)

    sentences  = [sentences[i] for i in keep]
    embeddings = embeddings[keep]

    if len(sentences) <= max_sentences:
        return " ".join(sentences)

    sims    = cosine_similarity(embeddings, [query_emb]).flatten()
    top_idx = sims.argsort()[-max_sentences:][::-1]
    selected = [sentences[i] for i in sorted(top_idx)]
    return " ".join(selected)

# ── Chunk fusion ──────────────────────────────────────────────────────────────

def fuse_chunks(
    chunks: list[str],
    embeddings: np.ndarray,
    threshold: float = 0.80,
) -> tuple[list[str], np.ndarray]:
    """
    Funde chunks semanticamente próximos em um único chunk.
    Reduz fragmentação sem perda semântica relevante.
    Retorna (chunks_fundidos, embeddings_fundidos).
    """
    if not chunks or len(chunks) == 1:
        return chunks, embeddings

    used    = [False] * len(chunks)
    fused_c = []
    fused_e = []

    for i in range(len(chunks)):
        if used[i]:
            continue
        group_texts = [chunks[i]]
        group_embs  = [embeddings[i]]
        used[i]     = True

        for j in range(i + 1, len(chunks)):
            if used[j]:
                continue
            sim = float(cosine_similarity([embeddings[i]], [embeddings[j]])[0][0])
            if sim >= threshold:
                group_texts.append(chunks[j])
                group_embs.append(embeddings[j])
                used[j] = True

        fused_text = " ".join(group_texts)
        fused_emb  = np.mean(group_embs, axis=0)
        # renormaliza
        norm = np.linalg.norm(fused_emb)
        if norm > 0:
            fused_emb = fused_emb / norm

        fused_c.append(fused_text)
        fused_e.append(fused_emb)

    return fused_c, np.vstack(fused_e) if fused_e else np.array([])

# ── Abstração semântica ────────────────────────────────────────────────────────

def semantic_abstract(texts: list[str], max_ratio: float = COMPRESSION_MAX_RATIO) -> str:
    """
    Abstração leve: remove frases com menor diversidade lexical
    até atingir max_ratio de compressão.
    Preserva ordem original das frases sobreviventes.
    """
    if not texts:
        return ""

    all_sentences: list[tuple[int, str]] = []
    for text in texts:
        for sent in _sentences(text):
            all_sentences.append((len(all_sentences), sent))

    if not all_sentences:
        return ""

    # score por diversidade lexical (type-token ratio)
    scored = []
    for idx, sent in all_sentences:
        toks  = _tokens(sent)
        score = len(set(toks)) / max(len(toks), 1)
        scored.append((score, idx, sent))

    scored.sort(reverse=True)

    total_tokens  = sum(len(_tokens(s)) for _, _, s in scored)
    target_tokens = int(total_tokens * (1 - max_ratio))
    target_tokens = max(target_tokens, 10)

    kept: list[tuple[int, str]] = []
    running = 0
    for score, idx, sent in scored:
        t = len(_tokens(sent))
        if running + t <= total_tokens and running < target_tokens + t:
            kept.append((idx, sent))
            running += t
        if running >= target_tokens:
            break

    # restaura ordem original
    kept.sort(key=lambda x: x[0])
    return " ".join(s for _, s in kept)

# ── Compressão hierárquica ────────────────────────────────────────────────────

def hierarchical_compress(
    texts: list[str],
    embeddings: np.ndarray,
    query_emb: np.ndarray,
    target_ratio: float = COMPRESSION_MAX_RATIO,
) -> tuple[str, dict]:
    """
    Compressão em dois estágios:
    1. Funde chunks próximos
    2. Sumarização extrativa no resultado

    Retorna (texto_comprimido, métricas).
    """
    tokens_in = sum(token_count(t) for t in texts)

    # estágio 1 — fusão
    fused_texts, fused_embs = fuse_chunks(texts, embeddings, threshold=0.78)

    # estágio 2 — sumarização
    sentences_all: list[str] = []
    embs_all:      list[np.ndarray] = []
    for ft, fe in zip(fused_texts, fused_embs):
        for sent in _sentences(ft):
            sentences_all.append(sent)
            embs_all.append(fe)  # aproximação: usa emb do chunk pai

    if sentences_all:
        embs_matrix = np.vstack(embs_all)
        max_s       = max(1, int(len(sentences_all) * (1 - target_ratio)))
        compressed  = extractive_summarize(
            sentences_all, embs_matrix, query_emb, max_sentences=max_s
        )
    else:
        compressed = " ".join(fused_texts)

    tokens_out = token_count(compressed)
    ratio      = round((tokens_in - tokens_out) / max(tokens_in, 1), 4)

    metrics = {
        "tokens_in":       tokens_in,
        "tokens_out":      tokens_out,
        "compression_ratio": ratio,
        "chunks_before":   len(texts),
        "chunks_after":    len(fused_texts),
        "fusion_ratio":    round(1 - len(fused_texts) / max(len(texts), 1), 4),
    }

    return compressed, metrics

