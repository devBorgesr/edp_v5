"""
test_catalogo_de_modulos_mortos.py — o catálogo de código morto do README vira
alegação verificada (13/08/2026).

O README enumera os módulos de topo de `edp/` e marca com `(†)` os que não têm
importador em todo o repositório. Até hoje isso era prosa: ninguém conferia, e a
lista tinha errado nas DUAS direções desde 22/07/2026 —

  marcados (†) mas VIVOS:  retrieval (run.py:187), vector_store (run.py:250)
  mortos e NÃO marcados:   analytics, reranker
  marcado e correto:       memory_graph  (deletado neste commit)

além do cabeçalho dizer "39 módulos" havendo 41.

A contagem aqui é por AST, não por grep: `\\bretrieval\\b` não distingue
`from .retrieval import` de uma menção em docstring ou de `retrieval_hybrid`,
e foi exatamente esse tipo de casamento frouxo que deixou a lista apodrecer.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
EDP = RAIZ / "edp"
README = RAIZ / "README.md"

IGNORAR_DIRS = {".venv", "graphify-out", ".git", "node_modules", "__pycache__"}


def _modulos_de_topo() -> set[str]:
    return {p.stem for p in EDP.glob("*.py") if p.stem != "__init__"}


def _fontes() -> list[pathlib.Path]:
    """
    `rglob` desceria em `.venv` inteiro antes de filtrar — segundos por chamada,
    num teste que chama isto três vezes. `os.walk` PODA os diretórios, então o
    passeio nunca entra lá.
    """
    import os
    out = []
    for dirpath, dirnames, filenames in os.walk(RAIZ):
        dirnames[:] = [d for d in dirnames if d not in IGNORAR_DIRS]
        for f in filenames:
            if f.endswith(".py"):
                out.append(pathlib.Path(dirpath) / f)
    return out


def _importadores() -> dict[str, set[str]]:
    """modulo de topo -> arquivos que o importam DE VERDADE (AST, não grep)."""
    import warnings

    mods = _modulos_de_topo()
    usos: dict[str, set[str]] = {m: set() for m in mods}
    for p in _fontes():
        try:
            # Parsear o repositório inteiro faz aflorar DeprecationWarning de
            # escape inválido de scripts soltos da raiz (`\\e` em
            # extract_ground_truth.py, force_extract_old.py). Não é achado
            # deste gate nem é dele a responsabilidade de consertar; silenciar
            # aqui evita poluir toda rodada da suíte com ruído de terceiros.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                arv = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for no in ast.walk(arv):
            if isinstance(no, ast.Import):
                alvos = [a.name for a in no.names]
            elif isinstance(no, ast.ImportFrom):
                alvos = [no.module or ""] + [
                    f"{no.module or ''}.{a.name}" for a in no.names
                ]
            else:
                continue
            for alvo in alvos:
                for parte in alvo.split("."):
                    if parte in mods and p.stem != parte:
                        usos[parte].add(str(p.relative_to(RAIZ)))
    return usos


def _bloco_do_readme() -> str:
    """
    Só a ENUMERAÇÃO, cortada antes da legenda `(†)`.

    A legenda cita o caminho deste próprio arquivo de teste, e `\\b\\w+\\.py\\b`
    engoliria `test_catalogo_de_modulos_mortos.py` como se fosse um módulo de
    `edp/`. Um gate que se autoinclui na lista que confere é um gate quebrado.
    """
    m = re.search(r"\+ \d+ módulos de topo.*?\n```", README.read_text(encoding="utf-8"), re.S)
    assert m, "o bloco de módulos de topo sumiu do README — o gate perdeu a âncora"
    bloco = m.group(0)
    # corta na LEGENDA, não no primeiro "(†)" — esse aparece inline na própria
    # enumeração (`analytics.py (†),`) e cortar ali levaria as marcas junto.
    corte = bloco.find("(†) sem importador")
    return bloco[:corte] if corte != -1 else bloco


# ── O catálogo bate com a realidade ──────────────────────────────────────────

def test_a_lista_do_readme_tem_todos_os_modulos():
    bloco = _bloco_do_readme()
    listados = set(re.findall(r"\b([a-z_0-9]+)\.py\b", bloco))
    reais = _modulos_de_topo()
    assert listados == reais, (
        f"README fora de sincronia | só no README: {sorted(listados - reais)} "
        f"| só no disco: {sorted(reais - listados)}"
    )


def test_a_contagem_declarada_bate_com_a_lista():
    """"39 módulos" com 41 na lista é o tipo de número que ninguém reconfere."""
    bloco = _bloco_do_readme()
    declarado = int(re.search(r"\+ (\d+) módulos de topo", bloco).group(1))
    assert declarado == len(_modulos_de_topo())


def test_o_dagger_marca_exatamente_os_modulos_sem_importador():
    """
    As duas direções importam.

    Marcar um módulo VIVO como morto convida a deletá-lo — `retrieval` e
    `vector_store` estavam assim, e ambos são importados por `run.py`. Deixar um
    módulo MORTO sem marca — `analytics`, `reranker` — é o esquecimento que o
    catálogo existe para impedir.
    """
    bloco = _bloco_do_readme()
    marcados = set(re.findall(r"\b([a-z_0-9]+)\.py[ \t]*\(†\)", bloco))
    usos = _importadores()
    mortos = {m for m, u in usos.items() if not u}

    vivos_marcados = {m: sorted(usos[m]) for m in marcados - mortos}
    mortos_sem_marca = sorted(mortos - marcados)
    assert not vivos_marcados and not mortos_sem_marca, (
        f"catálogo (†) divergiu | marcados como mortos mas IMPORTADOS: "
        f"{vivos_marcados} | mortos sem marca: {mortos_sem_marca}"
    )


# ── A decisão do memory_graph, travada ───────────────────────────────────────

def test_memory_graph_nao_volta_por_similaridade_de_embedding():
    """
    REGRESSÃO 13/08/2026: `memory_graph.py` foi deletado, não desligado.

    Ele ligava memórias por similaridade de embedding >= 0.70. Duas razões
    independentes, nenhuma delas minha:

    1. `docs/design_wiki_conversas.md` §5 registra que o R1 REFUTOU esse gate no
       dado real, e fixa a regra: as arestas vêm de co-ocorrência e de conceito
       compartilhado, **nunca de similaridade de embedding**.
    2. Medição de 13/08 neste store: a 0.70 o grafo teria 5 arestas de 153 pares
       possíveis, cobrindo 7 dos 18 nós — 11 memórias isoladas, grau máximo 2.

    Se alguém recriar o módulo, este teste acusa e manda ler a §5 antes.
    """
    assert not (EDP / "memory_graph.py").exists(), (
        "memory_graph.py voltou — leia docs/design_wiki_conversas.md §5 antes: "
        "aresta por similaridade de embedding foi refutada pelo R1"
    )
    assert "memory_graph" not in set(_modulos_de_topo())


# ── O gate morde ─────────────────────────────────────────────────────────────

def test_ast_nao_confunde_mencao_com_import():
    """
    Prova que a contagem é por import e não por menção de texto.

    `run.py` cita "retrieval" em docstring e em string de benchmark E o importa
    de verdade na linha 187. Um grep não separa os dois casos; o AST separa —
    e é essa diferença que fez o catálogo do README apodrecer por três semanas.
    """
    usos = _importadores()
    assert "run.py" in usos["retrieval"], "AST perdeu um import real de run.py"

    # o inverso: um módulo só MENCIONADO em texto não conta como importado
    falso = ast.parse('x = "usa edp.analytics para tudo"\n# import analytics\n')
    achou = any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in ast.walk(falso))
    assert not achou, "comentário e string não podem virar import"


def test_ha_modulos_para_conferir():
    """Se o glob quebrar, os testes acima passariam vazios."""
    assert len(_modulos_de_topo()) > 20
