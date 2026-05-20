"""edp.api.routes.metrics — Endpoints de métricas e snapshots."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...runtime import get_runtime, is_valid

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def get_metrics():
    """Métricas de performance agregadas do sistema."""
    try:
        from ... import metrics as M
        return M.summary(from_file=True)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/snapshot")
async def get_snapshot(session_id: str = "default"):
    """Estado atual da sessão: memória + métricas LLM."""
    runtime = get_runtime(session_id)
    if not is_valid(runtime):
        return {"error": "runtime indisponível", "session_id": session_id}
    try:
        health = runtime.health()
        return {
            "session_id": session_id,
            "memory":     health.get("memory", {}),
            "metrics":    health.get("metrics", {}),
        }
    except Exception:
        return {"session_id": session_id, "error": "snapshot indisponível"}


@router.get("/modes")
async def get_modes():
    """Histórico de modos operacionais (últimos 20 do metrics log)."""
    try:
        from ...config import METRICS_LOG
        from pathlib import Path
        import json as _json

        log = Path(METRICS_LOG)
        if not log.exists():
            return {"modes": [], "current": "unknown"}

        modes = []
        for line in log.read_text(encoding="utf-8").splitlines()[-100:]:
            try:
                e = _json.loads(line)
                if "mode" in e.get("event", ""):
                    modes.append(e)
            except Exception:
                continue

        return {"modes": modes[-20:], "total_logged": len(modes)}
    except Exception:
        return {"modes": [], "current": "unknown"}
