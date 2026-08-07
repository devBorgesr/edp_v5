"""
test_anchor_compact.py — peça 2.6f, flag EDP_ANCHOR_COMPACT.

Testa `EDPRuntime.format_task_anchor` diretamente sobre um objeto mínimo
com `_task_anchor`: o método só toca esse atributo, então isto exercita o
código de produção sem instanciar o runtime inteiro (que sobe memória,
background loop e provider).

Padrão flag-off byte-idêntico, exigido pelo `NORTE.md` §4.7 e aplicado a
toda feature nova deste projeto (`tests/test_flag_off_byte_identical.py`).

MOTIVO DA PEÇA (medido em 07/08/2026, `format_task_anchor()` real):
a linha `Decisões:` por seção custa 9.100 de 11.486 chars — 79% do bloco —
em 10 seções × 6 decisões × 120 chars. A âncora é Camada 0.5, injetada
ANTES da janela imediata, e é o único bloco sem teto; nessa configuração
chega a 96% do cap de 12.000 do turno-1. A tarefa de 10 seções validada em
30/05 rodou nessa borda.
"""
from __future__ import annotations

import importlib

import pytest

from edp.llm_adapter import EDPRuntime

CHAVES = ["messaging", "language", "database", "patterns", "contracts", "framework"]


class _Anchor:
    """Portador mínimo de `_task_anchor` — o único atributo que o método lê."""

    def __init__(self, anchor):
        self._task_anchor = anchor

    def format(self) -> str:
        return EDPRuntime.format_task_anchor(self)


def _anchor(n_sec=10, n_dec=6, val_len=120, muda_na_secao=None):
    secoes = []
    for k in range(n_sec):
        dec = {CHAVES[x % len(CHAVES)]: "V" * val_len for x in range(n_dec)}
        if muda_na_secao is not None and k + 1 == muda_na_secao:
            dec["messaging"] = "RabbitMQ 3.13 — trocado por throughput"
        secoes.append({
            "n": k + 1, "total": n_sec, "title": "T" * 28,
            "summary": "S" * 90, "decisions": dec,
        })
    return {"challenge": "D" * 2000, "expected_total": n_sec,
            "sections_delivered": secoes, "started_at": 0.0}


@pytest.fixture
def flag(monkeypatch):
    """Liga/desliga EDP_ANCHOR_COMPACT recarregando o módulo de config."""
    def _set(ligada: bool):
        monkeypatch.setenv("EDP_ANCHOR_COMPACT", "1" if ligada else "0")
        import edp.config
        importlib.reload(edp.config)
    yield _set
    monkeypatch.delenv("EDP_ANCHOR_COMPACT", raising=False)
    import edp.config
    importlib.reload(edp.config)


# ── Flag OFF: o código novo tem de estar inerte ──────────────────────────────

def test_flag_off_mantem_decisoes_por_secao(flag):
    flag(False)
    saida = _Anchor(_anchor()).format()
    assert "    Decisões: " in saida, "linha por-seção sumiu com a flag OFF"
    assert "DECISÕES ARQUITETURAIS JÁ ESTABELECIDAS" in saida


def test_flag_off_nao_emite_cadeia_de_mudanca(flag):
    """Com OFF, o caminho novo do consolidado não pode disparar."""
    flag(False)
    saida = _Anchor(_anchor(muda_na_secao=4)).format()
    assert "ALTERADA na Seção" not in saida


def test_flag_off_e_estavel_entre_chamadas(flag):
    flag(False)
    a = _Anchor(_anchor()).format()
    b = _Anchor(_anchor()).format()
    assert a == b


# ── Flag ON: reduz e preserva o que importa ──────────────────────────────────

def test_flag_on_remove_decisoes_por_secao(flag):
    flag(True)
    saida = _Anchor(_anchor()).format()
    assert "    Decisões: " not in saida
    # o consolidado — que é o que carrega a decisão — permanece
    assert "DECISÕES ARQUITETURAIS JÁ ESTABELECIDAS" in saida


def test_flag_on_reduz_pelo_menos_60_por_cento(flag):
    flag(False)
    off = len(_Anchor(_anchor()).format())
    flag(True)
    on = len(_Anchor(_anchor()).format())
    assert on < off
    reducao = 1 - on / off
    assert reducao >= 0.60, f"redução de apenas {reducao:.0%} (esperado >= 60%)"


def test_flag_on_cabe_no_cap_do_turno_1(flag):
    """
    Cap de 12.000 chars do turno-1 em sprint (llm_adapter, CAPS_POR_POSICAO).
    Com OFF, 10 seções chegam a ~96% dele; com ON tem de sobrar folga real.
    """
    flag(True)
    assert len(_Anchor(_anchor()).format()) < 12000 * 0.5


def test_flag_on_registra_re_decisao(flag):
    """
    O que se perderia ao omitir a linha por-seção é a RE-decisão. Ela tem de
    aparecer no consolidado — senão a economia custaria continuidade.
    """
    flag(True)
    saida = _Anchor(_anchor(muda_na_secao=4)).format()
    assert "ALTERADA na Seção 4" in saida
    assert "RabbitMQ" in saida
    # e a decisão original continua lá, com sua seção de origem
    assert "(def. Seção 1)" in saida


def test_flag_on_sem_mudanca_nao_cria_cadeia(flag):
    """Cadeia de mudança só cresce quando há mudança real."""
    flag(True)
    assert "ALTERADA na Seção" not in _Anchor(_anchor()).format()


# ── Bordas ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ligada", [True, False])
def test_sem_tarefa_devolve_none(flag, ligada):
    flag(ligada)
    assert _Anchor(None).format() is None


@pytest.mark.parametrize("ligada", [True, False])
def test_sem_secao_entregue_nao_quebra(flag, ligada):
    flag(ligada)
    vazio = {"challenge": "x", "expected_total": None,
             "sections_delivered": [], "started_at": 0.0}
    saida = _Anchor(vazio).format()
    assert "Nenhuma seção entregue ainda" in saida


@pytest.mark.parametrize("ligada", [True, False])
def test_secao_sem_decisoes_nao_quebra(flag, ligada):
    flag(ligada)
    a = _anchor(n_sec=2)
    for s in a["sections_delivered"]:
        s["decisions"] = None
    saida = _Anchor(a).format()
    assert "Seção 1/2" in saida
