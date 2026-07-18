"""
edp.runtime.inference_queue — Fila + lock para inferências LLM.

Princípio operacional:
  Em CPU-only 8GB, 2 inferências paralelas matam o sistema:
    - cada Ollama process consome ~2-4GB
    - CPU vira contenção total
    - latência sobe 5-10x para AMBAS
    - WebSocket de ambas as sessões morre

Solução pragmática:
  Fila assíncrona com max_concurrent=1.
  Próxima requisição espera (com timeout) a anterior terminar.
  Backpressure: se a fila tem >N pendentes, recusa novos.

Modular para evolução:
  max_concurrent é parametrizável. Em hardware melhor (32GB+):
    EDP_INFERENCE_MAX_CONCURRENT=2 (ou mais)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional

from ..clock import now as _now

logger = logging.getLogger("edp.runtime.queue")


MAX_CONCURRENT  = int(os.environ.get("EDP_INFERENCE_MAX_CONCURRENT", "1"))
MAX_QUEUED      = int(os.environ.get("EDP_INFERENCE_MAX_QUEUED",     "3"))
ACQUIRE_TIMEOUT = float(os.environ.get("EDP_INFERENCE_ACQUIRE_TIMEOUT_S", "60"))


class QueueFull(Exception):
    """Fila cheia — sistema sobrecarregado."""


class QueueTimeout(Exception):
    """Espera pelo slot excedeu o limite."""


@dataclass
class QueueStats:
    active:        int   = 0
    queued:        int   = 0
    total_served:  int   = 0
    total_rejected: int  = 0
    total_timeout: int   = 0
    avg_wait_ms:   float = 0.0
    avg_run_ms:    float = 0.0

    def to_dict(self) -> dict:
        return {
            "active":         self.active,
            "queued":         self.queued,
            "total_served":   self.total_served,
            "total_rejected": self.total_rejected,
            "total_timeout":  self.total_timeout,
            "avg_wait_ms":    round(self.avg_wait_ms, 1),
            "avg_run_ms":     round(self.avg_run_ms, 1),
            "max_concurrent": MAX_CONCURRENT,
            "max_queued":     MAX_QUEUED,
        }


class InferenceQueue:
    """
    Fila assíncrona thread-safe para inferências.

    Uso:
        queue = get_queue()
        async with queue.slot(session_id="x") as token:
            # token.is_cancelled diz se cliente desconectou
            for chunk in stream():
                if token.is_cancelled:
                    break
                yield chunk
    """

    def __init__(self,
                 max_concurrent: int = MAX_CONCURRENT,
                 max_queued:     int = MAX_QUEUED) -> None:
        self.max_concurrent = max_concurrent
        self.max_queued     = max_queued
        self._semaphore     = asyncio.Semaphore(max_concurrent)
        self._queued_count  = 0
        self._active_count  = 0
        self._lock          = asyncio.Lock()
        self._stats         = QueueStats()
        self._EMA           = 0.2
        logger.info(
            "[queue] inicializado | max_concurrent=%d max_queued=%d",
            max_concurrent, max_queued,
        )

    @property
    def stats(self) -> QueueStats:
        # Snapshot atômico
        self._stats.active = self._active_count
        self._stats.queued = self._queued_count
        return self._stats

    @asynccontextmanager
    async def slot(self,
                   session_id: str = "?",
                   timeout_s: float = ACQUIRE_TIMEOUT):
        """
        Context manager que adquire um slot de inferência.

        Yields: CancelToken (verificado pelos chunks da inferência)
        Raises: QueueFull, QueueTimeout
        """
        # ── Backpressure: rejeita se fila cheia ──────────────────────────────
        if self._queued_count >= self.max_queued:
            self._stats.total_rejected += 1
            logger.warning(
                "[queue] BACKPRESSURE | session=%s queued=%d/%d rejected",
                session_id, self._queued_count, self.max_queued,
            )
            raise QueueFull(
                f"Fila cheia: {self._queued_count}/{self.max_queued} pendentes. "
                "Aguarde a inferência atual terminar."
            )

        async with self._lock:
            self._queued_count += 1
        wait_start = time.perf_counter()

        token = CancelToken(session_id=session_id)
        try:
            # ── Espera slot com timeout ──────────────────────────────────────
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=timeout_s)
            except asyncio.TimeoutError:
                self._stats.total_timeout += 1
                logger.warning(
                    "[queue] TIMEOUT esperando slot | session=%s waited=%.1fs",
                    session_id, timeout_s,
                )
                raise QueueTimeout(
                    f"Timeout {timeout_s}s esperando slot de inferência"
                )

            # ── Slot adquirido ───────────────────────────────────────────────
            wait_ms = (time.perf_counter() - wait_start) * 1000.0
            async with self._lock:
                self._queued_count -= 1
                self._active_count += 1
            self._stats.avg_wait_ms = (
                self._EMA * wait_ms + (1 - self._EMA) * self._stats.avg_wait_ms
            )
            run_start = time.perf_counter()

            if wait_ms > 100:  # só loga se esperou de fato
                logger.info(
                    "[queue] slot adquirido | session=%s wait=%.0fms active=%d",
                    session_id, wait_ms, self._active_count,
                )

            try:
                yield token
            finally:
                run_ms = (time.perf_counter() - run_start) * 1000.0
                self._stats.avg_run_ms = (
                    self._EMA * run_ms + (1 - self._EMA) * self._stats.avg_run_ms
                )
                self._stats.total_served += 1
                async with self._lock:
                    self._active_count -= 1
                self._semaphore.release()
                logger.debug(
                    "[queue] slot liberado | session=%s run=%.0fms cancelled=%s",
                    session_id, run_ms, token.is_cancelled,
                )
        except (QueueTimeout, QueueFull):
            # Limpa contador de queued em caso de erro antes de adquirir
            async with self._lock:
                if self._queued_count > 0:
                    self._queued_count -= 1
            raise


# ── Cancel Token ─────────────────────────────────────────────────────────────

@dataclass
class CancelToken:
    """
    Token cooperativo de cancelamento.

    Marcado quando WebSocket desconecta. Loops de streaming devem
    verificar token.is_cancelled e parar de yield.

    Não interrompe o Ollama em si (provider não suporta), mas:
      - para de enviar chunks ao cliente desconectado
      - evita acumular em buffers
      - libera o slot mais cedo
    """
    session_id: str
    is_cancelled: bool = False
    cancel_reason: str = ""
    cancelled_at: float = 0.0

    def cancel(self, reason: str = "client_disconnect") -> None:
        if self.is_cancelled:
            return
        self.is_cancelled  = True
        self.cancel_reason = reason
        self.cancelled_at  = _now()
        logger.info(
            "[cancel] session=%s reason=%s",
            self.session_id, reason,
        )


# ── Singleton global ─────────────────────────────────────────────────────────

_queue: Optional[InferenceQueue] = None


def get_queue() -> InferenceQueue:
    global _queue
    if _queue is None:
        _queue = InferenceQueue()
    return _queue
