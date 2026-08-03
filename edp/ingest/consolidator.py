"""
edp.ingest.consolidator — consolidação automática de uma conversa do live feed.

Disparado (em thread daemon, fora do loop de recepção do WebSocket) quando
chega um evento response_end: reúne os eventos da conversa e gera um resumo,
armazenado na memória semântica.

Reusa edp.session_summary.generate_session_summary() (o mesmo mecanismo já
usado ao desconectar do /ws/chat) quando há um LLM conectado nessa sessão.
Como uma conversa do live feed nunca passa por /connect, isso normalmente
não vale — por isso há um fallback heurístico (extrativo, sem LLM) que
garante que response_end sempre produza um resumo armazenado.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..runtime import get_memory, get_runtime, is_valid, get_error
from ..session_summary import generate_session_summary, _heuristic_label
from .events import registry_key

logger = logging.getLogger("edp.ingest.consolidator")

# Quantas entries recentes considerar no resumo heurístico.
HEURISTIC_LAST_N = 20


def consolidate_session(conversation_id: str) -> Optional[dict]:
    """
    Consolida uma conversa do live feed: gera resumo e promove p/ semântica.

    Returns:
        {"summary", "label", "reused", "entry_id", "method"} ou None se
        não houver o que consolidar (memória indisponível ou vazia).
    """
    key = registry_key(conversation_id)
    mem = get_memory(key)
    if not is_valid(mem):
        logger.warning(
            "[consolidate] memory indisponível conversation_id=%s: %s",
            conversation_id, get_error(mem),
        )
        return None

    rt = get_runtime(key)
    connected = is_valid(rt) and rt.is_connected()

    result: Optional[dict] = None
    if connected:
        try:
            result = generate_session_summary(mem, rt, session_id=key)
        except Exception as e:
            logger.warning(
                "[consolidate] generate_session_summary falhou conversation_id=%s: %s",
                conversation_id, e,
            )
            result = None
        if result is not None:
            result["method"] = "llm"

    if result is None and not connected:
        result = _heuristic_consolidate(mem, conversation_id)

    if result is None:
        logger.debug(
            "[consolidate] nada consolidado conversation_id=%s (connected=%s)",
            conversation_id, connected,
        )
        return None

    try:
        entry = mem.get(result["entry_id"])
        if entry is not None:
            mem.semantic.promote(entry)
            logger.info(
                "[consolidate] resumo promovido p/ semântica conversation_id=%s "
                "method=%s label=%s",
                conversation_id, result.get("method"), result.get("label"),
            )
    except Exception as e:
        logger.warning(
            "[consolidate] promote p/ semântica falhou conversation_id=%s: %s",
            conversation_id, e,
        )

    return result


def _heuristic_consolidate(mem, conversation_id: str) -> Optional[dict]:
    """Resumo extrativo sem LLM: concatena texto recente, sem chamada externa."""
    try:
        entries = [
            e for e in mem.episodic.entries
            if e.get("source_type") != "session_summary"
        ]
    except Exception as e:
        logger.debug("[consolidate] coleta de entries falhou: %s", e)
        return None

    if not entries:
        return None

    texts = [(e.get("text") or "").strip() for e in entries[-HEURISTIC_LAST_N:]]
    texts = [t for t in texts if t]
    if not texts:
        return None

    summary_text = " ".join(texts)[:500]
    label = _heuristic_label(summary_text)

    try:
        entry = mem.add(
            text=f"[session_summary] {summary_text}",
            score=0.5,
            source="system:heuristic_summary",
            confidence=0.3,
        )
    except Exception as e:
        logger.warning("[consolidate] heuristic add falhou: %s", e)
        return None

    # EpisodicMemory.add() copia o dict — mutação precisa ser reaplicada na
    # entry persistida (mesmo padrão de edp/session_summary.py).
    entry_id = entry.get("id")
    entry["source_type"] = "session_summary"
    entry["topic_tag"]   = label
    entry["session_id"]  = conversation_id
    for e in mem.episodic.entries:
        if e.get("id") == entry_id:
            e["source_type"] = "session_summary"
            e["topic_tag"]   = label
            e["session_id"]  = conversation_id
            break
    mem.episodic._dirty = True
    mem.episodic.save()

    return {
        "summary":  summary_text,
        "label":    label,
        "reused":   False,
        "entry_id": entry_id,
        "method":   "heuristic",
    }
