"""
test_lint_wiki.py — `scripts/lint_wiki.py`.

Os testes montam páginas sintéticas em `tmp_path` em vez de rodar contra
`edp_wiki/paginas/`. Duas razões:

  1. `edp_wiki/` é gitignored — um teste que dependesse dela pularia
     sempre no CI e em clone limpo, cobrindo nada.
  2. O que precisa ser travado é o LINT, não o conteúdo da wiki. Conteúdo
     muda toda semana; a regra não.

Cada teste exercita uma classe de defeito do `docs/WIKI_SCHEMA.md`, mais
os dois falsos positivos que a primeira versão do lint produziu em 11/08
— eles voltariam calados sem teste.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_LINT = _ROOT / "scripts" / "lint_wiki.py"

PAGINA_OK = """\
---
titulo: "Página válida"
tipo: achado
status: verificado
fontes: ["commit:HEAD", "docs/WIKI_SCHEMA.md"]
criada: 2026-08-11
atualizada: 2026-08-11
links: ["outra"]
---

Corpo com link para [[outra]].
"""

OUTRA_OK = """\
---
titulo: "Outra"
tipo: conceito
status: verificado
fontes: ["docs/WIKI_SCHEMA.md"]
criada: 2026-08-11
atualizada: 2026-08-11
links: ["pagina"]
---

