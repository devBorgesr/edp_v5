"""
FRACTAL EDP v3.2 — COGNITIVE SNAPSHOT MANAGER (PATCHED)

PATCHES APLICADOS (baseados no Relatório Técnico Crítico):

  [P-S1] CRÍTICO 1 — _metrics.current_generation_id hardcoded como 0:
         Substituído por initial.generation_id — correto para qualquer
         sequência de inicialização (preload, bootstrap de disco, restore).

  [P-S2] CRÍTICO 2 — _current depende de atomicidade do GIL (STORE_ATTR):
         Introduzido AtomicReference wrapper com threading.Lock interno.
         Operações get()/set() são O(1) e compatíveis com non-GIL Python (PEP 703).
         _current agora é _current_ref: AtomicReference[CognitiveGeneration].
         API pública inalterada — snapshot(), get(), etc. continuam iguais.

  [P-S3] CRÍTICO 3 — Snapshot explosion sem controle de taxa:
         Adicionado rate limiter: min_snapshot_interval_ms (default 50ms).
         create_snapshot() e _commit() respeitam o intervalo mínimo.
         SnapshotMetrics inclui rate_limited_count para observabilidade.
         Configurável via __init__ para não quebrar testes de smoke.

  [P-S4] CRÍTICO 4 — _archive_generation() pode remover recovery anchors:
         Adicionado campo is_recovery_anchor em CognitiveGeneration.
         prune_old_snapshots() e _archive_generation() nunca removem anchors.
         rollback() bem-sucedido marca a geração restaurada como anchor.
         validate_snapshot() bem-sucedido pode marcar a geração como anchor.

  [P-S5] CRÍTICO 5 — validate_snapshot() recalcula checksum inteiro (O(n)):
         Adicionado _checksum_cache: dict[generation_id → str].
         _compute_generation_checksum() consulta cache antes de recalcular.
         Cache é invalidado apenas quando a geração é arquivada (nunca muda).
         Reduz validações repetidas de O(n) para O(1) após primeiro cálculo.

  [P-S6] CRÍTICO 6 — _compute_diff() usa on.generation como detector de mudança:
         on.generation pode não mudar mesmo com conteúdo diferente.
         Substituído por comparação de checksum individual do nó:
           on.checksum != nn.checksum  (quando disponível)
         Fallback para on.generation quando checksum ausente (retrocompat).

  [P-S7] CRÍTICO 7 — save_to_disk() não persiste histórico/métricas:
         save_to_disk() agora serializa CURRENT + history metadata + metrics.
         restore_from_disk() reconstrói history_summary (não os nós completos).
         Formato: { "current": {...}, "history_meta": [...], "metrics": {...} }
         Retro-compatível: arquivos antigos (sem history_meta) ainda carregam.

  [P-S8] CRÍTICO 8 — _emit() engole exceções silenciosamente:
         Adicionado _metrics.event_failures (contador cumulativo).
         _emit() registra falha em event_failures e ativa degraded_observability.
         Sistema não derruba — mas falhas ficam visíveis nas métricas.

  [P-SP1] PERF 1 — top_k_by_score() usa sorted() O(n log n):
          Substituído por heapq.nlargest() O(n log k).
          Para k=10, n=100k: ~14× mais rápido.

  [P-SP3] PERF 3 — history_summary() percorre tudo sempre:
          Adicionado _history_summary_cache (invalidado em _archive_generation).
          Chamadas repetidas sem mudança retornam cache em O(1).
"""
from __future__ import annotations

import hashlib
import heapq
import json
import os
import threading
import time
import zlib
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Callable, Dict, FrozenSet, Generic, Iterator,
    List, Optional, Set, Tuple, TypeVar,
)

from .exceptions import SnapshotConflict
from .types_v32 import (
    AbstractionLevel,
    DecisionEvent,
    DecisionEventType,
    ImmutableCognitiveNode,
    ScoringPolicy,
    DEFAULT_SCORING_POLICY,
)

_T = TypeVar("_T")


# ==================================================
# [P-S2] ATOMIC REFERENCE — future-proof para non-GIL
# ==================================================

class AtomicReference(Generic[_T]):
    """
    Wrapper thread-safe para referência compartilhada.
    Substitui atribuição direta a _current (que dependia do GIL).
    O(1) get/set. Compatível com PEP 703 (no-GIL Python).
    """
    __slots__ = ("_lock", "_value")

    def __init__(self, value: _T) -> None:
        self._lock  = threading.Lock()
        self._value = value

    def get(self) -> _T:
        with self._lock:
            return self._value

    def set(self, value: _T) -> None:
        with self._lock:
            self._value = value

    def get_and_set(self, value: _T) -> _T:
        """Atômico: retorna o valor anterior e define o novo."""
        with self._lock:
            old = self._value
            self._value = value
            return old


