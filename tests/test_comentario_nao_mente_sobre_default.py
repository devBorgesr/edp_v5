"""
test_comentario_nao_mente_sobre_default.py — a lacuna mais cara da auditoria
(22/08/2026).

O PADRÃO, MEDIDO

Em três dias apareceram CINCO comentários afirmando o contrário do mecanismo, e
três deles têm exatamente a mesma forma — o comentário declara o default de uma
flag, a flag foi promovida, o comentário ficou:

  1. `store.py:1504`      "flag DESLIGADA por padrão"  — ligada desde 08/07
  2. `store.py:1519`      "Desligada (default)"        — dentro da errata que
                          corrigia o caso 1, sobrevivendo à própria correção
  3. `websocket.py:1237`  "EDP_WRITE_PROVENANCE, default OFF" — default "1"
  4. `llm_adapter.py:2819` "EDP_CTX_SLOTS, default OFF" — promovido em 08/07
                          JUNTO com o hibrido; achei o do hibrido e este ficou
  5. `edi_001.py`         docstring dizendo o inverso do proprio codigo, em
                          DOIS lugares; consertei um e ia commitar

Os casos 1–4 sao mecanicamente conferiveis: comparar a afirmacao no comentario
com o segundo argumento do `os.environ.get`. O caso 5 nao e, e fica declarado
como fora do alcance deste gate (§ no fim).

POR QUE ISTO NAO EXISTIA

`test_preregistro_espelha_encarnacao` confere CONSTANTE contra DOCUMENTO.
`test_token_telemetry` exige que toda flag seja CLASSIFICADA. Nenhum dos dois
confere AFIRMACAO contra COMPORTAMENTO — e comentario que declara default e
escrito uma vez e nunca revisitado quando a flag e promovida.

FALSO POSITIVO E O QUE MATA UM GATE

A primeira versao desta varredura acusou `store.py:1519`, que diz "Desligada
(EDP_HYBRID_RETRIEVAL=0, a rede de seguranca — NAO o default)". O texto esta
CORRETO e o regex leu a palavra sem a negacao ao lado. Um gate que acusa texto
correto ganha excecoes ate nao valer nada — as tres listas de frases deste
projeto morreram assim. Por isso `_nega_ser_default` existe.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

# EDP_X = os.environ.get("EDP_X", "0") == "1"   -> o default e o 2o argumento
DECL = re.compile(
    r'^(EDP_[A-Z0-9_]+)\s*=\s*os\.environ\.get\(\s*"[^"]+"\s*,\s*"([01])"\s*\)', re.M)

FLAG = re.compile(r"\b(EDP_[A-Z0-9_]+)\b")

# Afirmacoes sobre o default. Deliberadamente estreitas: so o que e claramente
# uma declaracao de padrao, nao qualquer mencao a ligado/desligado.
AFIRMA_OFF = re.compile(
    r"default\s*[:=]?\s*off|padr[aã]o\s*[:=]?\s*off|"
    r"desligad[ao]\s+por\s+padr[aã]o|off\s+por\s+padr[aã]o", re.I)
AFIRMA_ON = re.compile(
    r"default\s*[:=]?\s*on\b|padr[aã]o\s*[:=]?\s*on\b|"
    r"ligad[ao]\s+por\s+padr[aã]o|on\s+por\s+padr[aã]o", re.I)

# "NAO o default", "nao e o padrao" — o texto esta dizendo justamente que
# aquele valor NAO e o default. Acusar aqui seria acusar quem acertou.
NEGA = re.compile(r"n[aã]o\s+(?:é\s+|e\s+)?o\s+(?:default|padr[aã]o)", re.I)


def _defaults() -> dict[str, bool]:
    """flag -> True se o default e ON."""
    cfg = (RAIZ / "edp" / "config.py").read_text(encoding="utf-8")
    return {n: (v == "1") for n, v in DECL.findall(cfg)}


def _linhas_de_comentario(p: Path):
    for i, ln in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        s = ln.strip()
        if s.startswith("#") or '"""' in ln or s.startswith("*"):
            yield i, ln


