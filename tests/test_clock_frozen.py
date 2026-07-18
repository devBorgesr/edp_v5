"""Fase 4, T1d — determinismo do frozen_clock (injeção completa).

Verifica que o congelamento propaga para edp.clock.now() E para todo `_now`
já vinculado por import em módulos vivos (memory.py, pipeline.py, etc.), e
que o avanço manual (.advance()) move o tempo de todos ao mesmo tempo.
"""
import edp.clock as clock_mod
import edp.memory as memory_mod
import edp.pipeline as pipeline_mod


def test_frozen_clock_freezes_direct_now(frozen_clock):
    a = clock_mod.now()
    b = clock_mod.now()
    assert a == b == frozen_clock


def test_frozen_clock_stable_across_calls(frozen_clock):
    readings = [clock_mod.now() for _ in range(5)]
    assert len(set(readings)) == 1


def test_frozen_clock_propagates_to_bound_now_names(frozen_clock):
    # memory.py e pipeline.py fazem `from .clock import now as _now` —
    # o nome fica vinculado no namespace deles, não só em edp.clock.
    assert memory_mod._now() == frozen_clock
    assert pipeline_mod._now() == frozen_clock
    assert memory_mod._now() == clock_mod.now() == pipeline_mod._now()


def test_frozen_clock_manual_advance_moves_all_bound_names(frozen_clock):
    before = clock_mod.now()
    frozen_clock.advance(15_000.0)
    after_direct = clock_mod.now()
    after_memory = memory_mod._now()

    assert after_direct == before + 15_000.0
    assert after_memory == after_direct


def test_frozen_clock_set_moves_all_bound_names(frozen_clock):
    frozen_clock.set(2_000_000_000.0)
    assert clock_mod.now() == 2_000_000_000.0
    assert memory_mod._now() == 2_000_000_000.0
