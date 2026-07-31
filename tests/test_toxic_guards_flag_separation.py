"""
fix/toxic-guards (30/07/2026) — regressão da separação EDP_TOXIC_GUARDS /
EDP_WRITE_PROVENANCE. Evidência: lab_edp docs/VEREDITO_EXP018.md e
docs/ACHADO_FLAG_UNICA_TOXICIDADE.md.

Um teste por mudança do fix quádruplo:
  T1/T2 — com as duas flags ON (default), comportamento byte-idêntico ao
          pré-mudança (o guard continua protegendo os três pontos de leitura).
  T2    — EDP_TOXIC_GUARDS=0 desliga as três leituras (piso, exclusão híbrida,
          guarda de consolidação); EDP_WRITE_PROVENANCE=0 sozinho NÃO desliga
          nenhuma delas — é o achado do lab sendo pinado.
  T3    — consolidate() não promove tóxico em nenhum dos dois branches de
          promoção (pós-merge e entry-sozinha).
  T4    — merge_cluster() com uma entry tóxica no cluster devolve a fundida
          com answer_class tóxico (sem isto, o T3 é cego — exp018 H3).
"""
from __future__ import annotations

import edp.config as edp_config
from edp.consolidation import consolidate, consolidate_promote_only, merge_cluster
from edp.embeddings import embed_one


# ── T1/T2: ambas as flags ON — byte-idêntico ao baseline ──────────────────────

def test_ambas_flags_on_guarda_continua_ativa(synthetic_store, entry_factory, monkeypatch):
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_WRITE_PROVENANCE", True, raising=False)

    query_text = "pergunta identica para ambas as entradas no piso"
    q_emb = embed_one(query_text)
    e_clean = entry_factory(embedding=q_emb.copy(), answer_class=None)
    e_toxic = entry_factory(embedding=q_emb.copy(), answer_class="not_found")
    synthetic_store.episodic.entries = [e_clean, e_toxic]

    results = synthetic_store.episodic.retrieve(q_emb, top_k=10, min_score=0.0)
    score_clean = next(r["ranking_score"] for r in results if r.get("id") == e_clean["id"])
    score_toxic = next(r["ranking_score"] for r in results if r.get("id") == e_toxic["id"])
    assert score_toxic < score_clean  # piso aplicado — mesmo comportamento pré-fix

    e_disq = entry_factory(acessos=5, answer_class="disqualification")
    synthetic_store.episodic.entries = [e_disq]
    result = consolidate_promote_only(synthetic_store, promote_threshold=3)
    assert result["blocked_toxic"] == 1
    assert result["promoted"] == 0


# ── T2: EDP_TOXIC_GUARDS=0 desliga as três leituras ────────────────────────────

def test_toxic_guards_off_desliga_piso(synthetic_store, entry_factory, monkeypatch):
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", False, raising=False)
    monkeypatch.setattr(edp_config, "EDP_WRITE_PROVENANCE", True, raising=False)

    query_text = "pergunta identica para testar piso desligado"
    q_emb = embed_one(query_text)
    e_clean = entry_factory(embedding=q_emb.copy(), answer_class=None)
    e_toxic = entry_factory(embedding=q_emb.copy(), answer_class="not_found")
    synthetic_store.episodic.entries = [e_clean, e_toxic]

    results = synthetic_store.episodic.retrieve(q_emb, top_k=10, min_score=0.0)
    score_clean = next(r["ranking_score"] for r in results if r.get("id") == e_clean["id"])
    score_toxic = next(r["ranking_score"] for r in results if r.get("id") == e_toxic["id"])
    assert score_toxic == score_clean


