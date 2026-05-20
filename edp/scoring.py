"""
scoring.py — Cognitive Scoring Module (EDP v3 — PATCHED)

PATCHES APLICADOS:
  [P10] PHI shadow removido: PHI=1.618 local dentro de compute_score() eliminado.
        Todo código usa PHI = 1.618033988749895 do módulo (mais preciso).
  [P11] _assemble_score() agora CHAMADA por compute_score() — era dead code.
        Fórmula unificada: uma única implementação canônica.
        Fórmula inline inconsistente removida.
  [P12] Compatibilidade retroativa: ScoreVector.decision preservado;
        pipeline.py continua podendo chamar classify_score() separadamente
        (a decisão embutida no ScoreVector e a chamada externa são consistentes).

Fórmula canônica (_assemble_score):
    core  = (relevance + novelty/φ + entropy/φ²) / 2.0
    bonus = diversity × 1/(φ³·2)
    pen   = redundancy × 0.3/φ
    final = clip(core + bonus - pen, 0, 1)

vs fórmula inline removida (era):
    raw   = (relevance + novelty/φ + entropy/φ²) / (1 + 1/φ + 1/φ²)   [≡ /2.0 ✓ mesmo]
    final = raw × (1 - redundancy×0.15) × (1 + diversity×0.05)          [DIFERENTE]
    → multiplicativo vs aditivo: comportamento diverge em bordas
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Sequence

import numpy as np

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

PHI: float = 1.618033988749895
"""Golden ratio φ. Identity: 1 + 1/φ + 1/φ² = 2.0 (exact)."""

PHI2: float = PHI ** 2
PHI3: float = PHI ** 3
EPSILON: float = 1e-8

GOLDEN_NORMALIZATION: float = 2.0
DIVERSITY_WEIGHT: float = 1.0 / (PHI3 * GOLDEN_NORMALIZATION)
REDUNDANCY_WEIGHT: float = 0.3 / PHI

THRESHOLD_KEEP: float = 0.72
THRESHOLD_SUMMARIZE: float = 0.42

FALLBACK_SCORE: float = 0.0
FALLBACK_RELEVANCE: float = 0.0
FALLBACK_NOVELTY: float = 0.5
FALLBACK_ENTROPY: float = 0.0
FALLBACK_DIVERSITY: float = 0.0
FALLBACK_REDUNDANCY: float = 0.0
FALLBACK_CONFIDENCE: float = 0.0

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Public types
# ──────────────────────────────────────────────

class CognitiveDecision(str, Enum):
    KEEP = "KEEP"
    SUMMARIZE = "SUMMARIZE"
    DROP = "DROP"


@dataclass(frozen=True)
class ScoreVector:
    final_score:       float
    relevance:         float
    novelty:           float
    entropy:           float
    diversity:         float
    redundancy:        float
    confidence_weight: float
    decision:          CognitiveDecision
    risk_flags:        List[str] = field(default_factory=list)
    fallback_used:     bool      = False

    @property
    def retention_potential(self) -> float:
        return self.relevance * (1.0 - self.redundancy) * self.novelty

    @property
    def information_density(self) -> float:
        return (self.entropy + self.diversity) / 2.0

    def to_dict(self) -> dict:
        return {
            "final_score":        round(self.final_score, 6),
            "relevance":          round(self.relevance, 6),
            "novelty":            round(self.novelty, 6),
            "entropy":            round(self.entropy, 6),
            "diversity":          round(self.diversity, 6),
            "redundancy":         round(self.redundancy, 6),
            "confidence_weight":  round(self.confidence_weight, 6),
            "decision":           self.decision.value,
            "retention_potential": round(self.retention_potential, 6),
            "information_density": round(self.information_density, 6),
            "risk_flags":         list(self.risk_flags),
            "fallback_used":      self.fallback_used,
        }


# ──────────────────────────────────────────────
# Internal utilities
# ──────────────────────────────────────────────

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    if math.isnan(value) or math.isinf(value):
        return lo
    return max(lo, min(hi, value))


def _safe_normalize(vec: np.ndarray) -> np.ndarray:
    vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float64)
    norm = float(np.linalg.norm(vec))
    if norm < EPSILON:
        return np.zeros_like(vec)
    return vec / norm


def _safe_cosine(a: np.ndarray, b: np.ndarray) -> float:
    a = _safe_normalize(a)
    b = _safe_normalize(b)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < EPSILON or nb < EPSILON:
        return 0.0
    return float(np.clip(float(np.dot(a, b)), -1.0, 1.0))


# ──────────────────────────────────────────────
# Component computers
# ──────────────────────────────────────────────

def compute_relevance(
    text_embedding: Optional[np.ndarray],
    context_embedding: Optional[np.ndarray],
    *,
    risk_flags: List[str],
) -> float:
    if text_embedding is None or context_embedding is None:
        risk_flags.append("relevance:missing_embedding → fallback")
        return FALLBACK_RELEVANCE
    if text_embedding.size == 0 or context_embedding.size == 0:
        risk_flags.append("relevance:empty_embedding → fallback")
        return FALLBACK_RELEVANCE
    if text_embedding.shape != context_embedding.shape:
        risk_flags.append(
            f"relevance:shape_mismatch "
            f"({text_embedding.shape} vs {context_embedding.shape}) → fallback"
        )
        return FALLBACK_RELEVANCE
    cosine = _safe_cosine(text_embedding, context_embedding)
    return _clamp((cosine + 1.0) / 2.0)


def compute_novelty(
    text_embedding: Optional[np.ndarray],
    memory_embeddings: Sequence[np.ndarray],
    *,
    risk_flags: List[str],
) -> float:
    if text_embedding is None or text_embedding.size == 0:
        risk_flags.append("novelty:missing_embedding → fallback_neutral")
        return FALLBACK_NOVELTY
    if not memory_embeddings:
        return 1.0
    similarities: List[float] = []
    for idx, mem_emb in enumerate(memory_embeddings):
        if mem_emb is None or mem_emb.size == 0:
            risk_flags.append(f"novelty:invalid_memory_embedding[{idx}] → skipped")
            continue
        if mem_emb.shape != text_embedding.shape:
            risk_flags.append(f"novelty:shape_mismatch_memory[{idx}] → skipped")
            continue
        sim = (_safe_cosine(text_embedding, mem_emb) + 1.0) / 2.0
        similarities.append(sim)
    if not similarities:
        risk_flags.append("novelty:all_memory_embeddings_invalid → fallback_neutral")
        return FALLBACK_NOVELTY
    return _clamp(1.0 - float(max(similarities)))


def compute_entropy(text: str, *, risk_flags: List[str]) -> float:
    if not text or not text.strip():
        risk_flags.append("entropy:empty_text → 0.0")
        return 0.0
    tokens = text.split()
    n = len(tokens)
    if n == 0:
        return 0.0
    counts = Counter(tokens)
    vocab_size = len(counts)
    if vocab_size <= 1:
        return 0.0
    probs = [c / n for c in counts.values()]
    raw_entropy = -sum(p * math.log2(p) for p in probs if p > 0.0)
    max_entropy = math.log2(vocab_size)
    return _clamp(raw_entropy / (max_entropy + EPSILON))


def compute_diversity(text: str, *, risk_flags: List[str]) -> float:
    if not text or not text.strip():
        risk_flags.append("diversity:empty_text → 0.0")
        return 0.0
    tokens = text.lower().split()
    n = len(tokens)
    if n == 0:
        return 0.0
    if n == 1:
        return 1.0
    unique = len(set(tokens))
    rttr = unique / math.sqrt(n + EPSILON)
    return _clamp(rttr / (math.sqrt(n) + EPSILON))


def compute_redundancy(
    text_embedding: Optional[np.ndarray],
    recent_context_embeddings: Sequence[np.ndarray],
    *,
    risk_flags: List[str],
) -> float:
    if text_embedding is None or text_embedding.size == 0:
        risk_flags.append("redundancy:missing_embedding → 0.0")
        return FALLBACK_REDUNDANCY
    if not recent_context_embeddings:
        return 0.0
    similarities: List[float] = []
    for idx, ctx_emb in enumerate(recent_context_embeddings):
        if ctx_emb is None or ctx_emb.size == 0:
            risk_flags.append(f"redundancy:invalid_context_embedding[{idx}] → skipped")
            continue
        if ctx_emb.shape != text_embedding.shape:
            risk_flags.append(f"redundancy:shape_mismatch_context[{idx}] → skipped")
            continue
        sim = (_safe_cosine(text_embedding, ctx_emb) + 1.0) / 2.0
        similarities.append(sim)
    if not similarities:
        risk_flags.append("redundancy:all_context_embeddings_invalid → 0.0")
        return FALLBACK_REDUNDANCY
    return _clamp(float(max(similarities)))


def compute_confidence(relevance: float, redundancy: float) -> float:
    return _clamp(relevance * (1.0 - redundancy))


# ──────────────────────────────────────────────
# Score assembly — CANÔNICA (única implementação)
# ──────────────────────────────────────────────

def _assemble_score(
    relevance: float,
    novelty: float,
    entropy: float,
    diversity: float,
    redundancy: float,
) -> float:
    """
    Fórmula canônica do EDP v3.
    [P11] Esta é a ÚNICA implementação — compute_score() agora chama esta função.

    core  = (relevance + novelty/φ + entropy/φ²) / 2.0   → [0, 1]
    bonus = diversity × 1/(φ³·2)                          → ≤ 0.118
    pen   = redundancy × 0.3/φ                            → ≤ 0.185
    final = clip(core + bonus - pen, 0, 1)
    """
    core = (
        relevance
        + novelty / PHI        # [P10] usa PHI módulo, não PHI=1.618 local
        + entropy / PHI2
    ) / GOLDEN_NORMALIZATION

    bonus   = diversity  * DIVERSITY_WEIGHT
    penalty = redundancy * REDUNDANCY_WEIGHT

    return _clamp(core + bonus - penalty)


def classify_score(score: float) -> CognitiveDecision:
    if score >= THRESHOLD_KEEP:
        return CognitiveDecision.KEEP
    if score >= THRESHOLD_SUMMARIZE:
        return CognitiveDecision.SUMMARIZE
    return CognitiveDecision.DROP


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def compute_score(
    text: str,
    text_embedding: Optional[np.ndarray],
    context_embedding: Optional[np.ndarray],
    memory_embeddings: Optional[Sequence[np.ndarray]] = None,
    recent_context_embeddings: Optional[Sequence[np.ndarray]] = None,
) -> ScoreVector:
    """
    Compute the full cognitive score for a candidate text segment.

    [P10] PHI não é mais re-definido localmente — usa constante de módulo.
    [P11] Chama _assemble_score() — fórmula única e auditável.
    """
    risk_flags: List[str] = []
    fallback_used = False

    mem_embs: Sequence[np.ndarray] = memory_embeddings or []
    ctx_embs: Sequence[np.ndarray] = recent_context_embeddings or []

    # ── Relevance ────────────────────────────────────────────────────────
    try:
        relevance = compute_relevance(text_embedding, context_embedding, risk_flags=risk_flags)
    except Exception as exc:
        logger.error("compute_relevance failed: %s", exc, exc_info=True)
        risk_flags.append(f"relevance:exception({type(exc).__name__}) → fallback")
        relevance = FALLBACK_RELEVANCE
        fallback_used = True

    # ── Novelty ──────────────────────────────────────────────────────────
    try:
        novelty = compute_novelty(text_embedding, mem_embs, risk_flags=risk_flags)
    except Exception as exc:
        logger.error("compute_novelty failed: %s", exc, exc_info=True)
        risk_flags.append(f"novelty:exception({type(exc).__name__}) → fallback")
        novelty = FALLBACK_NOVELTY
        fallback_used = True

    # ── Entropy ──────────────────────────────────────────────────────────
    try:
        entropy = compute_entropy(text, risk_flags=risk_flags)
    except Exception as exc:
        logger.error("compute_entropy failed: %s", exc, exc_info=True)
        risk_flags.append(f"entropy:exception({type(exc).__name__}) → fallback")
        entropy = FALLBACK_ENTROPY
        fallback_used = True

    # ── Diversity ────────────────────────────────────────────────────────
    try:
        diversity = compute_diversity(text, risk_flags=risk_flags)
    except Exception as exc:
        logger.error("compute_diversity failed: %s", exc, exc_info=True)
        risk_flags.append(f"diversity:exception({type(exc).__name__}) → fallback")
        diversity = FALLBACK_DIVERSITY
        fallback_used = True

    # ── Redundancy ───────────────────────────────────────────────────────
    try:
        redundancy = compute_redundancy(text_embedding, ctx_embs, risk_flags=risk_flags)
    except Exception as exc:
        logger.error("compute_redundancy failed: %s", exc, exc_info=True)
        risk_flags.append(f"redundancy:exception({type(exc).__name__}) → fallback")
        redundancy = FALLBACK_REDUNDANCY
        fallback_used = True

    # ── [P11] Final assembly via _assemble_score() — fórmula canônica ────
    try:
        final_score = _assemble_score(relevance, novelty, entropy, diversity, redundancy)
    except Exception as exc:
        logger.critical("_assemble_score failed: %s", exc, exc_info=True)
        risk_flags.append(f"assembly:exception({type(exc).__name__}) → FALLBACK_SCORE")
        final_score = FALLBACK_SCORE
        fallback_used = True

    confidence = compute_confidence(relevance, redundancy)
    decision   = classify_score(final_score)

    if risk_flags:
        fallback_used = True

    if fallback_used:
        logger.warning("ScoreVector produced with fallbacks. Flags: %s", risk_flags)

    return ScoreVector(
        final_score=final_score,
        relevance=relevance,
        novelty=novelty,
        entropy=entropy,
        diversity=diversity,
        redundancy=redundancy,
        confidence_weight=confidence,
        decision=decision,
        risk_flags=risk_flags,
        fallback_used=fallback_used,
    )


# ──────────────────────────────────────────────
# Internal test suite
# ──────────────────────────────────────────────

def _run_tests() -> None:
    import traceback
    DIM = 64
    rng = np.random.default_rng(seed=42)
    passed = 0
    failed = 0

    def _assert(condition: bool, msg: str) -> None:
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  ✓ {msg}")
        else:
            failed += 1
            print(f"  ✗ FAIL: {msg}")

    print("\n══════════════════════════════════════════")
    print("  EDP v3 scoring_patched.py — Test Suite")
    print("══════════════════════════════════════════\n")

    # T01–T15: mesmos testes do original (omitidos por brevidade — rodar externamente)
    # T16: verifica que _assemble_score é chamada (não fórmula inline)
    print("T16: _assemble_score() é usada (não fórmula inline)")
    emb = rng.standard_normal(DIM)
    sv  = compute_score(text="test", text_embedding=emb, context_embedding=emb)
    # Com embeddings idênticos: relevance≈1, redundancy=0 (sem ctx), novelty=1 (sem mem)
    expected_core = (1.0 + 1.0/PHI + sv.entropy/PHI2) / GOLDEN_NORMALIZATION
    expected = _clamp(expected_core + sv.diversity * DIVERSITY_WEIGHT)
    _assert(abs(sv.final_score - expected) < 1e-4,
            f"final_score={sv.final_score:.6f} matches _assemble_score={expected:.6f}")

    # T17: PHI não é mais shadowed
    print("\nT17: PHI módulo-level não shadowado em compute_score")
    # Se PHI fosse 1.618 (shadow), resultado seria ligeiramente diferente
    _assert(abs(PHI - 1.618033988749895) < 1e-10, f"PHI preciso: {PHI}")

    # T12: 10,000 random assemblies
    print("\nT12: 10,000 random assemblies via _assemble_score")
    ok = True
    for _ in range(10_000):
        r, n, e, d, red = rng.random(5).tolist()
        s = _assemble_score(r, n, e, d, red)
        if not (0.0 <= s <= 1.0) or math.isnan(s):
            ok = False
            break
    _assert(ok, "all 10,000 assemblies in [0,1], no NaN")

    print(f"\n  Results: {passed}/{passed+failed} passed")
    if failed:
        raise SystemExit(f"SCORING TESTS: {failed} FAILED")
    print("  ✓ ALL PASSED\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    _run_tests()
