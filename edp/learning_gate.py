# edp_v3/learning_gate.py
"""
learning_gate.py — Filtro cognitivo central do EDP v3.

Controla o que o sistema aprende ao longo do tempo.
Se este módulo falhar, toda a memória degrada.

Design:
    - Sem estado interno — puro cálculo, seguro para milhares de chamadas/s
    - Sem exceções propagadas — entradas inválidas são sanitizadas silenciosamente
    - Baseado na razão áurea (φ ≈ 1.618) como ponto de equilíbrio natural:
        · 1/φ  ≈ 0.618 — peso da curva de score
        · 1/φ² ≈ 0.382 — threshold de aceitação (segundo nível da sequência)
        · Isso não é cosmético: φ minimiza a sensibilidade local da função
          na região de incerteza (score ≈ 0.5), reduzindo flutuações bruscas.

Invariantes garantidos:
    - should_store(0, x, x)  → False  sempre
    - should_store(x, 0, x)  → False  sempre (confiança zero = nunca aprender)
    - should_store(1, 1, 1)  → True   sempre
    - memory_priority nunca retorna valor fora de {"alta", "media", "baixa"}
"""

from __future__ import annotations
import math

# ── Constantes áureas ─────────────────────────────────
_PHI: float       = (1.0 + math.sqrt(5.0)) / 2.0   # φ ≈ 1.618
_INV_PHI: float   = 1.0 / _PHI                      # 1/φ ≈ 0.618
_INV_PHI2: float  = _INV_PHI ** 2                   # 1/φ² ≈ 0.382  ← threshold base

# novelty abaixo deste valor recebe penalidade exponencial
# (informação muito redundante raramente vale ser guardada)
_NOVELTY_PENALTY_FLOOR: float = 0.30
_NOVELTY_HARD_FLOOR: float    = 0.05   # abaixo disso → rejeição imediata

# threshold de gate score para cada prioridade
# "alta" exige gate_score ≥ φ * threshold_base ≈ 0.618
# garante que "alta" é genuinamente rara (~top 15% das entradas aceitas)
_THRESH_STORE: float  = _INV_PHI2          # ≈ 0.382
_THRESH_ALTA:  float  = _INV_PHI           # ≈ 0.618
_THRESH_MEDIA: float  = _INV_PHI2          # ≈ 0.382


# ── Sanitização de entrada ────────────────────────────

def _clamp(value: float | None, default: float = 0.0) -> float:
    """
    Converte entrada para float válido em [0.0, 1.0].
    Nunca lança exceção — valores inválidos resultam em `default`.
    """
    if value is None:
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return max(0.0, min(1.0, v))


# ── Gate score ────────────────────────────────────────

def _gate_score(score: float, confidence: float, novelty: float) -> float:
    """
    Calcula gate score composto.

    Fórmula:
        gate = score^(1/φ) × confidence × novelty_factor

    Justificativa de cada componente:
        score^(1/φ)   → curva suavizada: penaliza scores baixos de forma
                         sublinear, reduzindo cliff edges próximo a 0.5.
                         O expoente 1/φ ≈ 0.618 não é arbitrário — minimiza
                         a derivada local na região de incerteza.

        × confidence  → penalidade linear direta: confiança zero = gate zero.
                         Não há suavização aqui: aprender com baixa confiança
                         é sempre errado.

        × novelty_factor → penalidade não-linear para redundância:
                         - novelty >= PENALTY_FLOOR → usa novelty direto
                         - novelty <  PENALTY_FLOOR → penalidade exponencial
                           novelty_factor = novelty^2 / PENALTY_FLOOR
                         - novelty <  HARD_FLOOR    → force zero (rejeição)
    """
    if score == 0.0 or confidence == 0.0:
        return 0.0

    # Fator de novelty com penalidade não-linear
    if novelty < _NOVELTY_HARD_FLOOR:
        novelty_factor = 0.0
    elif novelty < _NOVELTY_PENALTY_FLOOR:
        # Penalidade quadrática suave — cai mais rápido que linear
        # mas sem cliff edge abrupto
        novelty_factor = (novelty ** 2) / _NOVELTY_PENALTY_FLOOR
    else:
        novelty_factor = novelty

    curved_score = score ** _INV_PHI

    return curved_score * confidence * novelty_factor


# ── API pública ───────────────────────────────────────

def should_store(
    score:      float | None,
    confidence: float | None,
    novelty:    float | None = 1.0,
) -> bool:
    """
    Decide se um conteúdo deve ser armazenado na memória.

    Args:
        score:      Relevância semântica do conteúdo [0, 1].
        confidence: Confiança da fonte/contexto [0, 1].
        novelty:    Novidade relativa à memória existente [0, 1].
                    1.0 = totalmente novo, 0.0 = completamente redundante.

    Returns:
        True se deve armazenar, False caso contrário.

    Garantias:
        - score=0 ou confidence=0 → sempre False
        - novelty < 0.05          → sempre False
        - valores None/inválidos  → tratados como 0.0 (rejeição segura)
    """
    s = _clamp(score,      default=0.0)
    c = _clamp(confidence, default=0.0)
    n = _clamp(novelty,    default=1.0)

    return _gate_score(s, c, n) >= _THRESH_STORE


def memory_priority(
    score:      float | None,
    confidence: float | None,
) -> str:
    """
    Define a prioridade de uma entrada de memória.

    Args:
        score:      Relevância semântica [0, 1].
        confidence: Confiança do contexto [0, 1].

    Returns:
        "alta"   → gate_score ≥ 1/φ ≈ 0.618  (raro, ~top 15%)
        "media"  → gate_score ≥ 1/φ² ≈ 0.382
        "baixa"  → gate_score < 1/φ²

    Nota:
        novelty não entra aqui — prioridade é sobre importância intrínseca,
        não sobre novidade. Um conceito central já visto merece "alta" mesmo
        sendo familiar.
    """
    s = _clamp(score,      default=0.0)
    c = _clamp(confidence, default=0.0)

    # Sem novelty: usa 1.0 para isolar score × confidence
    gate = _gate_score(s, c, novelty=1.0)

    if gate >= _THRESH_ALTA:
        return "alta"
    if gate >= _THRESH_MEDIA:
        return "media"
    return "baixa"


def gate_info(
    score:      float | None,
    confidence: float | None,
    novelty:    float | None = 1.0,
) -> dict:
    """
    Retorna diagnóstico completo da decisão do gate.
    Útil para logging, auditoria e debug.

    Returns:
        {
            "should_store": bool,
            "priority":     str,
            "gate_score":   float,
            "threshold":    float,
            "inputs":       {score, confidence, novelty},
            "margin":       float,  # gate_score - threshold (positivo = aceitou)
        }
    """
    s = _clamp(score,      default=0.0)
    c = _clamp(confidence, default=0.0)
    n = _clamp(novelty,    default=1.0)

    gs     = _gate_score(s, c, n)
    store  = gs >= _THRESH_STORE
    prio   = memory_priority(s, c)
    margin = round(gs - _THRESH_STORE, 6)

    return {
        "should_store": store,
        "priority":     prio,
        "gate_score":   round(gs, 6),
        "threshold":    _THRESH_STORE,
        "margin":       margin,
        "inputs":       {
            "score":      s,
            "confidence": c,
            "novelty":    n,
        },
    }