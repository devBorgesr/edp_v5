"""
tests/test_audit_retrieval_audit.py — T6 (degrau 1 do funil, NORTE.md).

Patologias PLANTADAS em fixtures sintéticas (não depende do store EDP).
"""
from __future__ import annotations

import json

import pytest

from audit.retrieval_audit import (
    analyze_cross_query_repetition,
    analyze_intra_query_duplication,
    analyze_score_scale,
    build_report,
    main,
    parse_jsonl,
    truncate_query,
)


def _write_jsonl(path, records: list[dict]) -> str:
    p = path / "export.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return str(p)


def _rec(query: str, results: list[dict]) -> dict:
    return {"query": query, "results": results}


# ── T3a: duplicação por hash ────────────────────────────────────────────────

def test_dup_por_hash_detectado():
    records = [_rec("q1", [
        {"id": "1", "text": "Gato preto", "score": 0.9},
        {"id": "2", "text": "  GATO   preto  ", "score": 0.85},  # normaliza igual ao #1
        {"id": "3", "text": "Cachorro branco", "score": 0.7},
        {"id": "4", "text": "Peixe dourado", "score": 0.6},
    ])]
    dup = analyze_intra_query_duplication(records, top_k=None)
    row = dup["rows"][0]
    assert row["dup_hash_count"] == 1
    assert row["dup_hash_rate"] == pytest.approx(0.25)


# ── T3a: duplicação por ID ───────────────────────────────────────────────────

def test_dup_por_id_detectado():
    records = [_rec("q1", [
        {"id": "x1", "text": "Texto A", "score": 0.9},
        {"id": "x1", "text": "Texto B totalmente diferente", "score": 0.5},
        {"id": "x2", "text": "Texto C", "score": 0.4},
    ])]
    dup = analyze_intra_query_duplication(records, top_k=None)
    row = dup["rows"][0]
    assert row["dup_id_count"] == 1
    assert row["dup_id_rate"] == pytest.approx(1 / 3)
    assert row["dup_hash_count"] == 0  # textos diferentes, sem dup por hash


# ── T3b: repetição cross-query, valores calculados à mão ────────────────────

def test_repeticao_cross_query_valores_a_mao():
    records = [
        _rec("q1", [
            {"id": "A", "text": "texto A", "score": 0.9},
            {"id": "B", "text": "texto B", "score": 0.8},
            {"id": "C", "text": "texto C", "score": 0.7},
        ]),
        _rec("q2", [
            {"id": "B", "text": "texto B", "score": 0.9},
            {"id": "C", "text": "texto C", "score": 0.8},
            {"id": "D", "text": "texto D", "score": 0.7},
        ]),
        _rec("q3", [
            {"id": "E", "text": "texto E", "score": 0.9},
            {"id": "F", "text": "texto F", "score": 0.8},
            {"id": "G", "text": "texto G", "score": 0.7},
        ]),
    ]
    rep = analyze_cross_query_repetition(records, top_k=None)

    # pares consecutivos: (q1,q2) overlap={B,C}=2/3 binário True (>= min(2,3));
    # (q2,q3) overlap=0/3 binário False
    assert rep["cons_binary_rate"] == pytest.approx(0.5)
    assert rep["cons_continuous_mean"] == pytest.approx((2 / 3 + 0) / 2)

    # matriz completa (q1,q2)=2/3 True; (q1,q3)=0 False; (q2,q3)=0 False
    assert rep["total_pairs"] == 3
    assert rep["ref_binary_rate"] == pytest.approx(1 / 3)
    assert rep["ref_continuous_mean"] == pytest.approx((2 / 3 + 0 + 0) / 3)


def test_repeticao_cross_query_insuficiente_nao_crasha():
    records = [_rec("q1", [{"id": "A", "text": "a", "score": 0.5}])]
    rep = analyze_cross_query_repetition(records, top_k=None)
    assert rep["insufficient"] is True
    assert rep["n_queries"] == 1


