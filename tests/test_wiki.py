"""
test_wiki.py — Wiki de conhecimento compilado (Degrau 1).

Cobre: flag, índice, página, markdown, busca, e DOIS testes de regressão
que existem por causa de incidentes reais deste projeto:

  - test_busca_vaga_nao_casa_com_tudo: o gate de similaridade por embedding
    foi refutado em docs/preregistro_degrau1_honeypot.md (R1, seletividade
    invertida — "vamos continuar nossa conversa" casava com 0.70+). A busca
    da wiki é léxica justamente para não herdar isso. Este teste trava a
    propriedade.

  - test_wiki_nao_serve_conversa_real: a API roda com allow_origins=["*"]
    (api/main.py) e EDP_LIVE_FEED_TOKEN vazio (config.py). Conversa real
    numa página da wiki seria legível por qualquer origem sem auth —
    reabriria o que 3076559 e 99d827c fecharam.
"""
from __future__ import annotations

import html

import pytest
from fastapi.testclient import TestClient

from edp.api.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _limpa_cache():
    from edp import wiki
    wiki.invalidar_cache()
    yield
    wiki.invalidar_cache()


def _tem_grafo() -> bool:
    from edp import wiki
    _, stats = wiki.indice()
    return not stats.get("erro")


requer_grafo = pytest.mark.skipif(
    not _tem_grafo(), reason="graphify-out/graph.json ausente")


# ── Flag ──────────────────────────────────────────────────────────────────────

def test_flag_off_devolve_404(monkeypatch):
    from edp import config
    monkeypatch.setattr(config, "EDP_WIKI", False)
    for url in ("/wiki", "/wiki/search?q=x", "/wiki/qualquer", "/wiki/qualquer.md"):
        assert client.get(url).status_code == 404, url


def test_flag_conversas_default_off():
    """Nenhum código deve consumir esta flag ainda; o default é o contrato."""
    from edp import config
    assert config.EDP_WIKI_CONVERSAS is False


# ── Índice e páginas ──────────────────────────────────────────────────────────

@requer_grafo
def test_indice_lista_paginas():
    r = client.get("/wiki")
    assert r.status_code == 200
    assert "Wiki do EDP" in r.text
    from edp import wiki
    paginas, stats = wiki.indice()
    assert stats["paginas"] > 0
    assert all(p.n_nos >= wiki.MIN_NOS for p in paginas)


@requer_grafo
def test_pagina_e_markdown_coerentes():
    from edp import wiki
    paginas, _ = wiki.indice()
    p = paginas[0]

    r = client.get(f"/wiki/{p.slug}")
    assert r.status_code == 200
    # nome vai escapado no HTML ("A & B" -> "A &amp; B")
    assert html.escape(p.nome) in r.text

    md = client.get(f"/wiki/{p.slug}.md")
    assert md.status_code == 200
    assert md.text.startswith(f"# {p.nome}")
    assert f"{p.n_nos} nós" in md.text


@requer_grafo
def test_slug_inexistente_404():
    assert client.get("/wiki/nao-existe-mesmo").status_code == 404
    assert client.get("/wiki/nao-existe-mesmo.md").status_code == 404


@requer_grafo
def test_search_nao_e_capturado_como_slug():
    """/wiki/search deve casar a rota de busca, não /wiki/{slug}."""
    r = client.get("/wiki/search?q=memoria")
    assert r.status_code == 200
    assert "resultado(s)" in r.text


# ── Busca ─────────────────────────────────────────────────────────────────────

@requer_grafo
def test_busca_encontra_termo_tecnico():
    from edp import wiki
    res = wiki.buscar("retrieval híbrido")
    assert res, "busca por termo técnico não retornou nada"
    assert any("Retrieval" in p.nome or "Hybrid" in p.nome for p, _, _ in res)


@requer_grafo
@pytest.mark.parametrize("vaga", [
    "vamos continuar nossa conversa",
    "me lembra o que discutimos",
    "sobre o que conversamos até agora",
    "voltando ao que estávamos vendo",
])
def test_busca_vaga_nao_casa_com_tudo(vaga):
    """
    REGRESSÃO R1 (docs/preregistro_degrau1_honeypot.md).

    Estas são queries [R3] reais do pool do EXP017. Sob um gate de
    similaridade por embedding elas casavam com QUALQUER coisa
    (sim média 0.7362, contra 0.4883 das factuais). A busca léxica não
    pode reproduzir isso: consulta sem termo específico deve devolver
    pouco ou nada, nunca o acervo inteiro.
    """
    from edp import wiki
    paginas, _ = wiki.indice()
    res = wiki.buscar(vaga)
    assert len(res) < max(3, len(paginas) // 10), (
        f"consulta vaga {vaga!r} retornou {len(res)} de {len(paginas)} páginas "
        f"— seletividade invertida de volta")


# ── Segurança ─────────────────────────────────────────────────────────────────

@requer_grafo
def test_wiki_nao_serve_conversa_real():
    """
    REGRESSÃO de exposição de dados.

    Nenhuma página pode citar arquivo de conversa/export. O .graphifyignore
    (3076559) mantém esses arquivos fora do grafo; este teste garante que a
    wiki não passe a servi-los caso o ignore seja afrouxado.
    """
    from edp import wiki
    paginas, _ = wiki.indice()
    proibidos = ("conversa", "response1", "análise_geral", "analise_geral",
                 ".har", "chunks", "gt_rotulacao", "gt_features")
    for p in paginas:
        for arq in p.arquivos:
            baixo = arq.lower()
            assert not any(t in baixo for t in proibidos), (
                f"página {p.slug} referencia arquivo sensível: {arq}")


@requer_grafo
def test_slug_e_estavel_e_seguro():
    """Slug entra em URL — não pode conter separador de caminho nem espaço."""
    from edp import wiki
    paginas, _ = wiki.indice()
    slugs = [p.slug for p in paginas]
    assert len(slugs) == len(set(slugs)), "slugs duplicados"
    for s in slugs:
        assert "/" not in s and "\\" not in s and " " not in s, s
        assert s == wiki.slugify(s), f"slug não idempotente: {s}"
