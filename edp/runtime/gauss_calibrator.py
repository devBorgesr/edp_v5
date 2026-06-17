"""
edp.runtime.gauss_calibrator — Calibração estatística sobre eventos Pareto.

Princípio:
  Pareto event logger (Commit 3b) acumula eventos contínuos. Sem estatísticas
  sobre esses dados, calibradores futuros (Bayes, Memory Palace, threshold
  dinâmico) trabalham cego. Gauss calcula distribuições (média, desvio padrão,
  percentis) sobre métricas numéricas dos eventos para alimentar decisões
  adaptativas.

Funções principais:
  1. stats_for_metric(metric, event_type) → GaussStats
     Ex: stats_for_metric("top_score", "memory_accessed") retorna distribuição
     de top_score em todos os eventos memory_accessed da janela.

  2. is_outlier(value, metric, event_type, z_threshold) → bool
     Detecta se valor é outlier (|z-score| > threshold). Usado para flags de
     comportamento anômalo.

Aplicações futuras (Commits 5+):
  - Bayes: usar média/σ como prior de probabilidades condicionais
  - Memory Palace: detectar palácios anômalos (tamanho fora da gaussiana)
  - Active Recall: threshold dinâmico de "memória relevante" baseado em p50/p90
  - Calibração de SESSION_BOOST_FACTOR: validar empiricamente se 1.60 é certo

Decisões arquiteturais (D1-D5, Renato 05/06/2026):
  D1 = β: distribuição + outliers (sem sugestões de calibração — isso é Bayes)
  D2 = métricas numéricas relevantes: top_score, len_text, duration_sec...
  D3 = γ: rolling configurável, default 7d (igual retrieval_monitor)
  D4 = γ: cache com TTL 5min (equilibra performance × frescor)
  D5 = β: dataclass GaussStats (tipagem robusta, padrão runtime EDP)

Princípios EDP aplicados:
  1. Base Sólida          → apenas LÊ events.jsonl, não modifica nada
  2. Soberania Progressiva → cálculos locais com numpy + stdlib, sem cloud
  3. Arquitetura Forward  → GaussStats permite extensão sem quebrar consumidores
  4. Solidificação        → não toca retrieval/memory, peças validadas
  5. Reuso Infraestrutura → pareto_store.query(), dataclass, singleton, logger
  6. Retrieval Adaptativo → (futuro) Gauss validará empiricamente as escolhas
                              de calibração atuais do Commit 3c.β-γ

Robustez:
  - Falhas em cálculo retornam None (não propagam exceção)
  - Janela vazia ou < 3 amostras → None (não calcula com poucos dados)
  - Eventos corrompidos puláveis via try/except interno
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, asdict
from typing import Any, Iterator, List, Optional

from ..clock import now as _now
from .pareto_store import get_pareto_store, EVENT_TYPES

logger = logging.getLogger("edp.runtime.gauss_calibrator")


# ── Constantes ───────────────────────────────────────────────────────────────

DEFAULT_WINDOW_DAYS    = 7      # janela rolling padrão (igual retrieval_monitor)
DEFAULT_CACHE_TTL_SEC  = 300    # 5min — equilibra performance × frescor
MIN_SAMPLES_FOR_STATS  = 3      # < 3 não dá distribuição confiável
DEFAULT_Z_THRESHOLD    = 2.0    # |z-score| > 2.0 = outlier (95% confiança)

# Métricas numéricas conhecidas por tipo de evento.
# Mapeamento (event_type, metric_name) → docstring.
# Documenta quais métricas têm sentido extrair para Gauss. Tentar uma
# combinação não mapeada retorna None com log debug.
KNOWN_METRICS = {
    ("memory_added",    "len_text"):     "tamanho de memórias adicionadas",
    ("memory_accessed", "top_score"):    "score do melhor match no retrieval",
    ("memory_accessed", "n_returned"):   "quantidade de memórias recuperadas",
    ("task_started",    "expected_total"): "número de seções esperadas",
    ("task_completed",  "n_secoes"):     "número de seções entregues",
    ("task_completed",  "duration_sec"): "duração da task sectioned",
}


# ── Dataclass (D5=β: tipagem robusta) ────────────────────────────────────────

@dataclass
class GaussStats:
    """
    Estatísticas de um campo numérico de eventos Pareto.

    Atributos:
      metric_name    Nome da métrica (ex: "top_score")
      event_type     Tipo de evento de origem (ex: "memory_accessed")
      n_samples      Quantidade de amostras na janela
      mean           Média aritmética
      stddev         Desvio padrão (amostral, n-1)
      min            Menor valor
      max            Maior valor
      p50            Mediana
      p90            Percentil 90
      p99            Percentil 99
      window_days    Janela usada (rolling)
      computed_at    Timestamp de quando foi computado (epoch float)
    """
    metric_name:  str
    event_type:   str
    n_samples:    int
    mean:         float
    stddev:       float
    min:          float
    max:          float
    p50:          float
    p90:          float
    p99:          float
    window_days:  int
    computed_at:  float

    def to_dict(self) -> dict:
        """Serialização para JSON/dashboard."""
        return asdict(self)

    def z_score(self, value: float) -> float:
        """Calcula z-score para um valor. Retorna 0.0 se stddev é 0."""
        if self.stddev <= 0:
            return 0.0
        return (value - self.mean) / self.stddev


# ── Calibrador principal ─────────────────────────────────────────────────────

class GaussCalibrator:
    """
    Calibrador estatístico sobre eventos do Pareto.

    Lê events.jsonl via pareto_store.query() filtrado por janela temporal.
    Calcula distribuições por (event_type, metric). Cache com TTL.

    Thread-safe: cache protegido por threading.Lock.

    Robustez (Base Sólida):
      - Falhas em cálculo retornam None (não propagam)
      - Janela vazia → None silencioso
      - Pareto store offline → None com log warning
    """

    def __init__(
        self,
        window_days:    int = DEFAULT_WINDOW_DAYS,
        cache_ttl_sec:  int = DEFAULT_CACHE_TTL_SEC,
    ) -> None:
        self.window_days   = window_days
        self.cache_ttl_sec = cache_ttl_sec
        self._lock = threading.Lock()
        # Cache: (event_type, metric) → (GaussStats, computed_at)
        self._cache: dict = {}
        self._cache_stats = {
            "hits":   0,
            "misses": 0,
            "computed_at": _now(),
        }
        logger.info(
            "[gauss] inicializado | window_days=%d | cache_ttl=%ds",
            window_days, cache_ttl_sec,
        )

    def stats_for_metric(
        self,
        metric:     str,
        event_type: str,
    ) -> Optional[GaussStats]:
        """
        Retorna estatísticas de um campo numérico de eventos de um tipo.

        Args:
          metric:     nome do campo numérico (ex: "top_score")
          event_type: tipo de evento de origem (ex: "memory_accessed")

        Retorna:
          GaussStats com média/σ/percentis, OU None se:
            - Combinação (event_type, metric) não mapeada em KNOWN_METRICS
            - Janela sem amostras suficientes (< MIN_SAMPLES_FOR_STATS)
            - Erro de I/O lendo events.jsonl
        """
        # Validação do par (event_type, metric)
        if event_type not in EVENT_TYPES:
            logger.debug("[gauss] event_type desconhecido: %s", event_type)
            return None
        if (event_type, metric) not in KNOWN_METRICS:
            logger.debug(
                "[gauss] métrica não mapeada: (%s, %s)", event_type, metric,
            )
            return None

        # Cache hit?
        cache_key = (event_type, metric)
        with self._lock:
            if cache_key in self._cache:
                stats, computed_at = self._cache[cache_key]
                age = _now() - computed_at
                if age < self.cache_ttl_sec:
                    self._cache_stats["hits"] += 1
                    return stats
            self._cache_stats["misses"] += 1

        # Cache miss: computa
        stats = self._compute_stats(metric, event_type)

        # Atualiza cache
        if stats is not None:
            with self._lock:
                self._cache[cache_key] = (stats, _now())

        return stats

    def _compute_stats(
        self,
        metric:     str,
        event_type: str,
    ) -> Optional[GaussStats]:
        """
        Calcula GaussStats lendo events.jsonl. Não-cacheado (chamado por
        stats_for_metric quando cache miss/expirado).

        Commit 4-fix (Renato, 06/06/2026 — Dívida #40): consulta
        last_query_stats() do Pareto após iteração para distinguir
        "0 amostras legítimas" de "erro de I/O silencioso" e logar com
        precisão.
        """
        try:
            since_ts = _now() - (self.window_days * 86400)
            store    = get_pareto_store()
            values:  List[float] = []
            for evt in store.query(event_type=event_type, since_ts=since_ts):
                v = evt.get(metric)
                if v is None:
                    continue
                try:
                    fv = float(v)
                    if math.isnan(fv) or math.isinf(fv):
                        continue
                    values.append(fv)
                except (ValueError, TypeError):
                    continue

            # Commit 4-fix: consulta tracking do Pareto APÓS iteração
            qstats = store.last_query_stats()

            if len(values) < MIN_SAMPLES_FOR_STATS:
                # Diagnóstico preciso baseado em qstats (Dívida #40)
                if qstats.get("had_exception"):
                    logger.warning(
                        "[gauss] (%s, %s): leitura FALHOU com exceção (%s) — "
                        "tratando como 0 amostras. Recomendado retry posterior.",
                        event_type, metric, qstats.get("exception_msg"),
                    )
                elif not qstats.get("file_existed"):
                    logger.debug(
                        "[gauss] (%s, %s): events.jsonl não existe ainda — "
                        "0 amostras esperadas.",
                        event_type, metric,
                    )
                elif qstats.get("lines_corrupted", 0) > 0:
                    logger.warning(
                        "[gauss] (%s, %s): amostras insuficientes (%d < %d) | "
                        "lines_read=%d lines_corrupted=%d events_yielded=%d",
                        event_type, metric, len(values), MIN_SAMPLES_FOR_STATS,
                        qstats.get("lines_read", 0),
                        qstats.get("lines_corrupted", 0),
                        qstats.get("events_yielded", 0),
                    )
                else:
                    logger.debug(
                        "[gauss] (%s, %s): amostras insuficientes (%d < %d) | "
                        "yielded=%d (filtro por tipo/tempo)",
                        event_type, metric, len(values), MIN_SAMPLES_FOR_STATS,
                        qstats.get("events_yielded", 0),
                    )
                return None

            values.sort()
            n      = len(values)
            mean   = sum(values) / n
            # Desvio padrão amostral (n-1)
            variance = sum((v - mean) ** 2 for v in values) / max(n - 1, 1)
            stddev   = math.sqrt(variance)

            stats = GaussStats(
                metric_name = metric,
                event_type  = event_type,
                n_samples   = n,
                mean        = round(mean, 4),
                stddev      = round(stddev, 4),
                min         = values[0],
                max         = values[-1],
                p50         = _percentile(values, 50),
                p90         = _percentile(values, 90),
                p99         = _percentile(values, 99),
                window_days = self.window_days,
                computed_at = _now(),
            )
            logger.debug(
                "[gauss] computado (%s, %s): n=%d mean=%.4f σ=%.4f",
                event_type, metric, n, mean, stddev,
            )
            return stats
        except Exception as e:
            logger.warning(
                "[gauss] _compute_stats falhou (%s, %s): %s",
                event_type, metric, e,
            )
            return None

    def is_outlier(
        self,
        value:       float,
        metric:      str,
        event_type:  str,
        z_threshold: float = DEFAULT_Z_THRESHOLD,
    ) -> bool:
        """
        Verifica se value é outlier estatístico (|z-score| > threshold).

        Args:
          value:       valor a verificar
          metric:      campo da métrica
          event_type:  tipo de evento
          z_threshold: |z| acima disso = outlier (default 2.0 = 95% intervalo)

        Retorna:
          True  → outlier (valor anômalo)
          False → normal OU stats indisponível (não dá flag positivo sem dados)
        """
        try:
            stats = self.stats_for_metric(metric, event_type)
            if stats is None:
                return False
            z = stats.z_score(float(value))
            return abs(z) > z_threshold
        except Exception as e:
            logger.debug("[gauss] is_outlier falhou: %s", e)
            return False

    def list_available_metrics(self) -> List[dict]:
        """
        Lista todas as combinações (event_type, metric) conhecidas e indica
        quais têm dados suficientes para gerar GaussStats agora.

        Útil para dashboard / debug.
        """
        out = []
        for (etype, metric), desc in KNOWN_METRICS.items():
            stats = self.stats_for_metric(metric, etype)
            out.append({
                "event_type":  etype,
                "metric":      metric,
                "description": desc,
                "available":   stats is not None,
                "n_samples":   stats.n_samples if stats else 0,
            })
        return out

    def stats(self) -> dict:
        """Estatísticas operacionais do calibrador (cache, etc)."""
        with self._lock:
            cache_size = len(self._cache)
            cache_hits = self._cache_stats["hits"]
            cache_misses = self._cache_stats["misses"]
        total = cache_hits + cache_misses
        hit_rate = round(cache_hits / total, 3) if total > 0 else 0.0
        return {
            "window_days":   self.window_days,
            "cache_ttl_sec": self.cache_ttl_sec,
            "cache_size":    cache_size,
            "cache_hits":    cache_hits,
            "cache_misses":  cache_misses,
            "cache_hit_rate": hit_rate,
            "known_metrics_count": len(KNOWN_METRICS),
        }

    def invalidate_cache(self) -> None:
        """Força recálculo na próxima chamada. Útil em tests."""
        with self._lock:
            self._cache.clear()
            logger.debug("[gauss] cache invalidado")


# ── Helper interno ───────────────────────────────────────────────────────────

def _percentile(sorted_values: List[float], p: float) -> float:
    """
    Calcula percentil p (0-100) de lista JÁ ORDENADA.

    Implementação simples por interpolação linear. Para casos críticos de
    performance/precisão, considerar numpy.percentile no futuro.

    Args:
      sorted_values: lista ordenada ascendente, não-vazia
      p:             percentil (0-100)

    Retorna: valor do percentil, ou 0.0 se lista vazia (não deveria ocorrer).
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    # Posição (fracional) no array
    k = (p / 100.0) * (len(sorted_values) - 1)
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return round(sorted_values[f], 4)
    # Interpolação linear
    d = k - f
    return round(sorted_values[f] * (1 - d) + sorted_values[c] * d, 4)


# ── Singleton (padrão runtime EDP) ───────────────────────────────────────────

_calibrator: Optional[GaussCalibrator] = None
_calibrator_lock = threading.Lock()


def get_gauss_calibrator() -> GaussCalibrator:
    """Singleton thread-safe. Padrão usado em todos os módulos runtime."""
    global _calibrator
    if _calibrator is None:
        with _calibrator_lock:
            if _calibrator is None:
                _calibrator = GaussCalibrator()
    return _calibrator
