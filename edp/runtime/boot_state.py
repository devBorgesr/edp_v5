"""
edp.runtime.boot_state — Estado de boot do EDP.

Estados explícitos para que dashboard, /health, e clientes saibam
SE o sistema está pronto para receber requests.

Transições válidas:
    COLD     → WARMING   (boot iniciado)
    WARMING  → READY     (todos os componentes carregados)
    WARMING  → DEGRADED  (algum componente falhou mas core funciona)
    READY    → DEGRADED  (componente caiu durante operação)
    DEGRADED → READY     (componente recuperou)
    *        → SHUTDOWN  (encerramento controlado)

Em COLD ou WARMING: /health retorna 503.
Em READY: /health retorna 200.
Em DEGRADED: /health retorna 200 com lista de componentes degradados.
Em SHUTDOWN: /health retorna 503.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from ..clock import now as _now

logger = logging.getLogger("edp.runtime.boot")


class RuntimeState(str, Enum):
    COLD     = "cold"
    WARMING  = "warming"
    READY    = "ready"
    DEGRADED = "degraded"
    SHUTDOWN = "shutdown"


# Transições permitidas (state machine)
_VALID_TRANSITIONS = {
    RuntimeState.COLD:     {RuntimeState.WARMING, RuntimeState.SHUTDOWN},
    RuntimeState.WARMING:  {RuntimeState.READY, RuntimeState.DEGRADED, RuntimeState.SHUTDOWN},
    RuntimeState.READY:    {RuntimeState.DEGRADED, RuntimeState.SHUTDOWN},
    RuntimeState.DEGRADED: {RuntimeState.READY, RuntimeState.SHUTDOWN},
    RuntimeState.SHUTDOWN: set(),  # estado terminal
}


@dataclass
class ComponentHealth:
    name:      str
    healthy:   bool = True
    last_check: float = 0.0
    error:     Optional[str] = None


class BootStateMachine:
    """
    State machine thread-safe do runtime.

    Componentes registram-se via mark_component(name, healthy=True/False).
    Estado geral deriva: se algum componente crítico unhealthy → DEGRADED.
    """

    def __init__(self) -> None:
        self._state: RuntimeState = RuntimeState.COLD
        self._state_since: float  = _now()
        self._lock = threading.Lock()
        self._components: Dict[str, ComponentHealth] = {}
        self._history: List[dict] = []
        self._max_history = 20
        logger.info("[boot] state=COLD")

    @property
    def state(self) -> RuntimeState:
        with self._lock:
            return self._state

    def transition(self, new_state: RuntimeState, reason: str = "") -> bool:
        """
        Tenta transição. Retorna True se aceita, False se inválida.
        """
        with self._lock:
            if new_state == self._state:
                return True
            if new_state not in _VALID_TRANSITIONS.get(self._state, set()):
                logger.warning(
                    "[boot] transição inválida: %s -> %s (motivo: %s)",
                    self._state.value, new_state.value, reason,
                )
                return False

            prev = self._state
            self._state = new_state
            now = _now()
            duration_s = now - self._state_since
            self._state_since = now

            entry = {
                "from":     prev.value,
                "to":       new_state.value,
                "reason":   reason,
                "at":       now,
                "duration_in_previous_s": round(duration_s, 2),
            }
            self._history.append(entry)
            if len(self._history) > self._max_history:
                self._history.pop(0)

            logger.info(
                "[boot] %s -> %s (motivo: %s, anterior durou %.1fs)",
                prev.value, new_state.value, reason or "?", duration_s,
            )
            return True

    def mark_component(self,
                       name: str,
                       healthy: bool,
                       error: Optional[str] = None,
                       critical: bool = False) -> None:
        """
        Marca status de um componente.
        Se critical=True e healthy=False → força transição para DEGRADED.
        """
        with self._lock:
            self._components[name] = ComponentHealth(
                name=name, healthy=healthy,
                last_check=_now(), error=error,
            )

            # Re-avalia estado geral
            if not healthy and critical and self._state == RuntimeState.READY:
                self._state = RuntimeState.DEGRADED
                self._state_since = _now()
                logger.warning(
                    "[boot] DEGRADED por componente critico='%s' erro=%s",
                    name, error,
                )
            elif healthy and self._state == RuntimeState.DEGRADED:
                # Se TODOS críticos estão saudáveis, volta para READY
                any_critical_failing = any(
                    not c.healthy for c in self._components.values()
                )
                if not any_critical_failing:
                    self._state = RuntimeState.READY
                    self._state_since = _now()
                    logger.info("[boot] recuperado para READY")

    def is_ready(self) -> bool:
        """True se sistema pode receber requisições."""
        with self._lock:
            return self._state in (RuntimeState.READY, RuntimeState.DEGRADED)

    def is_accepting_inferences(self) -> bool:
        """True se inferências podem ser iniciadas."""
        with self._lock:
            return self._state in (RuntimeState.READY, RuntimeState.DEGRADED)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "state":         self._state.value,
                "state_since":   self._state_since,
                "uptime_s":      round(_now() - self._state_since, 1),
                "components":    {
                    name: {
                        "healthy":    c.healthy,
                        "last_check": c.last_check,
                        "error":      c.error,
                    }
                    for name, c in self._components.items()
                },
                "history": list(self._history[-5:]),  # últimas 5
            }


# ── Singleton ────────────────────────────────────────────────────────────────

_state_machine: Optional[BootStateMachine] = None
_sm_lock = threading.Lock()


def get_boot_state() -> BootStateMachine:
    global _state_machine
    if _state_machine is None:
        with _sm_lock:
            if _state_machine is None:
                _state_machine = BootStateMachine()
    return _state_machine
