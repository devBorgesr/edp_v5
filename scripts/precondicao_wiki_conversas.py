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


def textos_de_exports(path: Path) -> tuple[list[tuple[str, str]], int, int]:
    """
    Extrai o texto de cada turno dos exports do sensor.

    Inclui raw_text, blocos de texto E thinking (thinking_blocks +
    thinking_summaries, disponíveis desde a v4.9.0 do exportador) — o
    thinking é onde o raciocínio técnico costuma estar por extenso.

    Retorna (textos, n_turnos, n_conversas).
    """
    arquivos = ([path] if path.is_file()
                else sorted(p for p in path.rglob("*.json")))
    textos: list[tuple[str, str]] = []
    n_turnos = n_conv = 0

    for arq in arquivos:
        try:
            doc = json.loads(arq.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  AVISO: {arq.name} não parseou ({type(e).__name__}: {e})"
                  f" — NÃO contabilizado")
            continue
        # O exportador emite tanto 1 conversa por arquivo quanto um bundle
        # com centenas (ex.: 390 conversas / 3748 turnos num JSON de 46 MB).
        # Em vez de adivinhar a chave do invólucro, procura qualquer objeto
        # que tenha uma lista "turns", em qualquer profundidade razoável.
        for doc_conv in _achar_conversas(doc):
            turns = doc_conv["turns"]
            n_conv += 1
            titulo = str(doc_conv.get("title") or doc_conv.get("uuid")
                         or arq.stem)[:60]
            _coletar_turnos(turns, titulo, textos)
            n_turnos += sum(1 for t in turns if isinstance(t, dict))
    return textos, n_turnos, n_conv


def _achar_conversas(no, profundidade: int = 0):
    """Gera todo dict que tenha uma lista 'turns'. Tolerante ao invólucro."""
    if profundidade > 4:
        return
    if isinstance(no, dict):
        if isinstance(no.get("turns"), list):
            yield no
            return                      # não desce dentro dos próprios turnos
        for v in no.values():
            if isinstance(v, (dict, list)):
                yield from _achar_conversas(v, profundidade + 1)
    elif isinstance(no, list):
        for v in no:
            if isinstance(v, (dict, list)):
                yield from _achar_conversas(v, profundidade + 1)


def _coletar_turnos(turns: list, titulo: str,
                    textos: list[tuple[str, str]]) -> None:
    for i, t in enumerate(turns):
        if not isinstance(t, dict):
            continue
        partes: list[str] = []
        if t.get("raw_text"):
            partes.append(str(t["raw_text"]))
        for b in (t.get("blocks") or []):
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                partes.append(str(b["text"]))
        # thinking — v4.9.0 (commit 3bddd29 do exportador)
        for b in (t.get("thinking_blocks") or []):
            if isinstance(b, dict) and b.get("thinking"):
                partes.append(str(b["thinking"]))
            elif isinstance(b, str):
                partes.append(b)
        for s in (t.get("thinking_summaries") or []):
            if isinstance(s, str):
                partes.append(s)
        if partes:
            textos.append((f"{titulo}#t{i}", "\n".join(partes)))


