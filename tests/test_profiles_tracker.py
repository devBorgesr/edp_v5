"""
tests/test_profiles_tracker.py — UsageTracker: incremento e reset manuais
de contadores, e persistência do ProfileRegistry que os sustenta.
"""
from __future__ import annotations

import pytest

from edp.profiles.models import Profile, ProfileStatus
from edp.profiles.registry import ProfileRegistry
from edp.profiles.tracker import UsageTracker


@pytest.fixture
def registry(tmp_path):
    return ProfileRegistry(path=tmp_path / "profiles.json")


def test_log_usage_increments_counters_and_sets_timestamp(registry):
    registry.add(Profile(id="a", nome="A"))

    profile = UsageTracker(registry).log_usage("a", success=True)

    assert profile.contador_uso_diario == 1
    assert profile.contador_uso_semanal == 1
    assert profile.data_ultimo_uso is not None


def test_log_usage_twice_accumulates(registry):
    registry.add(Profile(id="a", nome="A"))
    tracker = UsageTracker(registry)

    tracker.log_usage("a")
    profile = tracker.log_usage("a")

    assert profile.contador_uso_diario == 2
    assert profile.contador_uso_semanal == 2


def test_log_usage_unknown_profile_raises(registry):
    with pytest.raises(KeyError):
        UsageTracker(registry).log_usage("nao_existe")


def test_reset_daily_zeroes_only_daily_counter(registry):
    registry.add(Profile(id="a", nome="A", contador_uso_diario=3, contador_uso_semanal=7))
    tracker = UsageTracker(registry)

    changed = tracker.reset_daily()

    profile = registry.get("a")
    assert changed == 1
    assert profile.contador_uso_diario == 0
    assert profile.contador_uso_semanal == 7


def test_reset_weekly_zeroes_only_weekly_counter(registry):
    registry.add(Profile(id="a", nome="A", contador_uso_diario=3, contador_uso_semanal=7))
    tracker = UsageTracker(registry)

    changed = tracker.reset_weekly()

    profile = registry.get("a")
    assert changed == 1
    assert profile.contador_uso_diario == 3
    assert profile.contador_uso_semanal == 0


def test_reset_daily_reports_zero_when_nothing_to_change(registry):
    registry.add(Profile(id="a", nome="A", contador_uso_diario=0))

    assert UsageTracker(registry).reset_daily() == 0


def test_usage_persists_across_registry_reload(tmp_path):
    path = tmp_path / "profiles.json"
    registry = ProfileRegistry(path=path)
    registry.add(Profile(id="a", nome="A"))
    UsageTracker(registry).log_usage("a")

    reloaded = ProfileRegistry(path=path)
    profile = reloaded.get("a")

    assert profile is not None
    assert profile.contador_uso_diario == 1
    assert profile.status == ProfileStatus.ATIVO


def test_set_status_updates_and_persists(tmp_path):
    path = tmp_path / "profiles.json"
    registry = ProfileRegistry(path=path)
    registry.add(Profile(id="a", nome="A"))

    registry.set_status("a", ProfileStatus.PAUSADO)
    reloaded = ProfileRegistry(path=path)

    assert reloaded.get("a").status == ProfileStatus.PAUSADO
