"""
cognitive_scheduler.py — Scheduler cognitivo do EDP v3.
Avalia episódios e produz ações: forget, consolidate, deepen, review, archive.

Correções vs versão em apply_patch.py:
  [FIX-1] _forget: campo 'id' pode não existir → usa .get("id", str(i))
  [FIX-2] _consolidate: np.ix_ pode falhar com cluster de 1 elem → guard len(cl)>=2
  [FIX-3] _archive: getattr em Concept dataclass e dict → unified _text_of helper
  [FIX-4] apply_actions/"deepen": memory.get() pode não existir → try/except por ação
  [FIX-5] _log: silencia IOError para não derrubar o pipeline
  [FIX-6] evaluate: episódios sem campo 'embedding' causam vstack crash → filtrado
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from .config import (
    CONSOLIDATION_SIM_THRESH,
    DECAY_LAMBDA,
    EPISODIC_MEM_SIZE,
    MEMORY_DIR,
)
from .clock import now as _now  # Peça 0.2b — relógio interno robusto
from . import metrics as M

FORGET_THRESH   = 0.08
DEEPEN_THRESH   = 5       # acessos mínimos para deepen
ARCHIVE_THRESH  = 0.75    # score mínimo para arquivar
REVIEW_THRESH   = 0.20    # similaridade máxima para sugerir review
CONSOLIDATE_MIN = 2       # tamanho mínimo de cluster para consolidar
LOG_PATH        = MEMORY_DIR / "cognitive_log.jsonl"


# ── Tipos ─────────────────────────────────────────────────────────────────────

@dataclass
class CognitiveAction:
    action:     str
    target_ids: list
    reason:     str
    priority:   float
    ts:         float = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "action":     self.action,
            "target_ids": self.target_ids,
            "reason":     self.reason,
            "priority":   self.priority,
            "ts":         self.ts,
        }


@dataclass
class SchedulerReport:
    actions:      List[CognitiveAction]
    entries_eval: int
    ts:           float = field(default_factory=_now)

    @property
    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for a in self.actions:
            counts[a.action] = counts.get(a.action, 0) + 1
        return {
            "entries_evaluated": self.entries_eval,
            "total_actions":     len(self.actions),
            "by_action":         counts,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _entry_id(entry: dict, fallback: str) -> str:
    """[FIX-1] id pode não existir."""
    return entry.get("id") or fallback


def _text_of(obj) -> str:
    """[FIX-3] Funciona tanto com dict quanto com Concept dataclass."""
    if isinstance(obj, dict):
        return obj.get("text", "")
    return getattr(obj, "text", "")


def _valid_episodes(episodes: list) -> tuple[list, np.ndarray]:
    """
    [FIX-6] Filtra episódios sem embedding válido.
    Retorna (episódios_válidos, matriz_de_embeddings).
    """
    valid = []
    embs  = []
    for e in episodes:
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
    matrix = np.vstack(embs) if embs else np.empty((0, 1), dtype=np.float32)
    return valid, matrix


# ── Scheduler ─────────────────────────────────────────────────────────────────

class CognitiveScheduler:
    """
    Avalia episódios de memória e produz ações cognitivas priorizadas.

    Uso:
        scheduler = CognitiveScheduler("sessao_id")
        report    = scheduler.evaluate(episodes)
        # report.summary  → resumo das ações
        # report.actions  → lista de CognitiveAction ordenada por prioridade
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id

    # ── API pública ────────────────────────────────────────────────────────

    def evaluate(
        self,
        episodes: list,
        concepts: Optional[list] = None,
        graph_stats: Optional[dict] = None,
    ) -> SchedulerReport:
        if not episodes:
            return SchedulerReport([], 0)

        with M.timer("cognitive_scheduler"):
            actions: List[CognitiveAction] = []
            actions += self._forget(episodes)
            actions += self._consolidate(episodes)
            actions += self._deepen(episodes)
            actions += self._review(episodes)
            actions += self._archive(episodes, concepts or [])
            actions.sort(key=lambda a: a.priority, reverse=True)
            self._log(actions, len(episodes))

        return SchedulerReport(actions, len(episodes))

    def apply_actions(
        self,
        memory,
        actions: List[CognitiveAction],
        dry_run: bool = False,
    ) -> dict:
        res = {"forget": 0, "consolidate": 0, "deepen": 0, "review": 0, "archive": 0}
        for a in actions:
            try:
                if a.action == "forget" and not dry_run:
                    for eid in a.target_ids:
                        if memory.delete(eid):
                            res["forget"] += 1

                elif a.action == "consolidate" and not dry_run:
                    from .consolidation import consolidate
                    consolidate(memory, threshold=CONSOLIDATION_SIM_THRESH)
                    res["consolidate"] += 1

                elif a.action == "deepen" and not dry_run:
                    for eid in a.target_ids:
                        # [FIX-4] memory.get() pode não existir em todas as impl.
                        e = None
                        if hasattr(memory, "get"):
                            e = memory.get(eid)
                        elif hasattr(memory, "episodic"):
                            e = next(
                                (x for x in memory.episodic.entries if x.get("id") == eid),
                                None,
                            )
                        if e:
                            e["score_inicial"] = min(
                                round(e.get("score_inicial", 0.5) + 0.10, 4), 1.0
                            )
                    if hasattr(memory, "save"):
                        memory.save()
                    elif hasattr(memory, "episodic"):
                        memory.episodic.save()
                    res["deepen"] += len(a.target_ids)

                elif a.action == "review":
                    res["review"] += 1

                elif a.action == "archive" and not dry_run:
                    for eid in a.target_ids:
                        e = None
                        if hasattr(memory, "get"):
                            e = memory.get(eid)
                        elif hasattr(memory, "episodic"):
                            e = next(
                                (x for x in memory.episodic.entries if x.get("id") == eid),
                                None,
                            )
                        if e and hasattr(memory, "semantic"):
                            memory.semantic.promote(e)
                    res["archive"] += len(a.target_ids)

            except Exception:
                M.record("scheduler_error", 1.0)

        return res

    # ── Jobs internos ──────────────────────────────────────────────────────

    def _forget(self, episodes: list) -> List[CognitiveAction]:
        out: List[CognitiveAction] = []
        now = _now()
        for i, e in enumerate(episodes):
            eid  = _entry_id(e, str(i))
            dias = (now - e.get("ultimo_acesso", now)) / 86_400.0
            d    = math.exp(-DECAY_LAMBDA * max(dias, 0.0))
            if d < FORGET_THRESH and e.get("prioridade", "media") != "alta":
                out.append(CognitiveAction(
                    "forget", [eid],
                    f"decay={d:.3f}",
                    round(1.0 - d, 4),
                ))
        return out

    def _consolidate(self, episodes: list) -> List[CognitiveAction]:
        valid, embs = _valid_episodes(episodes)
        if len(valid) < CONSOLIDATE_MIN or embs.size == 0:
            return []

        sim     = cosine_similarity(embs)
        n       = len(valid)
        visited = [False] * n
        actions: List[CognitiveAction] = []

        for i in range(n):
            if visited[i]:
                continue
            cl = [i]
            visited[i] = True
            for j in range(i + 1, n):
                if not visited[j] and sim[i, j] >= CONSOLIDATION_SIM_THRESH:
                    cl.append(j)
                    visited[j] = True

            if len(cl) >= CONSOLIDATE_MIN:
                # [FIX-2] np.ix_ requer len >= 2, já garantido aqui
                sub = sim[np.ix_(cl, cl)]
                avg = float(sub.mean())
                actions.append(CognitiveAction(
                    "consolidate",
                    [_entry_id(valid[k], str(k)) for k in cl],
                    f"cluster={len(cl)} avg_sim={avg:.3f}",
                    round(avg * len(cl) / n, 4),
                ))

        return actions

    def _deepen(self, episodes: list) -> List[CognitiveAction]:
        out: List[CognitiveAction] = []
        for i, e in enumerate(episodes):
            eid     = _entry_id(e, str(i))
            acessos = e.get("acessos", 0)
            score   = e.get("score_inicial", 0.5)
            if acessos >= DEEPEN_THRESH and score < 0.60:
                out.append(CognitiveAction(
                    "deepen", [eid],
                    f"acessos={acessos} score={score:.3f}",
                    round(acessos / (acessos + 10), 4),
                ))
        return out

    def _review(self, episodes: list) -> List[CognitiveAction]:
        accessed = [e for e in episodes if e.get("acessos", 0) > 0]
        if len(accessed) < 2:
            return []

        valid, embs = _valid_episodes(accessed)
        if len(valid) < 2 or embs.size == 0:
            return []

        sim = cosine_similarity(embs)
        out: List[CognitiveAction] = []
        for i in range(len(valid)):
            for j in range(i + 1, len(valid)):
                s = float(sim[i, j])
                if s < REVIEW_THRESH:
                    out.append(CognitiveAction(
                        "review",
                        [_entry_id(valid[i], str(i)), _entry_id(valid[j], str(j))],
                        f"sim={s:.3f}",
                        round(1.0 - s, 4),
                    ))
        return out

    def _archive(self, episodes: list, concepts: list) -> List[CognitiveAction]:
        if not concepts:
            return []

        # [FIX-3] funciona com Concept dataclass ou dict
        concept_texts = {_text_of(c)[:60] for c in concepts}
        now = _now()
        out: List[CognitiveAction] = []

        for i, e in enumerate(episodes):
            eid   = _entry_id(e, str(i))
            score = e.get("score_inicial", 0.5)
            dias  = (now - e.get("ultimo_acesso", now)) / 86_400.0
            text  = e.get("text", "")
            covered = any(text[:30] in ct for ct in concept_texts)
            if score >= ARCHIVE_THRESH and dias > 3.0 and covered:
                out.append(CognitiveAction(
                    "archive", [eid],
                    f"score={score:.3f} dias={dias:.1f}",
                    round(score * 0.5, 4),
                ))

        return out

    # ── Log ───────────────────────────────────────────────────────────────

    def _log(self, actions: List[CognitiveAction], n: int) -> None:
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "session_id": self.session_id,
                    "n_entries":  n,
                    "n_actions":  len(actions),
                    "actions":    [a.to_dict() for a in actions[:10]],
                    "ts":         _now(),
                }, ensure_ascii=False) + "\n")
        except OSError:
            pass  # [FIX-5] não derruba o pipeline por falha de I/O

    @staticmethod
    def log_stats() -> dict:
        if not LOG_PATH.exists():
            return {"total_runs": 0}
        try:
            runs = [
                json.loads(l)
                for l in LOG_PATH.read_text(encoding="utf-8").splitlines()
                if l.strip()
            ]
        except Exception:
            return {"total_runs": 0}
        if not runs:
            return {"total_runs": 0}
        total = sum(r.get("n_actions", 0) for r in runs)
        return {
            "total_runs":    len(runs),
            "total_actions": total,
            "avg_per_run":   round(total / len(runs), 2),
            "last_run":      runs[-1].get("ts"),
        }
