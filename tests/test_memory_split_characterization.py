"""Fase 4, T2 — caracterização pré-split de edp/memory.py.

Rede de segurança específica do corte do T3: comportamento mais invisível
do arquivo (fronteira de sessão por gap de tempo) e o roundtrip de save()
dos dois scopes (cognitive + sprint) — ambos fáceis de quebrar movendo
código sem querer.
"""
import edp.memory as memory_mod


# ── T2a: fronteira de sessão por gap (session_marker) ──────────────────────

def test_session_gap_marker_new_session_on_large_gap(synthetic_store, frozen_clock, entry_factory):
    ep = synthetic_store.episodic
    ep.add(entry_factory(timestamp=frozen_clock()))
    marker1 = ep.entries[-1]["session_marker"]
    assert marker1

    frozen_clock.advance(memory_mod.SESSION_GAP_THRESHOLD_SEC + 1)
    ep.add(entry_factory(timestamp=frozen_clock()))
    marker2 = ep.entries[-1]["session_marker"]

    assert marker2 and marker2 != marker1


def test_session_gap_marker_same_session_on_small_gap(synthetic_store, frozen_clock, entry_factory):
    ep = synthetic_store.episodic
    ep.add(entry_factory(timestamp=frozen_clock()))
    marker1 = ep.entries[-1]["session_marker"]
    assert marker1

    frozen_clock.advance(memory_mod.SESSION_GAP_THRESHOLD_SEC - 1)
    ep.add(entry_factory(timestamp=frozen_clock()))
    marker2 = ep.entries[-1]["session_marker"]

    assert marker2 == marker1


# ── T2b: save() persiste os dois scopes, reload íntegro ────────────────────

def test_save_roundtrip_both_scopes(synthetic_store, entry_factory):
    store = synthetic_store
    store.cognitive.episodic.add(entry_factory(text="cognitive entry"))
    store.sprint.episodic.add(entry_factory(text="sprint entry"))
    store.save()

    reloaded = memory_mod.MemoryStore(store.session_id)

    cog_texts = [e["text"] for e in reloaded.cognitive.episodic.entries]
    spr_texts = [e["text"] for e in reloaded.sprint.episodic.entries]

    assert cog_texts == ["cognitive entry"]
    assert spr_texts == ["sprint entry"]
