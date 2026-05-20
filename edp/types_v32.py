"""
FRACTAL EDP v3.2 — CORE TYPE CONTRACTS (REVISED)

P0 CHANGES vs v3.1:
- CognitiveNode is now IMMUTABLE (frozen=True equivalent via __slots__ + properties)
  Mutation always returns a new instance. No shared mutable state.
- ScoringPolicy: effective_score() is now a versioned, explicit contract —
  not an implicit method hidden inside the node.
- CognitiveTick: total_pressure() weights are documented mathematically.
- DecisionEventType: added new types for v3.2 subsystems.
- New exceptions: SnapshotConflict, StormDetected, EconomyBudgetExceeded.
- SemanticCluster: cooldown_jitter field added (anti-synchronization).

IMMUTABILITY DESIGN:
  Python frozen dataclasses don't support mutable defaults (list, dict).
  We use a custom ImmutableNode backed by tuples for sequences.
  All "mutation" is copy-on-write: node.with_incremented_retrieval() → new node.
  Callers never hold a reference they can mutate under another thread.
"""

from __future__ import annotations

import hashlib
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, List, Optional, Tuple


# ==================================================
# ENUMERATIONS
# ==================================================

class AbstractionLevel(int, Enum):
    EPISODIC         = 0
    EVENT            = 1
    SEMANTIC_CLUSTER = 2
    ABSTRACTION      = 3
    META_PATTERN     = 4
    MACRO_STATE      = 5


class PressureZone(Enum):
    LOCAL   = "local"
    CLUSTER = "cluster"
    GLOBAL  = "global"


class ConsolidationReason(Enum):
    PRESSURE_THRESHOLD  = "pressure_threshold"
    SIZE_THRESHOLD      = "size_threshold"
    SCHEDULED           = "scheduled"
    MANUAL              = "manual"
    ANTI_FOSSILIZATION  = "anti_fossilization"


class DecisionEventType(Enum):
    RETRIEVAL            = "retrieval"
    CONSOLIDATION        = "consolidation"
    SUMMARIZATION        = "summarization"
    PRESSURE_ESCALATION  = "pressure_escalation"
    ABSTRACTION_PROMOTE  = "abstraction_promote"
    MEMORY_MIGRATE       = "memory_migrate"
    DECAY_APPLIED        = "decay_applied"
    NOVELTY_REBALANCE    = "novelty_rebalance"
    BUDGET_EXCEEDED      = "budget_exceeded"
    ANTI_FOSSILIZATION   = "anti_fossilization"
    # v3.2 additions
    SNAPSHOT_PROMOTED    = "snapshot_promoted"
    SNAPSHOT_ROLLED_BACK = "snapshot_rolled_back"
    STORM_DETECTED       = "storm_detected"
    STORM_ABORTED        = "storm_aborted"
    ECONOMY_THROTTLED    = "economy_throttled"
    META_STABILITY_ACT   = "meta_stability_act"
    BIODIVERSITY_ALERT   = "biodiversity_alert"
    GRAPH_COMPACTED      = "graph_compacted"


class ScoringPolicyVersion(Enum):
    """
    Versioned scoring formula. Changing formula = new version.
    Subsystems declare which version they expect.
    Mismatch → explicit error, not silent behavior change.
    """
    V1_MULTIPLICATIVE = "v1_multiplicative"   # v3.1 formula
    V2_WEIGHTED_LOG   = "v2_weighted_log"     # v3.2 formula (see ScoringPolicy)


# ==================================================
# EXCEPTIONS — imported from canonical source
# ==================================================

from .exceptions import (
    EDPException,
    RecursiveBudgetExceeded,
    RetrievalBudgetExceeded,
    EconomyBudgetExceeded,
    SnapshotConflict,
    ConsolidationCascadeError,
    PressureSaturationError,
    StormDetected,
)


# ==================================================
# SCORING POLICY# ==================================================
# SCORING POLICY — FORMAL CONTRACT
# ==================================================

