"""
exp017 Fase 0 (T4) — instrumentação dup_rate@k (log-only) no retrieval_kept.

Ponto (ii) fixado no T1b (RELATORIO_T1_EXP017.md): ctx.retrieval (pós
ContextWindowManager.build()) não tem ID; a propagação read-only
`_last_similarity_ids` (id(bloco) -> entry_id) resolve isso. Testa as duas
métricas (dup por ID = fenômeno D no resultado; dup por hash normalizado =
fenômeno A no resultado), o diagnóstico de truncamento (E3) e que a
instrumentação nunca quebra o retrieve mesmo com dados degenerados (E4/try-
except).
"""
from __future__ import annotations

import logging


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
    rt._llm_config = None
    rt.session_id = "test-session"
    return rt


def _dup_rate_lines(caplog):
    return [r.message for r in caplog.records if "[exp017] dup_rate" in r.message]


def _truncamento_lines(caplog):
    return [r.message for r in caplog.records if "[exp017] truncamento" in r.message]


def test_dup_rate_zero_quando_tudo_unico(caplog):
    results = [
        {"id": f"id-{i}", "text": f"memoria numero {i} sobre o assunto",
         "ranking_score": 0.9 - i * 0.01}
        for i in range(5)
    ]
    rt = _make_rt(results)
    with caplog.at_level(logging.INFO, logger="edp.llm_adapter"):
        rt._build_enriched_context("pergunta de teste sobre o assunto", "{context}")

    lines = _dup_rate_lines(caplog)
    assert lines, "linha [exp017] dup_rate não foi logada"
    assert "id=0/5" in lines[-1]
    assert "hash=0/5" in lines[-1]


def test_last_kept_ids_exposto_para_t5(caplog):
    results = [
        {"id": f"id-{i}", "text": f"memoria numero {i} sobre o assunto",
         "ranking_score": 0.9 - i * 0.01}
        for i in range(5)
    ]
    rt = _make_rt(results)
    with caplog.at_level(logging.INFO, logger="edp.llm_adapter"):
        rt._build_enriched_context("pergunta de teste sobre exposicao de ids", "{context}")

    assert sorted(rt._last_kept_ids) == sorted(e["id"] for e in results)
    assert len(rt._last_kept_hashes) == len(rt._last_kept_ids)


def test_dup_rate_detecta_fenomeno_d_por_id(caplog):
    # mesmo ID em duas "entries" do retrieve — fenômeno D reproduzido no resultado
    results = [
        {"id": "shared-id", "text": "primeira memoria distinta", "ranking_score": 0.9},
        {"id": "shared-id", "text": "segunda memoria com outro texto", "ranking_score": 0.8},
        {"id": "id-3", "text": "terceira memoria tambem distinta", "ranking_score": 0.7},
    ]
    rt = _make_rt(results)
    with caplog.at_level(logging.INFO, logger="edp.llm_adapter"):
        rt._build_enriched_context("pergunta de teste sobre duplicatas por id", "{context}")

    lines = _dup_rate_lines(caplog)
    assert lines
    assert "id=1/3" in lines[-1]


def test_dup_rate_detecta_fenomeno_a_por_hash(caplog):
    results = [
        {"id": "id-1", "text": "Q: oi\nA: oi", "ranking_score": 0.9},
        {"id": "id-2", "text": "q: oi\na: oi", "ranking_score": 0.85},  # normaliza igual
        {"id": "id-3", "text": "outro assunto totalmente diferente", "ranking_score": 0.7},
    ]
    rt = _make_rt(results)
    with caplog.at_level(logging.INFO, logger="edp.llm_adapter"):
        rt._build_enriched_context("pergunta de teste sobre duplicatas por hash", "{context}")

    lines = _dup_rate_lines(caplog)
    assert lines
    assert "hash=1/3" in lines[-1]
    assert "id=0/3" in lines[-1]  # IDs distintos — só o hash normalizado colide


