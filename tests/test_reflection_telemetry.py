"""
test_reflection_telemetry.py — telemetria da reflexão (13/08/2026).

`MetaReasoner.reflect()` roda em todo turno pelo caminho vivo e o
`ReflectionResult` inteiro morre na variável — `pipeline.py:280` já chamava isso
de "dead store" e deixou o subsistema de pé porque o escopo daquela fase proibia
removê-lo. Isto MEDE antes de decidir entre aplicar `reweights` e remover o
subsistema; não aplica nada.

Dois testes aqui são guardas de fonte sobre `pipeline.py`, não sobre o emissor:
travam o fato que motiva a medição (o resultado é descartado, a memória entra
vazia). Se alguém consertar qualquer um dos dois, o teste quebra e obriga a
revisitar esta telemetria — que é o comportamento desejado.
"""
from __future__ import annotations

import inspect

import pytest

import edp.config as edp_config
from edp.meta_reasoner import MetaReasoner, ReflectionResult
from edp.runtime.pareto_store import (
    EVENT_TYPES,
    emit_reflection,
    get_pareto_store,
    resumo_reweights,
)


@pytest.fixture
def flag(monkeypatch):
    def _set(ligada: bool):
        monkeypatch.setattr(edp_config, "EDP_REFLECTION_TELEMETRY", ligada,
                            raising=False)
    return _set


def eventos():
    return list(get_pareto_store().query(event_type="reflection"))


def res(**kw):
    base = dict(confidence=0.71, conflicts=[], redundancies=[],
                hallucination_risk=0.29, critique="ok", reweights={},
                depth=0, skipped=False, skip_reason="")
    base.update(kw)
    return ReflectionResult(**base)


# ── Contrato com o event store ───────────────────────────────────────────────

def test_tipo_de_evento_registrado():
    assert "reflection" in EVENT_TYPES


# ── O que motiva medir: hoje o resultado é descartado ────────────────────────

def test_pipeline_ainda_descarta_a_reflexao():
    """
    GUARDA DE FONTE: `reflection` é atribuída em `pipeline.py` e nunca lida.

    Este teste NÃO defende o descarte — ele trava a premissa da medição. Quando
    alguém passar a consumir `reflection` (aplicar `reweights`, propagar
    `confidence`), este teste quebra, e é exatamente aí que a pergunta
    "aplicar ou remover?" precisa ser reaberta com o dado na mão.
    """
    from edp import pipeline as _p
    fonte = inspect.getsource(_p.run_pipeline)
    usos = [ln for ln in fonte.splitlines()
            if "reflection" in ln and "reflection =" not in ln
            and not ln.strip().startswith("#")]
    assert usos == [], f"reflection passou a ser consumida: {usos}"


def test_pipeline_ainda_entrega_memoria_vazia_ao_meta_reasoner():
    """
    GUARDA DE FONTE: `mem_results` é fixado em `[]` (pipeline.py:283) desde o
    corte da Fase 0.5. Consequência medível: `_conf` cai no `anchor=0.5` fixo e
    `_risk` sempre soma `+0.20`. Ou seja, `confidence` e `hallucination_risk`
    saem de um cálculo estruturalmente degradado — e é por isso que o evento
    grava `n_mem_entries`: para a degradação estar no DADO, não só no comentário.
    """
    from edp import pipeline as _p
    fonte = inspect.getsource(_p.run_pipeline)
    assert "mem_results: list[dict] = []" in fonte
    assert "meta.reflect(context_items, mem_results)" in fonte


# ── Os desvios contam ────────────────────────────────────────────────────────

@pytest.mark.parametrize("motivo", ["max_depth", "cooldown", "recursive"])
def test_desvio_tambem_e_gravado(motivo, flag, isolated_base_dir):
    """
    Os três desvios devolvem `confidence=0.5` FIXO. Sem gravá-los, um painel
    mostraria 0.5 e ninguém saberia se é "coerência média" ou "não rodou".
    """
    flag(True)
    MetaReasoner._telemetria(res(confidence=0.5, skipped=True, skip_reason=motivo),
                             n_ctx=4, n_mem=0)
    e = eventos()[0]
    assert e["skipped"] is True and e["skip_reason"] == motivo


