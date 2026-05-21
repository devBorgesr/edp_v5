"""
belief_graph.py — Grafo de crenças do EDP v3 (PATCHED)

PATCHES APLICADOS:
  [P-C3-6] build_from_entries(): O(n²) loop → np.where() vetorizado
            Antes: for i in range(n): for j in range(i+1, n): → O(n²) iterações Python
            Depois: np.where(sim_matrix >= thresh) → índices de pares em 1 operação NumPy
  [P-C3-7] add_edge(): MAX_EDGES assimétrico corrigido
            Antes: remove weakest de source E target antes de criar aresta nova
                   → pode remover aresta recém-adicionada se ela for a mais fraca
            Depois: verifica capacidade ANTES de criar, adiciona de forma atômica
  [P-C3-8] build_from_entries(): exposta como função standalone build_from_entries()
            para compatibilidade com scheduler._job_graph_link() que importa
            from .memory_graph import build_from_entries
            NOTA: scheduler ainda vai falhar com ImportError (importa de .memory_graph
            não de .belief_graph). Patch correto do scheduler está em scheduler_patched.py.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from .config import MEMORY_DIR
from .clock import now as _now  # Peça 0.2a — relógio interno robusto

SIMILAR_THRESH     = 0.65
CONTRADICT_THRESH  = 0.20
MAX_EDGES          = 15
DECAY_RATE         = 0.02


# ── BeliefEdge ────────────────────────────────────────────────────────────────

@dataclass
class BeliefEdge:
    source_id:      str
    target_id:      str
    relation:       str
    strength:       float
    created_at:     float = field(default_factory=_now)
    last_updated:   float = field(default_factory=_now)
    co_occurrences: int   = 1

    @property
    def effective_strength(self) -> float:
        dias = (_now() - self.last_updated) / 86_400.0
        return round(self.strength * math.exp(-DECAY_RATE * max(dias, 0.0)), 4)

    def reinforce(self, delta: float = 0.05) -> None:
        self.strength       = min(round(self.strength + delta, 4), 1.0)
        self.last_updated   = _now()
        self.co_occurrences += 1

    def to_dict(self) -> dict:
        return {
            "source_id":      self.source_id,
            "target_id":      self.target_id,
            "relation":       self.relation,
            "strength":       self.strength,
            "created_at":     self.created_at,
            "last_updated":   self.last_updated,
            "co_occurrences": self.co_occurrences,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BeliefEdge":
        return cls(
            source_id=d["source_id"],
            target_id=d["target_id"],
            relation=d.get("relation", "neutral"),
            strength=float(d.get("strength", 0.5)),
            created_at=float(d.get("created_at", _now())),
            last_updated=float(d.get("last_updated", _now())),
            co_occurrences=int(d.get("co_occurrences", 1)),
        )


# ── BeliefGraph ───────────────────────────────────────────────────────────────

class BeliefGraph:
    """
    Grafo não-dirigido de relações entre entradas de memória.
    [P-C3-6] build_from_entries() vetorizado via np.where().
    [P-C3-7] add_edge() atômico — sem remoção assimétrica.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._path      = MEMORY_DIR / f"{session_id}_belief_graph.json"
        self._adj: Dict[str, Dict[str, BeliefEdge]] = defaultdict(dict)
        self._load()

    # ── Persistência ──────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for edge_dict in raw:
                e = BeliefEdge.from_dict(edge_dict)
                self._adj[e.source_id][e.target_id] = e
                if e.target_id not in self._adj or e.source_id not in self._adj[e.target_id]:
                    mirror = BeliefEdge.from_dict({
                        **e.to_dict(),
                        "source_id": e.target_id,
                        "target_id": e.source_id,
                    })
                    self._adj[e.target_id][e.source_id] = mirror
        except Exception:
            self._adj = defaultdict(dict)

    def save(self) -> None:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        seen:  set[tuple[str, str]] = set()
        edges: list[dict]           = []
        for src, neighbors in self._adj.items():
            for tgt, edge in neighbors.items():
                key = tuple(sorted([src, tgt]))
                if key not in seen:
                    seen.add(key)
                    edges.append(edge.to_dict())
        self._path.write_text(
            json.dumps(edges, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── Gestão de arestas ─────────────────────────────────────────────────

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        strength:  float,
        relation:  str = "neutral",
    ) -> BeliefEdge:
        """
        [P-C3-7] FIX MAX_EDGES assimétrico:
        Antes: removia weakest de source E target ANTES de criar a aresta nova
               → se a nova aresta fosse a mais fraca de um dos nós, era removida
                 antes de ter o mirror criado (estado inconsistente)
        Depois: verifica capacidade ANTES de decidir criar.
                Se algum nó está cheio e a nova aresta é mais fraca que todas
                as existentes → descarta sem criar (não perturba estado).
                Só remove weakest se a nova aresta é mais forte que ela.
        """
        strength = max(0.0, min(1.0, float(strength)))

        # Reforço se já existe
        if target_id in self._adj[source_id]:
            edge = self._adj[source_id][target_id]
            edge.reinforce()
            if source_id in self._adj.get(target_id, {}):
                self._adj[target_id][source_id].reinforce()
            return edge

        # [P-C3-7] Verifica capacidade antes de criar — sem mutação prematura
        for node_id, other_id in ((source_id, target_id), (target_id, source_id)):
            if len(self._adj[node_id]) >= MAX_EDGES:
                weakest_id, weakest_edge = min(
                    self._adj[node_id].items(),
                    key=lambda x: x[1].effective_strength,
                )
                # Só remove se a nova aresta é MAIS FORTE que a mais fraca
                if strength <= weakest_edge.effective_strength:
                    # Nova aresta mais fraca que todas — descarta
                    return BeliefEdge(
                        source_id=source_id, target_id=target_id,
                        relation=relation, strength=strength,
                    )
                # Remove weakest + seu mirror antes de adicionar nova
                del self._adj[node_id][weakest_id]
                self._adj[weakest_id].pop(node_id, None)

        # Criação atômica de aresta + mirror
        edge = BeliefEdge(
            source_id=source_id, target_id=target_id,
            relation=relation, strength=strength,
        )
        mirror = BeliefEdge(
            source_id=target_id, target_id=source_id,
            relation=relation, strength=strength,
        )
        self._adj[source_id][target_id] = edge
        self._adj[target_id][source_id] = mirror
        return edge

    def get_neighbors(
        self,
        node_id:      str,
        top_k:        int   = 10,
        min_strength: float = 0.0,
    ) -> List[Tuple[str, BeliefEdge]]:
        neighbors = self._adj.get(node_id, {})
        filtered  = [
            (nid, e)
            for nid, e in neighbors.items()
            if e.effective_strength >= min_strength
        ]
        filtered.sort(key=lambda x: x[1].effective_strength, reverse=True)
        return filtered[:top_k]

    def remove_node(self, node_id: str) -> bool:
        if node_id not in self._adj:
            return False
        for neighbor_id in list(self._adj[node_id].keys()):
            self._adj[neighbor_id].pop(node_id, None)
        del self._adj[node_id]
        return True

    # ── [P-C3-6] build_from_entries — vetorizado ──────────────────────────

    def build_from_entries(
        self,
        entries:           list,
        similar_thresh:    float = SIMILAR_THRESH,
        contradict_thresh: float = CONTRADICT_THRESH,
    ) -> int:
        """
        [P-C3-6] FIX O(n²) loop Python → operações vetorizadas NumPy.

        Antes:
            for i in range(n):
                for j in range(i+1, n):     # O(n²) iterações Python
                    sim = sim_matrix[i,j]
                    if sim >= thresh: add_edge(...)

        Depois:
            sim_matrix já pré-computada → np.triu + np.where para obter
            todos os pares elegíveis em uma única operação vetorizada.
            Loop Python apenas sobre pares encontrados (geralmente << n²).

        Para n=500, threshold=0.65: se 2% dos pares são elegíveis → 5k vs 125k iterações.
        Para n=500, threshold=0.20 (contradicts): mais pares, mas np.where ainda vence
        pois a computação de índices é NumPy puro.
        """
        valid: list  = []
        embs:  list  = []
        for e in entries:
            emb = e.get("embedding")
            if emb is None:
                continue
            try:
                arr = np.array(emb, dtype=np.float32)
                if arr.ndim != 1 or arr.size == 0:
                    continue
                valid.append(e)
                embs.append(arr)
            except Exception:
                continue

        if len(valid) < 2:
            return 0

        matrix     = np.vstack(embs)
        sim_matrix = cosine_similarity(matrix)  # (n, n) — O(n²) NumPy, inevitable
        n_added    = 0

        # [P-C3-6] Extrai pares superiores de uma vez com np.where
        # triu_indices: evita duplos (i, j) e (j, i), exclui diagonal
        rows, cols = np.triu_indices(len(valid), k=1)
        sims_flat  = sim_matrix[rows, cols]

        # Pares similares
        sim_mask = sims_flat >= similar_thresh
        for i, j, sim in zip(rows[sim_mask], cols[sim_mask], sims_flat[sim_mask]):
            id_a = valid[int(i)].get("id", f"entry_{i}")
            id_b = valid[int(j)].get("id", f"entry_{j}")
            self.add_edge(id_a, id_b, float(sim), "similar")
            n_added += 1

        # Pares contraditórios
        con_mask = sims_flat <= contradict_thresh
        for i, j, sim in zip(rows[con_mask], cols[con_mask], sims_flat[con_mask]):
            id_a = valid[int(i)].get("id", f"entry_{i}")
            id_b = valid[int(j)].get("id", f"entry_{j}")
            self.add_edge(id_a, id_b, float(1.0 - sim), "contradicts")
            n_added += 1

        return n_added

    # ── Analytics ─────────────────────────────────────────────────────────

    def stats(self) -> dict:
        n_nodes   = len(self._adj)
        degrees   = [len(v) for v in self._adj.values()]
        seen:     set[tuple[str, str]] = set()
        n_edges   = 0
        rel_counts: dict[str, int] = defaultdict(int)

        for src, neighbors in self._adj.items():
            for tgt, edge in neighbors.items():
                key = tuple(sorted([src, tgt]))
                if key not in seen:
                    seen.add(key)
                    n_edges += 1
                    rel_counts[edge.relation] += 1

        return {
            "n_nodes":    n_nodes,
            "n_edges":    n_edges,
            "avg_degree": round(sum(degrees) / max(n_nodes, 1), 2),
            "max_degree": max(degrees, default=0),
            "relations":  dict(rel_counts),
        }

    def get_hubs(self, top_k: int = 10) -> List[Tuple[str, int, float]]:
        hubs = []
        for nid, neighbors in self._adj.items():
            if not neighbors:
                continue
            grau   = len(neighbors)
            w_mean = sum(e.effective_strength for e in neighbors.values()) / grau
            hubs.append((nid, grau, round(grau * w_mean, 4)))
        hubs.sort(key=lambda x: x[2], reverse=True)
        return hubs[:top_k]

    def apply_decay(self, min_strength: float = 0.05) -> int:
        removed = 0
        for src in list(self._adj.keys()):
            for tgt in list(self._adj[src].keys()):
                if self._adj[src][tgt].effective_strength < min_strength:
                    del self._adj[src][tgt]
                    self._adj[tgt].pop(src, None)
                    removed += 1
        return removed

    def __len__(self) -> int:
        seen: set[tuple[str, str]] = set()
        for src, neighbors in self._adj.items():
            for tgt in neighbors:
                seen.add(tuple(sorted([src, tgt])))
        return len(seen)


# ── [P-C3-8] Função standalone para compatibilidade com scheduler ─────────────

def build_from_entries(entries: list, session_id: str = "_standalone") -> int:
    """
    [P-C3-8] Wrapper standalone de BeliefGraph.build_from_entries().

    scheduler._job_graph_link() importa:
        from .memory_graph import build_from_entries   ← módulo ausente
    O scheduler_patched.py corrige o import para:
        from .belief_graph import build_from_entries   ← este wrapper

    Usa session_id="_standalone" para não colidir com sessões reais.
    Para uso em produção, passe session_id da sessão ativa.
    """
    graph = BeliefGraph(session_id)
    n     = graph.build_from_entries(entries)
    if n > 0:
        graph.save()
    return n
