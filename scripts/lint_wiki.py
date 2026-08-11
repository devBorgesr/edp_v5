#!/usr/bin/env python3
"""
scripts/lint_wiki.py — aplica as regras mecanizáveis do `docs/WIKI_SCHEMA.md`
às páginas de `edp_wiki/paginas/`.

POR QUE EXISTE: a regra 5 do schema diz "página órfã é defeito — **o lint
pega**", e o §7.3 diz que `arquivo:linha` que sumiu é "detectável por
lint, sem julgamento". O lint não existia. Isto é engenharia, não
experimento: os números já foram medidos à mão em 11/08, e o script passa
a ser a fonte deles.

O LINT REPORTA, NÃO REBAIXA. Lição de `aee20f9`: aplicar rebaixamento
automático derrubava `contagem-de-nos-como-medida-de-vagueza` e
`memoria-do-edp-nao-contem-o-edp`, duas das quatro páginas de núcleo.
Órfã não é sinônimo de fraca. Quem move camada é o humano.

ARESTA = união de `links:` do frontmatter com `[[...]]` do corpo. Contar
só o corpo foi um erro meu em 11/08 (deu 7 órfãs e 0 links quebrados; o
certo é 5 e 2) — o frontmatter carrega 28 das 29 arestas distintas.

O QUE NÃO É VERIFICÁVEL AQUI, e por quê — o §4 do schema lista cinco
itens de lint; três exigem julgamento e este script não finge cobri-los:

  - "afirmações sem fonte": só dá para checar que `fontes:` não está
    vazio. Saber se CADA afirmação do corpo tem lastro é leitura humana.
  - "status: verificado cuja fonte foi refutada depois": exige saber o
    que foi refutado. Não é mecânico.
  - "contradições entre páginas que ninguém marcou como contestado":
    exige julgamento semântico.
  - "páginas não tocadas cujo assunto teve commit novo": exige mapear
    página -> área de código. Fora de escopo.

Sem LLM, sem rede. `edp_wiki/` é gitignored: se a pasta não existe, sai
0 com aviso (CI e clone limpo não a têm).

USO:  python3 scripts/lint_wiki.py [--paginas CAMINHO]
SAÍDA: 0 se não há ERRO; 1 se há. AVISO nunca falha o processo.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# Vocabulário do schema — §7.1 (camadas) e §2 (anatomia). Não inventar.
STATUS_VALIDOS = {"nucleo", "verificado", "contestado", "hipotese", "obsoleto"}
TIPOS_VALIDOS = {"achado", "conceito", "decisao", "componente", "sessao"}
CHAVES_OBRIGATORIAS = ("titulo", "tipo", "status", "fontes", "criada",
                       "atualizada", "links")

_RE_FM = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_RE_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
# Aceita `arq`, `arq:N` e `arq:N-M` — o intervalo é a forma que as páginas
# mais usam (`edp/llm_adapter.py:1189-1225`), e a primeira versão deste
# lint a deixava passar como "não checável".
_RE_ARQ_LINHA = re.compile(
    r"^(?P<arq>[\w./\-]+\.\w+)(?::(?P<linha>\d+)(?:-(?P<fim>\d+))?)?$")

# Entradas de topo deste repositório — usadas para decidir se uma `fonte:`
# aponta para cá ou para fora (exportador, Downloads, outro repo).
_TOPO_REPO = {p.name for p in _ROOT.iterdir() if not p.name.startswith(".")}


def _campo(fm: str, chave: str) -> str | None:
    m = re.search(rf"^{chave}:\s*(.*)$", fm, re.M)
    return m.group(1).strip() if m else None


def _lista(fm: str, chave: str) -> list[str] | None:
    """Lê `chave: ["a", "b"]`. Devolve None se a chave não existe."""
    m = re.search(rf"^{chave}:\s*\[(.*?)\]\s*$", fm, re.M | re.DOTALL)
    if not m:
        return None
    return [x for x in re.findall(r'"([^"]*)"', m.group(1))]


def _commit_existe(sha: str) -> bool:
    try:
        return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                              cwd=_ROOT, capture_output=True).returncode == 0
    except FileNotFoundError:
        return True  # sem git no ambiente: não acusa o que não pode checar


def carregar(pasta: Path) -> dict[str, dict]:
    pags: dict[str, dict] = {}
    for p in sorted(pasta.glob("*.md")):
        txt = p.read_text(encoding="utf-8", errors="replace")
        m = _RE_FM.match(txt)
        fm, corpo = (m.group(1), txt[m.end():]) if m else ("", txt)
        pags[p.stem] = {
            "arquivo": p, "tem_fm": bool(m), "fm": fm, "corpo": corpo,
            "links_fm": _lista(fm, "links") or [],
            "links_corpo": [x.strip() for x in _RE_WIKILINK.findall(corpo)],
            "fontes": _lista(fm, "fontes"),
        }
    return pags


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paginas", type=Path, default=_ROOT / "edp_wiki" / "paginas")
    args = ap.parse_args()

    if not args.paginas.is_dir():
        print(f"lint: {args.paginas} não existe — nada a checar "
              f"(edp_wiki/ é gitignored; clone limpo e CI não a têm).")
        return 0

    pags = carregar(args.paginas)
    if not pags:
        print(f"lint: nenhuma página .md em {args.paginas}")
        return 0

    erros: list[str] = []
    avisos: list[str] = []
    nao_checaveis: list[str] = []
    slugs = set(pags)

    # Grau de entrada = união frontmatter + corpo (ver docstring)
    entrada: dict[str, int] = {s: 0 for s in slugs}
    for s, d in pags.items():
        for alvo in set(d["links_fm"]) | set(d["links_corpo"]):
            if alvo in entrada:
                entrada[alvo] += 1

    for s, d in sorted(pags.items()):
        saida = set(d["links_fm"]) | set(d["links_corpo"])

        # ── ERRO: contrato estrutural do §2 ─────────────────────────────
        if not d["tem_fm"]:
            erros.append(f"{s}: sem frontmatter YAML (§2)")
            continue
        for k in CHAVES_OBRIGATORIAS:
            if _campo(d["fm"], k) is None:
                erros.append(f"{s}: frontmatter sem `{k}:` (§2)")

        st = _campo(d["fm"], "status")
        if st and st not in STATUS_VALIDOS:
            erros.append(f"{s}: status `{st}` fora do vocabulário do §7.1")
        tp = _campo(d["fm"], "tipo")
        if tp and tp not in TIPOS_VALIDOS:
            erros.append(f"{s}: tipo `{tp}` fora do vocabulário do §2")

        # Regra 1 — nada sem fonte (checável só como "lista não vazia")
        if d["fontes"] is not None and not d["fontes"]:
            erros.append(f"{s}: `fontes: []` vazio (regra 1)")

        # Regra 5 — link tem de apontar para página que existe
        for alvo in sorted(saida):
            if alvo not in slugs:
                erros.append(f"{s}: link `[[{alvo}]]` aponta para página inexistente (regra 5)")

        # §7.2 — núcleo exige evidência externa declarada
        if st == "nucleo" and _campo(d["fm"], "camada_evidencia") is None:
            erros.append(f"{s}: núcleo sem `camada_evidencia:` (§7.2)")

        # §7.3 — `arquivo:linha` que sumiu; o schema chama isto de
        # "detectável por lint, sem julgamento".
        #
        # SÓ checa o que é DESTE repositório. `content.js` e
        # `interceptor.js` vivem no repo do exportador; `Downloads/...` é
        # caminho da máquina do pesquisador. A primeira versão deste lint
        # (11/08) tratou os três como arquivo sumido e acusou duas páginas
        # boas. Lint que acusa página boa ensina a ignorar o lint — é o
        # `aee20f9` outra vez, regra larga demais derrubando o que presta.
        for f in (d["fontes"] or []):
            if f.startswith("commit:"):
                sha = f.split(":", 1)[1].strip()
                if sha and not _commit_existe(sha):
                    erros.append(f"{s}: fonte `{f}` — commit não existe (§7.3)")
                continue
            mm = _RE_ARQ_LINHA.match(f)
            if not mm or "/" not in mm.group("arq"):
                nao_checaveis.append(f"{s}: {f}")
                continue
            if mm.group("arq").split("/", 1)[0] not in _TOPO_REPO:
                nao_checaveis.append(f"{s}: {f}")
                continue
            alvo = _ROOT / mm.group("arq")
            if not alvo.exists():
                erros.append(f"{s}: fonte `{f}` — arquivo não existe (§7.3)")
            elif mm.group("linha"):
                n = max(int(mm.group("linha")), int(mm.group("fim") or 0))
                total = len(alvo.read_text(encoding="utf-8",
                                           errors="replace").splitlines())
                if n > total:
                    erros.append(f"{s}: fonte `{f}` — arquivo tem {total} "
                                 f"linhas, citação vai até {n} (§7.3)")

        # ── AVISO: defeito real, conserto editorial ─────────────────────
        if entrada[s] == 0:
            avisos.append(f"{s}: órfã — nenhuma página linka para ela (regra 5)")
        if not saida:
            avisos.append(f"{s}: sem link de saída (regra 5)")

        so_fm = set(d["links_fm"]) - set(d["links_corpo"])
        so_corpo = set(d["links_corpo"]) - set(d["links_fm"])
        if so_fm or so_corpo:
            det = []
            if so_fm:
                det.append(f"só no frontmatter: {sorted(so_fm)}")
            if so_corpo:
                det.append(f"só no corpo: {sorted(so_corpo)}")
            avisos.append(f"{s}: `links:` diverge do corpo — {'; '.join(det)}")

    # ── Relatório ────────────────────────────────────────────────────────
    dist: dict[str, int] = {}
    for d in pags.values():
        k = _campo(d["fm"], "status") or "<sem status>"
        dist[k] = dist.get(k, 0) + 1
    arestas = sum(len(set(d["links_fm"]) | set(d["links_corpo"]))
                  for d in pags.values())

    print(f"páginas: {len(pags)}   arestas distintas: {arestas}   "
          f"órfãs: {sum(1 for s in slugs if entrada[s] == 0)}")
    print(f"status:  {dict(sorted(dist.items()))}")

    if erros:
        print(f"\nERRO ({len(erros)}) — quebra o contrato do schema:")
        for e in erros:
            print(f"  ✗ {e}")
    if avisos:
        print(f"\nAVISO ({len(avisos)}) — defeito real, conserto editorial:")
        for a in avisos:
            print(f"  ! {a}")
    if not erros and not avisos:
        print("\nlimpo.")

    if nao_checaveis:
        print(f"\nFONTES NÃO CHECÁVEIS ({len(nao_checaveis)}) — fora deste "
              f"repositório ou sem forma de caminho.\nNão são defeito; "
              f"estão aqui para o lint declarar sua própria cobertura:")
        for f in nao_checaveis:
            print(f"  · {f}")

    print("\nNão verificado aqui (exige julgamento — ver docstring): cada "
          "afirmação\nter fonte, fonte refutada depois, contradição não "
          "marcada, página velha\ncom commit novo no assunto.")
    print(f"\n{'FALHA' if erros else 'OK'} — {len(erros)} erro(s), "
          f"{len(avisos)} aviso(s). O lint reporta; quem move camada é você.")
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
