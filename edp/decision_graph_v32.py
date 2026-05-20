"""
decision_graph_v32.py — EDP v3.2 Async Decision Graph

Rastreia causalidade de decisões cognitivas.
O EDP precisa lembrar: por que decidiu, qual pressão influenciou,
quais eventos causaram degradação, quais ações geraram recovery.

Design:
  DAG (grafo dirigido acíclico) de DecisionEvent.
  Anti-cycle: antes de adicionar aresta, DFS leve verifica ciclo.
  Async: record() é não-bloqueante — usa queue interna processada em thread worker.
  Pruning automático por antiguidade e tamanho.
  obs_pressure(): pressão de observabilidade (queue cheia → pressão alta).

Contrato com orchestrator_v32:
  graph.record(event)          → bool (True=enqueued, False=dropped)
  graph.obs_pressure()         → ObservabilityPressure
  graph.stats()                → dict
  graph.shutdown(timeout)      → None
"""
from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from queue import Empty, Full, Queue
from typing import Callable, DefaultDict, Dict, List, Optional, Set, Tuple

from .types_v32 import DecisionEvent, DecisionEventType


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class AsyncDecisionGraphConfig:
    max_nodes:         int   = 5_000    # nós máximos no grafo
    max_queue_size:    int   = 500      # fila interna
    worker_batch_size: int   = 20       # processa N eventos por iteração
    prune_interval_s:  float = 60.0     # pruning automático a cada N segundos
    max_age_s:         float = 3600.0   # nós mais velhos são prunados
    causal_window:     int   = 10       # máximo de causas por evento


# ── Tipos internos ────────────────────────────────────────────────────────────

@dataclass
class DecisionNode:
    event_id:     str
    event_type:   str
    timestamp:    float
    pressure:     float
    metadata:     Dict[str, str]
    cause_ids:    Tuple[str, ...]     # arestas causais (DAG)
    causal_weight: float = 1.0       # peso causal acumulado


@dataclass(frozen=True)
class ObservabilityPressure:
    queue_fill_ratio: float   # [0,1]
    dropped_events:   int
    processing_lag_ms: float

    def pressure(self) -> float:
        """Pressão composta de observabilidade → consumida pelo PressureMonitor."""
        return min(1.0, self.queue_fill_ratio * 0.7 + min(self.dropped_events / 100, 1.0) * 0.3)


# ── Grafo ─────────────────────────────────────────────────────────────────────

