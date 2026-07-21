"""
exp017 Fase 0 (T5) — testa as fórmulas puras de scripts/medir_repeat_exp017.py
(repeat_rate, overlap binário/contínuo, matriz par-a-par) e a lista congelada
de queries (E5). Carregado por caminho — scripts/ não é um pacote.
"""
from __future__ import annotations

import importlib.util
import os

_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "medir_repeat_exp017.py",
)
_spec = importlib.util.spec_from_file_location("medir_repeat_exp017", _PATH)
medir = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(medir)


def test_queries_congeladas_contagem_e_pools():
    assert len(medir.QUERIES) == 14
    pools = [p for p, _ in medir.QUERIES]
    assert pools.count("R2") == 3
    assert pools.count("R3") == 6
    assert pools.count("N") == 5


def test_queries_r2_batem_com_exp010():
    from edp.lab.exp010 import REDIS_QUERIES
    r2_no_script = [q for p, q in medir.QUERIES if p == "R2"]
    assert sorted(r2_no_script) == sorted(REDIS_QUERIES)


def test_queries_r3_batem_com_exp009():
    from edp.lab.exp009 import VAGUE_QUERIES
    r3_no_script = [q for p, q in medir.QUERIES if p == "R3"]
    assert sorted(r3_no_script) == sorted(VAGUE_QUERIES)


def test_queries_ordem_intercalada_sem_blocos_longos():
    # E5: nenhum pool deve aparecer 3x seguidas (o risco que a emenda visa evitar)
    pools = [p for p, _ in medir.QUERIES]
    for i in range(len(pools) - 2):
        assert not (pools[i] == pools[i + 1] == pools[i + 2]), (
            f"bloco de 3 seguidas do mesmo pool em i={i}: {pools[i:i+3]}"
        )


def test_overlap_binario_satura_em_min_2_k():
    assert medir.overlap_binario(["a", "b", "c"], ["a", "b", "x"]) == 1
    assert medir.overlap_binario(["a", "b", "c"], ["a", "x", "y"]) == 0
    assert medir.overlap_binario(["a"], ["a"]) == 1  # min(2,1)=1
    assert medir.overlap_binario([], ["a", "b"]) == 0
    assert medir.overlap_binario(["a", "b"], []) == 0


def test_overlap_continuo_fracao_exata():
    assert medir.overlap_continuo(["a", "b", "c", "d"], ["a", "b", "x", "y"]) == 0.5
    assert medir.overlap_continuo(["a", "b"], ["a", "b"]) == 1.0
    assert medir.overlap_continuo(["a", "b"], ["x", "y"]) == 0.0
    assert medir.overlap_continuo([], ["a"]) == 0.0


def test_repeat_rate_tudo_identico_e_maximo():
    seq = [["a", "b", "c"]] * 5
    r = medir.repeat_rate(seq)
    assert r["binario"] == 1.0
    assert r["continuo_medio"] == 1.0
    assert r["n_pares"] == 4


def test_repeat_rate_tudo_distinto_e_zero():
    seq = [[f"id-{i}"] for i in range(5)]
    r = medir.repeat_rate(seq)
    assert r["binario"] == 0.0
    assert r["continuo_medio"] == 0.0


def test_matriz_overlap_diagonal_um_e_simetrica_quando_conjuntos_iguais():
    seq = [["a", "b"], ["a", "b"], ["c", "d"]]
    m = medir.matriz_overlap(seq)
    assert m[0][0] == 1.0
    assert m[0][1] == 1.0  # idênticos
    assert m[0][2] == 0.0  # disjuntos
    assert m[0][1] == m[1][0]  # simetria neste caso (mesmo tamanho)


def test_matriz_overlap_vazio_nao_quebra():
    seq = [[], ["a", "b"]]
    m = medir.matriz_overlap(seq)
    assert m[0] == [0.0, 0.0]