@dataclass(frozen=True)
class ScoringPolicy:
    """
    Formal, versioned, documented contract for node scoring.

    VERSION V1_MULTIPLICATIVE (v3.1):
        score = reinforcement * freshness * novelty * (1 - pressure*0.3)
        Problem: multiplicative → any factor near zero collapses entire score.
        Ghost nodes with freshness=0.001 score near-zero but still compete.

    VERSION V2_WEIGHTED_LOG (v3.2):
        score = w_r*log1p(reinforcement) + w_f*freshness + w_n*novelty - w_p*pressure
        Normalized to [0,1] via sigmoid.

        Mathematical properties:
        - Additive → single near-zero factor doesn't collapse score
        - log1p(reinforcement) → bounded growth, no runaway reinforcement dominance
        - pressure penalty is subtractive → predictable linear effect
        - Sigmoid normalization → stable output range regardless of input scale

        Weights (empirically chosen, documented):
        w_r = 0.35  (reinforcement: earned signal, highest weight)
        w_f = 0.30  (freshness: temporal relevance)
        w_n = 0.25  (novelty: diversity preservation)
        w_p = 0.10  (pressure penalty: load-aware deprioritization)

        These weights sum to 1.0 for the positive terms.
        They were chosen to balance stability (freshness) vs discovery (novelty).
        Changing them requires bumping version to V3.
    """
    version: ScoringPolicyVersion = ScoringPolicyVersion.V2_WEIGHTED_LOG

    # V2 weights — do not change without version bump
    W_REINFORCEMENT: float = 0.35
    W_FRESHNESS: float = 0.30
    W_NOVELTY: float = 0.25
    W_PRESSURE: float = 0.10

    def score(
        self,
        reinforcement: float,
        freshness: float,
        novelty: float,
        pressure: float,
    ) -> float:
        """
        Compute effective score. All inputs expected in [0, ∞) for reinforcement,
        [0,1] for others. Returns score in [0,1].
        """
        if self.version == ScoringPolicyVersion.V2_WEIGHTED_LOG:
            raw = (
                self.W_REINFORCEMENT * math.log1p(max(reinforcement, 0.0))
                + self.W_FRESHNESS   * max(min(freshness, 1.0), 0.0)
                + self.W_NOVELTY     * max(min(novelty, 1.0), 0.0)
                - self.W_PRESSURE    * max(min(pressure, 1.0), 0.0)
            )
            # Sigmoid to [0,1]. Center at 0.5 (raw≈0.5 → score≈0.62 for mid values)
            return 1.0 / (1.0 + math.exp(-raw * 3.0))

        elif self.version == ScoringPolicyVersion.V1_MULTIPLICATIVE:
            # Legacy — kept for rollback testing only
            return (
                reinforcement
                * freshness
                * novelty
                * (1.0 - min(pressure, 1.0) * 0.3)
            )
        else:
            raise ValueError(f"Unknown ScoringPolicyVersion: {self.version}")

    def assert_version(self, expected: ScoringPolicyVersion) -> None:
        if self.version != expected:
            raise ValueError(
                f"ScoringPolicy version mismatch: expected {expected.value}, "
                f"got {self.version.value}. Update caller or pin version explicitly."
            )


# Singleton default policy — subsystems import this
DEFAULT_SCORING_POLICY = ScoringPolicy(version=ScoringPolicyVersion.V2_WEIGHTED_LOG)


# ==================================================
# IMMUTABLE COGNITIVE NODE
# ==================================================

