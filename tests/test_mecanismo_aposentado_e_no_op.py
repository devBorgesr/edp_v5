"""
test_mecanismo_aposentado_e_no_op.py — as erratas do exp008/exp009 afirmam
comportamento; aqui elas viram gate. (18/08/2026)

O QUE ESTE ARQUIVO PINA

`AUDITORIA_MECANISMO_APOSENTADO.md` conclui que ONZE dos dezoito mecanismos do
retrieve cosseno nao decidem nada em producao, porque o default e o hibrido
desde 08/07 (`config.py:53`). Sobre isso escrevi duas erratas:

  - `preregistro_experimento_009.md` §8-bis: o `trat_gravador`
    (`prioridade -> "media"`, `epistemic_status -> "hypothesis"`) e um **no-op**
    no caminho vivo.
  - `preregistro_experimento_008.md` §9-quater: a escala de `ranking_score`
    mudou de cosseno (~0.4) para RRF (~0.016).

Ate agora isso era **prosa**. Se alguem reintroduzir `prioridade` no ranking
hibrido amanha, as duas erratas ficam falsas em silencio e o exp009 volta a
parecer disparavel. Mesma doenca que o `test_preregistro_espelha_encarnacao`
existe para curar: afirmacao que nada confere.

POR QUE COMPORTAMENTAL, E NAO GREP NA FONTE

Um gate textual apodrece. Hoje mesmo `"eval_count" not in fonte` falhou porque
`prompt_eval_count` **contem** `eval_count` — o mesmo casamento frouxo que
estragou o catalogo de codigo morto. E `prioridade` de fato aparece no bloco
hibrido, numa f-string de exibicao (`store.py:1898`): um grep honesto acusaria,
e estaria errado.

O que a errata afirma e comportamento, entao o teste mede comportamento: mesma
entrada, metadado trocado, ordenacao identica.

POR QUE DOIS STORES, E NAO UM STORE EDITADO

O indice hibrido tem cache com chave
`(scope, len(epi), len(sem), ultimo id de cada)` (`store.py:1626-1632`) —
edicao in-place NAO invalida, divida documentada ali mesmo. Mutar metadado e
re-chamar `retrieve` no mesmo store reusaria o indice cacheado e passaria
verde sem exercitar nada. Cada condicao monta seu proprio `MemoryStore`.
"""
from __future__ import annotations

import copy
import uuid

import pytest

import edp.config as edp_config
from edp.memory import MemoryStore

# Textos distintos e tematicamente separados: o BM25 precisa de sinal lexical
# real para que "a ordem nao mudou" seja uma afirmacao com conteudo, e nao o
# empate trivial de oito textos identicos.
TEXTOS = [
    "Q: como configurar o redis para cache\nA: use maxmemory-policy allkeys-lru",
    "Q: qual porta o postgres escuta\nA: 5432 por padrao",
    "Q: o redis persiste em disco\nA: sim, via RDB e AOF",
    "Q: como rodar a suite de testes\nA: pytest tests/ -q",
    "Q: onde ficam os logs do nginx\nA: /var/log/nginx/access.log",
    "Q: o que e um indice parcial\nA: indice com clausula WHERE",
    "Q: como medir latencia de rede\nA: ping e depois mtr para o caminho",
    "Q: qual o timeout padrao do requests\nA: nenhum, precisa passar explicito",
]
QUERY = "redis cache configuracao"


@pytest.fixture(autouse=True)
def _hibrido_ligado(monkeypatch):
    """
    Fixa a flag em vez de herdar o default.

    Nao e paranoia: se o default virar 0, estes testes passariam a exercitar o
    COSSENO, onde `prioridade` de fato ordena — e falhariam. Falhar ali seria
    correto (a errata teria virado falsa), mas o diagnostico ficaria obscuro.
    Fixando, a falha aponta para o mecanismo, e `test_o_default_e_hibrido`
    cuida da premissa separadamente.
    """
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", True, raising=False)


def _entradas_base(entry_factory):
    """
    Lista canonica COM os privilegios de nascenca que o exp009 removeria.

    `make_entry` ja nasce `prioridade="media"` / `epistemic_status="hypothesis"`
    — que sao os valores do TRATAMENTO. Sem sobrescrever aqui, baseline e
    tratamento seriam o mesmo dict e o teste passaria por identidade, nao por
    inercia do mecanismo.
    """
    return [
        dict(
            entry_factory(text=t),
            id=f"e{i}",
            prioridade="alta",
            epistemic_status="verified",
            src_weight=1.0,
        )
        for i, t in enumerate(TEXTOS)
    ]