def test_toxic_guards_off_desliga_exclusao_hibrida(synthetic_store, monkeypatch):
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", False, raising=False)
    monkeypatch.setattr(edp_config, "EDP_WRITE_PROVENANCE", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", True, raising=False)

    query_text = "conteudo exclusivo sobre exclusao hibrida desligada de teste"
    added = synthetic_store.add(query_text, score=0.7, prioridade="alta")
    e_toxic = next(e for e in synthetic_store.episodic.entries if e["id"] == added["id"])
    e_toxic["answer_class"] = "disqualification"
    synthetic_store.add("conteudo totalmente diferente sobre outro assunto qualquer", score=0.5)

    results = synthetic_store.retrieve(query_text, top_k=5)
    assert e_toxic["id"] in {r.get("id") for r in results}


def test_toxic_guards_off_desliga_guarda_consolidacao(synthetic_store, entry_factory, monkeypatch):
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", False, raising=False)
    monkeypatch.setattr(edp_config, "EDP_WRITE_PROVENANCE", True, raising=False)

    e = entry_factory(acessos=5, answer_class="not_found")
    synthetic_store.episodic.entries = [e]

    result = consolidate_promote_only(synthetic_store, promote_threshold=3)
    assert result["blocked_toxic"] == 0
    assert result["promoted"] == 1
    assert e["id"] in {s.get("id") for s in synthetic_store.semantic.entries}


# ── T2 (achado pinado): EDP_WRITE_PROVENANCE=0 sozinho NÃO desliga nenhuma ────

def test_write_provenance_off_nao_desliga_piso(synthetic_store, entry_factory, monkeypatch):
    monkeypatch.setattr(edp_config, "EDP_WRITE_PROVENANCE", False, raising=False)
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", True, raising=False)  # default, explícito

    query_text = "pergunta identica para testar rollback de escrita isolado"
    q_emb = embed_one(query_text)
    e_clean = entry_factory(embedding=q_emb.copy(), answer_class=None)
    e_toxic = entry_factory(embedding=q_emb.copy(), answer_class="not_found")
    synthetic_store.episodic.entries = [e_clean, e_toxic]

    results = synthetic_store.episodic.retrieve(q_emb, top_k=10, min_score=0.0)
    score_clean = next(r["ranking_score"] for r in results if r.get("id") == e_clean["id"])
    score_toxic = next(r["ranking_score"] for r in results if r.get("id") == e_toxic["id"])
    assert score_toxic < score_clean  # piso continua valendo — rollback de escrita não desarma


def test_write_provenance_off_nao_desliga_exclusao_hibrida(synthetic_store, monkeypatch):
    monkeypatch.setattr(edp_config, "EDP_WRITE_PROVENANCE", False, raising=False)
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", True, raising=False)

    query_text = "conteudo exclusivo sobre rollback de escrita nao desarma exclusao"
    added = synthetic_store.add(query_text, score=0.7, prioridade="alta")
    e_toxic = next(e for e in synthetic_store.episodic.entries if e["id"] == added["id"])
    e_toxic["answer_class"] = "disqualification"
    synthetic_store.add("conteudo totalmente diferente sobre outro assunto qualquer", score=0.5)

    results = synthetic_store.retrieve(query_text, top_k=5)
    assert e_toxic["id"] not in {r.get("id") for r in results}  # exclusão sobrevive ao rollback


def test_write_provenance_off_nao_desliga_guarda_consolidacao(synthetic_store, entry_factory, monkeypatch):
    monkeypatch.setattr(edp_config, "EDP_WRITE_PROVENANCE", False, raising=False)
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", True, raising=False)

    e = entry_factory(acessos=5, answer_class="not_found")
    synthetic_store.episodic.entries = [e]

    result = consolidate_promote_only(synthetic_store, promote_threshold=3)
    assert result["blocked_toxic"] == 1
    assert result["promoted"] == 0
    assert e["id"] not in {s.get("id") for s in synthetic_store.semantic.entries}


# ── T3: consolidate() não promove tóxico em nenhum dos dois branches ──────────

def test_consolidate_nao_promove_toxico_branch_solo(synthetic_store, entry_factory, monkeypatch):
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", True, raising=False)

    e = entry_factory(acessos=5, answer_class="not_found")
    synthetic_store.episodic.entries = [e]

    result = consolidate(synthetic_store, promote_threshold=3)
    assert result["promoted"] == 0
    assert e["id"] not in {s.get("id") for s in synthetic_store.semantic.entries}


def test_consolidate_nao_promove_toxico_branch_merge(synthetic_store, entry_factory, monkeypatch):
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", True, raising=False)

    q_emb = embed_one("duas entradas identicas para forcar merge no cluster")
    e1 = entry_factory(embedding=q_emb.copy(), acessos=2, answer_class="not_found")
    e2 = entry_factory(embedding=q_emb.copy(), acessos=2, answer_class=None)
    synthetic_store.episodic.entries = [e1, e2]

    result = consolidate(synthetic_store, promote_threshold=3)
    assert result["merged"] == 1
    assert result["promoted"] == 0  # cluster somou acessos=4 (>=3) mas herdou tóxico — H3
    assert synthetic_store.semantic.entries == []


# ── T4: merge_cluster propaga answer_class tóxico conservadoramente ──────────

def test_merge_cluster_propaga_answer_class_toxico(entry_factory):
    q_emb = embed_one("cluster com uma entry toxica e outra limpa")
    e_clean = entry_factory(embedding=q_emb.copy(), answer_class=None)
    e_toxic = entry_factory(embedding=q_emb.copy(), answer_class="disqualification")

    merged = merge_cluster([e_clean, e_toxic], [0, 1])

    assert merged is not None
    assert merged.get("answer_class") == "disqualification"
    assert merged.get("merged_from") == 2