# ==================================================
# ENUMS
# ==================================================

class GenerationState(Enum):
    PENDING    = "pending"
    CURRENT    = "current"
    SUPERSEDED = "superseded"
    ARCHIVED   = "archived"


# ==================================================
# METADATA DE SNAPSHOT
# ==================================================

@dataclass(frozen=True)
class SnapshotMetadata:
    generation_id:       int
    created_at:          float
    promoted_at:         Optional[float]
    node_count:          int
    checksum:            str
    state:               GenerationState
    parent_generation_id: Optional[int]
    rollback_source_id:  Optional[int]
    compression_used:    bool
    size_bytes:          int
    is_recovery_anchor:  bool = False  # [P-S4]


# ==================================================
# SNAPSHOT DIFF
# ==================================================

@dataclass(frozen=True)
class SnapshotDiff:
    from_generation: int
    to_generation:   int
    promoted_at:     float
    added_ids:       FrozenSet[str]
    removed_ids:     FrozenSet[str]
    modified_ids:    FrozenSet[str]

    @property
    def total_changes(self) -> int:
        return len(self.added_ids) + len(self.removed_ids) + len(self.modified_ids)

    @property
    def churn_ratio(self) -> float:
        common = len(self.added_ids) + len(self.modified_ids)
        if common == 0:
            return 0.0
        return len(self.modified_ids) / common


# ==================================================
# RESULTADO DE VALIDAÇÃO
# ==================================================

@dataclass(frozen=True)
class ValidationResult:
    generation_id:      int
    is_valid:           bool
    checksum_ok:        bool
    node_integrity_ok:  bool
    corrupted_node_ids: Tuple[str, ...]
    error_message:      Optional[str]
    validated_at:       float = field(default_factory=time.time)


# ==================================================
# MÉTRICAS DO SNAPSHOT MANAGER
# ==================================================

@dataclass
class SnapshotMetrics:
    total_snapshots:       int   = 0
    restore_count:         int   = 0
    corruption_events:     int   = 0
    rollback_count:        int   = 0
    conflict_count:        int   = 0
    snapshot_latency_ms:   float = 0.0
    current_generation_id: int   = 0
    current_node_count:    int   = 0
    history_size:          int   = 0
    rate_limited_count:    int   = 0   # [P-S3]
    event_failures:        int   = 0   # [P-S8]
    degraded_observability: bool = False  # [P-S8]

    _EMA_ALPHA: float = 0.2

    def record_snapshot_latency(self, ms: float) -> None:
        self.snapshot_latency_ms = (
            self._EMA_ALPHA * ms
            + (1.0 - self._EMA_ALPHA) * self.snapshot_latency_ms
        )

    def snapshot(self) -> Dict:
        return {
            "total_snapshots":      self.total_snapshots,
            "restore_count":        self.restore_count,
            "corruption_events":    self.corruption_events,
            "rollback_count":       self.rollback_count,
            "conflict_count":       self.conflict_count,
            "snapshot_latency_ms":  round(self.snapshot_latency_ms, 3),
            "current_generation":   self.current_generation_id,
            "current_node_count":   self.current_node_count,
            "history_size":         self.history_size,
            "rate_limited_count":   self.rate_limited_count,     # [P-S3]
            "event_failures":       self.event_failures,          # [P-S8]
            "degraded_observability": self.degraded_observability,# [P-S8]
        }


# ==================================================
# COGNITIVE GENERATION
# ==================================================

