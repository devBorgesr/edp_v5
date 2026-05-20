"""
edp.runtime.pressure_governor — Memory Pressure Governor

Princípio operacional:
  Em hardware modesto (8GB), swap silencioso é o vetor #1 de colapso.
  Ollama + FastAPI + sentence-transformers + vector store já consomem 3-4GB.
  Quando RAM livre cai < 1.2GB, qualquer nova carga ativa swap silencioso
  → latência sobe 10x, WebSocket morre, sistema parece "travado".

Estratégia (degradação, não shutdown):
  ✓ RAM > 2.0GB → NORMAL: tudo liberado
  ⚠ 1.2GB < RAM < 2.0GB → WARNING: novas inferências limitadas a contexto reduzido
  🔴 RAM < 1.2GB → CRITICAL: REJEITA novas inferências mas MANTÉM as em andamento

NÃO derruba o sistema. NÃO mata inferências ativas.
Apenas recusa novas requisições até pressure aliviar.

Não usa thread separada para evitar overhead — chamado on-demand
no início de cada nova inferência.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional

logger = logging.getLogger("edp.runtime.pressure")


# ── Thresholds (overridáveis por env) ────────────────────────────────────────
CRITICAL_GB = float(os.environ.get("EDP_PRESSURE_CRITICAL_GB", "1.2"))
WARNING_GB  = float(os.environ.get("EDP_PRESSURE_WARNING_GB",  "2.0"))
CHECK_TTL_S = 5.0   # cache: não consulta psutil mais que 1×/5s


class PressureLevel(str, Enum):
    NORMAL   = "normal"
    WARNING  = "warning"
    CRITICAL = "critical"
    UNKNOWN  = "unknown"   # psutil indisponível


@dataclass
class PressureReading:
    level:        PressureLevel
    available_gb: float
    total_gb:     float
    used_pct:     float
    timestamp:    float

    def to_dict(self) -> dict:
        return {**asdict(self), "level": self.level.value}


class MemoryPressureGovernor:
    """
    Governor singleton thread-safe.

    Uso típico:
        gov = get_governor()
        if not gov.allow_new_inference():
            raise RuntimeError("RAM pressure crítica")
        # ... inferência ...

    Reading cacheada por CHECK_TTL_S para evitar overhead de psutil.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_reading: Optional[PressureReading] = None
        self._psutil = None
        self._init_psutil()

    def _init_psutil(self) -> None:
        try:
            import psutil
            self._psutil = psutil
            logger.info(
                "[pressure] psutil OK | critical=%.1fGB warning=%.1fGB",
                CRITICAL_GB, WARNING_GB,
            )
        except ImportError:
            logger.warning(
                "[pressure] psutil indisponível — pressure governor em modo NOOP. "
                "Instale: pip install psutil"
            )

    def read(self, force: bool = False) -> PressureReading:
        """
        Retorna reading atual. Cacheada por CHECK_TTL_S a menos que force=True.
        """
        with self._lock:
            now = time.time()
            if (not force
                and self._last_reading is not None
                and now - self._last_reading.timestamp < CHECK_TTL_S):
                return self._last_reading

            if self._psutil is None:
                self._last_reading = PressureReading(
                    level=PressureLevel.UNKNOWN,
                    available_gb=0.0,
                    total_gb=0.0,
                    used_pct=0.0,
                    timestamp=now,
                )
                return self._last_reading

            vm = self._psutil.virtual_memory()
            available_gb = vm.available / (1024 ** 3)
            total_gb     = vm.total     / (1024 ** 3)
            used_pct     = vm.percent

            if available_gb < CRITICAL_GB:
                level = PressureLevel.CRITICAL
            elif available_gb < WARNING_GB:
                level = PressureLevel.WARNING
            else:
                level = PressureLevel.NORMAL

            self._last_reading = PressureReading(
                level=level,
                available_gb=round(available_gb, 2),
                total_gb=round(total_gb, 2),
                used_pct=round(used_pct, 1),
                timestamp=now,
            )

            # Log transitions
            if level == PressureLevel.CRITICAL:
                logger.warning(
                    "[pressure] CRITICAL | available=%.2fGB used=%.1f%%",
                    available_gb, used_pct,
                )
            elif level == PressureLevel.WARNING:
                logger.info(
                    "[pressure] WARNING | available=%.2fGB used=%.1f%%",
                    available_gb, used_pct,
                )

            return self._last_reading

    def allow_new_inference(self) -> bool:
        """
        Decide se uma NOVA inferência pode iniciar.
        Não afeta inferências em andamento.
        """
        reading = self.read()
        # UNKNOWN (psutil ausente) → permite, pois não temos sinal
        return reading.level != PressureLevel.CRITICAL

    def context_budget_factor(self) -> float:
        """
        Multiplicador para o budget de contexto.
        NORMAL=1.0, WARNING=0.5, CRITICAL=0.25 (mas não chega aqui pois rejeita)
        """
        reading = self.read()
        return {
            PressureLevel.NORMAL:   1.0,
            PressureLevel.WARNING:  0.5,
            PressureLevel.CRITICAL: 0.25,
            PressureLevel.UNKNOWN:  1.0,
        }[reading.level]


# ── Singleton global ─────────────────────────────────────────────────────────

_governor: Optional[MemoryPressureGovernor] = None
_governor_lock = threading.Lock()


def get_governor() -> MemoryPressureGovernor:
    global _governor
    if _governor is None:
        with _governor_lock:
            if _governor is None:
                _governor = MemoryPressureGovernor()
    return _governor
