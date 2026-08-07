"""
wiki.py — Endpoints da wiki de conhecimento compilado.

  GET /wiki               índice de todas as páginas
  GET /wiki/search?q=...  busca léxica
  GET /wiki/{slug}        página de uma comunidade
  GET /wiki/{slug}.md     a mesma página em Markdown cru

Conteúdo vem de `edp/wiki.py`, que compila graphify-out/graph.json +
GRAPH_REPORT.md. Nada de conversa real é servido aqui — ver a nota de
segurança em edp/config.py (EDP_WIKI_CONVERSAS).
"""
from __future__ import annotations

import html

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse

from ... import wiki as _wiki
from ._sidebar import SIDEBAR_CSS, SIDEBAR_JS, render_sidebar

router = APIRouter(tags=["wiki"])

_CSS = """
<style>
body { background:#0b1220; color:#e2e8f0; font-family:system-ui,-apple-system,sans-serif;
       margin:0; padding:24px 24px 64px 24px; line-height:1.55; }
.wrap { max-width:960px; margin:0 auto; }
h1 { color:#06b6d4; font-size:22px; margin:8px 0 4px 0; }
h2 { color:#94a3b8; font-size:14px; text-transform:uppercase; letter-spacing:.6px;
     margin:28px 0 10px 0; border-bottom:1px solid #1e293b; padding-bottom:6px; }
a { color:#38bdf8; text-decoration:none; }
a:hover { text-decoration:underline; }
.meta { color:#64748b; font-size:12px; margin-bottom:18px; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:10px; }
.card { background:#0f172a; border:1px solid #1e293b; border-radius:7px; padding:12px 14px; }
.card:hover { border-color:#334155; }
.card .n { color:#475569; font-size:11px; font-variant-numeric:tabular-nums; }
.card .t { font-size:14px; font-weight:500; margin-bottom:3px; }
ul { padding-left:20px; margin:6px 0; }
li { margin:3px 0; }
code { background:#1e293b; padding:1px 5px; border-radius:3px; font-size:12px; color:#a5b4fc; }
.search { display:flex; gap:8px; margin:14px 0 4px 0; }
.search input { flex:1; background:#0f172a; border:1px solid #334155; color:#e2e8f0;
                padding:9px 12px; border-radius:6px; font-size:14px; }
.search button { background:#0e7490; color:#fff; border:0; padding:9px 18px;
                 border-radius:6px; cursor:pointer; font-size:14px; }
.hit { background:#0f172a; border-left:2px solid #06b6d4; padding:10px 14px;
       margin:8px 0; border-radius:0 6px 6px 0; }
.hit .s { color:#475569; font-size:11px; float:right; font-variant-numeric:tabular-nums; }
.hit .frag { color:#94a3b8; font-size:12px; margin-top:5px; }
.warn { background:#1c1917; border:1px solid #78350f; color:#fbbf24; padding:12px 14px;
        border-radius:6px; font-size:13px; margin:14px 0; }
</style>
"""


def _shell(titulo: str, corpo: str, pagina_ativa: str = "wiki") -> str:
    return (f"<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(titulo)}</title>{SIDEBAR_CSS}{_CSS}</head><body>"
            f"{render_sidebar(pagina_ativa)}<div class='wrap'>{corpo}</div>"
            f"{SIDEBAR_JS}</body></html>")


def _desligada() -> HTMLResponse:
    return HTMLResponse(_shell("Wiki desabilitada",
        "<h1>Wiki desabilitada</h1><p class='meta'>Defina "
        "<code>EDP_WIKI=1</code> para habilitar.</p>"), status_code=404)


def _sem_grafo(stats: dict) -> HTMLResponse:
    return HTMLResponse(_shell("Grafo ausente",
        "<h1>Grafo ainda não gerado</h1>"
        f"<p class='meta'>{html.escape(str(stats.get('erro', '')))}</p>"
        "<p>Rode <code>graphify update .</code> na raiz do projeto.</p>"),
        status_code=404)


def _ligada() -> bool:
    from ... import config as _config
    return _config.EDP_WIKI


_BUSCA = """
<form class="search" method="get" action="/wiki/search">
  <input name="q" placeholder="buscar na wiki (ex: retrieval híbrido, memória episódica)"
         value="{q}" autofocus>
  <button type="submit">Buscar</button>
</form>
"""


