"""
edp.runtime.quality_score — Exposição de ScoreVector como QualityResult.

Commit A (Sessão 2, 08/06/2026):
  Reusa ScoreVector que já existe em edp.scoring (calculado por
  pipeline.py em chunk_score_vectors). Não constrói scoring novo —
  apenas combina componentes em score único + verdict legível.

Princípio aplicado:
  Reuso de Infraestrutura — ScoreVector já calcula relevance, novelty,
  entropy, diversity, redundancy, confidence_weight em compute_score().
  Quality Score é projeção semântica desses componentes.

Fórmula (Opção A — sem entropia):
  score = relevance       * 0.30
        + novelty         * 0.20
        + diversity       * 0.20
        + (1 - redundancy)* 0.15
        + confidence      * 0.15
  Total: 1.00

Decisão arquitetural: entropia EXPOSTA em components (auditoria) mas
NÃO pesada no verdict. Entropia alta pode ser sinal de complexidade
boa OU incoerência — ambígua sem contexto adicional.

Feature flag: EDP_QUALITY_SCORE=true|false (default true).
Thresholds: QS_APPROVE (default 0.75), QS_CAVEAT (default 0.50).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, asdict
from typing import Optional


logger = logging.getLogger("edp.runtime.quality_score")


# ── QualityResult dataclass ──────────────────────────────────────────────────

@dataclass
class QualityResult:
    """
    Resultado da projeção ScoreVector → score único + verdict legível.

    score: float em [0.0, 1.0] — composto ponderado
    verdict: "APPROVED" | "APPROVED_WITH_CAVEAT" | "LOW_CONFIDENCE"
    components: dict com componentes individuais (auditoria)
    source: identifica origem ("score_vector" no Commit A)
    """
    score:      float
    verdict:    str
    components: dict
    source:     str = "score_vector"

    def to_dict(self) -> dict:
        return asdict(self)


# ── QualityScoreExposer ──────────────────────────────────────────────────────

class QualityScoreExposer:
    """
    Converte ScoreVector agregado em QualityResult.
    Thresholds via env var para calibração futura sem mudar código.

    Uso típico:
        exposer = QualityScoreExposer()
        result = exposer.from_score_vector(pres.aggregate_score)
        if result:
            await ws.send_json({"type": "quality", **result.to_dict()})
    """

    # Defaults thresholds — podem ser sobrescritos via env
    DEFAULT_APPROVE = 0.75
    DEFAULT_CAVEAT  = 0.50

    # Pesos da fórmula (Opção A, soma = 1.00)
    W_RELEVANCE      = 0.30
    W_NOVELTY        = 0.20
    W_DIVERSITY      = 0.20
    W_NON_REDUNDANT  = 0.15  # (1 - redundancy)
    W_CONFIDENCE     = 0.15

    def __init__(self):
        try:
            self.t_approve = float(os.getenv("QS_APPROVE", str(self.DEFAULT_APPROVE)))
            self.t_caveat  = float(os.getenv("QS_CAVEAT",  str(self.DEFAULT_CAVEAT)))
        except ValueError:
            logger.warning(
                "[quality_score] env vars inválidas, usando defaults: "
                "APPROVE=%s CAVEAT=%s",
                self.DEFAULT_APPROVE, self.DEFAULT_CAVEAT,
            )
            self.t_approve = self.DEFAULT_APPROVE
            self.t_caveat  = self.DEFAULT_CAVEAT
        # Sanidade: approve > caveat
        if self.t_approve <= self.t_caveat:
            logger.warning(
                "[quality_score] thresholds inválidos (approve <= caveat), "
                "usando defaults",
            )
            self.t_approve = self.DEFAULT_APPROVE
            self.t_caveat  = self.DEFAULT_CAVEAT

    def from_score_vector(self, sv) -> Optional[QualityResult]:
        """
        Converte ScoreVector → QualityResult.

        Args:
            sv: ScoreVector (de edp.scoring) ou None.
                Acessa: relevance, novelty, diversity, redundancy,
                        confidence_weight, entropy (componente expostos
                        mas não pesado).

        Returns:
            QualityResult ou None se sv é None ou inválido.

        Robustez:
            Falha em acesso a atributo → warning + None
            (não derruba pipeline conversacional).
        """
        if sv is None:
            return None
        try:
            relevance  = float(sv.relevance)
            novelty    = float(sv.novelty)
            entropy    = float(sv.entropy)
            diversity  = float(sv.diversity)
            redundancy = float(sv.redundancy)
            confidence = float(sv.confidence_weight)
        except (AttributeError, TypeError, ValueError) as e:
            logger.warning(
                "[quality_score] ScoreVector malformado: %s: %s",
                type(e).__name__, str(e)[:120],
            )
            return None

        # Clamp defensivo em [0.0, 1.0]
        relevance  = max(0.0, min(1.0, relevance))
        novelty    = max(0.0, min(1.0, novelty))
        diversity  = max(0.0, min(1.0, diversity))
        redundancy = max(0.0, min(1.0, redundancy))
        confidence = max(0.0, min(1.0, confidence))

        score = (
            relevance              * self.W_RELEVANCE
            + novelty              * self.W_NOVELTY
            + diversity            * self.W_DIVERSITY
            + (1.0 - redundancy)   * self.W_NON_REDUNDANT
            + confidence           * self.W_CONFIDENCE
        )
        # Clamp final (paranoia — fórmula já garante)
        score = max(0.0, min(1.0, score))

        if score >= self.t_approve:
            verdict = "APPROVED"
        elif score >= self.t_caveat:
            verdict = "APPROVED_WITH_CAVEAT"
        else:
            verdict = "LOW_CONFIDENCE"

        return QualityResult(
            score=round(score, 3),
            verdict=verdict,
            components={
                "relevance":   round(relevance,  3),
                "novelty":     round(novelty,    3),
                "diversity":   round(diversity,  3),
                "redundancy":  round(redundancy, 3),
                "confidence":  round(confidence, 3),
                "entropy":     round(entropy,    3),  # exposto, não pesado
            },
        )


# ── Singleton thread-safe (padrão runtime EDP) ────────────────────────────────

import threading

_exposer: Optional[QualityScoreExposer] = None
_exposer_lock = threading.Lock()


def get_quality_exposer() -> QualityScoreExposer:
    """Singleton thread-safe do exposer."""
    global _exposer
    if _exposer is None:
        with _exposer_lock:
            if _exposer is None:
                _exposer = QualityScoreExposer()
    return _exposer


# ── Feature flag helper ───────────────────────────────────────────────────────

def is_quality_score_enabled() -> bool:
    """
    Verifica feature flag EDP_QUALITY_SCORE (default true).
    Re-lida a cada chamada para permitir toggle dinâmico em prod.
    """
    val = os.environ.get("EDP_QUALITY_SCORE", "true").strip().lower()
    return val in ("true", "1", "yes", "on")
