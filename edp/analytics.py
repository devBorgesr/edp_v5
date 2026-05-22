"""
analytics.py (PATCHED)
[P-F8] working_size: len(getattr(ms,"working",[])) → len(ms.working) via __len__
       WorkingMemory é objeto com __len__, não list. Default [] retornava 0 sempre.
"""
"""
analytics.py — Observabilidade cognitiva do EDP v3.

Correções vs versão original:
  [FIX-1] memory_store.semantic.entries não existe em SemanticMemory →
           corrigido para memory_store.semantic._concepts (lista de Concept)
  [FIX-2] memory_distribution: idem para semantic layer
  [FIX-3] _health e cálculos: guardam contra divisão por zero e lista vazia
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from .temporal import decay, age_days
from .clock import now as _now  # Peça 0.2b — relógio interno robusto
from . import metrics as M


# ── Helpers ───────────────────────────────────────────────────────────────────

def _concept_to_entry(concept) -> dict:
    """
    [FIX-1] Converte Concept dataclass para dict compatível com analytics.
    Funciona com Concept e com dict (ambas as formas existem no codebase).
    """
    if isinstance(concept, dict):
        return concept
    return {
        "id":            getattr(concept, "id", ""),
        "text":          getattr(concept, "text", ""),
        "prioridade":    "alta" if getattr(concept, "confidence", 0.5) > 0.70 else "media",
        "score_inicial": getattr(concept, "effective_confidence", 0.5),
        "acessos":       getattr(concept, "reinforced", 0),
        "timestamp":     getattr(concept, "created_at", _now()),
        "ultimo_acesso": getattr(concept, "last_seen", _now()),
    }


def _semantic_entries(memory_store) -> list:
    """[FIX-1] Acessa entradas da SemanticMemory de forma segura."""
    sm = getattr(memory_store, "semantic", None)
    if sm is None:
        return []
    # SemanticMemory usa _concepts (lista de Concept)
    concepts = getattr(sm, "_concepts", None)
    if concepts is not None:
        return [_concept_to_entry(c) for c in concepts]
    # Fallback: se por algum motivo .entries existir (memória episódica promovida)
    entries = getattr(sm, "entries", None)
    if entries is not None:
        return list(entries)
    return []


# ── Health report ─────────────────────────────────────────────────────────────

def memory_health_report(memory_store) -> dict:
    ep  = memory_store.episodic.entries
    sm  = _semantic_entries(memory_store)  # [FIX-1]
    now = _now()

    decays    = [decay(e.get("ultimo_acesso", now)) for e in ep]
    avg_decay = round(sum(decays) / max(len(decays), 1), 4)
    critical  = sum(1 for d in decays if d < 0.10)

    all_entries = ep + sm
    prio_counts = {"alta": 0, "media": 0, "baixa": 0}
    score_sum   = 0.0
    for e in all_entries:
        prio = e.get("prioridade", "media")
        prio_counts[prio] = prio_counts.get(prio, 0) + 1
        score_sum += e.get("score_inicial", 0.5)

    avg_score   = round(score_sum / max(len(all_entries), 1), 4)
    avg_acessos = round(
        sum(e.get("acessos", 0) for e in ep) / max(len(ep), 1), 2
    )

    return {
        "session_id":    memory_store.session_id,
        "working_size":  (len(memory_store.working) if hasattr(memory_store, "working") and memory_store.working is not None else 0),  # [P-F8]
        "episodic_size": len(ep),
        "semantic_size": len(sm),
        "total":         len(ep) + len(sm),
        "avg_decay":     avg_decay,
        "critical_count": critical,
        "priority_dist": prio_counts,
        "avg_score":     avg_score,
        "avg_acessos":   avg_acessos,
        "health_score":  _health(avg_decay, critical, len(ep)),
    }


def _health(avg_decay: float, critical: int, total: int) -> str:
    if total == 0:
        return "empty"
    r = critical / total
    if avg_decay >= 0.70 and r < 0.10:
        return "good"
    if avg_decay >= 0.40 and r < 0.30:
        return "fair"
    return "degraded"


# ── Distribution ──────────────────────────────────────────────────────────────

def memory_distribution(memory_store) -> dict:
    now = _now()

    def _bucket_age(age: float) -> str:
        if age < 1:   return "<1d"
        if age < 7:   return "1-7d"
        if age < 30:  return "7-30d"
        return ">30d"

    age_d: dict[str, int] = {}
    acc_d = {"0": 0, "1-5": 0, "6-20": 0, ">20": 0}

    for e in memory_store.episodic.entries:
        b = _bucket_age(age_days(e.get("timestamp", now)))
        age_d[b] = age_d.get(b, 0) + 1
        a = e.get("acessos", 0)
        if a == 0:       acc_d["0"]    += 1
        elif a <= 5:     acc_d["1-5"]  += 1
        elif a <= 20:    acc_d["6-20"] += 1
        else:            acc_d[">20"]  += 1

    sm = _semantic_entries(memory_store)  # [FIX-2]

    return {
        "episodic": {
            "total":       len(memory_store.episodic.entries),
            "age_dist":    age_d,
            "access_dist": acc_d,
        },
        "semantic": {
            "total":     len(sm),
            "high_prio": sum(1 for e in sm if e.get("prioridade") == "alta"),
        },
        "working": {
            "size": (len(memory_store.working) if hasattr(memory_store, "working") and memory_store.working is not None else 0),  # [P-F8]
        },
    }


# ── Semantic drift ────────────────────────────────────────────────────────────

def semantic_drift(memory_store, window: int = 20) -> dict:
    entries = memory_store.episodic.entries
    if len(entries) < window * 2:
        return {
            "drift_score": 0.0,
            "status":      "insufficient_data",
            "n_entries":   len(entries),
            "window":      window,
        }

    sorted_e = sorted(entries, key=lambda e: e.get("timestamp", 0))

    def _to_emb(e: dict) -> Optional[np.ndarray]:
        emb = e.get("embedding")
        if emb is None:
            return None
        try:
            arr = np.array(emb, dtype=np.float32)
            return arr if arr.ndim == 1 and arr.size > 0 else None
        except Exception:
            return None

    old_arrs = [_to_emb(e) for e in sorted_e[:window]]
    new_arrs = [_to_emb(e) for e in sorted_e[-window:]]
    old_arrs = [a for a in old_arrs if a is not None]
    new_arrs = [a for a in new_arrs if a is not None]

    if not old_arrs or not new_arrs:
        return {
            "drift_score": 0.0,
            "status":      "insufficient_embeddings",
            "n_entries":   len(entries),
            "window":      window,
        }

    old_embs = np.vstack(old_arrs)
    new_embs = np.vstack(new_arrs)
    oc = old_embs.mean(axis=0)
    nc = new_embs.mean(axis=0)

    for c in (oc, nc):
        n = np.linalg.norm(c)
        if n > 0:
            c /= n

    sim    = float(cosine_similarity([oc], [nc])[0][0])
    drift  = round(1.0 - sim, 4)
    status = (
        "stable"        if drift < 0.10 else
        "moderate_drift" if drift < 0.30 else
        "high_drift"
    )

    return {
        "drift_score": drift,
        "similarity":  round(sim, 4),
        "status":      status,
        "n_entries":   len(entries),
        "window":      window,
    }


# ── Growth stats ──────────────────────────────────────────────────────────────

def growth_stats(metrics_log_path: Optional[str] = None) -> dict:
    from .config import METRICS_LOG
    log_path = Path(metrics_log_path or METRICS_LOG)

    if not log_path.exists():
        return {"status": "no_metrics_log"}

    mem_s: List[float] = []
    comp:  List[float] = []
    hits:  List[float] = []
    lats:  List[float] = []

    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            e  = json.loads(line)
            ev = e.get("event", "")
            if ev == "memory_size":         mem_s.append(e["value"])
            elif ev == "compression_ratio": comp.append(e["value"])
            elif ev == "cache_hit":         hits.append(e["value"])
            elif ev == "latency":           lats.append(e.get("ms", 0))
        except Exception:
            pass

    def _s(v: List[float]) -> dict:
        if not v:
            return {"count": 0}
        return {
            "count": len(v),
            "mean":  round(sum(v) / len(v), 4),
            "min":   round(min(v), 4),
            "max":   round(max(v), 4),
            "last":  round(v[-1], 4),
        }

    trend = "stable"
    if len(mem_s) >= 10:
        h = len(mem_s) // 2
        f = sum(mem_s[:h]) / h
        s = sum(mem_s[h:]) / (len(mem_s) - h)
        trend = (
            "growing_fast" if s > f * 1.5 else
            "growing"      if s > f * 1.1 else
            "stable"
        )

    return {
        "memory_size":         _s(mem_s),
        "compression_ratio":   _s(comp),
        "cache_hit_rate":      _s(hits),
        "avg_latency_ms":      _s(lats),
        "memory_growth_trend": trend,
    }

