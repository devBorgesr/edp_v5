"""
edp.runtime.pressure_governor — Memory Pressure Governor

Mede RAM REAL do host (psutil) — não confundir com edp.pressure
(StorePressureMonitor: ocupação do store episódico, sem relação com SO).
Ver cross-reference nos dois módulos.

Princípio operacional:
  Swap silencioso é o vetor #1 de colapso. Quando RAM disponível cai abaixo
  do piso, qualquer nova carga ativa swap silencioso → latência sobe 10x,
  WebSocket morre, sistema parece "travado".

Estratégia (degradação, não shutdown):
  ✓ RAM > WARNING_GB              → NORMAL: tudo liberado
  ⚠ CRITICAL_GB < RAM < WARNING_GB → WARNING: jobs com suspend_on_pressure=True pulam
  🔴 RAM < CRITICAL_GB             → CRITICAL: REJEITA nova inferência LOCAL,
                                      pausa TODO o tick do background_loop

NÃO derruba o sistema. NÃO mata inferências ativas.
Apenas recusa novas requisições até pressure aliviar.

Não usa thread separada para evitar overhead — chamado on-demand
no início de cada nova inferência.

Hardening Fase 2 (Dívida #41, T2b/T2c): defaults ORIGINAIS (CRITICAL=1.2GB,
WARNING=2.0GB) foram dimensionados para um cenário que não existe mais —
docstring histórica assumia "hardware modesto (8GB)" com "Ollama + FastAPI +
sentence-transformers + vector store já consomem 3-4GB" (inferência LOCAL).
O deployment real é API-only (Anthropic/OpenAI via rede; único consumidor
de RAM residente é o modelo de embedding, não um LLM local) rodando numa
máquina com ~4GB de RAM TOTAL (medido: `benchmark_edp.py` reporta
RAM=4.1GB), não os 8GB assumidos no design original.

Evidência de runtime acumulada pelo pesquisador (uso normal, sem inferência
local): `available` oscila 0.28–1.45GB — SEMPRE abaixo do WARNING_GB antigo
(2.0GB) e quase sempre abaixo do CRITICAL_GB antigo (1.2GB). Resultado
medido: CRITICAL era o estado PERMANENTE, com 100% dos ticks do
background_loop pulados por dias (causa raiz do P6/exp016, reobservado no
smoke de 16/07) — a guarda nunca desliga, então nunca protege nada: RAM
real nunca chega perto de esgotar (é API-only), mas o sinal fica preso em
CRITICAL por comparar contra um piso dimensionado pra outra carga de
trabalho.

Defaults recalibrados para a realidade API-only (0.30GB CRITICAL / 0.60GB
WARNING — ~7%/15% dos 4.1GB totais observados, cobrindo o modelo de
embedding residente + FastAPI + folga): decisão do pesquisador, avaliada
contra a alternativa de threshold percentual do total de RAM (vm.percent)
e descartada — a pegada de RAM deste processo (embedding model +
baseline Python/FastAPI) é aproximadamente CONSTANTE, não escala com o
tamanho da máquina; um piso percentual se tornaria ridiculamente folgado
em máquinas grandes (15% de 64GB = 9.6GB, muito acima do necessário) e
apertado demais em máquinas pequenas — GB absoluto continua sendo o
modelo fisicamente correto aqui, só precisava de outro número.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional

from ..clock import now as _now

logger = logging.getLogger("edp.runtime.pressure")


# ── Thresholds (overridáveis por env) ────────────────────────────────────────
# Dívida #41 (Hardening Fase 2): defaults 0.30/0.60GB — ver docstring do
# módulo para a calibração completa (API-only, ~4.1GB de RAM total medidos).
# Rollback para os valores antigos (cenário de inferência local, 8GB+):
# EDP_PRESSURE_CRITICAL_GB=1.2 EDP_PRESSURE_WARNING_GB=2.0
CRITICAL_GB = float(os.environ.get("EDP_PRESSURE_CRITICAL_GB", "0.30"))
WARNING_GB  = float(os.environ.get("EDP_PRESSURE_WARNING_GB",  "0.60"))
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
            now = _now()
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
