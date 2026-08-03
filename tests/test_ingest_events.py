"""tests/test_ingest_events.py — validação/mapeamento puros do live feed."""
from __future__ import annotations

from edp.ingest.events import (
    extract_ids,
    extract_text,
    registry_key,
    sanitize_id,
    validate_event,
)


def _valid_event(**overrides) -> dict:
    event = {
        "type": "thinking",
        "timestamp": 1_800_000_000.0,
        "conversation_id": "conv_abc123",
        "data": {"text": "pensando em algo"},
    }
    event.update(overrides)
    return event


# ── sanitize_id ──────────────────────────────────────────────────────────────

def test_sanitize_id_aceita_alfanumerico_underscore_hifen():
    assert sanitize_id("conv_abc-123") == "conv_abc-123"


def test_sanitize_id_rejeita_path_traversal():
    assert sanitize_id("../../etc/passwd") is None


def test_sanitize_id_rejeita_barra():
    assert sanitize_id("a/b") is None


def test_sanitize_id_rejeita_nao_string():
    assert sanitize_id(123) is None
    assert sanitize_id(None) is None


def test_sanitize_id_rejeita_vazio_e_longo_demais():
    assert sanitize_id("") is None
    assert sanitize_id("a" * 129) is None
    assert sanitize_id("a" * 128) == "a" * 128


# ── validate_event ───────────────────────────────────────────────────────────

def test_validate_event_aceita_evento_valido():
    ok, reason = validate_event(_valid_event())
    assert ok is True
    assert reason is None


def test_validate_event_rejeita_nao_dict():
    ok, reason = validate_event("not a dict")
    assert ok is False
    assert "objeto JSON" in reason


def test_validate_event_rejeita_campo_obrigatorio_ausente():
    event = _valid_event()
    del event["conversation_id"]
    ok, reason = validate_event(event)
    assert ok is False
    assert "conversation_id" in reason


def test_validate_event_rejeita_data_nao_objeto():
    ok, reason = validate_event(_valid_event(data="not a dict"))
    assert ok is False


def test_validate_event_rejeita_timestamp_nao_numerico():
    ok, reason = validate_event(_valid_event(timestamp="agora"))
    assert ok is False


def test_validate_event_rejeita_conversation_id_invalido():
    ok, reason = validate_event(_valid_event(conversation_id="../etc/passwd"))
    assert ok is False
    assert "conversation_id" in reason


def test_validate_event_rejeita_session_id_invalido_top_level():
    ok, reason = validate_event(_valid_event(session_id="a/b"))
    assert ok is False
    assert "session_id" in reason


def test_validate_event_rejeita_profile_id_invalido_dentro_de_data():
    event = _valid_event()
    event["data"]["profile_id"] = "a/b"
    ok, reason = validate_event(event)
    assert ok is False
    assert "profile_id" in reason


def test_validate_event_aceita_session_id_e_profile_id_opcionais():
    ok, reason = validate_event(_valid_event(session_id="sess_1", profile_id="user_1"))
    assert ok is True


# ── extract_ids ──────────────────────────────────────────────────────────────

def test_extract_ids_top_level():
    event = _valid_event(session_id="sess_1", profile_id="user_1")
    ids = extract_ids(event)
    assert ids == {
        "conversation_id": "conv_abc123",
        "session_id": "sess_1",
        "profile_id": "user_1",
    }


def test_extract_ids_dentro_de_data():
    event = _valid_event()
    event["data"]["profile_id"] = "user_2"
    ids = extract_ids(event)
    assert ids["profile_id"] == "user_2"
    assert ids["session_id"] is None


# ── extract_text ─────────────────────────────────────────────────────────────

def test_extract_text_usa_chave_conhecida():
    event = _valid_event(data={"text": "olá mundo"})
    assert extract_text(event) == "olá mundo"


def test_extract_text_fallback_para_dump_json():
    event = _valid_event(data={"foo": "bar"})
    assert "foo" in extract_text(event)


def test_extract_text_sem_data_util():
    event = _valid_event(data={})
    text = extract_text(event)
    assert isinstance(text, str)
    assert text  # nunca vazio


# ── registry_key ─────────────────────────────────────────────────────────────

def test_registry_key_namespaced():
    assert registry_key("abc123") == "livefeed_abc123"


def test_registry_key_nao_colide_com_default():
    assert registry_key("default") != "default"
