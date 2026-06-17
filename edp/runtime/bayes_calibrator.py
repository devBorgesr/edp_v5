"""
edp.runtime.bayes_calibrator — Frequência Condicional sobre eventos Pareto.

Princípio (Commit 5α, Renato 06/06/2026):
  Pareto event logger (3b) acumula eventos com correlation_id. Eventos do
  mesmo turno compartilham mesmo UUID, permitindo análise de co-ocorrência.
  Bayes mínimo calcula P(B|A): probabilidade de B ocorrer dado que A ocorreu
  no mesmo turno.

Escopo INTENCIONAL do Bayes mínimo (Commit 5α):
  - Calcula P(B|A) via contagem de correlation_ids compartilhados
  - Reporta n_a, n_a_and_b, n_b_total para diagnóstico
  - Cache TTL idêntico ao Gauss (5min)
  - Lista conditionals conhecidos (KNOWN_CONDITIONALS)

Escopo NÃO incluído (Commits 5β e 5γ em sprints futuras):
  - Decisões automáticas baseadas em probabilidade
  - Priors bayesianos atualizando crenças com novas observações
  - Lift, support, confidence (association rule mining)
  - Modelos generativos / inferência sobre estados ocultos

Aplicações imediatas:
  1. P(task_completed | task_started) — taxa de conclusão de tasks sectioned
  2. P(memory_accessed | memory_added) — memória nova é recuperada?
  3. P(task_started | mode_switched) — troca de modo precede task?

Decisões arquiteturais (D1-D5, Renato 06/06/2026):
  D1 = β: distribuição + frequências (não inclui mining avançado)
  D2 = β: stream com dict de correlation_id (eficiente, simples)
  D3 = γ: None quando A não ocorreu (coerente com Gauss)
  D4 = coerência: 7 dias / 5min (iguais a Gauss/retrieval_monitor)
  D5 = 3 KNOWN_CONDITIONALS iniciais (modular, fácil expansão)

Princípios EDP aplicados:
  1. Base Sólida          → apenas LÊ events.jsonl via Pareto, não modifica
  2. Soberania Progressiva → cálculos locais com stdlib
  3. Arquitetura Forward  → ConditionalProb permite extensão (lift/support)
  4. Solidificação        → não toca código validado (memory, retrieval)
  5. Reuso Infraestrutura → reusa pareto_store.query() + last_query_stats()
  6. Retrieval Adaptativo → (futuro) Bayes pode reforçar boost/penalty

Robustez (lição do Commit 4-fix):
  - Usa pareto_store.last_query_stats() para distinguir "0 eventos" vs erro
  - Eventos sem correlation_id são ignorados (backward-compat)
  - Janela vazia → None (não inferência com 0 dados)
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, asdict
from typing import List, Optional

from ..clock import now as _now
from .pareto_store import get_pareto_store, EVENT_TYPES

logger = logging.getLogger("edp.runtime.bayes_calibrator")


# ── Constantes ───────────────────────────────────────────────────────────────

DEFAULT_WINDOW_DAYS    = 7      # coerente com Gauss e retrieval_monitor
DEFAULT_CACHE_TTL_SEC  = 300    # 5min — coerente com Gauss
MIN_OCCURRENCES_FOR_PROB = 3    # < 3 ocorrências de A → P(B|A) sem confiança

# Pares conhecidos de conditionals (A → B).
# Mapeamento (event_a, event_b) → docstring.
# Combinação fora desta lista ainda pode ser consultada via conditional_probability,
# apenas não aparece em list_known_conditionals.
KNOWN_CONDITIONALS = {
    ("task_started",    "task_completed"):  "taxa de conclusão de tasks sectioned",
    ("memory_added",    "memory_accessed"): "memória nova é recuperada no mesmo turno?",
    ("mode_switched",   "task_started"):    "troca de modo precede início de task?",
}


# ── Dataclass ────────────────────────────────────────────────────────────────

@dataclass
class ConditionalProb:
    """
    Probabilidade condicional P(B|A) calculada por co-ocorrência via
    correlation_id no mesmo turno.

    Atributos:
      event_a       Evento condicionante (ex: "task_started")
      event_b       Evento consequente (ex: "task_completed")
      p_b_given_a   P(B|A) = n_a_and_b / n_a
      n_a           Total de correlation_ids com A
      n_a_and_b     Total de correlation_ids com ambos A e B
      n_b_total     Total de correlation_ids com B (independente de A)
      window_days   Janela rolling usada
      computed_at   Timestamp epoch float
    """
    event_a:       str
    event_b:       str
    p_b_given_a:   float
    n_a:           int
    n_a_and_b:     int
    n_b_total:     int
    window_days:   int
    computed_at:   float

    def to_dict(self) -> dict:
        """Serialização para JSON / dashboard."""
        return asdict(self)

    @property
    def joint_frequency(self) -> int:
        """Co-ocorrências A e B."""
        return self.n_a_and_b


# ── Calibrador principal ─────────────────────────────────────────────────────

class BayesCalibrator:
    """
    Calibrador de frequência condicional sobre eventos do Pareto.

    Lê events.jsonl via pareto_store.query() filtrado por janela temporal.
    Calcula P(B|A) por co-ocorrência de correlation_id.

    Thread-safe: cache protegido por threading.Lock.

    Robustez:
      - Falhas em cálculo → None (não propaga)
      - n_a < MIN_OCCURRENCES_FOR_PROB → None (sem confiança estatística)
      - Pareto offline → None com log warning (via last_query_stats)
    """

    def __init__(
        self,
        window_days:   int = DEFAULT_WINDOW_DAYS,
        cache_ttl_sec: int = DEFAULT_CACHE_TTL_SEC,
    ) -> None:
        self.window_days   = window_days
        self.cache_ttl_sec = cache_ttl_sec
        self._lock = threading.Lock()
        # Cache: (event_a, event_b) → (ConditionalProb, computed_at)
        self._cache: dict = {}
        self._cache_stats = {
            "hits":   0,
            "misses": 0,
            "computed_at": _now(),
        }
        logger.info(
            "[bayes] inicializado | window_days=%d | cache_ttl=%ds | min_occurrences=%d",
            window_days, cache_ttl_sec, MIN_OCCURRENCES_FOR_PROB,
        )

    def conditional_probability(
        self,
        event_a: str,
        event_b: str,
    ) -> Optional[ConditionalProb]:
        """
        Calcula P(event_b | event_a) via co-ocorrência de correlation_id.

        Args:
          event_a: evento condicionante
          event_b: evento consequente

        Retorna:
          ConditionalProb se há dados suficientes (n_a >= MIN_OCCURRENCES),
          None caso contrário (com log diagnóstico preciso).

        Args inválidos (event_type desconhecido) → None com log debug.
        """
        # Validação dos event_types
        if event_a not in EVENT_TYPES:
            logger.debug("[bayes] event_a desconhecido: %s", event_a)
            return None
        if event_b not in EVENT_TYPES:
            logger.debug("[bayes] event_b desconhecido: %s", event_b)
            return None

        # Cache hit?
        cache_key = (event_a, event_b)
        with self._lock:
            if cache_key in self._cache:
                prob, computed_at = self._cache[cache_key]
                age = _now() - computed_at
                if age < self.cache_ttl_sec:
                    self._cache_stats["hits"] += 1
                    return prob
            self._cache_stats["misses"] += 1

        # Cache miss: computa
        prob = self._compute_probability(event_a, event_b)

        # Atualiza cache (apenas se sucesso)
        if prob is not None:
            with self._lock:
                self._cache[cache_key] = (prob, _now())

        return prob

    def _compute_probability(
        self,
        event_a: str,
        event_b: str,
    ) -> Optional[ConditionalProb]:
        """
        Calcula ConditionalProb lendo events.jsonl. Estratégia D2=β:
        stream com dict de correlation_id → set(eventos_observados).

        Robustez (Commit 4-fix #40): consulta last_query_stats para
        distinguir "0 eventos legítimos" de "erro de I/O".
        """
        try:
            since_ts = _now() - (self.window_days * 86400)
            store    = get_pareto_store()

            # correlation_id → set de event types observados nesse turno
            turn_events: dict = defaultdict(set)
            n_events_read   = 0
            n_no_corr_id    = 0

            for evt in store.query(since_ts=since_ts):
                n_events_read += 1
                cid = evt.get("correlation_id")
                etype = evt.get("event")
                # Backward-compat: eventos sem correlation_id são ignorados
                # (apenas no Bayes — Pareto e Gauss continuam vendo eles).
                if not cid:
                    n_no_corr_id += 1
                    continue
                if etype:
                    turn_events[cid].add(etype)

            # Consulta tracking do Pareto APÓS iteração (Dívida #40 resolvida)
            qstats = store.last_query_stats()

            # Conta:
            #   n_a       = correlation_ids contendo event_a
            #   n_a_and_b = correlation_ids contendo AMBOS event_a e event_b
            #   n_b_total = correlation_ids contendo event_b (independente)
            n_a       = sum(1 for events in turn_events.values() if event_a in events)
            n_a_and_b = sum(
                1 for events in turn_events.values()
                if event_a in events and event_b in events
            )
            n_b_total = sum(1 for events in turn_events.values() if event_b in events)

            # Diagnóstico preciso quando n_a insuficiente
            if n_a < MIN_OCCURRENCES_FOR_PROB:
                if qstats.get("had_exception"):
                    logger.warning(
                        "[bayes] P(%s|%s): leitura FALHOU com exceção (%s) — "
                        "tratando como sem dados.",
                        event_b, event_a, qstats.get("exception_msg"),
                    )
                elif not qstats.get("file_existed"):
                    logger.debug(
                        "[bayes] P(%s|%s): events.jsonl não existe ainda.",
                        event_b, event_a,
                    )
                else:
                    logger.debug(
                        "[bayes] P(%s|%s): n_a=%d < %d ocorrências mínimas | "
                        "events_read=%d | events_sem_corr_id=%d | turnos=%d",
                        event_b, event_a, n_a, MIN_OCCURRENCES_FOR_PROB,
                        n_events_read, n_no_corr_id, len(turn_events),
                    )
                return None

            p_b_given_a = n_a_and_b / n_a if n_a > 0 else 0.0

            prob = ConditionalProb(
                event_a       = event_a,
                event_b       = event_b,
                p_b_given_a   = round(p_b_given_a, 4),
                n_a           = n_a,
                n_a_and_b     = n_a_and_b,
                n_b_total     = n_b_total,
                window_days   = self.window_days,
                computed_at   = _now(),
            )
            logger.debug(
                "[bayes] computado P(%s|%s)=%.4f | n_a=%d n_a_and_b=%d n_b_total=%d",
                event_b, event_a, p_b_given_a, n_a, n_a_and_b, n_b_total,
            )
            return prob
        except Exception as e:
            logger.warning(
                "[bayes] _compute_probability falhou P(%s|%s): %s",
                event_b, event_a, e,
            )
            return None

    def list_known_conditionals(self) -> List[dict]:
        """
        Lista todas as combinações KNOWN_CONDITIONALS e indica quais têm
        dados suficientes para calcular P(B|A) agora.
        """
        out = []
        for (ea, eb), desc in KNOWN_CONDITIONALS.items():
            prob = self.conditional_probability(ea, eb)
            out.append({
                "event_a":      ea,
                "event_b":      eb,
                "description":  desc,
                "available":    prob is not None,
                "n_a":          prob.n_a if prob else 0,
                "p_b_given_a":  prob.p_b_given_a if prob else None,
            })
        return out

    def stats(self) -> dict:
        """Estatísticas operacionais do calibrador (cache, etc)."""
        with self._lock:
            cache_size   = len(self._cache)
            cache_hits   = self._cache_stats["hits"]
            cache_misses = self._cache_stats["misses"]
        total = cache_hits + cache_misses
        hit_rate = round(cache_hits / total, 3) if total > 0 else 0.0
        return {
            "window_days":          self.window_days,
            "cache_ttl_sec":        self.cache_ttl_sec,
            "cache_size":           cache_size,
            "cache_hits":           cache_hits,
            "cache_misses":         cache_misses,
            "cache_hit_rate":       hit_rate,
            "known_conditionals":   len(KNOWN_CONDITIONALS),
            "min_occurrences":      MIN_OCCURRENCES_FOR_PROB,
        }

    def invalidate_cache(self) -> None:
        """Força recálculo na próxima chamada. Útil em tests."""
        with self._lock:
            self._cache.clear()
            logger.debug("[bayes] cache invalidado")


# ── Singleton (padrão runtime EDP) ───────────────────────────────────────────

_calibrator: Optional[BayesCalibrator] = None
_calibrator_lock = threading.Lock()


def get_bayes_calibrator() -> BayesCalibrator:
    """Singleton thread-safe."""
    global _calibrator
    if _calibrator is None:
        with _calibrator_lock:
            if _calibrator is None:
                _calibrator = BayesCalibrator()
    return _calibrator
