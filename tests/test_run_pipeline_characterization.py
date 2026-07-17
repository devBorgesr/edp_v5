"""
T3 — Caracterização de run_pipeline(): a rede de segurança do corte do
split-brain semântico (semantic_memory.py / get_pipeline_memory()).

Achado da Fase 0.5 (M1, FASE0_5_MEDICAO_SPLITBRAIN.md): `.reduction_pct` e
`.aggregate_score` são os ÚNICOS dois campos de PipelineResult lidos pelos
chamadores vivos (edp/api/routes/llm.py:76, edp/api/routes/websocket.py:
633/645/647). Nada derivado de semantic_memory.retrieve()/consolidate_
from_episodes() alcança esses dois campos — mem_results morre em duas
variáveis não-lidas fora de debug (retrieval_quality→trace só com debug=True,
nunca ativo no caminho vivo; reflection, dead store).

Este teste deve continuar VERDE, sem alteração, através de T4a (remove
leitura) e T4b (remove escrita): antes do corte, prova a inércia via
monkeypatch (o CONTEÚDO da consulta semântica não influencia os dois campos);
depois do corte, a mesma comparação continua trivialmente verdadeira (o
código que seria monkeypatchado não é mais exercitado).

Fixture sintética — sem gt_*.csv, sem dado real.
"""
from __future__ import annotations

import uuid

from edp.pipeline import run_pipeline

FIXED_TEXT = (
    "A memória episódica registra eventos específicos vividos em um "
    "determinado momento e contexto, enquanto a memória semântica organiza "
    "conhecimento geral sobre o mundo, independente de quando foi adquirido. "
    "Pesquisas em neurociência cognitiva sugerem que essas duas formas de "
    "memória dependem de circuitos parcialmente distintos, envolvendo o "
    "hipocampo de maneira mais central na consolidação episódica do que na "
    "manutenção de fatos semânticos já consolidados. Compreender essa "
    "distinção ajuda a explicar por que certas lesões cerebrais prejudicam "
    "a lembrança de eventos pessoais recentes sem afetar o conhecimento "
    "factual acumulado ao longo da vida, revelando a arquitetura modular "
    "da cognição humana e suas implicações para sistemas artificiais de "
    "memória que tentam replicar esse mesmo tipo de separação funcional."
)
FIXED_QUESTION = "qual a diferença entre memória episódica e semântica?"


def _agg_tuple(agg) -> tuple | None:
    if agg is None:
        return None
    return (
        round(agg.final_score, 6),
        round(agg.relevance, 6),
        round(agg.novelty, 6),
        round(agg.entropy, 6),
        round(agg.diversity, 6),
        round(agg.redundancy, 6),
        round(agg.confidence_weight, 6),
        agg.decision,
    )


def test_caracterizacao_reduction_pct_e_aggregate_score(isolated_base_dir):
    """Os dois únicos campos lidos pelos chamadores vivos existem e têm
    forma válida com inputs fixos — baseline de forma, não de valor mágico
    (valor exato é comparado no teste de inércia abaixo)."""
    sid = f"char-forma-{uuid.uuid4().hex[:8]}"
    result = run_pipeline(FIXED_TEXT, FIXED_QUESTION, session_id=sid)

    assert result.tokens_original > 0
    assert isinstance(result.reduction_pct, float)
    assert 0.0 <= result.reduction_pct <= 100.0
    assert result.aggregate_score is not None
    assert 0.0 <= result.aggregate_score.final_score <= 1.5


def test_inercia_conteudo_da_consulta_semantica_nao_afeta_saida(isolated_base_dir, monkeypatch):
    """Prova de inércia (M1): troca o CONTEÚDO retornado por
    semantic_memory.retrieve()/consolidate_from_episodes() por algo
    completamente diferente do real — reduction_pct e aggregate_score devem
    permanecer byte-idênticos, porque nada deles alcança esses dois campos."""
    sid_baseline = f"char-baseline-{uuid.uuid4().hex[:8]}"
    baseline = run_pipeline(FIXED_TEXT, FIXED_QUESTION, session_id=sid_baseline)

    try:
        import edp.semantic_memory as sm_mod
    except ModuleNotFoundError:
        sm_mod = None  # pós-T4d: módulo aposentado — nada para monkeypatchar,
        # a comparação abaixo continua válida (trivialmente idêntica, já que
        # run_pipeline não chama mais nada do split-brain semântico).

    if sm_mod is not None:
        def _fabricated_retrieve(self, question, top_k=3):
            return [
                {"id": f"fabricado-{i}", "text": "x" * 500, "score": 999.0}
                for i in range(50)
            ]

        def _noop_consolidate(self, episodes):
            # Deliberadamente NÃO persiste nada — comportamento diferente do
            # real, para provar que o resultado de run_pipeline não depende disso.
            return None

        monkeypatch.setattr(sm_mod.SemanticMemory, "retrieve", _fabricated_retrieve, raising=False)
        monkeypatch.setattr(
            sm_mod.SemanticMemory, "consolidate_from_episodes", _noop_consolidate, raising=False
        )

    sid_patched = f"char-patched-{uuid.uuid4().hex[:8]}"
    patched = run_pipeline(FIXED_TEXT, FIXED_QUESTION, session_id=sid_patched)

    assert patched.reduction_pct == baseline.reduction_pct
    assert _agg_tuple(patched.aggregate_score) == _agg_tuple(baseline.aggregate_score)
