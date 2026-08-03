"""
edp.ingest.events — validação e mapeamento de eventos do live feed.

Funções puras (sem I/O): validam o formato do envelope recebido no
WebSocket /stream e extraem os campos usados para roteamento/armazenamento.
Nenhuma função aqui toca disco, rede ou o registry de sessões — isso mora
em websocket_receiver.py.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

# Campos obrigatórios do envelope (LIVE_EVENTS.md / WEBSOCKET_API.md).
REQUIRED_FIELDS = ("type", "timestamp", "conversation_id", "data")

# Allow-list estrita: conversation_id/session_id/profile_id viram nomes de
# diretório (MEMORY_DIR/<key>_<scope>/...) rio abaixo em MemoryStore. O
# WebSocket é alcançável por uma extensão externa e, por padrão
# (EDP_LIVE_FEED_TOKEN vazio), sem autenticação — nunca deixar esses valores
# tocarem um path sem passar por aqui.
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")

# Prefixo de namespace para nunca colidir com session_ids reais do EDP
# (ex.: "default") usados pelo chat/dashboard.
_REGISTRY_PREFIX = "livefeed_"

# Chaves tentadas, em ordem, para extrair um texto curto de `event["data"]`
# a ser embedado/indexado. Genérico o suficiente para cobrir thinking,
# respostas e tool calls sem acoplar a um formato específico de extensão.
_TEXT_KEYS = ("text", "content", "message", "response", "output", "summary")


def sanitize_id(raw: Any) -> Optional[str]:
    """Retorna `raw` se for uma string que bate com o allow-list, senão None."""
    if not isinstance(raw, str):
        return None
    if not _ID_RE.match(raw):
        return None
    return raw


def _get_field(event: dict, name: str) -> Any:
    """Busca `name` no nível superior do evento; se ausente, tenta em `data`."""
    if name in event and event[name] is not None:
        return event[name]
    data = event.get("data")
    if isinstance(data, dict):
        return data.get(name)
    return None


def validate_event(raw: Any) -> tuple[bool, Optional[str]]:
    """
    Valida o formato de um evento recebido no /stream.

    Retorna (True, None) se válido, ou (False, motivo) se inválido. Nunca
    lança exceção — chamadores devem tratar qualquer entrada, mesmo lixo.
    """
    if not isinstance(raw, dict):
        return False, "evento não é um objeto JSON"

    for field in REQUIRED_FIELDS:
        if field not in raw or raw[field] in (None, ""):
            return False, f"campo obrigatório ausente: {field}"

    if not isinstance(raw["type"], str) or not raw["type"].strip():
        return False, "campo 'type' deve ser string não-vazia"

    if not isinstance(raw["timestamp"], (int, float)):
        return False, "campo 'timestamp' deve ser numérico"

    if not isinstance(raw["data"], dict):
        return False, "campo 'data' deve ser um objeto"

    if sanitize_id(raw["conversation_id"]) is None:
        return False, "conversation_id inválido (esperado [A-Za-z0-9_-]{1,128})"

    session_id = _get_field(raw, "session_id")
    if session_id is not None and sanitize_id(session_id) is None:
        return False, "session_id inválido (esperado [A-Za-z0-9_-]{1,128})"

    profile_id = _get_field(raw, "profile_id")
    if profile_id is not None and sanitize_id(profile_id) is None:
        return False, "profile_id inválido (esperado [A-Za-z0-9_-]{1,128})"

    return True, None


def extract_text(event: dict) -> str:
    """
    Extrai um texto curto e representativo de `event["data"]` para
    embedding/retrieval. Best-effort e genérico: tenta chaves comuns antes
    de cair para um dump truncado de `data`.
    """
    data = event.get("data")
    if isinstance(data, dict):
        for key in _TEXT_KEYS:
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:2000]
        try:
            dumped = json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError):
            dumped = str(data)
        return dumped[:2000]
    if isinstance(data, str) and data.strip():
        return data.strip()[:2000]
    return f"[evento {event.get('type', 'unknown')} sem conteúdo textual]"


def registry_key(conversation_id: str) -> str:
    """Chave usada em get_memory()/get_runtime() para uma conversa do live feed."""
    return f"{_REGISTRY_PREFIX}{conversation_id}"


def extract_ids(event: dict) -> dict[str, Optional[str]]:
    """
    Extrai conversation_id/session_id/profile_id de um evento JÁ validado
    por validate_event() (não revalida — chamador garante isso).
    """
    return {
        "conversation_id": event["conversation_id"],
        "session_id":      _get_field(event, "session_id"),
        "profile_id":      _get_field(event, "profile_id"),
    }
