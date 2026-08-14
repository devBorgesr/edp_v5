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
# Linhas com dois nomes na mesma célula (`TOP_K` / `MIN_SCORE`) ou com prosa no
# valor são deliberadamente ignoradas — ver `test_cobertura_e_declarada`, que
# imprime o que ficou de fora para a lacuna ser visível em vez de silenciosa.
# O sufixo `[^|`]*` aceita a glosa da célula do nome (`` `BETA` (peso do overlap) ``)
# mas NÃO um segundo backtick — assim `` `K3`, `K5` `` (dois nomes numa célula)
# continua fora, que é o certo: não dá para casar dois nomes com um valor.
LINHA = re.compile(r"^\|\s*`([A-Z][A-Z0-9_]*)`[^|`]*\|\s*`([^`]+)`\s*\|\s*$", re.M)


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
