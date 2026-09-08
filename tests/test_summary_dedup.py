"""
test_summary_dedup.py — a guarda de escrita de `session_summary` (18/08/2026).

O QUE ESTA GUARDA CONSERTA

Medido no store vivo em 18/08: 14 copias extras em 5 grupos de texto
EXATAMENTE igual, e QUATRO dos cinco grupos sao `session_summary`. A dominancia
dos resumos no top-5 nao vem de ranking — vem de estarem la varias vezes.

Origem: `generate_session_summary` dispara em CADA `WebSocketDisconnect`
(websocket.py:1376) sobre `entries[-10:]`. Dois disconnects sem conversa nova
produzem o mesmo prompt e o mesmo resumo. O passo 4 ja DETECTA a duplicata
(cosseno >= 0.75) e usa isso so para copiar o `topic_tag`; o passo 5 grava
assim mesmo.

O QUE ESTES TESTES EXIGEM

1. flag OFF -> byte-identico ao de antes (NORTE §4.7). E o teste que mais
   importa: uma guarda que muda comportamento com a flag desligada nao e
   reversivel, e reversibilidade e a unica razao de existir flag.
2. flag ON  -> duplicata exata nao e gravada, e o retorno mantem o contrato
   que websocket.py/memory.py/consolidator.py ja consomem.
3. a varredura acha o MAXIMO, nao o primeiro. O laco do passo 4 tem `break` no
   primeiro >= 0.75 — reusar aquele `sim` daria um numero que parece o maximo e
   nao e.
4. resumo DIFERENTE continua sendo gravado (a guarda nao pode comer escrita
   legitima).
"""
from __future__ import annotations

import numpy as np
import pytest

import edp.config as edp_config
from edp.runtime.pareto_store import get_pareto_store


def eventos():
    return list(get_pareto_store().query(event_type="summary_write"))


class _RespFake:
    def __init__(self, text): self.text = text


class _RuntimeFake:
    """LLM que devolve sempre o MESMO resumo — o cenario dos dois disconnects."""
    def __init__(self, resumo="Conversa sobre Redis e cache.", label="redis_cache"):
        self.resumo, self.label = resumo, label
        self.chamadas = 0

    def is_connected(self): return True

    def chat(self, prompt, system=None, store_to_memory=True, **kw):
        self.chamadas += 1
        # o 2o prompt (LABEL_PROMPT) contem o resumo ja gerado
        if "label" in (system or "").lower() or "snake_case" in (system or ""):
            return _RespFake(self.label)
        return _RespFake(self.resumo)


@pytest.fixture
def store_com_conversa(synthetic_store, entry_factory):
    """Store com mensagens suficientes para passar MIN_MESSAGES_FOR_SUMMARY."""
    from edp.session_summary import MIN_MESSAGES_FOR_SUMMARY
    for i in range(MIN_MESSAGES_FOR_SUMMARY + 2):
        synthetic_store.episodic.entries.append(
            dict(entry_factory(text=f"Q: pergunta {i}\nA: resposta {i}"), id=f"m{i}")
        )
    return synthetic_store


def _resumos(store):
    return [e for e in store.episodic.entries
            if e.get("source_type") == "session_summary"]


# ── 1. o teste que mais importa ───────────────────────────────────────────────

def test_flag_off_grava_as_duas_copias(store_com_conversa, monkeypatch):
    """
    Com a guarda DESLIGADA o comportamento e o de hoje: duplicata gravada.

    Isto nao e o comportamento desejado — e o comportamento ATUAL, e a flag
    existe para que ligar/desligar seja reversivel. Se este teste virar verde
    por acidente depois de alguem "consertar" o default, a reversibilidade
    sumiu sem ninguem notar.
    """
    monkeypatch.setattr(edp_config, "EDP_SUMMARY_DEDUP", False, raising=False)
    monkeypatch.setattr(edp_config, "EDP_SUMMARY_TELEMETRY", False, raising=False)
    from edp.session_summary import generate_session_summary

    rt = _RuntimeFake()
    r1 = generate_session_summary(store_com_conversa, rt, session_id="s1")
    r2 = generate_session_summary(store_com_conversa, rt, session_id="s1")

    assert r1 and r2
    assert len(_resumos(store_com_conversa)) == 2, (
        "com a flag OFF as duas escritas tem de acontecer — se so uma aconteceu, "
        "a guarda esta agindo desligada e o §4.7 foi violado"
    )
    assert "skipped_duplicate" not in r2
    assert eventos() == [], "telemetria OFF nao pode emitir"


# ── 2. a guarda ligada ────────────────────────────────────────────────────────

