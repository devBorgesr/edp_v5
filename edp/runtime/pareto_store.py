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

import hashlib
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
    # Dívida #53 (docs/preregistro_fix_corrupcao_json.md, 04/08/2026):
    # sinal de observabilidade quando um store degrada para vazio por
    # corrupção irrecuperável (edp/memory/atomic_io.py::
    # _load_json_or_quarantine). Reaproveita este event store em vez de
    # subsistema novo — CognitiveHealthIndex foi avaliado primeiro e não
    # serve (calculador de score, não event log genérico).
    "store_degraded",
    # Fase 1 da calibração de tokens (12/08/2026, lab_edp_novo/docs/sujeito_edp/AUDITORIA_FASE1_TOKENS.md):
    # par (chars enviados, tokens REAIS cobrados) por chamada de LLM. Mesmo
    # motivo do store_degraded acima — reusa este event log em vez de criar
    # canal paralelo, e assim o dado cai onde bayes/gauss já olham.
    "token_usage",
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


# ── Regime de formato por turno (thread-local, 12/08/2026) ───────────────────
# Mesmo mecanismo do correlation_id acima, e de propósito: o provider (que tem
# o token real) não conhece modo, flags nem caps; quem conhece é o adapter. Um
# segundo mecanismo de propagação daria duas verdades sobre "em que regime este
# turno rodou". Este reusa a thread — o provider roda na mesma onde o adapter
# tira o snapshot.


def set_current_format_state(estado: Optional[dict]) -> None:
    """Registra o regime de formato do turno atual (chamado no início do turno)."""
    _thread_local.format_state = estado


def get_current_format_state() -> Optional[dict]:
    """Regime de formato do turno atual, ou None se não registrado."""
    return getattr(_thread_local, "format_state", None)


def clear_current_format_state() -> None:
    if hasattr(_thread_local, "format_state"):
        del _thread_local.format_state


def hash_format_state(estado: Optional[dict]) -> Optional[str]:
    """
    Identidade determinística do regime — sha256 truncado do JSON canônico.

    O hash não carrega informação nova (é derivável dos campos), e é isso que
    torna ele barato. O que ele muda é a natureza da garantia: sem hash, a
    Fase 2 confia que as configurações eram iguais; com hash, ela PROVA qual
    regime produziu cada amostra e agrupa por igualdade de string em vez de
    comparar dicts aninhados. É a diferença entre "mecanismo real" e "só
    confiança" que `docs/AVISO_INSTANCIA_LIMPA.md` distingue.

    `sort_keys=True` não é cosmético: sem ele, dois dicts com o mesmo conteúdo
    e ordem de inserção diferente produziriam hashes diferentes e a Fase 2 veria
    dois regimes onde só há um.
    """
    if not isinstance(estado, dict):
        return None
    try:
        canonico = json.dumps(estado, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":"))
        return hashlib.sha256(canonico.encode("utf-8")).hexdigest()[:16]
    except Exception as e:
        logger.debug("[pareto] hash_format_state falhou: %s", e)
        return None


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


def emit_store_degraded(
    store_label:     str,
    path:            str,
    quarantine_path: Optional[str],
    error_type:      str,
) -> None:
    """
    Hook: um store degradou para vazio por corrupção irrecuperável.

    Dívida #53 (docs/preregistro_fix_corrupcao_json.md, 04/08/2026):
    sinal de observabilidade emitido por
    edp/memory/atomic_io.py::_load_json_or_quarantine, verificável por
    asserção de teste via
    get_pareto_store().query(event_type="store_degraded") — não depende
    de inspeção visual de log (o log continua existindo em paralelo, via
    logger.critical no chamador, como segundo canal).
    """
    try:
        evt = {
            "event":           "store_degraded",
            "ts":               _now(),
            "store_label":      store_label,
            "path":             path,
            "quarantine_path":  quarantine_path,
            "error_type":       error_type,
        }
        get_pareto_store().emit(evt)
    except Exception as e:
        logger.warning("[pareto] emit_store_degraded falhou: %s", e)


# ── Fase 1 da calibração de tokens (12/08/2026) ──────────────────────────────
# lab_edp_novo/docs/sujeito_edp/AUDITORIA_FASE1_TOKENS.md. Coleta o par (chars enviados, tokens REAIS
# cobrados) para substituir o `4 chars ≈ 1 token` de
# runtime/context_window_manager.py:12-13, que nunca foi medido.

