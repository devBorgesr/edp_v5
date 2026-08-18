"""
test_ranking_telemetry_caminho_vivo.py — a telemetria alcanca o caminho que a
PRODUCAO percorre? (18/08/2026)

O QUE ACONTECEU

A telemetria de ranking foi instalada em 13/08 dentro de
`EpisodicMemory.retrieve` (store.py:768) — o caminho COSSENO. Mas
`MemoryStore.retrieve` (store.py:1494) despacha assim:

    store.py:1511   if EDP_HYBRID_RETRIEVAL:
                        return self._retrieve_hybrid(...)
    config.py:53    EDP_HYBRID_RETRIEVAL default = "1"   (desde 08/07)

O `return` sai antes de chegar perto do codigo instrumentado. Medido em
18/08 apos quatro turnos reais com `EDP_RANKING_TELEMETRY=1`: **zero eventos
`ranking_decision`**. Nao era bug de emissao — era codigo morto no caminho vivo.

POR QUE OS 12 TESTES DE 13/08 NAO PEGARAM

Todos exercitam a funcao que eu instrumentei, ou a fonte dela. Nenhum pergunta
se a producao chega la. E a mesma **prova de inercia** que apliquei ao
`memory_graph` ("alguem importa isso?") e nao apliquei ao meu proprio codigo:
verifiquei que estava correto, nao verifiquei que roda.

Este arquivo testa a propriedade que faltava: entrar por
`MemoryStore.retrieve` — o ponto que o websocket chama — e exigir emissao
nos DOIS despachos.
"""
from __future__ import annotations

import numpy as np
import pytest

import edp.config as edp_config
from edp.runtime.pareto_store import get_pareto_store


def eventos():
    return list(get_pareto_store().query(event_type="ranking_decision"))


class _ResFake:
    """Formato de retorno do HybridRetriever.search (retrieval_hybrid.py)."""
    def __init__(self, n):
        self.indices       = list(range(n))
        self.scores        = [0.016 - i * 0.001 for i in range(n)]
        self.bm25_scores   = [0.5 - i * 0.01 for i in range(n)]
        self.vector_scores = [0.4 - i * 0.01 for i in range(n)]


class _HrFake:
    def __init__(self, n):
        self.n = n

    def search(self, query, query_emb, top_k, min_score, method, mmr):
        return _ResFake(min(self.n, top_k))


@pytest.fixture
def store_com_hibrido(monkeypatch, isolated_base_dir, entry_factory):
    """
    MemoryStore real, com o INDICE hibrido falsificado.

    Falsifica o minimo: o indice e o embed. O DESPACHO de
    `MemoryStore.retrieve` — que e o objeto do teste — roda de verdade.
    """
    from edp.memory import store as store_mod

    entradas = [dict(entry_factory(text=f"memoria numero {i}"), id=f"id{i}")
                for i in range(6)]
    for e in entradas:
        e["embedding"] = [0.1] * 8

    ms = store_mod.MemoryStore("sessao_teste")
    ms._active_view().episodic.entries = list(entradas)

    monkeypatch.setattr(store_mod.MemoryStore, "_hybrid_index",
                        lambda self: {"entries": entradas, "hr": _HrFake(len(entradas)),
                                      "layer_of": ["episodic"] * len(entradas)})
    monkeypatch.setattr("edp.embeddings.embed_one",
                        lambda q: np.array([0.1] * 8, dtype=np.float32))
    monkeypatch.setattr(edp_config, "EDP_RANKING_TELEMETRY", True, raising=False)
    return ms


# ── A propriedade que faltava ────────────────────────────────────────────────

def test_o_caminho_hibrido_emite(store_com_hibrido, monkeypatch):
    """
    O caminho que a producao percorre DESDE 08/07. Este e o teste que teria
    poupado quatro turnos reais e a descoberta por telemetria vazia.
    """
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", True, raising=False)
    store_com_hibrido.retrieve("pergunta qualquer", top_k=3)
    ev = eventos()
    assert ev, "caminho hibrido nao emitiu — e ele que roda em producao"
    assert ev[-1]["metodo"] == "rrf"


def test_o_despacho_e_o_objeto_do_teste():
    """
    GUARDA DE FONTE: `MemoryStore.retrieve` devolve pelo hibrido ANTES de
    tocar o caminho cosseno. Se alguem inverter ou remover o despacho, o teste
    acima passaria a exercitar outra coisa sem avisar.
    """
    import inspect
    from edp.memory.store import MemoryStore
    fonte = inspect.getsource(MemoryStore.retrieve)
    i_flag = fonte.index("EDP_HYBRID_RETRIEVAL")
    i_ret  = fonte.index("return self._retrieve_hybrid")
    assert i_flag < i_ret


def test_ambos_os_caminhos_tem_emissao_na_fonte():
    """
    A licao, como mecanismo: emissor instalado em UM dos dois despachos volta
    a ser codigo morto na metade dos casos. Os tres blocos `(paridade)` que ja
    existiam no hibrido — pareto, monitor, contradiction — sao o padrao que a
    telemetria de ranking deveria ter seguido desde 13/08 e nao seguiu.
    """
    import inspect
    from edp.memory import store as store_mod
    from edp.memory.store import EpisodicMemory, MemoryStore

    assert "emit_ranking_decision" in inspect.getsource(EpisodicMemory.retrieve), \
        "caminho cosseno perdeu a emissao"
    assert "emit_ranking_decision" in inspect.getsource(MemoryStore._retrieve_hybrid), \
        "caminho hibrido perdeu a emissao"
    # e o modulo inteiro tem exatamente DOIS call sites, nao mais nem menos
    assert inspect.getsource(store_mod).count("emit_ranking_decision(") == 2


# ── O esquema nao mente sobre o que rodou ────────────────────────────────────

def test_estagio_inexistente_vira_None_e_nao_numero(store_com_hibrido, monkeypatch):
    """
    O hibrido nao tem filtro de sessao nem filtro_recusa. Repetir o numero
    anterior faria a cascata parecer completa e descreveria filtros que nao
    rodaram; zero pareceria corte total. `None` diz a verdade.
    """
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", True, raising=False)
    store_com_hibrido.retrieve("pergunta", top_k=3)
    e = eventos()[-1]
    assert e["n_apos_filtro_sessao"] is None
    assert e["n_apos_filtro_recusa"] is None
    assert e["n_apos_dedup"] == e["n_entregues"]


def test_metodo_distingue_os_dois_formatos_de_detalhe(store_com_hibrido, monkeypatch):
    """
    `detalhe.fatores` tem DEZ chaves no cosseno e {method,bm25,vec} no
    hibrido. Sem o campo `metodo`, os dois formatos ficariam indistinguiveis
    no mesmo arquivo e qualquer analise que somasse os dois estaria errada.
    """
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", True, raising=False)
    store_com_hibrido.retrieve("pergunta", top_k=3)
    e = eventos()[-1]
    assert e["metodo"] == "rrf"
    assert set(e["detalhe"][0]["fatores"]) == {"method", "bm25", "vec"}


def test_cascata_do_hibrido_e_monotonica(store_com_hibrido, monkeypatch):
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", True, raising=False)
    store_com_hibrido.retrieve("pergunta", top_k=3)
    e = eventos()[-1]
    assert e["n_avaliadas"] >= e["n_acima_do_piso"] >= e["n_entregues"]


def test_flag_off_nao_emite_no_hibrido(store_com_hibrido, monkeypatch):
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_RANKING_TELEMETRY", False, raising=False)
    store_com_hibrido.retrieve("pergunta", top_k=3)
    assert eventos() == []
