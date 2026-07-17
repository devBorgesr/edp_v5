"""
edp/pressure.py — Store eviction pressure monitor.

Mede ocupação do STORE EPISÓDICO (len(entries)/max_size) — sem relação com
RAM do sistema operacional. Não confundir com edp.runtime.pressure_governor
(MemoryPressureGovernor: RAM real do host via psutil, thresholds em GB,
gateia inferência LOCAL e ticks do background_loop — Dívida #41).

Extracted from pressure_monitor.py @ feb0db9, keeping only the two dimensions
that have measurable signals in the live EDP:

  eviction:      len(episodic.entries) / episodic.max_size
  consolidation: optional — no queue exists in the live EDP today; pass 0.0
                 until a signal is wired (placeholder for future use).

EMA smoothing + hysteresis prevent alert/clear oscillation.
Zero dependencies outside stdlib.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class StorePressureConfig:
    # Source: PressureMonitorConfig — feb0db9 pressure_monitor.py:77-79
    alert_threshold: float = 0.65
    critical_threshold: float = 0.82
    hysteresis: float = 0.10
    # Source: PressureMonitorConfig — feb0db9 pressure_monitor.py:82
    ema_alpha: float = 0.20


@dataclass(frozen=True)
class StorePressureSnapshot:
    eviction: float           # EMA-smoothed eviction ratio in [0,1]
    consolidation: float      # EMA-smoothed consolidation ratio in [0,1]
    eviction_alert: bool
    eviction_critical: bool
    consolidation_alert: bool
    ts: float


class _DimState:
    __slots__ = ("smoothed", "is_alert", "is_critical")

    def __init__(self) -> None:
        self.smoothed: float = 0.0
        self.is_alert: bool = False
        self.is_critical: bool = False


class StorePressureMonitor:
    """
    Measures eviction and consolidation pressure for the live EDP store.

    Call update(eviction_ratio) after each memory.add() / _prune().
    eviction_ratio = len(entries) / max_size — already available at
    memory.py:575 (len(self.entries) > self.max_size).

    snapshot() returns immutable state snapshot. Thread-safe.
    """

    def __init__(self, config: StorePressureConfig | None = None) -> None:
        self._cfg = config or StorePressureConfig()
        self._lock = threading.Lock()
        self._eviction = _DimState()
        self._consolidation = _DimState()

    def update(self, eviction_ratio: float, consolidation_ratio: float = 0.0) -> None:
        """
        eviction_ratio:      len(entries) / max_size   (clamp applied internally)
        consolidation_ratio: optional; leave 0.0 while no signal is wired
        """
        ev = max(0.0, min(eviction_ratio, 1.0))
        co = max(0.0, min(consolidation_ratio, 1.0))
        with self._lock:
            self._apply(self._eviction, ev)
            self._apply(self._consolidation, co)

    def snapshot(self) -> StorePressureSnapshot:
        with self._lock:
            return StorePressureSnapshot(
                eviction=self._eviction.smoothed,
                consolidation=self._consolidation.smoothed,
                eviction_alert=self._eviction.is_alert,
                eviction_critical=self._eviction.is_critical,
                consolidation_alert=self._consolidation.is_alert,
                ts=time.time(),
            )

    # ── internal ──────────────────────────────────────────────────────────────

    def _apply(self, state: _DimState, raw: float) -> None:
        # EMA — source: _update() feb0db9 pressure_monitor.py:192-193
        alpha = self._cfg.ema_alpha
        state.smoothed = alpha * raw + (1.0 - alpha) * state.smoothed

        # Hysteresis — source: _update() feb0db9 pressure_monitor.py:197-206
        cfg = self._cfg
        if not state.is_alert and state.smoothed >= cfg.alert_threshold:
            state.is_alert = True
        elif state.is_alert and state.smoothed < (cfg.alert_threshold - cfg.hysteresis):
            state.is_alert = False
            state.is_critical = False

        if not state.is_critical and state.smoothed >= cfg.critical_threshold:
            state.is_critical = True
        elif state.is_critical and state.smoothed < (cfg.critical_threshold - cfg.hysteresis):
            state.is_critical = False