@router.get("/wiki", response_class=HTMLResponse)
async def wiki_index():
    if not _ligada():
        return _desligada()
    paginas, stats = _wiki.indice()
    if stats.get("erro"):
        return _sem_grafo(stats)

    cards = "".join(
        f"<a class='card' href='/wiki/{p.slug}'>"
        f"<div class='t'>{html.escape(p.nome)}</div>"
        f"<div class='n'>{p.n_nos} nós · {len(p.arquivos)} arquivos · "
        f"coesão {p.coesao:.2f}</div></a>"
        for p in paginas)

    corpo = (
        "<h1>Wiki do EDP</h1>"
        f"<p class='meta'>{stats['paginas']} páginas de "
        f"{stats['comunidades_totais']} comunidades "
        f"({stats['omitidas_por_tamanho']} omitidas por terem &lt; "
        f"{_wiki.MIN_NOS} nós) · {stats['nos']} nós · grafo em "
        f"<code>{str(stats.get('commit') or '?')[:8]}</code></p>"
        + _BUSCA.format(q="")
        + "<h2>Páginas</h2>"
        f"<div class='grid'>{cards}</div>")
    return HTMLResponse(_shell("Wiki do EDP", corpo))


# IMPORTANTE: /wiki/search precisa vir ANTES de /wiki/{slug}, senão o
# FastAPI casa "search" como slug.
@router.get("/wiki/search", response_class=HTMLResponse)
async def wiki_search(q: str = ""):
    if not _ligada():
        return _desligada()
    _, stats = _wiki.indice()
    if stats.get("erro"):
        return _sem_grafo(stats)

    resultados = _wiki.buscar(q) if q.strip() else []
    if not q.strip():
        miolo = "<p class='meta'>Digite um termo para buscar.</p>"
    elif not resultados:
        miolo = (f"<p class='meta'>Nada encontrado para "
                 f"<code>{html.escape(q)}</code>.</p>")
    else:
        miolo = "".join(
            f"<div class='hit'><span class='s'>{sc:.0f}</span>"
            f"<a href='/wiki/{p.slug}'>{html.escape(p.nome)}</a>"
            f"<div class='frag'>" +
            "<br>".join(html.escape(c) for c in casou) +
            "</div></div>"
            for p, sc, casou in resultados)

    corpo = ("<h1>Busca</h1>" + _BUSCA.format(q=html.escape(q, quote=True))
             + f"<h2>{len(resultados)} resultado(s)</h2>" + miolo)
    return HTMLResponse(_shell(f"Busca: {q}" if q else "Busca", corpo))


@router.get("/wiki/{slug}.md", response_class=PlainTextResponse)
async def wiki_pagina_md(slug: str):
    if not _ligada():
        return PlainTextResponse("wiki desabilitada (EDP_WIKI=0)", status_code=404)
    p = _wiki.pagina(slug)
    if p is None:
        return PlainTextResponse(f"página não encontrada: {slug}", status_code=404)
    return PlainTextResponse(_wiki.pagina_markdown(p))


@router.get("/wiki/{slug}", response_class=HTMLResponse)
async def wiki_pagina(slug: str):
    if not _ligada():
        return _desligada()
    p = _wiki.pagina(slug)
    if p is None:
        _, stats = _wiki.indice()
        if stats.get("erro"):
            return _sem_grafo(stats)
        return HTMLResponse(_shell("Não encontrada",
            f"<h1>Página não encontrada</h1><p class='meta'>"
            f"<code>{html.escape(slug)}</code></p>"
            "<p><a href='/wiki'>← índice</a></p>"), status_code=404)

    arquivos = "".join(f"<li><code>{html.escape(a)}</code></li>" for a in p.arquivos)

    nos = []
    for n in p.nos:
        rot = html.escape(str(n.get("label", "?")))
        arq = n.get("source_file") or ""
        loc = (n.get("source_location") or "").lstrip("L")
        ref = (f" — <code>{html.escape(arq)}"
               f"{(':' + html.escape(loc)) if loc else ''}</code>") if arq else ""
        nos.append(f"<li>{rot}{ref}</li>")

    viz = "".join(
        f"<li><a href='/wiki/{_wiki.slugify(nome)}'>{html.escape(nome)}</a>"
        f" <span class='n'>— {peso} arestas</span></li>"
        for _, nome, peso in p.vizinhas)

    corpo = (
        f"<p class='meta'><a href='/wiki'>← índice</a></p>"
        f"<h1>{html.escape(p.nome)}</h1>"
        f"<p class='meta'>comunidade <code>{p.community}</code> · {p.n_nos} nós · "
        f"coesão {p.coesao:.2f} · <a href='/wiki/{p.slug}.md'>markdown</a></p>"
        f"<h2>Arquivos ({len(p.arquivos)})</h2><ul>{arquivos}</ul>"
        f"<h2>Nós ({p.n_nos})</h2><ul>{''.join(nos)}</ul>"
        + (f"<h2>Comunidades vizinhas</h2><ul>{viz}</ul>" if viz else ""))
    return HTMLResponse(_shell(p.nome, corpo))