_CERCA = "```"


def classificar_conteudo(texto: str) -> dict:
    """
    Rotula o conteúdo de um prompt em UMA passada, sem LLM e sem rede.

    Existe porque tokenização é dependente de conteúdo, não uniforme: a mesma
    frase em PT e EN gasta número diferente de tokens (o BPE tem mais peças
    grandes para inglês), e código tokeniza de um terceiro jeito. Uma razão
    global chars/token seria a média de regimes distintos e não serviria para
    nenhum deles.

    DEVOLVE OS SINAIS CRUS, não só o rótulo — de propósito. Os limiares abaixo
    são escolha minha, sem medição por trás (mesmo defeito que
    lab_edp_novo/docs/sujeito_edp/AUDITORIA_CONSTANTES_NAO_CALIBRADAS.md cataloga em ~90 constantes). A
    diferença é que aqui isso é inofensivo: nenhum deles entra em ranking, eles
    só PARTICIONAM o dataset, e como os sinais crus vão gravados junto, a Fase 2
    pode re-particionar com outros limiares sem recoletar nada. O que seria
    irreversível é não gravar os sinais — não o valor do limiar.

    Classes: "codigo" | "acentuado" (prosa PT-BR) | "ascii" (prosa sem acento,
    tipicamente EN ou PT sem acentuação).
    """
    if not texto:
        return {"classe": "vazio",
                "sinais": {"n": 0, "nao_ascii": 0.0, "cercas": 0, "simbolos": 0.0}}

    n = len(texto)
    n_nao_ascii = 0
    n_simbolos = 0
    for ch in texto:
        if ch > "\x7f":
            n_nao_ascii += 1
        elif ch in "{}[]()<>=;/\\|_*#$&^~`":
            n_simbolos += 1

    cercas = texto.count(_CERCA)
    r_nao_ascii = n_nao_ascii / n
    r_simbolos = n_simbolos / n

    if cercas >= 2 or r_simbolos > 0.15:
        classe = "codigo"
    elif r_nao_ascii > 0.01:
        classe = "acentuado"
    else:
        classe = "ascii"

    return {
        "classe": classe,
        "sinais": {
            "n":          n,
            "nao_ascii":  round(r_nao_ascii, 4),
            "cercas":     cercas,
            "simbolos":   round(r_simbolos, 4),
        },
    }


def amostra_valida_fase2(evt: dict) -> bool:
    """
    A população experimental da Fase 2, como PREDICADO e não como convenção.

    Existe porque a regra em prosa ("filtre por format_state não-nulo") tem um
    modo de falha conhecido e barato: alguém escreve `carrega_tudo()`, esquece
    o filtro, e a contaminação volta pela porta que o `format_state` foi criado
    para fechar. O harness da Fase 2 importa isto em vez de re-derivar.

    Três classes de chamada produzem eventos, e só a primeira é observação:

      A. turno principal          -> format_state preenchido    -> ENTRA
      B. câmara, cognitive_decisions -> format_state None        -> fica fora
      C. validate()               -> não emite (telemetria=False) -> nem existe

    (B) são chamadas legítimas ao mesmo provider, com token real — mas de
    composição própria (system de refutação, prompt de extração congelado).
    Não são inválidas; são outra população. Descartá-las na emissão perderia
    dado que pode servir a outra pergunta; misturá-las aqui responderia a
    pergunta errada.
    """
    if not isinstance(evt, dict) or evt.get("event") != "token_usage":
        return False
    if evt.get("format_state") is None:
        return False
    if evt.get("provider") != "anthropic":
        return False
    usage = evt.get("usage")
    if not isinstance(usage, dict):
        return False
    return (usage.get("input_tokens") is not None
            and usage.get("output_tokens") is not None)


