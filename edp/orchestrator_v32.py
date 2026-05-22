"""
FRACTAL EDP v3.2 — ORCHESTRATOR V32

Replaces orchestrator.py (v3.1).
Wires ALL v3.2 subsystems into a single coherent execution loop.

ARCHITECTURAL DECISIONS (documented):

1. SINGLE WRITE LOCK STRATEGY:
   The orchestrator holds no global lock itself.
   Each subsystem owns its own lock. The orchestrator calls subsystems
   in a defined order to avoid deadlock:
   ORDER: economy → snapshot → pressure → storm_guard → meta → biodiversity → graph
   No two subsystems ever hold each other's lock simultaneously.

2. RACE CONDITION IN v3.1 FIXED:
   v3.1 had: event = memory.ingest(node) then event.pressure_before = X
   This mutated a DecisionEvent after creation — race with graph worker.
   v3.2: all fields are set before the event is emitted. DecisionEvent is frozen.

3. EMBEDDING CACHE PLACEMENT:
   Cache is checked BEFORE snapshot write. If cache miss and no embedding_fn,
   node is stored without embedding (valid — scored by reinforcement/freshness only).

4. FOSSILIZATION CYCLE:
   Runs on snapshot directly (reads current generation, writes updated CoW copies).
   Uses begin_write() — atomic, no partial updates.

5. PRESSURE MONITOR AGGREGATION:
   Runs after every subsystem operation. Feeds MetaStabilityController.
   MetaStabilityController output (OperationalParams) gates the NEXT operation.
   This creates a feedback loop with one-tick lag — intentional, prevents oscillation.

6. BIODIVERSITY:
   Runs every N ticks (configurable). Reads snapshot, writes CoW updates.
   Results feed entropy_pressure dimension of PressureMonitor.

7. GRAPH RECORDING:
   AsyncDecisionGraph.record() is non-blocking (returns bool).
   If queue full (backpressure): graph pressure dimension is updated.
   Core operations never block on graph.

FAILURE MODES (documented):
  - Economy FROZEN + all ops critical: system degrades to minimal retrieval only.
  - Snapshot conflict: PendingWrite raises SnapshotConflict → caller retries.
  - Pressure saturation: handled per on_saturation_mode config.
  - Graph queue full: drops event, increments graph_pressure. Never blocks.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .types_v32 import (
    AbstractionLevel,
    CognitiveTick,
    DecisionEvent,
    DecisionEventType,
    DEFAULT_SCORING_POLICY,
    EconomyBudgetExceeded,
    ImmutableCognitiveNode,
    PressureSaturationError,
    RecursionBudget,
    RetrievalResult,
    ScoringPolicy,
    SnapshotConflict,
    StormDetected,
)
from .snapshot_manager import CognitiveSnapshotManager
from .decision_graph_v32 import AsyncDecisionGraph, AsyncDecisionGraphConfig
from .storm_guard import RetrievalStormGuard, StormGuardConfig, StormMetrics
from .economy import CognitiveEconomyEngine, EconomyConfig, ThrottleMode
from .meta_stability import (
    MetaStabilityController, ControllerConfig,
    StabilityMode, StabilitySignals, OperationalParams, ConsolidationMode,
)
from .biodiversity import SemanticBiodiversityEngine, BiodiversityConfig, BiodiversityReport
from .pressure_regulator import FractalPressureRegulator, PressureConfig
from .pressure_monitor import PressureMonitor, PressureMonitorConfig, PressureDimension
from .embedding_cache import EmbeddingCache, EmbeddingCacheConfig
from .vector_store import VectorStoreProtocol, InMemoryVectorStore
from .clock import now as _now  # Peça 0.2b — relógio interno robusto


# ==================================================
# CONFIGURATION
# ==================================================

@dataclass
class OrchestratorV32Config:
    # Sub-system configs
    snapshot_history: int = 10
    graph: AsyncDecisionGraphConfig = field(default_factory=AsyncDecisionGraphConfig)
    storm_guard: StormGuardConfig = field(default_factory=StormGuardConfig)
    economy: EconomyConfig = field(default_factory=EconomyConfig)
    controller: ControllerConfig = field(default_factory=ControllerConfig)
    biodiversity: BiodiversityConfig = field(default_factory=BiodiversityConfig)
    pressure_reg: PressureConfig = field(default_factory=PressureConfig)
    pressure_mon: PressureMonitorConfig = field(default_factory=PressureMonitorConfig)
    embedding_cache: EmbeddingCacheConfig = field(default_factory=EmbeddingCacheConfig)
    scoring_policy: ScoringPolicy = field(default_factory=lambda: DEFAULT_SCORING_POLICY)

    # Operational
    fossilization_cycle_ticks: int = 500
    biodiversity_cycle_ticks: int = 200
    on_saturation: str = "warn"   # "warn" | "drain" | "raise"

    # Snapshot conflict: max retries before giving up on a write
    snapshot_max_retries: int = 3


# ==================================================
# HEALTH SNAPSHOT
# ==================================================

@dataclass(frozen=True)
class SystemHealthV32:
    timestamp: float
    stability_mode: str
    composite_pressure: float
    pressure_dimensions: Dict[str, float]
    snapshot_generation: int
    snapshot_node_count: int
    graph_stats: Dict
    storm_metrics_score: float
    economy_mode: str
    cache_hit_rate: float
    cache_instability: float
    tick_count: int
    warnings: Tuple[str, ...]
    biodiversity_last: Optional[BiodiversityReport]


# ==================================================
# ORCHESTRATOR V32
# ==================================================

class OrchestratorV32:
    """
    Central coordinator for all v3.2 subsystems.

    Public API:
        ingest(node)          → DecisionEvent
        retrieve(embedding, k) → List[RetrievalResult]
        health()               → SystemHealthV32
        shutdown()             → None
    """

    def __init__(
        self,
        config: Optional[OrchestratorV32Config] = None,
        embedding_fn: Optional[Callable[[str], Tuple[float, ...]]] = None,
        event_listeners: Optional[List[Callable[[DecisionEvent], None]]] = None,
    ) -> None:
        self._cfg = config or OrchestratorV32Config()
        self._tick_count = 0
        self._last_bio_report: Optional[BiodiversityReport] = None

        # Event fan-out
        listeners = list(event_listeners or [])
        def _dispatch(event: DecisionEvent) -> None:
            for fn in listeners:
                try:
                    fn(event)
                except Exception:
                    pass
        self._dispatch = _dispatch

        # Subsystems — init order matters for dependency clarity
        self._snap = CognitiveSnapshotManager(
            max_history_size=self._cfg.snapshot_history,
            event_sink=_dispatch,
        )
        self._graph = AsyncDecisionGraph(
            config=self._cfg.graph,
            event_sink=_dispatch,
        )
        self._storm = RetrievalStormGuard(
            config=self._cfg.storm_guard,
            event_sink=_dispatch,
        )
        self._economy = CognitiveEconomyEngine(
            config=self._cfg.economy,
            event_sink=_dispatch,
        )
        self._meta = MetaStabilityController(
            config=self._cfg.controller,
            event_sink=_dispatch,
        )
        self._bio = SemanticBiodiversityEngine(
            config=self._cfg.biodiversity,
            event_sink=_dispatch,
        )
        self._pressure_reg = FractalPressureRegulator(
            config=self._cfg.pressure_reg,
            event_sink=_dispatch,
        )
        self._pressure_mon = PressureMonitor(config=self._cfg.pressure_mon)
        self._emb_cache = EmbeddingCache(
            config=self._cfg.embedding_cache,
            embedding_fn=embedding_fn,
        )

        self._policy = self._cfg.scoring_policy

    # --------------------------------------------------
    # INGEST
    # --------------------------------------------------

    def ingest(
        self,
        node: ImmutableCognitiveNode,
        pressure_delta: float = 0.05,
        is_critical: bool = False,
    ) -> Optional[DecisionEvent]:
        """
        Ingest a new node into the cognitive system.

        Steps (ordered to avoid deadlock):
          1. Economy gate
          2. Embedding cache check
          3. Snapshot write (with retry on conflict)
          4. Pressure absorption
          5. Economy + monitor update
          6. MetaStability update
          7. Periodic cycles (bio, fossilization)
          8. Async graph record

        Returns DecisionEvent or None if economy rejected.
        """
        self._tick_count += 1

        # 1. Economy gate — fast path rejection
        try:
            self._economy.request_retrieval(levels=1, is_critical=is_critical)
        except EconomyBudgetExceeded:
            return None

        # 2. Embedding cache — enrich node if cache has it
        if node.embedding is None and node.text:
            cached_emb = self._emb_cache.get(node.text)
            if cached_emb is not None:
                node = node._clone(embedding=cached_emb)
        elif node.embedding is not None and node.text:
            # Store in cache for future lookups
            self._emb_cache.put(node.text, node.embedding)

        # 3. Snapshot write with conflict retry
        written = False
        for attempt in range(self._cfg.snapshot_max_retries):
            try:
                with self._snap.begin_write() as pw:
                    pw.put(node)
                written = True
                break
            except SnapshotConflict:
                if attempt == self._cfg.snapshot_max_retries - 1:
                    # Give up — not fatal, just log via graph
                    pass

        # 4. Pressure absorption
        tick = CognitiveTick(
            pressure_delta=pressure_delta,
            semantic_load=node.semantic_entropy,
            affected_node_ids=(node.id,),
            source_operation="ingest",
        )
        global_pressure = self._absorb_pressure(tick)

        # 5. Economy + monitor update
        self._economy.update_pressure(global_pressure)
        self._update_pressure_monitor()

        # 6. MetaStability update — one-tick lag is intentional
        params = self._meta.update(self._build_signals(global_pressure))

        # React to emergency mode
        if params.stability_mode == StabilityMode.EMERGENCY:
            self._handle_emergency(params)

        # 7. Periodic cycles
        if self._tick_count % self._cfg.biodiversity_cycle_ticks == 0:
            self._run_biodiversity()
        if self._tick_count % self._cfg.fossilization_cycle_ticks == 0:
            self._run_fossilization()

        # 8. Async graph record (non-blocking — never blocks ingest)
        # Build event with all fields set BEFORE emitting (fixes v3.1 race)
        event = DecisionEvent(
            event_type=DecisionEventType.RETRIEVAL,
            affected_nodes=(node.id,),
            pressure_before=global_pressure,
            pressure_after=global_pressure,
            abstraction_level=node.abstraction_level,
            metadata=(
                ("operation", "ingest"),
                ("written", str(written)),
                ("tick", str(self._tick_count)),
            ),
        )
        accepted = self._graph.record(event)
        if not accepted:
            # Graph backpressure — update graph pressure dimension
            self._pressure_mon.update_graph(
                self._graph.obs_pressure().pressure()
            )

        self._dispatch(event)
        return event

    # --------------------------------------------------
    # RETRIEVE
    # --------------------------------------------------

    def retrieve(
        self,
        query_embedding: Tuple[float, ...],
        k: int = 5,
        is_critical: bool = False,
    ) -> List[RetrievalResult]:
        """
        Retrieve top-k nodes from current snapshot.

        Steps:
          1. Economy gate
          2. Storm guard: effective_k (may reduce k under storm)
          3. Snapshot read (lock-free)
          4. Score and rank
          5. Storm guard: record results
          6. Pressure tick
          7. Graph record
        """
        params = self._meta.current_params()

        # 1. Economy gate
        try:
            self._economy.request_retrieval(
                levels=params.retrieval_depth, is_critical=is_critical
            )
        except EconomyBudgetExceeded:
            return []

        # 2. Storm guard: may reduce k
        effective_k = self._storm.effective_k(min(k, params.retrieval_breadth))

        # 3. Lock-free snapshot read
        gen = self._snap.snapshot()
        level_filter: Optional[AbstractionLevel] = None
        # Under DEGRADED/EMERGENCY: only search abstractions (faster, less pressure)
        if params.stability_mode in (StabilityMode.DEGRADED, StabilityMode.EMERGENCY):
            level_filter = AbstractionLevel.ABSTRACTION

        top_nodes = gen.top_k_by_score(effective_k, self._policy, level=level_filter)

        # 4. Build results — only nodes with embeddings get similarity score
        results: List[RetrievalResult] = []
        for node in top_nodes:
            # Embedding similarity if both have embeddings
            sim = self._cosine_sim(query_embedding, node.embedding) if node.embedding else 0.5
            combined = 0.6 * sim + 0.4 * node.effective_score(self._policy)
            results.append(RetrievalResult(
                node=node,
                score=combined,
                retrieval_depth=node.abstraction_level,
                retrieval_path=(node.abstraction_level.name,),
                cost=self._cfg.economy.costs.retrieval_base,
            ))

        results.sort(key=lambda r: r.score, reverse=True)

        # 5. Storm guard: record results
        try:
            self._storm.record(results)
        except StormDetected as e:
            self._pressure_mon.update_retrieval(e.score)

        # 6. Pressure tick
        tick = CognitiveTick(
            pressure_delta=0.02,
            retrieval_cost=0.15,
            source_operation="retrieve",
        )
        global_pressure = self._absorb_pressure(tick)
        self._economy.update_pressure(global_pressure)
        self._update_pressure_monitor()
        self._meta.update(self._build_signals(global_pressure))

        # 7. Graph record (non-blocking, fields all set before emit)
        event = DecisionEvent(
            event_type=DecisionEventType.RETRIEVAL,
            affected_nodes=tuple(r.node.id for r in results),
            retrieval_lineage=tuple(r.retrieval_depth.name for r in results),
            pressure_before=global_pressure,
            metadata=(
                ("k_requested", str(k)),
                ("k_effective", str(effective_k)),
                ("results", str(len(results))),
            ),
        )
        self._graph.record(event)
        return results

    # --------------------------------------------------
    # HEALTH
    # --------------------------------------------------

    def health(self) -> SystemHealthV32:
        surface = self._pressure_mon.read()
        gen = self._snap.snapshot()
        graph_stats = self._graph.stats()
        cache_m = self._emb_cache.metrics()
        storm_m = self._storm.current_metrics()
        meta_summary = self._meta.signal_history_summary()
        eco_snap = self._economy.snapshot()

        warnings: List[str] = []
        if surface.is_any_critical():
            warnings.append(f"CRITICAL_PRESSURE:{','.join(surface.critical_dimensions)}")
        if storm_m.is_storm():
            warnings.append(f"RETRIEVAL_STORM:score={storm_m.composite_storm_score:.2f}")
        if self._meta.current_mode() == StabilityMode.EMERGENCY:
            warnings.append("EMERGENCY_MODE_ACTIVE")
        if cache_m.instability_score() > 0.7:
            warnings.append(f"CACHE_UNSTABLE:hit_rate={cache_m.hit_rate():.2f}")
        if graph_stats.get("dropped", 0) > 100:
            warnings.append(f"GRAPH_DROPPING_EVENTS:{graph_stats['dropped']}")

        return SystemHealthV32(
            timestamp=_now(),
            stability_mode=meta_summary.get("current_mode", "unknown"),
            composite_pressure=surface.composite,
            pressure_dimensions=dict(surface.dimensions),
            snapshot_generation=gen.generation_id,
            snapshot_node_count=gen.count(),
            graph_stats=graph_stats,
            storm_metrics_score=storm_m.composite_storm_score,
            economy_mode=eco_snap.get("mode", "unknown"),
            cache_hit_rate=cache_m.hit_rate(),
            cache_instability=cache_m.instability_score(),
            tick_count=self._tick_count,
            warnings=tuple(warnings),
            biodiversity_last=self._last_bio_report,
        )

    def add_event_listener(self, fn: Callable[[DecisionEvent], None]) -> None:
        orig = self._dispatch
        def new_dispatch(event: DecisionEvent) -> None:
            orig(event)
            try:
                fn(event)
            except Exception:
                pass
        self._dispatch = new_dispatch

    def shutdown(self, timeout: float = 5.0) -> None:
        self._graph.shutdown(timeout=timeout)

    # --------------------------------------------------
    # INTERNAL
    # --------------------------------------------------

    def _absorb_pressure(self, tick: CognitiveTick) -> float:
        try:
            snap = self._pressure_reg.absorb_tick(tick)
            return snap.get("global", 0.0)
        except PressureSaturationError:
            mode = self._cfg.on_saturation
            if mode == "drain":
                self._pressure_reg.reset_local()
            elif mode == "raise":
                raise
            return 1.0

    def _update_pressure_monitor(self) -> None:
        """Aggregate all subsystem signals into PressureMonitor."""
        gen = self._snap.snapshot()

        # Eviction pressure: how close to snapshot capacity
        # (proxy: generation churn rate — high churn = high eviction pressure)
        history = self._snap.history_summary()
        if len(history) >= 2:
            last_diff_info = history[-1]
            # Use node_count relative to expected max as proxy
            eviction_proxy = min(gen.count() / max(self._cfg.embedding_cache.max_entries, 1), 1.0)
            self._pressure_mon.update_eviction(eviction_proxy)

        # Entropy pressure: from biodiversity last report
        if self._last_bio_report:
            mrd = self._last_bio_report.mean_radial_distance
            entropy_pressure = max(0.0, 1.0 - min(mrd / 0.5, 1.0))
            self._pressure_mon.update_entropy(entropy_pressure)

        # Retrieval pressure: from storm guard
        storm_m = self._storm.current_metrics()
        self._pressure_mon.update_retrieval(storm_m.composite_storm_score)

        # Embedding pressure: from cache instability
        cache_m = self._emb_cache.metrics()
        self._pressure_mon.update_embedding(cache_m.instability_score())

        # Graph pressure: from decision graph
        obs = self._graph.obs_pressure()
        self._pressure_mon.update_graph(obs.pressure())

    def _build_signals(self, global_pressure: float) -> StabilitySignals:
        surface = self._pressure_mon.read()
        storm_m = self._storm.current_metrics()
        cache_m = self._emb_cache.metrics()

        entropy_dim = surface.get(PressureDimension.ENTROPY)
        # entropy_drift: negative = collapsing, positive = expanding
        entropy_drift = -entropy_dim if entropy_dim > 0.5 else entropy_dim * 0.1

        return StabilitySignals(
            entropy_drift=entropy_drift,
            global_pressure=global_pressure,
            storm_score=storm_m.composite_storm_score,
            cache_instability=cache_m.instability_score(),
            abstraction_entropy=storm_m.abstraction_entropy,
            obs_pressure=surface.get(PressureDimension.GRAPH),
        )

    def _handle_emergency(self, params: OperationalParams) -> None:
        """React to EMERGENCY mode: shrink cache, reset storm guard."""
        self._emb_cache.on_pressure_high()
        # Storm guard reset clears accumulated oscillation state
        self._storm.reset()

    def _run_biodiversity(self) -> None:
        gen = self._snap.snapshot()
        nodes = gen.all_nodes()
        if not nodes:
            return
        report, updated = self._bio.measure_and_rebalance(nodes, self._policy)
        self._last_bio_report = report
        if updated:
            for attempt in range(self._cfg.snapshot_max_retries):
                try:
                    with self._snap.begin_write() as pw:
                        for n in updated:
                            pw.put(n)
                    break
                except SnapshotConflict:
                    pass

    def _run_fossilization(self) -> None:
        """
        Apply freshness decay and anti-dominance to all nodes.
        Uses AntiFossilizationEngine from v3.1 adapted for immutable nodes.
        """
        gen = self._snap.snapshot()
        nodes = gen.all_nodes()
        if not nodes:
            return

        import math
        now = _now()
        half_lives = {
            AbstractionLevel.EPISODIC:        3_600.0,
            AbstractionLevel.EVENT:          86_400.0,
            AbstractionLevel.SEMANTIC_CLUSTER: 259_200.0,
            AbstractionLevel.ABSTRACTION:    604_800.0,
            AbstractionLevel.META_PATTERN:  2_592_000.0,
            AbstractionLevel.MACRO_STATE:  10_368_000.0,
        }
        params = self._meta.current_params()
        decay_modifier = params.decay_rate_modifier

        updated: List[ImmutableCognitiveNode] = []
        for node in nodes:
            hl = half_lives.get(node.abstraction_level, 86_400.0)
            age = now - node.timestamp
            new_freshness = math.exp(-math.log(2) * age / hl * decay_modifier)
            new_freshness = max(new_freshness, 1e-4)
            if abs(new_freshness - node.freshness) > 1e-6:
                updated.append(node.with_freshness(new_freshness))

        if updated:
            for attempt in range(self._cfg.snapshot_max_retries):
                try:
                    with self._snap.begin_write() as pw:
                        for n in updated:
                            pw.put(n)
                    break
                except SnapshotConflict:
                    pass

    @staticmethod
    def _cosine_sim(
        a: Tuple[float, ...],
        b: Optional[Tuple[float, ...]],
    ) -> float:
        """O(dim). Returns cosine similarity in [0,1], negative clamped to 0."""
        if b is None or len(a) != len(b):
            return 0.5
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x**2 for x in a) ** 0.5
        nb = sum(x**2 for x in b) ** 0.5
        if na < 1e-8 or nb < 1e-8:
            return 0.0
        return max(0.0, dot / (na * nb))