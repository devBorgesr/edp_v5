"""edp.runtime — runtime management, session registry, context, pressure, queue, boot state."""
from .registry import (
    get_runtime, get_memory, is_valid, get_error,
    list_sessions, reset_session, stats,
)
from .context_window_manager import (
    ContextWindowManager, BuiltContext, ContextBudget,
    estimate_tokens, get_window_size, KNOWN_WINDOWS,
)

# v3.4 — sprint estabilidade
from .pressure_governor import (
    get_governor, MemoryPressureGovernor, PressureLevel, PressureReading,
)
from .inference_queue import (
    get_queue, InferenceQueue, CancelToken,
    QueueFull, QueueTimeout, QueueStats,
)
from .boot_state import (
    get_boot_state, BootStateMachine, RuntimeState, ComponentHealth,
)

# v3.5 — sprint governança cognitiva mínima
from .retrieval_monitor import (
    get_monitor, RetrievalQualityMonitor, DailyBucket,
)
from .contradiction_flagger import (
    get_flagger, ContradictionFlagger, ConflictFlag,
    has_negation, negation_asymmetry,
)

__all__ = [
    "get_runtime", "get_memory", "is_valid", "get_error",
    "list_sessions", "reset_session", "stats",
    "ContextWindowManager", "BuiltContext", "ContextBudget",
    "estimate_tokens", "get_window_size", "KNOWN_WINDOWS",
    # v3.4
    "get_governor", "MemoryPressureGovernor", "PressureLevel", "PressureReading",
    "get_queue", "InferenceQueue", "CancelToken",
    "QueueFull", "QueueTimeout", "QueueStats",
    "get_boot_state", "BootStateMachine", "RuntimeState", "ComponentHealth",
    # v3.5
    "get_monitor", "RetrievalQualityMonitor", "DailyBucket",
    "get_flagger", "ContradictionFlagger", "ConflictFlag",
    "has_negation", "negation_asymmetry",
]
