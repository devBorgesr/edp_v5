"""
meta_stability.py — EDP v3.2 Meta Stability Controller

Homeostase cognitiva global. Integra todos os sinais e decide
o modo operacional do sistema.

Modos: NORMAL → ELEVATED → DEGRADED → CRITICAL → EMERGENCY → RECOVERY

Contrato com orchestrator_v32:
  ctrl.update(signals)          → OperationalParams
  ctrl.current_params()         → OperationalParams
  ctrl.current_mode()           → StabilityMode
  ctrl.signal_history_summary() → dict
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Deque, List, Optional, Tuple

from .types_v32 import DecisionEvent, DecisionEventType


# ── Enums ──────────────────────────────────────────────────────────────────────

class StabilityMode(str, Enum):
    NORMAL    = "normal"
    ELEVATED  = "elevated"
    DEGRADED  = "degraded"
    CRITICAL  = "critical"
    EMERGENCY = "emergency"
    RECOVERY  = "recovery"


class ConsolidationMode(str, Enum):
    NORMAL      = "normal"
    AGGRESSIVE  = "aggressive"
    SUSPENDED   = "suspended"


# ── Sinais de entrada ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StabilitySignals:
    global_pressure:     float = 0.0   # pressão global do regulator
    storm_score:         float = 0.0   # score do storm_guard
    cache_instability:   float = 0.0   # instabilidade do EmbeddingCache
    entropy_drift:       float = 0.0   # drift semântico (negativo = colapso)
    abstraction_entropy: float = 0.0   # entropia de distribuição de abstraction
    obs_pressure:        float = 0.0   # pressão de observabilidade do grafo


# ── Parâmetros de saída ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class OperationalParams:
    stability_mode:             StabilityMode
    target_reasoning_depth:     int     # profundidade máxima de recursão
    retrieval_budget_multiplier: float  # multiplicador no budget de retrieval
    retrieval_depth:            int     # níveis de abstração para retrieval
    retrieval_breadth:          int     # k máximo
    consolidation_aggressiveness: float # [0,1]
    consolidation_mode:         ConsolidationMode
    compression_strength:       float   # [0,1]
    decay_rate_modifier:        float   # multiplicador no decay de freshness
    abstraction_restriction:    bool    # só busca abstrações (DEGRADED/EMERGENCY)
    emergency_actions:          Tuple[str, ...]
    snapshot_policy:            str     # "normal" | "checkpoint_only" | "freeze"
    cooldown_ms:                float


# ── Configuração ──────────────────────────────────────────────────────────────

@dataclass
class ControllerConfig:
    # Thresholds para transição de modo (pressão composta EMA)
    elevated_threshold:  float = 0.30
    degraded_threshold:  float = 0.50
    critical_threshold:  float = 0.68
    emergency_threshold: float = 0.82

    # Hysteresis: zona morta para evitar thrashing
    hysteresis:          float = 0.08

    # EMA suavização dos sinais
    signal_ema_alpha:    float = 0.18

    # Mínimo de ticks em cada modo antes de sair
    # Anti-oscillation: não troca de modo muito rápido
    min_ticks_per_mode:  int   = 3

    # Recovery: % de quanto o score deve cair antes de voltar ao normal
    recovery_factor:     float = 0.60   # score deve cair para < recovery_factor * threshold

    # Histórico de sinais
    signal_history_size: int   = 50


# ── Controller ────────────────────────────────────────────────────────────────

class MetaStabilityController:
    """
    Controlador de homeostase cognitiva.

    Lê StabilitySignals, calcula pressão composta via EMA,
    decide StabilityMode com hysteresis e anti-oscillation,
    e retorna OperationalParams para o orchestrator.
    """

    def __init__(
        self,
        config: Optional[ControllerConfig] = None,
        event_sink: Optional[Callable[[DecisionEvent], None]] = None,
    ) -> None:
        self._cfg   = config or ControllerConfig()
        self._sink  = event_sink or (lambda _: None)
        self._lock  = threading.Lock()

        # Estado EMA por sinal
        self._ema_pressure:     float = 0.0
        self._ema_storm:        float = 0.0
        self._ema_cache:        float = 0.0
        self._ema_entropy:      float = 0.0
        self._ema_abstraction:  float = 0.0
        self._ema_obs:          float = 0.0

        # Modo atual
        self._mode:            StabilityMode = StabilityMode.NORMAL
        self._mode_ticks:      int           = 0
        self._mode_since:      float         = time.monotonic()
        self._total_ticks:     int           = 0
        self._mode_changes:    int           = 0

        # Parâmetros atuais
        self._params: OperationalParams = self._build_params(
            StabilityMode.NORMAL, composite=0.0
        )

        # Histórico de sinais (para signal_history_summary)
        self._history: Deque[dict] = deque(maxlen=self._cfg.signal_history_size)

    # ── API pública ────────────────────────────────────────────────────────────

    def update(self, signals: StabilitySignals) -> OperationalParams:
        """
        Atualiza estado com novos sinais. Retorna OperationalParams.
        Thread-safe. O(1).
        """
        with self._lock:
            alpha = self._cfg.signal_ema_alpha

            # Atualiza EMA por sinal
            self._ema_pressure    = alpha * signals.global_pressure    + (1-alpha) * self._ema_pressure
            self._ema_storm       = alpha * signals.storm_score        + (1-alpha) * self._ema_storm
            self._ema_cache       = alpha * signals.cache_instability  + (1-alpha) * self._ema_cache
            self._ema_obs         = alpha * signals.obs_pressure       + (1-alpha) * self._ema_obs

            # entropy_drift: negativo = colapso → contribui para instabilidade
            entropy_risk = max(0.0, -signals.entropy_drift)
            self._ema_entropy = alpha * entropy_risk + (1-alpha) * self._ema_entropy

            abstraction_risk = 1.0 - signals.abstraction_entropy  # baixa entropia → risk
            self._ema_abstraction = alpha * abstraction_risk + (1-alpha) * self._ema_abstraction

            # Pressão composta ponderada
            composite = _clamp(
                0.35 * self._ema_pressure
                + 0.25 * self._ema_storm
                + 0.15 * self._ema_cache
                + 0.15 * self._ema_entropy
                + 0.05 * self._ema_abstraction
                + 0.05 * self._ema_obs
            )

            self._total_ticks += 1
            self._mode_ticks  += 1

            # Decide novo modo
            new_mode = self._decide_mode(composite)
            if new_mode != self._mode and self._mode_ticks >= self._cfg.min_ticks_per_mode:
                self._transition(new_mode, composite)

            # Registra histórico
            self._history.append({
                "ts":        time.time(),
                "composite": round(composite, 4),
                "mode":      self._mode.value,
                "ema_pressure": round(self._ema_pressure, 4),
                "ema_storm":    round(self._ema_storm, 4),
            })

            self._params = self._build_params(self._mode, composite)
            return self._params

    def current_params(self) -> OperationalParams:
        with self._lock:
            return self._params

    def current_mode(self) -> StabilityMode:
        with self._lock:
            return self._mode

    def signal_history_summary(self) -> dict:
        with self._lock:
            history = list(self._history)

        if not history:
            return {"current_mode": self._mode.value, "ticks": 0}

        composites = [h["composite"] for h in history]
        return {
            "current_mode":    self._mode.value,
            "ticks":           self._total_ticks,
            "mode_changes":    self._mode_changes,
            "avg_composite":   round(sum(composites) / len(composites), 4),
            "max_composite":   round(max(composites), 4),
            "last_signals":    history[-1],
        }

    def health_report(self) -> dict:
        with self._lock:
            return {
                "mode":         self._mode.value,
                "mode_ticks":   self._mode_ticks,
                "mode_changes": self._mode_changes,
                "total_ticks":  self._total_ticks,
                "ema_pressure": round(self._ema_pressure, 4),
                "ema_storm":    round(self._ema_storm, 4),
            }

    def metrics(self) -> dict:
        return self.health_report()

    # ── Interno ────────────────────────────────────────────────────────────────

    def _decide_mode(self, composite: float) -> StabilityMode:
        """
        Decide próximo modo com hysteresis.
        Não aplica a transição — apenas decide qual seria o alvo.
        Anti-oscillation: transição requer min_ticks_per_mode no modo atual.
        """
        cfg = self._cfg
        hys = cfg.hysteresis
        cur = self._mode

        # Emergency / Critical
        if composite >= cfg.emergency_threshold:
            return StabilityMode.EMERGENCY
        if composite >= cfg.critical_threshold:
            return StabilityMode.CRITICAL

        # Recovery: sai de EMERGENCY/CRITICAL gradualmente
        if cur in (StabilityMode.EMERGENCY, StabilityMode.CRITICAL):
            if composite < cfg.critical_threshold - hys:
                return StabilityMode.RECOVERY

        # Recovery → Degraded → Elevated → Normal (gradual)
        if cur == StabilityMode.RECOVERY:
            if composite < cfg.degraded_threshold - hys:
                return StabilityMode.ELEVATED
            return StabilityMode.RECOVERY

        if composite >= cfg.degraded_threshold:
            return StabilityMode.DEGRADED
        if composite >= cfg.elevated_threshold:
            return StabilityMode.ELEVATED

        # Abaixo de elevated_threshold - hys → Normal
        if composite < cfg.elevated_threshold - hys:
            return StabilityMode.NORMAL

        # Dentro da zona de hysteresis → mantém atual
        return cur

    def _transition(self, new_mode: StabilityMode, composite: float) -> None:
        """Aplica transição de modo. Chamado dentro de _lock."""
        old_mode    = self._mode
        self._mode  = new_mode
        self._mode_ticks  = 0
        self._mode_since  = time.monotonic()
        self._mode_changes += 1

        try:
            self._sink(DecisionEvent(
                event_type=DecisionEventType.PRESSURE_ESCALATION,
                metadata=(
                    ("event",      "mode_transition"),
                    ("from_mode",  old_mode.value),
                    ("to_mode",    new_mode.value),
                    ("composite",  str(round(composite, 4))),
                ),
            ))
        except Exception:
            pass

    def _build_params(self, mode: StabilityMode, composite: float) -> OperationalParams:
        """
        Mapeia modo → OperationalParams.
        Parâmetros degradam gradualmente — nunca cliff edge brusco.
        """
        if mode == StabilityMode.NORMAL:
            return OperationalParams(
                stability_mode=mode,
                target_reasoning_depth=5,
                retrieval_budget_multiplier=1.0,
                retrieval_depth=3,
                retrieval_breadth=10,
                consolidation_aggressiveness=0.5,
                consolidation_mode=ConsolidationMode.NORMAL,
                compression_strength=0.3,
                decay_rate_modifier=1.0,
                abstraction_restriction=False,
                emergency_actions=(),
                snapshot_policy="normal",
                cooldown_ms=0.0,
            )
        elif mode == StabilityMode.ELEVATED:
            return OperationalParams(
                stability_mode=mode,
                target_reasoning_depth=4,
                retrieval_budget_multiplier=0.85,
                retrieval_depth=2,
                retrieval_breadth=8,
                consolidation_aggressiveness=0.65,
                consolidation_mode=ConsolidationMode.NORMAL,
                compression_strength=0.45,
                decay_rate_modifier=1.1,
                abstraction_restriction=False,
                emergency_actions=(),
                snapshot_policy="normal",
                cooldown_ms=100.0,
            )
        elif mode == StabilityMode.DEGRADED:
            return OperationalParams(
                stability_mode=mode,
                target_reasoning_depth=3,
                retrieval_budget_multiplier=0.65,
                retrieval_depth=2,
                retrieval_breadth=5,
                consolidation_aggressiveness=0.80,
                consolidation_mode=ConsolidationMode.AGGRESSIVE,
                compression_strength=0.60,
                decay_rate_modifier=1.3,
                abstraction_restriction=False,
                emergency_actions=("reduce_retrieval", "increase_compression"),
                snapshot_policy="normal",
                cooldown_ms=300.0,
            )
        elif mode == StabilityMode.CRITICAL:
            return OperationalParams(
                stability_mode=mode,
                target_reasoning_depth=2,
                retrieval_budget_multiplier=0.40,
                retrieval_depth=1,
                retrieval_breadth=3,
                consolidation_aggressiveness=0.95,
                consolidation_mode=ConsolidationMode.AGGRESSIVE,
                compression_strength=0.75,
                decay_rate_modifier=1.6,
                abstraction_restriction=True,
                emergency_actions=("reduce_retrieval", "increase_compression",
                                   "suspend_scheduler", "shrink_cache"),
                snapshot_policy="checkpoint_only",
                cooldown_ms=1000.0,
            )
        elif mode == StabilityMode.EMERGENCY:
            return OperationalParams(
                stability_mode=mode,
                target_reasoning_depth=1,
                retrieval_budget_multiplier=0.20,
                retrieval_depth=1,
                retrieval_breadth=2,
                consolidation_aggressiveness=1.0,
                consolidation_mode=ConsolidationMode.AGGRESSIVE,
                compression_strength=0.90,
                decay_rate_modifier=2.0,
                abstraction_restriction=True,
                emergency_actions=("reduce_retrieval", "increase_compression",
                                   "suspend_scheduler", "shrink_cache",
                                   "reset_storm_guard", "drain_pressure"),
                snapshot_policy="freeze",
                cooldown_ms=2000.0,
            )
        else:  # RECOVERY
            return OperationalParams(
                stability_mode=mode,
                target_reasoning_depth=2,
                retrieval_budget_multiplier=0.50,
                retrieval_depth=2,
                retrieval_breadth=4,
                consolidation_aggressiveness=0.70,
                consolidation_mode=ConsolidationMode.NORMAL,
                compression_strength=0.55,
                decay_rate_modifier=1.2,
                abstraction_restriction=False,
                emergency_actions=("gradual_recovery",),
                snapshot_policy="normal",
                cooldown_ms=500.0,
            )


# ── Utils ─────────────────────────────────────────────────────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    if math.isnan(v) or math.isinf(v):
        return lo
    return max(lo, min(hi, v))
