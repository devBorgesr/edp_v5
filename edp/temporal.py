"""
temporal.py — Modelagem temporal do EDP v3.
Decay exponencial e gaussiano, reinforcement by access,
temporal relevance score, recency bias.
"""
import math
import time

from .config import DECAY_LAMBDA, TEMPORAL_DECAY_TYPE, TEMPORAL_GAUSSIAN_STD

# ── Decay functions ───────────────────────────────────────────────────────────

def decay_exponential(ultimo_acesso: float, lam: float = DECAY_LAMBDA) -> float:
    """
    Decay exponencial clássico.
    score = exp(-λ × dias_desde_acesso)
    λ=0.1 → meia-vida ~7d | λ=0.5 → meia-vida ~1.4d
    """
    dias = (time.time() - ultimo_acesso) / 86_400.0
    return math.exp(-lam * max(dias, 0.0))

def decay_gaussian(
    ultimo_acesso: float,
    std_dias: float = TEMPORAL_GAUSSIAN_STD,
) -> float:
    """
    Decay gaussiano — queda mais suave no início, depois rápida.
    score = exp(-0.5 × (dias/std)²)
    """
    dias = (time.time() - ultimo_acesso) / 86_400.0
    return math.exp(-0.5 * (max(dias, 0.0) / std_dias) ** 2)

def decay(ultimo_acesso: float) -> float:
    """Seleciona tipo de decay via config."""
    if TEMPORAL_DECAY_TYPE == "gaussian":
        return decay_gaussian(ultimo_acesso)
    return decay_exponential(ultimo_acesso)

# ── Reinforcement by access ───────────────────────────────────────────────────

def access_boost(n_acessos: int) -> float:
    """
    Boost logarítmico por frequência de acesso.
    Simula reforço por repetição (memória de trabalho → semântica).
    boost = 1 + 0.05 × log(1 + n_acessos)
    """
    return 1.0 + 0.05 * math.log1p(max(n_acessos, 0))

# ── Score temporal composto ───────────────────────────────────────────────────

def temporal_score(
    importance: float,
    ultimo_acesso: float,
    n_acessos: int = 0,
    lam: float = DECAY_LAMBDA,
) -> float:
    """
    Score temporal completo:
    score = importance × decay × access_boost
    """
    d     = decay_exponential(ultimo_acesso, lam)
    boost = access_boost(n_acessos)
    return round(importance * d * boost, 6)

# ── Recency bias ──────────────────────────────────────────────────────────────

def recency_rank(entries: list[dict], top_k: int | None = None) -> list[dict]:
    """
    Ordena entradas por recência pura (ultimo_acesso DESC).
    Usado para working memory onde recência domina relevância.
    """
    sorted_entries = sorted(
        entries,
        key=lambda e: e.get("ultimo_acesso", 0),
        reverse=True,
    )
    return sorted_entries[:top_k] if top_k else sorted_entries

def age_days(timestamp: float) -> float:
    """Retorna a idade de um timestamp em dias."""
    return (time.time() - timestamp) / 86_400.0

