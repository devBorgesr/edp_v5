"""
tests/test_profiles_selector.py — ProfileSelector: recomendação de perfil.

Cada teste usa um ProfileRegistry isolado (arquivo JSON em tmp_path), nunca
o singleton global, para não vazar estado entre testes.
"""
from __future__ import annotations

import pytest

from edp.profiles.models import Profile, ProfileStatus
from edp.profiles.registry import ProfileRegistry
from edp.profiles.selector import ProfileSelector


@pytest.fixture
def registry(tmp_path):
    return ProfileRegistry(path=tmp_path / "profiles.json")


def test_select_prefers_least_used_active_profile(registry):
    registry.add(Profile(id="a", nome="A", contador_uso_semanal=5, contador_uso_diario=2))
    registry.add(Profile(id="b", nome="B", contador_uso_semanal=1, contador_uso_diario=9))
    registry.add(Profile(id="c", nome="C", contador_uso_semanal=5, contador_uso_diario=0))

    chosen = ProfileSelector(registry).select(strategy="balanced")

    assert chosen.id == "b"  # menor contador_uso_semanal vence primeiro


def test_select_breaks_ties_with_daily_counter(registry):
    registry.add(Profile(id="a", nome="A", contador_uso_semanal=3, contador_uso_diario=2))
    registry.add(Profile(id="b", nome="B", contador_uso_semanal=3, contador_uso_diario=0))

    chosen = ProfileSelector(registry).select(strategy="balanced")

    assert chosen.id == "b"


def test_select_ignores_paused_profiles(registry):
    registry.add(Profile(id="a", nome="A", status=ProfileStatus.PAUSADO, contador_uso_semanal=0))
    registry.add(Profile(id="b", nome="B", status=ProfileStatus.ATIVO, contador_uso_semanal=99))

    chosen = ProfileSelector(registry).select(strategy="balanced")

    assert chosen.id == "b"  # "a" tem uso menor mas está pausado


def test_select_returns_none_when_no_active_profiles(registry):
    registry.add(Profile(id="a", nome="A", status=ProfileStatus.PAUSADO))

    assert ProfileSelector(registry).select() is None


def test_select_never_used_profile_preferred_over_used_one(registry):
    registry.add(Profile(id="used", nome="Used", data_ultimo_uso="2026-08-01T00:00:00Z"))
    registry.add(Profile(id="fresh", nome="Fresh", data_ultimo_uso=None))

    chosen = ProfileSelector(registry).select(strategy="least_recent")

    assert chosen.id == "fresh"

    chosen = ProfileSelector(registry).select(strategy="balanced")
    assert chosen.id == "fresh"


def test_select_unknown_strategy_raises(registry):
    registry.add(Profile(id="a", nome="A"))

    with pytest.raises(ValueError):
        ProfileSelector(registry).select(strategy="nonexistent")