def _ordem(entradas, query=QUERY, top_k=5):
    """(id, ranking_score) do topo, entrando por `MemoryStore.retrieve`."""
    store = MemoryStore(f"noop-{uuid.uuid4().hex[:8]}")
    store.episodic.entries = copy.deepcopy(entradas)
    devolvidas = store.retrieve(query, top_k=top_k, min_score=0.0)
    return [(e["id"], round(float(e["ranking_score"]), 12)) for e in devolvidas]


# ── premissa ──────────────────────────────────────────────────────────────────

def test_o_default_e_hibrido(monkeypatch):
    """
    A premissa das duas erratas, conferida na fonte da verdade.

    Se isto cair, as erratas do exp008/exp009 precisam ser reescritas — nao
    estes testes.
    """
    monkeypatch.delenv("EDP_HYBRID_RETRIEVAL", raising=False)
    import importlib
    fresh = importlib.reload(edp_config)
    try:
        assert fresh.EDP_HYBRID_RETRIEVAL is True, (
            "o default voltou a ser cosseno — as erratas do exp008 §9-quater e "
            "do exp009 §8-bis assumem hibrido e precisam ser revistas"
        )
    finally:
        importlib.reload(edp_config)


# ── controles anti-teatro (rodam ANTES de valer alguma coisa) ─────────────────

def test_a_bancada_consegue_mudar_a_ordem(entry_factory, synthetic_store):
    """
    CONTROLE. Sem isto, tudo abaixo passaria contra uma bancada inerte.

    Mexe no que DEVE importar — o texto — e exige que a ordem mude. Se este
    teste ficar verde por acidente, os testes de no-op nao provam nada.
    """
    base = _entradas_base(entry_factory)
    mexido = copy.deepcopy(base)
    # o ultimo (sobre timeout do requests) passa a casar a query em cheio
    mexido[-1]["text"] = "Q: redis cache configuracao\nA: redis cache configuracao"

    assert _ordem(base) != _ordem(mexido), (
        "trocar o TEXTO nao mudou a ordenacao — a bancada esta inerte e os "
        "testes de no-op abaixo seriam teatro"
    )


def test_epistemic_status_e_lido_de_verdade(entry_factory, synthetic_store):
    """
    CONTROLE. `epistemic_status` NAO e ignorado — a distincao e o ponto.

    Ele e lido exatamente uma vez no hibrido (`store.py:1654`), como exclusao
    binaria em tempo de indice. A errata do exp009 nao diz "o campo e morto";
    diz "o campo nao ORDENA, e os dois valores que o tratamento usa
    (verified/hypothesis) estao fora do conjunto excluido". Este teste segura
    a primeira metade; `test_trat_gravador_e_no_op` segura a segunda.
    """
    base = _entradas_base(entry_factory)
    censurado = copy.deepcopy(base)
    censurado[0]["epistemic_status"] = "contradicted"

    ids_base = {i for i, _ in _ordem(base, top_k=8)}
    ids_cens = {i for i, _ in _ordem(censurado, top_k=8)}

    assert "e0" in ids_base, "a bancada nao devolveu e0 nem sem censura"
    assert "e0" not in ids_cens, (
        "governanca dura parou de morder: `contradicted` deveria sair do indice "
        "(store.py:1654). Se isso mudou, a errata do exp009 esta desatualizada"
    )


# ── o que as erratas afirmam ──────────────────────────────────────────────────

def test_trat_gravador_e_no_op(entry_factory, synthetic_store):
    """
    exp009 §8-bis: `prioridade -> "media"`, `epistemic_status -> "hypothesis"`
    nao muda ordem NEM escore no caminho vivo.

    Este e, literalmente, o experimento que o exp009 rodaria — cabendo num
    teste unitario. E por isso que ele nao e uma pergunta de pesquisa.
    """
    base = _entradas_base(entry_factory)
    tratado = copy.deepcopy(base)
    # SO METADE. Aplicar a todas seria um escalonamento uniforme: medido no
    # cosseno em 18/08, tratar todas derruba os escores ~35% (1.0075 -> 0.6587)
    # e NAO mexe na ordem. Um teste que comparasse so ids passaria verde no
    # cosseno tambem e nao discriminaria nada. Tratar metade quebra o empate —
    # e e o que o exp009 faz de verdade (so as session_summary).
    for e in tratado[::2]:
        e["prioridade"] = "media"
        e["epistemic_status"] = "hypothesis"

    assert _ordem(base) == _ordem(tratado), (
        "remover os privilegios de nascenca MUDOU a ordenacao — o exp009 voltou "
        "a ter objeto e a errata §8-bis precisa ser revista"
    )


