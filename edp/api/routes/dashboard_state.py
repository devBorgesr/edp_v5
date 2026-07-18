"""
edp.api.routes.dashboard_state — Endpoint consolidado para o dashboard.

Reduz 4 requisições (health + memory + metrics + snapshot) em 1.

v3.4 — sprint estabilidade:
  Adiciona pressure (RAM), queue (inferência), boot_state (COLD/READY/DEGRADED)
"""
from __future__ import annotations

from fastapi import APIRouter

from ...clock import now as _now
from ...runtime import get_memory, get_runtime, is_valid, list_sessions

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/state")
async def dashboard_state(session_id: str = "default"):
    """
    Estado completo do dashboard em UMA chamada.
    Substitui polling de /health + /memory + /metrics + /snapshot.
    """
    out = {
        "timestamp": _now(),
        "session_id": session_id,
    }

    # ── Boot state (v3.4) ────────────────────────────────────────────────────
    try:
        from ...runtime.boot_state import get_boot_state
        out["runtime_state"] = get_boot_state().to_dict()
    except Exception as e:
        out["runtime_state"] = {"state": "unknown", "error": str(e)}

    # ── Memory pressure (v3.4) ───────────────────────────────────────────────
    try:
        from ...runtime.pressure_governor import get_governor
        out["pressure"] = get_governor().read().to_dict()
    except Exception as e:
        out["pressure"] = {"level": "unknown", "error": str(e)}

    # ── Inference queue (v3.4) ───────────────────────────────────────────────
    try:
        from ...runtime.inference_queue import get_queue
        out["queue"] = get_queue().stats.to_dict()
    except Exception as e:
        out["queue"] = {"error": str(e)}

    # ── P2: Retrieval quality (v3.5) ─────────────────────────────────────────
    try:
        from ...runtime.retrieval_monitor import get_monitor
        out["retrieval_quality"] = get_monitor().snapshot()
    except Exception as e:
        out["retrieval_quality"] = {"error": str(e)}

    # ── P3: Contradiction flags (v3.5) ───────────────────────────────────────
    try:
        from ...runtime.contradiction_flagger import get_flagger
        flagger = get_flagger()
        out["contradictions"] = {
            "stats":  flagger.stats(),
            "recent": flagger.recent_flags(n=5),
        }
    except Exception as e:
        out["contradictions"] = {"error": str(e)}

    # ── Health legacy ────────────────────────────────────────────────────────
    try:
        from ... import metrics as M
        out["health"] = {
            "status":   out["runtime_state"].get("state", "ok"),
            "version":  "3.5.0",
            "sessions": list_sessions(),
        }
        out["system_metrics"] = M.summary(from_file=False)
    except Exception as e:
        out["health"] = {"status": "degraded", "error": str(e)}
        out["system_metrics"] = {}

    # ── Memory stats ─────────────────────────────────────────────────────────
    mem = get_memory(session_id)
    if is_valid(mem):
        try:
            out["memory"] = mem.stats()
        except Exception as e:
            out["memory"] = {"error": str(e)}
    else:
        out["memory"] = {"error": "unavailable"}

    # ── Runtime + LLM metrics ────────────────────────────────────────────────
    rt = get_runtime(session_id)
    if is_valid(rt):
        try:
            out["llm_metrics"] = rt.llm_metrics()
        except Exception:
            out["llm_metrics"] = {}
    else:
        out["llm_metrics"] = {}

    return out
