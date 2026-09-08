#!/usr/bin/env python3
"""
audit/retrieval_audit.py — auditoria de retrieval sobre um export JSONL.

AUTOCONTIDO: zero imports de edp/, stdlib apenas. Motivo: extração futura
como ferramenta standalone deve ser copiar-a-pasta (ver audit/README
implícito nos comentários deste arquivo — não há doc separada por
mínimo viável).

Formato de entrada CONGELADO (uma linha JSON por query):
    {"query": "...", "results": [{"id": "...", "text": "...", "score": 0.83}, ...]}
"text" é OBRIGATÓRIO em cada resultado. "id" e "score" são OPCIONAIS —
sem "id", métricas por ID são omitidas do relatório (com nota); sem
"score", a análise de escala é omitida (com nota). Linhas malformadas
(JSON inválido, sem "query", "results" não é lista) são puladas,
contadas e reportadas — o script NUNCA crasha por dado ruim de cliente.
Resultados individuais sem "text" utilizável são descartados (contados
à parte) sem invalidar o resto da linha/query.

Uso:
    python audit/retrieval_audit.py export.jsonl -o RELATORIO.md
    python audit/retrieval_audit.py export.jsonl -o RELATORIO.md --top-k 10
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from pathlib import Path

# ── Heurísticas v1 (documentadas aqui, não são "verdade") ──────────────────
# "Escala esmagada": sinal de que os scores não discriminam bem entre
# resultados — thresholds escolhidos por julgamento de engenharia, não
# calibrados estatisticamente. Revisar se o cliente reportar falso positivo.
ADJACENT_TIE_FLAG_THRESHOLD = 0.20   # fração de empates adjacentes exatos
MEDIAN_SPREAD_FLAG_THRESHOLD = 0.10  # spread mediano relativo


def normalize_text(text) -> str:
    """strip + casefold + colapso de whitespace — a normalização do censo
    exp017 (scripts/censo_exp017.py, edp/memory/store.py:_normalize_text_exp017)."""
    return re.sub(r"\s+", " ", (text or "").strip().casefold())


def text_hash(text) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def truncate_query(q: str, max_len: int = 80) -> str:
    """Trunca para exibição no relatório, cortando em fronteira de palavra
    (nunca no meio de um token) e marcando o corte com "…"."""
    q = q.strip()
    if len(q) <= max_len:
        return q
    cut = q[:max_len]
    last_space = cut.rfind(" ")
    if last_space > 0:
        cut = cut[:last_space]
    return cut.rstrip() + "…"


# ── T2: parsing tolerante a dado ruim ───────────────────────────────────────

class ParseResult:
    def __init__(self):
        self.records: list[dict] = []       # [{"query": str, "results": [...]}]
        self.n_malformed_lines = 0
        self.malformed_examples: list[str] = []  # primeiras razões, para o relatório
        self.n_dropped_results = 0          # resultados descartados por falta de "text"

    def add_malformed(self, reason: str):
        self.n_malformed_lines += 1
        if len(self.malformed_examples) < 5:
            self.malformed_examples.append(reason)


def parse_jsonl(path: str) -> ParseResult:
    out = ParseResult()
    # utf-8-sig: exports gerados no Windows (Set-Content -Encoding UTF8)
    # carregam BOM (﻿) no início do arquivo — sem isso, a primeira
    # query sai suja no relatório.
    with open(path, "r", encoding="utf-8-sig") as f:
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as e:
                out.add_malformed(f"linha {lineno}: JSON inválido ({e.msg})")
                continue
            if not isinstance(obj, dict):
                out.add_malformed(f"linha {lineno}: não é um objeto JSON")
                continue
            query = obj.get("query")
            if not isinstance(query, str) or not query.strip():
                out.add_malformed(f"linha {lineno}: campo 'query' ausente ou vazio")
                continue
            raw_results = obj.get("results", [])
            if not isinstance(raw_results, list):
                out.add_malformed(f"linha {lineno}: campo 'results' não é lista")
                continue

            results = []
            for item in raw_results:
                if not isinstance(item, dict):
                    out.n_dropped_results += 1
                    continue
                text = item.get("text")
                if not isinstance(text, str) or not text.strip():
                    out.n_dropped_results += 1
                    continue
                rid = item.get("id")
                rid = rid if isinstance(rid, str) and rid.strip() else None
                score = item.get("score")
                score = score if isinstance(score, (int, float)) else None
                results.append({"id": rid, "text": text, "score": score})

            out.records.append({"query": query, "results": results})
    return out


def _slice_top_k(results: list[dict], top_k: int | None) -> list[dict]:
    return results[:top_k] if top_k else results


# ── T3a: duplicação intra-query ─────────────────────────────────────────────

def analyze_intra_query_duplication(records: list[dict], top_k: int | None) -> dict:
    rows = []
    dup_examples: list[str] = []
    any_id = any(r["id"] for rec in records for r in rec["results"])

    for rec in records:
        results = _slice_top_k(rec["results"], top_k)
        k = len(results)
        if k == 0:
            continue

        hashes = [text_hash(r["text"]) for r in results]
        dup_hash_count = k - len(set(hashes))
        dup_hash_rate = dup_hash_count / k

        # exemplos de texto duplicado (para o relatório)
        seen_hash: dict[str, str] = {}
        for r, h in zip(results, hashes):
            if h in seen_hash and len(dup_examples) < 5:
                snippet = r["text"].strip().replace("\n", " ")[:80]
                dup_examples.append(f'"{snippet}" (query: "{truncate_query(rec["query"])}")')
            seen_hash.setdefault(h, r["text"])

        ids = [r["id"] for r in results if r["id"]]
        k_id = len(ids)
        if k_id > 0:
            dup_id_count = k_id - len(set(ids))
            dup_id_rate = dup_id_count / k_id
        else:
            dup_id_count = None
            dup_id_rate = None

        rows.append({
            "query": rec["query"], "k": k,
            "dup_hash_count": dup_hash_count, "dup_hash_rate": dup_hash_rate,
            "dup_id_count": dup_id_count, "dup_id_rate": dup_id_rate,
        })

    hash_rates = [r["dup_hash_rate"] for r in rows]
    id_rates = [r["dup_id_rate"] for r in rows if r["dup_id_rate"] is not None]

    worst_hash = max(rows, key=lambda r: r["dup_hash_rate"]) if rows else None
    worst_id = max(
        (r for r in rows if r["dup_id_rate"] is not None),
        key=lambda r: r["dup_id_rate"], default=None,
    )

    return {
        "rows": rows,
        "any_id": any_id,
        "avg_dup_hash_rate": statistics.mean(hash_rates) if hash_rates else None,
        "worst_hash": worst_hash,
        "avg_dup_id_rate": statistics.mean(id_rates) if id_rates else None,
        "worst_id": worst_id,
        "dup_examples": dup_examples,
        "n_queries_sem_id": sum(1 for r in rows if r["dup_id_rate"] is None),
    }


# ── T3b: repetição cross-query ──────────────────────────────────────────────

def _identity(r: dict) -> str:
    """id quando disponível, senão hash do texto normalizado (fallback
    documentado: sem id não existe outra chave de identidade estável)."""
    return r["id"] if r["id"] else text_hash(r["text"])


def analyze_cross_query_repetition(records: list[dict], top_k: int | None) -> dict:
    query_sets = []
    for rec in records:
        results = _slice_top_k(rec["results"], top_k)
        if not results:
            continue
        query_sets.append((rec["query"], {_identity(r) for r in results}, len(results)))

    n = len(query_sets)
    if n < 2:
        return {"n_queries": n, "insufficient": True}

    # matriz completa par-a-par (i<j), continua e binária
    matrix = {}
    for i in range(n):
        for j in range(i + 1, n):
            _, set_i, k_i = query_sets[i]
            _, set_j, k_j = query_sets[j]
            k_pair = min(k_i, k_j)
            overlap = len(set_i & set_j)
            frac = overlap / k_pair if k_pair else 0.0
            threshold = min(2, k_pair)
            binary = overlap >= threshold if k_pair else False
            matrix[(i, j)] = {"overlap": overlap, "k_pair": k_pair, "frac": frac, "binary": binary}

    all_pairs = list(matrix.values())
    total_pairs = len(all_pairs)
    ref_binary_rate = sum(1 for p in all_pairs if p["binary"]) / total_pairs if total_pairs else None
    ref_continuous_mean = statistics.mean(p["frac"] for p in all_pairs) if all_pairs else None

    # pares CONSECUTIVOS na ordem do arquivo — CAVEAT: a ordem do export
    # determina o que "consecutivo" significa aqui, não há ordem canônica.
    consecutive = []
    for i in range(n - 1):
        q_i, _, _ = query_sets[i]
        q_j, _, _ = query_sets[i + 1]
        p = matrix[(i, i + 1)]
        consecutive.append({"query_a": q_i, "query_b": q_j, **p})

    n_cons = len(consecutive)
    cons_binary_rate = sum(1 for p in consecutive if p["binary"]) / n_cons if n_cons else None
    cons_continuous_mean = statistics.mean(p["frac"] for p in consecutive) if n_cons else None

    show_full_matrix = n <= 15

    return {
        "n_queries": n,
        "insufficient": False,
        "consecutive": consecutive,
        "cons_binary_rate": cons_binary_rate,
        "cons_continuous_mean": cons_continuous_mean,
        "ref_binary_rate": ref_binary_rate,
        "ref_continuous_mean": ref_continuous_mean,
        "total_pairs": total_pairs,
        "show_full_matrix": show_full_matrix,
        "matrix": matrix if show_full_matrix else None,
        "query_labels": [q for q, _, _ in query_sets],
        "off_diag_min": min((p["frac"] for p in all_pairs), default=None),
        "off_diag_max": max((p["frac"] for p in all_pairs), default=None),
    }


# ── T3c: escala de score ────────────────────────────────────────────────────

def analyze_score_scale(records: list[dict], top_k: int | None) -> dict:
    rows = []
    any_score = any(r["score"] is not None for rec in records for r in rec["results"])
    total_adjacent_pairs = 0
    total_tied_pairs = 0

    for rec in records:
        results = _slice_top_k(rec["results"], top_k)
        scores = [r["score"] for r in results if r["score"] is not None]
        if len(scores) < 2:
            continue

        mx, mn = max(scores), min(scores)
        spread = (mx - mn) / mx if mx else 0.0

        adjacent_pairs = len(scores) - 1
        tied = sum(1 for a, b in zip(scores, scores[1:]) if a == b)
        total_adjacent_pairs += adjacent_pairs
        total_tied_pairs += tied

        rows.append({
            "query": rec["query"], "k": len(scores),
            "spread": spread, "tied_adjacent": tied, "adjacent_pairs": adjacent_pairs,
        })

    spreads = [r["spread"] for r in rows]
    median_spread = statistics.median(spreads) if spreads else None
    tie_fraction = (total_tied_pairs / total_adjacent_pairs) if total_adjacent_pairs else None

    flagged = False
    if tie_fraction is not None and tie_fraction > ADJACENT_TIE_FLAG_THRESHOLD:
        flagged = True
    if median_spread is not None and median_spread < MEDIAN_SPREAD_FLAG_THRESHOLD:
        flagged = True

    return {
        "rows": rows,
        "any_score": any_score,
        "median_spread": median_spread,
        "avg_spread": statistics.mean(spreads) if spreads else None,
        "tie_fraction": tie_fraction,
        "flagged": flagged,
        "n_queries_sem_score": sum(
            1 for rec in records if not any(r["score"] is not None for r in _slice_top_k(rec["results"], top_k))
        ),
    }


# ── T4: relatório Markdown ──────────────────────────────────────────────────

# ── família 4: chunking ──────────────────────────────────────────────────────
# As três famílias anteriores DESCREVEM o comportamento do ranking. Esta é a
# primeira que APONTA a montante: ela mede propriedades do próprio texto
# entregue, e essas propriedades são consequência da estratégia de chunking.
#
# Ela não precisa de `id`, não precisa de `score`, não precisa de corpus e não
# precisa de rótulo. Só do `text`, que já é obrigatório no contrato.
#
# O QUE ELA NÃO MEDE, e precisa estar escrito antes dos números:
#   * não mede relevância — um chunk bem cortado pode ser inútil para a query;
#   * não mede qualidade de resposta — nenhum experimento aqui comparou
#     resposta com e sem corte no meio de frase;
#   * sobreposição adjacente ALTA não é defeito: janela deslizante é estratégia
#     deliberada e comum. O número diz qual estratégia está em uso, não se ela
#     está certa.

# Sem regex de proposito: a classe de caracteres exigiria escapar aspas
# dentro de aspas, e foi exatamente ai que a primeira versao quebrou.
_PONTO_FINAL = ".!?\u2026"           # . ! ? …
_FECHAMENTO = "\"'\u2019\u00bb)]"   # aspas, apostrofo tipografico, » ) ]


def _termina_em_frase(t: str) -> bool:
    """Fim de frase, tolerando fechamento de aspas/parenteses depois do ponto."""
    t = t.rstrip().rstrip(_FECHAMENTO)
    return bool(t) and t[-1] in _PONTO_FINAL
_SHINGLE_N = 5
_BOILER_MIN_CHARS = 20      # linha curta demais não é boilerplate, é ruído
_BOILER_MIN_CHUNKS = 3      # aparecer em 3 chunks distintos: repetição, não coincidência


def _shingles(texto: str) -> set:
    """
    Conjunto de n-gramas de palavra. Abaixo de N palavras, o próprio conjunto.

    A pontuação de borda é removida de cada token. Sem isso, a última palavra
    de um trecho (`hotel.`) nunca casa com a mesma palavra no trecho seguinte
    (`hotel`), e uma janela deslizante real mede sobreposição zero. Encontrado
    por teste que falhou em 07/09/2026.
    """
    palavras = [w.strip(_PONTO_FINAL + _FECHAMENTO + ",;:—-")
                for w in normalize_text(texto).split()]
    palavras = [w for w in palavras if w]
    if len(palavras) < _SHINGLE_N:
        return set(palavras)
    return {tuple(palavras[i:i + _SHINGLE_N])
            for i in range(len(palavras) - _SHINGLE_N + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def analyze_chunking(records: list[dict], top_k: int | None) -> dict:
    """
    Propriedades do texto entregue. Ver o bloco de comentário acima para o que
    esta família NÃO permite concluir.
    """
    comprimentos: list[int] = []
    sem_fim = 0
    inicio_minusculo = 0
    n = 0
    sobreposicoes: list[float] = []
    distintos: dict = {}          # hash -> texto, para não contar o mesmo duas vezes

    for rec in records:
        resultados = _slice_top_k(rec.get("results") or [], top_k)
        textos = [(r.get("text") or "") for r in resultados]
        textos = [t for t in textos if t.strip()]

        for t in textos:
            n += 1
            comprimentos.append(len(t))
            if not _termina_em_frase(t):
                sem_fim += 1
            primeiro = t.lstrip()[:1]
            if primeiro and primeiro.isalpha() and primeiro.islower():
                inicio_minusculo += 1
            distintos.setdefault(text_hash(t), t)

        # sobreposição entre POSIÇÕES CONSECUTIVAS do mesmo ranking
        for a, b in zip(textos, textos[1:]):
            sobreposicoes.append(_jaccard(_shingles(a), _shingles(b)))

    if n == 0:
        return {"n_textos": 0, "n_textos_distintos": 0, "comprimento": None,
                "frac_sem_fim_de_frase": None, "frac_inicio_minusculo": None,
                "sobreposicao_adjacente": None, "boilerplate": None}

    # boilerplate: linhas que reaparecem em muitos chunks DISTINTOS
    linha_em: dict = {}
    total_chars = 0
    for h, t in distintos.items():
        total_chars += len(t)
        vistas = set()
        for linha in t.splitlines():
            linha = linha.strip()
            if len(linha) >= _BOILER_MIN_CHARS and linha not in vistas:
                vistas.add(linha)
                linha_em.setdefault(linha, set()).add(h)
    repetidas = {l for l, onde in linha_em.items()
                 if len(onde) >= _BOILER_MIN_CHUNKS}
    chars_repetidos = 0
    for h, t in distintos.items():
        for linha in t.splitlines():
            if linha.strip() in repetidas:
                chars_repetidos += len(linha.strip())

    ordenados = sorted(comprimentos)

    def _p(frac: float) -> int:
        return ordenados[min(len(ordenados) - 1, int(frac * len(ordenados)))]

    return {
        "n_textos": n,
        "n_textos_distintos": len(distintos),
        "comprimento": {
            "mediana": int(statistics.median(comprimentos)),
            "p10": _p(0.10), "p90": _p(0.90),
            "min": ordenados[0], "max": ordenados[-1],
        },
        "frac_sem_fim_de_frase": sem_fim / n,
        "frac_inicio_minusculo": inicio_minusculo / n,
        "sobreposicao_adjacente": (
            {"mediana": statistics.median(sobreposicoes),
             "n_pares": len(sobreposicoes)} if sobreposicoes else None),
        "boilerplate": {
            "n_linhas_repetidas": len(repetidas),
            "frac_chars": (chars_repetidos / total_chars) if total_chars else 0.0,
            "min_chunks": _BOILER_MIN_CHUNKS,
        },
    }


def _pct(x) -> str:
    return f"{x * 100:.1f}%" if x is not None else "N/D"


def build_report(parse: ParseResult, dup: dict, rep: dict, scale: dict,
                 top_k: int | None, chunk: dict | None = None) -> str:
    k_desc = str(top_k) if top_k else "tamanho de cada export (sem truncamento)"
    n_queries = len(parse.records)

    achados = []
    if dup["avg_dup_hash_rate"] is not None:
        achados.append(
            f"- Em média, **{_pct(dup['avg_dup_hash_rate'])}** dos resultados de cada busca "
            f"são textos duplicados (mesmo conteúdo, presença repetida no mesmo retorno)."
        )
    if dup["worst_hash"] is not None and dup["worst_hash"]["dup_hash_rate"] > 0:
        w = dup["worst_hash"]
        impacto = (
            f" Na pior busca, {w['dup_hash_count']} de cada {w['k']} resultados eram repetição "
            f"do mesmo texto — tokens pagos em dobro ocupando o lugar de conteúdo novo."
        )
        achados.append(
            f"- Pior caso: a busca \"{truncate_query(w['query'])}\" retornou "
            f"{_pct(w['dup_hash_rate'])} de conteúdo duplicado.{impacto}"
        )
    if not rep.get("insufficient") and rep["cons_continuous_mean"] is not None:
        linha = (
            f"- Buscas consecutivas no export compartilham em média "
            f"**{_pct(rep['cons_continuous_mean'])}** dos resultados (referência sob "
            f"aleatoriedade: {_pct(rep['ref_continuous_mean'])}) — isso pode ser normal "
            f"quando as buscas são sobre o mesmo assunto, não é necessariamente falha."
        )
        if (rep["ref_continuous_mean"] is not None
                and rep["cons_continuous_mean"] > rep["ref_continuous_mean"]):
            linha += (
                " Buscas diferentes recebem as mesmas respostas — o sistema tem \"favoritos\" "
                "independentes da pergunta."
            )
        achados.append(linha)
    if scale["flagged"]:
        achados.append(
            "- **Escala de relevância comprometida**: os scores retornados discriminam mal "
            "entre resultados bons e ruins (muitos empates e/ou pouca variação). Os scores não "
            "distinguem resultado bom de ruim — qualquer corte por relevância que o sistema "
            "faça está decidindo no escuro."
        )
    if not achados:
        achados.append("- Nenhum padrão de duplicação, repetição ou escala achatada saltou aos olhos nesta amostra.")
    if parse.n_malformed_lines:
        achados.append(
            f"- {parse.n_malformed_lines} linha(s) do export vieram malformadas e foram ignoradas "
            f"(ver seção de limitações)."
        )

    lines = []
    lines.append("# Relatório de Auditoria de Retrieval\n")
    lines.append(
        f"Export analisado: {n_queries} queries válidas, k considerado: {k_desc}.\n"
    )

    # 1. Sumário executivo
    lines.append("## 1. Sumário executivo\n")
    lines.extend(achados)
    lines.append("")

    # 2. Números por família
    lines.append("## 2. Números por família\n")

    lines.append("### 2a. Duplicação intra-query\n")
    if not dup["any_id"]:
        lines.append("_Nenhum resultado trouxe campo `id` — métricas de duplicação por ID omitidas (nota T2)._\n")
    lines.append("| Métrica | Valor |")
    lines.append("|---|---|")
    lines.append(f"| dup_rate@k por hash (média) | {_pct(dup['avg_dup_hash_rate'])} |")
    if dup["worst_hash"]:
        lines.append(f"| dup_rate@k por hash (pior query) | {_pct(dup['worst_hash']['dup_hash_rate'])} — \"{truncate_query(dup['worst_hash']['query'])}\" |")
    if dup["any_id"]:
        lines.append(f"| dup_rate@k por ID (média, queries com ID) | {_pct(dup['avg_dup_id_rate'])} |")
        if dup["worst_id"]:
            lines.append(f"| dup_rate@k por ID (pior query) | {_pct(dup['worst_id']['dup_id_rate'])} — \"{truncate_query(dup['worst_id']['query'])}\" |")
        if dup["n_queries_sem_id"]:
            lines.append(f"| queries sem nenhum ID presente | {dup['n_queries_sem_id']} |")
    lines.append("")
    if dup["dup_examples"]:
        lines.append("Exemplos de texto duplicado encontrado:\n")
        for ex in dup["dup_examples"]:
            lines.append(f"- {ex}")
        lines.append("")

    lines.append("### 2b. Repetição cross-query\n")
    lines.append(
        "> **Caveat obrigatório**: \"consecutivo\" é definido pela ORDEM DAS LINHAS no arquivo "
        "exportado — se o export não preserva a ordem cronológica/real das buscas, esta métrica "
        "reflete a ordem do arquivo, não necessariamente a experiência real do usuário.\n"
    )
    if rep.get("insufficient"):
        lines.append(f"_Apenas {rep['n_queries']} query(ies) com resultados — pares insuficientes para esta análise._\n")
    else:
        lines.append("| Visão | Valor observado (pares consecutivos) | Referência neutra (todos os pares, aleatória) |")
        lines.append("|---|---|---|")
        lines.append(f"| Binária (overlap ≥ min(2,k)) | {_pct(rep['cons_binary_rate'])} | {_pct(rep['ref_binary_rate'])} |")
        lines.append(f"| Contínua (\\|∩\\|/k, média) | {_pct(rep['cons_continuous_mean'])} | {_pct(rep['ref_continuous_mean'])} |")
        lines.append("")
        lines.append(
            f"Matriz completa par-a-par: {rep['total_pairs']} pares avaliados entre "
            f"{rep['n_queries']} queries. Overlap fora-da-diagonal: min={_pct(rep['off_diag_min'])}, "
            f"max={_pct(rep['off_diag_max'])}.\n"
        )
        if rep["show_full_matrix"]:
            labels = rep["query_labels"]
            header = "| |" + "|".join(f"q{j+1}" for j in range(len(labels))) + "|"
            lines.append(header)
            lines.append("|---" * (len(labels) + 1) + "|")
            for i in range(len(labels)):
                row = [f"q{i+1}"]
                for j in range(len(labels)):
                    if j <= i:
                        row.append("")
                    else:
                        row.append(f"{rep['matrix'][(i, j)]['frac']*100:.0f}%")
                lines.append("|" + "|".join(row) + "|")
            lines.append("")
            lines.append("Legenda: " + "; ".join(f"q{i+1}=\"{truncate_query(q)}\"" for i, q in enumerate(labels)))
            lines.append("")
        else:
            lines.append(
                f"_Matriz completa omitida do relatório impresso (>{15} queries; "
                f"calculada internamente para a referência neutra acima)._\n"
            )

    lines.append("### 2c. Escala de score\n")
    if not scale["any_score"]:
        lines.append("_Nenhum resultado trouxe campo `score` — análise de escala omitida (nota T2)._\n")
    else:
        lines.append("| Métrica | Valor |")
        lines.append("|---|---|")
        lines.append(f"| Spread relativo (mediana) | {_pct(scale['median_spread'])} |")
        lines.append(f"| Spread relativo (média) | {_pct(scale['avg_spread'])} |")
        lines.append(f"| Empates exatos adjacentes (fração dos pares) | {_pct(scale['tie_fraction'])} |")
        lines.append(f"| **Escala esmagada?** | {'SIM' if scale['flagged'] else 'não'} |")
        if scale["n_queries_sem_score"]:
            lines.append(f"| queries sem score utilizável | {scale['n_queries_sem_score']} |")
        lines.append("")
        lines.append(
            f"_Heurística v1 (não é verdade absoluta): flag disparada se empates adjacentes > "
            f"{ADJACENT_TIE_FLAG_THRESHOLD*100:.0f}% dos pares OU spread mediano < "
            f"{MEDIAN_SPREAD_FLAG_THRESHOLD*100:.0f}%. Ver `audit/retrieval_audit.py` (thresholds no topo do arquivo)._\n"
        )

    # 3. Interpretação
    lines.append("## 3. O que cada número significa\n")
    lines.append(
        "**Duplicação intra-query** (2a) mede o mesmo conteúdo aparecendo mais de uma vez "
        "dentro do retorno de UMA busca. Isso quase sempre é desperdício: espaço de contexto "
        "e atenção do modelo gastos em repetição, não em cobertura. Um dup_rate@k alto é "
        "candidato forte a correção (deduplicação no pipeline de retrieval).\n"
    )
    lines.append(
        "**Repetição cross-query** (2b) mede o quanto buscas diferentes retornam os mesmos "
        "documentos. Isto **não é automaticamente um problema**: se duas perguntas seguidas são "
        "sobre o mesmo assunto, é esperado — e correto — que tragam os mesmos documentos "
        "(topicalidade). O sinal de alerta real é quando o valor observado está muito acima da "
        "referência neutra (aleatória) E as perguntas consecutivas no export não parecem, pelo "
        "conteúdo, ser sobre o mesmo tema — isso sugere que o sistema está sempre devolvendo os "
        "mesmos itens \"populares\" independente da pergunta.\n"
    )
    lines.append(
        "**Escala de score** (2c) mede se os números de relevância retornados pelo sistema "
        "realmente diferenciam bons resultados de ruins. Uma escala esmagada (pouca variação, "
        "muitos empates) não impede o sistema de funcionar, mas invalida qualquer uso do score "
        "para decisões downstream (corte por threshold, priorização, exibição de \"confiança\" "
        "ao usuário) — o número deixa de carregar informação.\n"
    )

    # 4. Limitações
    lines.append("## 4. Limitações deste diagnóstico\n")
    lines.append(
        "Este relatório analisa exclusivamente o **retorno do retrieval** (o que foi buscado e "
        "o que voltou). Ele não permite ver, e portanto não avalia:\n"
    )
    lines.append("- **Write-side**: como e quando os dados entraram no índice/store (a causa raiz de duplicação pode estar na escrita, não na busca).")
    lines.append("- **Truncamento de contexto**: se o que chega ao modelo é cortado antes ou depois destes resultados, por limite de janela de contexto.")
    lines.append("- **Qualidade da resposta final**: um retrieval limpo não garante uma resposta boa, e um retrieval com ruído não garante uma resposta ruim — isso depende do que o modelo faz com o material recuperado.")
    lines.append(
        f"\nLinhas malformadas no export: {parse.n_malformed_lines} "
        f"(puladas, não processadas). Resultados individuais descartados por falta de texto "
        f"utilizável: {parse.n_dropped_results}."
    )
    if parse.malformed_examples:
        lines.append("\nExemplos de linhas malformadas:")
        for ex in parse.malformed_examples:
            lines.append(f"- {ex}")

    if chunk and chunk.get("n_textos"):
        c = chunk
        comp = c["comprimento"]
        lines.append("")
        lines.append("## Chunking — propriedades do texto entregue")
        lines.append("")
        lines.append(
            "Esta seção é a única que aponta **a montante**. As três anteriores "
            "descrevem o comportamento do ranking; esta mede propriedades do "
            "próprio texto, e essas propriedades são consequência da estratégia "
            "de chunking."
        )
        lines.append("")
        lines.append(f"Base: {c['n_textos']} trechos entregues, "
                     f"{c['n_textos_distintos']} distintos.")
        lines.append("")
        lines.append("| grandeza | valor |")
        lines.append("|---|---|")
        lines.append(f"| comprimento mediano | {comp['mediana']} caracteres |")
        lines.append(f"| comprimento p10 / p90 | {comp['p10']} / {comp['p90']} |")
        lines.append(f"| comprimento mín / máx | {comp['min']} / {comp['max']} |")
        lines.append(f"| trechos que **não terminam em fim de frase** | "
                     f"{_pct(c['frac_sem_fim_de_frase'])} |")
        lines.append(f"| trechos que começam em letra minúscula | "
                     f"{_pct(c['frac_inicio_minusculo'])} |")
        if c["sobreposicao_adjacente"]:
            sa = c["sobreposicao_adjacente"]
            lines.append(f"| sobreposição entre posições consecutivas (mediana) | "
                         f"{_pct(sa['mediana'])} em {sa['n_pares']} pares |")
        b = c["boilerplate"]
        lines.append(f"| texto em linhas repetidas em ≥{b['min_chunks']} trechos | "
                     f"{_pct(b['frac_chars'])} ({b['n_linhas_repetidas']} linhas) |")
        lines.append("")
        lines.append("**O que esta seção NÃO diz.** Nenhum destes números mede "
                     "relevância, e nenhum mede qualidade de resposta — nenhum "
                     "experimento comparou resposta com e sem corte no meio de "
                     "frase. Sobreposição alta entre posições consecutivas **não "
                     "é defeito**: janela deslizante é estratégia deliberada e "
                     "comum; o número diz qual estratégia está em uso, não se ela "
                     "está certa.")

    return "\n".join(lines) + "\n"


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Auditoria de retrieval sobre export JSONL.")
    ap.add_argument("input", help="Caminho do export JSONL (formato: {\"query\":..., \"results\":[...]})")
    ap.add_argument("-o", "--output", required=True, help="Caminho do relatório Markdown de saída")
    ap.add_argument("--top-k", type=int, default=None, help="Trunca cada query aos top-k resultados antes de medir (default: usa o export como veio)")
    args = ap.parse_args(argv)

    if not Path(args.input).exists():
        print(f"[erro] arquivo não encontrado: {args.input}", file=sys.stderr)
        return 2

    parse = parse_jsonl(args.input)
    dup = analyze_intra_query_duplication(parse.records, args.top_k)
    rep = analyze_cross_query_repetition(parse.records, args.top_k)
    scale = analyze_score_scale(parse.records, args.top_k)

    chunk = analyze_chunking(parse.records, args.top_k)

    report = build_report(parse, dup, rep, scale, args.top_k, chunk)
    Path(args.output).write_text(report, encoding="utf-8")
    print(f"Relatório escrito em {args.output} ({len(parse.records)} queries válidas, "
          f"{parse.n_malformed_lines} linhas malformadas ignoradas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
