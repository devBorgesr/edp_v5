"""
test_medicao_wiki.py — trava os scripts que PRODUZEM NÚMERO para o
pré-registro `docs/preregistro_gap_score.md`.

POR QUE EXISTE: em 11/08 publiquei três números errados nesta frente —
7 órfãs (eram 5), 0 links quebrados (eram 2) e mediana de alcance 4,0
(era 11,5). Os três são o MESMO defeito: **ler parte da estrutura e
tratar como o todo** — que é literalmente o que os quatro métodos do Gap
Score erraram e o que esta frente investiga.

Os dois primeiros só apareceram porque escrevi o lint depois; o terceiro,
porque duas medições discordaram por acaso. Nenhum foi pego por teste,
porque não havia. Enquanto o código que mede for menos rigoroso que o
objeto medido, um "achado" pode ser bug com boa apresentação.

Corpus sintético em `tmp_path`: `edp_wiki/` é gitignored e um teste que
dependesse dela pularia sempre no CI. O que precisa ser travado é o
MEDIDOR, não o conteúdo.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import sys

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

from medir_alcance_wiki import alcance, construir, medir  # noqa: E402


def pagina(links_fm: list[str] = (), links_corpo: list[str] = ()) -> str:
    fm = ", ".join(f'"{x}"' for x in links_fm)
    corpo = " ".join(f"[[{x}]]" for x in links_corpo)
    return (f"---\ntitulo: \"x\"\ntipo: achado\nstatus: verificado\n"
            f"fontes: [\"docs/WIKI_SCHEMA.md\"]\ncriada: 2026-08-11\n"
            f"atualizada: 2026-08-11\nlinks: [{fm}]\n---\n\nCorpo. {corpo}\n")


@pytest.fixture
def wiki(tmp_path: Path):
    d = tmp_path / "paginas"
    d.mkdir()

    def escreve(nome: str, **kw):
        (d / f"{nome}.md").write_text(pagina(**kw), encoding="utf-8")
    return d, escreve


# ── Armadilha 1: aresta vem de DOIS lugares ──────────────────────────────────

def test_aresta_so_no_frontmatter_conta(wiki):
    """
    REGRESSÃO 11/08: contei só os `[[ ]]` do corpo. O frontmatter
    carregava 28 das 29 arestas, e por isso publiquei 7 órfãs e 0 links
    quebrados quando eram 5 e 2.
    """
    d, escreve = wiki
    escreve("a", links_fm=["b"])
    escreve("b")
    g, _, _ = construir(d)
    assert g["a"] == {"b"}, "aresta declarada só no frontmatter sumiu"


def test_aresta_so_no_corpo_conta(wiki):
    d, escreve = wiki
    escreve("a", links_corpo=["b"])
    escreve("b")
    g, _, _ = construir(d)
    assert g["a"] == {"b"}


def test_frontmatter_e_corpo_somam(wiki):
    d, escreve = wiki
    escreve("a", links_fm=["b"], links_corpo=["c"])
    escreve("b")
    escreve("c")
    g, _, _ = construir(d)
    assert g["a"] == {"b", "c"}


def test_mesmo_alvo_nos_dois_conta_uma_vez(wiki):
    d, escreve = wiki
    escreve("a", links_fm=["b"], links_corpo=["b"])
    escreve("b")
    g, _, _ = construir(d)
    assert g["a"] == {"b"}


# ── Armadilha 2: slugs completo ANTES de filtrar ─────────────────────────────

def test_referencia_para_pagina_posterior_na_ordem_alfabetica(wiki):
    """
    REGRESSÃO 11/08, a pior das três: filtrei os alvos contra o conjunto
    `slugs` ENQUANTO ele ainda estava sendo preenchido no mesmo laço.
    Toda referência para página que vem depois na ordem alfabética foi
    descartada — mediana de alcance deu 4,0 onde o certo era 11,5.

    `aaa` vem antes de `zzz`; sob o bug esta aresta desaparece.
    """
    d, escreve = wiki
    escreve("aaa", links_fm=["zzz"])
    escreve("zzz")
    g, _, quebrados = construir(d)
    assert g["aaa"] == {"zzz"}, "referência 'para frente' foi descartada"
    assert quebrados == [], "aresta válida foi classificada como quebrada"


def test_referencia_para_pagina_anterior_tambem(wiki):
    d, escreve = wiki
    escreve("aaa")
    escreve("zzz", links_fm=["aaa"])
    g, _, _ = construir(d)
    assert g["zzz"] == {"aaa"}


# ── Armadilha 3: link quebrado é reportado, não some ─────────────────────────

def test_link_quebrado_nao_vira_aresta_e_e_reportado(wiki):
    d, escreve = wiki
    escreve("a", links_fm=["fantasma"])
    g, _, quebrados = construir(d)
    assert g["a"] == set(), "alvo inexistente virou aresta"
    assert ("a", "fantasma") in quebrados


def test_link_quebrado_nao_infla_o_alcance(wiki):
    d, escreve = wiki
    escreve("a", links_fm=["fantasma1", "fantasma2"])
    assert alcance(construir(d)[0], "a") == 1


# ── BFS ──────────────────────────────────────────────────────────────────────

def test_cadeia_linear(wiki):
    d, escreve = wiki
    escreve("a", links_fm=["b"])
    escreve("b", links_fm=["c"])
    escreve("c", links_fm=["d"])
    escreve("d")
    g, _, _ = construir(d)
    assert alcance(g, "a", 3) == 4       # a,b,c,d
    assert alcance(g, "b", 3) == 3
    assert alcance(g, "d", 3) == 1


def test_profundidade_e_respeitada(wiki):
    d, escreve = wiki
    for x, y in (("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")):
        escreve(x, links_fm=[y])
    escreve("e")
    g, _, _ = construir(d)
    assert alcance(g, "a", 3) == 4, "BFS passou da profundidade 3"
    assert alcance(g, "a", 4) == 5


def test_ciclo_nao_trava_nem_conta_duas_vezes(wiki):
    d, escreve = wiki
    escreve("a", links_fm=["b"])
    escreve("b", links_fm=["a"])
    g, _, _ = construir(d)
    assert alcance(g, "a", 3) == 2


def test_no_isolado_alcanca_so_a_si(wiki):
    d, escreve = wiki
    escreve("a")
    escreve("b")
    assert alcance(construir(d)[0], "a") == 1


# ── Dirigido vs não-dirigido ─────────────────────────────────────────────────

def test_dirigido_nao_segue_backlink(wiki):
    """E-3.1: 'A cita B' não implica que B conheça A."""
    d, escreve = wiki
    escreve("a", links_fm=["b"])
    escreve("b")
    g, _, _ = construir(d, dirigido=True)
    assert alcance(g, "b") == 1


def test_nao_dirigido_segue_backlink(wiki):
    d, escreve = wiki
    escreve("a", links_fm=["b"])
    escreve("b")
    g, _, _ = construir(d, dirigido=False)
    assert alcance(g, "b") == 2


# ── Pré-condição E-3.2 ───────────────────────────────────────────────────────

def test_precondicao_falha_quando_alcance_e_baixo_demais(wiki):
    """Tudo isolado: navegar é ler uma página. Abaixo de 3 → FALHA."""
    d, escreve = wiki
    for n in "abcde":
        escreve(n)
    assert medir(d)["passa"] is False


def test_precondicao_falha_quando_alcance_e_alto_demais(wiki):
    """Grafo completo de 14 nós: navegar é ler o acervo. Acima de 12 → FALHA."""
    d, escreve = wiki
    nomes = [f"n{i:02d}" for i in range(14)]
    for n in nomes:
        escreve(n, links_fm=[x for x in nomes if x != n])
    r = medir(d)
    assert r["mediana"] == 14
    assert r["passa"] is False


def test_precondicao_passa_na_faixa(wiki):
    """
    Três ciclos de 4, isolados entre si: todo nó alcança os 4 do seu
    ciclo em <=3 saltos. Mediana 4, dentro de [3,12].

    Cadeia LINEAR de 4 não serve, e o teste original errou por isso: os
    alcances são [4,3,2,1] por bloco e a mediana cai para 2,5. Eu tinha
    pensado só na cabeça da cadeia. O código estava certo.
    """
    d, escreve = wiki
    for bloco in ("a", "b", "c"):
        ns = [f"{bloco}{i}" for i in range(4)]
        for i, n in enumerate(ns):
            escreve(n, links_fm=[ns[(i + 1) % len(ns)]])
    r = medir(d)
    assert r["mediana"] == 4
    assert r["passa"] is True


def test_pasta_vazia_nao_quebra(tmp_path):
    d = tmp_path / "vazio"
    d.mkdir()
    assert medir(d)["nos"] == 0


# ── Faixa proporcional (substituiu a absoluta [3,12] em 11/08) ───────────────

def test_faixa_escala_com_o_corpus(wiki):
    """
    A absoluta [3,12] tinha 12 = 75% de 16; num corpus de 100 páginas
    passaria a testar outra coisa, e a ressalva "revalidar quando
    crescer" é instrução sem gatilho — se perde. A proporcional escala
    por construção.
    """
    d, escreve = wiki
    for i in range(20):
        escreve(f"n{i:02d}")
    assert medir(d)["faixa"] == (4.0, 15.0)      # 20% e 75% de 20


def test_piso_nunca_cai_abaixo_de_dois(wiki):
    """
    Alcançar só a si mesmo é ausência de navegação por definição. Em
    corpus pequeno 20% cairia abaixo de 1, e "tudo isolado" passaria.
    """
    d, escreve = wiki
    for i in range(4):
        escreve(f"n{i}")
    lo, _ = medir(d)["faixa"]
    assert lo == 2.0, "piso proporcional afundou abaixo do mínimo absoluto"
    assert medir(d)["passa"] is False, "corpus todo isolado passou"


def test_faixa_reproduz_o_veredito_congelado_em_16_nos(wiki):
    """
    REGRESSÃO da troca de critério. Trocar faixa depois de ver resultado
    só é legítimo se o veredito congelado não muda — mesma disciplina do
    flag-off byte-idêntico. Em N=16 a proporcional dá [3.2, 12.0], e as
    duas medianas que E-3.2 registrou continuam PASSA.
    """
    d, escreve = wiki
    for i in range(16):
        escreve(f"n{i:02d}")
    lo, hi = medir(d)["faixa"]
    assert (round(lo, 1), round(hi, 1)) == (3.2, 12.0)
    assert lo <= 4.5 <= hi, "mediana dirigida de E-3.2 deixou de passar"
    assert lo <= 11.5 <= hi, "mediana não-dirigida de E-3.2 deixou de passar"


# ── Regressão do veredito publicado do Haiku (E-2) ───────────────────────────

_JSON_E2 = _ROOT / "resultado_gap_score_haiku.json"


@pytest.mark.skipif(not _JSON_E2.exists(), reason="resultado E-2 ausente")
def test_condicoes_de_e2_re_derivadas_do_bruto():
    """
    Recalcula (a)–(e) a partir do JSON bruto e confere contra o que foi
    publicado. Trava o veredito FALHA contra edição acidental do
    documento, e prova que a lógica de unanimidade fez o que diz.
    """
    from medir_gap_score import CONJ_A, CONJ_B

    d = json.loads(_JSON_E2.read_text(encoding="utf-8"))
    r = d["resultados"]

    def ok(qid: str, esperado: str) -> bool:
        v = r[qid]
        return bool(v["unanime"]) and v["veredito"] == esperado

    # unanimidade tem de ser derivável dos 5 vereditos, não confiada
    for qid, v in r.items():
        assert v["unanime"] == (len(set(v["vereditos"])) == 1
                                and None not in v["vereditos"]), qid
        assert len(v["vereditos"]) == d["rodadas"], qid

    a = sum(1 for q in CONJ_A if ok(q, "SIM"))
    b = sum(1 for q in CONJ_B if ok(q, "NAO"))
    unan = sum(1 for v in r.values() if v["unanime"])

    assert (a, b, unan) == (3, 6, 15), (a, b, unan)
    assert d["condicoes"] == {"a": a >= 3, "b": b >= 7,
                              "c": ok("Q3", "NAO"), "d": ok("N3", "NAO"),
                              "e": unan >= 12}
    assert d["veredito"] == "FALHA"
    assert d["condicoes"]["b"] is False, "a condição que reprovou mudou"
