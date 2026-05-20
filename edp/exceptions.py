"""
FRACTAL EDP — CANONICAL EXCEPTION HIERARCHY

Single source of truth for all EDP exceptions.
Both types.py and types_v32.py import from here.
This eliminates the duplicate-class problem that caused
`except PressureSaturationError` to silently miss exceptions
raised by modules still on types.py.

IMPORT RULE:
  All modules — regardless of types.py or types_v32.py — must import
  exceptions from this module (directly or via types_v32).
  Never define exceptions inline in types files.

HIERARCHY:
  EDPException                   base for all EDP errors
  ├── BudgetExceeded             resource budget violations
  │   ├── RecursiveBudgetExceeded
  │   ├── RetrievalBudgetExceeded
  │   └── EconomyBudgetExceeded
  ├── StructuralError            architecture contract violations
  │   ├── SnapshotConflict
  │   └── ConsolidationCascadeError
  └── StabilityError             operational stability violations
      ├── PressureSaturationError
      └── StormDetected
"""
from __future__ import annotations


class EDPException(Exception):
    """Base for all EDP exceptions. Catch this to handle any EDP error."""


# ---- Budget Exceeded ----

class BudgetExceeded(EDPException):
    """Any resource budget violation."""


class RecursiveBudgetExceeded(BudgetExceeded):
    def __init__(self, depth: int, budget: int, context: str = ""):
        self.depth = depth
        self.budget = budget
        self.context = context
        super().__init__(
            f"Recursive budget exceeded: depth={depth}, budget={budget}, ctx={context}"
        )


class RetrievalBudgetExceeded(BudgetExceeded):
    pass


class EconomyBudgetExceeded(BudgetExceeded):
    def __init__(self, operation: str, cost: float, budget: float):
        self.operation = operation
        self.cost = cost
        self.budget = budget
        super().__init__(
            f"Economy budget exceeded: op={operation}, cost={cost:.3f}, budget={budget:.3f}"
        )


# ---- Structural Errors ----

class StructuralError(EDPException):
    """Architecture contract violation."""


class SnapshotConflict(StructuralError):
    """Atomic snapshot promotion detected a write conflict."""


class ConsolidationCascadeError(StructuralError):
    """Consolidation would trigger a cascade — blocked."""


# ---- Stability Errors ----

class StabilityError(EDPException):
    """Operational stability violation."""


class PressureSaturationError(StabilityError):
    """Global pressure exceeded saturation threshold."""


class StormDetected(StabilityError):
    """RetrievalStormGuard detected a storm pattern."""
    def __init__(self, pattern: str, score: float):
        self.pattern = pattern
        self.score = score
        super().__init__(f"Storm detected: pattern={pattern}, score={score:.3f}")