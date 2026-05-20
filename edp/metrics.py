"""
metrics.py — Observabilidade completa do EDP v3.
JSONL persistente + summary cross-process + timers contextuais.
"""
import time
import json
from contextlib import contextmanager
from collections import defaultdict

from .config import METRICS_LOG

# estado in-memory do processo atual
_stats: dict[str, list[float]] = defaultdict(list)

# ── Timer contextual ──────────────────────────────────────────────────────────

@contextmanager
def timer(label: str):
    t0 = time.perf_counter()
    yield
    ms = (time.perf_counter() - t0) * 1000
    _stats[f"latency_{label}"].append(round(ms, 2))
    _log({"event": "latency", "label": label, "ms": round(ms, 2)})

# ── Registro de métricas ──────────────────────────────────────────────────────

def record(key: str, value: float) -> None:
    _stats[key].append(value)
    _log({"event": key, "value": value})

def compression_ratio(tokens_in: int, tokens_out: int) -> float:
    if tokens_in == 0:
        return 0.0
    r = round((tokens_in - tokens_out) / tokens_in, 4)
    record("compression_ratio", r)
    return r

def cache_hit(hit: bool) -> None:
    record("cache_hit", int(hit))

def retrieval_score(score: float) -> None:
    record("retrieval_score", score)

def memory_size(n: int) -> None:
    record("memory_size", n)

def consolidation_ratio(before: int, after: int) -> float:
    if before == 0:
        return 0.0
    r = round((before - after) / before, 4)
    record("consolidation_ratio", r)
    return r

def semantic_redundancy(score: float) -> None:
    record("semantic_redundancy", score)

# ── Persistência ──────────────────────────────────────────────────────────────

def _log(entry: dict) -> None:
    METRICS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = time.time()
    with open(METRICS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

# ── Summary ───────────────────────────────────────────────────────────────────

def summary(from_file: bool = True) -> dict:
    """
    from_file=True  → lê do JSONL (cross-process, sempre atualizado).
    from_file=False → usa estado in-memory do processo atual.
    """
    return _summary_from_file() if from_file else _summary_from_memory()

def _summary_from_file() -> dict:
    if not METRICS_LOG.exists():
        return {}
    agg: dict[str, list[float]] = defaultdict(list)
    for line in METRICS_LOG.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
            if e.get("event") == "latency":
                agg[f"latency_{e['label']}"].append(e["ms"])
            else:
                agg[e["event"]].append(e["value"])
        except Exception:
            pass
    return _format(agg)

def _summary_from_memory() -> dict:
    return _format(_stats)

def _format(agg: dict) -> dict:
    return {
        key: {
            "count": len(vals),
            "mean":  round(sum(vals) / len(vals), 4),
            "min":   round(min(vals), 4),
            "max":   round(max(vals), 4),
        }
        for key, vals in agg.items()
        if vals
    }

def reset() -> None:
    """Zera estado in-memory (não apaga JSONL)."""
    _stats.clear()