# ── T3c: escala esmagada ─────────────────────────────────────────────────────

def test_escala_esmagada_flagada():
    records = [_rec("q1", [
        {"id": "1", "text": "a", "score": 0.9},
        {"id": "2", "text": "b", "score": 0.5},
        {"id": "3", "text": "c", "score": 0.5},
        {"id": "4", "text": "d", "score": 0.5},
        {"id": "5", "text": "e", "score": 0.5},
    ])]
    scale = analyze_score_scale(records, top_k=None)
    # pares adjacentes: (.9,.5) dif, (.5,.5) igual, (.5,.5) igual, (.5,.5) igual -> 3/4
    assert scale["tie_fraction"] == pytest.approx(0.75)
    assert scale["flagged"] is True


# ── Fixture limpa: zero falsos positivos ────────────────────────────────────

def test_fixture_limpa_zero_falsos_positivos():
    records = [
        _rec(f"q{i}", [
            {"id": f"{i}-1", "text": f"conteudo unico {i} um", "score": 0.9},
            {"id": f"{i}-2", "text": f"conteudo unico {i} dois", "score": 0.7},
            {"id": f"{i}-3", "text": f"conteudo unico {i} tres", "score": 0.5},
            {"id": f"{i}-4", "text": f"conteudo unico {i} quatro", "score": 0.3},
        ])
        for i in range(3)
    ]
    dup = analyze_intra_query_duplication(records, top_k=None)
    scale = analyze_score_scale(records, top_k=None)

    assert dup["avg_dup_hash_rate"] == pytest.approx(0.0)
    assert dup["avg_dup_id_rate"] == pytest.approx(0.0)
    assert scale["flagged"] is False
    assert scale["tie_fraction"] == pytest.approx(0.0)


# ── T2: robustez a dado malformado ──────────────────────────────────────────

def test_jsonl_malformado_nao_crasha(tmp_path):
    p = tmp_path / "export.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"query": "ok", "results": [{"text": "t"}]}) + "\n")
        f.write("{not valid json\n")
        f.write(json.dumps({"results": [{"text": "sem query"}]}) + "\n")
        f.write(json.dumps({"query": "results nao lista", "results": "oops"}) + "\n")

    parsed = parse_jsonl(str(p))
    assert parsed.n_malformed_lines == 3
    assert len(parsed.records) == 1
    assert parsed.records[0]["query"] == "ok"


def test_resultado_sem_text_e_descartado_sem_quebrar_linha(tmp_path):
    p = tmp_path / "export.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"query": "q1", "results": [
            {"id": "1", "text": "valido"},
            {"id": "2"},  # sem "text" — descartado, não invalida a linha
        ]}) + "\n")

    parsed = parse_jsonl(str(p))
    assert parsed.n_malformed_lines == 0
    assert parsed.n_dropped_results == 1
    assert len(parsed.records[0]["results"]) == 1


# ── Degradação gracil sem id / sem score ────────────────────────────────────

def test_sem_id_degrada_com_nota():
    records = [_rec("q1", [
        {"id": None, "text": "texto A", "score": 0.9},
        {"id": None, "text": "texto B", "score": 0.5},
    ])]
    dup = analyze_intra_query_duplication(records, top_k=None)
    assert dup["any_id"] is False
    assert dup["avg_dup_id_rate"] is None
    assert dup["worst_id"] is None

    class _Empty:
        records = ["q1"]
        n_malformed_lines = 0
        n_dropped_results = 0
        malformed_examples = []

    scale = analyze_score_scale(records, top_k=None)
    rep = analyze_cross_query_repetition(records, top_k=None)
    report = build_report(_Empty(), dup, rep, scale, top_k=None)
    assert "métricas de duplicação por ID omitidas" in report


