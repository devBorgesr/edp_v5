"""
types.py (PATCHED)
[P-F1] 3 símbolos importados 2× em from .exceptions import:
       PressureSaturationError, ConsolidationCascadeError, RetrievalBudgetExceeded
       Linter silenciava silenciosamente — agora único bloco de import.
"""
"""
FRACTAL EDP — CORE TYPE CONTRACTS
All fundamental data structures. No business logic here.
Immutable contracts — changes here ripple everywhere.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, FrozenSet, List, Optional, Tuple
import time


# ==================================================
# ENUMERATIONS
# ==================================================

class AbstractionLevel(int, Enum):
    EPISODIC        = 0   # raw episode
    EVENT           = 1   # consolidated event
    SEMANTIC_CLUSTER = 2  # semantic cluster
    ABSTRACTION     = 3   # persistent abstraction
    META_PATTERN    = 4   # meta cognitive pattern
    MACRO_STATE     = 5   # global semantic macrostate


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
    RETRIEVAL           = "retrieval"
    CONSOLIDATION       = "consolidation"
    SUMMARIZATION       = "summarization"
    PRESSURE_ESCALATION = "pressure_escalation"
    ABSTRACTION_PROMOTE = "abstraction_promote"
    MEMORY_MIGRATE      = "memory_migrate"
    DECAY_APPLIED       = "decay_applied"
    NOVELTY_REBALANCE   = "novelty_rebalance"
    BUDGET_EXCEEDED     = "budget_exceeded"
    ANTI_FOSSILIZATION  = "anti_fossilization"


# ==================================================
# FAIL-SAFE EXCEPTIONS
# ==================================================

# Canonical exceptions — single source of truth
from .exceptions import (
    RecursiveBudgetExceeded,
    PressureSaturationError,
    ConsolidationCascadeError,
    RetrievalBudgetExceeded,
    EconomyBudgetExceeded,
    SnapshotConflict,
    StormDetected,
)  # [P-F1] Removidas 3 linhas duplicadas: PressureSaturationError,
   #         ConsolidationCascadeError, RetrievalBudgetExceeded









# ==================================================
# COGNITIVE NODE — BASE UNIT
# ==================================================

@dataclass
class CognitiveNode:
    """
    Fundamental semantic unit of the fractal architecture.
    Embedding is ONE dimension, not the whole node.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    text: str = ""
    embedding: Optional[List[float]] = None

    # Temporal
    timestamp: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    last_modified: float = field(default_factory=time.time)

    # Source
    source: str = "unknown"

    # Semantic scores
    semantic_entropy: float = 0.0      # information density [0,1]
    novelty_score: float = 1.0         # how novel vs existing [0,1]
    reinforcement_score: float = 1.0   # reinforcement weight [0,∞)
    retrieval_count: int = 0
    consolidation_count: int = 0

    # Hierarchy
    parent_ids: List[str] = field(default_factory=list)
    child_ids: List[str] = field(default_factory=list)
    abstraction_level: AbstractionLevel = AbstractionLevel.EPISODIC

    # Pressure & freshness
    pressure_score: float = 0.0   # accumulated cognitive pressure
    freshness: float = 1.0        # decays over time [0,1]

    # Causal
    causal_links: List[str] = field(default_factory=list)   # node ids

    # Internal state
    is_archived: bool = False
    cluster_id: Optional[str] = None
    checksum: Optional[str] = None   # for corruption detection

    def touch(self) -> None:
        """Record access without modifying semantic content."""
        self.last_accessed = time.time()
        self.retrieval_count += 1

    def age_seconds(self) -> float:
        return time.time() - self.timestamp

    def staleness(self) -> float:
        """How long since last access, normalized."""
        return time.time() - self.last_accessed

    def effective_score(self) -> float:
        """Combined relevance score for retrieval ranking."""
        return (
            self.reinforcement_score
            * self.freshness
            * self.novelty_score
            * (1.0 - min(self.pressure_score, 1.0) * 0.3)
        )


# ==================================================
# COGNITIVE TICK — PRESSURE EVENT
# ==================================================

@dataclass
class CognitiveTick:
    """
    Scheduler operates on pressure events, not wall-clock.
    Every significant operation emits a CognitiveTick.
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
    affected_node_ids: List[str] = field(default_factory=list)

    def total_pressure(self) -> float:
        return (
            self.pressure_delta * 0.4
            + self.semantic_load * 0.2
            + self.retrieval_cost * 0.15
            + self.latency_cost * 0.1
            + self.memory_saturation * 0.1
            + self.hallucination_risk * 0.05
        )


# ==================================================
# DECISION EVENT — OBSERVABILITY CONTRACT
# ==================================================

@dataclass
class DecisionEvent:
    """
    Every architecturally significant decision emits a DecisionEvent.
    This is the observability backbone — never skip this.
    """
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: DecisionEventType = DecisionEventType.RETRIEVAL
    timestamp: float = field(default_factory=time.time)

    # Causality
    causes: List[str] = field(default_factory=list)         # decision_event ids
    affected_nodes: List[str] = field(default_factory=list) # node ids

    # Pressure state
    pressure_before: float = 0.0
    pressure_after: float = 0.0
    pressure_zone: PressureZone = PressureZone.LOCAL

    # Scheduler state snapshot
    scheduler_state: Dict = field(default_factory=dict)

    # Recursion
    recursion_depth: int = 0
    recursion_budget_remaining: int = 0

    # Context
    consolidation_reason: Optional[ConsolidationReason] = None
    retrieval_lineage: List[str] = field(default_factory=list)
    abstraction_level: Optional[AbstractionLevel] = None

    # Free-form metadata (bounded — max 512 bytes conceptually)
    metadata: Dict = field(default_factory=dict)


# ==================================================
# RECURSION BUDGET — EXPLICIT CONTRACT
# ==================================================

@dataclass
class RecursionBudget:
    """
    Passed explicitly through every recursive call.
    Never use implicit recursion depth tracking.
    """
    max_depth: int = 5
    current_depth: int = 0
    pressure_budget: float = 1.0       # total pressure allowed
    pressure_consumed: float = 0.0
    latency_budget_ms: float = 500.0
    latency_consumed_ms: float = 0.0

    def descend(self, pressure_cost: float = 0.1, latency_ms: float = 0.0) -> "RecursionBudget":
        """Returns new budget for child call. Raises if limits exceeded."""
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

@dataclass
class RetrievalResult:
    """Typed result from any retrieval operation."""
    node: CognitiveNode
    score: float
    retrieval_depth: AbstractionLevel
    retrieval_path: List[str]   # abstraction levels traversed
    cost: float = 0.0


# ==================================================
# CLUSTER DESCRIPTOR
# ==================================================

@dataclass
class SemanticCluster:
    """Represents a group of semantically similar nodes at a given level."""
    cluster_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    abstraction_level: AbstractionLevel = AbstractionLevel.SEMANTIC_CLUSTER
    node_ids: List[str] = field(default_factory=list)
    centroid_embedding: Optional[List[float]] = None
    summary_node_id: Optional[str] = None   # points to CognitiveNode at level+1
    local_pressure: float = 0.0
    created_at: float = field(default_factory=time.time)
    last_consolidated: float = field(default_factory=time.time)
    consolidation_cooldown_until: float = 0.0   # anti-cascade

    def is_in_cooldown(self) -> bool:
        return time.time() < self.consolidation_cooldown_until

    def member_count(self) -> int:
        return len(self.node_ids)