class ImmutableCognitiveNode:
    """
    Immutable semantic unit. All fields are read-only after construction.
    Mutation is copy-on-write: every "update" returns a new instance.

    Design rationale:
    - No shared mutable state between subsystems (race condition eliminated)
    - Snapshot consistency: a reference to a node is forever valid
    - CognitiveSnapshotManager can safely hold node references across generations
    - Scoring uses injected ScoringPolicy (no hardcoded formula)

    Sequence fields (parent_ids, child_ids, causal_links) stored as tuples.
    Embedding stored as tuple (immutable, hashable).
    """

    __slots__ = (
        '_id', '_text', '_embedding',
        '_timestamp', '_last_accessed', '_last_modified',
        '_source',
        '_semantic_entropy', '_novelty_score', '_reinforcement_score',
        '_retrieval_count', '_consolidation_count',
        '_parent_ids', '_child_ids', '_abstraction_level',
        '_pressure_score', '_freshness',
        '_causal_links',
        '_is_archived', '_cluster_id', '_checksum',
        '_generation',  # snapshot generation stamp
    )

    def __init__(
        self,
        id: Optional[str] = None,
        text: str = "",
        embedding: Optional[Tuple[float, ...]] = None,
        timestamp: Optional[float] = None,
        last_accessed: Optional[float] = None,
        last_modified: Optional[float] = None,
        source: str = "unknown",
        semantic_entropy: float = 0.0,
        novelty_score: float = 1.0,
        reinforcement_score: float = 1.0,
        retrieval_count: int = 0,
        consolidation_count: int = 0,
        parent_ids: Tuple[str, ...] = (),
        child_ids: Tuple[str, ...] = (),
        abstraction_level: AbstractionLevel = AbstractionLevel.EPISODIC,
        pressure_score: float = 0.0,
        freshness: float = 1.0,
        causal_links: Tuple[str, ...] = (),
        is_archived: bool = False,
        cluster_id: Optional[str] = None,
        checksum: Optional[str] = None,
        generation: int = 0,
    ) -> None:
        now = time.time()
        object.__setattr__(self, '_id', id or str(uuid.uuid4()))
        object.__setattr__(self, '_text', text)
        object.__setattr__(self, '_embedding', embedding)
        object.__setattr__(self, '_timestamp', timestamp or now)
        object.__setattr__(self, '_last_accessed', last_accessed or now)
        object.__setattr__(self, '_last_modified', last_modified or now)
        object.__setattr__(self, '_source', source)
        object.__setattr__(self, '_semantic_entropy', semantic_entropy)
        object.__setattr__(self, '_novelty_score', novelty_score)
        object.__setattr__(self, '_reinforcement_score', reinforcement_score)
        object.__setattr__(self, '_retrieval_count', retrieval_count)
        object.__setattr__(self, '_consolidation_count', consolidation_count)
        object.__setattr__(self, '_parent_ids', tuple(parent_ids))
        object.__setattr__(self, '_child_ids', tuple(child_ids))
        object.__setattr__(self, '_abstraction_level', abstraction_level)
        object.__setattr__(self, '_pressure_score', pressure_score)
        object.__setattr__(self, '_freshness', freshness)
        object.__setattr__(self, '_causal_links', tuple(causal_links))
        object.__setattr__(self, '_is_archived', is_archived)
        object.__setattr__(self, '_cluster_id', cluster_id)
        object.__setattr__(self, '_checksum', checksum)
        object.__setattr__(self, '_generation', generation)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(
            f"ImmutableCognitiveNode is read-only. Use with_* methods for copy-on-write. "
            f"Attempted: {name}"
        )

    # ---- Read-only properties ----
    @property
    def id(self) -> str: return self._id  # type: ignore
    @property
    def text(self) -> str: return self._text  # type: ignore
    @property
    def embedding(self) -> Optional[Tuple[float, ...]]: return self._embedding  # type: ignore
    @property
    def timestamp(self) -> float: return self._timestamp  # type: ignore
    @property
    def last_accessed(self) -> float: return self._last_accessed  # type: ignore
    @property
    def last_modified(self) -> float: return self._last_modified  # type: ignore
    @property
    def source(self) -> str: return self._source  # type: ignore
    @property
    def semantic_entropy(self) -> float: return self._semantic_entropy  # type: ignore
    @property
    def novelty_score(self) -> float: return self._novelty_score  # type: ignore
    @property
    def reinforcement_score(self) -> float: return self._reinforcement_score  # type: ignore
    @property
    def retrieval_count(self) -> int: return self._retrieval_count  # type: ignore
    @property
    def consolidation_count(self) -> int: return self._consolidation_count  # type: ignore
    @property
    def parent_ids(self) -> Tuple[str, ...]: return self._parent_ids  # type: ignore
    @property
    def child_ids(self) -> Tuple[str, ...]: return self._child_ids  # type: ignore
    @property
    def abstraction_level(self) -> AbstractionLevel: return self._abstraction_level  # type: ignore
    @property
    def pressure_score(self) -> float: return self._pressure_score  # type: ignore
    @property
    def freshness(self) -> float: return self._freshness  # type: ignore
    @property
    def causal_links(self) -> Tuple[str, ...]: return self._causal_links  # type: ignore
    @property
    def is_archived(self) -> bool: return self._is_archived  # type: ignore
    @property
    def cluster_id(self) -> Optional[str]: return self._cluster_id  # type: ignore
    @property
    def checksum(self) -> Optional[str]: return self._checksum  # type: ignore
    @property
    def generation(self) -> int: return self._generation  # type: ignore

    # ---- Computed ----
    def age_seconds(self) -> float:
        return time.time() - self._timestamp  # type: ignore

    def staleness(self) -> float:
        return time.time() - self._last_accessed  # type: ignore

    def effective_score(self, policy: ScoringPolicy = DEFAULT_SCORING_POLICY) -> float:
        """Score computed via injected policy. No hardcoded formula."""
        return policy.score(
            reinforcement=self._reinforcement_score,  # type: ignore
            freshness=self._freshness,  # type: ignore
            novelty=self._novelty_score,  # type: ignore
            pressure=self._pressure_score,  # type: ignore
        )

    # ---- Copy-on-write mutations ----
    def _clone(self, **overrides) -> "ImmutableCognitiveNode":
        base = dict(
            id=self._id,
            text=self._text,
            embedding=self._embedding,
            timestamp=self._timestamp,
            last_accessed=self._last_accessed,
            last_modified=self._last_modified,
            source=self._source,
            semantic_entropy=self._semantic_entropy,
            novelty_score=self._novelty_score,
            reinforcement_score=self._reinforcement_score,
            retrieval_count=self._retrieval_count,
            consolidation_count=self._consolidation_count,
            parent_ids=self._parent_ids,
            child_ids=self._child_ids,
            abstraction_level=self._abstraction_level,
            pressure_score=self._pressure_score,
            freshness=self._freshness,
            causal_links=self._causal_links,
            is_archived=self._is_archived,
            cluster_id=self._cluster_id,
            checksum=self._checksum,
            generation=self._generation,
        )
        base.update(overrides)
        return ImmutableCognitiveNode(**base)

    def with_touch(self) -> "ImmutableCognitiveNode":
        """Return new node with access recorded. Does NOT mutate self."""
        return self._clone(
            last_accessed=time.time(),
            retrieval_count=self._retrieval_count + 1,  # type: ignore
            generation=self._generation + 1,  # type: ignore
        )

    def with_freshness(self, new_freshness: float) -> "ImmutableCognitiveNode":
        return self._clone(freshness=max(new_freshness, 1e-4), generation=self._generation + 1)  # type: ignore

    def with_reinforcement(self, new_score: float) -> "ImmutableCognitiveNode":
        return self._clone(reinforcement_score=max(new_score, 0.0), generation=self._generation + 1)  # type: ignore

    def with_novelty(self, new_score: float) -> "ImmutableCognitiveNode":
        return self._clone(novelty_score=max(min(new_score, 1.0), 0.0), generation=self._generation + 1)  # type: ignore

    def with_pressure(self, new_score: float) -> "ImmutableCognitiveNode":
        return self._clone(pressure_score=max(min(new_score, 1.0), 0.0), generation=self._generation + 1)  # type: ignore

    def with_abstraction_level(self, level: AbstractionLevel) -> "ImmutableCognitiveNode":
        return self._clone(abstraction_level=level, generation=self._generation + 1)  # type: ignore

    def with_cluster(self, cluster_id: str) -> "ImmutableCognitiveNode":
        return self._clone(cluster_id=cluster_id, generation=self._generation + 1)  # type: ignore

    def with_archived(self) -> "ImmutableCognitiveNode":
        return self._clone(is_archived=True, generation=self._generation + 1)  # type: ignore

    def with_parents(self, parent_ids: Tuple[str, ...]) -> "ImmutableCognitiveNode":
        return self._clone(parent_ids=parent_ids, generation=self._generation + 1)  # type: ignore

    def with_checksum(self, cs: str) -> "ImmutableCognitiveNode":
        return self._clone(checksum=cs, generation=self._generation)  # type: ignore

    def compute_checksum(self) -> str:
        payload = f"{self._id}:{self._text}:{self._timestamp}"  # type: ignore
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def __repr__(self) -> str:
        return (
            f"ImmutableCognitiveNode(id={self._id[:8]}..., "  # type: ignore
            f"level={self._abstraction_level.name}, "  # type: ignore
            f"gen={self._generation}, "  # type: ignore
            f"freshness={self._freshness:.3f})"  # type: ignore
        )

    def __hash__(self) -> int:
        return hash(self._id)  # type: ignore

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ImmutableCognitiveNode):
            return self._id == other._id  # type: ignore
        return False