def test_cooldown_real_cai_no_desvio(flag, isolated_base_dir, monkeypatch):
    """
    Não é só o `_telemetria` que grava o desvio — o caminho real cai nele.
    Dois `reflect()` seguidos dentro de REFLECTION_COOLDOWN (5s): o segundo é
    stub. Prova sem embeddings: o primeiro é forçado a sair pelo caminho de
    contexto vazio.
    """
    flag(True)
    import edp.meta_reasoner as mr
    monkeypatch.setattr(mr, "_last_ts", 0.0)
    m = MetaReasoner()
    m.reflect([], [])           # arma o _last_ts
    r2 = m.reflect([], [])      # dentro do cooldown
    assert r2.skip_reason == "cooldown"
    assert [e["skip_reason"] for e in eventos()] == ["", "cooldown"]


def test_telemetria_devolve_o_mesmo_objeto(flag, isolated_base_dir):
    """Medir não pode trocar o que o chamador recebe."""
    flag(True)
    r = res()
    assert MetaReasoner._telemetria(r, 3, 0) is r


# ── resumo_reweights ─────────────────────────────────────────────────────────

def test_amplitude_zero_quando_o_peso_nao_varia():
    """
    O caso que decide aplicar-ou-remover: média alta, amplitude zero. O sinal
    PARECE forte e não separa chunk nenhum — aplicá-lo seria multiplicar tudo
    pela mesma constante.
    """
    r = resumo_reweights({"a": 0.9, "b": 0.9, "c": 0.9})
    assert r["media"] == 0.9 and r["amplitude"] == 0.0 and r["desvio"] == 0.0


def test_amplitude_captura_a_penalidade_de_conflito():
    r = resumo_reweights({"a": 0.90, "b": 0.63})
    assert r["amplitude"] == 0.27


def test_reweights_vazio_nao_divide_por_zero():
    assert resumo_reweights({}) == {"n": 0}


def test_um_unico_peso_nao_estoura():
    r = resumo_reweights({"a": 0.5})
    assert r["n"] == 1 and r["desvio"] == 0.0 and r["amplitude"] == 0.0


def test_dict_bruto_nao_vai_para_o_log(flag, isolated_base_dir):
    """
    As chaves de `reweights` são ids de chunk e o volume é por turno, sem
    limite. Só o resumo pode ir ao disco.
    """
    flag(True)
    MetaReasoner._telemetria(res(reweights={"chunk_texto_longo": 0.8}), 1, 0)
    assert set(eventos()[0]["reweights"]) == {
        "n", "min", "max", "media", "mediana", "amplitude", "desvio"}


# ── Flag OFF ─────────────────────────────────────────────────────────────────

def test_flag_off_nao_grava(flag, isolated_base_dir):
    flag(False)
    MetaReasoner._telemetria(res(), 3, 0)
    assert eventos() == []


def test_gate_le_a_flag_antes_de_montar_o_evento():
    fonte = inspect.getsource(MetaReasoner._telemetria)
    assert fonte.index("EDP_REFLECTION_TELEMETRY") < fonte.index("emit_reflection")


# ── Telemetria nunca derruba caminho vivo ───────────────────────────────────

def test_falha_do_store_nao_propaga(flag, isolated_base_dir, monkeypatch):
    flag(True)
    import edp.runtime.pareto_store as ps
    monkeypatch.setattr(ps, "get_pareto_store",
                        lambda: (_ for _ in ()).throw(OSError("disco cheio")))
    assert MetaReasoner._telemetria(res(), 3, 0) is not None


def test_reweights_nao_numerico_nao_propaga(flag, isolated_base_dir):
    flag(True)
    MetaReasoner._telemetria(res(reweights={"a": object()}), 1, 0)


# ── Não contamina a coleta da Fase 2 ────────────────────────────────────────

def test_flag_e_classificada_como_nao_afetando_o_prompt():
    assert "EDP_REFLECTION_TELEMETRY" in edp_config.FORMAT_STATE_FLAGS_IGNORADAS
    assert "EDP_REFLECTION_TELEMETRY" not in edp_config.FORMAT_STATE_FLAGS


def test_reflection_nao_entra_na_populacao_de_tokens(flag, isolated_base_dir):
    from edp.runtime.pareto_store import amostra_valida_fase2
    flag(True)
    MetaReasoner._telemetria(res(), 3, 0)
    assert not amostra_valida_fase2(eventos()[0])