class AsyncDecisionGraph:
    """
    Grafo de causalidade assíncrono.

    record() é O(1) — apenas enfileira.
    Processamento real acontece no worker thread.
    Nunca bloqueia o core loop do orchestrator.
    """

    def __init__(
        self,
        config: Optional[AsyncDecisionGraphConfig] = None,
        event_sink: Optional[Callable[[DecisionEvent], None]] = None,
    ) -> None:
        self._cfg      = config or AsyncDecisionGraphConfig()
        self._sink     = event_sink or (lambda _: None)
        self._lock     = threading.RLock()

        # Grafo: node_id → DecisionNode
        self._nodes:    Dict[str, DecisionNode] = {}
        # Adjacência reversa: causa → efeitos (para DFS anti-cycle)
        self._effects:  DefaultDict[str, Set[str]] = defaultdict(set)
        # Últimos N event_ids (para construir links causais automáticos)
        self._recent:   deque = deque(maxlen=self._cfg.causal_window)

        # Fila assíncrona
        self._queue:    Queue = Queue(maxsize=self._cfg.max_queue_size)
        self._dropped:  int   = 0
        self._processed: int  = 0
        self._process_lag_ms: float = 0.0

        # Métricas de causalidade
        self._total_cycles:   int = 0
        self._total_pruned:   int = 0

        # Worker
        self._running = True
        self._worker  = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

        # Pruning periódico
        self._last_prune = time.monotonic()

    # ── API pública ────────────────────────────────────────────────────────────

    def record(self, event: DecisionEvent) -> bool:
        """
        Enfileira evento para processamento assíncrono.
        Não-bloqueante: retorna False se fila cheia (backpressure).
        O caller nunca deve esperar aqui.
        """
        try:
            self._queue.put_nowait(event)
            return True
        except Full:
            self._dropped += 1
            return False

    def obs_pressure(self) -> ObservabilityPressure:
        """Pressão de observabilidade — chamada pelo orchestrator após record()."""
        qsize   = self._queue.qsize()
        qmax    = self._cfg.max_queue_size
        fill    = qsize / max(qmax, 1)
        return ObservabilityPressure(
            queue_fill_ratio=round(fill, 4),
            dropped_events=self._dropped,
            processing_lag_ms=round(self._process_lag_ms, 2),
        )

    def stats(self) -> dict:
        with self._lock:
            n_nodes  = len(self._nodes)
            n_edges  = sum(len(n.cause_ids) for n in self._nodes.values())
            # Top causas por peso causal
            dominant = sorted(
                self._nodes.values(),
                key=lambda n: n.causal_weight,
                reverse=True
            )[:5]
        return {
            "n_nodes":   n_nodes,
            "n_edges":   n_edges,
            "dropped":   self._dropped,
            "processed": self._processed,
            "lag_ms":    round(self._process_lag_ms, 2),
            "dominant_causes": [
                {"id": n.event_id, "type": n.event_type, "weight": round(n.causal_weight, 3)}
                for n in dominant
            ],
        }

    def get_causal_path(self, event_id: str, max_depth: int = 5) -> List[str]:
        """Retorna caminho causal de um evento (DFS regressivo)."""
        with self._lock:
            path: List[str] = []
            visited: Set[str] = set()
            self._dfs_causes(event_id, path, visited, max_depth)
            return path

    def shutdown(self, timeout: float = 5.0) -> None:
        """Drain da fila e encerra worker."""
        self._running = False
        # Envia sentinel
        try:
            self._queue.put_nowait(None)
        except Full:
            pass
        self._worker.join(timeout=timeout)

    def health_report(self) -> dict:
        return {
            "n_nodes":     len(self._nodes),
            "queue_fill":  round(self._queue.qsize() / max(self._cfg.max_queue_size, 1), 3),
            "dropped":     self._dropped,
            "processed":   self._processed,
        }

    def metrics(self) -> dict:
        return self.health_report()

    # ── Worker ─────────────────────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        while self._running:
            batch = []
            try:
                # Bloqueia até ter ao menos 1 item ou timeout
                item = self._queue.get(timeout=0.5)
                if item is None:
                    break
                batch.append(item)
                # Drena o restante do batch sem bloquear
                for _ in range(self._cfg.worker_batch_size - 1):
                    try:
                        it = self._queue.get_nowait()
                        if it is None:
                            self._running = False
                            break
                        batch.append(it)
                    except Empty:
                        break
            except Empty:
                pass

            for event in batch:
                t0 = time.perf_counter()
                self._process_event(event)
                lag = (time.perf_counter() - t0) * 1000
                self._process_lag_ms = 0.8 * self._process_lag_ms + 0.2 * lag
                self._processed += 1

            # Pruning periódico
            now = time.monotonic()
            if now - self._last_prune > self._cfg.prune_interval_s:
                self._prune()
                self._last_prune = now

    def _process_event(self, event: DecisionEvent) -> None:
        """Adiciona evento ao grafo com links causais automáticos."""
        eid = event.event_id
        pressure = getattr(event, 'pressure_before', 0.0) or 0.0

        # Causa: eventos recentes compatíveis
        with self._lock:
            recent_ids = list(self._recent)

        # Filtra causes: pega os mais recentes que existem no grafo
        causes: List[str] = []
        with self._lock:
            for candidate in reversed(recent_ids):
                if candidate in self._nodes and len(causes) < 3:
                    # Anti-cycle: verifica que candidate não é descendente de eid
                    if not self._would_create_cycle(candidate, eid):
                        causes.append(candidate)

        meta: Dict[str, str] = {}
        for k, v in (event.metadata or ()):
            meta[k] = str(v)

        node = DecisionNode(
            event_id=eid,
            event_type=event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type),
            timestamp=event.timestamp,
            pressure=pressure,
            metadata=meta,
            cause_ids=tuple(causes),
            causal_weight=1.0 + pressure,
        )

        with self._lock:
            # Overflow: remove o mais antigo
            if len(self._nodes) >= self._cfg.max_nodes:
                oldest_id = min(self._nodes, key=lambda k: self._nodes[k].timestamp)
                self._remove_node(oldest_id)

            self._nodes[eid] = node
            for cause_id in causes:
                self._effects[cause_id].add(eid)
                # Propaga peso causal
                if cause_id in self._nodes:
                    self._nodes[cause_id].causal_weight += 0.1

            self._recent.append(eid)
            self._total_cycles += 1

    def _would_create_cycle(self, from_id: str, to_id: str) -> bool:
        """DFS leve para detectar ciclo. Chamado dentro de _lock."""
        if from_id == to_id:
            return True
        visited: Set[str] = set()
        stack = [from_id]
        while stack:
            curr = stack.pop()
            if curr in visited:
                continue
            visited.add(curr)
            for effect in self._effects.get(curr, set()):
                if effect == to_id:
                    return True
                if effect not in visited:
                    stack.append(effect)
                if len(visited) > 100:  # limite de busca
                    return False
        return False

    def _remove_node(self, node_id: str) -> None:
        """Remove nó e suas arestas. Chamado dentro de _lock."""
        self._nodes.pop(node_id, None)
        self._effects.pop(node_id, None)
        for effects_set in self._effects.values():
            effects_set.discard(node_id)
        self._total_pruned += 1

    def _prune(self) -> None:
        """Remove nós antigos além do max_age."""
        now = time.time()
        with self._lock:
            to_remove = [
                nid for nid, n in self._nodes.items()
                if (now - n.timestamp) > self._cfg.max_age_s
            ]
            for nid in to_remove:
                self._remove_node(nid)

    def _dfs_causes(self, node_id: str, path: List[str],
                     visited: Set[str], depth: int) -> None:
        if depth <= 0 or node_id in visited:
            return
        visited.add(node_id)
        node = self._nodes.get(node_id)
        if node:
            path.append(node_id)
            for cause_id in node.cause_ids:
                self._dfs_causes(cause_id, path, visited, depth - 1)
