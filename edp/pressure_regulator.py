"""
pressure_regulator.py — EDP v3.2 Fractal Pressure Regulator

Camada intermediária entre pressure_monitor, adaptive_controller,
meta_stability, economy e storm_guard.

Responsabilidade:
  - Absorver CognitiveTick e produzir pressão global regulada
  - Aplicar hysteresis, EMA smoothing, anti-spike protection
  - Propagar pressão LOCAL → CLUSTER → GLOBAL com decay
  - Raise PressureSaturationError quando saturado

Contrato com orchestrator_v32:
  regulator.absorb_tick(tick)  → dict {"global": float, "local": float, ...}
  regulator.reset_local()      → None
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

from .exceptions import PressureSaturationError
from .types_v32 import CognitiveTick, DecisionEvent, DecisionEventType


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class PressureConfig:
    # Thresholds
    saturation_threshold: float = 0.92   # acima → PressureSaturationError
    alert_threshold:      float = 0.75
    critical_threshold:   float = 0.85

    # EMA smoothing
    ema_alpha:            float = 0.20   # suavização (menor = mais inércia)
    spike_alpha:          float = 0.60   # detecta spikes rápidos

    # Propagação LOCAL → CLUSTER → GLOBAL
    local_to_cluster:     float = 0.40   # fator de propagação
    cluster_to_global:    float = 0.30

    # Decay por tick (pressão se dissipa com tempo)
    decay_rate:           float = 0.02   # por segundo

    # Hysteresis: zona morta ao redor dos thresholds
    hysteresis:           float = 0.05

    # Anti-spike: limita variação máxima por tick
    max_delta_per_tick:   float = 0.15

    # Saturação: cooldown mínimo após saturação
    saturation_cooldown_ms: float = 1000.0


# ── Signal snapshot ───────────────────────────────────────────────────────────

@dataclass
class PressureSnapshot:
    local:   float = 0.0
    cluster: float = 0.0
    global_: float = 0.0
    ema:     float = 0.0
    spike:   float = 0.0
    ts:      float = field(default_factory=time.time)
    saturated: bool = False
    in_alert:  bool = False
    in_critical: bool = False

    def get(self, zone: str) -> float:
        return getattr(self, zone, self.global_)

    def to_dict(self) -> dict:
        return {
            "local":    round(self.local, 4),
            "cluster":  round(self.cluster, 4),
            "global":   round(self.global_, 4),
            "ema":      round(self.ema, 4),
            "spike":    round(self.spike, 4),
            "saturated": self.saturated,
            "in_alert":  self.in_alert,
        }


# ── Regulator ─────────────────────────────────────────────────────────────────

class FractalPressureRegulator:
    """
    Regula pressão cognitiva com hysteresis + EMA + anti-spike.

    Thread-safe. Lock ordering: chamado após economy lock, antes de
    pressure_monitor (conforme orchestrator_v32 doc).
    """

    def __init__(
        self,
        config: Optional[PressureConfig] = None,
        event_sink: Optional[Callable[[DecisionEvent], None]] = None,
    ) -> None:
        self._cfg  = config or PressureConfig()
        self._sink = event_sink or (lambda _: None)
        self._lock = threading.Lock()

        # Pressure state
        self._local:   float = 0.0
        self._cluster: float = 0.0
        self._global:  float = 0.0
        self._ema:     float = 0.0
        self._spike:   float = 0.0

        # Alert state (com hysteresis)
        self._in_alert:    bool  = False
        self._in_critical: bool  = False
        self._saturated:   bool  = False
        self._sat_until:   float = 0.0

        self._last_tick_ts: float = time.monotonic()
        self._total_ticks:  int   = 0
        self._total_sat:    int   = 0

    # ── API pública ────────────────────────────────────────────────────────────

    def absorb_tick(self, tick: CognitiveTick) -> dict:
        """
        Absorve um CognitiveTick e retorna pressão regulada.
        Raise PressureSaturationError se saturado e cooldown ativo.
        """
        with self._lock:
            now     = time.monotonic()
            elapsed = max(now - self._last_tick_ts, 1e-6)
            self._last_tick_ts = now
            self._total_ticks += 1

            # Decay temporal (pressão se dissipa)
            decay = math.exp(-self._cfg.decay_rate * elapsed)
            self._local   *= decay
            self._cluster *= decay
            self._global  *= decay

            # Pressão bruta do tick
            raw_delta = tick.total_pressure()

            # Anti-spike: limita variação máxima por tick
            capped_delta = min(raw_delta, self._cfg.max_delta_per_tick)

            # LOCAL absorbe o tick direto
            self._local = _clamp(self._local + capped_delta)

            # Propagação LOCAL → CLUSTER → GLOBAL
            cluster_delta = self._local * self._cfg.local_to_cluster
            global_delta  = self._cluster * self._cfg.cluster_to_global
            self._cluster = _clamp(self._cluster + cluster_delta * 0.1)
            self._global  = _clamp(self._global  + global_delta  * 0.1)

            # EMA suaviza o global
            self._ema = (
                self._cfg.ema_alpha * self._global
                + (1 - self._cfg.ema_alpha) * self._ema
            )

            # Spike detector (alpha mais alto = reage rápido)
            self._spike = (
                self._cfg.spike_alpha * raw_delta
                + (1 - self._cfg.spike_alpha) * self._spike
            )

            # Pressão efetiva = max(ema, spike * 0.5) — conservadora
            effective = _clamp(max(self._ema, self._spike * 0.5))

            # Update alert states com hysteresis
            hys = self._cfg.hysteresis
            if not self._in_alert and effective >= self._cfg.alert_threshold:
                self._in_alert = True
            elif self._in_alert and effective < self._cfg.alert_threshold - hys:
                self._in_alert = False

            if not self._in_critical and effective >= self._cfg.critical_threshold:
                self._in_critical = True
                self._emit_critical(effective)
            elif self._in_critical and effective < self._cfg.critical_threshold - hys:
                self._in_critical = False

            # Saturação
            if effective >= self._cfg.saturation_threshold:
                if now >= self._sat_until:
                    self._saturated  = True
                    self._sat_until  = now + self._cfg.saturation_cooldown_ms / 1000.0
                    self._total_sat += 1
                    self._emit_saturation(effective)
                    raise PressureSaturationError(
                        f"Pressure saturated at {effective:.3f} "
                        f"(threshold={self._cfg.saturation_threshold})"
                    )
            else:
                self._saturated = False

            snap = {
                "global":    round(effective, 4),
                "local":     round(self._local, 4),
                "cluster":   round(self._cluster, 4),
                "ema":       round(self._ema, 4),
                "spike":     round(self._spike, 4),
                "in_alert":  self._in_alert,
                "in_critical": self._in_critical,
            }
            return snap

    def reset_local(self) -> None:
        """Drena pressão local — chamado pelo orchestrator em modo 'drain'."""
        with self._lock:
            self._local  = 0.0
            self._cluster *= 0.5  # drena cluster parcialmente
            self._saturated = False

    def current_pressure(self) -> PressureSnapshot:
        with self._lock:
            return PressureSnapshot(
                local=self._local,
                cluster=self._cluster,
                global_=self._global,
                ema=self._ema,
                spike=self._spike,
                saturated=self._saturated,
                in_alert=self._in_alert,
                in_critical=self._in_critical,
            )

    def health_report(self) -> dict:
        snap = self.current_pressure()
        return {
            "global":      round(snap.global_, 4),
            "ema":         round(snap.ema, 4),
            "in_alert":    snap.in_alert,
            "in_critical": snap.in_critical,
            "saturated":   snap.saturated,
            "total_ticks": self._total_ticks,
            "total_sat":   self._total_sat,
        }

    def metrics(self) -> dict:
        return self.health_report()

    # ── Interno ────────────────────────────────────────────────────────────────

    def _emit_critical(self, pressure: float) -> None:
        try:
            self._sink(DecisionEvent(
                event_type=DecisionEventType.PRESSURE_ESCALATION,
                metadata=(
                    ("event", "pressure_critical"),
                    ("pressure", str(round(pressure, 4))),
                ),
            ))
        except Exception:
            pass  # _emit never kills runtime — failure tracked externally

    def _emit_saturation(self, pressure: float) -> None:
        try:
            self._sink(DecisionEvent(
                event_type=DecisionEventType.PRESSURE_ESCALATION,
                metadata=(
                    ("event", "pressure_saturated"),
                    ("pressure", str(round(pressure, 4))),
                    ("total_sat", str(self._total_sat)),
                ),
            ))
        except Exception:
            pass  # _emit never kills runtime — failure tracked externally


# ── Utils ─────────────────────────────────────────────────────────────────────

import math

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    if math.isnan(v) or math.isinf(v):
        return lo
    return max(lo, min(hi, v))
