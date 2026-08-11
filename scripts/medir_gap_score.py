#!/usr/bin/env python3
"""
scripts/medir_gap_score.py — executa o pré-registro `docs/preregistro_gap_score.md`.

Mede as DUAS fórmulas de Gap Score congeladas no §2 contra o gabarito da
rodagem cruzada (§3) e aplica as quatro condições do §4.

PROTOCOLO (§2, congelado antes de rodar):
    tokenização e stopwords vêm de `edp.wiki`, IMPORTADAS SEM ALTERAÇÃO.
    Nenhum tokenizer novo é escrito aqui — se fosse, daria para ajustá-lo
    até o resultado sair.

O IDF é recalculado sobre as 16 páginas de `edp_wiki/paginas/` (o `_idf()`
do `edp.wiki` é sobre a wiki de comunidades do graphify, outro corpus),
mas com a MESMA fórmula log((N+1)/(df+1)).

Imprime o detalhamento por pergunta — quais termos casaram e quais não —
para que cada número seja conferível à mão. Sem LLM, sem rede, sem custo.

USO:  python3 scripts/medir_gap_score.py
"""
from __future__ import annotations

import hashlib
import math
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# ── PROTOCOLO §2: importado, não reescrito ───────────────────────────────────
from edp.wiki import _RE_TOKEN, _STOPWORDS  # noqa: E402

PAGINAS = _ROOT / "edp_wiki" / "paginas"
TAU = 0.5  # §4 — limiar tirado do próprio plano, não escolhido aqui

# §3 — gabarito do resultado do W (07/08)
CONJ_A = ["Q1", "Q6", "Q11", "Q12"]          # a wiki tinha material
CONJ_B = ["Q2", "Q3", "Q4", "Q5", "Q7", "Q8", "Q9", "Q10"]  # não tinha

PERGUNTAS = [
    ("Q1", "A", "Já testamos rotear recuperação por similaridade de embedding? O que o dado mostrou?"),
    ("Q2", "B", "O que foi tentado antes do form-check `Q:`/`A:` para identificar um turno de conversa, e por que falhou?"),
    ("Q3", "B", "Por que `SESSION_BOOST_FACTOR` vale 1.60 e não outro valor?"),
    ("Q4", "B", "Por que existe a flag `EDP_HYBRID_RETRIEVAL` e o que ela muda no piso de conteúdo tóxico?"),
    ("Q5", "B", "Por que o exportador passou a mandar timestamp em segundos?"),
    ("Q6", "A", "De onde saiu o número de 47% de perda de faixa dinâmica do blob `Q+A`?"),
    ("Q7", "B", "Qual incidente real motivou a calibração do boost de sessão?"),
    ("Q8", "B", "Como a posição sobre 'competir com Mem0/Zep/Letta' mudou ao longo do tempo?"),
    ("Q9", "B", "O que mudou na definição do que o EDP é, entre abril e agosto de 2026?"),
    ("Q10", "B", "Há afirmações opostas registradas sobre o valor de manter contas gratuitas?"),
    ("Q11", "A", "Que tipo de premissa costumo assumir sem verificar antes de desenhar?"),
    ("Q12", "A", "Quais predições foram registradas e depois refutadas? Há padrão nelas?"),
    ("N1", "ctrl", "Como funciona o RRF no retrieval híbrido?"),
    ("N2", "ctrl", "Qual é a capital da Mongólia?"),
    ("N3", "ctrl", "Me lembra o que a gente discutiu"),
]


def toks(texto: str) -> set[str]:
    """Tokens crus — usado para o CORPO das páginas (sem filtro)."""
    return {t.lower() for t in _RE_TOKEN.findall(texto)}


def toks_consulta(q: str) -> set[str]:
    """Mesma regra do `edp.wiki.buscar()`: len>2 e fora das stopwords."""
    return {t.lower() for t in _RE_TOKEN.findall(q)
            if len(t) > 2 and t.lower() not in _STOPWORDS}


def carregar() -> dict[str, dict]:
    pags = {}
    for p in sorted(PAGINAS.glob("*.md")):
        txt = p.read_text(encoding="utf-8", errors="replace")
        fm = re.search(r"\A---\n(.*?)\n---\n", txt, re.DOTALL)
        status = "hipotese"
        corpo = txt
        if fm:
            m = re.search(r"^status:\s*(\S+)", fm.group(1), re.M)
            if m:
                status = m.group(1).strip()
            corpo = txt[fm.end():]
        titulo = ""
        if fm:
            mt = re.search(r'^titulo:\s*"?(.*?)"?\s*$', fm.group(1), re.M)
            if mt:
                titulo = mt.group(1)
        pags[p.stem] = {"status": status, "corpo": corpo,
                        "titulo": titulo, "frontmatter": fm.group(1) if fm else ""}
    return pags


