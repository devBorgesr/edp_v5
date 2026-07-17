"""
Dívida #41 (Hardening Fase 2, T2) — thresholds do pressure_governor.

edp/runtime/pressure_governor.py: CRITICAL_GB/WARNING_GB lidos de
EDP_PRESSURE_CRITICAL_GB/EDP_PRESSURE_WARNING_GB no import do módulo
(defaults recalibrados 0.30/0.60GB para a realidade API-only — ver
docstring do módulo). Dois checks: (1) env vars são respeitadas quando o
módulo é (re)carregado; (2) classificação de nível correta nos 3 regimes
(NORMAL/WARNING/CRITICAL), com psutil mockado (sem depender de RAM real
da máquina que roda o teste).
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import edp.runtime.pressure_governor as pg


class _FakePsutil:
    """Substitui psutil.virtual_memory() com valores controlados — sem
    depender da RAM real da máquina que roda o teste."""

    def __init__(self, available_gb: float, total_gb: float = 4.1, percent: float = 50.0):
        self._vm = SimpleNamespace(
            available=int(available_gb * (1024 ** 3)),
            total=int(total_gb * (1024 ** 3)),
            percent=percent,
        )

    def virtual_memory(self):
        return self._vm


def _governor_with_fake_ram(available_gb: float) -> pg.MemoryPressureGovernor:
    gov = pg.MemoryPressureGovernor()
    gov._psutil = _FakePsutil(available_gb)
    return gov


# ── Env vars respeitadas (defaults recalibrados, Dívida #41) ──────────────────

def test_defaults_recalibrados_api_only():
    """Sem env var setada, os defaults são os novos (0.30/0.60), não os
    antigos (1.2/2.0) dimensionados para inferência local."""
    assert pg.CRITICAL_GB == 0.30
    assert pg.WARNING_GB == 0.60


def test_env_vars_respeitadas(monkeypatch):
    monkeypatch.setenv("EDP_PRESSURE_CRITICAL_GB", "0.5")
    monkeypatch.setenv("EDP_PRESSURE_WARNING_GB", "1.5")
    importlib.reload(pg)
    try:
        assert pg.CRITICAL_GB == 0.5
        assert pg.WARNING_GB == 1.5
    finally:
        # Restaura env ANTES de recarregar de novo, senão o reload de
        # limpeza herdaria os valores monkeypatchados (undo() só reverte
        # no teardown do fixture, depois que esta função já retornou).
        monkeypatch.undo()
        importlib.reload(pg)


def test_env_var_rollback_para_valores_antigos(monkeypatch):
    """Rollback documentado no módulo: setar os valores antigos (cenário
    de inferência local) continua funcionando via env, sem mudar código."""
    monkeypatch.setenv("EDP_PRESSURE_CRITICAL_GB", "1.2")
    monkeypatch.setenv("EDP_PRESSURE_WARNING_GB", "2.0")
    importlib.reload(pg)
    try:
        assert pg.CRITICAL_GB == 1.2
        assert pg.WARNING_GB == 2.0
    finally:
        monkeypatch.undo()
        importlib.reload(pg)


# ── Classificação de nível nos 3 regimes (psutil mockado) ─────────────────────

def test_regime_normal(monkeypatch):
    monkeypatch.setattr(pg, "CRITICAL_GB", 0.30, raising=False)
    monkeypatch.setattr(pg, "WARNING_GB", 0.60, raising=False)
    gov = _governor_with_fake_ram(available_gb=1.45)  # topo da oscilação observada
    reading = gov.read(force=True)
    assert reading.level == pg.PressureLevel.NORMAL


def test_regime_warning(monkeypatch):
    monkeypatch.setattr(pg, "CRITICAL_GB", 0.30, raising=False)
    monkeypatch.setattr(pg, "WARNING_GB", 0.60, raising=False)
    gov = _governor_with_fake_ram(available_gb=0.45)  # entre os dois pisos
    reading = gov.read(force=True)
    assert reading.level == pg.PressureLevel.WARNING


def test_regime_critical(monkeypatch):
    monkeypatch.setattr(pg, "CRITICAL_GB", 0.30, raising=False)
    monkeypatch.setattr(pg, "WARNING_GB", 0.60, raising=False)
    gov = _governor_with_fake_ram(available_gb=0.28)  # piso da oscilação observada
    reading = gov.read(force=True)
    assert reading.level == pg.PressureLevel.CRITICAL


def test_regime_critical_nao_e_mais_permanente_com_defaults_novos():
    """Reproduz a faixa de oscilação observada pelo pesquisador
    (0.28-1.45GB) contra os defaults NOVOS (não os antigos 1.2/2.0, que
    mantinham CRITICAL permanente nessa faixa inteira)."""
    baixo  = _governor_with_fake_ram(available_gb=0.28).read(force=True).level
    alto   = _governor_with_fake_ram(available_gb=1.45).read(force=True).level
    assert baixo == pg.PressureLevel.CRITICAL
    assert alto == pg.PressureLevel.NORMAL
    assert baixo != alto  # com os defaults antigos, os dois eram CRITICAL