def test_sem_score_degrada_com_nota():
    records = [_rec("q1", [
        {"id": "1", "text": "texto A", "score": None},
        {"id": "2", "text": "texto B", "score": None},
    ])]
    scale = analyze_score_scale(records, top_k=None)
    assert scale["any_score"] is False
    assert scale["median_spread"] is None
    assert scale["tie_fraction"] is None
    assert scale["flagged"] is False

    dup = analyze_intra_query_duplication(records, top_k=None)
    rep = analyze_cross_query_repetition(records, top_k=None)

    class _Empty:
        records = ["q1"]
        n_malformed_lines = 0
        n_dropped_results = 0
        malformed_examples = []

    report = build_report(_Empty(), dup, rep, scale, top_k=None)
    assert "análise de escala omitida" in report


# ── top-k trunca consistentemente ───────────────────────────────────────────

def test_top_k_trunca_resultados():
    records = [_rec("q1", [
        {"id": "1", "text": "a", "score": 0.9},
        {"id": "2", "text": "b", "score": 0.8},
        {"id": "3", "text": "c", "score": 0.7},
        {"id": "4", "text": "d", "score": 0.6},
    ])]
    dup = analyze_intra_query_duplication(records, top_k=2)
    assert dup["rows"][0]["k"] == 2


# ── Fim a fim via CLI ────────────────────────────────────────────────────────

def test_main_end_to_end_gera_relatorio(tmp_path):
    input_path = _write_jsonl(tmp_path, [
        _rec("q1", [
            {"id": "1", "text": "Gato preto", "score": 0.9},
            {"id": "2", "text": "gato   PRETO", "score": 0.8},
        ]),
    ])
    output_path = str(tmp_path / "RELATORIO.md")
    rc = main([input_path, "-o", output_path])
    assert rc == 0
    content = open(output_path, encoding="utf-8").read()
    assert "Sumário executivo" in content
    assert "Limitações deste diagnóstico" in content


# ── Dogfood: BOM UTF-8 (exports Windows) ────────────────────────────────────

def test_bom_utf8_primeira_query_sai_limpa(tmp_path):
    p = tmp_path / "export.jsonl"
    line = json.dumps({"query": "vamos continuar", "results": [{"id": "1", "text": "t"}]}, ensure_ascii=False)
    with open(p, "w", encoding="utf-8") as f:
        f.write("﻿" + line + "\n")  # simula Set-Content -Encoding UTF8 do Windows

    parsed = parse_jsonl(str(p))
    assert parsed.n_malformed_lines == 0
    assert parsed.records[0]["query"] == "vamos continuar"


# ── Dogfood: truncamento em fronteira de palavra ────────────────────────────

def test_truncate_query_corta_em_fronteira_de_palavra():
    q = "abcde fghij klmno pqrst uvwxy"
    assert truncate_query(q, max_len=10) == "abcde…"


def test_truncate_query_nao_corta_query_curta():
    assert truncate_query("qual o prazo de reembolso?") == "qual o prazo de reembolso?"


# ── Dogfood: fixture limpa não ganha frase de impacto ───────────────────────

def test_fixture_limpa_sem_frase_de_impacto():
    records = [
        _rec(f"q{i}", [
            {"id": f"{i}-1", "text": f"conteudo unico {i} um", "score": 0.9},
            {"id": f"{i}-2", "text": f"conteudo unico {i} dois", "score": 0.7},
            {"id": f"{i}-3", "text": f"conteudo unico {i} tres", "score": 0.5},
            {"id": f"{i}-4", "text": f"conteudo unico {i} quatro", "score": 0.3},
        ])
        for i in range(3)
    ]
    dup = analyze_intra_query_duplication(records, top_k=None)
    rep = analyze_cross_query_repetition(records, top_k=None)
    scale = analyze_score_scale(records, top_k=None)

    class _Parse:
        records_ = records

        def __init__(self):
            self.records = records
            self.n_malformed_lines = 0
            self.n_dropped_results = 0
            self.malformed_examples = []

    report = build_report(_Parse(), dup, rep, scale, top_k=None)
    assert "tokens pagos em dobro" not in report
    assert "decidindo no escuro" not in report
    assert "favoritos" not in report
