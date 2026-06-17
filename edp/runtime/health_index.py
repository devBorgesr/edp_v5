"""
edp.runtime.health_index — Cognitive Health Index (CHI).

Commit C (Sessão 3, 09/06/2026):
  Combina 4 métricas que JÁ existem no EDP em score único de saúde
  cognitiva. NÃO calcula scoring novo — reusa Gauss + Bayes +
  CoOccurrence + cognitive_decisions persistidas.

Princípio aplicado:
  Reuso de Infraestrutura. Antes de construir saúde do zero,
  inspecionar o que já é calculado. Os 4 calibradores já fornecem
  sinais — CHI agrega.

Fórmula:
  CHI = retrieval_precision  × 0.30   (Gauss top_score médio)
      + memory_utility       × 0.30   (Bayes P(accessed|added))
      + semantic_richness    × 0.20   (CoOccurrence pairs vs entries×5)
      + domain_coverage      × 0.20   (unique domains / TARGET_DOMAINS)

Decisão arquitetural:
  Biodiversity NÃO usada — depende de ImmutableCognitiveNode do v3.2
  desconectado. domain_coverage via cognitive_decisions é semântico,
  validado, viável solo.

Classify:
  n_samples < 20      → INSUFFICIENT_DATA
  n_samples < 50      → cap em DEVELOPING (confiança parcial)
  n_samples ≥ 50:
    score ≥ 0.80      → MATURE
    score ≥ 0.60      → DEVELOPING
    score ≥ 0.40      → GROWING
    else              → NASCENT

Persistência: $EDP_BASE_DIR/sessions/<session>_cognitive/health_history.jsonl
Rotação: últimas MAX_HEALTH_ENTRIES (default 5000, ~4 meses uso normal).

Feature flag: EDP_HEALTH_INDEX=true|false (default true).

Envs configuráveis:
  CHI_TARGET_DOMAINS         (default 10) — alvo de diversidade temática
  CHI_PAIRS_PER_ENTRY        (default 5)  — denominador semantic_richness
  CHI_MATURE_THRESHOLD       (default 0.80)
  CHI_DEVELOPING_THRESHOLD   (default 0.60)
  CHI_GROWING_THRESHOLD      (default 0.40)
  CHI_MIN_SAMPLES_GAUSS      (default 20)
  CHI_MIN_SAMPLES_MATURE     (default 50)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

from ..clock import now as _now


logger = logging.getLogger("edp.runtime.health_index")


# ── Constantes (defaults configuráveis via env) ──────────────────────────────

DEFAULT_TARGET_DOMAINS       = 10
DEFAULT_PAIRS_PER_ENTRY      = 5
DEFAULT_MATURE_THRESHOLD     = 0.80
DEFAULT_DEVELOPING_THRESHOLD = 0.60
DEFAULT_GROWING_THRESHOLD    = 0.40
DEFAULT_MIN_SAMPLES_GAUSS    = 20
DEFAULT_MIN_SAMPLES_MATURE   = 50
MAX_HEALTH_ENTRIES           = 5000

WEIGHTS = {
    "retrieval_precision": 0.30,
    "memory_utility":      0.30,
    "semantic_richness":   0.20,
    "domain_coverage":     0.20,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 0.001, "Pesos devem somar 1.0"

HIERARCHY = ["NASCENT", "GROWING", "DEVELOPING", "MATURE"]


# ── HealthResult ──────────────────────────────────────────────────────────────

@dataclass
class HealthResult:
    """
    Snapshot de saúde cognitiva em momento dado.

    score:        composto ponderado em [0.0, 1.0], ou None se INSUFFICIENT_DATA
    level:        NASCENT/GROWING/DEVELOPING/MATURE/INSUFFICIENT_DATA
    components:   dict {nome → valor [0..1]}
    samples_used: n_samples do Gauss (fonte mais robusta)
    computed_at:  epoch float
    weights:      pesos usados (auditoria)
    """
    score:        Optional[float]
    level:        str
    components:   dict
    samples_used: int
    computed_at:  float
    weights:      dict = field(default_factory=lambda: dict(WEIGHTS))

    def to_dict(self) -> dict:
        return asdict(self)

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


# ── CognitiveHealthIndex ──────────────────────────────────────────────────────

class CognitiveHealthIndex:
    """
    Combina sinais existentes em índice único.

    Uso típico (background job):
        chi = get_health_index()
        result = chi.compute(session_id="default")
        if result.score is not None:
            chi.persist(result)
    """

    def __init__(self) -> None:
        # Lazy imports — evitam circular se modulo importado cedo
        from .gauss_calibrator import get_gauss_calibrator
        from .bayes_calibrator import get_bayes_calibrator

        self.gauss = get_gauss_calibrator()
        self.bayes = get_bayes_calibrator()

        # Validação explícita (não-assert — sempre executa)
        if self.gauss is None:
            raise RuntimeError("CHI: GaussCalibrator singleton retornou None")
        if self.bayes is None:
            raise RuntimeError("CHI: BayesCalibrator singleton retornou None")

        # Thresholds configuráveis via env
        self._target_domains    = self._env_int("CHI_TARGET_DOMAINS",       DEFAULT_TARGET_DOMAINS)
        self._pairs_per_entry   = self._env_int("CHI_PAIRS_PER_ENTRY",      DEFAULT_PAIRS_PER_ENTRY)
        self._mature_thr        = self._env_float("CHI_MATURE_THRESHOLD",     DEFAULT_MATURE_THRESHOLD)
        self._developing_thr    = self._env_float("CHI_DEVELOPING_THRESHOLD", DEFAULT_DEVELOPING_THRESHOLD)
        self._growing_thr       = self._env_float("CHI_GROWING_THRESHOLD",    DEFAULT_GROWING_THRESHOLD)
        self._min_samples_gauss = self._env_int("CHI_MIN_SAMPLES_GAUSS",    DEFAULT_MIN_SAMPLES_GAUSS)
        self._min_samples_mature = self._env_int("CHI_MIN_SAMPLES_MATURE",   DEFAULT_MIN_SAMPLES_MATURE)

        # Lock para escrita atômica em health_history.jsonl
        self._write_lock = threading.Lock()

        # Cooldown enforcement (last compute timestamp por session)
        self._last_compute: dict[str, float] = {}
        self._compute_lock = threading.Lock()

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except ValueError:
            logger.warning(
                "[chi] env %s inválido, usando default %d", name, default,
            )
            return default

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, str(default)))
        except ValueError:
            logger.warning(
                "[chi] env %s inválido, usando default %f", name, default,
            )
            return default

    # ── Cálculo principal ──────────────────────────────────────────────────

    def compute(self, session_id: str = "default") -> HealthResult:
        """
        Calcula CHI. Sempre retorna HealthResult (mesmo INSUFFICIENT_DATA).

        Args:
            session_id: sessão para localizar episodic.json e
                        co_occurrence.json (scope cognitive).

        Returns:
            HealthResult com score=None se Gauss < min_samples.
        """
        # 1. retrieval_precision (Gauss top_score)
        gauss_stats = self.gauss.stats_for_metric("top_score", "memory_accessed")
        n_samples = gauss_stats.n_samples if gauss_stats else 0

        if n_samples < self._min_samples_gauss:
            logger.info(
                "[chi] insufficient_data | n_samples=%d < %d",
                n_samples, self._min_samples_gauss,
            )
            return HealthResult(
                score=None,
                level="INSUFFICIENT_DATA",
                components={"reason": f"need {self._min_samples_gauss} gauss samples, have {n_samples}"},
                samples_used=n_samples,
                computed_at=_now(),
            )

        retrieval_precision = max(0.0, min(1.0, gauss_stats.mean))

        # 2. memory_utility (Bayes P(accessed|added))
        memory_utility = self._compute_memory_utility()

        # 3. semantic_richness (CoOccurrence pairs vs entries × N)
        semantic_richness, entries_count, pairs_count = self._compute_semantic_richness(session_id)

        # 4. domain_coverage (cognitive_decisions unique domains)
        domain_coverage, unique_domains_count = self._compute_domain_coverage(session_id)

        # Score composto
        score = (
            retrieval_precision * WEIGHTS["retrieval_precision"]
            + memory_utility     * WEIGHTS["memory_utility"]
            + semantic_richness  * WEIGHTS["semantic_richness"]
            + domain_coverage    * WEIGHTS["domain_coverage"]
        )
        score = round(max(0.0, min(1.0, score)), 3)

        level = self._classify(score, n_samples)

        return HealthResult(
            score=score,
            level=level,
            components={
                "retrieval_precision": round(retrieval_precision, 3),
                "memory_utility":      round(memory_utility, 3),
                "semantic_richness":   round(semantic_richness, 3),
                "domain_coverage":     round(domain_coverage, 3),
                # Auditoria — números brutos
                "_pairs_count":          pairs_count,
                "_entries_count":        entries_count,
                "_unique_domains_count": unique_domains_count,
                "_target_domains":       self._target_domains,
                "_pairs_per_entry":      self._pairs_per_entry,
            },
            samples_used=n_samples,
            computed_at=_now(),
        )

    def _compute_memory_utility(self) -> float:
        """Bayes P(memory_accessed | memory_added). Default 0.5 (neutro) se sem dado."""
        try:
            prob = self.bayes.conditional_probability("memory_added", "memory_accessed")
            if prob is None or prob.p_b_given_a is None:
                return 0.5
            return max(0.0, min(1.0, prob.p_b_given_a))
        except Exception as e:
            logger.warning("[chi] bayes falhou: %s", e)
            return 0.5

    def _compute_semantic_richness(self, session_id: str) -> tuple[float, int, int]:
        """
        Lê co_occurrence.json + episodic.json (scope cognitive).
        Retorna (richness, entries_count, pairs_count).
        """
        base = self._get_base_dir()
        co_path      = base / "sessions" / f"{session_id}_cognitive" / "co_occurrence.json"
        epi_path     = base / "sessions" / f"{session_id}_cognitive" / "episodic.json"

        entries_count = 0
        pairs_count   = 0

        # entries count (denominador)
        try:
            if epi_path.exists():
                with open(epi_path, "r", encoding="utf-8") as f:
                    entries = json.load(f)
                entries_count = len(entries) if isinstance(entries, list) else 0
        except Exception as e:
            logger.warning("[chi] episodic.json leitura falhou: %s", e)

        # pairs count (numerador)
        try:
            if co_path.exists():
                with open(co_path, "r", encoding="utf-8") as f:
                    pairs_data = json.load(f)
                if isinstance(pairs_data, dict):
                    # Conta entradas únicas (divide por 2 pela simetria)
                    total = sum(len(v) for v in pairs_data.values() if isinstance(v, dict))
                    pairs_count = total // 2
        except Exception as e:
            logger.warning("[chi] co_occurrence.json leitura falhou: %s", e)

        expected_pairs = max(50, entries_count * self._pairs_per_entry)
        richness = min(pairs_count / expected_pairs, 1.0) if expected_pairs > 0 else 0.0
        return (richness, entries_count, pairs_count)

    def _compute_domain_coverage(self, session_id: str) -> tuple[float, int]:
        """
        Conta unique domains em cognitive_decisions persistidas.
        Retorna (coverage, count).
        """
        base = self._get_base_dir()
        epi_path = base / "sessions" / f"{session_id}_cognitive" / "episodic.json"

        domains: set[str] = set()
        try:
            if epi_path.exists():
                with open(epi_path, "r", encoding="utf-8") as f:
                    entries = json.load(f)
                if isinstance(entries, list):
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        cd = entry.get("cognitive_decisions")
                        if isinstance(cd, dict):
                            d = cd.get("domain")
                            if d and isinstance(d, str):
                                domains.add(d.strip().lower())
        except Exception as e:
            logger.warning("[chi] domain leitura falhou: %s", e)

        coverage = min(len(domains) / self._target_domains, 1.0)
        return (coverage, len(domains))

    def _classify(self, score: float, n_samples: int) -> str:
        """
        Score + n_samples → level.

        Regra 1: n_samples < MIN_SAMPLES_GAUSS → INSUFFICIENT_DATA (já tratado em compute)
        Regra 2: n_samples < MIN_SAMPLES_MATURE → cap em DEVELOPING (sem MATURE)
        Regra 3: n_samples ≥ MIN_SAMPLES_MATURE → tabela completa
        """
        # Score → raw level (sempre)
        if   score >= self._mature_thr:     raw_idx = 3  # MATURE
        elif score >= self._developing_thr: raw_idx = 2  # DEVELOPING
        elif score >= self._growing_thr:    raw_idx = 1  # GROWING
        else:                                raw_idx = 0  # NASCENT

        # Cap por confiança: < MIN_SAMPLES_MATURE não pode ser MATURE
        max_idx = 2 if n_samples < self._min_samples_mature else 3

        return HIERARCHY[min(raw_idx, max_idx)]

    # ── Persistência ───────────────────────────────────────────────────────

    def persist(self, result: HealthResult, session_id: str = "default") -> bool:
        """
        Append HealthResult em health_history.jsonl + rotação se passar do limite.

        Returns: True se persistiu, False se falhou.
        """
        base = self._get_base_dir()
        path = base / "sessions" / f"{session_id}_cognitive" / "health_history.jsonl"

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("[chi] mkdir falhou: %s", e)
            return False

        with self._write_lock:
            try:
                # Append
                with open(path, "a", encoding="utf-8") as f:
                    f.write(result.to_jsonl() + "\n")

                # Rotação: se > MAX, trunca para últimas MAX
                self._rotate_if_needed(path)
                return True
            except Exception as e:
                logger.warning("[chi] persist falhou: %s", e)
                return False

    def _rotate_if_needed(self, path: Path) -> None:
        """Se > MAX_HEALTH_ENTRIES, mantém apenas as últimas N."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) <= MAX_HEALTH_ENTRIES:
                return
            # Rotação atômica
            keep = lines[-MAX_HEALTH_ENTRIES:]
            tmp = path.with_suffix(".jsonl.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(keep)
            os.replace(tmp, path)
            logger.info(
                "[chi] rotacionado | mantidas=%d removidas=%d",
                len(keep), len(lines) - len(keep),
            )
        except Exception as e:
            logger.warning("[chi] rotação falhou: %s", e)

    def get_history(self, session_id: str = "default", last_n: int = 30) -> List[dict]:
        """Lê últimas last_n entries do histórico. Útil para dashboard."""
        base = self._get_base_dir()
        path = base / "sessions" / f"{session_id}_cognitive" / "health_history.jsonl"

        if not path.exists():
            return []

        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            last = lines[-last_n:]
            return [json.loads(ln) for ln in last if ln.strip()]
        except Exception as e:
            logger.warning("[chi] get_history falhou: %s", e)
            return []

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _get_base_dir() -> Path:
        return Path(os.environ.get("EDP_BASE_DIR", "C:/edp_data"))


# ── Singleton ────────────────────────────────────────────────────────────────

_chi: Optional[CognitiveHealthIndex] = None
_chi_lock = threading.Lock()


def get_health_index() -> CognitiveHealthIndex:
    """Singleton thread-safe."""
    global _chi
    if _chi is None:
        with _chi_lock:
            if _chi is None:
                _chi = CognitiveHealthIndex()
    return _chi


# ── Feature flag helper ──────────────────────────────────────────────────────

def is_health_index_enabled() -> bool:
    """Verifica feature flag EDP_HEALTH_INDEX (default true)."""
    val = os.environ.get("EDP_HEALTH_INDEX", "true").strip().lower()
    return val in ("true", "1", "yes", "on")


# ── Background job factory ───────────────────────────────────────────────────

# Cooldown enforcement no nível do job
_last_chi_compute: dict[str, float] = {}
_chi_job_lock = threading.Lock()

DEFAULT_COOLDOWN_SEC = 900.0  # 15 minutos


def make_health_job(
    session_id: str = "default",
    cooldown_sec: float = DEFAULT_COOLDOWN_SEC,
    interval_sec: float = 60.0,
):
    """
    Cria BackgroundJob para CHI. Tick a cada interval_sec, mas só executa
    se cooldown_sec passou desde último compute (event-driven com cooldown).

    Returns: BackgroundJob pronto para loop.register_job()
    """
    from .background_loop import BackgroundJob

    async def _job_func():
        # Bypass se feature flag desligada
        if not is_health_index_enabled():
            return

        # Cooldown check
        with _chi_job_lock:
            last = _last_chi_compute.get(session_id, 0.0)
            now_ts = _now()
            if (now_ts - last) < cooldown_sec:
                return  # ainda em cooldown
            _last_chi_compute[session_id] = now_ts

        try:
            chi = get_health_index()
            result = chi.compute(session_id=session_id)

            if result.score is not None:
                chi.persist(result, session_id=session_id)
                logger.info(
                    "[chi] computed | score=%.3f level=%s n=%d",
                    result.score, result.level, result.samples_used,
                )
            else:
                logger.info(
                    "[chi] %s (n=%d)", result.level, result.samples_used,
                )
        except Exception as e:
            logger.warning(
                "[chi] compute falhou (não-fatal): %s: %s",
                type(e).__name__, str(e)[:120],
            )

    return BackgroundJob(
        name="cognitive_health_index",
        func=_job_func,
        interval_sec=interval_sec,
        priority=3,  # baixa
        suspend_on_pressure=True,
    )
