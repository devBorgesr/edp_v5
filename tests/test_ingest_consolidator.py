"""
tests/test_ingest_consolidator.py — consolidação (resumo + promoção p/
memória semântica) de uma conversa do live feed.

get_memory/get_runtime são monkeypatchados no MÓDULO consumidor
(edp.ingest.consolidator), não na fonte (edp.runtime) — são `from ..runtime
import ...` no topo do módulo, vinculados no namespace do próprio módulo no
momento do import (mesma convenção documentada em test_health_check.py e em
conftest.py/_CLOCK_BOUND_MODULES).
"""
from __future__ import annotations

import edp.ingest.consolidator as consolidator_mod
from edp.ingest.consolidator import _heuristic_consolidate, consolidate_session


class _FakeRuntimeDisconnected:
    def is_connected(self) -> bool:
        return False


def test_heuristic_consolidate_sem_entries_retorna_none(synthetic_store):
    assert _heuristic_consolidate(synthetic_store, "conv_x") is None


def test_heuristic_consolidate_gera_resumo_e_persiste(synthetic_store):
    synthetic_store.add(
        "thinking sobre RAG e embeddings", score=0.35,
        source="live_feed", confidence=0.3,
    )
    synthetic_store.add(
        "tool_call: buscar documentacao", score=0.5,
        source="live_feed", confidence=0.3,
    )

    result = _heuristic_consolidate(synthetic_store, "conv_x")

    assert result is not None
    assert result["method"] == "heuristic"
    assert "RAG" in result["summary"] or "embeddings" in result["summary"]
    assert result["entry_id"]

    entry = synthetic_store.get(result["entry_id"])
    assert entry["source_type"] == "session_summary"
    assert entry["session_id"] == "conv_x"
    assert entry["topic_tag"] == result["label"]
    # a entry mutada deve ser a MESMA persistida em episodic.entries, não
    # uma cópia órfã (EpisodicMemory.add() copia o dict recebido).
    persisted = [e for e in synthetic_store.episodic.entries if e["id"] == result["entry_id"]]
    assert len(persisted) == 1
    assert persisted[0]["source_type"] == "session_summary"


def test_consolidate_session_usa_fallback_heuristico_sem_llm(monkeypatch, synthetic_store):
    monkeypatch.setattr(consolidator_mod, "get_memory", lambda key: synthetic_store)
    monkeypatch.setattr(consolidator_mod, "get_runtime", lambda key: _FakeRuntimeDisconnected())

    synthetic_store.add(
        "thinking sobre bitcoin mining", score=0.35,
        source="live_feed", confidence=0.3,
    )

    result = consolidate_session("conv_y")

    assert result is not None
    assert result["method"] == "heuristic"

    promoted = [e for e in synthetic_store.semantic.entries if e.get("id") == result["entry_id"]]
    assert len(promoted) == 1
    assert promoted[0]["layer"] == "semantic"


def test_consolidate_session_memory_invalida_retorna_none(monkeypatch):
    monkeypatch.setattr(consolidator_mod, "get_memory", lambda key: {"__init_error__": "boom"})
    assert consolidate_session("conv_z") is None
