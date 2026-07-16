"""
Sementes T2 (item C do adendo) — idempotência dos scripts de backfill REAL
(exp012_fase4_backfill_apply.py + exp016_backfill_apply.py). Roda main() de
cada script DUAS VEZES contra um store sintético em tmp_path (nunca
C:\\edp_data, nunca dado real) e confirma que a 2ª rodada não regrava nada
(checagem "e.get('answer_class'): pula" nos dois scripts).

Backup (shutil.copytree) é parte real do fluxo do script — não mockado;
só o timestamp do nome do dir de backup é tornado único (evita colisão de
segundo entre as duas chamadas do mesmo script na mesma execução de teste).
"""
from __future__ import annotations

import importlib
import itertools
import json
import sys
import time as time_mod

import pytest


def _make_layer_files(base, sid: str, entries_cognitive_episodic: list[dict]) -> None:
    d = base / "sessions" / f"{sid}_cognitive"
    d.mkdir(parents=True, exist_ok=True)
    (d / "episodic.json").write_text(
        json.dumps(entries_cognitive_episodic, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def backfill_base(tmp_path):
    base = tmp_path / "edp_data_test"
    return base


@pytest.fixture
def unique_backup_ts(monkeypatch):
    """Evita colisão de shutil.copytree quando o script roda 2x no mesmo teste
    (nome do backup usa time.strftime com precisão de segundo)."""
    counter = itertools.count()
    orig = time_mod.strftime

    def fake(fmt, *a, **kw):
        return orig(fmt, *a, **kw) + f"_{next(counter)}"

    monkeypatch.setattr(time_mod, "strftime", fake)


def _run_main(monkeypatch, module_name: str, base, sid: str = "default"):
    monkeypatch.setenv("EDP_BASE_DIR", str(base))
    monkeypatch.setattr(sys, "argv", ["backfill", sid])
    mod = importlib.import_module(module_name)
    mod.main()


# ── exp012 (NEG/KW → not_found) ────────────────────────────────────────────────

def test_exp012_backfill_idempotente(backfill_base, unique_backup_ts, monkeypatch, capsys):
    _make_layer_files(backfill_base, "default", [
        {"id": "a1", "text": "Q: o que combinamos?\nA: Não encontro registro disso."},
        {"id": "a2", "text": "Q: tudo bem?\nA: Tudo ótimo, como posso ajudar?"},
    ])

    _run_main(monkeypatch, "exp012_fase4_backfill_apply", backfill_base)
    out1 = capsys.readouterr().out
    assert "gravadas=1" in out1

    data = json.loads((backfill_base / "sessions" / "default_cognitive" / "episodic.json").read_text())
    by_id = {e["id"]: e for e in data}
    assert by_id["a1"]["answer_class"] == "not_found"
    assert "answer_class" not in by_id["a2"]

    # 2ª rodada: idempotente — nada novo gravado, a1 pulada por já classificada
    _run_main(monkeypatch, "exp012_fase4_backfill_apply", backfill_base)
    out2 = capsys.readouterr().out
    assert "gravadas=0" in out2
    assert "puladas(já classificadas)=1" in out2

    data2 = json.loads((backfill_base / "sessions" / "default_cognitive" / "episodic.json").read_text())
    by_id2 = {e["id"]: e for e in data2}
    assert by_id2["a1"]["answer_class"] == "not_found"


# ── exp016 (DISQ → disqualification) ───────────────────────────────────────────

def test_exp016_backfill_idempotente(backfill_base, unique_backup_ts, monkeypatch, capsys):
    _make_layer_files(backfill_base, "default", [
        {"id": "b1", "text": "Q: isso é real?\nA: Eu inventei essa história."},
        {"id": "b2", "text": "Q: tudo bem?\nA: Tudo ótimo, como posso ajudar?"},
    ])

    _run_main(monkeypatch, "exp016_backfill_apply", backfill_base)
    out1 = capsys.readouterr().out
    assert "gravadas=1" in out1

    data = json.loads((backfill_base / "sessions" / "default_cognitive" / "episodic.json").read_text())
    by_id = {e["id"]: e for e in data}
    assert by_id["b1"]["answer_class"] == "disqualification"
    assert "answer_class" not in by_id["b2"]

    # 2ª rodada: idempotente
    _run_main(monkeypatch, "exp016_backfill_apply", backfill_base)
    out2 = capsys.readouterr().out
    assert "gravadas=0" in out2
    assert "puladas(já classificadas)=1" in out2


# ── separação de escopo: exp012 não pega DISQ, exp016 não pega NEG ─────────────

def test_backfills_nao_se_cruzam(backfill_base, unique_backup_ts, monkeypatch, capsys):
    _make_layer_files(backfill_base, "default", [
        {"id": "a1", "text": "Q: o que combinamos?\nA: Não encontro registro disso."},
        {"id": "b1", "text": "Q: isso é real?\nA: Eu inventei essa história."},
    ])

    _run_main(monkeypatch, "exp012_fase4_backfill_apply", backfill_base)
    capsys.readouterr()
    data = json.loads((backfill_base / "sessions" / "default_cognitive" / "episodic.json").read_text())
    by_id = {e["id"]: e for e in data}
    assert by_id["a1"]["answer_class"] == "not_found"
    assert "answer_class" not in by_id["b1"]   # DISQ não é pego pelo backfill exp012

    _run_main(monkeypatch, "exp016_backfill_apply", backfill_base)
    capsys.readouterr()
    data2 = json.loads((backfill_base / "sessions" / "default_cognitive" / "episodic.json").read_text())
    by_id2 = {e["id"]: e for e in data2}
    assert by_id2["b1"]["answer_class"] == "disqualification"
    assert by_id2["a1"]["answer_class"] == "not_found"   # preservado, não sobrescrito
