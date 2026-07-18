"""
edp.runtime.auto_consolidation — Consolidação automática episódica → semântica.

Sprint #43 (11/06/2026):
  A SemanticMemory do scope cognitive ficou ZERADA durante toda a história
  do projeto. Causa (cravada por investigação 10/06/2026): o run_pipeline
  consolidava numa SemanticMemory PARALELA (semantic_memory.py standalone →
  _concepts.json, "coexistem sem sincronização" — docstring do próprio
  módulo), nunca na dos Dois Exocórtices. O único caminho que populava a
  semântica real era o endpoint manual POST /memory/consolidate (que gerou
  as 3 entries do sprint quando acionado via dashboard).

  Este módulo AUTOMATIZA o caminho que já funciona:
  consolidation.consolidate_promote_only(mem) promovendo entries episódicas
  com acessos >= threshold para a semântica do SCOPE ATIVO do MemoryStore.

Princípios aplicados:
  - Reuso de Infraestrutura: zero lógica nova de consolidação — reusa
    consolidation.py (testado, idempotente, dedupe por id, não-destrutivo)
  - Soberania: respeita Dois Exocórtices — opera no scope ativo
    (teste em sprint → semântica do sprint; uso real → cognitive)
  - Critério semântico: promove o que o RETRIEVAL provou ser útil
    (acessos), não o que foi meramente dito

Configuração via env:
  EDP_AUTO_CONSOLIDATE=true|false   (default true — feature flag)
  EDP_CONSOLIDATE_THRESHOLD=3       (acessos mínimos para promover)
  EDP_CONSOLIDATE_COOLDOWN_SEC=1800 (30min entre execuções)

Observabilidade:
  promoted > 0 → INFO  (evento relevante)
  promoted = 0 → DEBUG (sem ruído a cada 30min)
"""
from __future__ import annotations

import logging
import os
import threading

from ..clock import now as _now

logger = logging.getLogger("edp.runtime.auto_consolidation")


# ── Configuração ─────────────────────────────────────────────────────────────

DEFAULT_COOLDOWN_SEC = 1800.0  # 30 minutos
DEFAULT_THRESHOLD = 3          # acessos mínimos (mesmo default do endpoint)


def is_auto_consolidate_enabled() -> bool:
    """Feature flag EDP_AUTO_CONSOLIDATE (default true)."""
    val = os.environ.get("EDP_AUTO_CONSOLIDATE", "true").strip().lower()
    return val in ("true", "1", "yes", "on")


def _get_threshold() -> int:
    try:
        v = int(os.environ.get("EDP_CONSOLIDATE_THRESHOLD", str(DEFAULT_THRESHOLD)))
        return max(1, min(100, v))
    except ValueError:
        return DEFAULT_THRESHOLD


def _get_cooldown() -> float:
    try:
        return float(os.environ.get(
            "EDP_CONSOLIDATE_COOLDOWN_SEC", str(DEFAULT_COOLDOWN_SEC)
        ))
    except ValueError:
        return DEFAULT_COOLDOWN_SEC


# ── Execução core (testável isoladamente, sem bgloop) ────────────────────────

def run_consolidation_once(session_id: str = "default") -> dict | None:
    """
    Executa uma consolidação no scope ativo do MemoryStore da sessão.

    Returns:
        dict de métricas do consolidate_promote_only, ou None se
        feature off / memory indisponível / falha (logada, não propagada).
    """
    if not is_auto_consolidate_enabled():
        return None
    try:
        from .registry import get_memory
        mem = get_memory(session_id)
        if mem is None:
            logger.debug("[auto_consol] memory indisponível para %s", session_id)
            return None

        from ..consolidation import consolidate_promote_only
        result = consolidate_promote_only(
            mem, promote_threshold=_get_threshold()
        )

        promoted = int(result.get("promoted", 0))
        scope = getattr(mem, "_active_scope", "?")
        if promoted > 0:
            logger.info(
                "[auto_consol] promovidas %d entries → semântica | "
                "scope=%s scanned=%d threshold=%d",
                promoted, scope,
                int(result.get("scanned", 0)), int(result.get("threshold", 0)),
            )
        else:
            logger.debug(
                "[auto_consol] nada a promover | scope=%s scanned=%d",
                scope, int(result.get("scanned", 0)),
            )
        return result
    except Exception as e:
        logger.warning(
            "[auto_consol] falhou (não-fatal): %s: %s",
            type(e).__name__, str(e)[:120],
        )
        return None


# ── Background job factory (padrão idêntico ao CHI) ──────────────────────────

_last_run: dict[str, float] = {}
_job_lock = threading.Lock()


def make_auto_consolidation_job(
    session_id: str = "default",
    cooldown_sec: float | None = None,
    interval_sec: float = 60.0,
):
    """
    Cria BackgroundJob de consolidação automática.
    Tick a cada interval_sec; executa apenas se cooldown passou.

    Returns: BackgroundJob pronto para loop.register_job()
    """
    from .background_loop import BackgroundJob

    effective_cooldown = cooldown_sec if cooldown_sec is not None else _get_cooldown()

    async def _job_func():
        if not is_auto_consolidate_enabled():
            return
        with _job_lock:
            last = _last_run.get(session_id, 0.0)
            now_ts = _now()
            if (now_ts - last) < effective_cooldown:
                return  # cooldown
            _last_run[session_id] = now_ts
        run_consolidation_once(session_id)

    return BackgroundJob(
        name="auto_consolidation",
        func=_job_func,
        interval_sec=interval_sec,
        priority=3,  # baixa
        suspend_on_pressure=True,
    )
