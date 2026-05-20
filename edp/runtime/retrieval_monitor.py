"""
edp.runtime.retrieval_monitor — Monitor de qualidade de retrieval.

Princípio:
  Retrieval degrada silenciosamente. Score médio cai de 0.7 -> 0.4 ao longo
  de semanas e ninguém percebe — o sistema só "começa a esquecer".

Estratégia LEVE (não overengineering):
  - Rolling window de N dias (default 7)
  - Buckets diários (evita crescimento ilimitado)
  - Snapshot em memória, sem persistência
  - Detecção de tendência via comparação half/half

Custo: ~5KB/dia x 7 = ~35KB total. Negligível.

Triggers acionáveis (não decorativos):
  - score 7d cai >20% vs primeiros dias -> WARNING (cooldown 10min)
  - repetition_rate > 60% -> WARNING
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional

logger = logging.getLogger("edp.runtime.retrieval_monitor")

ROLLING_DAYS = 7
SECONDS_PER_DAY = 86400


@dataclass
class DailyBucket:
    day_start_ts: float
    turn_count:   int = 0
    sum_top:      float = 0.0
    sum_topk:     float = 0.0
    empty_count:  int = 0
    repeat_count: int = 0
    last_top_ids: List[str] = field(default_factory=list)

    @property
    def avg_top(self) -> float:
        return self.sum_top / self.turn_count if self.turn_count else 0.0

    @property
    def avg_topk(self) -> float:
        return self.sum_topk / self.turn_count if self.turn_count else 0.0

    @property
    def empty_rate(self) -> float:
        return self.empty_count / self.turn_count if self.turn_count else 0.0

    @property
    def repeat_rate(self) -> float:
        return self.repeat_count / self.turn_count if self.turn_count else 0.0

    def to_dict(self) -> dict:
        return {
            "day_start_ts": self.day_start_ts,
            "turns":        self.turn_count,
            "avg_top":      round(self.avg_top, 4),
            "avg_topk":     round(self.avg_topk, 4),
            "empty_rate":   round(self.empty_rate, 3),
            "repeat_rate":  round(self.repeat_rate, 3),
        }


class RetrievalQualityMonitor:
    def __init__(self, rolling_days: int = ROLLING_DAYS) -> None:
        self.rolling_days = rolling_days
        self.buckets: Deque[DailyBucket] = deque(maxlen=rolling_days)
        self._lock = threading.Lock()
        self._last_warn_ts: float = 0.0
        self._warn_cooldown: float = 600.0
        logger.info(
            "[retrieval_monitor] inicializado rolling_days=%d",
            rolling_days,
        )

    def _current_bucket(self) -> DailyBucket:
        now = time.time()
        day_start = now - (now % SECONDS_PER_DAY)
        if not self.buckets or self.buckets[-1].day_start_ts < day_start:
            self.buckets.append(DailyBucket(day_start_ts=day_start))
        return self.buckets[-1]

    def record_turn(
        self,
        top_scores: List[float],
        result_ids: Optional[List[str]] = None,
    ) -> None:
        """Registra 1 turno de retrieval."""
        with self._lock:
            bucket = self._current_bucket()
            bucket.turn_count += 1

            if not top_scores:
                bucket.empty_count += 1
                bucket.last_top_ids = []
                return

            top_score = float(top_scores[0])
            avg_topk  = sum(top_scores) / len(top_scores)
            bucket.sum_top  += top_score
            bucket.sum_topk += avg_topk

            current_ids = list(result_ids or [])
            if current_ids and bucket.last_top_ids:
                overlap = set(current_ids) & set(bucket.last_top_ids)
                if len(overlap) >= min(2, len(current_ids)):
                    bucket.repeat_count += 1
            bucket.last_top_ids = current_ids

        self._check_warnings()

    def _check_warnings(self) -> None:
        now = time.time()
        if now - self._last_warn_ts < self._warn_cooldown:
            return

        snap = self.snapshot()
        trend = snap.get("trend", "stable")
        recent_avg = snap.get("avg_top_recent", 0)
        older_avg  = snap.get("avg_top_older", 0)

        if trend == "declining" and older_avg > 0:
            drop_pct = (older_avg - recent_avg) / older_avg * 100
            if drop_pct > 20:
                logger.warning(
                    "[retrieval_monitor] DEGRADACAO: avg_top caiu %.1f%% "
                    "(de %.3f para %.3f) em %d dias",
                    drop_pct, older_avg, recent_avg, self.rolling_days,
                )
                self._last_warn_ts = now
                return

        latest = snap.get("days", [])
        if latest:
            last = latest[-1]
            if last["turns"] >= 5 and last["repeat_rate"] > 0.6:
                logger.warning(
                    "[retrieval_monitor] retrieval REPETITIVO: %.0f%% "
                    "dos turnos retornam memorias iguais",
                    last["repeat_rate"] * 100,
                )
                self._last_warn_ts = now

    def snapshot(self) -> dict:
        with self._lock:
            days = [b.to_dict() for b in self.buckets]

        if len(days) < 2:
            return {
                "days":  days,
                "trend": "unknown",
                "rolling_days": self.rolling_days,
                "total_turns": sum(d["turns"] for d in days),
            }

        half = len(days) // 2
        older = days[:half] if half > 0 else days[:1]
        recent = days[half:] if half > 0 else days[-1:]

        older_with_turns = [d for d in older if d["turns"]]
        recent_with_turns = [d for d in recent if d["turns"]]

        older_avg = (
            sum(d["avg_top"] for d in older_with_turns) / len(older_with_turns)
            if older_with_turns else 0
        )
        recent_avg = (
            sum(d["avg_top"] for d in recent_with_turns) / len(recent_with_turns)
            if recent_with_turns else 0
        )

        if older_avg == 0:
            trend = "unknown"
        elif recent_avg < older_avg * 0.85:
            trend = "declining"
        elif recent_avg > older_avg * 1.15:
            trend = "improving"
        else:
            trend = "stable"

        return {
            "days":            days,
            "trend":           trend,
            "avg_top_older":   round(older_avg, 4),
            "avg_top_recent":  round(recent_avg, 4),
            "rolling_days":    self.rolling_days,
            "total_turns":     sum(d["turns"] for d in days),
        }


_monitor: Optional[RetrievalQualityMonitor] = None
_monitor_lock = threading.Lock()


def get_monitor() -> RetrievalQualityMonitor:
    global _monitor
    if _monitor is None:
        with _monitor_lock:
            if _monitor is None:
                _monitor = RetrievalQualityMonitor()
    return _monitor
