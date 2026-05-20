"""
FRACTAL EDP v3.2 — PRESSURE MONITOR (P1)

REPLACES: scattered pressure logic across pressure_regulator.py
PURPOSE:  Unified multidimensional pressure surface with typed dimensions.

WHY A SEPARATE MODULE FROM pressure_regulator.py:
  pressure_regulator.py handles LOCAL/CLUSTER/GLOBAL pressure propagation
  with EMA, hysteresis, and oscillation damping.
  This module aggregates TYPED pressure dimensions (eviction, consolidation,
  entropy, retrieval, embedding) into a single PressureSurface that
  MetaStabilityController can read without coupling to individual subsystems.

PRESSURE DIMENSIONS:
  eviction_pressure:      memory levels filling up → forced eviction imminent
  consolidation_pressure: cluster queue backing up → consolidation overdue
  entropy_pressure:       semantic diversity collapsing → biodiversity alert
  retrieval_pressure:     storm guard score rising → retrieval lock-in
  embedding_pressure:     cache miss rate climbing → embedding generation cost
  graph_pressure:         decision graph HOT layer filling → tracing cost rising

MATH — COMPOSITE PRESSURE:
  Each dimension is in [0,1].
  Composite = weighted sum, normalized to [0,1].
  Weights reflect observed impact on system stability (documented):
    eviction:      0.25  (memory is hard resource — highest priority)
    consolidation: 0.20  (cascade risk if ignored)
    entropy:       0.20  (diversity collapse is irreversible in short term)
    retrieval:     0.15  (storm is detectable and correctable)
    embedding:     0.10  (latency impact, not correctness)
    graph:         0.10  (observability, not core function)

  Weights sum to 1.0.

HYSTERESIS:
  Each dimension has independent hysteresis. A dimension marked SATURATED
  must drop below (threshold - hysteresis) before clearing.
  This prevents rapid oscillation between alert states.

THREAD SAFETY:
  All writes through update_*() methods, each holding the internal lock.
  read() is also locked (copy-on-write snapshot returned).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple


class PressureDimension(Enum):
    EVICTION      = "eviction"
    CONSOLIDATION = "consolidation"
    ENTROPY       = "entropy"
    RETRIEVAL     = "retrieval"
    EMBEDDING     = "embedding"
    GRAPH         = "graph"


# Documented weights — changing requires version bump
_DIMENSION_WEIGHTS: Dict[PressureDimension, float] = {
    PressureDimension.EVICTION:      0.25,
    PressureDimension.CONSOLIDATION: 0.20,
    PressureDimension.ENTROPY:       0.20,
    PressureDimension.RETRIEVAL:     0.15,
    PressureDimension.EMBEDDING:     0.10,
    PressureDimension.GRAPH:         0.10,
}
assert abs(sum(_DIMENSION_WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1.0"


@dataclass
class PressureMonitorConfig:
    # Alert thresholds per dimension
    alert_threshold: float = 0.65
    critical_threshold: float = 0.82
    hysteresis: float = 0.10

    # EMA smoothing per dimension (alpha)
    ema_alpha: float = 0.20   # lower = more inertia (slower response)

    # Composite alert threshold
    composite_alert_threshold: float = 0.55
    composite_critical_threshold: float = 0.75


@dataclass
class DimensionState:
    dimension: PressureDimension
    raw: float = 0.0           # last raw input
    smoothed: float = 0.0      # EMA-smoothed
    is_alert: bool = False
    is_critical: bool = False
    last_update: float = field(default_factory=time.time)


@dataclass(frozen=True)
class PressureSurface:
    """Immutable snapshot of all pressure dimensions. Safe to read without lock."""
    timestamp: float
    dimensions: Tuple[Tuple[str, float], ...]   # (name, smoothed_value)
    composite: float
    alert_dimensions: Tuple[str, ...]
    critical_dimensions: Tuple[str, ...]

    def get(self, dim: PressureDimension) -> float:
        for name, val in self.dimensions:
            if name == dim.value:
                return val
        return 0.0

    def is_any_critical(self) -> bool:
        return len(self.critical_dimensions) > 0

    def is_any_alert(self) -> bool:
        return len(self.alert_dimensions) > 0


class PressureMonitor:
    """
    Unified multidimensional pressure surface.
    Subsystems call update_*(value) to report their pressure.
    MetaStabilityController calls read() to get the current surface.
    """

    def __init__(self, config: Optional[PressureMonitorConfig] = None) -> None:
        self._cfg = config or PressureMonitorConfig()
        self._lock = threading.RLock()
        self._states: Dict[PressureDimension, DimensionState] = {
            dim: DimensionState(dimension=dim)
            for dim in PressureDimension
        }

    # --------------------------------------------------
    # UPDATE API (one method per dimension for clarity)
    # --------------------------------------------------

    def update_eviction(self, value: float) -> None:
        self._update(PressureDimension.EVICTION, value)

    def update_consolidation(self, value: float) -> None:
        self._update(PressureDimension.CONSOLIDATION, value)

    def update_entropy(self, value: float) -> None:
        """
        value: 1.0 - (mrd / mrd_max) — converts MRD to pressure.
        High entropy pressure = low diversity.
        """
        self._update(PressureDimension.ENTROPY, value)

    def update_retrieval(self, value: float) -> None:
        """value = storm_guard.composite_storm_score"""
        self._update(PressureDimension.RETRIEVAL, value)

    def update_embedding(self, value: float) -> None:
        """value = embedding_cache.instability_score()"""
        self._update(PressureDimension.EMBEDDING, value)

    def update_graph(self, value: float) -> None:
        """value = decision_graph.obs_pressure().pressure()"""
        self._update(PressureDimension.GRAPH, value)

    # --------------------------------------------------
    # READ API
    # --------------------------------------------------

    def read(self) -> PressureSurface:
        """Returns immutable PressureSurface snapshot. Lock-free read of frozen object."""
        with self._lock:
            return self._build_surface()

    def composite(self) -> float:
        """Fast composite read for tight loops."""
        with self._lock:
            return self._compute_composite()

    def dimension_value(self, dim: PressureDimension) -> float:
        with self._lock:
            return self._states[dim].smoothed

    # --------------------------------------------------
    # INTERNAL
    # --------------------------------------------------

    def _update(self, dim: PressureDimension, raw: float) -> None:
        raw = max(0.0, min(raw, 1.0))
        with self._lock:
            state = self._states[dim]
            alpha = self._cfg.ema_alpha
            state.raw = raw
            state.smoothed = alpha * raw + (1.0 - alpha) * state.smoothed
            state.last_update = time.time()

            # Alert with hysteresis
            if not state.is_alert and state.smoothed >= self._cfg.alert_threshold:
                state.is_alert = True
            elif state.is_alert and state.smoothed < (self._cfg.alert_threshold - self._cfg.hysteresis):
                state.is_alert = False
                state.is_critical = False

            if not state.is_critical and state.smoothed >= self._cfg.critical_threshold:
                state.is_critical = True
            elif state.is_critical and state.smoothed < (self._cfg.critical_threshold - self._cfg.hysteresis):
                state.is_critical = False

    def _compute_composite(self) -> float:
        return min(1.0, sum(
            _DIMENSION_WEIGHTS[dim] * state.smoothed
            for dim, state in self._states.items()
        ))

    def _build_surface(self) -> PressureSurface:
        dims = tuple(
            (dim.value, state.smoothed)
            for dim, state in self._states.items()
        )
        alerts = tuple(
            dim.value for dim, state in self._states.items()
            if state.is_alert
        )
        criticals = tuple(
            dim.value for dim, state in self._states.items()
            if state.is_critical
        )
        return PressureSurface(
            timestamp=time.time(),
            dimensions=dims,
            composite=self._compute_composite(),
            alert_dimensions=alerts,
            critical_dimensions=criticals,
        )