def test_trat_gravador_srcw_e_no_op(entry_factory, synthetic_store):
    """exp009 §8, condicao exploratoria: `src_weight` tambem nao ordena."""
    base = _entradas_base(entry_factory)
    tratado = copy.deepcopy(base)
    for e in tratado[::2]:      # metade, mesma razao de `test_trat_gravador_e_no_op`
        e["src_weight"] = 0.1

    assert _ordem(base) == _ordem(tratado), (
        "`src_weight` voltou a pesar no ranking hibrido"
    )


def test_acessos_nao_realimentam_o_ranking(entry_factory, synthetic_store):
    """
    ACHADO_MEMORIA_TOXICA_RETROATIVA errata: o laco `acessos -> access_boost`
    que descrevi como vivo nao existe no hibrido.

    Foi a correcao mais desconfortavel do dia — eu tinha explicado um mecanismo
    de realimentacao inteiro a partir de codigo que producao nao percorre.
    """
    base = _entradas_base(entry_factory)
    acessado = copy.deepcopy(base)
    acessado[-1]["acessos"] = 9999

    assert _ordem(base) == _ordem(acessado), (
        "`acessos` voltou a influenciar o ranking — o laco de realimentacao "
        "descrito no ACHADO passou a existir de fato"
    )


def test_escala_do_ranking_score_e_rrf(entry_factory, synthetic_store):
    """
    exp008 §9-quater: o `ranking_score` entregue e escala RRF, nao cosseno.

    O piso vem da aritmetica, nao de palpite: com `rrf_k=60` e dois rankers, o
    teto e `2/61 ~= 0.0328`. O `BETA=0.25 * overlap` congelado no §3 do exp008
    chega a ~16x isso — que e o motivo de a formula estar aposentada.
    """
    topo = _ordem(_entradas_base(entry_factory))
    assert topo, "a bancada nao devolveu nada"

    maior = max(s for _, s in topo)
    assert 0.0 < maior <= 2 / 61 + 1e-9, (
        f"ranking_score de topo = {maior}; fora da escala RRF (teto 2/61 = "
        f"{2/61:.4f}). Se a escala voltou a ser cosseno (~0.4), a errata do "
        f"exp008 §9-quater precisa ser revista"
    )

    assert maior < 0.25, (
        "o termo BETA*overlap do exp008 (max 0.25) ainda domina a base — "
        "confirma o §9-quater"
    )


# ── prova de discriminacao ────────────────────────────────────────────────────

def test_no_cosseno_o_mesmo_tratamento_muda_tudo(entry_factory, synthetic_store,
                                                  monkeypatch, capsys):
    """
    O par que da sentido a todos os no-ops acima: MESMO tratamento, MESMAS
    entradas, caminho APOSENTADO — e agora tem de mudar.

    Sem esta metade, "a ordem nao mudou" seria compativel com uma bancada que
    nunca muda a ordem por nada. Com ela, a afirmacao vira especifica: o
    mecanismo existe, funciona, e nao e percorrido.

    Medido em 18/08 (fake_embeddings, seed do conftest):
        cosseno base    e2=1.0241  e3=1.0017  e4=0.9932  e5=0.9913  e1=0.9897
        cosseno tratado e3=1.0017  e5=0.9913  e1=0.9897  e7=0.9816  e2=0.6696
    `e2` cai de 1o para 5o e perde 35% do escore. No hibrido, zero.
    """
    monkeypatch.setattr(edp_config, "EDP_HYBRID_RETRIEVAL", False, raising=False)

    base = _entradas_base(entry_factory)
    tratado = copy.deepcopy(base)
    for e in tratado[::2]:
        e["prioridade"] = "media"
        e["epistemic_status"] = "hypothesis"

    a, b = _ordem(base), _ordem(tratado)
    print(f"\n  cosseno base   : {a}\n  cosseno tratado: {b}")

    assert a != b, (
        "nem no cosseno o `trat_gravador` muda alguma coisa — entao os testes "
        "de no-op acima nao discriminam nada e este arquivo inteiro e teatro"
    )
    assert max(s for _, s in a) > 2 / 61, (
        "a escala do cosseno caberia no teto RRF — `test_escala_do_ranking_score_e_rrf` "
        "nao estaria distinguindo os dois caminhos"
    )
