"""
exp017 Fase 0 (T3) — EDP_RETRIEVE_SHUFFLE.

Complementa o teste flag-off (tests/test_flag_off_byte_identical.py) provando
que a flag, quando ON, de fato reordena o conjunto top-k antes do builder
(llm_adapter.py:2334-2354) sem remover nenhum item, e que a seed é
determinística POR QUERY (mesma query -> mesma permutação entre runs;
queries diferentes -> permutações diferentes, evitando degenerar no
fenômeno C — PRE_REGISTRO_EXP017.md, H2).
"""
from __future__ import annotations


class _FakeMemory:
    def __init__(self, results):
        self._results = results

    def retrieve(self, query, top_k=5, min_score=0.20):
        return list(self._results)


def _make_rt(results):
    from edp.llm_adapter import EDPRuntime

    rt = EDPRuntime.__new__(EDPRuntime)
    rt._memory = _FakeMemory(results)
    rt._operational_mode = "cognitive"
    rt._co_occurrence = None
    rt.session_id = "test-session"
    return rt


def _retrieval_texts(blocks):
    return [b.split("] ", 1)[-1] for b in blocks if "memoria numero" in b]


def test_shuffle_on_reordena_sem_remover(monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_SHUFFLE", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_SHUFFLE_SEED", "20260719", raising=False)

    fixed_order = [
        {"id": f"id-{i}", "text": f"memoria numero {i} sobre o assunto",
         "ranking_score": 0.9 - i * 0.01}
        for i in range(5)
    ]
    rt = _make_rt(fixed_order)
    blocks, hits = rt._retrieve_context("pergunta especifica sobre o assunto ordenado")

    obtido = _retrieval_texts(blocks)
    esperado = [e["text"] for e in fixed_order]
    assert set(obtido) == set(esperado)  # zero remoção — mesmo conjunto
    assert obtido != esperado            # ordem mudou (seed fixa reordena)


def test_shuffle_e_deterministico_por_query(monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_SHUFFLE", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_SHUFFLE_SEED", "20260719", raising=False)

    fixed_order = [
        {"id": f"id-{i}", "text": f"memoria numero {i} sobre o assunto",
         "ranking_score": 0.9 - i * 0.01}
        for i in range(5)
    ]

    rt1 = _make_rt(fixed_order)
    rt2 = _make_rt(fixed_order)
    blocks1, _ = rt1._retrieve_context("mesma query para reprodutibilidade")
    blocks2, _ = rt2._retrieve_context("mesma query para reprodutibilidade")
    assert _retrieval_texts(blocks1) == _retrieval_texts(blocks2)


def test_shuffle_permutacao_difere_entre_queries(monkeypatch):
    import edp.config as edp_config
    monkeypatch.setattr(edp_config, "EDP_RETRIEVE_SHUFFLE", True, raising=False)
    monkeypatch.setattr(edp_config, "EDP_SHUFFLE_SEED", "20260719", raising=False)

    fixed_order = [
        {"id": f"id-{i}", "text": f"memoria numero {i} sobre o assunto",
         "ranking_score": 0.9 - i * 0.01}
        for i in range(5)
    ]

    rt1 = _make_rt(fixed_order)
    rt2 = _make_rt(fixed_order)
    blocks1, _ = rt1._retrieve_context("primeira query distinta sobre o tema")
    blocks2, _ = rt2._retrieve_context("segunda query bem diferente da primeira")
    # seed única global degeneraria no fenômeno C (listas iguais -> permutação
    # igual); seed por query deve produzir permutações distintas aqui.
    assert _retrieval_texts(blocks1) != _retrieval_texts(blocks2)
