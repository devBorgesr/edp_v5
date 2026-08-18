"""
test_contradiction_telemetry.py — por que o detector de contradição não flaga
(13/08/2026).

`scan_results` roda em todo retrieve com `top_k >= 2` (`memory/store.py:1594` e
`:1808`) e o `data/flags/` deste store está VAZIO. Zero flags, sozinho, não
distingue quatro histórias: não rodou, abortou por embedding ausente, rodou e
nada cruzou o limiar, ou cruzou e caiu num filtro posterior.

Medição por leitura pura em 13/08 (sem instanciar o flagger, que persiste em
disco): 153 pares do `default_cognitive`, **máximo 0.778 contra
SIMILARITY_THRESHOLD = 0.85** — o limiar está acima do máximo do corpus,
enquanto 16 de 18 textos têm marcador de negação. O gargalo é a similaridade.

Este arquivo NÃO recalibra o 0.85. Recalibrar é decisão com pré-registro; aqui
só se instala o número que falta para tomá-la.
"""
from __future__ import annotations

import inspect

import pytest

import edp.config as edp_config
from edp.runtime.contradiction_flagger import (
    SIMILARITY_THRESHOLD,
    ContradictionFlagger,
    has_negation,
    negation_asymmetry,
)
from edp.runtime.pareto_store import EVENT_TYPES, get_pareto_store


@pytest.fixture
def flag(monkeypatch):
    def _set(ligada: bool):
        monkeypatch.setattr(edp_config, "EDP_CONTRADICTION_TELEMETRY", ligada,
                            raising=False)
    return _set


@pytest.fixture
def flagger(tmp_path, monkeypatch):
    """Flagger isolado — o real PERSISTE em disco; nunca no diretório de produção."""
    monkeypatch.setenv("EDP_BASE_DIR", str(tmp_path))
    return ContradictionFlagger()


def eventos():
    return list(get_pareto_store().query(event_type="contradiction_scan"))


def ent(id_, texto, emb):
    return {"id": id_, "text": texto, "embedding": emb}


# ── Contrato ─────────────────────────────────────────────────────────────────

def test_tipo_de_evento_registrado():
    assert "contradiction_scan" in EVENT_TYPES


# ── O que faltava: distinguir os zeros ───────────────────────────────────────

def test_menos_de_dois_resultados_e_um_zero_com_nome(flag, flagger, isolated_base_dir):
    flag(True)
    assert flagger.scan_results([ent("a", "x", [1.0, 0.0])]) == 0
    assert eventos()[0]["abortou"] == "menos_de_2"


def test_um_embedding_ausente_cancela_o_par_a_par_inteiro(flag, flagger, isolated_base_dir):
    """
    UM resultado sem embedding faz `return 0` para a lista TODA
    (contradiction_flagger.py:297) — não é "pula esse", é "cancela o scan".
    Sem telemetria isso era indistinguível de "escaneou e não achou".
    """
    flag(True)
    r = flagger.scan_results([
        ent("a", "x", [1.0, 0.0]),
        ent("b", "y", None),
        ent("c", "z", [0.0, 1.0]),
    ])
    assert r == 0
    e = eventos()[0]
    assert e["abortou"] == "sem_embedding"
    assert e["n_pares"] == 0 and e["n_resultados"] == 3


def test_scan_completo_grava_max_sim_mesmo_sem_flagar(flag, flagger, isolated_base_dir):
    """
    O caso do store real: rodou, comparou, e o melhor par ficou LONGE do limiar.
    `max_sim` é o que separa "não achou" de "não tinha como achar".
    """
    flag(True)
    flagger.scan_results([
        ent("a", "o céu é azul",       [1.0, 0.0]),
        ent("b", "gatos dormem muito", [0.0, 1.0]),
    ])
    e = eventos()[0]
    assert e["abortou"] == "" and e["n_pares"] == 1
    assert e["n_flagados"] == 0 and e["n_acima_do_limiar"] == 0
    assert e["max_sim"] < SIMILARITY_THRESHOLD


def test_max_sim_e_o_maximo_e_nao_o_ultimo(flag, flagger, isolated_base_dir):
    """
    Acumulado dentro do laço que já existe. Se pegasse o último par comparado
    em vez do máximo, o número pareceria plausível e seria errado.
    """
    flag(True)
    flagger.scan_results([
        ent("a", "um", [1.0, 0.0]),
        ent("b", "dois", [0.98, 0.199]),   # par (a,b): sim alta
        ent("c", "tres", [0.0, 1.0]),      # pares (a,c) e (b,c): sim baixa
    ])
    e = eventos()[0]
    assert e["n_pares"] == 3
    assert e["max_sim"] > 0.9, "max_sim pegou o último par, não o maior"


