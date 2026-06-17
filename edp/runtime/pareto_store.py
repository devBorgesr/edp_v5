"""
edp.runtime.pareto_store — Event store para análise estatística e calibradores.

Princípio:
  EDP gera eventos contínuos (memórias adicionadas, retrievals, mode switches,
  task lifecycle). Calibradores futuros (Gauss, Bayes, Memory Palace) precisam
  de dados históricos para aprender padrões. Sem telemetria estruturada,
  calibradores trabalham cego.

Estratégia LEVE (não overengineering):
  - JSONL append-only em disco ($EDP_BASE_DIR/pareto/events.jsonl)
  - Rotação automática quando arquivo > 10MB
  - Singleton thread-safe (double-checked locking)
  - Schema plano (D1=β, decisão Renato 04/06/2026)
  - Hooks explícitos (D2=α, decisão Renato 04/06/2026)
  - Falhas em emit NÃO propagam (Base Sólida: telemetria não quebra fluxo)

Arquitetura Forward:
  Interface ParetoEventStore (Protocol) permite trocar FileParetoStore
  por RemoteParetoStore (Servidor B com Postgres) no futuro sem mudar
  chamadores. Soberania Progressiva.

Custo: ~100-500 bytes por evento × ~50 eventos/dia uso intenso ≈ ~25KB/dia.
       Rotação após 10MB ≈ ~400 dias por arquivo. Negligível.

Eventos rastreados (todos com schema plano D1=β):
  - memory_added       (de EpisodicMemory.add)
  - memory_accessed    (de EpisodicMemory.retrieve)
  - task_started       (sectioned ativa N seções esperadas)
  - task_completed     (task_anchor fecha N/N entregue)
  - mode_switched      (de set_operational_mode)

Schema (plano, D1=β):
  {"event": "<tipo>", "ts": <epoch>, "session_id": "<id>",
   "correlation_id": "<turn_uuid>", <campos específicos>}

Campos específicos por evento:
  memory_added:    source_type, scope, len_text, [topic_tag]
  memory_accessed: n_returned, top_score, scope
  task_started:    expected_total
  task_completed:  n_secoes, duration_sec
  mode_switched:   from_mode, to_mode

Correlation ID (thread-local):
  - Gerado no início de stream_chat/chat via new_correlation_id()
  - Registrado via set_current_correlation_id()
  - Hooks recuperam via get_current_correlation_id() para correlacionar
    múltiplos eventos do mesmo turno (preparação para Bayes condicional).

Princípios EDP aplicados (Renato, 04/06/2026):
  1. Base Sólida          → try/except em todo emit, nunca propaga
  2. Soberania Progressiva → interface Protocol prepara remoto
  3. Arquitetura Forward  → schema com correlation_id e topic_tag
                             para Gauss/Bayes/Memory Palace
  4. Solidificação        → hooks não tocam fluxo crítico
  5. Reuso Infraestrutura → padrão singleton, threading.Lock, EDP_BASE_DIR,
                             logger Python — todos reusados de outros módulos
                             runtime (retrieval_monitor, contradiction_flagger)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Iterator, Optional, Protocol

from ..clock import now as _now

logger = logging.getLogger("edp.runtime.pareto_store")

# ── Constantes ───────────────────────────────────────────────────────────────

MAX_FILE_SIZE_MB    = 10
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

EVENT_TYPES = frozenset({
    "memory_added",
    "memory_accessed",
    "task_started",
    "task_completed",
    "mode_switched",
    # Fase 1 — Câmara Adaptativa (12/06/2026)
    "camara_outcome",
})


# ── Correlation ID por turno (thread-local) ──────────────────────────────────

_thread_local = threading.local()


def set_current_correlation_id(correlation_id: str) -> None:
    """Define correlation_id do turno atual (chamado no início de chat/stream_chat)."""
    _thread_local.correlation_id = correlation_id


def get_current_correlation_id() -> Optional[str]:
    """Retorna correlation_id do turno atual, ou None se não definido."""
    return getattr(_thread_local, "correlation_id", None)


def new_correlation_id() -> str:
    """Gera novo correlation_id formato 'turn_<12hex>'."""
    return f"turn_{uuid.uuid4().hex[:12]}"


def clear_current_correlation_id() -> None:
    """Limpa correlation_id da thread atual (chamado ao fim do turno)."""
    if hasattr(_thread_local, "correlation_id"):
        del _thread_local.correlation_id


# ── Interface (Arquitetura Forward) ──────────────────────────────────────────

class ParetoEventStore(Protocol):
    """Interface para event stores. Implementações: File (local), Remote (futuro)."""

    def emit(self, event: dict) -> None: ...

    def query(
        self,
        event_type: Optional[str] = None,
        since_ts:   Optional[float] = None,
    ) -> Iterator[dict]: ...

    def stats(self) -> dict: ...


# ── Implementação local (JSONL append-only) ──────────────────────────────────

class FileParetoStore:
    """
    Implementação local: JSONL append-only com rotação automática.

    Storage: $EDP_BASE_DIR/pareto/events.jsonl
    Rotação: quando arquivo > 10MB, renomeia para events.{ts}.jsonl
             e cria novo events.jsonl.

    Thread-safety: threading.Lock em todas as operações de I/O.

    Robustez (Base Sólida):
      - emit() nunca propaga exceção — telemetria não quebra fluxo crítico
      - linhas corrompidas em query() são puladas com log debug
      - rotação falhada é logada mas não bloqueia emit
    """

    def __init__(self, persist_dir: Optional[Path] = None) -> None:
        if persist_dir is None:
            base = os.environ.get("EDP_BASE_DIR", "data")
            persist_dir = Path(base) / "pareto"
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.persist_file = self.persist_dir / "events.jsonl"
        self._lock = threading.Lock()
        self._stats = {
            "events_emitted": 0,
            "events_failed":  0,
            "rotations":      0,
            "started_at":     _now(),
        }
        # Commit 4-fix (Renato, 06/06/2026 — Dívida #40):
        # Tracking de erros em query() para que Gauss possa distinguir
        # "0 eventos legítimos" de "erro de leitura silencioso".
        # Antes: query() pegava exceção, logava warning, retornava iterator
        # vazio. Caller não conseguia distinguir "arquivo vazio" de "I/O error".
        # Agora: _last_query_stats expõe tudo via last_query_stats() público.
        self._last_query_stats: dict = {
            "called_at":         None,
            "lines_read":        0,
            "lines_corrupted":   0,
            "events_yielded":    0,
            "had_exception":     False,
            "exception_msg":     None,
            "file_existed":      False,
        }
        logger.info(
            "[pareto] inicializado | persist=%s | rotation_mb=%d",
            self.persist_file, MAX_FILE_SIZE_MB,
        )

    def emit(self, event: dict) -> None:
        """
        Adiciona evento ao log. Falhas são logadas mas não propagadas.

        Validação:
          - event deve ser dict
          - event['event'] deve estar em EVENT_TYPES

        Preenchimento automático:
          - ts ← _now() se ausente
          - correlation_id ← thread-local se ausente
        """
        try:
            if not isinstance(event, dict):
                logger.warning("[pareto] emit ignorado: evento não-dict")
                self._stats["events_failed"] += 1
                return
            evt_type = event.get("event")
            if not evt_type or evt_type not in EVENT_TYPES:
                logger.warning(
                    "[pareto] emit ignorado: tipo desconhecido '%s'", evt_type,
                )
                self._stats["events_failed"] += 1
                return
            # Preenchimento automático
            if "ts" not in event:
                event["ts"] = _now()
            if "correlation_id" not in event:
                cid = get_current_correlation_id()
                if cid:
                    event["correlation_id"] = cid
            # Append thread-safe
            line = json.dumps(event, ensure_ascii=False) + "\n"
            with self._lock:
                # Rotação se arquivo passa do limite
                if self.persist_file.exists():
                    try:
                        size = self.persist_file.stat().st_size
                        if size > MAX_FILE_SIZE_BYTES:
                            self._rotate_locked()
                    except Exception as e:
                        logger.debug("[pareto] stat falhou: %s", e)
                with open(self.persist_file, "a", encoding="utf-8") as f:
                    f.write(line)
                self._stats["events_emitted"] += 1
        except Exception as e:
            logger.warning("[pareto] emit falhou: %s", e)
            self._stats["events_failed"] = self._stats.get("events_failed", 0) + 1

    def _rotate_locked(self) -> None:
        """Renomeia arquivo atual com timestamp. Chamado COM lock segurado."""
        try:
            timestamp = int(_now())
            rotated = self.persist_dir / f"events.{timestamp}.jsonl"
            self.persist_file.rename(rotated)
            self._stats["rotations"] += 1
            logger.info("[pareto] rotação | arquivo antigo=%s", rotated.name)
        except Exception as e:
            logger.warning("[pareto] rotação falhou: %s", e)

    def query(
        self,
        event_type: Optional[str] = None,
        since_ts:   Optional[float] = None,
    ) -> Iterator[dict]:
        """
        Itera eventos do arquivo atual. Filtra opcionalmente por tipo e
        timestamp mínimo. Lê APENAS events.jsonl (atual); arquivos rotacionados
        ficam disponíveis via query_rotated() em iteração futura.

        Para Commit 4 (Gauss): bastará rolling recente do events.jsonl.

        Robustez: linhas corrompidas são puladas com log debug. Continua
        iterando mesmo após erro em uma linha.

        Commit 4-fix (Renato, 06/06/2026 — Dívida #40): popula
        _last_query_stats para que callers possam distinguir "0 eventos
        legítimos" de "erro de leitura silencioso" via last_query_stats().
        Crucial para Gauss não confundir falha de I/O com ausência de dados.
        """
        # Reseta tracking no início de cada query
        # NÃO usa _lock — query é generator, lock travaria iterator inteiro.
        # Race condition aqui só sobrescreve stats da query anterior, não
        # corrompe dados. Aceita.
        self._last_query_stats = {
            "called_at":         _now(),
            "lines_read":        0,
            "lines_corrupted":   0,
            "events_yielded":    0,
            "had_exception":     False,
            "exception_msg":     None,
            "file_existed":      self.persist_file.exists(),
        }
        if not self.persist_file.exists():
            return
        try:
            with open(self.persist_file, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    self._last_query_stats["lines_read"] += 1
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except Exception:
                        self._last_query_stats["lines_corrupted"] += 1
                        logger.debug(
                            "[pareto] linha %d corrompida, pulando", line_num,
                        )
                        continue
                    if event_type and evt.get("event") != event_type:
                        continue
                    if since_ts is not None and evt.get("ts", 0) < since_ts:
                        continue
                    self._last_query_stats["events_yielded"] += 1
                    yield evt
        except Exception as e:
            self._last_query_stats["had_exception"] = True
            self._last_query_stats["exception_msg"] = str(e)[:200]
            logger.warning("[pareto] query falhou: %s", e)
            return

    def last_query_stats(self) -> dict:
        """
        Retorna estatísticas da ÚLTIMA chamada a query().

        Commit 4-fix (Dívida #40): observabilidade explícita de erros de
        leitura. Callers (ex: Gauss) podem distinguir:
          - lines_read=0, file_existed=False     → arquivo nunca existiu
          - lines_read=0, file_existed=True      → arquivo vazio
          - had_exception=True                   → erro de I/O (file lock?)
          - events_yielded < lines_read - corrupted → filtros eliminaram

        Retorna cópia para evitar mutação externa.
        """
        return dict(self._last_query_stats)

    def stats(self) -> dict:
        """Retorna estatísticas operacionais. Não-bloqueante."""
        try:
            file_size = (
                self.persist_file.stat().st_size
                if self.persist_file.exists() else 0
            )
        except Exception:
            file_size = -1
        return {
            **self._stats,
            "file_size_bytes": file_size,
            "file_size_mb": (
                round(file_size / (1024 * 1024), 2) if file_size > 0 else 0.0
            ),
            "persist_file":  str(self.persist_file),
            "max_size_mb":   MAX_FILE_SIZE_MB,
        }


# ── Singleton (mesmo padrão de retrieval_monitor, contradiction_flagger) ─────

_store: Optional[FileParetoStore] = None
_store_lock = threading.Lock()


def get_pareto_store() -> FileParetoStore:
    """Singleton thread-safe (double-checked locking). Padrão runtime do EDP."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = FileParetoStore()
    return _store


# ── Helpers de emit (conveniência para hooks D2=α) ───────────────────────────
#
# Todos os helpers seguem o mesmo padrão:
#   - try/except envolvendo tudo
#   - log debug em caso de falha (não warning — falha em helper é silenciosa)
#   - retorna None sempre
#
# Hooks chamam estes helpers diretamente (não emit() bruto) para garantir
# schema correto e sempre incluir ts/correlation_id automaticamente.


def emit_memory_added(
    session_id:  str,
    source_type: str,
    scope:       str,
    len_text:    int,
    topic_tag:   Optional[str] = None,
) -> None:
    """Hook: nova memória episódica gravada."""
    try:
        evt = {
            "event":       "memory_added",
            "ts":          _now(),
            "session_id":  session_id,
            "source_type": source_type,
            "scope":       scope,
            "len_text":    int(len_text),
        }
        if topic_tag:
            evt["topic_tag"] = topic_tag
        get_pareto_store().emit(evt)
    except Exception as e:
        # Commit δ (07/06/2026): elevado DEBUG→WARNING.
        # Telemetria falhando silenciosamente quebra Pareto+Gauss+Bayes.
        # Extensão da Dívida #40 — query() foi corrigido, emits faltavam.
        logger.warning(
            "[pareto] emit_memory_added falhou: %s: %s",
            type(e).__name__, str(e)[:150],
        )


def emit_memory_accessed(
    session_id: str,
    n_returned: int,
    top_score:  float,
    scope:      str,
) -> None:
    """Hook: memórias resgatadas via retrieval semântico."""
    try:
        evt = {
            "event":      "memory_accessed",
            "ts":         _now(),
            "session_id": session_id,
            "n_returned": int(n_returned),
            "top_score":  round(float(top_score), 4),
            "scope":      scope,
        }
        get_pareto_store().emit(evt)
    except Exception as e:
        # Commit δ (07/06/2026): elevado DEBUG→WARNING.
        # Gauss top_score depende deste evento.
        logger.warning(
            "[pareto] emit_memory_accessed falhou: %s: %s",
            type(e).__name__, str(e)[:150],
        )


def emit_mode_switched(
    session_id: str,
    from_mode:  str,
    to_mode:    str,
) -> None:
    """Hook: troca de modo operacional (cognitive ↔ sprint)."""
    try:
        evt = {
            "event":      "mode_switched",
            "ts":         _now(),
            "session_id": session_id,
            "from_mode":  from_mode,
            "to_mode":    to_mode,
        }
        get_pareto_store().emit(evt)
    except Exception as e:
        # Commit δ (07/06/2026): elevado DEBUG→WARNING.
        logger.warning(
            "[pareto] emit_mode_switched falhou: %s: %s",
            type(e).__name__, str(e)[:150],
        )


def emit_task_started(
    session_id:     str,
    expected_total: int,
) -> None:
    """Hook: sectioned task iniciada com N seções esperadas."""
    try:
        evt = {
            "event":          "task_started",
            "ts":             _now(),
            "session_id":     session_id,
            "expected_total": int(expected_total),
        }
        get_pareto_store().emit(evt)
    except Exception as e:
        # Commit δ (07/06/2026): elevado DEBUG→WARNING.
        logger.warning(
            "[pareto] emit_task_started falhou: %s: %s",
            type(e).__name__, str(e)[:150],
        )


def emit_task_completed(
    session_id:   str,
    n_secoes:     int,
    duration_sec: float,
) -> None:
    """Hook: sectioned task fechada com N/N seções entregues."""
    try:
        evt = {
            "event":        "task_completed",
            "ts":           _now(),
            "session_id":   session_id,
            "n_secoes":     int(n_secoes),
            "duration_sec": round(float(duration_sec), 2),
        }
        get_pareto_store().emit(evt)
    except Exception as e:
        # Commit δ (07/06/2026): elevado DEBUG→WARNING.
        logger.warning(
            "[pareto] emit_task_completed falhou: %s: %s",
            type(e).__name__, str(e)[:150],
        )


def emit_camara_outcome(
    session_id: str,
    camara_id: str,
    modelo_A: str,
    modelo_B: str,
    vencedor: str,
    concordancia: int,
    custo_total_usd: float,
    latencia_total_ms: float,
    tier_gap=None,
    fallback: bool = False,
    flag_condescendencia: bool = False,
    auto_sinal_confianca=None,
) -> None:
    """
    Hook: resultado de uma execução da câmara de eco.

    Fase 1 da Câmara Adaptativa (12/06/2026): cada disparo vira evento
    Pareto — a base do histórico calibrado: P(b_venceu | modelo_B),
    curva de custo médio, taxa de condescendência, tempo até
    significância. count(camara_outcome) = taxa de disparo (todo
    disparo termina em sucesso ou fallback gracioso — ambos emitem).
    """
    try:
        evt = {
            "event":        "camara_outcome",
            "ts":           _now(),
            "session_id":   session_id,
            "camara_id":    camara_id,
            "modelo_A":     modelo_A,
            "modelo_B":     modelo_B,
            "vencedor":     vencedor,
            "concordancia": int(concordancia),
            "custo_usd":    round(float(custo_total_usd), 6),
            "latencia_ms":  round(float(latencia_total_ms), 1),
            "tier_gap":     tier_gap,
            "fallback":     bool(fallback),
            "flag_condescendencia": bool(flag_condescendencia),
            "auto_sinal_confianca": auto_sinal_confianca,
        }
        get_pareto_store().emit(evt)
    except Exception as e:
        logger.warning("[pareto] emit_camara_outcome falhou: %s", e)
