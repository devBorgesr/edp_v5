"""edp.api.routes.health — Endpoint de health check."""
from __future__ import annotations

import os
import time
from fastapi import APIRouter, Response

from ...runtime import list_sessions, stats as registry_stats
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(response: Response):
    """
    Health real do runtime cognitivo (Hardening Fase 3, T2) — sem chamada
    de rede, 3 checks:
      - boot_state: BootStateMachine já mantido pelo lifespan (main.py);
        COLD/WARMING/SHUTDOWN -> 503 (contrato documentado em
        edp/runtime/boot_state.py, não estava exposto aqui antes).
      - memory_store: sessão 'default' carregou (get_memory + is_valid,
        registry já cacheado — não relê disco a cada chamada de /health).
      - provider: pelo menos um provider LLM registrado E api key presente
        no ambiente. NÃO chama provider.validate() (isso pinga a rede;
        ver /providers/validate) — só confere config presente.
    """
    try:
        from ... import metrics as M
        m = M.summary(from_file=False)
    except Exception:
        m = {}

    from ...runtime.boot_state import get_boot_state, RuntimeState
    boot = get_boot_state()
    boot_state = boot.state

    from ...runtime import get_memory, is_valid, get_error
    mem = get_memory("default")
    store_ok = is_valid(mem)

    from ...llm.providers import list_providers
    providers_available = list_providers()
    provider_ok = bool(providers_available and os.environ.get("ANTHROPIC_API_KEY"))

    checks = {
        "boot_state": boot.to_dict(),
        "memory_store": {
            "ok": store_ok,
            "error": get_error(mem) if not store_ok else None,
        },
        "provider": {
            "ok": provider_ok,
            "available": providers_available,
            "has_anthropic_key_in_env": bool(os.environ.get("ANTHROPIC_API_KEY")),
        },
    }

    if not boot.is_ready():
        status = "shutdown" if boot_state == RuntimeState.SHUTDOWN else "starting"
        response.status_code = 503
    elif boot_state == RuntimeState.DEGRADED or not (store_ok and provider_ok):
        status = "degraded"
    else:
        status = "ok"

    return HealthResponse(
        status=status,
        version="3.3.0",
        timestamp=time.time(),
        sessions=list_sessions(),
        metrics=m,
        boot_state=boot_state.value,
        checks=checks,
    )


@router.get("/health/registry")
async def health_registry():
    """Diagnóstico do registry de sessões (debug)."""
    return registry_stats()