Volta para [[pagina]].
"""


def roda(pasta: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    import os
    ambiente = {**os.environ, **(env or {})}
    return subprocess.run([sys.executable, str(_LINT), "--paginas", str(pasta)],
                          capture_output=True, text=True, cwd=_ROOT, env=ambiente)


def escreve(pasta: Path, nome: str, conteudo: str) -> None:
    (pasta / f"{nome}.md").write_text(conteudo, encoding="utf-8")


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    """Par de páginas que se linkam — estado limpo, sem erro nem aviso."""
    d = tmp_path / "paginas"
    d.mkdir()
    escreve(d, "pagina", PAGINA_OK)
    escreve(d, "outra", OUTRA_OK)
    return d


# ── Estado limpo ─────────────────────────────────────────────────────────────

def test_wiki_limpa_passa(wiki):
    r = roda(wiki)
    assert r.returncode == 0, r.stdout
    assert "0 erro(s), 0 aviso(s)" in r.stdout


def test_pasta_ausente_nao_falha(tmp_path):
    """edp_wiki/ é gitignored — ausência não pode quebrar CI."""
    r = roda(tmp_path / "nao-existe")
    assert r.returncode == 0
    assert "nada a checar" in r.stdout


# ── ERRO: contrato estrutural ────────────────────────────────────────────────

def test_link_para_pagina_inexistente_e_erro(wiki):
    escreve(wiki, "pagina", PAGINA_OK.replace(
        'links: ["outra"]', 'links: ["outra", "fantasma"]'))
    r = roda(wiki)
    assert r.returncode == 1
    assert "fantasma" in r.stdout and "inexistente" in r.stdout


def test_status_fora_do_vocabulario_e_erro(wiki):
    escreve(wiki, "pagina", PAGINA_OK.replace("status: verificado",
                                              "status: quase-certo"))
    r = roda(wiki)
    assert r.returncode == 1
    assert "quase-certo" in r.stdout and "§7.1" in r.stdout


def test_chave_obrigatoria_faltando_e_erro(wiki):
    escreve(wiki, "pagina", PAGINA_OK.replace("criada: 2026-08-11\n", ""))
    r = roda(wiki)
    assert r.returncode == 1
    assert "`criada:`" in r.stdout


def test_fontes_vazio_e_erro(wiki):
    """Regra 1 — nada sem fonte."""
    escreve(wiki, "pagina", PAGINA_OK.replace(
        'fontes: ["commit:HEAD", "docs/WIKI_SCHEMA.md"]', "fontes: []"))
    r = roda(wiki)
    assert r.returncode == 1
    assert "regra 1" in r.stdout


def test_nucleo_sem_evidencia_e_erro(wiki):
    """§7.2 — promover para dentro exige evidência externa declarada."""
    escreve(wiki, "pagina", PAGINA_OK.replace("status: verificado",
                                              "status: nucleo"))
    r = roda(wiki)
    assert r.returncode == 1
    assert "camada_evidencia" in r.stdout


def test_nucleo_com_evidencia_passa(wiki):
    escreve(wiki, "pagina", PAGINA_OK.replace(
        "status: verificado",
        'status: nucleo\ncamada_evidencia: "pré-registro, critério congelado"'))
    r = roda(wiki)
    assert r.returncode == 0, r.stdout


def test_arquivo_do_repo_que_sumiu_e_erro(wiki):
    """§7.3 — 'detectável por lint, sem julgamento'."""
    escreve(wiki, "pagina", PAGINA_OK.replace(
        '"docs/WIKI_SCHEMA.md"', '"docs/arquivo-que-nunca-existiu.md"'))
    r = roda(wiki)
    assert r.returncode == 1
    assert "não existe" in r.stdout and "§7.3" in r.stdout


def test_linha_alem_do_fim_do_arquivo_e_erro(wiki):
    escreve(wiki, "pagina", PAGINA_OK.replace(
        '"docs/WIKI_SCHEMA.md"', '"docs/WIKI_SCHEMA.md:999999"'))
    r = roda(wiki)
    assert r.returncode == 1
    assert "citação vai até 999999" in r.stdout


def test_intervalo_de_linhas_e_checado(wiki):
    """
    REGRESSÃO 11/08: a primeira regex só aceitava `arq:N`, e `arq:N-M`
    caía calado em "não checável" — justo a forma que as páginas mais
    usam (`edp/llm_adapter.py:1189-1225`).
    """
    escreve(wiki, "pagina", PAGINA_OK.replace(
        '"docs/WIKI_SCHEMA.md"', '"docs/WIKI_SCHEMA.md:1-999999"'))
    r = roda(wiki)
    assert r.returncode == 1
    assert "citação vai até 999999" in r.stdout


# ── Falsos positivos que a v1 do lint produziu, travados ─────────────────────

def test_fonte_de_fora_do_repo_nao_e_erro(wiki):
    """
    REGRESSÃO 11/08: `content.js` (repo do exportador) e
    `Downloads/...` (máquina do pesquisador) foram acusados de "arquivo
    não existe". Lint que acusa página boa ensina a ignorar o lint.
    """
    escreve(wiki, "pagina", PAGINA_OK.replace(
        '"docs/WIKI_SCHEMA.md"',
        '"content.js", "Downloads/x/_indice.json", "conv:abc#t1"'))
    r = roda(wiki)
    assert r.returncode == 0, r.stdout
    assert "NÃO CHECÁVEIS" in r.stdout
    assert "content.js" in r.stdout


# ── AVISO: defeito real, não falha o processo ────────────────────────────────

def test_orfa_e_aviso_e_nao_falha(wiki):
    """
    Órfã não é sinônimo de fraca — `aee20f9`. O lint reporta; quem move
    camada é o humano. Exit 0.
    """
    escreve(wiki, "sozinha", PAGINA_OK.replace(
        'links: ["outra"]', "links: []").replace(
        "Corpo com link para [[outra]].", "Corpo sem link."))
    r = roda(wiki)
    assert r.returncode == 0, r.stdout
    assert "sozinha: órfã" in r.stdout
    assert "sozinha: sem link de saída" in r.stdout


def test_divergencia_frontmatter_corpo_e_aviso(wiki):
    escreve(wiki, "pagina", PAGINA_OK.replace(
        "Corpo com link para [[outra]].", "Corpo sem wikilink nenhum."))
    r = roda(wiki)
    assert r.returncode == 0, r.stdout
    assert "diverge do corpo" in r.stdout


def test_grau_de_entrada_conta_frontmatter_e_corpo(wiki):
    """
    REGRESSÃO 11/08: contei órfãs só pelos `[[ ]]` do corpo e publiquei
    7; o certo era 5. O frontmatter carregava 28 das 29 arestas.
    Aqui `alvo` só é linkada pelo frontmatter de `pagina` — não é órfã.
    """
    escreve(wiki, "alvo", OUTRA_OK.replace('links: ["pagina"]',
                                           'links: ["pagina"]'))
    escreve(wiki, "pagina", PAGINA_OK.replace('links: ["outra"]',
                                              'links: ["outra", "alvo"]'))
    r = roda(wiki)
    assert "alvo: órfã" not in r.stdout, r.stdout


# ── Regressão de encoding: CI Windows, 11/08 ──────────────────────────────────

def test_erro_nao_crasha_sob_encoding_cp1252(wiki):
    """
    REGRESSÃO — falha real no CI (`windows-latest`), 11/08/2026: stdout
    capturado por subprocess no Windows usa cp1252 ("charmap") quando não
    é console real. `print(f"  ✗ {e}")` — ✗ é U+2717, fora do Latin-1 —
    derrubava o processo com UnicodeEncodeError NA PRIMEIRA linha de
    ERRO, antes de imprimir qualquer achado. O lint saía mudo justo no
    caso que mais precisa reportar algo.

    Reproduzido aqui simulando o ambiente real via `PYTHONIOENCODING`
    (Linux honra a variável mesmo sem console Windows) — sem isso, este
    teste falha com o mesmo traceback do CI.
    """
    escreve(wiki, "pagina", PAGINA_OK.replace(
        'links: ["outra"]', 'links: ["outra", "fantasma"]'))
    r = roda(wiki, env={"PYTHONIOENCODING": "cp1252"})
    assert "UnicodeEncodeError" not in r.stderr, r.stderr
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "fantasma" in r.stdout and "inexistente" in r.stdout
