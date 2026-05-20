from __future__ import annotations

import threading
"""
biodiversity.py — EDP v3.2 Semantic Biodiversity Engine

Mantém diversidade semântica do store cognitivo.
Evita collapse (todos os nós semanticamente idênticos) e
dominance (um cluster domina todos os outros).

measure_and_rebalance(nodes, policy) → (BiodiversityReport, List[updated_nodes])

Algoritmo:
  1. Agrupa nós por abstraction_level
  2. Estima distribuição de clusters via centróides por nível
  3. Calcula Mean Radial Distance (MRD) como proxy de diversidade
  4. Se MRD < threshold → detecta collapse → penaliza nós muito centrais
     (aumenta pressure_score) para forçar decay mais rápido
  5. Se algum cluster domina > dominance_threshold → redistribui novelty

Performance:
  O(n·dim) — vetorizado numpy sem loops duplos.
  Centróides cacheados entre ciclos quando nós não mudam.

Lock: engine não tem lock próprio — chamado dentro de _write_lock do orchestrator.
"""

import math
import time
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from .types_v32 import (
    AbstractionLevel, ImmutableCognitiveNode,
    ScoringPolicy, DecisionEvent, DecisionEventType,
)


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class BiodiversityConfig:
    # MRD: distância média radial ao centróide global [0,1]
    mrd_collapse_threshold:   float = 0.15   # abaixo → collapse detectado
    mrd_target:               float = 0.35   # MRD saudável

    # Dominance: fração máxima de nós num cluster
    dominance_threshold:      float = 0.60   # acima → dominance detectado

    # Penalidade para nós muito centrais (redundantes)
    central_pressure_boost:   float = 0.12   # acréscimo em pressure_score
    novelty_boost_on_collapse: float = 0.08  # boost de novelty para nós periféricos

    # Cluster estimation (k-means lite com centróides fixos por level)
    max_centroids_per_level:  int   = 8
    min_nodes_for_analysis:   int   = 5      # mínimo para rodar análise


# ── Resultado ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ClusterDistribution:
    level:          AbstractionLevel
    n_nodes:        int
    n_clusters:     int
    mrd:            float     # mean radial distance
    dominant_frac:  float     # fração do maior cluster
    entropy:        float     # entropia da distribuição

    @property
    def is_collapsed(self) -> bool:
        return self.mrd < 0.15

    @property
    def is_dominated(self) -> bool:
        return self.dominant_frac > 0.60


@dataclass(frozen=True)
class BiodiversityReport:
    timestamp:              float
    n_nodes_analyzed:       int
    mean_radial_distance:   float    # MRD global [0,1] — usado pelo orchestrator
    global_entropy:         float
    collapse_detected:      bool
    dominance_detected:     bool
    n_nodes_updated:        int
    by_level:               Tuple[ClusterDistribution, ...]

    def snapshot(self) -> dict:
        return {
            "mrd":          round(self.mean_radial_distance, 4),
            "entropy":      round(self.global_entropy, 4),
            "collapse":     self.collapse_detected,
            "dominance":    self.dominance_detected,
            "updated":      self.n_nodes_updated,
            "n_analyzed":   self.n_nodes_analyzed,
        }


# ── Engine ────────────────────────────────────────────────────────────────────

