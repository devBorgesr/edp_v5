"""
runtime/registry.py — Registry central de sessões EDP

Centraliza acesso a:
  - EDPRuntime por session_id (LLM adapter + memória + pipeline)
  - MemoryStore por session_id
  - Locks por sessão para concorrência segura

Princípio: SINGLE SOURCE OF TRUTH. Todas as rotas/websockets usam o mesmo
registry, garantindo que /chat, /ws/chat, /memory, /retrieve operam sobre
o MESMO estado da sessão.

Sem isso, cada endpoint poderia criar seu próprio runtime, levando a
estados divergentes e perda de memória entre chamadas.

NÃO esconde erros: se runtime/memory falha, retorna dict com __init_error__.
Verifique sempre com is_valid() antes de usar.
"""
from __future__ import annotations

import logging
import threading
import traceback
from typing import Any, Dict, Optional

logger = logging.getLogger("edp.registry")

# ── Storage global thread-safe ────────────────────────────────────────────────
_runtimes: Dict[str, Any] = {}
_memories: Dict[str, Any] = {}
_session_locks: Dict[str, threading.RLock] = {}
_global_lock = threading.RLock()


def _session_lock(session_id: str) -> threading.RLock:
    """Lock por sessão para operações concorrentes."""
    with _global_lock:
        if session_id not in _session_locks:
            _session_locks[session_id] = threading.RLock()
        return _session_locks[session_id]


def get_runtime(session_id: str) -> Any:
    """
    Retorna EDPRuntime da sessão (cria se não existir).
    Em caso de falha: dict {"__init_error__": str}.
    """
    with _session_lock(session_id):
        if session_id not in _runtimes:
            try:
                from ..llm_adapter import EDPRuntime
                _runtimes[session_id] = EDPRuntime(session_id=session_id)
                logger.info("[registry] runtime inicializado: session=%s", session_id)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                logger.error("[registry] FALHA init runtime session=%s: %s", session_id, err)
                traceback.print_exc()
                _runtimes[session_id] = {"__init_error__": err}
        return _runtimes[session_id]


def get_memory(session_id: str) -> Any:
    """
    Retorna MemoryStore da sessão (cria se não existir).
    Em caso de falha: dict {"__init_error__": str}.
    """
    with _session_lock(session_id):
        if session_id not in _memories:
            try:
                from ..memory import MemoryStore
                _memories[session_id] = MemoryStore(session_id)
                logger.info("[registry] memory inicializado: session=%s", session_id)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                logger.error("[registry] FALHA init memory session=%s: %s", session_id, err)
                traceback.print_exc()
                _memories[session_id] = {"__init_error__": err}
        return _memories[session_id]


def is_valid(obj: Any) -> bool:
    """True se objeto não é um marcador de erro."""
    return obj is not None and not (isinstance(obj, dict) and "__init_error__" in obj)


def get_error(obj: Any) -> Optional[str]:
    """Retorna mensagem de erro se obj é marcador, senão None."""
    if isinstance(obj, dict) and "__init_error__" in obj:
        return obj["__init_error__"]
    return None


def list_sessions() -> list:
    """Lista todas as sessões registradas (runtime + memory)."""
    with _global_lock:
        return sorted(set(_runtimes.keys()) | set(_memories.keys()))


def reset_session(session_id: str) -> bool:
    """
    Remove runtime e memory de uma sessão (força recriação no próximo acesso).
    Útil para testes ou após erros graves.
    """
    with _session_lock(session_id):
        removed = False
        if session_id in _runtimes:
            del _runtimes[session_id]
            removed = True
        if session_id in _memories:
            del _memories[session_id]
            removed = True
        logger.info("[registry] sessão %s resetada (removed=%s)", session_id, removed)
        return removed


def stats() -> dict:
    """Diagnóstico do registry."""
    with _global_lock:
        return {
            "n_runtimes": len(_runtimes),
            "n_memories": len(_memories),
            "sessions":   sorted(_runtimes.keys()),
            "errors":     {
                sid: get_error(rt)
                for sid, rt in _runtimes.items()
                if get_error(rt)
            },
        }