def test_limiar_vai_dentro_do_evento(flag, flagger, isolated_base_dir):
    """
    Se alguém recalibrar o 0.85, as amostras de antes e depois têm de ser
    separáveis pelo próprio dado — mesma lição do `format_state`.
    """
    flag(True)
    flagger.scan_results([ent("a", "x", [1.0, 0.0]), ent("b", "y", [0.0, 1.0])])
    assert eventos()[0]["limiar"] == SIMILARITY_THRESHOLD


# ── O achado, travado ────────────────────────────────────────────────────────

def test_negacao_nao_e_o_gargalo():
    """
    16 de 18 textos do store real têm marcador de negação. A condição de
    negação é quase sempre satisfazível; quem não deixa flagar é a similaridade.
    Aqui a parte verificável em unidade: o detector reconhece o marcador comum
    e a assimetria funciona.
    """
    assert has_negation("isso não é verdade")
    assert has_negation("nunca aconteceu")
    assert not has_negation("isso é verdade")
    assert negation_asymmetry("isso é verdade", "isso não é verdade")
    assert not negation_asymmetry("isso não é verdade", "aquilo nunca ocorreu")


def test_o_limiar_continua_o_gargalo_declarado():
    """
    REGRESSÃO: o número medido em 13/08 (máx 0.778 no corpus) só é interpretável
    contra o limiar da época. Se alguém baixar o SIMILARITY_THRESHOLD para
    <= 0.778 sem refazer a medição, a conclusão registrada nos comentários vira
    falsa e este teste força a revisita.
    """
    assert SIMILARITY_THRESHOLD == 0.85, (
        "limiar mudou — a medição de 13/08 (máx 0.778 em 153 pares) precisa "
        "ser refeita antes de repetir a conclusão 'inalcançável'"
    )


# ── Flag OFF ─────────────────────────────────────────────────────────────────

def test_flag_off_nao_grava(flag, flagger, isolated_base_dir):
    flag(False)
    flagger.scan_results([ent("a", "x", [1.0, 0.0]), ent("b", "y", [0.0, 1.0])])
    assert eventos() == []


def test_gate_le_a_flag_antes_de_emitir():
    fonte = inspect.getsource(ContradictionFlagger._telemetria)
    assert fonte.index("EDP_CONTRADICTION_TELEMETRY") < fonte.index("emit_contradiction_scan")


# ── Telemetria nunca derruba retrieve ───────────────────────────────────────

def test_falha_do_store_nao_propaga(flag, flagger, isolated_base_dir, monkeypatch):
    flag(True)
    import edp.runtime.pareto_store as ps
    monkeypatch.setattr(ps, "get_pareto_store",
                        lambda: (_ for _ in ()).throw(OSError("disco cheio")))
    assert flagger.scan_results([ent("a", "x", [1.0, 0.0]),
                                 ent("b", "y", [0.0, 1.0])]) == 0


def test_retorno_do_scan_nao_muda_com_a_flag(flag, flagger, isolated_base_dir):
    """Medir não pode mudar o que o chamador recebe."""
    dados = [ent("a", "x", [1.0, 0.0]), ent("b", "y", [0.0, 1.0])]
    flag(False)
    sem = flagger.scan_results(dados)
    flag(True)
    com = flagger.scan_results(dados)
    assert sem == com == 0


# ── Não contamina a coleta da Fase 2 ────────────────────────────────────────

def test_flag_e_classificada_como_nao_afetando_o_prompt():
    assert "EDP_CONTRADICTION_TELEMETRY" in edp_config.FORMAT_STATE_FLAGS_IGNORADAS
    assert "EDP_CONTRADICTION_TELEMETRY" not in edp_config.FORMAT_STATE_FLAGS


def test_contradiction_scan_nao_entra_na_populacao_de_tokens(flag, flagger, isolated_base_dir):
    from edp.runtime.pareto_store import amostra_valida_fase2
    flag(True)
    flagger.scan_results([ent("a", "x", [1.0, 0.0]), ent("b", "y", [0.0, 1.0])])
    assert not amostra_valida_fase2(eventos()[0])
