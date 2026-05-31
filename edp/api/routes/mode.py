"""
edp.api.routes.mode — Endpoint HTTP para troca de modo operacional (peça 2.6a).

Permite trocar/consultar o modo operacional via HTTP além do comando /mode no
chat WebSocket. Abre porta para botão de toggle no dashboard sem refactor.

Endpoints:
    GET  /mode/current             → modo atual da sessão
    POST /mode/{name}              → troca para 'sprint' ou 'cognitive'
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ...runtime import get_runtime, is_valid, get_error

router = APIRouter(tags=["mode"], prefix="/mode")


@router.get("/current")
async def get_current_mode(session_id: str = Query("default")):
    """Retorna o modo operacional atual da sessão."""
    runtime = get_runtime(session_id)
    if not is_valid(runtime):
        raise HTTPException(503, get_error(runtime) or "Runtime indisponível")

    if not hasattr(runtime, "get_operational_mode"):
        raise HTTPException(501, "Runtime sem suporte a modo operacional")

    return {
        "session_id": session_id,
        "mode":       runtime.get_operational_mode(),
    }


@router.post("/{name}")
async def set_mode(name: str, session_id: str = Query("default")):
    """
    Troca o modo operacional.

    name aceita: 'cognitive' ou 'sprint' (case-insensitive).
    """
    runtime = get_runtime(session_id)
    if not is_valid(runtime):
        raise HTTPException(503, get_error(runtime) or "Runtime indisponível")

    if not hasattr(runtime, "set_operational_mode"):
        raise HTTPException(501, "Runtime sem suporte a modo operacional")

    result = runtime.set_operational_mode(name)
    if not result.get("ok"):
        raise HTTPException(400, result.get("message", "modo inválido"))

    return {
        "session_id": session_id,
        "mode":       result.get("mode"),
        "previous":   result.get("previous"),
        "message":    result.get("message"),
    }