def test_flag_on_pula_a_duplicata(store_com_conversa, monkeypatch):
    monkeypatch.setattr(edp_config, "EDP_SUMMARY_DEDUP", True, raising=False)
    from edp.session_summary import generate_session_summary

    rt = _RuntimeFake()
    r1 = generate_session_summary(store_com_conversa, rt, session_id="s1")
    r2 = generate_session_summary(store_com_conversa, rt, session_id="s1")

    assert r1 and r2
    assert len(_resumos(store_com_conversa)) == 1, (
        "a segunda escrita do MESMO resumo foi gravada — a guarda nao mordeu"
    )
    assert r2.get("skipped_duplicate") is True
    assert r2.get("reused") is True
    # contrato que os tres callers ja consomem
    assert set(("summary", "label", "entry_id")) <= set(r2)
    assert r2["entry_id"] == r1["entry_id"], (
        "o retorno tem de apontar para o resumo que JA existe, nao para None"
    )


def test_resumo_diferente_continua_sendo_gravado(store_com_conversa, monkeypatch):
    """CONTROLE: a guarda nao pode comer escrita legitima."""
    monkeypatch.setattr(edp_config, "EDP_SUMMARY_DEDUP", True, raising=False)
    from edp.session_summary import generate_session_summary

    generate_session_summary(store_com_conversa, _RuntimeFake("Sobre Redis e cache."), session_id="s1")
    generate_session_summary(
        store_com_conversa,
        _RuntimeFake("Assunto completamente distinto: latencia de rede e mtr.", "rede"),
        session_id="s1",
    )
    assert len(_resumos(store_com_conversa)) == 2, (
        "a guarda suprimiu um resumo de conteudo DIFERENTE — limiar frouxo demais"
    )


# ── 3. maximo, nao primeiro ───────────────────────────────────────────────────

def test_a_varredura_pega_o_maximo_e_nao_o_primeiro(store_com_conversa, monkeypatch):
    """
    O laco do passo 4 para no PRIMEIRO resumo acima de 0.75 (`break`). Reusar
    aquele `sim` daria um numero com cara de maximo que nao e.

    Monta tres resumos anteriores em ordem crescente de similaridade e exige que
    o `max_sim` emitido seja o do ULTIMO — inalcancavel para quem para no
    primeiro.
    """
    monkeypatch.setattr(edp_config, "EDP_SUMMARY_TELEMETRY", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_SUMMARY_DEDUP", False, raising=False)
    from edp.session_summary import generate_session_summary

    alvo = "Conversa sobre Redis e cache."
    from edp.embeddings import embed_one
    emb_alvo = embed_one(alvo)

    # anteriores: um quase ortogonal, um intermediario, e um IDENTICO ao alvo
    for i, txt in enumerate(["assunto totalmente outro", "redis parcialmente", alvo]):
        # embedding do texto COM prefixo — que e o que `memory_store.add`
        # produz no passo 5. A primeira versao deste fixture usava o texto nu e
        # reproduzia na bancada o proprio descasamento que a guarda conserta:
        # duplicata exata media 0.77 em vez de 1.0.
        e = dict(
            store_com_conversa.episodic.entries[0],
            id=f"prev{i}", text=f"[session_summary] {txt}",
            embedding=embed_one(f"[session_summary] {txt}"),
        )
        e["source_type"] = "session_summary"
        e["topic_tag"] = f"tag{i}"
        store_com_conversa.episodic.entries.append(e)

    generate_session_summary(store_com_conversa, _RuntimeFake(alvo), session_id="s1")

    evs = eventos()
    assert evs, "telemetria ON e nenhum summary_write emitido"
    ev = evs[-1]
    assert ev["n_anteriores"] == 3
    assert ev["max_sim"] == pytest.approx(1.0, abs=1e-4), (
        f"max_sim={ev['max_sim']}; o identico (cos=1.0) e o TERCEIRO da lista — "
        "se veio menor, a varredura parou antes dele"
    )


# ── 4. contrafactual com a guarda desligada ───────────────────────────────────

def test_telemetria_registra_o_que_teria_sido_pulado(store_com_conversa, monkeypatch):
    """
    Com telemetria ON e guarda OFF, o evento grava `gravou=True` e
    `guarda_ativa=False` — mas o `max_sim` alto mostra o que a guarda TERIA
    pulado. E como medir o efeito antes de ligar.
    """
    monkeypatch.setattr(edp_config, "EDP_SUMMARY_TELEMETRY", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_SUMMARY_DEDUP", False, raising=False)
    from edp.session_summary import generate_session_summary

    rt = _RuntimeFake()
    generate_session_summary(store_com_conversa, rt, session_id="s1")
    generate_session_summary(store_com_conversa, rt, session_id="s1")

    evs = eventos()
    assert len(evs) == 2
    assert evs[0]["max_sim"] is None, "no 1o nao havia resumo anterior"
    assert evs[1]["max_sim"] == pytest.approx(1.0, abs=1e-4)
    assert evs[1]["gravou"] is True and evs[1]["guarda_ativa"] is False
    assert len(_resumos(store_com_conversa)) == 2, "telemetria nao pode mudar escrita"


def test_o_evento_esta_no_whitelist():
    """Sem isto o emit e descartado em silencio por tipo desconhecido."""
    from edp.runtime.pareto_store import EVENT_TYPES
    assert "summary_write" in EVENT_TYPES
