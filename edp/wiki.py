"""
wiki.py — Páginas navegáveis de conhecimento compilado, a partir do grafo.

Degrau 1 da escada. Compila `graphify-out/graph.json` (estrutura) +
`graphify-out/GRAPH_REPORT.md` (nomes de comunidade, já gerados por LLM)
em páginas por comunidade, com índice e busca.

── Por que NÃO gera conhecimento do zero ─────────────────────────────────────
`FILA_FUTURO.md` manda avaliar o que já existe antes de construir módulo
novo. O que já existe (verificado 06/08):

  graphify-out/GRAPH_REPORT.md   325 comunidades, 205 já NOMEADAS por LLM
                                 ("Adaptive Controller", "Episodic Memory
                                 Core", ...), + hubs de navegação, god
                                 nodes e "surprising connections"
  graphify-out/graph.json        3978 nós com community, source_file,
                                 source_location e links

Ou seja: a compilação de conhecimento já estava feita — faltava
paginação, índice e endpoints. Este módulo faz só isso. Nenhum nome de
comunidade é inventado aqui; todos vêm do GRAPH_REPORT.

── O que este módulo deliberadamente NÃO faz ─────────────────────────────────
Não indexa conversas nem thinking. Motivo é segurança, não escopo:
`edp/api/main.py:260` roda com `allow_origins=["*"]` e
`edp/config.py:219` deixa `EDP_LIVE_FEED_TOKEN` vazio por padrão — uma
página servida por esta API é legível por qualquer origem, sem
autenticação. Colocar trecho de conversa real aqui reabriria exatamente a
exposição fechada em 3076559 (.graphifyignore) e 99d827c (.gitignore).
Ver `docs/wiki_conversas_pendente.md` para o desenho seguro.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_ROOT        = Path(__file__).resolve().parent.parent
_GRAPH_JSON  = _ROOT / "graphify-out" / "graph.json"
_GRAPH_REPORT = _ROOT / "graphify-out" / "GRAPH_REPORT.md"

# Comunidades abaixo disto viram ruído de navegação, não página.
# Mesmo espírito do GRAPH_REPORT, que omite 120 "thin communities".
MIN_NOS = 5

_RE_COMUNIDADE = re.compile(
    r'^### Community (\d+) - "(.+?)"\s*\nCohesion: ([\d.]+)', re.M)
_RE_TOKEN = re.compile(r"\w+", re.UNICODE)

# ── Stopwords da BUSCA ────────────────────────────────────────────────────────
# ATENÇÃO à diferença de contexto: em
# `docs/preregistro_gate_especificidade.md` §3 eu recusei lista de stopwords
# de propósito, porque lá é uma métrica científica e a lista seria mais um
# parâmetro que eu poderia ajustar até o resultado sair como eu queria.
# Aqui é uma caixa de busca — lista de stopwords é higiene padrão de IR, não
# grau de liberdade sobre uma hipótese.
#
# Existe porque o IDF sozinho NÃO resolve neste corpus: medido em 06/08,
# "agora" tem IDF 3.21 e "retrieval" tem 2.16 — palavra de conversa é rara
# em código, então pontua como se fosse específica. É a armadilha de gênero
# registrada em §3-bis.1 daquele pré-registro, aqui confirmada na prática.
_STOPWORDS = frozenset("""
a ao aos as até com como da das de dela dele deles do dos e ela elas ele eles
em entre era eram essa essas esse esses esta estas este estes estava estavam
estamos estávamos estou está estão eu foi fomos for foram há isso isto já lhe
lhes mais mas me mesmo meu meus minha minhas muito na nas nem no nos nossa
nossas nosso nossos num numa não nós o os ou para pela pelas pelo pelos por
qual quando que quem se sem ser seu seus sua suas são só também te tem temos
tenho ter teu teus tinha tive tu tua tuas um uma umas uns vamos vendo ver vez
vindo vinha vir você vocês vou voltando estivemos sobre agora ainda antes
depois aqui ali lá então assim onde porque pois cada todo toda todos todas
outro outra outros outras algum alguma alguns algumas nada tudo
the of and to in is it for on with as at by an be this that from or are was
""".split())


def slugify(texto: str) -> str:
    """'Adaptive Controller' -> 'adaptive-controller'. Estável entre builds."""
    norm = unicodedata.normalize("NFKD", texto)
    norm = "".join(c for c in norm if not unicodedata.combining(c))
    norm = re.sub(r"[^\w\s-]", "", norm.lower())
    return re.sub(r"[\s_]+", "-", norm).strip("-") or "sem-nome"


@dataclass
class Pagina:
    community: int
    nome: str
    slug: str
    coesao: float
    nos: list[dict] = field(default_factory=list)
    arquivos: list[str] = field(default_factory=list)
    vizinhas: list[tuple[int, str, int]] = field(default_factory=list)  # (id, nome, peso)

    @property
    def n_nos(self) -> int:
        return len(self.nos)


def _nomes_do_report() -> dict[int, tuple[str, float]]:
    """Lê nome + coesão de cada comunidade do GRAPH_REPORT.md."""
    if not _GRAPH_REPORT.exists():
        return {}
    txt = _GRAPH_REPORT.read_text(encoding="utf-8", errors="replace")
    return {int(cid): (nome.strip(), float(coe))
            for cid, nome, coe in _RE_COMUNIDADE.findall(txt)}


@lru_cache(maxsize=1)
def _carregar() -> tuple[dict[str, Pagina], dict]:
    """
    Constrói todas as páginas. Cacheado — o grafo só muda em `graphify update`.
    Retorna (paginas_por_slug, stats).
    """
    if not _GRAPH_JSON.exists():
        return {}, {"erro": "graph.json ausente — rode `graphify update .`"}

    grafo = json.loads(_GRAPH_JSON.read_text(encoding="utf-8", errors="replace"))
    nodes = grafo.get("nodes", [])
    links = grafo.get("links", [])
    nomes = _nomes_do_report()

    # nós por comunidade
    por_com: dict[int, list[dict]] = {}
    com_de_no: dict[str, int] = {}
    for n in nodes:
        c = n.get("community")
        if c is None:
            continue
        por_com.setdefault(c, []).append(n)
        if n.get("id"):
            com_de_no[n["id"]] = c

    # arestas entre comunidades → vizinhança
    entre: dict[tuple[int, int], int] = {}
    for l in links:
        a, b = com_de_no.get(l.get("source")), com_de_no.get(l.get("target"))
        if a is None or b is None or a == b:
            continue
        chave = (a, b) if a < b else (b, a)
        entre[chave] = entre.get(chave, 0) + 1

    paginas: dict[str, Pagina] = {}
    slugs_usados: set[str] = set()
    for cid, ns in por_com.items():
        if len(ns) < MIN_NOS:
            continue
        nome, coesao = nomes.get(cid, (f"Comunidade {cid}", 0.0))
        slug = slugify(nome)
        if slug in slugs_usados:                     # nomes repetidos no report
            slug = f"{slug}-{cid}"
        slugs_usados.add(slug)

        arquivos = sorted({n["source_file"] for n in ns if n.get("source_file")})
        viz = []
        for (a, b), peso in entre.items():
            if a == cid or b == cid:
                outro = b if a == cid else a
                if len(por_com.get(outro, [])) >= MIN_NOS:
                    viz.append((outro, nomes.get(outro, (f"Comunidade {outro}", 0))[0], peso))
        viz.sort(key=lambda t: -t[2])

        paginas[slug] = Pagina(
            community=cid, nome=nome, slug=slug, coesao=coesao,
            nos=sorted(ns, key=lambda n: (n.get("source_file") or "",
                                          n.get("source_location") or "")),
            arquivos=arquivos, vizinhas=viz[:8],
        )

    stats = {
        "paginas": len(paginas),
        "comunidades_totais": len(por_com),
        "omitidas_por_tamanho": len(por_com) - len(paginas),
        "nos": len(nodes),
        "commit": grafo.get("built_at_commit"),
        "nomes_do_report": len(nomes),
    }
    return paginas, stats


def invalidar_cache() -> None:
    """Chamar após `graphify update`."""
    _carregar.cache_clear()
    _idf.cache_clear()


def indice() -> tuple[list[Pagina], dict]:
    paginas, stats = _carregar()
    return sorted(paginas.values(), key=lambda p: -p.n_nos), stats


def pagina(slug: str) -> Pagina | None:
    paginas, _ = _carregar()
    return paginas.get(slug)


def _tokens_da_pagina(p: Pagina) -> set[str]:
    toks = {t.lower() for t in _RE_TOKEN.findall(p.nome)}
    for n in p.nos:
        toks |= {t.lower() for t in _RE_TOKEN.findall(n.get("label") or "")}
    for arq in p.arquivos:
        toks |= {t.lower() for t in _RE_TOKEN.findall(arq)}
    return toks


@lru_cache(maxsize=1)
def _idf() -> dict[str, float]:
    """
    IDF sobre as páginas (documento = uma página). Termo presente em toda
    página vale ~0; termo raro vale alto.

    Existe por causa de um defeito real, pego pelo próprio teste de
    regressão: a primeira versão de `buscar()` contava qualquer token com
    mais de 2 caracteres, e `"voltando ao que estávamos vendo"` devolvia 20
    de 198 páginas — porque `que` e `vendo` aparecem em docstring em
    português por todo lado. Era o R1
    (`docs/preregistro_degrau1_honeypot.md`) reaparecendo em forma léxica:
    consulta vaga casando com tudo.

    A ponderação por IDF é a mesma ideia de
    `docs/preregistro_gate_especificidade.md`, e aqui a condição de
    sanidade §3-bis.1 daquele pré-registro é satisfeita por construção: o
    corpus (código e docs) é do mesmo gênero das consultas que a wiki
    espera (nomes de módulo, conceito, arquivo).
    """
    import math
    paginas, _ = _carregar()
    N = len(paginas)
    if N == 0:
        return {}
    df: dict[str, int] = {}
    for p in paginas.values():
        for t in _tokens_da_pagina(p):
            df[t] = df.get(t, 0) + 1
    return {t: math.log((N + 1) / (d + 1)) for t, d in df.items()}


def buscar(q: str, limite: int = 20) -> list[tuple[Pagina, float, list[str]]]:
    """
    Busca léxica ponderada por IDF sobre nome de comunidade, rótulos de nó e
    caminhos de arquivo. Retorna [(pagina, score, trechos_que_casaram)].

    Léxica e não semântica de propósito: o gate por embedding foi refutado
    em `docs/preregistro_degrau1_honeypot.md` (R1, seletividade invertida —
    consulta vaga casava com tudo, sim média 0.7362 contra 0.4883 das
    factuais). Ver `_idf()` para por que só ser léxica não bastava.
    """
    termos = {t.lower() for t in _RE_TOKEN.findall(q)
              if len(t) > 2 and t.lower() not in _STOPWORDS}
    if not termos:
        return []

    paginas, _ = _carregar()
    idf = _idf()
    # Termo que o acervo desconhece não pode ajudar nem atrapalhar: peso 0.
    # Mesma regra da emenda E1 do pré-registro do gate.
    pesos = {t: idf.get(t, 0.0) for t in termos}
    if not any(w > 0 for w in pesos.values()):
        return []

    saida = []
    for p in paginas.values():
        casou: list[str] = []
        score = 0.0

        nome_toks = {t.lower() for t in _RE_TOKEN.findall(p.nome)}
        peso_nome = sum(pesos[t] for t in termos & nome_toks)
        if peso_nome > 0:
            score += 10.0 * peso_nome
            casou.append(f"nome: {p.nome}")

        # Cada termo conta UMA vez por página, não uma vez por nó. Sem isso,
        # um termo de peso baixo domina pelo simples número de nós que o
        # contêm — foi assim que "voltando ao que estávamos vendo" devolvia
        # 20 de 198 páginas antes desta correção.
        termos_em_nos: set[str] = set()
        for n in p.nos:
            rotulo = n.get("label") or ""
            if not rotulo:
                continue
            rt = {t.lower() for t in _RE_TOKEN.findall(rotulo)}
            novos = (termos & rt) - termos_em_nos
            if novos:
                termos_em_nos |= novos
                if len(casou) < 6:
                    loc = n.get("source_file") or ""
                    casou.append(f"{rotulo[:70]}" + (f" — {loc}" if loc else ""))
        score += 2.0 * sum(pesos[t] for t in termos_em_nos)

        termos_em_arq: set[str] = set()
        for arq in p.arquivos:
            termos_em_arq |= termos & {t.lower() for t in _RE_TOKEN.findall(arq)}
        score += 1.0 * sum(pesos[t] for t in termos_em_arq)

        if score > 0:
            saida.append((p, score, casou[:6]))

    saida.sort(key=lambda t: -t[1])
    return saida[:limite]


def pagina_markdown(p: Pagina) -> str:
    """Renderiza a página como Markdown (para export ou leitura crua)."""
    linhas = [f"# {p.nome}", ""]
    linhas.append(f"Comunidade `{p.community}` · {p.n_nos} nós · "
                  f"coesão {p.coesao:.2f} · {len(p.arquivos)} arquivos")
    linhas += ["", "## Arquivos", ""]
    linhas += [f"- `{a}`" for a in p.arquivos]
    linhas += ["", "## Nós", ""]
    for n in p.nos:
        loc = n.get("source_location") or ""
        arq = n.get("source_file") or ""
        ref = f" — `{arq}{(':' + loc.lstrip('L')) if loc else ''}`" if arq else ""
        linhas.append(f"- **{n.get('label', '?')}**{ref}")
    if p.vizinhas:
        linhas += ["", "## Comunidades vizinhas", ""]
        linhas += [f"- [{nome}](/wiki/{slugify(nome)}) — {peso} arestas"
                   for _, nome, peso in p.vizinhas]
    return "\n".join(linhas)
