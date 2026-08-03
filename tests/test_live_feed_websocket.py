"""
tests/test_live_feed_websocket.py — WebSocket /stream + API HTTP de consulta.

Mirrors tests/test_health_check.py's padrão de app mínima + TestClient: uma
FastAPI() só com os routers sob teste, sem o lifespan real de edp.api.main.
"""
from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import edp.config as edp_config
import edp.ingest.session_index as session_index_mod
import edp.ingest.websocket_receiver as wsr_mod
from edp.api.routes.live_feed import router as live_feed_http_router
from edp.ingest.websocket_receiver import router as live_feed_ws_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(live_feed_http_router)
    app.include_router(live_feed_ws_router)
    return app


@pytest.fixture
def client():
    return TestClient(_make_app())


@pytest.fixture(autouse=True)
def isolated_live_feed_paths(isolated_base_dir, monkeypatch):
    """
    isolated_base_dir redireciona BASE_DIR/MEMORY_DIR, mas LIVE_FEED_LOG/
    LIVE_FEED_INDEX_PATH são constantes derivadas 1x no import de config.py
    — precisam de patch explícito para não vazar entre testes/produção.

    session_index._instance e o logger do live feed são singletons de
    processo (sobrevivem entre testes) — resetados aqui p/ cada teste
    partir de um estado limpo e ler os paths patchados acima.
    """
    base = isolated_base_dir
    monkeypatch.setattr(edp_config, "LIVE_FEED_LOG", base / "live_feed.log", raising=False)
    monkeypatch.setattr(edp_config, "LIVE_FEED_INDEX_PATH", base / "live_feed_index.json", raising=False)
    monkeypatch.setattr(edp_config, "EDP_LIVE_FEED_TOKEN", "", raising=False)
    monkeypatch.setattr(session_index_mod, "_instance", None, raising=False)
    wsr_mod.logger.handlers = []


def _thinking_event(conversation_id="conv_test1", **overrides):
    event = {
        "type": "thinking",
        "timestamp": 1_800_000_000.0,
        "conversation_id": conversation_id,
        "data": {"text": "pensando sobre RAG"},
    }
    event.update(overrides)
    return event


# ── ping/pong ────────────────────────────────────────────────────────────────

def test_ping_pong(client):
    with client.websocket_connect("/stream") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


# ── evento inválido não derruba a conexão ──────────────────────────────────

def test_evento_invalido_retorna_erro_e_mantem_conexao(client):
    with client.websocket_connect("/stream") as ws:
        ws.send_json({"type": "thinking"})  # falta conversation_id/timestamp/data
        resp = ws.receive_json()
        assert resp["type"] == "error"

        # conexão segue viva: um evento válido depois funciona normalmente
        ws.send_json(_thinking_event())
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


# ── evento válido é armazenado e recuperável via HTTP ──────────────────────

def test_evento_valido_e_recuperavel_via_http(client):
    with client.websocket_connect("/stream") as ws:
        ws.send_json(_thinking_event(conversation_id="conv_http1"))
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

    resp = client.get("/memory/session/conv_http1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["events"][0]["event_type"] == "thinking"
    assert body["events"][0]["conversation_id"] == "conv_http1"


def test_session_id_invalido_na_url_e_rejeitado(client):
    resp = client.get("/memory/session/bad$id")
    assert resp.status_code == 400


def test_sessions_por_profile_id(client):
    with client.websocket_connect("/stream") as ws:
        ws.send_json(_thinking_event(conversation_id="conv_p1", profile_id="prof_x"))
        ws.send_json({"type": "ping"})
        ws.receive_json()

    resp = client.get("/memory/sessions", params={"profile_id": "prof_x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["sessions"][0]["session_id"] == "conv_p1"


# ── response_end dispara consolidação (spy, sem depender de I/O real) ──────

def test_response_end_dispara_consolidacao(client, monkeypatch):
    calls: list[str] = []
    done = threading.Event()

    def _fake_consolidate(conversation_id: str):
        calls.append(conversation_id)
        done.set()

    monkeypatch.setattr(wsr_mod, "consolidate_session", _fake_consolidate)

    with client.websocket_connect("/stream") as ws:
        ws.send_json(_thinking_event(conversation_id="conv_end1"))
        ws.send_json(_thinking_event(
            conversation_id="conv_end1", type="response_end", data={"text": "fim"},
        ))
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

    assert done.wait(timeout=2.0), "consolidate_session não foi chamado a tempo"
    assert calls == ["conv_end1"]


# ── autenticação por token ──────────────────────────────────────────────────

def test_token_incorreto_rejeita_conexao(client, monkeypatch):
    monkeypatch.setattr(edp_config, "EDP_LIVE_FEED_TOKEN", "segredo", raising=False)
    with pytest.raises(Exception):
        with client.websocket_connect("/stream?token=errado"):
            pass


def test_token_ausente_rejeita_conexao_quando_configurado(client, monkeypatch):
    monkeypatch.setattr(edp_config, "EDP_LIVE_FEED_TOKEN", "segredo", raising=False)
    with pytest.raises(Exception):
        with client.websocket_connect("/stream"):
            pass


def test_token_correto_aceita_conexao(client, monkeypatch):
    monkeypatch.setattr(edp_config, "EDP_LIVE_FEED_TOKEN", "segredo", raising=False)
    with client.websocket_connect("/stream?token=segredo") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
