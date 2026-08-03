"""
edp.api.routes.live_feed — API HTTP de consulta sobre eventos do live feed.

Complementa o WebSocket /stream (edp/ingest/websocket_receiver.py): aqui o
agente EDP (ou qualquer cliente HTTP) consulta o que já foi recebido e
consolidado. Ver WEBSOCKET_API.md para o contrato completo.

Nota de roteamento: este router precisa ser registrado em main.py ANTES de
memory.router — GET /memory/sessions (2 segmentos de path) colide com o
catch-all GET /memory/{entry_id} (também 2 segmentos) de edp/api/routes/
memory.py, e o Starlette resolve por ordem de registro no app, não por
especificidade.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ...ingest.events import registry_key, sanitize_id
from ...ingest.session_index import get_session_index
from ...runtime import get_error, get_memory, is_valid

router = APIRouter(tags=["live_feed"])


@router.get("/memory/sessions")
async def list_sessions(profile_id: str = Query(..., min_length=1)):
    """Lista as conversation_id (sessões) já vistas para um profile_id."""
    if sanitize_id(profile_id) is None:
        raise HTTPException(400, "profile_id inválido")
    sessions = get_session_index().list_conversations(profile_id)
    return {"profile_id": profile_id, "sessions": sessions, "count": len(sessions)}


@router.get("/memory/session/{session_id}")
async def get_session_events(
    session_id: str,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """
    Retorna os eventos do live feed de uma conversa, ordenados por
    event_timestamp (o relógio do sensor, não o de chegada no EDP).

    `session_id` aqui é o conversation_id do evento original.
    """
    if sanitize_id(session_id) is None:
        raise HTTPException(400, "session_id inválido")

    mem = get_memory(registry_key(session_id))
    if not is_valid(mem):
        raise HTTPException(503, get_error(mem) or "memória indisponível")

    entries = [
        {k: v for k, v in e.items() if k != "embedding"}
        for e in mem.episodic.entries
        if e.get("source") == "live_feed"
    ]
    entries.sort(key=lambda e: e.get("event_timestamp", 0))
    page = entries[offset:offset + limit]

    return {
        "session_id": session_id,
        "count":      len(page),
        "total":      len(entries),
        "events":     page,
    }


@router.get("/memory/session/{session_id}/summary")
async def get_session_summary(session_id: str):
    """Retorna o resumo mais recente gerado por consolidate_session() para a conversa."""
    if sanitize_id(session_id) is None:
        raise HTTPException(400, "session_id inválido")

    mem = get_memory(registry_key(session_id))
    if not is_valid(mem):
        raise HTTPException(503, get_error(mem) or "memória indisponível")

    candidates = [
        e for e in (mem.episodic.entries + mem.semantic.entries)
        if e.get("source_type") == "session_summary"
    ]
    if not candidates:
        raise HTTPException(404, f"nenhum resumo disponível para a sessão '{session_id}'")

    latest = max(candidates, key=lambda e: e.get("timestamp", 0))
    return {
        "session_id": session_id,
        "summary":    latest.get("text", "").removeprefix("[session_summary] "),
        "topic_tag":  latest.get("topic_tag"),
        "source":     latest.get("source"),
        "layer":      latest.get("layer", "episodic"),
        "timestamp":  latest.get("timestamp"),
    }