def test_truncamento_log_kept_igual_offered_sem_pressao_de_budget(caplog):
    results = [
        {"id": f"id-{i}", "text": f"memoria curta {i}", "ranking_score": 0.5}
        for i in range(3)
    ]
    rt = _make_rt(results)
    with caplog.at_level(logging.INFO, logger="edp.llm_adapter"):
        rt._build_enriched_context("pergunta de teste sobre truncamento", "{context}")

    lines = _truncamento_lines(caplog)
    assert lines
    assert "kept=3 offered_mapeado=3 offered_total=3 truncado=False" in lines[-1]
    assert rt._last_trunc == {"kept": 3, "offered_mapeado": 3, "offered_total": 3}


def test_truncamento_entry_sem_id_nao_e_falso_positivo(caplog):
    # E3 FIX: entries sem `eid` truthy nunca entram em `_sim_id_map`
    # (llm_adapter.py ~2399-2400), logo nunca podem aparecer em "kept" — mas
    # ANTES do fix elas contavam em "offered" (len(_sim_blocks), TODO
    # bloco), inflando truncado=True sem nenhum corte real de budget. Cenário
    # motivador do fix: 2 entries com id, 3 sem id, textos curtos (nenhuma
    # pressão de budget).
    results = [
        {"id": "id-1", "text": "memoria com id um", "ranking_score": 0.9},
        {"id": "id-2", "text": "memoria com id dois", "ranking_score": 0.85},
        {"id": None, "text": "memoria sem id um", "ranking_score": 0.8},
        {"id": "", "text": "memoria sem id dois", "ranking_score": 0.75},
        {"id": None, "text": "memoria sem id tres", "ranking_score": 0.7},
    ]
    rt = _make_rt(results)
    with caplog.at_level(logging.INFO, logger="edp.llm_adapter"):
        rt._build_enriched_context("pergunta de teste sobre entries sem id", "{context}")

    lines = _truncamento_lines(caplog)
    assert lines
    assert "kept=2 offered_mapeado=2 offered_total=5 truncado=False" in lines[-1]
    assert rt._last_trunc == {"kept": 2, "offered_mapeado": 2, "offered_total": 5}


def test_truncamento_real_por_corte_de_budget_da_truncado_true(caplog):
    # Blocos enormes (muito acima de qualquer janela default local, ver
    # context_window_manager.py KNOWN_WINDOWS["default"]=4096 tokens) forçam
    # o corte guloso por budget (context_window_manager.py:320-327). Todos
    # os 5 têm id válido, então offered_mapeado == offered_total == 5 —
    # isola o corte real de budget do viés do E3 (id ausente).
    results = [
        {"id": f"id-{i}", "text": ("x" * 40000) + f" memoria {i}",
         "ranking_score": 0.9 - i * 0.01}
        for i in range(5)
    ]
    rt = _make_rt(results)
    with caplog.at_level(logging.INFO, logger="edp.llm_adapter"):
        rt._build_enriched_context("pergunta de teste sobre corte real de budget", "{context}")

    lines = _truncamento_lines(caplog)
    assert lines
    line = lines[-1]
    assert "offered_mapeado=5 offered_total=5" in line
    assert "truncado=True" in line
    assert rt._last_trunc["offered_mapeado"] == 5
    assert rt._last_trunc["kept"] < rt._last_trunc["offered_mapeado"]


def test_instrumentacao_nunca_quebra_retrieve_com_dados_degenerados(caplog):
    # ranking_score ausente/None e texto vazio — instrumentação deve ignorar
    # silenciosamente (try/except) e o método deve retornar normalmente.
    results = [
        {"id": None, "text": "", "ranking_score": None},
        {"id": "id-ok", "text": "memoria valida", "ranking_score": 0.5},
    ]
    rt = _make_rt(results)
    with caplog.at_level(logging.DEBUG, logger="edp.llm_adapter"):
        rendered, meta = rt._build_enriched_context("pergunta com dados degenerados", "{context}")

    assert isinstance(rendered, str)
    assert isinstance(meta, dict)
    falhas = [r.message for r in caplog.records if "instrumentacao T4 falhou" in r.message]
    assert not falhas, f"instrumentação T4 quebrou: {falhas}"
