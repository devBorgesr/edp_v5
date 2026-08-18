"""
edp.runtime.lineage — Lineage Tracking (Commit B).

Commit B (Sessão 4, 09-10/06/2026):
  Cada resposta sabe de onde veio. Captura, por turno conversacional:
  quais memórias informaram a resposta (entry_ids + scores), qual
  modelo gerou, qual quality_score recebeu, em qual sessão temporal.

Princípio aplicado:
  Soberania Progressiva — LineageTracker é dono do seu estado.
  Gera response_id próprio (uuid), NÃO depende de correlation_id
  do Pareto (que é thread-local gerado dentro de stream_chat e
  não é visível de forma confiável no WS handler async).
  correlation_id é capturado SE disponível (campo opcional).

Decisão arquitetural (investigação 09/06/2026):
  - Pareto memory_accessed NÃO grava entry_ids (só n_returned +
    top_score) → "estruturar o que o Pareto já tem" era premissa
    FALSA. Lineage captura direto no WS handler, onde retrieve,
    modelo, quality e resposta convivem no mesmo escopo.
  - memory.retrieve() retorna dicts com id, ranking_score,
    timestamp, source_type — fonte real dos source_entries.

Persistência:
  $EDP_BASE_DIR/sessions/<session>_cognitive/lineage.jsonl
  Append-only, rotação MAX_LINEAGE_ENTRIES (5000).

Feature flag: EDP_LINEAGE=true|false (default true).

Registro (schema):
  {
    "response_id":     "uuid4",
    "correlation_id":  "turn_xxx" | null,
    "session_id":      "default",
    "session_marker":  "abc123" | null,
    "source_entries":  [{"entry_id", "score", "source_type", "timestamp"}],
    "model_used":      "claude-sonnet-4-6" | null,
    "quality_score":   0.87 | null,
    "quality_verdict": "APPROVED" | null,
    "n_sources":       int,
    "timestamp":       epoch float
  }
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

from ..clock import now as _now


logger = logging.getLogger("edp.runtime.lineage")


# ── Constantes ───────────────────────────────────────────────────────────────

MAX_LINEAGE_ENTRIES = 5000   # rotação (mesmo padrão do CHI)
MAX_SOURCES_PER_RECORD = 20  # paranoia: nunca gravar lista gigante


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class SourceEntry:
    """Uma memória que informou a resposta."""
    entry_id:    str
    score:       float
    source_type: str = "unknown"
    timestamp:   Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LineageRecord:
    """Registro completo de origem de uma resposta."""
    response_id:     str
    session_id:      str
    source_entries:  List[dict] = field(default_factory=list)
    correlation_id:  Optional[str]   = None
    session_marker:  Optional[str]   = None
    model_used:      Optional[str]   = None
    quality_score:   Optional[float] = None
    quality_verdict: Optional[str]   = None
    n_sources:       int             = 0
    timestamp:       float           = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False,
                          separators=(",", ":"))


# ── LineageTracker ────────────────────────────────────────────────────────────

class LineageTracker:
    """
    Constrói e persiste registros de lineage por resposta.

    Uso típico (WS handler, após resposta + quality):
        tracker = get_lineage_tracker()
        record = tracker.build(
            session_id=session_id,
            retrieved=retrieved,          # list[dict] do memory.retrieve()
            model_used=model_name,
            quality_score=qresult.score if qresult else None,
            quality_verdict=qresult.verdict if qresult else None,
            session_marker=marker,
        )
        tracker.persist(record)
        # opcional: enviar record.to_dict() ao cliente via send_json
    """

    def __init__(self) -> None:
        self._write_lock = threading.Lock()

    # ── Construção ──────────────────────────────────────────────────────────

    def build(
        self,
        session_id: str,
        retrieved: Optional[List[dict]] = None,
        model_used: Optional[str] = None,
        quality_score: Optional[float] = None,
        quality_verdict: Optional[str] = None,
        session_marker: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> LineageRecord:
        """
        Monta LineageRecord a partir do que o WS handler já tem em escopo.

        Args:
            session_id: sessão do turno
            retrieved:  output bruto de memory.retrieve() — dicts com
                        id, ranking_score, source_type, timestamp.
                        None ou [] → record sem sources (turno trivial).
            model_used: modelo final que gerou a resposta (pós-router)
            quality_score/quality_verdict: do Commit A, se disponível
            session_marker: marker da sessão temporal, se disponível

        Returns:
            LineageRecord pronto para persist().
        """
        sources: List[dict] = []
        if retrieved:
            for r in retrieved[:MAX_SOURCES_PER_RECORD]:
                if not isinstance(r, dict):
                    continue
                eid = r.get("id")
                if not eid:
                    continue
                try:
                    score = float(r.get("ranking_score", 0.0))
                except (TypeError, ValueError):
                    score = 0.0
                src = SourceEntry(
                    entry_id=str(eid),
                    score=round(score, 4),
                    source_type=str(r.get("source_type") or "unknown"),
                    timestamp=r.get("timestamp"),
                )
                sources.append(src.to_dict())

        # correlation_id: melhor esforço via thread-local do Pareto.
        # Pode ser None (WS async vs thread do stream) — campo opcional.
        # 18/08: o argumento explícito vence o thread-local. O thread-local é
        # lido na thread do handler, e o id é gravado numa thread do pool
        # (websocket.py:882 -> run_in_executor) — falha SEMPRE, não às vezes.
        # Passar explicitamente é o único caminho que atravessa o executor.
        if not correlation_id:
            try:
                from .pareto_store import get_current_correlation_id
                correlation_id = get_current_correlation_id()
            except Exception:
                pass

        return LineageRecord(
            response_id=str(uuid.uuid4()),
            session_id=session_id,
            source_entries=sources,
            correlation_id=correlation_id,
            session_marker=session_marker,
            model_used=model_used,
            quality_score=quality_score,
            quality_verdict=quality_verdict,
            n_sources=len(sources),
            timestamp=_now(),
        )

    # ── Persistência ────────────────────────────────────────────────────────

    def persist(self, record: LineageRecord) -> bool:
        """
        Append em lineage.jsonl + rotação. Nunca levanta exceção
        (lineage é observabilidade — não pode derrubar o turno).

        Returns: True se persistiu, False se falhou.
        """
        path = self._lineage_path(record.session_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("[lineage] mkdir falhou: %s", e)
            return False

        with self._write_lock:
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(record.to_jsonl() + "\n")
                self._rotate_if_needed(path)
                return True
            except Exception as e:
                logger.warning("[lineage] persist falhou: %s", e)
                return False

    def get_history(
        self, session_id: str = "default", last_n: int = 30
    ) -> List[dict]:
        """Últimos N registros (para dashboard/endpoint futuro)."""
        path = self._lineage_path(session_id)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            return [json.loads(ln) for ln in lines[-last_n:] if ln.strip()]
        except Exception as e:
            logger.warning("[lineage] get_history falhou: %s", e)
            return []

    def get_by_response_id(
        self, response_id: str, session_id: str = "default"
    ) -> Optional[dict]:
        """Busca registro específico (para endpoint GET /lineage/{id})."""
        path = self._lineage_path(session_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                for ln in f:
                    if not ln.strip():
                        continue
                    rec = json.loads(ln)
                    if rec.get("response_id") == response_id:
                        return rec
            return None
        except Exception as e:
            logger.warning("[lineage] get_by_response_id falhou: %s", e)
            return None

    def find_by_prefix(
        self, prefix: str, session_id: str = "default", limit: int = 5
    ) -> List[dict]:
        """Registros cujo response_id começa com `prefix`.

        α (13/06/2026): ergonomia de debug — o log mostra só o prefixo de
        8 chars (ex: 'response_id=5b56a95b'). Um UUID completo casa apenas
        consigo mesmo (sem colisão entre UUIDs de 36 chars), então este
        método cobre tanto prefixo quanto ID completo. O endpoint decide
        0→404, 1→retorna, >1→409. `limit` corta a varredura cedo: 2 já
        basta para o endpoint detectar ambiguidade.
        """
        path = self._lineage_path(session_id)
        if not path.exists():
            return []
        matches: List[dict] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for ln in f:
                    if not ln.strip():
                        continue
                    rec = json.loads(ln)
                    rid = rec.get("response_id", "")
                    if isinstance(rid, str) and rid.startswith(prefix):
                        matches.append(rec)
                        if len(matches) >= limit:
                            break
            return matches
        except Exception as e:
            logger.warning("[lineage] find_by_prefix falhou: %s", e)
            return matches

    # ── Internos ────────────────────────────────────────────────────────────

    def _rotate_if_needed(self, path: Path) -> None:
        """Mantém últimas MAX_LINEAGE_ENTRIES (rotação atômica)."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) <= MAX_LINEAGE_ENTRIES:
                return
            keep = lines[-MAX_LINEAGE_ENTRIES:]
            tmp = path.with_suffix(".jsonl.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(keep)
            os.replace(tmp, path)
            logger.info(
                "[lineage] rotacionado | mantidas=%d removidas=%d",
                len(keep), len(lines) - len(keep),
            )
        except Exception as e:
            logger.warning("[lineage] rotação falhou: %s", e)

    @staticmethod
    def _lineage_path(session_id: str) -> Path:
        base = Path(os.environ.get("EDP_BASE_DIR", "C:/edp_data"))
        return base / "sessions" / f"{session_id}_cognitive" / "lineage.jsonl"


# ── Singleton ────────────────────────────────────────────────────────────────

_tracker: Optional[LineageTracker] = None
_tracker_lock = threading.Lock()


def get_lineage_tracker() -> LineageTracker:
    """Singleton thread-safe."""
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = LineageTracker()
    return _tracker


# ── Feature flag ─────────────────────────────────────────────────────────────

def is_lineage_enabled() -> bool:
    """Verifica EDP_LINEAGE (default true)."""
    val = os.environ.get("EDP_LINEAGE", "true").strip().lower()
    return val in ("true", "1", "yes", "on")