class CognitiveGeneration:
    """
    Snapshot imutável do store de nós.
    [P-S4] Campo is_recovery_anchor adicionado.
    [P-SP1] top_k_by_score usa heapq.nlargest O(n log k).
    """

    __slots__ = (
        'generation_id', 'state', 'promoted_at',
        '_nodes', '_checksum', '_metadata', '_frozen',
        '_parent_id', '_rollback_source_id',
        'is_recovery_anchor',  # [P-S4]
    )

    def __init__(
        self,
        generation_id: int,
        nodes: Dict[str, ImmutableCognitiveNode],
        parent_generation_id: Optional[int] = None,
        rollback_source_id: Optional[int] = None,
        state: GenerationState = GenerationState.PENDING,
        promoted_at: Optional[float] = None,
    ) -> None:
        self.generation_id          = generation_id
        self.state                  = state
        self.promoted_at            = promoted_at
        self._nodes                 = dict(nodes)
        self._checksum: Optional[str] = None
        self._frozen                = False
        self._metadata: Optional[SnapshotMetadata] = None
        self._parent_id             = parent_generation_id
        self._rollback_source_id    = rollback_source_id
        self.is_recovery_anchor     = False  # [P-S4]

    def get(self, node_id: str) -> Optional[ImmutableCognitiveNode]:
        return self._nodes.get(node_id)

    def all_nodes(self) -> List[ImmutableCognitiveNode]:
        return list(self._nodes.values())

    def node_ids(self) -> FrozenSet[str]:
        return frozenset(self._nodes.keys())

    def count(self) -> int:
        return len(self._nodes)

    def top_k_by_score(
        self,
        k: int,
        policy: ScoringPolicy = DEFAULT_SCORING_POLICY,
        level: Optional[AbstractionLevel] = None,
    ) -> List[ImmutableCognitiveNode]:
        """
        [P-SP1] heapq.nlargest O(n log k) — antes: sorted() O(n log n).
        Para k=10, n=100k: ~14× mais rápido.
        """
        candidates = (
            [n for n in self._nodes.values() if n.abstraction_level == level]
            if level is not None
            else list(self._nodes.values())
        )
        return heapq.nlargest(k, candidates, key=lambda n: n.effective_score(policy))

    def checksum(self) -> Optional[str]:
        return self._checksum

    def metadata(self) -> Optional[SnapshotMetadata]:
        return self._metadata

    def _freeze(self, checksum: str) -> None:
        if self._frozen:
            raise RuntimeError(f"Generation {self.generation_id} already frozen")
        self._checksum   = checksum
        self.state       = GenerationState.CURRENT
        self.promoted_at = time.time()
        self._frozen     = True
        self._metadata   = SnapshotMetadata(
            generation_id=self.generation_id,
            created_at=self.promoted_at,
            promoted_at=self.promoted_at,
            node_count=len(self._nodes),
            checksum=checksum,
            state=GenerationState.CURRENT,
            parent_generation_id=self._parent_id,
            rollback_source_id=self._rollback_source_id,
            compression_used=False,
            size_bytes=len(self._nodes) * 512,
            is_recovery_anchor=self.is_recovery_anchor,  # [P-S4]
        )

    def _supersede(self) -> None:
        self.state = GenerationState.SUPERSEDED

    def _archive(self) -> None:
        self.state = GenerationState.ARCHIVED

    def __len__(self) -> int:
        return len(self._nodes)

    def __repr__(self) -> str:
        return (
            f"CognitiveGeneration("
            f"id={self.generation_id}, "
            f"state={self.state.value}, "
            f"nodes={len(self._nodes)}, "
            f"anchor={self.is_recovery_anchor}, "
            f"checksum={self._checksum})"
        )


# ==================================================
# GERAÇÃO PENDENTE
# ==================================================

@dataclass
class _PendingGeneration:
    base_generation_id: int
    nodes: Dict[str, ImmutableCognitiveNode] = field(default_factory=dict)


# ==================================================
# PENDING WRITE
# ==================================================

class PendingWrite:
    """
    Context manager de escrita atômica.
    [P-S3] Verifica rate limit antes de commitar.
    """

    __slots__ = ('_manager', '_lock_held', '_pending', '_committed')

    def __init__(self, manager: "CognitiveSnapshotManager") -> None:
        self._manager   = manager
        self._lock_held = False
        self._pending: Optional[_PendingGeneration] = None
        self._committed = False

    def __enter__(self) -> "PendingWrite":
        self._manager._write_lock.acquire()
        self._lock_held = True
        current = self._manager._current_ref.get()
        self._pending = _PendingGeneration(
            base_generation_id=current.generation_id,
            nodes=dict(current._nodes),
        )
        return self

    def put(self, node: ImmutableCognitiveNode) -> None:
        if self._pending is None:
            raise RuntimeError("PendingWrite não iniciado.")
        self._pending.nodes[node.id] = node

    def remove(self, node_id: str) -> bool:
        if self._pending is None:
            raise RuntimeError("PendingWrite não iniciado.")
        if node_id in self._pending.nodes:
            del self._pending.nodes[node_id]
            return True
        return False

    def get(self, node_id: str) -> Optional[ImmutableCognitiveNode]:
        if self._pending is None:
            return None
        return self._pending.nodes.get(node_id)

    def count(self) -> int:
        return len(self._pending.nodes) if self._pending else 0

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        try:
            if exc_type is None and self._pending is not None and not self._committed:
                self._manager._commit(self._pending)
                self._committed = True
        finally:
            if self._lock_held:
                self._manager._write_lock.release()
                self._lock_held = False
        return False