# Keep old name as alias for migration compatibility
CognitiveNode = ImmutableCognitiveNode


# ==================================================
# COGNITIVE TICK — IMMUTABLE PRESSURE EVENT
# ==================================================

@dataclass(frozen=True)
class CognitiveTick:
    """
    Immutable. Every significant operation emits one.
    Scheduler is pressure-event driven, not wall-clock.

    total_pressure() weights are documented:
    - pressure_delta:    0.40  primary load signal
    - semantic_load:     0.20  embedding/semantic complexity
    - retrieval_cost:    0.15  cost of coarse-to-fine expansion
    - latency_cost:      0.10  observed latency vs budget
    - memory_saturation: 0.10  level fill ratio
    - hallucination_risk: 0.05  downstream safety signal

    Weights sum to 1.0. Source: empirical from v3.1 runs.
    """
    tick_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    pressure_delta: float = 0.0
    semantic_load: float = 0.0
    recursion_depth: int = 0
    retrieval_cost: float = 0.0
    latency_cost: float = 0.0
    memory_saturation: float = 0.0
    hallucination_risk: float = 0.0

    source_operation: str = ""
    affected_node_ids: Tuple[str, ...] = ()

    def total_pressure(self) -> float:
        return (
            self.pressure_delta    * 0.40
            + self.semantic_load   * 0.20
            + self.retrieval_cost  * 0.15
            + self.latency_cost    * 0.10
            + self.memory_saturation * 0.10
            + self.hallucination_risk * 0.05
        )


