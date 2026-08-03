"""tests/test_ingest_session_index.py — índice reverso profile_id -> conversas."""
from __future__ import annotations

from edp.ingest.session_index import SessionIndex


def test_touch_e_list_conversations(tmp_path):
    idx = SessionIndex(tmp_path / "index.json")
    idx.touch("profile_1", "conv_a", 100.0)
    idx.touch("profile_1", "conv_b", 200.0)

    result = idx.list_conversations("profile_1")
    assert [e["session_id"] for e in result] == ["conv_b", "conv_a"]  # mais recente primeiro


def test_list_conversations_profile_desconhecido_retorna_vazio(tmp_path):
    idx = SessionIndex(tmp_path / "index.json")
    assert idx.list_conversations("nunca_visto") == []


def test_touch_atualiza_last_seen(tmp_path):
    idx = SessionIndex(tmp_path / "index.json")
    idx.touch("profile_1", "conv_a", 100.0)
    idx.touch("profile_1", "conv_a", 300.0)

    result = idx.list_conversations("profile_1")
    assert len(result) == 1
    assert result[0]["last_seen"] == 300.0


def test_flush_persiste_e_recarrega(tmp_path):
    path = tmp_path / "index.json"
    idx = SessionIndex(path)
    idx.touch("profile_1", "conv_a", 100.0)
    idx.flush()
    assert path.exists()

    reloaded = SessionIndex(path)
    assert reloaded.list_conversations("profile_1") == [
        {"session_id": "conv_a", "last_seen": 100.0}
    ]


def test_flush_sem_mudancas_nao_recria_arquivo(tmp_path):
    path = tmp_path / "index.json"
    idx = SessionIndex(path)
    idx.flush()  # nada dirty
    assert not path.exists()


def test_isolamento_entre_profiles(tmp_path):
    idx = SessionIndex(tmp_path / "index.json")
    idx.touch("profile_1", "conv_a", 100.0)
    idx.touch("profile_2", "conv_b", 200.0)

    assert [e["session_id"] for e in idx.list_conversations("profile_1")] == ["conv_a"]
    assert [e["session_id"] for e in idx.list_conversations("profile_2")] == ["conv_b"]
