"""
test_preregistro_espelha_encarnacao.py — a tabela de constantes congeladas vale
o que vale a checagem dela (13/08/2026).

Cada `preregistro_experimento_*.md` da bancada tem uma tabela "Constantes
congeladas (espelhadas em `expNNN.py`)" e a frase "CONGELADO ao primeiro
disparo". Até hoje, "espelhadas" era uma afirmação em prosa — nada comparava as
duas.

O caso que motivou: `POOL_SIZE` foi congelado em `50` no §9 do exp008 e mudado
para `100` em `a855240` (27/06/2026), commit cuja própria mensagem diz "segundo
disparo real". Uma constante congelada mudou DEPOIS do primeiro disparo, um
segundo disparo real rodou com ela, os dois arquivos seguiram afirmando o
congelamento, e nada no repositório registrava o desvio — por dois meses.

Aqui a prosa vira build gate: divergência não declarada quebra. Desvio
DECLARADO numa tabela `§N-bis` passa, com o valor real exigido. A diferença
entre os dois casos é o ponto inteiro do pré-registro.
"""
from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parent.parent / "edp" / "lab"

# Linha de tabela markdown com UM nome de constante e UM literal:
#   | `POOL_SIZE` | `100` |
# O sufixo `[^|`]*` aceita a glosa da célula do nome (`` `BETA` (peso do overlap) ``)
# mas NÃO um segundo backtick, então esta regex sozinha só vê 1-nome/1-valor.
#
# ERRATA 18/08/2026: este bloco dizia que linhas de dois nomes "são
# deliberadamente ignoradas ... que é o certo". Não era — ver NOME_N abaixo, que
# passou a pareá-las quando as contagens batem. O texto original fica porque
# `test_cobertura_e_declarada` foi escrito confiando nele: a lacuna era visível
# no stdout desde 13/08 e ninguém (eu incluído) leu o número.
LINHA = re.compile(r"^\|\s*`([A-Z][A-Z0-9_]*)`[^|`]*\|\s*`([^`]+)`\s*\|\s*$", re.M)

# ── 18/08/2026: a exclusão acima era larga demais ──────────────────────────────
# "Não dá para casar dois nomes com um valor" é verdade — mas a linha real não
# tem um valor, tem dois:
#
#     | `TOP_K` / `MIN_SCORE` | `10` / `0.0` |
#
# Com N nomes e N literais na mesma ordem, o pareamento é único. A objeção só
# vale quando as contagens divergem, e aí a linha continua fora.
#
# Custo de manter a exclusão: `TOP_K` e `MIN_SCORE` do exp009 E do exp010 nunca
# foram conferidos. O exp010 é o experimento cujo resultado promoveu o híbrido a
# default de produção (`config.py:53`) — seus dois parâmetros de recuperação
# estavam fora do gate que existe para vigiá-los.
NOME_N  = re.compile(r"`([A-Z][A-Z0-9_]*)`")
VALOR_N = re.compile(r"`([^`]+)`")


def _literal(txt: str):
    try:
        return ast.literal_eval(txt.strip())
    except (ValueError, SyntaxError):
        return None


def _tabelas(md: str) -> tuple[dict, dict]:
    """(congeladas, desvios_declarados) — desvios vivem numa seção `-bis`."""
    corte = re.search(r"^##\s*§\d+-bis", md, re.M)
    principal, bis = (md[:corte.start()], md[corte.start():]) if corte else (md, "")

    congeladas = {}
    for nome, val in LINHA.findall(principal):
        lit = _literal(val)
        if lit is not None:
            congeladas[nome] = lit

    # Linhas de N nomes / N valores (ver NOME_N acima). Só duas colunas, para
    # não invadir a tabela de desvio, que tem três e semântica diferente.
    for ln in principal.splitlines():
        celulas = [c.strip() for c in ln.split("|")[1:-1]]
        if len(celulas) != 2:
            continue
        nomes, vals = NOME_N.findall(celulas[0]), VALOR_N.findall(celulas[1])
        if len(nomes) < 2 or len(nomes) != len(vals):
            continue
        for nome, val in zip(nomes, vals):
            lit = _literal(val)
            if lit is not None:
                congeladas.setdefault(nome, lit)

    # Na tabela de desvio a coluna do valor REAL é a terceira:
    #   | `POOL_SIZE` | `50` | `100` | ... |
    desvios = {}
    for ln in bis.splitlines():
        celulas = [c.strip() for c in ln.split("|")[1:-1]]
        if len(celulas) >= 3 and re.fullmatch(r"`[A-Z][A-Z0-9_]*`", celulas[0]):
            lit = _literal(celulas[2].strip("`"))
            if lit is not None:
                desvios[celulas[0].strip("`")] = lit
    return congeladas, desvios


def _casos():
    out = []
    for md_path in sorted(LAB.glob("preregistro_experimento_*.md")):
        num = re.search(r"(\d+)", md_path.stem).group(1)
        py = LAB / f"exp{num}.py"
        if py.exists():
            out.append(pytest.param(md_path, f"edp.lab.exp{num}", id=f"exp{num}"))
    return out


CASOS = _casos()


def test_ha_pre_registros_para_conferir():
    """Se o glob quebrar, os testes abaixo passariam vazios — isso é teste-teatro."""
    assert CASOS, "nenhum par preregistro/encarnação encontrado em edp/lab"


