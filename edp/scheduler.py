"""
scheduler.py — Auto-consolidação e manutenção periódica do EDP v3 (PATCHED)

PATCHES APLICADOS:
  [P-C3-9]  _job_graph_link: from .memory_graph import build_from_entries →
            from .belief_graph import build_from_entries
            (memory_graph.py não existe; função está em belief_graph.py)
  [P-C3-10] _job_consolidate: from .consolidation import consolidate →
            usa SemanticMemory.consolidate_from_episodes() inline
            (consolidation.py ausente; lógica equivalente já existe em semantic_memory.py)
  [P-C3-11] Todos os jobs usam get_pipeline_memory() em vez de instanciar
            MemoryStore() por job — evita leitura stale de disco por job
  [P-C3-12] _job_graph_link: passa session_id para build_from_entries
            (antes usava session_id implícito "_standalone")
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from .config import (
    CONSOLIDATION_SIM_THRESH,
    EPISODIC_MEM_SIZE,
    MEMORY_DIR,
)

logger = logging.getLogger("edp.scheduler")

DEFAULT_INTERVAL_SECS = 300
DEFAULT_PRUNE_KEEP    = int(EPISODIC_MEM_SIZE * 0.8)
DEFAULT_CONSOL_THRESH = CONSOLIDATION_SIM_THRESH
SCHEDULER_LOG         = MEMORY_DIR / "scheduler.jsonl"
JOB_TIMEOUT_SECS      = 60

# Lock por session_id para serializar acesso ao disco
_session_locks:      Dict[str, threading.Lock] = {}
_session_locks_lock = threading.Lock()


def _get_session_lock(session_id: str) -> threading.Lock:
    with _session_locks_lock:
        if session_id not in _session_locks:
            _session_locks[session_id] = threading.Lock()
        return _session_locks[session_id]


class AutoScheduler:
    """
    Scheduler de manutenção cognitiva.
    [P-C3-11] Jobs usam get_pipeline_memory() — sem instâncias stale por job.
    """

    def __init__(
        self,
        session_id:    str,
        interval_secs: int  = DEFAULT_INTERVAL_SECS,
        auto_start:    bool = False,
    ) -> None:
        self.session_id   = session_id
        self.interval     = interval_secs
        self._timer:      Optional[threading.Timer] = None
        self._running     = False
        self._jobs:       Dict[str, dict] = {}
        self._last_run:   Dict[str, float] = {}
        self._run_counts: Dict[str, int]   = {}
        self._errors:     list[dict]       = []

        self._register_defaults()
        if auto_start:
            self.start()

    def _register_defaults(self) -> None:
        self.register("consolidate", self._job_consolidate, interval_secs=DEFAULT_INTERVAL_SECS)
        self.register("prune",       self._job_prune,       interval_secs=DEFAULT_INTERVAL_SECS * 2)
        self.register("replay",      self._job_replay,      interval_secs=DEFAULT_INTERVAL_SECS * 3)
        self.register("graph_link",  self._job_graph_link,  interval_secs=DEFAULT_INTERVAL_SECS)

    def register(
        self,
        name:          str,
        fn:            Callable,
        interval_secs: int  = DEFAULT_INTERVAL_SECS,
        enabled:       bool = True,
    ) -> None:
        self._jobs[name]       = {"fn": fn, "interval": interval_secs, "enabled": enabled}
        self._last_run[name]   = 0.0
        self._run_counts[name] = 0

    def enable(self, name: str)  -> None: self._jobs[name]["enabled"] = True
    def disable(self, name: str) -> None: self._jobs[name]["enabled"] = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._schedule_next()
        logger.info("[Scheduler] Iniciado sessão '%s' (interval=%ds)", self.session_id, self.interval)

    def stop(self) -> None:
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None
        logger.info("[Scheduler] Parado.")

    def _schedule_next(self) -> None:
        if not self._running:
            return
        self._timer = threading.Timer(self.interval, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self) -> None:
        now = time.time()
        for name, job in self._jobs.items():
            if not job["enabled"]:
                continue
            if now - self._last_run.get(name, 0.0) >= job["interval"]:
                self._run_job(name, job["fn"])
        self._schedule_next()

    def _run_job(self, name: str, fn: Callable) -> None:
        result_holder: list = []
        error_holder:  list = []

        def _target():
            t0 = time.time()
            try:
                res = fn()
                result_holder.append((res, time.time() - t0))
            except Exception as e:
                error_holder.append((str(e), time.time() - t0))

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout=JOB_TIMEOUT_SECS)

        if t.is_alive():
            err = {"job": name, "error": f"timeout after {JOB_TIMEOUT_SECS}s", "ts": time.time()}
            self._errors.append(err)
            self._log_run(name, "timeout", JOB_TIMEOUT_SECS, {"error": err["error"]})
            logger.warning("[Scheduler] Job '%s' timeout após %ds", name, JOB_TIMEOUT_SECS)
            return

        if error_holder:
            err_msg, elapsed = error_holder[0]
            self._errors.append({"job": name, "error": err_msg, "ts": time.time()})
            self._log_run(name, "error", elapsed, {"error": err_msg})
            logger.warning("[Scheduler] Job '%s' falhou: %s", name, err_msg)
        elif result_holder:
            res, elapsed = result_holder[0]
            self._last_run[name]    = time.time()
            self._run_counts[name] += 1
            self._log_run(name, "ok", elapsed, res or {})

    # ── Jobs ──────────────────────────────────────────────────────────────

    def _job_consolidate(self) -> dict:
        """
        [P-C3-10] FIX: from .consolidation import consolidate → AUSENTE
        Usa semantic_memory.consolidate_from_episodes() inline.
        consolidation.py não existe nos arquivos do projeto.
        """
        from .pipeline import get_pipeline_memory          # [P-C3-11] singleton
        from .semantic_memory import SemanticMemory

        lock = _get_session_lock(self.session_id)
        with lock:
            mem        = get_pipeline_memory(self.session_id)
            sem_mem    = SemanticMemory(self.session_id)
            episodes   = [
                {"id": e["id"], "text": e["text"], "embedding": e["embedding"]}
                for e in mem.episodic.entries
                if e.get("embedding") is not None
            ]
            affected   = sem_mem.consolidate_from_episodes(episodes, threshold=DEFAULT_CONSOL_THRESH)
        return {"consolidated": len(affected)}

    def _job_prune(self) -> dict:
        """[P-C3-11] Usa get_pipeline_memory() — sem instância stale."""
        from .pipeline import get_pipeline_memory
        lock = _get_session_lock(self.session_id)
        with lock:
            mem    = get_pipeline_memory(self.session_id)
            result = mem.prune(keep=DEFAULT_PRUNE_KEEP)
        return result

    def _job_replay(self) -> dict:
        """[P-C3-11] Usa get_pipeline_memory() — sem instância stale."""
        from .pipeline import get_pipeline_memory
        lock = _get_session_lock(self.session_id)
        with lock:
            mem      = get_pipeline_memory(self.session_id)
            replayed = replay_memories(mem, top_k=5)
        return {"replayed": replayed}

    def _job_graph_link(self) -> dict:
        """
        [P-C3-9]  FIX: from .memory_graph import build_from_entries → AUSENTE
                  Corrigido para: from .belief_graph import build_from_entries
        [P-C3-11] Usa get_pipeline_memory() — sem instância stale
        [P-C3-12] Passa session_id para build_from_entries
        """
        from .pipeline import get_pipeline_memory
        from .belief_graph import build_from_entries  # [P-C3-9] import correto

        lock = _get_session_lock(self.session_id)
        with lock:
            mem   = get_pipeline_memory(self.session_id)
            # [P-C3-12] session_id passado explicitamente
            n_new = build_from_entries(
                mem.episodic.entries,
                session_id=self.session_id,
            )
        return {"new_edges": n_new}

    def run_now(self, job_name: str) -> dict:
        if job_name not in self._jobs:
            raise KeyError(f"Job '{job_name}' não registrado")
        result: dict = {}
        t0 = time.time()
        try:
            result = self._jobs[job_name]["fn"]() or {}
            self._last_run[job_name]    = time.time()
            self._run_counts[job_name] += 1
        except Exception as e:
            result = {"error": str(e)}
        result["latency_ms"] = round((time.time() - t0) * 1000, 2)
        return result

    def run_all(self) -> dict:
        results = {}
        for name, job in self._jobs.items():
            if job["enabled"]:
                results[name] = self.run_now(name)
        return results

    def _log_run(self, name: str, status: str, elapsed: float, result: dict) -> None:
        try:
            SCHEDULER_LOG.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "job":        name,
                "status":     status,
                "elapsed_ms": round(elapsed * 1000, 2),
                "result":     result,
                "ts":         time.time(),
            }
            with open(SCHEDULER_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def stats(self) -> dict:
        return {
            "session_id": self.session_id,
            "running":    self._running,
            "interval_s": self.interval,
            "jobs": {
                name: {
                    "enabled":    job["enabled"],
                    "interval_s": job["interval"],
                    "run_count":  self._run_counts.get(name, 0),
                    "last_run":   self._last_run.get(name, 0.0),
                }
                for name, job in self._jobs.items()
            },
            "errors": self._errors[-10:],
        }


# ── Helpers standalone ────────────────────────────────────────────────────────

def auto_consolidate(session_id: str, threshold: Optional[float] = None) -> dict:
    """
    [P-C3-10] Usa SemanticMemory.consolidate_from_episodes() em vez de
    from .consolidation import consolidate (módulo ausente).
    """
    from .pipeline import get_pipeline_memory
    from .semantic_memory import SemanticMemory

    with _get_session_lock(session_id):
        mem      = get_pipeline_memory(session_id)
        sem_mem  = SemanticMemory(session_id)
        episodes = [
            {"id": e["id"], "text": e["text"], "embedding": e["embedding"]}
            for e in mem.episodic.entries
            if e.get("embedding") is not None
        ]
        affected = sem_mem.consolidate_from_episodes(
            episodes, threshold=threshold or CONSOLIDATION_SIM_THRESH
        )
    return {"consolidated": len(affected)}


def replay_memories(
    memory,
    top_k: int   = 5,
    boost: float = 0.15,
) -> int:
    """
    Memory Replay: reforça entradas com maior score_inicial
    que não foram acessadas recentemente (> 1 dia).
    """
    from .temporal import age_days

    entries = memory.episodic.entries
    if not entries:
        return 0

    now        = time.time()
    candidates = [
        e for e in entries
        if age_days(e.get("ultimo_acesso", 0)) > 1.0
    ]
    candidates.sort(key=lambda e: e.get("score_inicial", 0), reverse=True)

    replayed = 0
    for entry in candidates[:top_k]:
        entry["acessos"]       = entry.get("acessos", 0) + 1
        entry["ultimo_acesso"] = now
        entry["score_inicial"] = min(
            round(entry.get("score_inicial", 0.5) + boost, 4), 1.0
        )
        replayed += 1

    if replayed:
        memory.episodic.save()

    return replayed