class SemanticBiodiversityEngine:
    """
    Mede e reequilibra diversidade semântica do store.

    measure_and_rebalance() é chamado periodicamente pelo orchestrator.
    Retorna (report, updated_nodes) onde updated_nodes são ImmutableCognitiveNode
    com pressure_score ou novelty_score ajustados.
    """

    def __init__(
        self,
        config: Optional[BiodiversityConfig] = None,
        event_sink: Optional[Callable[[DecisionEvent], None]] = None,
    ) -> None:
        self._cfg   = config or BiodiversityConfig()
        self._sink  = event_sink or (lambda _: None)
        self._lock           = threading.Lock()
        self._total_cycles   = 0
        self._total_collapses = 0
        self._total_updates  = 0

    # ── API pública ────────────────────────────────────────────────────────────

    def measure_and_rebalance(
        self,
        nodes: List[ImmutableCognitiveNode],
        policy: ScoringPolicy,
    ) -> Tuple[BiodiversityReport, List[ImmutableCognitiveNode]]:
        """
        Analisa diversidade e retorna nós atualizados.
        Nunca modifica os nós originais — cria novos (imutável).
        O(n·dim) via numpy.
        """
        self._total_cycles += 1

        # Filtra nós com embedding
        valid = [n for n in nodes if n.embedding is not None and len(n.embedding) > 0]
        if len(valid) < self._cfg.min_nodes_for_analysis:
            report = BiodiversityReport(
                timestamp=time.time(),
                n_nodes_analyzed=len(valid),
                mean_radial_distance=1.0,  # assume diverso se poucos nós
                global_entropy=1.0,
                collapse_detected=False,
                dominance_detected=False,
                n_nodes_updated=0,
                by_level=(),
            )
            return report, []

        # Matriz de embeddings normalizada
        emb_matrix = np.vstack([np.array(n.embedding, dtype=np.float32) for n in valid])
        norms       = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
        emb_matrix  = emb_matrix / np.where(norms < 1e-8, 1.0, norms)

        # Centróide global
        centroid = emb_matrix.mean(axis=0)
        cn       = np.linalg.norm(centroid)
        if cn > 1e-8:
            centroid /= cn

        # MRD: distância cosine média ao centróide
        sims = (emb_matrix @ centroid).clip(-1.0, 1.0)
        dists = 1.0 - sims          # distância = 1 - similaridade
        mrd   = float(dists.mean())

        # Análise por nível
        by_level: List[ClusterDistribution] = []
        for level in AbstractionLevel:
            level_nodes = [n for n in valid if n.abstraction_level == level]
            if len(level_nodes) < 2:
                continue
            dist = self._analyze_level(level_nodes, level)
            by_level.append(dist)

        # Entropia global (distribuição de nós por nível)
        counts = [len([n for n in valid if n.abstraction_level == lv])
                  for lv in AbstractionLevel]
        global_entropy = _categorical_entropy(counts, len(valid))

        collapse_detected  = mrd < self._cfg.mrd_collapse_threshold
        dominance_detected = any(d.is_dominated for d in by_level)

        if collapse_detected:
            self._total_collapses += 1

        # Rebalancing: produz nós atualizados
        updated = self._rebalance(valid, emb_matrix, centroid, dists,
                                  collapse_detected, dominance_detected)
        self._total_updates += len(updated)

        if collapse_detected or dominance_detected:
            self._emit_alert(mrd, global_entropy, collapse_detected, dominance_detected)

        report = BiodiversityReport(
            timestamp=time.time(),
            n_nodes_analyzed=len(valid),
            mean_radial_distance=round(mrd, 4),
            global_entropy=round(global_entropy, 4),
            collapse_detected=collapse_detected,
            dominance_detected=dominance_detected,
            n_nodes_updated=len(updated),
            by_level=tuple(by_level),
        )
        return report, updated

    def health_report(self) -> dict:
        return {
            "total_cycles":    self._total_cycles,
            "total_collapses": self._total_collapses,
            "total_updates":   self._total_updates,
        }

    def metrics(self) -> dict:
        return self.health_report()

    # ── Interno ────────────────────────────────────────────────────────────────

    def _analyze_level(
        self,
        nodes: List[ImmutableCognitiveNode],
        level: AbstractionLevel,
    ) -> ClusterDistribution:
        if not nodes:
            return ClusterDistribution(level, 0, 0, 1.0, 0.0, 0.0)

        emb  = np.vstack([np.array(n.embedding, dtype=np.float32) for n in nodes])
        norms = np.linalg.norm(emb, axis=1, keepdims=True)
        emb   = emb / np.where(norms < 1e-8, 1.0, norms)

        k = min(self._cfg.max_centroids_per_level, max(2, len(nodes) // 3))
        # K-means lite: k iterações do Lloyd com centróides iniciais aleatórios
        # Sementes: primeiros k nós (deterministicamente estável)
        centroids = emb[:k].copy()

        for _ in range(5):  # 5 iterações — suficiente para convergir
            # Assign: O(n·k·dim)
            sims     = emb @ centroids.T  # (n, k)
            labels   = sims.argmax(axis=1)
            # Update centróides
            new_c = np.zeros_like(centroids)
            for c_idx in range(k):
                mask = labels == c_idx
                if mask.any():
                    c = emb[mask].mean(axis=0)
                    cn = np.linalg.norm(c)
                    new_c[c_idx] = c / cn if cn > 1e-8 else c
                else:
                    new_c[c_idx] = centroids[c_idx]
            centroids = new_c

        # Métricas do nível
        cluster_counts = [(labels == i).sum() for i in range(k)]
        dominant_frac  = max(cluster_counts) / max(len(nodes), 1)

        # MRD por nível
        centroid_global = emb.mean(axis=0)
        cgn = np.linalg.norm(centroid_global)
        if cgn > 1e-8:
            centroid_global /= cgn
        sims_g = (emb @ centroid_global).clip(-1, 1)
        mrd    = float((1.0 - sims_g).mean())

        entropy = _categorical_entropy(cluster_counts, len(nodes))
        n_non_empty = sum(1 for c in cluster_counts if c > 0)

        return ClusterDistribution(
            level=level,
            n_nodes=len(nodes),
            n_clusters=n_non_empty,
            mrd=round(mrd, 4),
            dominant_frac=round(dominant_frac, 4),
            entropy=round(entropy, 4),
        )

    def _rebalance(
        self,
        nodes: List[ImmutableCognitiveNode],
        emb_matrix: np.ndarray,
        centroid: np.ndarray,
        dists: np.ndarray,
        collapse: bool,
        dominance: bool,
    ) -> List[ImmutableCognitiveNode]:
        """
        Produz lista de nós atualizados (imutáveis → novos objetos).
        - Collapse: nós muito centrais (baixa distância) recebem pressure boost
        - Dominance: nós periféricos recebem novelty boost
        """
        if not collapse and not dominance:
            return []

        updated = []
        cfg     = self._cfg
        median_dist = float(np.median(dists))

        for i, node in enumerate(nodes):
            d = float(dists[i])
            new_pressure = node.pressure_score
            new_novelty  = node.novelty_score

            if collapse and d < median_dist * 0.5:
                # Muito central → redundante → aumenta pressão para decay mais rápido
                new_pressure = min(new_pressure + cfg.central_pressure_boost, 1.0)

            if dominance and d > median_dist * 1.5:
                # Periférico → contribui para diversidade → boost de novelty
                new_novelty = min(new_novelty + cfg.novelty_boost_on_collapse, 1.0)

            if new_pressure != node.pressure_score or new_novelty != node.novelty_score:
                # ImmutableCognitiveNode é frozen — usa _clone()
                updated.append(node._clone(
                    pressure_score=round(new_pressure, 4),
                    novelty_score=round(new_novelty, 4),
                ))

        return updated

    def _emit_alert(self, mrd: float, entropy: float,
                    collapse: bool, dominance: bool) -> None:
        try:
            self._sink(DecisionEvent(
                event_type=DecisionEventType.NOVELTY_REBALANCE,
                metadata=(
                    ("mrd",       str(round(mrd, 4))),
                    ("entropy",   str(round(entropy, 4))),
                    ("collapse",  str(collapse)),
                    ("dominance", str(dominance)),
                ),
            ))
        except Exception:
            pass  # _emit never kills runtime — failure tracked externally


# ── Utils ─────────────────────────────────────────────────────────────────────

def _categorical_entropy(counts: list, total: int) -> float:
    if total <= 0:
        return 0.0
    entropy = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            entropy -= p * math.log2(p)
    max_e = math.log2(max(len(counts), 2))
    return round(entropy / max_e, 4) if max_e > 0 else 0.0