"""
exp017 Fase 1 (T4) — testes de integração de EDP_RETRIEVE_DEDUP /
EDP_RETRIEVE_RANDOM_DROP contra MemoryStore real (synthetic_store).

Complementa tests/test_exp017_dedup_ranked.py (função pura _dedup_ranked) e
tests/test_flag_off_byte_identical.py (OFF == baseline). Aqui: flag ON com
duplicatas reais no store sintético (dup_id + dup_hash, refill), a invariante
de quarentena (o teste mais importante da fase — RELATORIO_F1T1_EXP017.md,
item c) e reprodutibilidade do controle-reserva.

Contrato: PRE_REGISTRO_EXP017.md (com ERRATA + E6) + RELATORIO_F1T1_EXP017.md.
"""
from __future__ import annotations

import re

from edp.embeddings import embed_one


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").strip().casefold())


# ── ON: dup_id=0 E dup_hash=0, k preenchido por refill (híbrido, default) ────

def test_dedup_on_hibrido_colapsa_id_e_hash_com_refill(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_DEDUP", True, raising=False)

    query_text = "consulta compartilhada para o teste de dedup com refill"
    q_emb = embed_one(query_text)

    # fenômeno D: mesma entry (mesmo ID) em episódica e semântica
    e_d = entry_factory(embedding=q_emb.copy(), text=query_text + " original D")
    e_d_sem = dict(e_d)

    # fenômeno A-no-resultado: hash igual, IDs distintos
    e_a1 = entry_factory(embedding=q_emb.copy(), text="Q: oi\nA: oi tudo bem")
    e_a2 = entry_factory(embedding=q_emb.copy(), text="q: OI\na: OI TUDO BEM")

    fillers = [
        entry_factory(embedding=q_emb.copy(), text=f"conteudo unico de preenchimento {i}")
        for i in range(5)
    ]

    synthetic_store.episodic.entries = [e_d, e_a1, e_a2] + fillers
    synthetic_store.semantic.entries = [e_d_sem]

    results = synthetic_store.retrieve(query_text, top_k=5)

    ids = [r["id"] for r in results]
    assert len(ids) == 5, "k deve ser preenchido por refill (7 unicos disponiveis >= 5)"
    assert len(set(ids)) == len(ids), "dup_id deve ser 0"
    hashes = [_norm(r["text"]) for r in results]
    assert len(set(hashes)) == len(hashes), "dup_hash deve ser 0"


def test_dedup_on_cosine_colapsa_hash_com_refill(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", False, raising=False)
    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_DEDUP", True, raising=False)

    query_text = "consulta compartilhada para o teste de dedup cosine com refill"
    q_emb = embed_one(query_text)

    e_a1 = entry_factory(embedding=q_emb.copy(), text="Q: oi\nA: oi tudo bem")
    e_a2 = entry_factory(embedding=q_emb.copy(), text="q: OI\na: OI TUDO BEM")
    fillers = [
        entry_factory(embedding=q_emb.copy(), text=f"conteudo unico de preenchimento cosine {i}")
        for i in range(4)
    ]
    synthetic_store.episodic.entries = [e_a1, e_a2] + fillers

    results = synthetic_store.retrieve(query_text, top_k=5, min_score=0.0)

    ids = [r["id"] for r in results]
    assert len(ids) == 5
    hashes = [_norm(r["text"]) for r in results]
    assert len(set(hashes)) == len(hashes)  # dup_hash = 0 — refill trouxe o 5o filler


# ── Invariante de quarentena — o teste mais importante da fase ──────────────

def test_invariante_quarentena_nunca_aparece_via_refill_hibrido(synthetic_store, entry_factory, monkeypatch):
    """Caminho híbrido: exclusão de answer_class tóxico roda ANTES da
    indexação (store.py._hybrid_index, :~1455) — estruturalmente garantida,
    refill só puxa de um índice já sem entries tóxicas."""
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_WRITE_PROVENANCE", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_DEDUP", True, raising=False)

    query_text = "consulta unica da invariante de quarentena no hibrido"
    q_emb = embed_one(query_text)

    e_nf   = entry_factory(embedding=q_emb.copy(), text=query_text + " toxica nf", answer_class="not_found")
    e_disq = entry_factory(embedding=q_emb.copy(), text=query_text + " toxica disq", answer_class="disqualification")
    # bloco de duplicatas força o refill a "cavar" fundo no ranking —
    # se o refill vazasse quarentena, seria aqui que apareceria
    dup_block = [
        dict(entry_factory(embedding=q_emb.copy(), text="Q: oi\nA: oi tudo bem"), id=f"dup-{i}")
        for i in range(6)
    ]
    filler = entry_factory(embedding=q_emb.copy(), text="unico conteudo de preenchimento final")

    synthetic_store.episodic.entries = [e_nf, e_disq] + dup_block + [filler]

    results = synthetic_store.retrieve(query_text, top_k=10)

    ids = {r["id"] for r in results}
    assert e_nf["id"] not in ids
    assert e_disq["id"] not in ids


def test_invariante_quarentena_nunca_aparece_via_refill_cosine_caso_limpo(synthetic_store, entry_factory, monkeypatch):
    """Caminho cosine, entry tóxica SEM cópia semântica: o piso NOT_FOUND_
    FLOOR (0.05) combinado com min_score default (0.20) filtra a entry na
    própria fase de scoring — nunca entra em `scored`/`final`, então o
    refill não pode reintroduzi-la (não há o que reintroduzir)."""
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", False, raising=False)
    monkeypatch.setattr(edp_config, "EDP_WRITE_PROVENANCE", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_DEDUP", True, raising=False)

    query_text = "consulta cosine da invariante de quarentena caso limpo"
    q_emb = embed_one(query_text)

    e_toxic = entry_factory(embedding=q_emb.copy(), text=query_text + " toxica", answer_class="not_found")
    dup_block = [
        dict(entry_factory(embedding=q_emb.copy(), text="Q: oi\nA: oi tudo bem"), id=f"dup-{i}")
        for i in range(6)
    ]
    synthetic_store.episodic.entries = [e_toxic] + dup_block

    results = synthetic_store.retrieve(query_text, top_k=10)  # min_score default 0.20

    ids = {r["id"] for r in results}
    assert e_toxic["id"] not in ids


def test_gap_preexistente_cosine_copia_semantica_dedup_nao_agrava(synthetic_store, entry_factory, monkeypatch):
    """Achado do T1 (RELATORIO_F1T1_EXP017.md, item c): SemanticMemory.
    retrieve() não lê answer_class (dívida documentada, semantic.py:8-13) —
    uma cópia semântica (fenômeno D) de uma entry tóxica escapa do piso no
    caminho cosine HOJE, independente de dedup (fora de escopo do exp017:
    scoring congelado). Este teste NÃO afirma a invariante — confirma que o
    dedup não piora o vazamento pré-existente: mesmo conjunto com a flag
    ON e OFF."""
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", False, raising=False)
    monkeypatch.setattr(edp_config, "EDP_WRITE_PROVENANCE", True, raising=False)

    query_text = "consulta cosine sobre vazamento da copia semantica"
    q_emb = embed_one(query_text)

    e_toxic = entry_factory(embedding=q_emb.copy(), text=query_text, answer_class="not_found")
    e_toxic_sem = dict(e_toxic)  # mesma ID — cópia semântica
    synthetic_store.episodic.entries = [e_toxic]
    synthetic_store.semantic.entries = [e_toxic_sem]

    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_DEDUP", False, raising=False)
    ids_off = {r["id"] for r in synthetic_store.retrieve(query_text, top_k=10)}

    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_DEDUP", True, raising=False)
    ids_on = {r["id"] for r in synthetic_store.retrieve(query_text, top_k=10)}

    assert ids_off == ids_on  # dedup não altera o gap pré-existente


# ── random_pareado (controle-reserva) — |resultado|=k, reprodutível por seed ─

def test_random_pareado_hibrido_tamanho_e_reprodutivel(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_RANDOM_DROP", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_SHUFFLE_SEED", "20260719", raising=False)

    # mix realista (não 100% duplicado — d precisa caber no refill disponível
    # além da janela top-k, mesma restrição que o dedup honesto tem):
    # 4 duplicatas de hash + 4 conteúdos únicos, k=5.
    query_text = "consulta unica para testar o controle reserva reprodutivel"
    q_emb = embed_one(query_text)
    dup_block = [
        dict(entry_factory(embedding=q_emb.copy(), text="Q: oi\nA: oi tudo bem"), id=f"dup-{i}")
        for i in range(4)
    ]
    fillers = [
        entry_factory(embedding=q_emb.copy(), text=f"conteudo unico hibrido {i}")
        for i in range(4)
    ]
    synthetic_store.episodic.entries = dup_block + fillers

    r1 = synthetic_store.retrieve(query_text, top_k=5)
    r2 = synthetic_store.retrieve(query_text, top_k=5)

    assert len(r1) == 5
    assert len(r2) == 5
    assert [r["id"] for r in r1] == [r["id"] for r in r2]  # mesma query -> mesma seleção


def test_random_pareado_cosine_tamanho_e_reprodutivel(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", False, raising=False)
    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_RANDOM_DROP", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_SHUFFLE_SEED", "20260719", raising=False)

    query_text = "consulta cosine unica para testar o controle reserva reprodutivel"
    q_emb = embed_one(query_text)
    dup_block = [
        dict(entry_factory(embedding=q_emb.copy(), text="Q: oi\nA: oi tudo bem"), id=f"dup-{i}")
        for i in range(4)
    ]
    fillers = [
        entry_factory(embedding=q_emb.copy(), text=f"conteudo unico cosine {i}")
        for i in range(4)
    ]
    synthetic_store.episodic.entries = dup_block + fillers

    r1 = synthetic_store.retrieve(query_text, top_k=5, min_score=0.0)
    r2 = synthetic_store.retrieve(query_text, top_k=5, min_score=0.0)

    assert len(r1) == 5
    assert len(r2) == 5
    assert [r["id"] for r in r1] == [r["id"] for r in r2]


# ── Guard de exclusividade mútua ─────────────────────────────────────────────

def test_guard_prioriza_off_quando_duas_flags_ligadas(synthetic_store, entry_factory, monkeypatch, caplog):
    import logging
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", False, raising=False)
    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_DEDUP", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_SHUFFLE", True, raising=False)  # configuração inválida

    query_text = "consulta para testar o guard de flags mutuamente exclusivas"
    q_emb = embed_one(query_text)
    e1 = entry_factory(embedding=q_emb.copy(), text="Q: oi\nA: oi tudo bem")
    e2 = entry_factory(embedding=q_emb.copy(), text="q: OI\na: OI TUDO BEM")
    synthetic_store.episodic.entries = [e1, e2]

    with caplog.at_level(logging.ERROR, logger="edp.memory"):
        results = synthetic_store.retrieve(query_text, top_k=5, min_score=0.0)

    # guard prioriza OFF: duplicata por hash NÃO é colapsada
    ids = {r["id"] for r in results}
    assert ids == {e1["id"], e2["id"]}
    assert any("mutuamente exclusivas" in r.message for r in caplog.records)
