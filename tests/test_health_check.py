"""
tests/test_health_check.py — /health real (Hardening Fase 3, T2).

Antes desta tarefa, /health retornava {"status": "ok"} fixo — nunca
refletia o estado real do sistema. Agora expõe boot_state (já mantido
pelo lifespan em edp/api/main.py, só não estava lido aqui), leitura do
memory_store da sessão 'default' e config de provider — SEM chamada de
rede (não usa provider.validate(), que é quem de fato pinga a API).

App de teste minimalista: só o router de health, sem o lifespan real de
edp.api.main (que faria preload de embedding e boot de sessão 'default'
de verdade — fora do escopo hermético de um teste unitário do endpoint).

Os imports dentro de health() são tardios (`from ...runtime import
get_memory` dentro da função, não no topo do módulo) — cada chamada
relê o atributo atual do módulo de origem, então monkeypatch nos
módulos de origem (edp.runtime, edp.llm.providers) é respeitado
normalmente, sem o problema de "from x import y" vinculado no import
do módulo (ver nota em conftest.py/T3a).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import edp.runtime as runtime_mod
import edp.runtime.boot_state as boot_state_mod
import edp.llm.providers as providers_mod
from edp.runtime.boot_state import BootStateMachine, RuntimeState
from edp.api.routes.health import router as health_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(health_router)
    return app


@pytest.fixture
def client():
    return TestClient(_make_app())


@pytest.fixture(autouse=True)
def fresh_boot_state(monkeypatch):
    """Singleton global de boot_state isolado por teste."""
    fresh = BootStateMachine()
    monkeypatch.setattr(boot_state_mod, "_state_machine", fresh, raising=False)
    return fresh


def _to_ready(boot: BootStateMachine) -> None:
    boot.transition(RuntimeState.WARMING, "test_setup")
    boot.transition(RuntimeState.READY, "test_setup")


# ── Cenário saudável ────────────────────────────────────────────────────────

def test_health_ok_quando_tudo_saudavel(client, fresh_boot_state, monkeypatch):
    _to_ready(fresh_boot_state)

    monkeypatch.setattr(runtime_mod, "get_memory", lambda sid: object(), raising=False)
    monkeypatch.setattr(providers_mod, "list_providers", lambda: ["anthropic"], raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake")

    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["boot_state"] == "ready"
    assert body["checks"]["memory_store"]["ok"] is True
    assert body["checks"]["provider"]["ok"] is True


# ── Cenários degradados ──────────────────────────────────────────────────────

def test_health_503_quando_boot_ainda_warming(client, fresh_boot_state, monkeypatch):
    fresh_boot_state.transition(RuntimeState.WARMING, "test_setup")

    monkeypatch.setattr(runtime_mod, "get_memory", lambda sid: object(), raising=False)
    monkeypatch.setattr(providers_mod, "list_providers", lambda: ["anthropic"], raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake")

    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["boot_state"] == "warming"


def test_health_degraded_quando_memory_store_falha(client, fresh_boot_state, monkeypatch):
    _to_ready(fresh_boot_state)

    monkeypatch.setattr(
        runtime_mod, "get_memory",
        lambda sid: {"__init_error__": "RuntimeError: disco cheio"},
        raising=False,
    )
    monkeypatch.setattr(providers_mod, "list_providers", lambda: ["anthropic"], raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake")

    resp = client.get("/health")
    # DEGRADED continua 200 (contrato documentado em boot_state.py) —
    # sistema aceita requests, mas sinaliza o componente com problema.
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["memory_store"]["ok"] is False
    assert "disco cheio" in body["checks"]["memory_store"]["error"]


def test_health_degraded_quando_sem_provider_configurado(client, fresh_boot_state, monkeypatch):
    _to_ready(fresh_boot_state)

    monkeypatch.setattr(runtime_mod, "get_memory", lambda sid: object(), raising=False)
    monkeypatch.setattr(providers_mod, "list_providers", lambda: ["anthropic"], raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["provider"]["ok"] is False
    assert body["checks"]["provider"]["has_anthropic_key_in_env"] is False


def test_health_degraded_quando_boot_state_machine_degraded(client, fresh_boot_state, monkeypatch):
    fresh_boot_state.transition(RuntimeState.WARMING, "test_setup")
    fresh_boot_state.mark_component("embeddings", healthy=False, error="preload failed", critical=True)
    fresh_boot_state.transition(RuntimeState.DEGRADED, "embeddings_failed")

    monkeypatch.setattr(runtime_mod, "get_memory", lambda sid: object(), raising=False)
    monkeypatch.setattr(providers_mod, "list_providers", lambda: ["anthropic"], raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake")

    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["boot_state"] == "degraded"
    assert body["checks"]["boot_state"]["components"]["embeddings"]["healthy"] is False