@pytest.mark.parametrize("md_path,modulo", CASOS)
def test_constantes_congeladas_batem_com_a_encarnacao(md_path, modulo):
    """
    O valor no código tem de ser o congelado — OU o declarado numa seção `-bis`.

    Note a assimetria proposital: declarar o desvio é barato (uma linha de
    tabela) e mudar em silêncio é caro (build vermelho). É a única forma de a
    régua não depender de alguém lembrar.
    """
    congeladas, desvios = _tabelas(md_path.read_text(encoding="utf-8"))
    mod = importlib.import_module(modulo)

    divergencias = []
    for nome, esperado in congeladas.items():
        if not hasattr(mod, nome):
            continue  # constante só do documento (ex.: rótulos em prosa)
        real = getattr(mod, nome)
        alvo = desvios.get(nome, esperado)
        if real != alvo:
            declarado = " (desvio declarado)" if nome in desvios else ""
            divergencias.append(
                f"{nome}: {modulo} tem {real!r}, {md_path.name} exige {alvo!r}{declarado}"
            )

    assert not divergencias, (
        "constante congelada divergiu do pré-registro sem desvio declarado:\n  "
        + "\n  ".join(divergencias)
        + "\n\nDeclare o desvio numa seção §N-bis (congelado | real | quando | commit) "
          "em vez de editar a tabela congelada."
    )


@pytest.mark.parametrize("md_path,modulo", CASOS)
def test_cobertura_e_declarada(md_path, modulo, capsys):
    """
    Quantas constantes este gate realmente cobre.

    Um gate que confere 1 de 16 linhas e não diz isso é pior que nenhum: dá a
    sensação de cobertura. Aqui a fração vai para o stdout do teste (`-s`), e o
    piso é 1 — o `EXPERIMENTO` sempre casa, então zero significaria parser
    quebrado, não tabela vazia.
    """
    congeladas, _ = _tabelas(md_path.read_text(encoding="utf-8"))
    mod = importlib.import_module(modulo)
    conferidas = [n for n in congeladas if hasattr(mod, n)]
    total_linhas = len(re.findall(r"^\|\s*`?[^|]+`?\s*\|", md_path.read_text(encoding="utf-8"), re.M))
    print(f"\n{md_path.name}: {len(conferidas)} constantes conferidas "
          f"({sorted(conferidas)}) de ~{total_linhas} linhas de tabela")
    assert conferidas, f"parser não extraiu nenhuma constante de {md_path.name}"


def test_o_desvio_do_pool_size_esta_declarado():
    """
    REGRESSÃO 13/08/2026: o caso concreto que este arquivo existe para pegar.

    `POOL_SIZE` congelado em 50, rodando em 100 desde `a855240` — o commit que
    anuncia o segundo disparo real. Se alguém apagar a §9-bis (ou "consertar" a
    §9 para 100, que é o mesmo erro em outra direção), este teste acusa.
    """
    md = (LAB / "preregistro_experimento_008.md").read_text(encoding="utf-8")
    congeladas, desvios = _tabelas(md)
    assert congeladas.get("POOL_SIZE") == 50, "a tabela §9 congelada foi reescrita"
    assert desvios.get("POOL_SIZE") == 100, "o desvio declarado sumiu da §9-bis"
    assert "a855240" in md, "o desvio perdeu a âncora no commit que o causou"

    from edp.lab import exp008
    assert exp008.POOL_SIZE == 100


def test_divergencia_nao_declarada_seria_pega():
    """
    Prova que o gate morde, em vez de confiar que morde.

    Simula a §9 do exp008 sem a §9-bis: o parser tem de voltar a exigir 50, que
    é o que o módulo NÃO tem. Sem esta prova, os testes acima passariam
    igualmente contra um parser que não extrai nada.
    """
    md = (LAB / "preregistro_experimento_008.md").read_text(encoding="utf-8")
    sem_bis = md[:re.search(r"^##\s*§\d+-bis", md, re.M).start()]
    congeladas, desvios = _tabelas(sem_bis)

    assert desvios == {}, "o corte não removeu a seção de desvios"
    from edp.lab import exp008
    assert exp008.POOL_SIZE != congeladas["POOL_SIZE"], (
        "sem a §9-bis o gate teria de acusar POOL_SIZE — se não acusa, é teatro"
    )


def test_linha_de_dois_nomes_e_conferida():
    """
    Prova que o pareamento N-nomes/N-valores extrai — e morde.

    Até 18/08/2026 a linha `| `TOP_K` / `MIN_SCORE` | `10` / `0.0` |` era
    ignorada de propósito. A justificativa ("não dá para casar dois nomes com um
    valor") descrevia um caso que a linha não é: há dois valores, na mesma
    ordem. O custo silencioso foi o exp010 — cujo resultado promoveu o híbrido a
    default de produção — ter os dois parâmetros de recuperação fora do gate.
    """
    md = (LAB / "preregistro_experimento_010.md").read_text(encoding="utf-8")
    congeladas, _ = _tabelas(md)

    assert congeladas.get("TOP_K") == 10, "pareamento não extraiu TOP_K do exp010"
    assert congeladas.get("MIN_SCORE") == 0.0, "pareamento não extraiu MIN_SCORE"

    # morde: uma encarnação com TOP_K trocado teria de divergir
    assert 5 != congeladas["TOP_K"], "comparação seria teatro"


def test_contagem_desigual_continua_fora():
    """
    A exclusão original vale onde a contagem diverge — e continua valendo.

    Dois nomes com UM valor não têm pareamento único; adivinhar ali seria pior
    que não conferir, porque congelaria a constante errada em silêncio.
    """
    assert _tabelas("| `A_X` / `B_Y` | `7` |\n")[0] == {}, (
        "2 nomes / 1 valor não pode ser pareado — o parser adivinhou"
    )
    assert _tabelas("| `A_X` | `7` / `8` |\n")[0] == {}, (
        "1 nome / 2 valores não pode ser pareado — o parser adivinhou"
    )
