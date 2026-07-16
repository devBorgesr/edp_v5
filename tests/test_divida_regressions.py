"""
Sementes T2 (item C do adendo) — regressões nomeadas de dívidas técnicas
resolvidas (docs/MARCO_v3.15_STABLE.md, docs/MARCADOR_PECA_2.md):

  #8  PermissionError [WinError 32] em writes concorrentes Windows
      (edp/memory.py:_atomic_write_json — lock + retry + backoff).
  #9  Truncamento da janela imediata em 600 chars (_retrieve_context).
  #9b Truncamento de recent_turns/all_history em 300 chars
      (_build_enriched_context).
  #10 Gravação duplicada com truncamento agressivo em _store_to_memory
      (caps Q[:4000]+A[:12000] alinhados com websocket.py).

Contra código real (chamadas diretas às funções/métodos, sem reimplementar
a lógica) — fixture sintética, sem dado real.
"""
from __future__ import annotations

import json
import os
import time as time_mod
from types import SimpleNamespace

import pytest


# ── Dívida #8 — retry/backoff em PermissionError no os.replace ────────────────

@pytest.mark.windows_only
def test_divida_8_atomic_write_retry_em_permission_error(tmp_path, monkeypatch):
    from edp.memory import _atomic_write_json

    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("[WinError 32] simulado — destino aberto por outro processo")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    monkeypatch.setattr(time_mod, "sleep", lambda s: None)  # sem backoff real no teste

    path = tmp_path / "regressao_divida8.json"
    _atomic_write_json(path, {"a": 1})

    assert calls["n"] == 3   # 2 falhas simuladas + 1 sucesso
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


# ── Dívida #9 — janela imediata de _retrieve_context não volta a 600 chars ────

def test_divida_9_janela_imediata_nao_trunca_em_600(synthetic_store, entry_factory):
    from edp.llm_adapter import EDPRuntime

    texto_longo = "Q: pergunta\nA: " + ("conteúdo relevante " * 400)  # > 4000 chars
    assert len(texto_longo) > 4000

    e = entry_factory(text=texto_longo, timestamp=1_800_000_100.0)
    synthetic_store.episodic.entries.append(e)

    rt = EDPRuntime.__new__(EDPRuntime)
    rt._memory = synthetic_store
    rt._operational_mode = "cognitive"
    rt._co_occurrence = None
    rt.session_id = synthetic_store.session_id

    blocks, hits = rt._retrieve_context("pergunta de teste sobre o conteúdo")

    turno_blocks = [b for b in blocks if texto_longo[:200] in b]
    assert turno_blocks, f"texto longo não apareceu na janela imediata: {blocks}"
    # Cap atual (cognitive, turno mais recente) é 4000 — bem acima dos 600
    # originais da dívida #9. Corta em 4000, não em 600.
    corpo = turno_blocks[0].split("] ", 1)[1]
    assert len(corpo) == 4000


# ── Dívida #9b — recent_turns de _build_enriched_context não trunca em 300 ────

def test_divida_9b_recent_turns_integro_nao_trunca_em_300(synthetic_store, entry_factory):
    from edp.llm_adapter import EDPRuntime

    texto_longo = "Q: pergunta\nA: " + ("conteúdo relevante " * 400)  # > 4000 chars
    e = entry_factory(text=texto_longo, timestamp=1_800_000_100.0)
    synthetic_store.episodic.entries.append(e)

    rt = EDPRuntime.__new__(EDPRuntime)
    rt._memory = synthetic_store
    rt._operational_mode = "cognitive"
    rt._co_occurrence = None
    rt._llm_config = None
    rt.session_id = synthetic_store.session_id

    sys_prompt, meta = rt._build_enriched_context("pergunta de teste", "{context}")

    # recent_turns ÍNTEGRO (Dívida #46 supersedeu #9b com o mesmo espírito:
    # nunca mais truncar cegamente) — o texto completo deve sobreviver, bem
    # acima dos 300 chars originais da dívida #9b.
    assert texto_longo in sys_prompt or texto_longo in "".join(str(v) for v in meta.values())


# ── Dívida #10 — caps de _store_to_memory (Q[:4000]+A[:12000]) + source ───────

def test_divida_10_store_to_memory_caps_e_source(synthetic_store):
    from edp.llm_adapter import EDPRuntime

    rt = EDPRuntime.__new__(EDPRuntime)
    rt._memory = synthetic_store
    rt._llm_config = SimpleNamespace(model="claude-test-model")

    user_msg = "P" * 5000
    response = "R" * 15000

    n_antes = len(synthetic_store.episodic.entries)
    rt._store_to_memory(user_msg, response)
    n_depois = len(synthetic_store.episodic.entries)

    assert n_depois == n_antes + 1
    novo = synthetic_store.episodic.entries[-1]
    esperado = f"Q: {user_msg[:4000]}\nA: {response[:12000]}"
    assert novo["text"] == esperado
    assert novo.get("source") == "llm:claude-test-model"
