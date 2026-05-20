"""
economy.py — EDP v3.2 Cognitive Economy Engine

Gerencia budget cognitivo global:
  retrieval_tokens, recursion_budget, embedding_budget,
  consolidation_budget, scheduler_budget, snapshot_budget

Mecânica:
  - Budget regenera com tempo (token bucket)
  - Reduz sob overload (pressure feedback)
  - Prioriza tarefas críticas (emergency reserve)
  - Soft quotas com starvation prevention
  - ThrottleMode: NORMAL → REDUCED → MINIMAL → FROZEN

Lock ordering: economy lock adquirido APÓS snapshot_manager (conforme orchestrator_v32)
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Optional

from .exceptions import EconomyBudgetExceeded
from .types_v32 import DecisionEvent, DecisionEventType


# ── Enums ──────────────────────────────────────────────────────────────────────

class ThrottleMode(str, Enum):
    NORMAL  = "normal"    # operação normal
    REDUCED = "reduced"   # retrieval reduzido
    MINIMAL = "minimal"   # apenas tarefas críticas
    FROZEN  = "frozen"    # somente emergency reserve


# ── Configuração ──────────────────────────────────────────────────────────────

@dataclass
class BudgetCosts:
    retrieval_base:       float = 0.10   # custo base por retrieval
    retrieval_per_level:  float = 0.05   # custo adicional por nível de abstração
    recursion_per_depth:  float = 0.08   # custo por nível de recursão
    embedding_per_call:   float = 0.03   # custo por embedding
    consolidation_base:   float = 0.20   # custo de consolidação
    snapshot_base:        float = 0.02   # custo de snapshot
    scheduler_base:       float = 0.05   # custo por ação de scheduler


@dataclass
class EconomyConfig:
    # Budget inicial e máximo por recurso
    retrieval_budget:     float = 100.0
    recursion_budget:     float = 50.0
    embedding_budget:     float = 200.0
    consolidation_budget: float = 30.0
    scheduler_budget:     float = 80.0
    snapshot_budget:      float = 150.0

    # Regeneração: tokens por segundo
    regen_rate:           float = 5.0     # tokens/s base
    regen_rate_degraded:  float = 2.0     # tokens/s sob pressão
    emergency_reserve:    float = 10.0    # reserva para tarefas críticas

    # Thresholds para mudança de modo
    reduced_threshold:    float = 0.40    # abaixo de 40% → REDUCED
    minimal_threshold:    float = 0.20    # abaixo de 20% → MINIMAL
    frozen_threshold:     float = 0.05    # abaixo de 5%  → FROZEN

    # Starvation prevention
    starvation_boost_ms:  float = 5000.0  # ms sem acesso → boost automático
    starvation_boost_amt: float = 5.0     # quantidade do boost

    # Costs
    costs: BudgetCosts = field(default_factory=BudgetCosts)


# ── Métricas ──────────────────────────────────────────────────────────────────

@dataclass
class EconomyMetrics:
    total_requests:   int   = 0
    total_granted:    int   = 0
    total_denied:     int   = 0
    total_spent:      float = 0.0
    total_regenerated: float = 0.0
    mode_changes:     int   = 0
    starvation_boosts: int  = 0
    current_mode:     str   = ThrottleMode.NORMAL

    def grant_rate(self) -> float:
        t = self.total_requests
        return self.total_granted / t if t > 0 else 1.0


# ── Resource Bucket (token bucket por recurso) ────────────────────────────────

class _ResourceBucket:
    """Token bucket para um recurso cognitivo."""

    def __init__(self, capacity: float, regen_rate: float) -> None:
        self._capacity     = capacity
        self._regen_rate   = regen_rate
        self._tokens       = capacity
        self._last_regen   = time.monotonic()
        self._last_access  = time.monotonic()
        self._lock         = threading.Lock()

    def _regen(self) -> None:
        now     = time.monotonic()
        elapsed = now - self._last_regen
        gained  = elapsed * self._regen_rate
        self._tokens       = min(self._capacity, self._tokens + gained)
        self._last_regen   = now

    def request(self, cost: float, is_critical: bool = False,
                emergency_reserve: float = 0.0) -> bool:
        with self._lock:
            self._regen()
            floor = emergency_reserve if not is_critical else 0.0
            if self._tokens - cost >= floor:
                self._tokens      -= cost
                self._last_access  = time.monotonic()
                return True
            return False

    def boost(self, amount: float) -> None:
        with self._lock:
            self._tokens = min(self._capacity, self._tokens + amount)

    def fill_ratio(self) -> float:
        with self._lock:
            self._regen()
            return self._tokens / max(self._capacity, 1e-8)

    def idle_ms(self) -> float:
        with self._lock:
            return (time.monotonic() - self._last_access) * 1000

    def set_regen_rate(self, rate: float) -> None:
        with self._lock:
            self._regen_rate = max(rate, 0.0)

    def snapshot(self) -> dict:
        with self._lock:
            self._regen()
            return {
                "tokens":    round(self._tokens, 2),
                "capacity":  self._capacity,
                "fill_ratio": round(self._fill_ratio_unsafe(), 4),
                "regen_rate": self._regen_rate,
            }

    def _fill_ratio_unsafe(self) -> float:
        return self._tokens / max(self._capacity, 1e-8)


# ── Engine ────────────────────────────────────────────────────────────────────

class CognitiveEconomyEngine:
    """
    Motor de budget cognitivo.

    Thread-safe via _mode_lock para mudanças de modo,
    e _ResourceBucket individual por recurso.

    Contrato com orchestrator_v32:
      engine.request_retrieval(levels, is_critical)  → raise EconomyBudgetExceeded ou None
      engine.update_pressure(global_pressure)         → None
      engine.snapshot()                               → dict
    """

    def __init__(
        self,
        config: Optional[EconomyConfig] = None,
        event_sink: Optional[Callable[[DecisionEvent], None]] = None,
    ) -> None:
        self._cfg      = config or EconomyConfig()
        self._sink     = event_sink or (lambda _: None)
        self._mode     = ThrottleMode.NORMAL
        self._mode_lock = threading.Lock()
        self._metrics  = EconomyMetrics()
        self._pressure_ema = 0.0
        self._EMA_ALPHA    = 0.15

        cfg = self._cfg
        self._buckets: Dict[str, _ResourceBucket] = {
            "retrieval":     _ResourceBucket(cfg.retrieval_budget,     cfg.regen_rate),
            "recursion":     _ResourceBucket(cfg.recursion_budget,     cfg.regen_rate * 0.5),
            "embedding":     _ResourceBucket(cfg.embedding_budget,     cfg.regen_rate * 2.0),
            "consolidation": _ResourceBucket(cfg.consolidation_budget, cfg.regen_rate * 0.3),
            "scheduler":     _ResourceBucket(cfg.scheduler_budget,     cfg.regen_rate),
            "snapshot":      _ResourceBucket(cfg.snapshot_budget,      cfg.regen_rate * 3.0),
        }

        # Background starvation prevention
        self._starvation_thread = threading.Thread(
            target=self._starvation_loop, daemon=True
        )
        self._starvation_thread.start()

    # ── API pública ────────────────────────────────────────────────────────────

    def request_retrieval(self, levels: int = 1, is_critical: bool = False) -> None:
        """
        Solicita budget de retrieval. Raise EconomyBudgetExceeded se negado.
        levels: profundidade de retrieval (multiplica custo).
        """
        cost = self._cfg.costs.retrieval_base + levels * self._cfg.costs.retrieval_per_level
        self._request("retrieval", cost, is_critical)

    def request_recursion(self, depth: int = 1, is_critical: bool = False) -> None:
        cost = depth * self._cfg.costs.recursion_per_depth
        self._request("recursion", cost, is_critical)

    def request_embedding(self, is_critical: bool = False) -> None:
        self._request("embedding", self._cfg.costs.embedding_per_call, is_critical)

    def request_consolidation(self, is_critical: bool = False) -> None:
        self._request("consolidation", self._cfg.costs.consolidation_base, is_critical)

    def request_scheduler(self, is_critical: bool = False) -> None:
        self._request("scheduler", self._cfg.costs.scheduler_base, is_critical)

    def request_snapshot(self, is_critical: bool = False) -> None:
        self._request("snapshot", self._cfg.costs.snapshot_base, is_critical)

    def update_pressure(self, global_pressure: float) -> None:
        """Feedback de pressão global → ajusta modo e regen rate."""
        self._pressure_ema = (
            self._EMA_ALPHA * global_pressure
            + (1 - self._EMA_ALPHA) * self._pressure_ema
        )
        self._recompute_mode()

    def current_mode(self) -> ThrottleMode:
        with self._mode_lock:
            return self._mode

    def snapshot(self) -> dict:
        with self._mode_lock:
            mode = self._mode.value
        return {
            "mode":            mode,
            "pressure_ema":    round(self._pressure_ema, 4),
            "buckets":         {k: b.snapshot() for k, b in self._buckets.items()},
            "metrics": {
                "total_requests": self._metrics.total_requests,
                "total_granted":  self._metrics.total_granted,
                "total_denied":   self._metrics.total_denied,
                "grant_rate":     round(self._metrics.grant_rate(), 4),
                "mode_changes":   self._metrics.mode_changes,
            },
        }

    def metrics(self) -> EconomyMetrics:
        with self._mode_lock:
            return EconomyMetrics(
                total_requests=self._metrics.total_requests,
                total_granted=self._metrics.total_granted,
                total_denied=self._metrics.total_denied,
                total_spent=self._metrics.total_spent,
                total_regenerated=self._metrics.total_regenerated,
                mode_changes=self._metrics.mode_changes,
                starvation_boosts=self._metrics.starvation_boosts,
                current_mode=self._mode.value,
            )

    def health_report(self) -> dict:
        fill_ratios = {k: round(b.fill_ratio(), 3) for k, b in self._buckets.items()}
        min_fill = min(fill_ratios.values()) if fill_ratios else 1.0
        return {
            "mode":        self._mode.value,
            "pressure":    round(self._pressure_ema, 4),
            "fill_ratios": fill_ratios,
            "min_fill":    min_fill,
            "grant_rate":  round(self._metrics.grant_rate(), 4),
            "healthy":     self._mode in (ThrottleMode.NORMAL, ThrottleMode.REDUCED),
        }

    # ── Interno ────────────────────────────────────────────────────────────────

    def _request(self, resource: str, cost: float, is_critical: bool) -> None:
        with self._mode_lock:
            mode = self._mode

        # FROZEN: só críticos com emergency reserve
        if mode == ThrottleMode.FROZEN and not is_critical:
            self._metrics.total_requests += 1
            self._metrics.total_denied   += 1
            raise EconomyBudgetExceeded(
                operation=resource, cost=cost,
                budget=self._cfg.emergency_reserve,
            )

        # Multiplicador por modo
        multiplier = {
            ThrottleMode.NORMAL:  1.0,
            ThrottleMode.REDUCED: 1.3,
            ThrottleMode.MINIMAL: 1.8,
            ThrottleMode.FROZEN:  2.5,
        }.get(mode, 1.0)

        effective_cost = cost * multiplier
        bucket = self._buckets.get(resource)
        if bucket is None:
            return

        self._metrics.total_requests += 1
        granted = bucket.request(
            effective_cost, is_critical, self._cfg.emergency_reserve
        )
        if granted:
            self._metrics.total_granted += 1
            self._metrics.total_spent   += effective_cost
        else:
            self._metrics.total_denied += 1
            raise EconomyBudgetExceeded(
                operation=resource, cost=effective_cost,
                budget=bucket.fill_ratio() * getattr(self._cfg, f"{resource}_budget", 100.0),
            )

    def _recompute_mode(self) -> None:
        """Recomputa ThrottleMode baseado em fill ratio mínimo e pressão EMA."""
        # fill ratio mínimo entre todos os buckets
        min_fill = min(b.fill_ratio() for b in self._buckets.values())

        # pressão EMA também influencia
        pressure_factor = 1.0 - self._pressure_ema  # 0=saturado, 1=ok
        effective = min_fill * pressure_factor

        new_mode: ThrottleMode
        cfg = self._cfg
        if effective <= cfg.frozen_threshold:
            new_mode = ThrottleMode.FROZEN
        elif effective <= cfg.minimal_threshold:
            new_mode = ThrottleMode.MINIMAL
        elif effective <= cfg.reduced_threshold:
            new_mode = ThrottleMode.REDUCED
        else:
            new_mode = ThrottleMode.NORMAL

        # Ajusta regen rate por modo
        regen = cfg.regen_rate if new_mode == ThrottleMode.NORMAL else cfg.regen_rate_degraded
        for b in self._buckets.values():
            b.set_regen_rate(regen / len(self._buckets))

        with self._mode_lock:
            if new_mode != self._mode:
                self._mode = new_mode
                self._metrics.mode_changes += 1
                self._emit_mode_change(new_mode)

    def _emit_mode_change(self, new_mode: ThrottleMode) -> None:
        try:
            self._sink(DecisionEvent(
                event_type=DecisionEventType.BUDGET_EXCEEDED,
                metadata=(
                    ("economy_mode", new_mode.value),
                    ("pressure_ema", str(round(self._pressure_ema, 4))),
                ),
            ))
        except Exception:
            pass  # _emit never kills runtime — failure tracked externally

    def _starvation_loop(self) -> None:
        """
        Background thread: detecta recursos idle por muito tempo e aplica boost.
        Previne starvation de recursos raramente usados.
        """
        while True:
            time.sleep(self._cfg.starvation_boost_ms / 1000.0 / 2.0)
            for name, bucket in self._buckets.items():
                if bucket.idle_ms() > self._cfg.starvation_boost_ms:
                    if bucket.fill_ratio() < 0.50:
                        bucket.boost(self._cfg.starvation_boost_amt)
                        self._metrics.starvation_boosts += 1
