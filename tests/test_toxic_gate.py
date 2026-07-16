"""
Sementes T2 (item C do adendo) — mecanismo de gate do exp012/exp016 contra
MemoryStore REAL (synthetic_store): peso-piso NOT_FOUND_FLOOR=0.05 no cosine
puro (EpisodicMemory.retrieve) + exclusão total do índice híbrido
(MemoryStore._hybrid_index, default EDP_HYBRID_RETRIEVAL=1).

Fixture sintética — sem gt_*.csv, sem dado real.
"""
from __future__ import annotations

import pytest

from edp.config import NOT_FOUND_FLOOR
from edp.embeddings import embed_one


def test_piso_not_found_floor_no_cosine_puro(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_WRITE_PROVENANCE", True, raising=False)

    query_text = "pergunta idêntica para ambas as entradas"
    q_emb = embed_one(query_text)

    e_clean = entry_factory(embedding=q_emb.copy(), answer_class=None)
    e_toxic = entry_factory(embedding=q_emb.copy(), answer_class="not_found")
    synthetic_store.episodic.entries = [e_clean, e_toxic]

    results = synthetic_store.episodic.retrieve(q_emb, top_k=10, min_score=0.0)

    assert e_clean["id"] in {r.get("id") for r in results}
    assert e_toxic["id"] in {r.get("id") for r in results}

    score_clean = next(r["ranking_score"] for r in results if r.get("id") == e_clean["id"])
    score_toxic = next(r["ranking_score"] for r in results if r.get("id") == e_toxic["id"])

    # Único fator diferente entre as duas entries é nf_floor (memory.py:723-724)
    # — a razão entre os scores deve ser exatamente NOT_FOUND_FLOOR.
    assert score_clean > 0
    assert score_toxic == pytest.approx(score_clean * NOT_FOUND_FLOOR, rel=1e-3)


def test_exclusao_total_do_indice_hibrido(synthetic_store, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_WRITE_PROVENANCE", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", True, raising=False)

    query_text = "conteudo exclusivo sobre desqualificacao toxica de teste"
    added = synthetic_store.add(query_text, score=0.7, prioridade="alta")
    # EpisodicMemory.add() faz cópia rasa (edp/memory.py:571) — o dict
    # retornado por MemoryStore.add() NÃO é o objeto armazenado. Precisa
    # buscar de volta no store para marcar o carimbo na entry real.
    e_toxic = next(e for e in synthetic_store.episodic.entries if e["id"] == added["id"])
    e_toxic["answer_class"] = "disqualification"
    synthetic_store.add("conteudo totalmente diferente sobre outro assunto qualquer", score=0.5)

    # Query EXATA do texto tóxico — se não fosse excluído, seria o hit #1.
    results = synthetic_store.retrieve(query_text, top_k=5)

    ids = {r.get("id") for r in results}
    assert e_toxic["id"] not in ids
