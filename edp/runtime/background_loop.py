"""
edp.runtime.background_loop — Scheduler async de jobs periódicos.

Princípio (Commit 6, Renato 06/06/2026):
  Camadas 2/3 do EDP exigem processamento que NÃO bloqueie o turno
  conversacional. Sumarização autônoma, active recall, consolidação de
  palácios, calibração via Gauss/Bayes — todas dependem de infraestrutura
  de background. Este módulo entrega essa fundação.

Escopo do Commit 6 (mínimo, validado):
  - asyncio.create_task no lifespan FastAPI
  - Tick fixo (default 30s) — jobs declaram interval_sec próprio
  - BackgroundJob como contrato: func + interval + priority + pressure flag
  - 3 níveis de pressure: NORMAL (todos), WARNING (priority 1 only),
    CRITICAL (loop pausado)
  - try/except por job — falha de 1 NÃO derruba loop
  - sem retry automático (próxima tick tenta novamente)
  - sem persistência (jobs registrados no startup)

Escopo NÃO incluído (commits futuros):
  - Persistência de estado de jobs em disco
  - Retry com backoff exponencial
  - Dependências entre jobs (DAG)
  - Cancelamento de job em execução (apenas via timeout)
  - API REST para gerenciar jobs em runtime

Consumidores futuros (destravados por este commit):
  - Commit 3d: decisions cognitive via Haiku em background
  - Commit 8: active recall por cue
  - Memory Palace #36: consolidação periódica
  - Sumarização autônoma de blocos antigos
  - Calibração automática Gauss/Bayes

Decisões arquiteturais (D1-D6, Renato 06/06/2026):
  D1 = β: asyncio.create_task no lifespan (não threading)
  D2 = α: tick fixo 30s + interval por job
  D3 = BackgroundJob dataclass como contrato
  D4 = 3 níveis pressure (NORMAL/WARNING/CRITICAL)
  D5 = α: timeout por job + sem retry automático
  D6 = α: sem persistência (jobs in-memory)

Princípios EDP aplicados:
  1. Base Sólida          → try/except por job, falha não derruba loop
  2. Soberania Progressiva → loop local in-process, sem dependências externas
  3. Arquitetura Forward  → jobs registráveis modularmente
  4. Solidificação        → não toca código validado
  5. Reuso Infraestrutura → pressure_governor + clock + padrão singleton
  6. Retrieval Adaptativo → (futuro) jobs podem reajustar parâmetros

Robustez:
  - Job que dispara exceção: log warning + total_failures+=1 + próxima tick
  - Job que demora demais: asyncio.wait_for com timeout configurável
  - Pressure CRITICAL: loop dorme até voltar a NORMAL/WARNING
  - Shutdown: cancel task + aguarda finalização limpa
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field, asdict
from typing import Awaitable, Callable, List, Optional, Union

from ..clock import now as _now

logger = logging.getLogger("edp.runtime.background_loop")


# ── Constantes ───────────────────────────────────────────────────────────────

DEFAULT_TICK_SEC      = 30.0    # frequência do scheduler (verifica jobs a cada N seg)
DEFAULT_JOB_TIMEOUT   = 20.0    # timeout por job (não pode travar loop)
SHUTDOWN_GRACE_SEC    = 5.0     # tempo de espera para shutdown limpo

# Tipo da função do job: sync OU async (loop chama com await await_if_coro)
JobFunc = Callable[[], Union[None, Awaitable[None]]]


# ── Dataclass do Job ─────────────────────────────────────────────────────────

@dataclass
class BackgroundJob:
    """
    Contrato para job no background loop.

    Atributos obrigatórios:
      name           Identificador único (usado em logs + unregister)
      func           Função (sync ou async) sem args/return. Lança exceção em
                     erro — loop captura e loga.
      interval_sec   Intervalo entre execuções (em segundos). Ex: 60 → 1×/min.
      priority       1 (alta — roda mesmo em WARNING), 2 ou 3 (baixa)

    Atributos opcionais:
      suspend_on_pressure  Se True, job não roda em WARNING/CRITICAL.
                            Default True (a maioria dos jobs deve respeitar).
      timeout_sec    Timeout específico (default DEFAULT_JOB_TIMEOUT).
      enabled        Permite pausar job sem desregistrar.

    Estado interno (gerenciado pelo loop):
      last_run_at    Timestamp da última execução (None = nunca rodou)
      total_runs     Quantas vezes executou com sucesso
      total_failures Quantas vezes falhou (exceção ou timeout)
      last_error     Última mensagem de erro (debugging)
    """
    name:                str
    func:                JobFunc
    interval_sec:        float
    priority:            int  = 2
    suspend_on_pressure: bool = True
    timeout_sec:         float = DEFAULT_JOB_TIMEOUT
    enabled:             bool = True
    # Estado interno (NÃO definido pelo usuário no register):
    last_run_at:         Optional[float] = None
    total_runs:          int = 0
    total_failures:      int = 0
    last_error:          Optional[str] = None

    def to_dict(self) -> dict:
        """Serialização (omitindo func que não é serializável)."""
        d = asdict(self)
        d.pop("func", None)
        return d

    def is_due(self, now_ts: float) -> bool:
        """True se job deve rodar agora (interval expirou)."""
        if not self.enabled:
            return False
        if self.last_run_at is None:
            return True
        return (now_ts - self.last_run_at) >= self.interval_sec


# ── Loop principal ───────────────────────────────────────────────────────────

class BackgroundLoop:
    """
    Scheduler async de jobs periódicos. Roda em asyncio.task criado no
    lifespan do FastAPI. Tick fixo (default 30s) verifica todos os jobs e
    executa os que estão due + atendem condições de pressure/enabled.

    Thread-safe: jobs registry protegido por threading.Lock.

    Lifecycle:
      1. Singleton criado na primeira chamada de get_background_loop()
      2. Jobs registrados via register_job() (qualquer momento)
      3. start() chamado no lifespan startup → cria asyncio.Task
      4. _tick() loop principal — verifica + executa jobs
      5. stop() chamado no shutdown → cancela task + aguarda
    """

    def __init__(self, tick_sec: float = DEFAULT_TICK_SEC) -> None:
        self.tick_sec   = tick_sec
        self._jobs:     dict[str, BackgroundJob] = {}
        self._lock      = threading.Lock()
        self._task:     Optional[asyncio.Task] = None
        self._stop_evt: Optional[asyncio.Event] = None
        self._stats = {
            "ticks_total":      0,
            "ticks_suspended":  0,   # ticks pulados por pressure crítica
            "started_at":       None,
        }
        logger.info("[bgloop] inicializado | tick_sec=%.1f", tick_sec)

    # ── Registry de jobs ────────────────────────────────────────────────

    def register_job(self, job: BackgroundJob) -> None:
        """
        Registra job no loop. Se já existe com mesmo name, sobrescreve
        (idempotente). Pode ser chamado antes ou depois de start().
        """
        with self._lock:
            if job.name in self._jobs:
                logger.warning(
                    "[bgloop] job '%s' já registrado — sobrescrevendo",
                    job.name,
                )
            self._jobs[job.name] = job
        logger.info(
            "[bgloop] registrado: name=%s | interval=%.1fs | priority=%d | suspend_pressure=%s",
            job.name, job.interval_sec, job.priority, job.suspend_on_pressure,
        )

    def unregister_job(self, name: str) -> bool:
        """Remove job. Retorna True se removeu, False se não existia."""
        with self._lock:
            existed = name in self._jobs
            self._jobs.pop(name, None)
        if existed:
            logger.info("[bgloop] desregistrado: name=%s", name)
        return existed

    def list_jobs(self) -> List[dict]:
        """Snapshot dos jobs registrados (para dashboard / debug)."""
        with self._lock:
            return [job.to_dict() for job in self._jobs.values()]

    def stats(self) -> dict:
        """Estatísticas operacionais do loop."""
        with self._lock:
            return {
                "tick_sec":         self.tick_sec,
                "n_jobs":           len(self._jobs),
                "ticks_total":      self._stats["ticks_total"],
                "ticks_suspended":  self._stats["ticks_suspended"],
                "started_at":       self._stats["started_at"],
                "is_running":       self._task is not None and not self._task.done(),
            }

    # ── Lifecycle async ─────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Inicia o loop em uma asyncio.Task. Idempotente — chamadas múltiplas
        ignoradas se já iniciado.
        """
        if self._task is not None and not self._task.done():
            logger.debug("[bgloop] start() chamado mas já está rodando")
            return
        self._stop_evt = asyncio.Event()
        self._stats["started_at"] = _now()
        self._task = asyncio.create_task(self._run(), name="edp_background_loop")
        logger.info("[bgloop] iniciado | %d jobs registrados", len(self._jobs))

    async def stop(self) -> None:
        """
        Para o loop graciosamente. Sinaliza stop, aguarda até
        SHUTDOWN_GRACE_SEC, cancela se não terminar.
        """
        if self._task is None or self._task.done():
            logger.debug("[bgloop] stop() chamado mas loop não está rodando")
            return
        logger.info("[bgloop] parando...")
        if self._stop_evt:
            self._stop_evt.set()
        try:
            await asyncio.wait_for(self._task, timeout=SHUTDOWN_GRACE_SEC)
            logger.info("[bgloop] parado graciosamente")
        except asyncio.TimeoutError:
            logger.warning(
                "[bgloop] não parou em %.1fs, cancelando", SHUTDOWN_GRACE_SEC,
            )
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("[bgloop] cancelado")

    async def _run(self) -> None:
        """Loop principal — tick fixo. Termina quando _stop_evt sinaliza."""
        try:
            while not (self._stop_evt and self._stop_evt.is_set()):
                await self._tick()
                # asyncio.wait_for permite acordar antes do tick se stop sinaliza
                try:
                    if self._stop_evt:
                        await asyncio.wait_for(
                            self._stop_evt.wait(), timeout=self.tick_sec,
                        )
                    else:
                        await asyncio.sleep(self.tick_sec)
                except asyncio.TimeoutError:
                    pass  # tick normal, continua loop
        except asyncio.CancelledError:
            logger.info("[bgloop] _run cancelado externamente")
            raise
        except Exception as e:
            logger.error("[bgloop] _run morreu com exceção: %s", e, exc_info=True)
            raise

    async def _tick(self) -> None:
        """
        Uma iteração do scheduler. Consulta pressure, decide jobs elegíveis,
        executa em ordem de priority.

        Robustez: falha em 1 job NÃO interrompe o tick — próximo job ainda
        é tentado.
        """
        with self._lock:
            self._stats["ticks_total"] += 1

        # Consulta pressure (import lazy para evitar circular)
        try:
            from .pressure_governor import get_governor, PressureLevel
            pressure = get_governor().read().level
        except Exception as e:
            # Commit δ (07/06/2026): DEBUG→INFO. Indica grau de degradação
            # operacional — útil saber sem alarmar (não é erro grave).
            logger.info("[bgloop] pressure_governor indisponível: %s", e)
            pressure = None  # trata como NORMAL

        # CRITICAL → pausa tudo, libera RAM
        if pressure is not None and pressure.value == "critical":
            with self._lock:
                self._stats["ticks_suspended"] += 1
            logger.warning("[bgloop] pressure=CRITICAL, tick pulado completamente")
            return

        # Snapshot dos jobs (ordena por priority asc → 1 antes de 2/3)
        with self._lock:
            jobs_snapshot = sorted(
                self._jobs.values(), key=lambda j: j.priority,
            )

        now_ts = _now()
        for job in jobs_snapshot:
            if not job.is_due(now_ts):
                continue

            # WARNING + suspend_on_pressure → pula este job (mas continua tick)
            if (
                pressure is not None
                and pressure.value == "warning"
                and job.suspend_on_pressure
            ):
                logger.debug(
                    "[bgloop] pressure=WARNING, pulando job '%s' (suspend=True)",
                    job.name,
                )
                continue

            await self._run_job(job)

    async def _run_job(self, job: BackgroundJob) -> None:
        """
        Executa um job com try/except + timeout. Atualiza estado do job.
        """
        started = _now()
        try:
            # Funciona com sync e async
            result = job.func()
            if asyncio.iscoroutine(result):
                await asyncio.wait_for(result, timeout=job.timeout_sec)
            # Sucesso
            job.last_run_at    = started
            job.total_runs    += 1
            job.last_error     = None
            elapsed = _now() - started
            logger.debug(
                "[bgloop] job '%s' OK | elapsed=%.3fs | total_runs=%d",
                job.name, elapsed, job.total_runs,
            )
        except asyncio.TimeoutError:
            job.last_run_at      = started  # marca como rodou (não retry imediato)
            job.total_failures  += 1
            job.last_error       = f"timeout > {job.timeout_sec}s"
            logger.warning(
                "[bgloop] job '%s' TIMEOUT (>%ds) | failures=%d",
                job.name, job.timeout_sec, job.total_failures,
            )
        except Exception as e:
            job.last_run_at      = started
            job.total_failures  += 1
            job.last_error       = str(e)[:200]
            logger.warning(
                "[bgloop] job '%s' falhou: %s | failures=%d",
                job.name, e, job.total_failures,
            )


# ── Singleton (padrão runtime EDP) ───────────────────────────────────────────

_loop: Optional[BackgroundLoop] = None
_loop_lock = threading.Lock()


def get_background_loop() -> BackgroundLoop:
    """Singleton thread-safe."""
    global _loop
    if _loop is None:
        with _loop_lock:
            if _loop is None:
                _loop = BackgroundLoop()
    return _loop
