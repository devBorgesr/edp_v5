"""
Sementes T2 (item C do adendo) — write_provenance.classify() congelado
(Fase 3 R4 + exp016 DISQ-v1). Regras copiadas VERBATIM do módulo real,
sem reimplementação — cada assert chama edp.write_provenance.classify()
diretamente. Fixture sintética, sem gt_*.csv, sem dado real.

12 checks: 4 padrões DISQ isolados, precedência DISQ>R4, NEG isolado
(estrato A), KW isolado (estrato A), nenhum sinal, estrato B (n_mem==0 e
n_mem>0), guarda EDP_CTX_SLOTS=OFF descarta n_mem_prompt, prov não-dict.
"""
from __future__ import annotations

from edp.write_provenance import classify


# ── DISQ v1 — 4 padrões isolados (exp016, incondicional) ──────────────────────

def test_disq_pattern0_fabricado_por_mim():
    assert classify({}, "isso é real?", "Essa resposta foi fabricada por mim.") == "disqualification"


def test_disq_pattern1_eu_inventei():
    assert classify({}, "isso é real?", "Eu inventei essa história.") == "disqualification"


def test_disq_pattern2_nao_e_memoria_sua():
    assert classify({}, "isso é real?", "Isso não é uma memória sua.") == "disqualification"


def test_disq_pattern3_nao_corresponde_a_pergunta_sua():
    assert classify({}, "isso é real?", "Isso não corresponde a nenhuma pergunta sua.") == "disqualification"


# ── Precedência: DISQ vence R4 mesmo quando NEG também dispara ────────────────

def test_disq_precedencia_sobre_neg():
    resposta = "Eu inventei isso — não encontro registro disso na nossa conversa."
    assert classify({}, "oi", resposta) == "disqualification"


# ── R4 estrato A (backlog, prov sem n_mem_prompt) ──────────────────────────────

def test_neg_isolado_estrato_a():
    resposta = "Não encontro registro disso na nossa conversa."
    assert classify({}, "oi, tudo bem?", resposta) == "not_found"


def test_kw_isolado_estrato_a():
    query = "Podemos continuar de onde paramos?"
    resposta = "Claro, vamos lá."
    assert classify({}, query, resposta) == "not_found"


def test_nenhum_sinal_retorna_none():
    assert classify({}, "oi, tudo bem?", "Tudo ótimo, como posso ajudar?") is None


# ── R4 estrato B (proveniência exata via n_mem_prompt) ─────────────────────────

def test_estrato_b_n_mem_zero_quarentena():
    prov = {"n_mem_prompt": 0}
    resposta = "Não encontro registro disso na nossa conversa."
    assert classify(prov, "oi", resposta) == "not_found"


def test_estrato_b_n_mem_positivo_nao_quarentena():
    prov = {"n_mem_prompt": 3}
    resposta = "Não encontro registro disso na nossa conversa."
    assert classify(prov, "oi", resposta) is None


# ── Guarda de defesa: EDP_CTX_SLOTS OFF descarta n_mem_prompt (cai em A) ───────

def test_guarda_ctx_slots_off_descarta_n_mem_prompt(monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_CTX_SLOTS", False, raising=False)
    # n_mem_prompt>0 normalmente absolveria (estrato B); com slots OFF o sinal
    # não é confiável — descartado, cai no estrato A (R4 puro) e quarentena.
    prov = {"n_mem_prompt": 5}
    resposta = "Não encontro registro disso na nossa conversa."
    assert classify(prov, "oi", resposta) == "not_found"


# ── prov não-dict — retorno defensivo None ─────────────────────────────────────

def test_prov_none_retorna_none():
    assert classify(None, "oi", "Não encontro registro disso.") is None
