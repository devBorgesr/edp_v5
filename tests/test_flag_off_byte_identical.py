"""
Sementes T2 (item C do adendo) — EDP_TOXIC_GUARDS=0 é byte-idêntico ao
comportamento pré-exp012: answer_class tóxico deixa de ter QUALQUER efeito
no gate (nem piso no cosine, nem exclusão do híbrido, nem bloqueio de
promoção) — mesmo padrão de rede de segurança usado para EDP_HYBRID_RETRIEVAL/
EDP_CTX_SLOTS (edp/config.py). Contra MemoryStore real (synthetic_store).

fix/toxic-guards (30/07/2026): as três leituras migraram de EDP_WRITE_PROVENANCE
para EDP_TOXIC_GUARDS (ACHADO_FLAG_UNICA_TOXICIDADE.md do lab_edp) — os
testes abaixo migraram o monkeypatch junto. A prova de que EDP_WRITE_
PROVENANCE=0 NÃO desliga mais estas três leituras está em
test_toxic_guards_flag_separation.py.
"""
from __future__ import annotations

from edp.consolidation import consolidate_promote_only
from edp.embeddings import embed_one


def test_flag_off_cosine_sem_piso(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", False, raising=False)

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
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", False, raising=False)
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
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", False, raising=False)

    e_not_found = entry_factory(acessos=5, answer_class="not_found")
    e_disq = entry_factory(acessos=5, answer_class="disqualification")
    e_clean = entry_factory(acessos=5, answer_class=None)
    synthetic_store.episodic.entries = [e_not_found, e_disq, e_clean]

    result = consolidate_promote_only(synthetic_store, promote_threshold=3)

    assert result["blocked_toxic"] == 0
    assert result["promoted"] == 3
    semantic_ids = {s.get("id") for s in synthetic_store.semantic.entries}
    assert {e_not_found["id"], e_disq["id"], e_clean["id"]} <= semantic_ids


# ── exp017 Fase 0 — EDP_RETRIEVE_SHUFFLE=0 (default) é byte-idêntico ──────────
# ao comportamento pré-exp017: ordem do top-k entregue ao builder preservada
# (RELATORIO_T1_EXP017.md, ponto (i) = llm_adapter.py:2334). Mesmo padrão de
# rede de segurança das flags acima — instrumento de medição, nunca produção.

class _FakeMemory:
    """Stub mínimo: só o que _retrieve_context toca sem `.episodic`."""

    def __init__(self, results):
        self._results = results
        self.calls = 0

    def retrieve(self, query, top_k=5, min_score=0.20):
        self.calls += 1
        return list(self._results)


def test_flag_off_shuffle_preserva_ordem_do_topk(monkeypatch):
    from edp.llm_adapter import EDPRuntime

    fixed_order = [
        {"id": f"id-{i}", "text": f"memoria numero {i} sobre o assunto",
         "ranking_score": 0.9 - i * 0.01}
        for i in range(5)
    ]

    rt = EDPRuntime.__new__(EDPRuntime)
    rt._memory = _FakeMemory(fixed_order)
    rt._operational_mode = "cognitive"
    rt._co_occurrence = None
    rt.session_id = "test-session"

    blocks, hits = rt._retrieve_context("pergunta sobre o assunto")

    # Sem janela imediata/bloco atual (stub sem .episodic) — só a âncora
    # temporal + os 5 blocos de retrieval, na MESMA ordem de fixed_order.
    textos_retrieval = [b for b in blocks if "memoria numero" in b]
    esperado = [e["text"] for e in fixed_order]
    obtido = [t.split("] ", 1)[-1] if "] " in t else t for t in textos_retrieval]
    assert obtido == esperado


def test_flag_off_shuffle_e_default():
    import edp.config as edp_config
    assert edp_config.EDP_RETRIEVE_SHUFFLE is False


# ── exp017 Fase 1 (T4) — EDP_RETRIEVE_DEDUP=0 / EDP_RETRIEVE_RANDOM_DROP=0 são
# byte-idênticas ao comportamento pré-exp017 Fase 1, nos DOIS caminhos: cosine
# preserva duplicatas por hash (o dedup-por-ID pré-existente do merge,
# store.py, é comportamento ANTERIOR ao exp017 — RELATORIO_F1T1_EXP017.md,
# item d); híbrido preserva duplicatas por ID (fenômeno D intacto, sem
# colapso). Mesma rede de segurança das flags acima.

def test_flag_off_dedup_e_random_drop_sao_default():
    import edp.config as edp_config
    assert edp_config.EDP_RETRIEVE_DEDUP is False
    assert edp_config.EDP_RETRIEVE_RANDOM_DROP is False


def test_flag_off_dedup_cosine_preserva_duplicata_por_hash(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", False, raising=False)
    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_DEDUP", False, raising=False)
    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_RANDOM_DROP", False, raising=False)

    query_text = "consulta para testar preservacao de duplicatas por hash no cosine"
    q_emb = embed_one(query_text)

    e1 = entry_factory(embedding=q_emb.copy(), text="Q: oi\nA: oi tudo bem")
    e2 = entry_factory(embedding=q_emb.copy(), text="q: OI\na: OI TUDO BEM")  # normaliza igual a e1
    synthetic_store.episodic.entries = [e1, e2]

    results = synthetic_store.retrieve(query_text, top_k=5, min_score=0.0)
    ids = {r["id"] for r in results}
    assert ids == {e1["id"], e2["id"]}  # ambos presentes — sem colapso por hash


def test_flag_off_dedup_hibrido_preserva_duplicata_por_id(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_DEDUP", False, raising=False)
    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_RANDOM_DROP", False, raising=False)

    query_text = "consulta unica para testar fenomeno d preservado no hibrido"
    q_emb = embed_one(query_text)

    e_epi = entry_factory(embedding=q_emb.copy(), text=query_text)
    e_sem = dict(e_epi)  # mesma ID — cópia semântica (fenômeno D)
    synthetic_store.episodic.entries = [e_epi]
    synthetic_store.semantic.entries = [e_sem]

    results = synthetic_store.retrieve(query_text, top_k=5)
    ids = [r["id"] for r in results]
    assert ids.count(e_epi["id"]) == 2  # as duas cópias aparecem — sem colapso
