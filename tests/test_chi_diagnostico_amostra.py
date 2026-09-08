"""
CHI — `n_samples=0` significava QUATRO coisas.

O QUE ESTES TESTES PROTEGEM

`health_index.compute()` logava `insufficient_data | n_samples=0` sem
distinguir:

    store nunca existiu no EDP_BASE_DIR resolvido
    store existe e esta vazio
    erro de I/O lendo events.jsonl
    ha eventos, mas nenhum na janela (ou < MIN_SAMPLES_FOR_STATS)

Medido em 07/09/2026: o kernel tem CINCO defaults distintos para EDP_BASE_DIR
(`grep -rn 'environ.get("EDP_BASE_DIR"' edp/`), quatro deles relativos a cwd,
e havia 41 `events.jsonl` divergentes no disco. "Zero amostras" quase nunca
significa "o sistema nao foi usado"; costuma significar "o store nao esta onde
o leitor procura".

`pareto_store.last_query_stats()` ja existia para isso (Divida #40) e nao era
consultada aqui.

O que falha se alguem desfizer isto:
  * as quatro causas voltam a colapsar    -> test_diagnostico_separa_*
  * `components` perde compatibilidade    -> test_components_mantem_reason
  * compute() para de devolver resultado  -> test_compute_devolve_resultado
"""
from __future__ import annotations

import pytest

from edp.runtime.health_index import CognitiveHealthIndex, HealthResult


class _GaussFalso:
    def __init__(self, stats=None): self._s = stats
    def stats_for_metric(self, metric, event_type): return self._s


class _Stats:
    def __init__(self, n): self.n_samples = n


@pytest.fixture
def chi(monkeypatch):
    def _monta(gauss_stats=None, query_stats=None):
        c = CognitiveHealthIndex.__new__(CognitiveHealthIndex)
        c.gauss = _GaussFalso(gauss_stats)
        c.bayes = None
        c._min_samples_gauss = 20
        c._min_samples_mature = 50
        if query_stats is not None:
            import edp.runtime.pareto_store as ps
            class _StoreFalso:
                def last_query_stats(self): return dict(query_stats)
            monkeypatch.setattr(ps, "get_pareto_store", lambda: _StoreFalso())
        return c
    return _monta


# ── a regressao que este arquivo existe para pegar ──────────────────────────

def test_compute_devolve_resultado(chi):
    """
    compute() tem de devolver HealthResult. Um helper inserido no lugar errado
    dentro do corpo do metodo deixa o resto como codigo morto depois do return
    e faz compute() devolver None — e `ast.parse` passa, porque sintaxe nao e
    verificacao.
    """
    c = chi(gauss_stats=_Stats(3), query_stats={})
    r = c.compute()
    assert isinstance(r, HealthResult), "compute() nao devolveu HealthResult"
    assert r.level == "INSUFFICIENT_DATA"
    assert r.samples_used == 3


# ── as quatro causas, separadas ─────────────────────────────────────────────

def test_diagnostico_separa_arquivo_ausente(chi):
    c = chi(gauss_stats=None,
            query_stats={"file_existed": False, "lines_read": 0,
                         "had_exception": False})
    d = c._diagnostico_de_amostra(0, None)
    assert "nao existe" in d and "EDP_BASE_DIR" in d


def test_diagnostico_separa_arquivo_vazio(chi):
    c = chi(gauss_stats=None,
            query_stats={"file_existed": True, "lines_read": 0,
                         "had_exception": False})
    d = c._diagnostico_de_amostra(0, None)
    assert "vazio" in d


def test_diagnostico_separa_erro_de_io(chi):
    c = chi(gauss_stats=None,
            query_stats={"file_existed": True, "lines_read": 0,
                         "had_exception": True})
    d = c._diagnostico_de_amostra(0, None)
    assert "I/O" in d


def test_diagnostico_separa_fora_da_janela(chi):
    """O caso medido em 07/09: 143 linhas no store, zero na janela de 7 dias."""
    c = chi(gauss_stats=None,
            query_stats={"file_existed": True, "lines_read": 143,
                         "events_yielded": 85, "had_exception": False})
    d = c._diagnostico_de_amostra(0, None)
    assert "143" in d and "85" in d and "janela" in d


def test_diagnostico_com_amostra_abaixo_do_minimo(chi):
    c = chi(gauss_stats=_Stats(11))
    d = c._diagnostico_de_amostra(11, _Stats(11))
    assert "11 amostras" in d and "abaixo do minimo" in d


# ── compatibilidade ─────────────────────────────────────────────────────────

def test_components_mantem_reason(chi):
    """`components` vai para o dashboard (websocket.py:675). Chave acrescida,
    nunca trocada."""
    c = chi(gauss_stats=_Stats(2), query_stats={})
    r = c.compute()
    assert "reason" in r.components
    assert "diagnostico" in r.components


def test_store_indisponivel_nao_derruba(chi, monkeypatch):
    """Diagnostico e instrumentacao: se o store nao responde, o CHI continua."""
    import edp.runtime.pareto_store as ps
    def explode(): raise RuntimeError("store fora")
    monkeypatch.setattr(ps, "get_pareto_store", explode)
    c = chi(gauss_stats=None)
    d = c._diagnostico_de_amostra(0, None)
    assert "store nao respondeu" in d
