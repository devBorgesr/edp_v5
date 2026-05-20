"""
storm_guard.py — EDP v3.2 Retrieval Storm Guard

Circuit breaker cognitivo. Detecta e mitiga:
  - Retrieval storms (repetição de queries similares)
  - Recursion explosion
  - Snapshot explosion
  - Consolidation cascade
  - Semantic saturation
  - Scheduler congestion

Mecanismo:
  Sliding window de eventos recentes (configurable).
  EMA sobre taxas de ocorrência → composite_storm_score.
  Quando score > threshold → StormDetected (ou soft throttle).
  Cooldown automático após detecção.
  Auto-recovery quando score cai abaixo de clear_threshold.

Contrato com orchestrator_v32:
  guard.effective_k(k)          → int (pode reduzir k sob storm)
  guard.record(results)         → raise StormDetected ou None
  guard.current_metrics()       → StormMetrics
  guard.reset()                 → None
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Deque, List, Optional, Tuple

from .exceptions import StormDetected
from .types_v32 import DecisionEvent, DecisionEventType, RetrievalResult


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class StormGuardConfig:
    # Detecção
    storm_threshold:   float = 0.70     # score acima → storm ativo
    clear_threshold:   float = 0.35     # score abaixo → storm encerrado
    window_size:       int   = 50       # número de eventos na janela

    # EMA
    ema_alpha:         float = 0.25

    # Cooldown
    cooldown_ms:       float = 2000.0   # ms de cooldown após storm
    min_k_under_storm: int   = 1        # k mínimo sob storm (nunca zero)

    # Componentes do storm score (pesos devem somar 1.0)
    w_retrieval_rate:  float = 0.30
    w_similarity_sat:  float = 0.25
    w_recursion:       float = 0.20
    w_abstraction:     float = 0.15
    w_scheduler:       float = 0.10

    # Saturation detection
    similarity_sat_threshold: float = 0.88  # resultado muito similar → saturação


# ── Métricas ──────────────────────────────────────────────────────────────────

@dataclass
class StormMetrics:
    composite_storm_score: float = 0.0
    retrieval_rate_score:  float = 0.0
    similarity_sat_score:  float = 0.0
    recursion_score:       float = 0.0
    abstraction_entropy:   float = 0.0
    scheduler_score:       float = 0.0
    storm_active:          bool  = False
    storm_count:           int   = 0
    last_storm_at:         float = 0.0
    cooldown_remaining_ms: float = 0.0
    effective_k_multiplier: float = 1.0
    in_cooldown:           bool  = False

    def is_storm(self) -> bool:
        return self.storm_active or self.in_cooldown

    def snapshot(self) -> dict:
        return {
            "composite_score":   round(self.composite_storm_score, 4),
            "storm_active":      self.storm_active,
            "in_cooldown":       self.in_cooldown,
            "storm_count":       self.storm_count,
            "effective_k_mult":  round(self.effective_k_multiplier, 3),
            "abstraction_entropy": round(self.abstraction_entropy, 4),
        }


# ── Guard ─────────────────────────────────────────────────────────────────────

class RetrievalStormGuard:
    """
    Circuit breaker para overload cognitivo.

    Thread-safe. EMA sobre sliding window.
    Nunca bloqueia o runtime — apenas reduz k e emite StormDetected.
    """

    def __init__(
        self,
        config: Optional[StormGuardConfig] = None,
        event_sink: Optional[Callable[[DecisionEvent], None]] = None,
    ) -> None:
        self._cfg   = config or StormGuardConfig()
        self._sink  = event_sink or (lambda _: None)
        self._lock  = threading.Lock()

        # EMA state
        self._ema_retrieval:   float = 0.0
        self._ema_similarity:  float = 0.0
        self._ema_recursion:   float = 0.0
        self._ema_abstraction: float = 0.0
        self._ema_scheduler:   float = 0.0

        # Sliding window: timestamps de retrievals recentes
        self._window: Deque[float] = deque(maxlen=self._cfg.window_size)

        # Storm state
        self._storm_active  = False
        self._storm_count   = 0
        self._storm_start   = 0.0
        self._cooldown_until = 0.0

        self._metrics = StormMetrics()

    # ── API pública ────────────────────────────────────────────────────────────

    def effective_k(self, k: int) -> int:
        """
        Retorna k efetivo após redução por storm.
        Nunca retorna menos que min_k_under_storm.
        """
        with self._lock:
            score = self._composite_score_unsafe()
            if score < self._cfg.storm_threshold * 0.5:
                return k
            # Redução proporcional ao score
            mult = max(
                self._cfg.min_k_under_storm / max(k, 1),
                1.0 - score,
            )
            self._metrics.effective_k_multiplier = mult
        return max(self._cfg.min_k_under_storm, int(k * mult))

    def record(self, results: List[RetrievalResult]) -> None:
        """
        Registra resultado de retrieval. Raise StormDetected se storm detectado.
        Nunca bloqueia o caller por mais de O(window_size).
        """
        now = time.monotonic()
        with self._lock:
            self._window.append(now)

            # Atualiza sinais
            self._update_retrieval_rate(now)
            self._update_similarity_saturation(results)
            self._update_abstraction_entropy(results)

            score = self._composite_score_unsafe()
            self._metrics.composite_storm_score = score

            in_cooldown = now < self._cooldown_until

            if score >= self._cfg.storm_threshold and not self._storm_active:
                self._storm_active  = True
                self._storm_count  += 1
                self._storm_start   = now
                self._cooldown_until = now + self._cfg.cooldown_ms / 1000.0
                self._metrics.storm_active = True
                self._metrics.storm_count  = self._storm_count
                self._metrics.last_storm_at = now
                self._emit_storm(score)
                raise StormDetected(
                    pattern="composite_threshold",
                    score=score,
                )

            elif self._storm_active and score < self._cfg.clear_threshold and not in_cooldown:
                self._storm_active = False
                self._metrics.storm_active = False
                self._emit_recovery(score)

            self._metrics.in_cooldown = in_cooldown

    def record_recursion(self, depth: int, budget: int) -> None:
        """Registra pressão de recursão."""
        with self._lock:
            ratio = depth / max(budget, 1)
            self._ema_recursion = (
                self._cfg.ema_alpha * ratio
                + (1 - self._cfg.ema_alpha) * self._ema_recursion
            )
            self._metrics.recursion_score = self._ema_recursion

    def record_scheduler_action(self) -> None:
        """Registra ação do scheduler — contribui para scheduler pressure."""
        with self._lock:
            self._ema_scheduler = (
                self._cfg.ema_alpha * 1.0
                + (1 - self._cfg.ema_alpha) * self._ema_scheduler
            )
            self._metrics.scheduler_score = self._ema_scheduler

    def current_metrics(self) -> StormMetrics:
        with self._lock:
            now  = time.monotonic()
            cdms = max(0.0, (self._cooldown_until - now) * 1000)
            return StormMetrics(
                composite_storm_score=self._metrics.composite_storm_score,
                retrieval_rate_score=self._ema_retrieval,
                similarity_sat_score=self._ema_similarity,
                recursion_score=self._ema_recursion,
                abstraction_entropy=self._metrics.abstraction_entropy,
                scheduler_score=self._ema_scheduler,
                storm_active=self._storm_active,
                storm_count=self._storm_count,
                last_storm_at=self._metrics.last_storm_at,
                cooldown_remaining_ms=cdms,
                effective_k_multiplier=self._metrics.effective_k_multiplier,
                in_cooldown=now < self._cooldown_until,
            )

    def reset(self) -> None:
        """Reseta estado acumulado — chamado pelo orchestrator em EMERGENCY."""
        with self._lock:
            self._ema_retrieval   = 0.0
            self._ema_similarity  = 0.0
            self._ema_recursion   = 0.0
            self._ema_abstraction = 0.0
            self._ema_scheduler   = 0.0
            self._storm_active    = False
            self._cooldown_until  = 0.0
            self._window.clear()
            self._metrics = StormMetrics(storm_count=self._storm_count)

    def metrics(self) -> dict:
        return self.health_report()

    def health_report(self) -> dict:
        m = self.current_metrics()
        return {
            "storm_active":  m.storm_active,
            "score":         round(m.composite_storm_score, 4),
            "storm_count":   m.storm_count,
            "in_cooldown":   m.in_cooldown,
            "cooldown_ms":   round(m.cooldown_remaining_ms, 1),
        }

    # ── Interno ────────────────────────────────────────────────────────────────

    def _composite_score_unsafe(self) -> float:
        cfg = self._cfg
        return min(1.0, (
            cfg.w_retrieval_rate * self._ema_retrieval
            + cfg.w_similarity_sat * self._ema_similarity
            + cfg.w_recursion * self._ema_recursion
            + cfg.w_abstraction * self._ema_abstraction
            + cfg.w_scheduler * self._ema_scheduler
        ))

    def _update_retrieval_rate(self, now: float) -> None:
        """Taxa de retrieval na janela deslizante → [0,1]."""
        if len(self._window) < 2:
            return
        oldest = self._window[0]
        elapsed = max(now - oldest, 1e-3)
        rate_per_s = len(self._window) / elapsed
        # Normaliza: 10 queries/s → score=1.0
        normalized = min(rate_per_s / 10.0, 1.0)
        self._ema_retrieval = (
            self._cfg.ema_alpha * normalized
            + (1 - self._cfg.ema_alpha) * self._ema_retrieval
        )
        self._metrics.retrieval_rate_score = self._ema_retrieval

    def _update_similarity_saturation(self, results: List[RetrievalResult]) -> None:
        """Fração de resultados com score muito alto → saturation."""
        if not results:
            return
        sat_count = sum(1 for r in results if r.score >= self._cfg.similarity_sat_threshold)
        ratio = sat_count / len(results)
        self._ema_similarity = (
            self._cfg.ema_alpha * ratio
            + (1 - self._cfg.ema_alpha) * self._ema_similarity
        )
        self._metrics.similarity_sat_score = self._ema_similarity

    def _update_abstraction_entropy(self, results: List[RetrievalResult]) -> None:
        """Entropia da distribuição de abstraction levels nos resultados."""
        if not results:
            return
        counts: dict = {}
        for r in results:
            lv = r.retrieval_depth.value
            counts[lv] = counts.get(lv, 0) + 1
        n = len(results)
        entropy = 0.0
        for c in counts.values():
            p = c / n
            if p > 0:
                entropy -= p * math.log2(p)
        # Normaliza pelo máximo teórico (log2 do número de levels)
        max_entropy = math.log2(max(len(counts), 2))
        normalized  = entropy / max_entropy if max_entropy > 0 else 0.0
        # Alta entropia → diversidade → BOM (baixo risco de storm)
        # Baixa entropia → concentração → storm risk
        risk = 1.0 - normalized
        self._ema_abstraction = (
            self._cfg.ema_alpha * risk
            + (1 - self._cfg.ema_alpha) * self._ema_abstraction
        )
        self._metrics.abstraction_entropy = normalized

    def _emit_storm(self, score: float) -> None:
        try:
            self._sink(DecisionEvent(
                event_type=DecisionEventType.PRESSURE_ESCALATION,
                metadata=(
                    ("event", "storm_detected"),
                    ("score", str(round(score, 4))),
                    ("storm_count", str(self._storm_count)),
                ),
            ))
        except Exception:
            pass  # _emit never kills runtime — failure tracked externally

    def _emit_recovery(self, score: float) -> None:
        try:
            self._sink(DecisionEvent(
                event_type=DecisionEventType.PRESSURE_ESCALATION,
                metadata=(
                    ("event", "storm_cleared"),
                    ("score", str(round(score, 4))),
                ),
            ))
        except Exception:
            pass  # _emit never kills runtime — failure tracked externally
