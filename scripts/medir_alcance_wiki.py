#!/usr/bin/env python3
"""
scripts/medir_alcance_wiki.py — pré-condição de alcance da emenda E-3.2
de `docs/preregistro_gap_score.md`.

Responde a UMA pergunta: partindo de um nó, quantos nós a BFS alcança em
até 3 saltos? É o que decide se existe vizinhança pequena o bastante para
navegar, ou se navegar é ler o acervo com passos no meio.

CRITÉRIO CONGELADO EM E-3.2, antes de qualquer medição:
    navegação só carrega informação além da leitura global se a mediana
    do alcance em profundidade <=3 ficar entre 3 e 12 dos 16 nós.

MODELO CONGELADO EM E-3.1: **dirigido**. Link de wiki é dirigido — "A
cita B" não implica que B conheça A. `--nao-dirigido` existe só para
reproduzir a comparação registrada na emenda, nunca como default.

TRÊS ARMADILHAS QUE ESTE SCRIPT EXISTE PARA NÃO REPETIR — as três me
pegaram em 11/08, e as três são a mesma coisa: ler parte da estrutura e
tratar como o todo.

  1. **Aresta vem de DOIS lugares**: `links:` no frontmatter E `[[...]]`
     no corpo. Contando só o corpo eu publiquei 7 órfãs (eram 5) e 0
     links quebrados (eram 2) — o frontmatter carregava 28 das 29.
  2. **O conjunto de slugs tem de estar completo ANTES de filtrar.**
     Filtrando dentro do laço que ainda o preenche, toda referência a
     página posterior na ordem alfabética some. Isso me deu mediana 4,0
     onde o certo era 11,5.
  3. **Link quebrado não é aresta.** Descartar em silêncio esconde
     defeito; aqui ele é contado e reportado.

Sem LLM, sem rede, sem custo.

USO:  python3 scripts/medir_alcance_wiki.py [--paginas DIR] [--profundidade 3]
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

PROFUNDIDADE = 3        # E-3.2
FAIXA = (3, 12)         # E-3.2, congelada antes de medir

_RE_FM = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_RE_LINKS_FM = re.compile(r"^links:\s*\[(.*?)\]\s*$", re.M | re.DOTALL)
_RE_ASPAS = re.compile(r'"([^"]*)"')
_RE_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def arestas_declaradas(pasta: Path) -> tuple[dict[str, list[str]], set[str]]:
    """
    Devolve (declaradas_por_slug, slugs). SEM filtrar — o filtro exige o
    conjunto completo e por isso acontece depois (armadilha 2).
    """
    arquivos = sorted(pasta.glob("*.md"))
    slugs = {p.stem for p in arquivos}          # completo ANTES de tudo
    decl: dict[str, list[str]] = {}
    for p in arquivos:
        txt = p.read_text(encoding="utf-8", errors="replace")
        m = _RE_FM.match(txt)
        fm, corpo = (m.group(1), txt[m.end():]) if m else ("", txt)
        alvos: list[str] = []
        mf = _RE_LINKS_FM.search(fm)
        if mf:                                   # armadilha 1: frontmatter
            alvos += _RE_ASPAS.findall(mf.group(1))
        alvos += [x.strip() for x in _RE_WIKILINK.findall(corpo)]  # e corpo
        decl[p.stem] = alvos
    return decl, slugs


def construir(pasta: Path, dirigido: bool = True):
    """Devolve (grafo, slugs, quebrados). Grafo só com aresta válida."""
    decl, slugs = arestas_declaradas(pasta)
    grafo: dict[str, set[str]] = {s: set() for s in slugs}
    quebrados: list[tuple[str, str]] = []
    for origem, alvos in decl.items():
        for alvo in alvos:
            if alvo not in slugs:                # armadilha 3
                quebrados.append((origem, alvo))
                continue
            grafo[origem].add(alvo)
            if not dirigido:
                grafo[alvo].add(origem)
    return grafo, slugs, quebrados


def alcance(grafo: dict[str, set[str]], origem: str,
            profundidade: int = PROFUNDIDADE) -> int:
    """Nós distintos alcançáveis a partir de `origem`, incluindo ela."""
    vistos, fronteira = {origem}, {origem}
    for _ in range(profundidade):
        proxima = {v for u in fronteira for v in grafo.get(u, ())} - vistos
        if not proxima:
            break
        vistos |= proxima
        fronteira = proxima
    return len(vistos)


def medir(pasta: Path, dirigido: bool = True,
          profundidade: int = PROFUNDIDADE) -> dict:
    grafo, slugs, quebrados = construir(pasta, dirigido)
    por_no = {s: alcance(grafo, s, profundidade) for s in sorted(slugs)}
    vals = sorted(por_no.values())
    mediana = statistics.median(vals) if vals else 0
    lo, hi = FAIXA
    return {
        "nos": len(slugs),
        "arestas": sum(len(v) for v in grafo.values()) // (1 if dirigido else 2),
        "quebrados": quebrados,
        "por_no": por_no,
        "mediana": mediana,
        "min": min(vals) if vals else 0,
        "max": max(vals) if vals else 0,
        "fora_da_faixa": sum(1 for v in vals if not (lo <= v <= hi)),
        "passa": bool(vals) and lo <= mediana <= hi,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paginas", type=Path,
                    default=_ROOT / "edp_wiki" / "paginas")
    ap.add_argument("--profundidade", type=int, default=PROFUNDIDADE)
    ap.add_argument("--nao-dirigido", action="store_true",
                    help="só para reproduzir a comparação de E-3.2; "
                         "o modelo congelado é o dirigido")
    args = ap.parse_args()

    if not args.paginas.is_dir():
        print(f"alcance: {args.paginas} não existe — nada a medir "
              f"(edp_wiki/ é gitignored).")
        return 0

    dirigido = not args.nao_dirigido
    r = medir(args.paginas, dirigido, args.profundidade)
    if not r["nos"]:
        print(f"alcance: nenhuma página .md em {args.paginas}")
        return 0

    modelo = "DIRIGIDO (congelado em E-3.1)" if dirigido else "não-dirigido"
    print(f"modelo: {modelo}   profundidade: {args.profundidade}")
    print(f"nós: {r['nos']}   arestas válidas: {r['arestas']}   "
          f"links quebrados: {len(r['quebrados'])}")
    for o, a in r["quebrados"]:
        print(f"   quebrado: {o} -> {a}  (não conta como aresta)")

    print(f"\n{'nó de entrada':<50}{'alcance':>8}")
    for s, v in sorted(r["por_no"].items(), key=lambda kv: -kv[1]):
        print(f"  {s:<48}{v:>8}")

    lo, hi = FAIXA
    print(f"\nmediana {r['mediana']}   min {r['min']}   max {r['max']}   "
          f"fora de [{lo},{hi}]: {r['fora_da_faixa']}/{r['nos']}")
    print(f"pré-condição E-3.2 ({lo} <= mediana <= {hi}): "
          f"{'PASSA' if r['passa'] else 'FALHA'}")
    print("\nPré-condição satisfeita != hipótese comprovada. Isto responde "
          "'existe\nvizinhança pequena o bastante para experimentar?'. Não "
          "responde 'essa\nvizinhança contém o caminho necessário?'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
