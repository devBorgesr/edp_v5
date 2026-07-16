"""
Sementes T2 (item C do adendo) — EDP_WRITE_PROVENANCE=0 é byte-idêntico ao
comportamento pré-exp012: answer_class tóxico deixa de ter QUALQUER efeito
no gate (nem piso no cosine, nem exclusão do híbrido, nem bloqueio de
promoção) — mesmo padrão de rede de segurança usado para EDP_HYBRID_RETRIEVAL/
EDP_CTX_SLOTS (edp/config.py). Contra MemoryStore real (synthetic_store).
"""
from __future__ import annotations

from edp.consolidation import consolidate_promote_only
from edp.embeddings import embed_one


def test_flag_off_cosine_sem_piso(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_WRITE_PROVENANCE", False, raising=False)

    query_text = "pergunta idêntica para ambas as entradas"
    q_emb = embed_one(query_text)

    e_clean = entry_factory(embedding=q_emb.copy(), answer_class=None)
    e_toxic = entry_factory(embedding=q_emb.copy(), answer_class="not_found")
    synthetic_store.episodic.entries = [e_clean, e_toxic]

    results = synthetic_store.episodic.retrieve(q_emb, top_k=10, min_score=0.0)
    score_clean = next(r["ranking_score"] for r in results if r.get("id") == e_clean["id"])
    score_toxic = next(r["ranking_score"] for r in results if r.get("id") == e_toxic["id"])

    # Com a flag OFF, nf_floor==1.0 sempre — scores idênticos.
    assert score_toxic == score_clean


def test_flag_off_hibrido_nao_exclui(synthetic_store, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_WRITE_PROVENANCE", False, raising=False)
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", True, raising=False)

    query_text = "conteudo exclusivo sobre desqualificacao toxica de teste"
    added = synthetic_store.add(query_text, score=0.7, prioridade="alta")
    e_toxic = next(e for e in synthetic_store.episodic.entries if e["id"] == added["id"])
    e_toxic["answer_class"] = "disqualification"
    synthetic_store.add("conteudo totalmente diferente sobre outro assunto qualquer", score=0.5)

    results = synthetic_store.retrieve(query_text, top_k=5)

    ids = {r.get("id") for r in results}
    assert e_toxic["id"] in ids


def test_flag_off_consolidacao_promove_tudo(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_WRITE_PROVENANCE", False, raising=False)

    e_not_found = entry_factory(acessos=5, answer_class="not_found")
    e_disq = entry_factory(acessos=5, answer_class="disqualification")
    e_clean = entry_factory(acessos=5, answer_class=None)
    synthetic_store.episodic.entries = [e_not_found, e_disq, e_clean]

    result = consolidate_promote_only(synthetic_store, promote_threshold=3)

    assert result["blocked_toxic"] == 0
    assert result["promoted"] == 3
    semantic_ids = {s.get("id") for s in synthetic_store.semantic.entries}
    assert {e_not_found["id"], e_disq["id"], e_clean["id"]} <= semantic_ids