# ==================================================
# DECISION EVENT — OBSERVABILITY CONTRACT (IMMUTABLE)
# ==================================================

@dataclass(frozen=True)
class DecisionEvent:
    """
    Immutable observability record.
    Emitted by every architecturally significant decision.
    """
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: DecisionEventType = DecisionEventType.RETRIEVAL
    timestamp: float = field(default_factory=time.time)

    causes: Tuple[str, ...] = ()
    affected_nodes: Tuple[str, ...] = ()

    pressure_before: float = 0.0
    pressure_after: float = 0.0
    pressure_zone: PressureZone = PressureZone.LOCAL

    scheduler_state: Tuple[Tuple[str, float], ...] = ()  # immutable k-v pairs

    recursion_depth: int = 0
    recursion_budget_remaining: int = 0

    consolidation_reason: Optional[ConsolidationReason] = None
    retrieval_lineage: Tuple[str, ...] = ()
    abstraction_level: Optional[AbstractionLevel] = None

    # Bounded metadata: max 16 key-value pairs
    metadata: Tuple[Tuple[str, str], ...] = ()

    def metadata_dict(self) -> Dict[str, str]:
        return dict(self.metadata)


# ==================================================
# RECURSION BUDGET — IMMUTABLE EXPLICIT CONTRACT
# ==================================================

