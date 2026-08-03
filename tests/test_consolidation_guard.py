"""
Sementes T2 (item C do adendo) — guarda de toxicidade da consolidação
(Fase 5, commit 07dc13e): consolidate_promote_only() não promove conteúdo
quarentenado (answer_class em TOXIC_ANSWER_CLASSES) quando EDP_TOXIC_GUARDS
está ON; com a flag OFF, comportamento byte-idêntico ao pré-mudança.

fix/toxic-guards (30/07/2026): a guarda em consolidation.py:290 passou a ler
EDP_TOXIC_GUARDS em vez de EDP_WRITE_PROVENANCE (ACHADO_FLAG_UNICA_TOXICIDADE.md
do lab_edp — o rollback de escrita não pode desarmar a defesa de leitura sobre
carimbos já persistidos). Os testes "flag off" abaixo migraram o monkeypatch
de EDP_WRITE_PROVENANCE para EDP_TOXIC_GUARDS; a prova de que
EDP_WRITE_PROVENANCE=0 NÃO desliga a guarda está em
test_toxic_guards_flag_separation.py.

7 checks (mesmo placar do commit original): flag ON bloqueia not_found E
disqualification; flag OFF promove os dois como antes; entry sem answer_class
promove nos dois regimes; carimbo sobrevive na cópia semântica pós-promoção
(promote() faz cópia rasa, edp/memory.py:1199).

Contra MemoryStore real (synthetic_store) — fixture sintética, sem dado real.
"""
from __future__ import annotations

from edp.consolidation import consolidate_promote_only


def _seed(store, entry_factory, *, answer_class=None, acessos=5):
    e = entry_factory(acessos=acessos, answer_class=answer_class)
    store.episodic.entries.append(e)
    return e


# ── flag ON: bloqueia as duas classes tóxicas ──────────────────────────────────

def test_flag_on_bloqueia_not_found(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", True, raising=False)
    e = _seed(synthetic_store, entry_factory, answer_class="not_found")

    result = consolidate_promote_only(synthetic_store, promote_threshold=3)

    assert result["blocked_toxic"] == 1
    assert result["promoted"] == 0
    assert e["id"] not in {s.get("id") for s in synthetic_store.semantic.entries}


def test_flag_on_bloqueia_disqualification(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", True, raising=False)
    e = _seed(synthetic_store, entry_factory, answer_class="disqualification")

    result = consolidate_promote_only(synthetic_store, promote_threshold=3)

    assert result["blocked_toxic"] == 1
    assert result["promoted"] == 0
    assert e["id"] not in {s.get("id") for s in synthetic_store.semantic.entries}


# ── flag OFF: promove igual ao comportamento pré-mudança ───────────────────────

def test_flag_off_promove_not_found(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", False, raising=False)
    e = _seed(synthetic_store, entry_factory, answer_class="not_found")

    result = consolidate_promote_only(synthetic_store, promote_threshold=3)

    assert result["blocked_toxic"] == 0
    assert result["promoted"] == 1
    assert e["id"] in {s.get("id") for s in synthetic_store.semantic.entries}


def test_flag_off_promove_disqualification(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", False, raising=False)
    e = _seed(synthetic_store, entry_factory, answer_class="disqualification")

    result = consolidate_promote_only(synthetic_store, promote_threshold=3)

    assert result["blocked_toxic"] == 0
    assert result["promoted"] == 1
    assert e["id"] in {s.get("id") for s in synthetic_store.semantic.entries}


# ── entry sem answer_class promove normalmente nos dois regimes ───────────────

def test_sem_answer_class_promove_com_flag_on(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", True, raising=False)
    e = _seed(synthetic_store, entry_factory, answer_class=None)

    result = consolidate_promote_only(synthetic_store, promote_threshold=3)

    assert result["blocked_toxic"] == 0
    assert result["promoted"] == 1
    assert e["id"] in {s.get("id") for s in synthetic_store.semantic.entries}


def test_sem_answer_class_promove_com_flag_off(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", False, raising=False)
    e = _seed(synthetic_store, entry_factory, answer_class=None)

    result = consolidate_promote_only(synthetic_store, promote_threshold=3)

    assert result["blocked_toxic"] == 0
    assert result["promoted"] == 1
    assert e["id"] in {s.get("id") for s in synthetic_store.semantic.entries}


# ── carimbo sobrevive à cópia rasa da promoção (promote(): entry = dict(entry)) ─

def test_carimbo_sobrevive_na_copia_semantica(synthetic_store, entry_factory, monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_TOXIC_GUARDS", False, raising=False)
    e = _seed(synthetic_store, entry_factory, answer_class="not_found")

    consolidate_promote_only(synthetic_store, promote_threshold=3)

    promoted = next(s for s in synthetic_store.semantic.entries if s.get("id") == e["id"])
    assert promoted.get("answer_class") == "not_found"
    assert promoted.get("layer") == "semantic"
