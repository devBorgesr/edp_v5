"""edp.api.routes.health — Endpoint de health check."""
from __future__ import annotations

import time
from fastapi import APIRouter

from ...runtime import list_sessions, stats as registry_stats
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health():
    """Health completo do runtime cognitivo."""
    try:
        from ... import metrics as M
        m = M.summary(from_file=False)
    except Exception:
        m = {}

    return HealthResponse(
        status="ok",
        version="3.3.0",
        timestamp=time.time(),
        sessions=list_sessions(),
        metrics=m,
    )


@router.get("/health/registry")
async def health_registry():
    """Diagnóstico do registry de sessões (debug)."""
    return registry_stats()