def emit_token_usage(
    model:         str,
    modo:          str,
    usage:         Optional[dict],
    text_chars:    int,
    system_chars:  int,
    payload_bytes: int,
    n_messages:    int,
    amostra_texto: str = "",
) -> None:
    """
    Hook: par (chars enviados, tokens REAIS) de UMA chamada de LLM.

    Governado por `EDP_TOKEN_TELEMETRY` (default OFF). Com a flag OFF esta
    função retorna antes de qualquer trabalho — nada é computado, nada é
    escrito, nenhum evento existe.

    DUAS medidas de "chars", de propósito. A pergunta "qual o numerador da
    razão chars/token" tem duas respostas defensáveis e escolher uma às cegas
    produziria um número com aparência de medido:
      - `text_chars`   — system + conteúdo das mensagens. É o que a API
                         tokeniza e cobra.
      - `payload_bytes`— bytes reais no fio (`len(req.data)`), incluindo o
                         andaime JSON, que a API NÃO cobra mas cujo tamanho
                         escala com `n_messages`.
    Gravando as duas + `n_messages`, a Fase 2 calcula a razão das duas formas e
    escolhe a mais estável com dado, em vez de por decreto. Custo: dois inteiros.

    `usage` vai VERBATIM, não em campos extraídos. Motivo concreto: se prompt
    caching for ligado um dia, `usage` ganha `cache_read_input_tokens` /
    `cache_creation_input_tokens`, e uma chamada cacheada tem relação
    chars→tokens-cobrados completamente diferente de uma sem cache. Se este
    evento gravasse só `input_tokens`, toda amostra posterior a esse dia ficaria
    contaminada e INDISTINGUÍVEL das limpas — o dataset inteiro viraria suspeito
    retroativamente. Gravando o dict inteiro, cacheadas e limpas ficam
    separáveis para sempre.

    AMOSTRA DESCARTADA quando `input_tokens` ou `output_tokens` estiver ausente
    (acontece no streaming: chegam em eventos SSE distintos — `message_start` e
    `message_delta` — e qualquer um pode faltar). Gravar ausência como 0
    injetaria par falso no dataset; a amostra a menos é o custo certo.

    NÃO carrega `session_id`: o provider não o conhece, e inventar um seria
    pior que omitir. O `correlation_id` é preenchido automaticamente por
    `emit()` a partir do thread-local (setado em `llm_adapter.py:1527` e
    `:1607`, mesma thread da chamada), e por ele a Fase 2 junta com
    `memory_added`/`memory_accessed` do mesmo turno, que têm `session_id`.
    """
    try:
        from ..config import EDP_TOKEN_TELEMETRY
        if not EDP_TOKEN_TELEMETRY:
            return

        if not isinstance(usage, dict):
            logger.debug("[pareto] token_usage descartado: usage ausente")
            return
        if usage.get("input_tokens") is None or usage.get("output_tokens") is None:
            logger.debug(
                "[pareto] token_usage descartado: tokens incompletos "
                "(in=%s out=%s)",
                usage.get("input_tokens"), usage.get("output_tokens"),
            )
            return

        try:
            from ..clock import is_verified as _is_verified
            clock_ok = bool(_is_verified())
        except Exception:
            clock_ok = False

        cls = classificar_conteudo(amostra_texto)

        # Regime de formato que produziu ESTA amostra. Sem ele, uma mudança de
        # modo ou de flag no meio da coleta mistura regimes de forma
        # indetectável; com ele, vira estrato separável pelo hash.
        formato = get_current_format_state()

        evt = {
            "event":          "token_usage",
            "ts":             _now(),
            "clock_verified": clock_ok,
            "format_state":   formato,
            "format_hash":    hash_format_state(formato),
            # Hoje só o provider Anthropic emite, então "anthropic" é
            # redundante — e é exatamente por isso que vai gravado. A regra de
            # população da Fase 2 nomeia o provider; deixá-lo implícito
            # significa que, no dia em que o Ollama for instrumentado, as
            # amostras antigas viram ambíguas retroativamente e não há como
            # desambiguar depois.
            "provider":       "anthropic",
            "model":          model,
            "modo":           modo,
            "usage":          dict(usage),
            "text_chars":     int(text_chars),
            "system_chars":   int(system_chars),
            "payload_bytes":  int(payload_bytes),
            "n_messages":     int(n_messages),
            "classe":         cls["classe"],
            "sinais":         cls["sinais"],
        }
        get_pareto_store().emit(evt)
    except Exception as e:
        logger.warning("[pareto] emit_token_usage falhou: %s", e)
