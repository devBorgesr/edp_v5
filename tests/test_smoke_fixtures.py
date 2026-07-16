"""Smoke tests for the T1 skeleton itself (fixtures wire up correctly)."""
import numpy as np


def test_fake_embeddings_deterministic(fake_embeddings):
    from edp.embeddings import embed_one
    a = embed_one("mesmo texto")
    b = embed_one("mesmo texto")
    assert np.allclose(a, b)
    assert a.shape == (384,)


def test_synthetic_store_isolated(synthetic_store, isolated_base_dir):
    assert synthetic_store.session_id.startswith("test-")
    assert str(isolated_base_dir) in str(synthetic_store._cognitive_view.episodic.path.parent.parent)


def test_entry_factory(entry_factory):
    e = entry_factory(answer_class="not_found")
    assert e["answer_class"] == "not_found"
    assert e["embedding"].shape == (384,)


def test_frozen_clock_skeleton(frozen_clock):
    import edp.clock as clock_mod
    assert clock_mod.now() == frozen_clock