def main() -> int:
    pags = carregar()
    if not pags:
        raise SystemExit(f"ERRO: nenhuma página em {PAGINAS}")

    # §7 — a wiki não pode ter mudado desde 07/08; hash para o registro
    h = hashlib.sha256()
    for slug in sorted(pags):
        h.update((PAGINAS / f"{slug}.md").read_bytes())
    print(f"páginas: {len(pags)}   sha256 do conjunto: {h.hexdigest()[:16]}")

    dist = {}
    for info in pags.values():
        dist[info["status"]] = dist.get(info["status"], 0) + 1
    print(f"status:  {dist}")
    print(f"protocolo: _RE_TOKEN={_RE_TOKEN.pattern!r}  "
          f"stopwords={len(_STOPWORDS)} (importados de edp.wiki)")

    nv = [s for s, i in pags.items() if i["status"] in ("nucleo", "verificado")]
    print(f"páginas nucleo/verificado usadas na cobertura: {len(nv)} de {len(pags)}")

    # Vocabulário coberto = união dos tokens das páginas nucleo/verificado
    # (corpo + slug + título), exatamente como a fórmula do §2 especifica.
    coberto: set[str] = set()
    for s in nv:
        coberto |= toks(pags[s]["corpo"]) | toks(s) | toks(pags[s]["titulo"])
    print(f"vocabulário coberto: {len(coberto)} termos únicos")

    # IDF sobre as 16 páginas, mesma fórmula do edp.wiki._idf()
    N = len(pags)
    df: dict[str, int] = {}
    for s, i in pags.items():
        for t in toks(i["corpo"]) | toks(s) | toks(i["titulo"]):
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log((N + 1) / (d + 1)) for t, d in df.items()}

    # E-1.1 — termo nunca visto (df=0) vale log((N+1)/(0+1)), não 0.0
    IDF0 = math.log(N + 1)
    print(f"E-1.1: peso de termo com df=0 -> {IDF0:.3f} (era 0.0)")

    print("\n" + "=" * 100)
    print(f"{'#':<5}{'cl':<6}{'g_bruto':>9}{'g_idf':>9}{'g_idf0':>9}   "
          f"termos da consulta  (ausente = MAIÚSCULA)")
    print("-" * 100)

    res: dict[str, tuple[float, float, float]] = {}
    for qid, classe, texto in PERGUNTAS:
        termos = toks_consulta(texto)
        if not termos:
            res[qid] = (1.0, 1.0, 1.0)
            print(f"{qid:<5}{classe:<6}{1.0:>9.3f}{1.0:>9.3f}{1.0:>9.3f}   (sem termo)")
            continue

        presentes = termos & coberto
        g_bruto = 1.0 - len(presentes) / len(termos)

        peso_tot = sum(idf.get(t, 0.0) for t in termos)
        g_idf = (1.0 - sum(idf.get(t, 0.0) for t in presentes) / peso_tot
                 if peso_tot else 1.0)

        peso_tot0 = sum(idf.get(t, IDF0) for t in termos)
        g_idf0 = (1.0 - sum(idf.get(t, IDF0) for t in presentes) / peso_tot0
                  if peso_tot0 else 1.0)

        res[qid] = (g_bruto, g_idf, g_idf0)
        vis = " ".join(sorted(t if t in presentes else t.upper() for t in termos))
        print(f"{qid:<5}{classe:<6}{g_bruto:>9.3f}{g_idf:>9.3f}{g_idf0:>9.3f}   {vis[:62]}")

    print("=" * 100)

    def avalia(idx: int, nome: str) -> bool:
        a_ok = sum(1 for q in CONJ_A if res[q][idx] < TAU)
        b_ok = sum(1 for q in CONJ_B if res[q][idx] >= TAU)
        q3, n3 = res["Q3"][idx], res["N3"][idx]
        cond = [
            ("a", f"A com gap<{TAU}: {a_ok}/4 (exige >=3)", a_ok >= 3),
            ("b", f"B com gap>={TAU}: {b_ok}/8 (exige >=7)", b_ok >= 7),
            ("c", f"Q3 gap={q3:.3f} (exige >={TAU})", q3 >= TAU),
            ("d", f"N3 gap={n3:.3f} (exige >={TAU})", n3 >= TAU),
        ]
        print(f"\nfórmula {nome}:")
        for k, desc, ok in cond:
            print(f"   ({k}) {'OK ' if ok else 'NAO'}  {desc}")
        passa = all(ok for _, _, ok in cond)
        print(f"   VEREDITO: {'PASSA' if passa else 'FALHA'}")
        return passa

    p_bruto = avalia(0, "BRUTA  (§2, como o plano escreveu)")
    p_idf = avalia(1, "IDF    (§2, ponderada)")
    p_idf0 = avalia(2, "IDF⁰   (E-1.1, df=0 vale o máximo)")

    print("\n" + "=" * 100)
    print("N1/N2 medidos e relatados, FORA do critério (§5):")
    for q in ("N1", "N2"):
        print(f"   {q}: bruto={res[q][0]:.3f}  idf={res[q][1]:.3f}  idf0={res[q][2]:.3f}")
    print("=" * 100)
    print(f"RESULTADO: bruta={'PASSA' if p_bruto else 'FALHA'}   "
          f"idf={'PASSA' if p_idf else 'FALHA'}   "
          f"idf0={'PASSA' if p_idf0 else 'FALHA'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