def varredura_bruta(textos: list[tuple[str, str]]
                    ) -> list[tuple[str, list[str], int, str | None]]:
    """
    DIAGNÓSTICO SEPARADO — não é o critério congelado.

    Procura os termos-alvo no TEXTO CRU, com 100% de cobertura do corpus.
    Existe porque a pré-condição congelada só enxerga entries que já têm
    `cognitive_decisions` — na rodada de 07/08 isso foi 40% do store, e os
    outros 60% ficaram invisíveis.

    Responde uma pergunta a MONTANTE: o conteúdo está no corpus? Se um
    termo não aparece em texto nenhum, nenhuma extração vai fazê-lo
    aparecer. Se aparece, a extração provavelmente o alcança.
    """
    # Normaliza UMA vez por texto, não uma por alvo — com 46 MB / 3.748
    # turnos, NFKD repetido 5x é o gargalo.
    alvos_norm = [(q, sorted(ts), [normalizar(t) for t in ts]) for q, ts in ALVOS]
    n_por_alvo = [0] * len(alvos_norm)
    amostras: list[str | None] = [None] * len(alvos_norm)

    for fonte, txt in textos:
        tn = normalizar(txt)
        for k, (_, _, termos_n) in enumerate(alvos_norm):
            if any(t in tn for t in termos_n):
                n_por_alvo[k] += 1
                if amostras[k] is None:
                    amostras[k] = fonte

    return [(q, ts, n_por_alvo[k], amostras[k])
            for k, (q, ts, _) in enumerate(alvos_norm)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", type=Path, default=None,
                    help="raiz do store (pasta que contém sessions/). "
                         "Produção é C:\\edp_data (RUNBOOK.md:138) — prefira uma CÓPIA.")
    ap.add_argument("--exports", type=Path, default=None,
                    help="arquivo ou pasta de exports do sensor. Varre subpastas.")
    ap.add_argument("--out", type=Path,
                    default=Path("resultado_precondicao_wiki.json"))
    args = ap.parse_args()

    if not args.store and not args.exports:
        ap.error("informe --store, --exports, ou os dois. "
                 "Só exports é válido: a Wiki é sobre conversas (design §2).")

    entries = carregar_entries(args.store) if args.store else []
    if args.store:
        print(f"entries carregadas: {len(entries)}")
    else:
        print("sem --store: rodando só sobre exports (a pré-condição congelada "
              "depende de cognitive_decisions, que só existe no store — ela sai "
              "0/5 por ausência de fonte, não por ausência de conteúdo; o que "
              "vale nesta modalidade é o DIAGNÓSTICO de texto cru lá embaixo)")

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

    modo_store = bool(entries)

    if not modo_store:
        print("\n[E1/E3] pulados — não há store. cognitive_decisions só existe")
        print("        em entries do EDP; exports do sensor são texto cru.")
        print("        A pré-condição CONGELADA não é avaliável nesta modalidade.")
    else:
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

    if not modo_store:
        veredito = "NAO_AVALIAVEL"
        print("\nVEREDITO congelado: NÃO AVALIÁVEL sem store.")
        print("  Ver o diagnóstico de texto cru abaixo — é ele que vale aqui.")
    elif n_presentes <= CORTE_PARAR:
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

    # ── DIAGNÓSTICO (não é o critério): varredura de texto cru ───────────────
    # Cobertura 100%. A pré-condição acima só enxerga entries com
    # cognitive_decisions — na rodada de 07/08 isso foi 40% do store.
    textos: list[tuple[str, str]] = [
        (str(e.get("id", "?")), str(e.get("text") or "")) for e in entries
    ]
    n_turnos_exp = n_conv_exp = 0
    if args.exports:
        t_exp, n_turnos_exp, n_conv_exp = textos_de_exports(args.exports)
        textos.extend(t_exp)
        print(f"\n[exports] {n_conv_exp} conversas, {n_turnos_exp} turnos "
              f"(raw_text + blocks + thinking v4.9.0)")

    bruta = varredura_bruta(textos)
    n_bruta = sum(1 for _, _, n, _ in bruta if n > 0)

    print("\n" + "-" * 88)
    print(f"DIAGNÓSTICO — varredura de TEXTO CRU ({len(textos)} textos, "
          f"cobertura 100%)")
    print("  NÃO é o critério congelado. Responde a pergunta a montante:")
    print("  o conteúdo está no corpus, independentemente de ter sido extraído?")
    print("-" * 88)
    for query, termos, n, amostra in bruta:
        marca = f"{n:>4}x" if n else "   —"
        print(f"  [{marca}] {query[:58]}")
        if amostra:
            print(f"          1ª ocorrência: {amostra}")
    print("-" * 88)
    print(f"presentes em texto cru: {n_bruta}/5   "
          f"(pré-condição congelada: {n_presentes}/5)")

    if n_bruta > n_presentes:
        print(f"\n  LEITURA: {n_bruta - n_presentes} alvo(s) EXISTEM no corpus mas")
        print( "  não foram alcançados pela extração de conceitos. Isso não muda")
        print( "  o veredito congelado — muda o diagnóstico: o problema seria de")
        print( "  COBERTURA da extração, não de ausência de conteúdo.")
    elif n_bruta == 0:
        print("\n  LEITURA: os alvos não existem nem em texto cru. Ausência de")
        print("  conteúdo confirmada com cobertura total — nenhuma extração")
        print("  resolveria.")

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
        "diagnostico_texto_cru": {
            "n_textos": len(textos),
            "exports_conversas": n_conv_exp,
            "exports_turnos": n_turnos_exp,
            "presentes": n_bruta,
            "por_alvo": [
                {"query": q, "termos": t, "ocorrencias": n, "amostra": a}
                for q, t, n, a in bruta
            ],
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
