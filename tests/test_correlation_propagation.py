"""
test_correlation_propagation.py — o id do turno atravessa o executor? (18/08/2026)

Medido antes da correcao: `correlation_id` nulo em 18/18 registros de lineage e
em 18/18 `memory_added`, contra 38/38 em `token_usage`. Causa em
lab_edp_novo/docs/sujeito_edp/ACHADO_CORRELATION_ID.md.

O MECANISMO, e por que a solucao obvia nao serve:

    websocket.py:878   gen = runtime.stream_chat(message)
    websocket.py:882   await loop.run_in_executor(None, lambda: next(gen, None))
                                                  ^ o CORPO roda numa thread do pool
    llm_adapter.py     set_current_correlation_id(_cid)   <- grava naquela thread
    websocket.py:1318  lineage.build(...)                 <- le na thread do handler

`contextvars` NAO resolve: `run_in_executor` nao copia contexto, e mesmo que
copiasse, mutacao feita la dentro nao volta para quem chamou. O unico caminho
que atravessa e o turno ser DONO do id e passa-lo explicitamente.

NORTE §4.7: feature atras de flag, com prova de que desligada nao muda nada.
"""
from __future__ import annotations

import asyncio
import inspect
import threading

import pytest

import edp.config as edp_config
from edp.runtime.lineage import LineageTracker
from edp.runtime.pareto_store import (
    clear_current_correlation_id,
    set_current_correlation_id,
)


@pytest.fixture(autouse=True)
def _limpa():
    clear_current_correlation_id()
    yield
    clear_current_correlation_id()


# ── O defeito, reproduzido ───────────────────────────────────────────────────

def test_thread_local_nao_atravessa_o_executor():
    """
    Reproduz a causa raiz, para ela nao virar folclore.

    Grava o id numa thread de pool (como stream_chat faz) e le na thread
    chamadora (como o lineage faz). Devolve None — SEMPRE, nao as vezes.
    """
    async def _cenario():
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: set_current_correlation_id("turn_X"))
        from edp.runtime.pareto_store import get_current_correlation_id
        return get_current_correlation_id()

    assert asyncio.run(_cenario()) is None, (
        "se isto passar a devolver o id, o thread-local passou a atravessar o "
        "executor e a correcao explicita deixou de ser necessaria"
    )


def test_contextvars_tambem_nao_resolveria():
    """
    Guarda contra a correcao errada. Mutacao dentro do executor nao volta,
    mesmo com o contexto copiado — por isso a solucao e passar explicitamente.
    """
    import contextvars
    var = contextvars.ContextVar("cid", default=None)

    async def _cenario():
        loop = asyncio.get_event_loop()
        ctx = contextvars.copy_context()
        await loop.run_in_executor(None, lambda: ctx.run(var.set, "turn_X"))
        return var.get()

    assert asyncio.run(_cenario()) is None


# ── A correcao ───────────────────────────────────────────────────────────────

def test_id_explicito_vence_o_thread_local():
    rec = LineageTracker().build(session_id="s", retrieved=[],
                                 correlation_id="turn_EXPLICITO")
    assert rec.correlation_id == "turn_EXPLICITO"


def test_id_explicito_funciona_de_outra_thread():
    """O caso real: quem monta o lineage nunca viu a thread que gerou o id."""
    saida = {}

    def _em_outra_thread():
        set_current_correlation_id("turn_DA_POOL")

    t = threading.Thread(target=_em_outra_thread)
    t.start(); t.join()

    rec = LineageTracker().build(session_id="s", retrieved=[],
                                 correlation_id="turn_DO_HANDLER")
    saida["cid"] = rec.correlation_id
    assert saida["cid"] == "turn_DO_HANDLER"


def test_sem_id_explicito_cai_no_thread_local_como_antes():
    """Compatibilidade: o caminho antigo continua existindo e funcionando."""
    set_current_correlation_id("turn_LOCAL")
    rec = LineageTracker().build(session_id="s", retrieved=[])
    assert rec.correlation_id == "turn_LOCAL"


def test_sem_id_e_sem_thread_local_fica_none():
    rec = LineageTracker().build(session_id="s", retrieved=[])
    assert rec.correlation_id is None


# ── Flag OFF: byte-identico (NORTE §4.7) ─────────────────────────────────────

def test_flag_existe_e_nasce_desligada():
    assert edp_config.EDP_CORRELATION_PROPAGATION is False


def test_flag_classificada_como_nao_afetando_o_prompt():
    """
    Muda de ONDE vem o id, nao o que vai ao modelo. Se entrasse no
    format_hash, ligar isto no meio da coleta da Fase 2 partiria o dataset em
    dois regimes por um motivo que nao existe.
    """
    assert "EDP_CORRELATION_PROPAGATION" in edp_config.FORMAT_STATE_FLAGS_IGNORADAS
    assert "EDP_CORRELATION_PROPAGATION" not in edp_config.FORMAT_STATE_FLAGS


def test_websocket_le_a_flag_antes_de_gerar_o_id():
    """Flag desligada -> _turn_cid fica None -> todo caminho vira o de hoje."""
    from edp.api.routes import websocket as ws
    fonte = inspect.getsource(ws)
    i_flag = fonte.index("EDP_CORRELATION_PROPAGATION")
    i_gera = fonte.index("_turn_cid = new_correlation_id()")
    assert i_flag < i_gera


def test_o_id_e_gerado_antes_do_executor():
    """
    GUARDA DE FONTE, e e o ponto inteiro da correcao: gerar DEPOIS do
    run_in_executor reintroduziria o defeito sem mudar mais nada visivel.
    """
    from edp.api.routes import websocket as ws
    fonte = inspect.getsource(ws)
    i_gera = fonte.index("_turn_cid = new_correlation_id()")
    i_exec = fonte.index("loop.run_in_executor")
    assert i_gera < i_exec, "o id passou a ser gerado depois do executor"


def test_stream_chat_gera_o_proprio_id_quando_nao_recebe():
    """
    Flag-off byte-identico do lado do adapter: sem argumento, o comportamento
    e o de sempre — gerar. A prova e de fonte porque exercitar stream_chat
    exigiria provedor de LLM real, e a suite nao sai para a rede.
    """
    from edp import llm_adapter
    fonte = inspect.getsource(llm_adapter.EDPRuntime.stream_chat)
    assert "correlation_id or new_correlation_id()" in fonte
    assinatura = inspect.signature(llm_adapter.EDPRuntime.stream_chat)
    assert assinatura.parameters["correlation_id"].default is None


def test_chat_tem_o_mesmo_contrato():
    from edp import llm_adapter
    assinatura = inspect.signature(llm_adapter.EDPRuntime.chat)
    assert assinatura.parameters["correlation_id"].default is None
    fonte = inspect.getsource(llm_adapter.EDPRuntime.chat)
    assert "correlation_id or new_correlation_id()" in fonte
