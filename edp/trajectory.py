"""
edp.trajectory — Detecta padrões observáveis nas memórias episódicas.

Versão simplificada v3.12.2: removidas inferências que dependiam de
embeddings ortogonais (raízes, abandonados, profundidade). Mantidas
apenas as funções que funcionam de forma robusta com MiniLM:

  - Loops: clusters densos com retornos espaçados no tempo
  - Folhas recentes: últimas memórias da sessão (visão "onde você parou")

Princípios:
  - Não modifica memórias (puramente analítico)
  - Sem chamada de LLM
  - Robusto a embeddings imperfeitos (não depende de temas ortogonais)
"""
from __future__ import annotations
from typing import List, Dict, Optional
import logging
import time as _time

from .clock import now as _now  # Peça 0.2b — relógio interno robusto

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger("edp.trajectory")


# ── Parâmetros calibráveis ────────────────────────────────────────────────────
LOOP_SIM_THRESHOLD = 0.65        # similaridade interna para considerar cluster
LOOP_MIN_REVISITS = 3            # mínimo de retornos no cluster
LOOP_MIN_TIME_GAPS = 2           # gaps temporais (>1h) que indicam retorno


def _to_array(emb) -> Optional[np.ndarray]:
    """Converte embedding para np.array. Retorna None se inválido."""
    if emb is None:
        return None
    if isinstance(emb, np.ndarray):
        return emb if emb.size > 0 else None
    if isinstance(emb, list) and emb:
        return np.array(emb, dtype=np.float32)
    return None


def _short_text(text: str, limit: int = 80) -> str:
    """Snippet curto para visualização."""
    if not text:
        return ""
    t = text.replace("\n", " ").strip()
    if t.startswith("Q:"):
        end = t.find("A:")
        if end > 5:
            t = t[2:end].strip()
    return t[:limit].strip()


def build_trajectory(memory_store) -> dict:
    """
    Detecta padrões observáveis em memórias episódicas.

    Args:
        memory_store: MemoryStore com .episodic.entries

    Returns:
        dict com:
            - leaves: últimas memórias da sessão (onde você parou)
            - loops: clusters revisitados ao longo do tempo
            - stats: contadores
    """
    if memory_store is None or not hasattr(memory_store, "episodic"):
        return _empty_result()

    try:
        entries = list(memory_store.episodic.entries)
        valid = []
        for e in entries:
            emb = _to_array(e.get("embedding"))
            ts = e.get("timestamp")
            if emb is not None and ts is not None:
                valid.append({
                    "id":          e.get("id"),
                    "text":        e.get("text", ""),
                    "timestamp":   float(ts),
                    "embedding":   emb,
                    "source_type": e.get("source_type", "unknown"),
                    "status":      e.get("epistemic_status", "hypothesis"),
                    "acessos":     e.get("acessos", 0),
                })

        if len(valid) < 2:
            return _empty_result()

        valid.sort(key=lambda x: x["timestamp"])
        now = _now()

        # ── Folhas: últimas memórias por timestamp ────────────────────
        leaves = []
        for v in sorted(valid, key=lambda x: -x["timestamp"])[:10]:
            leaves.append({
                "id":         v["id"],
                "text":       _short_text(v["text"]),
                "timestamp":  v["timestamp"],
                "age_hours":  (now - v["timestamp"]) / 3600,
                "source_type": v["source_type"],
            })

        # ── Loops ──────────────────────────────────────────────────────
        loops = _detect_loops(valid)

        stats = {
            "total_nodes": len(valid),
            "loops":       len(loops),
            "first_ts":    valid[0]["timestamp"],
            "last_ts":     valid[-1]["timestamp"],
            "span_days":   round((valid[-1]["timestamp"] - valid[0]["timestamp"]) / 86400, 1),
        }

        return {
            "leaves":      leaves,
            "loops":       loops,
            "stats":       stats,
            "computed_at": now,
        }

    except Exception as e:
        logger.exception("[trajectory] build_trajectory falhou: %s", e)
        return _empty_result()


def _detect_loops(valid: List[dict]) -> List[dict]:
    """
    Detecta clusters densos onde o usuário retorna repetidamente.

    Heurística:
      - Agrupa memórias com similaridade cruzada alta (>0.65)
      - Cluster precisa ter ≥3 membros
      - Precisa ter ≥2 gaps temporais grandes (>1h) entre acessos
        (indica retornos espaçados, não apenas conversa contínua)
    """
    if len(valid) < 3:
        return []

    embeddings = np.vstack([v["embedding"] for v in valid])

    # Para sessões muito grandes, sampleia para evitar O(n²)
    if len(valid) > 500:
        idx = np.random.RandomState(42).choice(len(valid), 500, replace=False)
        idx.sort()
        valid_sub = [valid[i] for i in idx]
        embeddings_sub = embeddings[idx]
    else:
        valid_sub = valid
        embeddings_sub = embeddings

    sim_matrix = cosine_similarity(embeddings_sub)
    np.fill_diagonal(sim_matrix, 0)

    clusters = []
    visited_cluster = set()

    for i in range(len(valid_sub)):
        if i in visited_cluster:
            continue
        neighbors = np.where(sim_matrix[i] > LOOP_SIM_THRESHOLD)[0]
        if len(neighbors) < LOOP_MIN_REVISITS - 1:
            continue
        cluster = [i] + neighbors.tolist()

        # Filtro: retornos ESPAÇADOS NO TEMPO
        cluster_ts = sorted([valid_sub[c]["timestamp"] for c in cluster])
        time_spans = [cluster_ts[k+1] - cluster_ts[k] for k in range(len(cluster_ts)-1)]
        big_gaps = sum(1 for g in time_spans if g > 3600)
        if big_gaps >= LOOP_MIN_TIME_GAPS:
            clusters.append(cluster)
            visited_cluster.update(cluster)

    loops = []
    for cluster in clusters[:8]:
        cluster_entries = [valid_sub[c] for c in cluster]
        cluster_entries.sort(key=lambda x: x["timestamp"])
        loops.append({
            "size":      len(cluster_entries),
            "first":     _short_text(cluster_entries[0]["text"], limit=80),
            "first_ts":  cluster_entries[0]["timestamp"],
            "last_ts":   cluster_entries[-1]["timestamp"],
            "span_days": round((cluster_entries[-1]["timestamp"] -
                                cluster_entries[0]["timestamp"]) / 86400, 1),
            "ids":       [v["id"] for v in cluster_entries],
        })

    loops.sort(key=lambda l: -l["size"])
    return loops


def _empty_result() -> dict:
    """Retorno default quando não há dados suficientes."""
    return {
        "leaves":      [],
        "loops":       [],
        "stats":       {
            "total_nodes": 0,
            "loops":       0,
            "first_ts":    None,
            "last_ts":     None,
            "span_days":   0,
        },
        "computed_at": _now(),
    }
