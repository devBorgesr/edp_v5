"""
memory_graph.py — Grafo leve de relações entre memórias.
"""
import json, time
from pathlib import Path
from typing import Dict, List, Tuple
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

_graph: Dict[str, Dict[str, float]] = {}

def link_memories(memory_id_a: str, memory_id_b: str, score: float) -> None:
    score = round(float(score), 4)
    _graph.setdefault(memory_id_a, {}); _graph.setdefault(memory_id_b, {})
    _graph[memory_id_a][memory_id_b] = max(_graph[memory_id_a].get(memory_id_b, 0.0), score)
    _graph[memory_id_b][memory_id_a] = max(_graph[memory_id_b].get(memory_id_a, 0.0), score)

def get_related(memory_id: str, top_k: int = 10, min_score: float = 0.0) -> List[Tuple[str, float]]:
    neighbors = _graph.get(memory_id, {})
    filtered  = [(nid, s) for nid, s in neighbors.items() if s >= min_score]
    filtered.sort(key=lambda x: x[1], reverse=True)
    return filtered[:top_k]

def get_memory_hubs(top_k: int = 10) -> List[Tuple[str, int, float]]:
    hubs = []
    for nid, neighbors in _graph.items():
        if not neighbors: continue
        grau   = len(neighbors)
        w_mean = sum(neighbors.values()) / grau
        hubs.append((nid, grau, round(grau * w_mean, 4)))
    hubs.sort(key=lambda x: x[2], reverse=True)
    return hubs[:top_k]

def remove_node(memory_id: str) -> bool:
    if memory_id not in _graph: return False
    for nid in list(_graph[memory_id].keys()):
        _graph[nid].pop(memory_id, None)
    del _graph[memory_id]
    return True

def build_from_entries(entries: list, threshold: float = 0.70) -> int:
    if len(entries) < 2: return 0
    embs = np.vstack([np.array(e["embedding"], dtype=np.float32) for e in entries])
    sim_matrix = cosine_similarity(embs)
    n_edges = 0
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            sim = float(sim_matrix[i, j])
            if sim >= threshold:
                link_memories(entries[i]["id"], entries[j]["id"], sim)
                n_edges += 1
    return n_edges

def graph_stats() -> dict:
    n_nodes = len(_graph)
    n_edges = sum(len(v) for v in _graph.values()) // 2
    degrees = [len(v) for v in _graph.values()]
    return {
        "n_nodes": n_nodes, "n_edges": n_edges,
        "avg_degree": round(sum(degrees) / max(n_nodes, 1), 2),
        "max_degree": max(degrees, default=0),
    }

def save_graph(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_graph, f, ensure_ascii=False)

def load_graph(path: str) -> bool:
    global _graph
    p = Path(path)
    if not p.exists(): return False
    with open(path, "r", encoding="utf-8") as f:
        _graph = json.load(f)
    return True

