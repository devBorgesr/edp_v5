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

# Commit 3b (Renato, 04/06/2026) — Pareto event logger
# Telemetria estruturada para calibradores futuros (Gauss, Bayes, Memory Palace).
# Interface abstrata + impl local FileParetoStore (JSONL append-only).
# Arquitetura Forward: RemoteParetoStore preparado para Servidor B futuro.
from .pareto_store import (
    ParetoEventStore, FileParetoStore, get_pareto_store,
    new_correlation_id, set_current_correlation_id,
    get_current_correlation_id, clear_current_correlation_id,
    emit_memory_added, emit_memory_accessed, emit_mode_switched,
    emit_task_started, emit_task_completed,
    emit_token_usage, emit_ranking_decision, classificar_conteudo,
    emit_reflection, resumo_reweights, emit_contradiction_scan,
    amostra_valida_fase2,
    set_current_format_state, get_current_format_state,
    clear_current_format_state, hash_format_state,
    EVENT_TYPES,
)

# Commit 4 (Renato, 05/06/2026) — Gauss calibrator
# Estatísticas (média, σ, percentis) sobre eventos Pareto. Habilita decisões
# adaptativas: detecção de outliers, threshold dinâmico, calibração futura
# de SESSION_BOOST_FACTOR/CURRENT_SESSION_TRUST_THRESHOLD baseada em dados reais.
from .gauss_calibrator import (
    GaussStats, GaussCalibrator, get_gauss_calibrator,
    KNOWN_METRICS, DEFAULT_WINDOW_DAYS, DEFAULT_Z_THRESHOLD,
)

# Commit 5α (Renato, 06/06/2026) — Bayes mínimo (frequência condicional)
# Calcula P(B|A) via co-ocorrência de correlation_id no mesmo turno.
# Escopo mínimo: probabilidade + n_a/n_a_and_b/n_b_total. NÃO inclui decisões
# automáticas, priors bayesianos ou mining avançado (Commits 5β/5γ futuros).
# Reusa pareto_store.last_query_stats() para diagnóstico preciso (Dívida #40).
from .bayes_calibrator import (
    ConditionalProb, BayesCalibrator, get_bayes_calibrator,
    KNOWN_CONDITIONALS, MIN_OCCURRENCES_FOR_PROB,
)

# Commit 6 (Renato, 06/06/2026) — Background loop
# Scheduler async de jobs periódicos. asyncio.task no lifespan FastAPI.
# Destrava Camadas 2/3: sumarização autônoma, active recall (8), Memory
# Palace (#36), decisions cognitive via Haiku (3d), calibração automática.
# Robustez: try/except por job, suspend em pressure WARNING/CRITICAL, sem
# retry automático. Sem persistência de estado (jobs in-memory).
from .background_loop import (
    BackgroundJob, BackgroundLoop, get_background_loop,
    DEFAULT_TICK_SEC, DEFAULT_JOB_TIMEOUT,
)

# Commit 3d (Renato, 06/06/2026) — Cognitive decisions extractor
# Polling em background: extrai decisões estruturadas (key_assertion +
# concepts + domain) para entries cognitive via Haiku. NÃO afeta hot path
# conversacional — roda 60s após a entry ser gravada. Reusa background_loop,
# LLM provider, e memory.entries já validados.
from .cognitive_decisions import (
    CognitiveDecisions, CognitiveDecisionsExtractor,
    make_extraction_job, FIELD_COGNITIVE_DECISIONS,
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
    # Commit 3b — Pareto event logger
    "ParetoEventStore", "FileParetoStore", "get_pareto_store",
    "new_correlation_id", "set_current_correlation_id",
    "get_current_correlation_id", "clear_current_correlation_id",
    "emit_memory_added", "emit_memory_accessed", "emit_mode_switched",
    "emit_task_started", "emit_task_completed",
    # Fase 1 da calibração de tokens (12/08/2026)
    "emit_token_usage", "emit_ranking_decision", "classificar_conteudo",
    "emit_reflection", "resumo_reweights", "emit_contradiction_scan",
    "amostra_valida_fase2",
    "set_current_format_state", "get_current_format_state",
    "clear_current_format_state", "hash_format_state",
    "EVENT_TYPES",
    # Commit 4 — Gauss calibrator
    "GaussStats", "GaussCalibrator", "get_gauss_calibrator",
    "KNOWN_METRICS", "DEFAULT_WINDOW_DAYS", "DEFAULT_Z_THRESHOLD",
    # Commit 5α — Bayes mínimo
    "ConditionalProb", "BayesCalibrator", "get_bayes_calibrator",
    "KNOWN_CONDITIONALS", "MIN_OCCURRENCES_FOR_PROB",
    # Commit 6 — Background loop
    "BackgroundJob", "BackgroundLoop", "get_background_loop",
    "DEFAULT_TICK_SEC", "DEFAULT_JOB_TIMEOUT",
    # Commit 3d — Cognitive decisions
    "CognitiveDecisions", "CognitiveDecisionsExtractor",
    "make_extraction_job", "FIELD_COGNITIVE_DECISIONS",
]
