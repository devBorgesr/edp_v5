#!/usr/bin/env python3
"""
scripts/precondicao_wiki_conversas.py — E1+E3, teste de pré-condição.

Contrato: docs/design_wiki_conversas.md §11 passo 1 (critério congelado
em b76828b, ANTES desta medição).

PERGUNTA: o corpus contém os conceitos necessários para a Wiki responder
as queries que o honeypot não respondeu?

CRITÉRIO (congelado):
  das 5 queries [N] do EXP017, quantas têm o termo central presente no
  conjunto de conceitos extraídos por cognitive_decisions?
    <= 1 de 5  -> PARAR. A camada 3 cai sem gastar LLM.
    >= 2 de 5  -> segue para o pré-registro do passo 2.

ASSIMÉTRICO DE PROPÓSITO: pode refutar, não pode confirmar. Ele NÃO diz
se uma página compilada vale mais que o turno cru, nem se o roteamento
por concepts[] funciona — só se há conteúdo com que trabalhar.

SUBPRODUTO OBRIGATÓRIO: cobertura de cognitive_decisions. O §7 do design
calculou US$0,29 assumindo reuso; cobertura baixa multiplica esse custo e
enfraquece a afirmação do §2 ("a Wiki não cria camada de extração nova").

SEM LLM, SEM CUSTO, READ-ONLY: lê os JSON direto, não instancia
EpisodicMemory (cujo __init__ faz mkdir, store.py:310), não chama
retrieve(), não chama nenhum provider.

USO:
  python scripts/precondicao_wiki_conversas.py --store "C:\\edp_data_fase0"
  python scripts/precondicao_wiki_conversas.py --store ... --exports "C:\\exports"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIELD = "cognitive_decisions"
PISO_OCORRENCIAS = 3        # design §3 E3: abaixo disso não vira página
CORTE_PARAR      = 1        # <= 1 de 5 presentes -> parar

# ── Alvos congelados (design §11, commit b76828b) ─────────────────────────────
# As 5 queries [N] do pool do EXP017 e os termos que contam como "presente".
ALVOS: list[tuple[str, set[str]]] = [
    ("qual é a capital da Mongólia mesmo?",
     {"mongolia", "mongólia"}),
    ("me explica de novo como funciona o RRF no retrieval híbrido",
     {"rrf", "reciprocal rank fusion"}),
    ("qual foi a última vez que ajustamos o piso do NOT_FOUND_FLOOR?",
     {"not_found_floor", "not found floor", "nf_floor", "not-found-floor"}),
    ("pode resumir o que ficou pendente no exp016?",
     {"exp016", "exp 016", "experimento 016"}),
    ("o que a gente decidiu sobre o calibrador Bayes-vs-Gauss?",
     {"bayes", "gauss", "calibrador"}),
]


def normalizar(s: str) -> str:
    """minúsculas, sem acento — para casar 'Mongólia' com 'mongolia'."""
    n = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in n if not unicodedata.combining(c))


def carregar_entries(base: Path) -> list[dict]:
    sessions = base / "sessions"
    if not sessions.is_dir():
        raise SystemExit(f"ERRO: {sessions} não existe. --store aponta para a "
                         f"raiz do store (a pasta que CONTÉM sessions/).")
    arquivos = sorted(sessions.glob("**/episodic.json")) + \
               sorted(sessions.glob("**/semantic.json"))
    if not arquivos:
        raise SystemExit(f"ERRO: nenhum episodic.json/semantic.json sob {sessions}.")

    entries: list[dict] = []
    for arq in arquivos:
        try:
            doc = json.loads(arq.read_text(encoding="utf-8"))
        except Exception as e:
            # NORTE §4.9: falha explícita, nunca pular em silêncio.
            print(f"  AVISO: {arq.parent.name}/{arq.name} não parseou "
                  f"({type(e).__name__}: {e}) — NÃO contabilizado")
            continue
        lista = doc.get("entries", doc) if isinstance(doc, dict) else doc
        if isinstance(lista, list):
            entries.extend(e for e in lista if isinstance(e, dict))
    return entries


def turnos_de_exports(path: Path) -> int:
    """Conta turnos de usuário/assistente nos exports (para dimensionar E2)."""
    arquivos = ([path] if path.is_file() else sorted(path.glob("*.json")))
    total = 0
    for arq in arquivos:
        try:
            doc = json.loads(arq.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  AVISO: {arq.name} não parseou ({type(e).__name__})")
            continue
        turns = doc.get("turns")
        if isinstance(turns, list):
            total += len(turns)
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", type=Path, required=True,
                    help="raiz do store (pasta que contém sessions/) — use uma CÓPIA")
    ap.add_argument("--exports", type=Path, default=None,
                    help="arquivo ou pasta de exports do sensor (opcional)")
    ap.add_argument("--out", type=Path,
                    default=Path("resultado_precondicao_wiki.json"))
    args = ap.parse_args()

    entries = carregar_entries(args.store)
    print(f"entries carregadas: {len(entries)}")

    # ── E1: cobertura de cognitive_decisions ─────────────────────────────────
    com_cd = [e for e in entries if isinstance(e.get(FIELD), dict)]
    cobertura = len(com_cd) / len(entries) if entries else 0.0

    # ── E3: agregação conceito -> ocorrências ────────────────────────────────
    ocorr: dict[str, list[str]] = defaultdict(list)
    dominios: Counter = Counter()
    for e in com_cd:
        cd = e[FIELD]
        eid = str(e.get("id", "?"))
        for c in cd.get("concepts") or []:
            if isinstance(c, str) and c.strip():
                ocorr[normalizar(c.strip())].append(eid)
        dm = cd.get("domain")
        if isinstance(dm, str) and dm.strip():
            dominios[normalizar(dm.strip())] += 1

    dist = Counter(len(v) for v in ocorr.values())
    passam = {c: v for c, v in ocorr.items() if len(v) >= PISO_OCORRENCIAS}

    # ── Pré-condição: os alvos existem? ──────────────────────────────────────
    vocab = set(ocorr) | set(dominios)
    vocab_blob = " | ".join(vocab)
    presentes = []
    for query, termos in ALVOS:
        achou = None
        for t in termos:
            tn = normalizar(t)
            if tn in vocab:
                achou = f"{t} (conceito exato)"
                break
            if tn in vocab_blob:
                achou = f"{t} (dentro de conceito)"
                break
        presentes.append((query, sorted(termos), achou))

    n_presentes = sum(1 for _, _, a in presentes if a)

    # ── Relatório ────────────────────────────────────────────────────────────
    print("\n" + "=" * 88)
    print("E1+E3 — PRÉ-CONDIÇÃO DA WIKI DE CONVERSAS (sem LLM, sem custo)")
    print("=" * 88)

    print(f"\n[E1] cobertura de cognitive_decisions: {len(com_cd)}/{len(entries)} "
          f"= {cobertura:.1%}")
    if cobertura < 0.50:
        falta = len(entries) - len(com_cd)
        print(f"  → {falta} entries precisariam de extração. O §7 do design")
        print(f"     calculou US$0,29 assumindo reuso; com esta cobertura o")
        print(f"     custo real sobe e a afirmação do §2 enfraquece.")

    print(f"\n[E3] conceitos distintos: {len(ocorr)} | domínios: {len(dominios)}")
    print(f"     passam o piso de {PISO_OCORRENCIAS} ocorrências: "
          f"{len(passam)}  ← nº de páginas de conceito")
    if dist:
        print("     distribuição de ocorrências:")
        for n in sorted(dist)[:8]:
            print(f"       {n:>3} ocorrência(s): {dist[n]:>4} conceito(s)")
        if len(dist) > 8:
            print(f"       ... (máx: {max(dist)} ocorrências)")

    if dominios:
        print("\n     top domínios:")
        for d, n in dominios.most_common(8):
            print(f"       {n:>4}  {d}")

    print("\n" + "-" * 88)
    print("PRÉ-CONDIÇÃO — as 5 queries [N] do EXP017 têm conteúdo no corpus?")
    print("-" * 88)
    for query, termos, achou in presentes:
        marca = "SIM" if achou else "não"
        print(f"  [{marca:>3}] {query[:58]:<60}")
        print(f"        termos aceitos: {termos}")
        if achou:
            print(f"        encontrado: {achou}")

    print("-" * 88)
    print(f"presentes: {n_presentes}/5   corte: <= {CORTE_PARAR} -> PARAR")

    if n_presentes <= CORTE_PARAR:
        veredito = "PARAR"
        print(f"\nVEREDITO: **PARAR** (§11)")
        print("  A Wiki não pode bater o baseline de 0/14 neste corpus por")
        print("  AUSÊNCIA DE CONTEÚDO — não por defeito de desenho. A camada 3")
        print("  cai sem gastar um centavo de LLM.")
    else:
        veredito = "SEGUE"
        print(f"\nVEREDITO: **SEGUE** para o passo 2 (pré-registro do §9).")
        print("  Atenção ao que este teste NÃO disse: se a página compilada vale")
        print("  mais que o turno cru, e se o roteamento por concepts[] funciona.")
        print("  Ambos exigem E2+E4 e julgamento humano.")

    if args.exports:
        n_turnos = turnos_de_exports(args.exports)
        print(f"\n[exports] {n_turnos} turnos fora do store — precisariam de")
        print(f"          extração no E2 (não entram na cobertura acima).")

    args.out.write_text(json.dumps({
        "contrato": "docs/design_wiki_conversas.md §11",
        "store": str(args.store),
        "n_entries": len(entries),
        "cobertura_cognitive_decisions": round(cobertura, 4),
        "n_com_cd": len(com_cd),
        "conceitos_distintos": len(ocorr),
        "conceitos_acima_do_piso": len(passam),
        "piso": PISO_OCORRENCIAS,
        "distribuicao_ocorrencias": dict(sorted(dist.items())),
        "top_dominios": dominios.most_common(20),
        "precondicao": [
            {"query": q, "termos": t, "encontrado": a} for q, t, a in presentes
        ],
        "presentes": n_presentes,
        "veredito": veredito,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