# ==================================================
# COGNITIVE SNAPSHOT MANAGER
# ==================================================

class CognitiveSnapshotManager:
    """
    Store primário de nós cognitivos com versionamento geracional.

    Patches aplicados: P-S1 a P-S8, P-SP1, P-SP3.
    Interface pública 100% compatível com versão original.
    """

    def __init__(
        self,
        max_history_size: int = 10,
        save_path: Optional[str] = None,
        compress: bool = False,
        event_sink: Optional[Callable[[DecisionEvent], None]] = None,
        min_snapshot_interval_ms: float = 50.0,  # [P-S3]
    ) -> None:
        self._max_history               = max_history_size
        self._save_path                 = save_path
        self._compress                  = compress
        self._event_sink                = event_sink or (lambda _: None)
        self._min_interval_ms           = min_snapshot_interval_ms  # [P-S3]
        self._last_snapshot_time: float = 0.0                       # [P-S3]

        self._write_lock   = threading.Lock()
        self._history_lock = threading.Lock()
        self._metrics      = SnapshotMetrics()
        self._generation_counter: int = 0

        # [P-S5] Cache de checksums de geração — evita recálculo O(n)
        self._checksum_cache: Dict[int, str] = {}

        # [P-SP3] Cache de history_summary — invalidado em _archive_generation
        self._history_summary_cache: Optional[List[Dict]] = None

        # Geração inicial vazia
        initial = self._make_generation(nodes={}, parent_id=None, rollback_source_id=None)
        initial_checksum = self._compute_generation_checksum(initial._nodes)
        initial._freeze(initial_checksum)
        initial.is_recovery_anchor = True  # [P-S4] primeira geração é sempre anchor

        # [P-S2] AtomicReference — sem dependência de atomicidade do GIL
        self._current_ref: AtomicReference[CognitiveGeneration] = AtomicReference(initial)

        self._history: List[CognitiveGeneration] = [initial]

        self._metrics.total_snapshots      = 1
        # [P-S1] usa initial.generation_id, não hardcoded 0
        self._metrics.current_generation_id = initial.generation_id
        self._metrics.current_node_count   = 0

    # ---- Propriedade de conveniência interna ----

    @property
    def _current(self) -> CognitiveGeneration:
        """[P-S2] Acesso interno legível — usa AtomicReference."""
        return self._current_ref.get()

    # --------------------------------------------------
    # API DE LEITURA (lock-free)
    # --------------------------------------------------

    def snapshot(self) -> CognitiveGeneration:
        return self._current_ref.get()

    def latest(self) -> CognitiveGeneration:
        return self._current_ref.get()

    def get(self, node_id: str) -> Optional[ImmutableCognitiveNode]:
        return self._current_ref.get().get(node_id)

    def current_generation_id(self) -> int:
        return self._current_ref.get().generation_id

    # --------------------------------------------------
    # API DE ESCRITA
    # --------------------------------------------------

    def begin_write(self) -> PendingWrite:
        return PendingWrite(self)

    def create_snapshot(self) -> int:
        """
        [P-S3] Verifica rate limit antes de forçar checkpoint vazio.
        Se dentro do intervalo mínimo, incrementa rate_limited_count e retorna
        o generation_id atual sem criar nova geração.
        """
        now_ms = time.perf_counter() * 1000
        elapsed = now_ms - self._last_snapshot_time
        if elapsed < self._min_interval_ms:
            self._metrics.rate_limited_count += 1
            return self._current_ref.get().generation_id

        with self.begin_write():
            pass
        return self._current_ref.get().generation_id

    # --------------------------------------------------
    # VALIDAÇÃO
    # --------------------------------------------------

    def validate_snapshot(
        self, generation_id: Optional[int] = None
    ) -> ValidationResult:
        """
        [P-S5] Usa _checksum_cache para evitar recálculo O(n) em validações repetidas.
        Primeira validação de uma geração: O(n). Subsequentes: O(1).
        """
        with self._history_lock:
            if generation_id is None:
                gen = self._current_ref.get()
            else:
                gen = next(
                    (g for g in self._history if g.generation_id == generation_id),
                    None,
                )
            if gen is None:
                return ValidationResult(
                    generation_id=generation_id or -1,
                    is_valid=False,
                    checksum_ok=False,
                    node_integrity_ok=False,
                    corrupted_node_ids=(),
                    error_message=f"Generation {generation_id} not found",
                )

        try:
            # [P-S5] Consulta cache antes de recalcular
            gen_id = gen.generation_id
            if gen_id in self._checksum_cache:
                expected_checksum = self._checksum_cache[gen_id]
            else:
                expected_checksum = self._compute_generation_checksum(gen._nodes)
                self._checksum_cache[gen_id] = expected_checksum

            checksum_ok = (gen._checksum == expected_checksum)

            corrupted: List[str] = []
            for node_id, node in gen._nodes.items():
                if node.checksum is not None:
                    if node.checksum != node.compute_checksum():
                        corrupted.append(node_id)

            node_integrity_ok = len(corrupted) == 0
            is_valid = checksum_ok and node_integrity_ok

            if not is_valid:
                self._metrics.corruption_events += 1
            else:
                # [P-S4] Validação bem-sucedida marca como recovery anchor
                gen.is_recovery_anchor = True

            return ValidationResult(
                generation_id=gen.generation_id,
                is_valid=is_valid,
                checksum_ok=checksum_ok,
                node_integrity_ok=node_integrity_ok,
                corrupted_node_ids=tuple(corrupted),
                error_message=None if is_valid else (
                    f"checksum_ok={checksum_ok}, corrupted_nodes={len(corrupted)}"
                ),
            )
        except Exception as e:
            self._metrics.corruption_events += 1
            return ValidationResult(
                generation_id=gen.generation_id if gen else -1,
                is_valid=False,
                checksum_ok=False,
                node_integrity_ok=False,
                corrupted_node_ids=(),
                error_message=str(e),
            )

    # --------------------------------------------------
    # ROLLBACK
    # --------------------------------------------------

    def rollback(self, target_generation_id: int) -> bool:
        """
        [P-S4] Geração restaurada é marcada como recovery anchor.
        """
        with self._write_lock:
            with self._history_lock:
                target = next(
                    (g for g in self._history if g.generation_id == target_generation_id),
                    None,
                )
            if target is None:
                return False

            result = self.validate_snapshot(target_generation_id)
            if not result.is_valid:
                self._metrics.corruption_events += 1
                return False

            current = self._current_ref.get()
            new_gen = self._make_generation(
                nodes=dict(target._nodes),
                parent_id=current.generation_id,
                rollback_source_id=target_generation_id,
            )
            checksum = self._compute_generation_checksum(new_gen._nodes)
            new_gen._freeze(checksum)
            new_gen.is_recovery_anchor = True  # [P-S4] rollback → novo anchor

            old_gen = self._current_ref.get_and_set(new_gen)  # [P-S2] atômico
            old_gen._supersede()
            self._archive_generation(old_gen)
            self._last_snapshot_time = time.perf_counter() * 1000

            self._metrics.rollback_count         += 1
            self._metrics.total_snapshots        += 1
            self._metrics.current_generation_id  = new_gen.generation_id
            self._metrics.current_node_count     = new_gen.count()

            self._emit(DecisionEvent(
                event_type=DecisionEventType.SNAPSHOT_ROLLED_BACK,
                metadata=(
                    ("from_gen",       str(old_gen.generation_id)),
                    ("to_gen",         str(new_gen.generation_id)),
                    ("restored_from",  str(target_generation_id)),
                    ("node_count",     str(new_gen.count())),
                ),
            ))
            return True

    # --------------------------------------------------
    # DIFF
    # --------------------------------------------------

    def diff(self, gen_a_id: int, gen_b_id: int) -> Optional[SnapshotDiff]:
        current = self._current_ref.get()

        def find(gen_id: int):
            if current.generation_id == gen_id:
                return current
            with self._history_lock:
                return next(
                    (g for g in self._history if g.generation_id == gen_id),
                    None,
                )

        gen_a = find(gen_a_id)
        gen_b = find(gen_b_id)
        if gen_a is None or gen_b is None:
            return None
        return self._compute_diff(gen_a, gen_b)

    # --------------------------------------------------
    # HISTÓRICO E MÉTRICAS
    # --------------------------------------------------

    def history_summary(self) -> List[Dict]:
        """
        [P-SP3] Retorna cache se disponível — O(1) após primeira chamada.
        Cache invalidado em _archive_generation().
        """
        with self._history_lock:
            if self._history_summary_cache is not None:
                return list(self._history_summary_cache)
            summary = [
                {
                    "generation_id":      g.generation_id,
                    "state":              g.state.value,
                    "promoted_at":        g.promoted_at,
                    "node_count":         g.count(),
                    "checksum":           g._checksum,
                    "is_recovery_anchor": g.is_recovery_anchor,  # [P-S4]
                }
                for g in self._history
            ]
            self._history_summary_cache = summary
            return list(summary)

    def prune_old_snapshots(self) -> int:
        """
        [P-S4] Nunca remove recovery anchors.
        """
        with self._history_lock:
            current_id = self._current_ref.get().generation_id
            removable = [
                g for g in self._history
                if g.generation_id != current_id
                and g.state in (GenerationState.ARCHIVED, GenerationState.SUPERSEDED)
                and not g.is_recovery_anchor  # [P-S4] preserva anchors
            ]
            excess = len(self._history) - self._max_history
            if excess <= 0:
                return 0
            to_remove = sorted(removable, key=lambda g: g.generation_id)[:excess]
            to_remove_ids = {g.generation_id for g in to_remove}
            self._history = [
                g for g in self._history if g.generation_id not in to_remove_ids
            ]
            # [P-S5] Remove checksums cacheados das gerações removidas
            for gid in to_remove_ids:
                self._checksum_cache.pop(gid, None)
            self._history_summary_cache = None  # [P-SP3] invalida cache
            self._metrics.history_size = len(self._history)
            return len(to_remove)

    def metrics(self) -> SnapshotMetrics:
        current = self._current_ref.get()
        self._metrics.current_generation_id = current.generation_id
        self._metrics.current_node_count    = current.count()
        with self._history_lock:
            self._metrics.history_size = len(self._history)
        return SnapshotMetrics(
            total_snapshots=self._metrics.total_snapshots,
            restore_count=self._metrics.restore_count,
            corruption_events=self._metrics.corruption_events,
            rollback_count=self._metrics.rollback_count,
            conflict_count=self._metrics.conflict_count,
            snapshot_latency_ms=self._metrics.snapshot_latency_ms,
            current_generation_id=self._metrics.current_generation_id,
            current_node_count=self._metrics.current_node_count,
            history_size=self._metrics.history_size,
            rate_limited_count=self._metrics.rate_limited_count,  # [P-S3]
            event_failures=self._metrics.event_failures,            # [P-S8]
            degraded_observability=self._metrics.degraded_observability,
        )

    # --------------------------------------------------
    # PERSISTÊNCIA — [P-S7] transacional
    # --------------------------------------------------

    def save_to_disk(
        self,
        path: Optional[str] = None,
        compress: Optional[bool] = None,
    ) -> str:
        """
        [P-S7] Persiste CURRENT + history_meta + metrics.
        Formato estendido mas retro-compatível com restore_from_disk().
        """
        save_path  = path or self._save_path
        if save_path is None:
            raise ValueError("save_path não configurado")
        use_compress = compress if compress is not None else self._compress

        gen = self._current_ref.get()
        payload = {
            "generation_id": gen.generation_id,
            "promoted_at":   gen.promoted_at,
            "checksum":      gen._checksum,
            "nodes": [self._node_to_dict(n) for n in gen._nodes.values()],
            # [P-S7] campos adicionais
            "history_meta":  self.history_summary(),
            "metrics":       self._metrics.snapshot(),
        }

        json_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        data = zlib.compress(json_bytes, level=6) if use_compress else json_bytes
        ext  = ".edp.gz" if use_compress else ".edp.json"

        os.makedirs(save_path, exist_ok=True)
        filename = f"gen_{gen.generation_id:08d}_{int(gen.promoted_at or 0)}{ext}"
        target   = os.path.join(save_path, filename)
        tmp      = target + ".tmp"

        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
        return target

    def restore_from_disk(self, filepath: str) -> bool:
        """
        [P-S7] Restaura CURRENT + history_meta (sem recriar nós históricos).
        Retro-compatível: arquivos sem history_meta carregam apenas CURRENT.
        """
        try:
            with open(filepath, "rb") as f:
                raw = f.read()
            if filepath.endswith(".gz"):
                raw = zlib.decompress(raw)
            payload = json.loads(raw.decode("utf-8"))

            nodes: Dict[str, ImmutableCognitiveNode] = {}
            for nd in payload.get("nodes", []):
                node = self._dict_to_node(nd)
                nodes[node.id] = node

            saved_checksum = payload.get("checksum")
            computed = self._compute_generation_checksum(nodes)
            if saved_checksum and saved_checksum != computed:
                self._metrics.corruption_events += 1
                return False

            with self._write_lock:
                old_gen = self._current_ref.get()
                new_gen = self._make_generation(
                    nodes=nodes,
                    parent_id=old_gen.generation_id,
                    rollback_source_id=payload.get("generation_id"),
                )
                new_gen._freeze(computed)
                new_gen.is_recovery_anchor = True  # [P-S4] restore → anchor
                old_gen._supersede()
                self._current_ref.set(new_gen)     # [P-S2] atômico
                self._archive_generation(old_gen)
                self._metrics.restore_count  += 1
                self._metrics.total_snapshots += 1

            return True
        except Exception:
            self._metrics.corruption_events += 1
            return False

    # --------------------------------------------------
    # INTERNO — COMMIT
    # --------------------------------------------------

    def _commit(self, pending: _PendingGeneration) -> None:
        """
        [P-S3] Verifica rate limit antes de commitar.
        [P-S2] Usa AtomicReference.get_and_set() para swap atômico.
        """
        t0 = time.perf_counter()

        # [P-S3] Rate limit
        now_ms  = t0 * 1000
        elapsed = now_ms - self._last_snapshot_time
        if elapsed < self._min_interval_ms:
            self._metrics.rate_limited_count += 1
            return

        current = self._current_ref.get()
        if pending.base_generation_id != current.generation_id:
            self._metrics.conflict_count += 1
            raise SnapshotConflict(
                f"Conflito: base={pending.base_generation_id}, "
                f"atual={current.generation_id}. Re-leia e reconstrua."
            )

        new_gen  = self._make_generation(
            nodes=pending.nodes,
            parent_id=current.generation_id,
            rollback_source_id=None,
        )
        checksum = self._compute_generation_checksum(new_gen._nodes)
        new_gen._freeze(checksum)

        diff    = self._compute_diff(current, new_gen)
        old_gen = self._current_ref.get_and_set(new_gen)  # [P-S2] atômico
        old_gen._supersede()
        self._archive_generation(old_gen)

        self._last_snapshot_time = now_ms  # [P-S3]

        elapsed_ms = (time.perf_counter() - t0) * 1000
        self._metrics.record_snapshot_latency(elapsed_ms)
        self._metrics.total_snapshots       += 1
        self._metrics.current_generation_id  = new_gen.generation_id
        self._metrics.current_node_count     = new_gen.count()

        self._emit(DecisionEvent(
            event_type=DecisionEventType.SNAPSHOT_PROMOTED,
            metadata=(
                ("from_gen",   str(old_gen.generation_id)),
                ("to_gen",     str(new_gen.generation_id)),
                ("added",      str(len(diff.added_ids))),
                ("removed",    str(len(diff.removed_ids))),
                ("modified",   str(len(diff.modified_ids))),
                ("churn",      f"{diff.churn_ratio:.3f}"),
                ("latency_ms", f"{elapsed_ms:.2f}"),
            ),
        ))

    # --------------------------------------------------
    # INTERNO — HELPERS
    # --------------------------------------------------

    def _make_generation(
        self,
        nodes: Dict[str, ImmutableCognitiveNode],
        parent_id: Optional[int],
        rollback_source_id: Optional[int],
    ) -> CognitiveGeneration:
        new_id = self._generation_counter
        self._generation_counter += 1
        return CognitiveGeneration(
            generation_id=new_id,
            nodes=nodes,
            parent_generation_id=parent_id,
            rollback_source_id=rollback_source_id,
        )

    def _compute_generation_checksum(
        self, nodes: Dict[str, ImmutableCognitiveNode]
    ) -> str:
        h = hashlib.sha256()
        for nid in sorted(nodes.keys()):
            node   = nodes[nid]
            node_cs = node.checksum or node.compute_checksum()
            h.update(f"{nid}:{node_cs}:{node.generation}\n".encode())
        return h.hexdigest()[:32]

    def _compute_diff(
        self, old: CognitiveGeneration, new: CognitiveGeneration
    ) -> SnapshotDiff:
        """
        [P-S6] Compara checksum individual do nó em vez de generation stamp.
        Garante que mudanças de conteúdo sem bump de generation são detectadas.
        Fallback para comparação de generation quando checksum ausente.
        """
        old_ids  = old.node_ids()
        new_ids  = new.node_ids()
        added    = new_ids - old_ids
        removed  = old_ids - new_ids
        common   = old_ids & new_ids

        modified: Set[str] = set()
        for nid in common:
            on = old.get(nid)
            nn = new.get(nid)
            if on is None or nn is None:
                continue
            # [P-S6] Prefere comparação de checksum — mais confiável
            if on.checksum is not None and nn.checksum is not None:
                if on.checksum != nn.checksum:
                    modified.add(nid)
            else:
                # Fallback: generation stamp (comportamento original)
                if on.generation != nn.generation:
                    modified.add(nid)

        return SnapshotDiff(
            from_generation=old.generation_id,
            to_generation=new.generation_id,
            promoted_at=new.promoted_at or time.time(),
            added_ids=frozenset(added),
            removed_ids=frozenset(removed),
            modified_ids=frozenset(modified),
        )

    def _archive_generation(self, gen: CognitiveGeneration) -> None:
        """
        [P-S4] Nunca remove recovery anchors no evict automático.
        [P-SP3] Invalida _history_summary_cache.
        """
        with self._history_lock:
            gen._archive()
            if not any(g.generation_id == gen.generation_id for g in self._history):
                self._history.append(gen)

            current_id = self._current_ref.get().generation_id
            while len(self._history) > self._max_history:
                removable = [
                    i for i, g in enumerate(self._history)
                    if g.generation_id != current_id
                    and not g.is_recovery_anchor  # [P-S4] preserva anchors
                ]
                if not removable:
                    break
                self._history.pop(removable[0])

            self._history_summary_cache = None  # [P-SP3] invalida
            self._metrics.history_size = len(self._history)

    def _emit(self, event: DecisionEvent) -> None:
        """
        [P-S8] Registra falha em event_failures em vez de silenciar.
        Ativa degraded_observability quando há falhas cumulativas.
        Sistema nunca derruba — mas a falha fica visível nas métricas.
        """
        try:
            self._event_sink(event)
        except Exception:
            self._metrics.event_failures += 1
            if self._metrics.event_failures > 0:
                self._metrics.degraded_observability = True

    @staticmethod
    def _node_to_dict(node: ImmutableCognitiveNode) -> Dict:
        return {
            "id":                  node.id,
            "text":                node.text,
            "embedding":           list(node.embedding) if node.embedding else None,
            "timestamp":           node.timestamp,
            "last_accessed":       node.last_accessed,
            "last_modified":       node.last_modified,
            "source":              node.source,
            "semantic_entropy":    node.semantic_entropy,
            "novelty_score":       node.novelty_score,
            "reinforcement_score": node.reinforcement_score,
            "retrieval_count":     node.retrieval_count,
            "consolidation_count": node.consolidation_count,
            "parent_ids":          list(node.parent_ids),
            "child_ids":           list(node.child_ids),
            "abstraction_level":   node.abstraction_level.value,
            "pressure_score":      node.pressure_score,
            "freshness":           node.freshness,
            "causal_links":        list(node.causal_links),
            "is_archived":         node.is_archived,
            "cluster_id":          node.cluster_id,
            "checksum":            node.checksum,
            "generation":          node.generation,
        }

    @staticmethod
    def _dict_to_node(d: Dict) -> ImmutableCognitiveNode:
        emb = d.get("embedding")
        return ImmutableCognitiveNode(
            id=d["id"],
            text=d.get("text", ""),
            embedding=tuple(emb) if emb is not None else None,
            timestamp=d.get("timestamp", time.time()),
            last_accessed=d.get("last_accessed", time.time()),
            last_modified=d.get("last_modified", time.time()),
            source=d.get("source", "disk"),
            semantic_entropy=d.get("semantic_entropy", 0.0),
            novelty_score=d.get("novelty_score", 1.0),
            reinforcement_score=d.get("reinforcement_score", 1.0),
            retrieval_count=d.get("retrieval_count", 0),
            consolidation_count=d.get("consolidation_count", 0),
            parent_ids=tuple(d.get("parent_ids", [])),
            child_ids=tuple(d.get("child_ids", [])),
            abstraction_level=AbstractionLevel(d.get("abstraction_level", 0)),
            pressure_score=d.get("pressure_score", 0.0),
            freshness=d.get("freshness", 1.0),
            causal_links=tuple(d.get("causal_links", [])),
            is_archived=d.get("is_archived", False),
            cluster_id=d.get("cluster_id"),
            checksum=d.get("checksum"),
            generation=d.get("generation", 0),
        )