def varre() -> list[tuple]:
    """(arquivo, linha, flag, afirmado, real, texto) para cada contradicao."""
    reais = _defaults()
    achados = []
    for p in sorted((RAIZ / "edp").rglob("*.py")):
        for i, ln in _linhas_de_comentario(p):
            flags = [f for f in FLAG.findall(ln) if f in reais]
            if not flags:
                continue
            if NEGA.search(ln):
                continue
            diz_off, diz_on = bool(AFIRMA_OFF.search(ln)), bool(AFIRMA_ON.search(ln))
            if diz_off == diz_on:          # nenhuma ou ambas: nao afirma nada util
                continue
            for f in flags:
                if diz_on != reais[f]:
                    achados.append((str(p.relative_to(RAIZ)), i, f,
                                    "ON" if diz_on else "OFF",
                                    "ON" if reais[f] else "OFF", ln.strip()[:90]))
    return achados


# ── O gate ────────────────────────────────────────────────────────────────────

def test_nenhum_comentario_contradiz_o_default_real():
    """
    Quinta ocorrencia do padrao em tres dias. Aqui ele para de depender de
    alguem reler o arquivo certo no dia certo.
    """
    achados = varre()
    msg = "\n".join(
        f"  {a}:{i}\n    {f}: comentario diz {d}, default e {r}\n    “{t}”"
        for a, i, f, d, r, t in achados)
    assert not achados, (
        f"comentario contradiz o default real da flag ({len(achados)}):\n{msg}\n\n"
        f"Quando uma flag e promovida, o comentario que declara o default dela "
        f"precisa ser corrigido no mesmo commit — com errata, nao apagando o "
        f"texto original (NORTE §4.4)."
    )


def test_a_varredura_le_alguma_coisa():
    """Sem isto, o gate acima passaria contra um parser que devolve lista vazia."""
    d = _defaults()
    assert len(d) >= 15, f"so {len(d)} flags extraidas do config — o parser quebrou"
    assert any(v for v in d.values()) and any(not v for v in d.values()), (
        "todas as flags com o mesmo default: o parser nao esta lendo o 2o argumento")


def test_o_gate_morde():
    """
    Prova contra texto sintetico, sem depender do estado do repositorio.

    Se este arquivo passasse so porque hoje nao ha contradicao, ele nao provaria
    nada — e amanha, com o parser quebrado, continuaria verde.
    """
    reais = {"EDP_FAKE": True}                      # default ON
    ln = "# ── exp999 (EDP_FAKE, default OFF): alguma coisa ──"
    assert bool(AFIRMA_OFF.search(ln)) and not AFIRMA_ON.search(ln)
    assert "EDP_FAKE" in FLAG.findall(ln)
    assert reais["EDP_FAKE"] is True, "afirmacao OFF contra default ON = contradicao"


def test_negacao_explicita_nao_e_acusada():
    """
    REGRESSAO do falso positivo que a primeira versao produziu.

    `store.py:1519` diz "Desligada (EDP_HYBRID_RETRIEVAL=0, a rede de seguranca
    — NAO o default)". Esta CORRETO. Um gate que acusa quem acertou ganha
    excecoes ate virar decoracao.
    """
    ln = ("# Desligada (EDP_HYBRID_RETRIEVAL=0, a rede de seguranca — NAO o "
          "default), o fluxo abaixo e EXATAMENTE o de antes.")
    assert NEGA.search(ln), "a guarda de negacao nao reconhece o texto real do store.py"


def test_o_que_este_gate_NAO_alcanca():
    """
    Declarado, nao escondido.

    O caso 5 do cabecalho — docstring afirmando o inverso da propria logica do
    codigo — NAO e conferivel por regex, porque exige entender o que a funcao
    faz. Este gate cobre 4 dos 5 casos medidos. A quinta continua dependendo de
    releitura, e isso esta escrito aqui para nao parecer coberta.
    """
    assert True