@dataclass(frozen=True)
class RecursionBudget:
    max_depth: int = 5
    current_depth: int = 0
    pressure_budget: float = 1.0
    pressure_consumed: float = 0.0
    latency_budget_ms: float = 500.0
    latency_consumed_ms: float = 0.0

    def descend(self, pressure_cost: float = 0.1, latency_ms: float = 0.0) -> "RecursionBudget":
        new_depth = self.current_depth + 1
        new_pressure = self.pressure_consumed + pressure_cost
        new_latency = self.latency_consumed_ms + latency_ms

        if new_depth > self.max_depth:
            raise RecursiveBudgetExceeded(new_depth, self.max_depth, "depth")
        if new_pressure > self.pressure_budget:
            raise RecursiveBudgetExceeded(new_depth, self.max_depth, "pressure")
        if new_latency > self.latency_budget_ms:
            raise RecursiveBudgetExceeded(new_depth, self.max_depth, "latency")

        return RecursionBudget(
            max_depth=self.max_depth,
            current_depth=new_depth,
            pressure_budget=self.pressure_budget,
            pressure_consumed=new_pressure,
            latency_budget_ms=self.latency_budget_ms,
            latency_consumed_ms=new_latency,
        )

    @property
    def remaining_depth(self) -> int:
        return self.max_depth - self.current_depth

    @property
    def pressure_remaining(self) -> float:
        return self.pressure_budget - self.pressure_consumed

    @property
    def is_exhausted(self) -> bool:
        return self.current_depth >= self.max_depth or self.pressure_remaining <= 0.0


# ==================================================
# RETRIEVAL RESULT
# ==================================================

@dataclass(frozen=True)
class RetrievalResult:
    node: ImmutableCognitiveNode
    score: float
    retrieval_depth: AbstractionLevel
    retrieval_path: Tuple[str, ...]
    cost: float = 0.0


# ==================================================
# SEMANTIC CLUSTER — with cooldown jitter
# ==================================================

@dataclass
class SemanticCluster:
    """
    Mutable cluster descriptor (owned by ClusterRegistry, not shared).
    cooldown_jitter_factor: per-cluster random jitter applied at construction
    to prevent synchronized re-consolidation after burst.

    Mathematical rationale for jitter:
    If N clusters all consolidate at time T with cooldown C,
    they all re-trigger at T+C. Jitter U(0, C*factor) desynchronizes.
    Expected desynchronization = C*factor/2 seconds apart.
    """
    cluster_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    abstraction_level: AbstractionLevel = AbstractionLevel.SEMANTIC_CLUSTER
    node_ids: List[str] = field(default_factory=list)
    centroid_embedding: Optional[Tuple[float, ...]] = None
    summary_node_id: Optional[str] = None
    local_pressure: float = 0.0
    created_at: float = field(default_factory=time.time)
    last_consolidated: float = field(default_factory=time.time)
    consolidation_cooldown_until: float = 0.0

    # Jitter seed assigned at cluster creation — deterministic per cluster
    cooldown_jitter_seed: float = field(default_factory=random.random)

    def is_in_cooldown(self) -> bool:
        return time.time() < self.consolidation_cooldown_until

    def set_cooldown(self, base_seconds: float) -> None:
        """
        Set cooldown with per-cluster jitter to prevent synchronized storms.
        jitter ∈ U(0, base * 0.5) — seeded by cluster-specific value.
        """
        rng = random.Random(self.cooldown_jitter_seed + self.last_consolidated)
        jitter = rng.uniform(0, base_seconds * 0.5)
        self.consolidation_cooldown_until = time.time() + base_seconds + jitter
        self.last_consolidated = time.time()

    def member_count(self) -> int:
        return len(self.node_ids)