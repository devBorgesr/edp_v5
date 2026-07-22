"""
tests/test_exp017_dedup_ranked.py — T2: testes unitários da função pura
`_dedup_ranked` (edp/memory/store.py, exp017 Fase 1).

Contrato: PRE_REGISTRO_EXP017.md (com ERRATA + E6) + RELATORIO_F1T1_EXP017.md.
Só testa a função pura — sem flags, sem store real, sem I/O.
"""
import random

import pytest

from edp.memory.store import _dedup_ranked


def _cand(id_, text, score):
    return {"id": id_, "text": text, "ranking_score": score}


def _ranked(n, *, same_hash=False, same_id=False, text_fn=None, id_fn=None):
    """n candidatos sintéticos, score descendente (10.0, 9.0, ...)."""
    out = []
    for i in range(n):
        if same_id:
            cid = "same-id"
        elif id_fn:
            cid = id_fn(i)
        else:
            cid = f"id-{i}"
        if same_hash:
            text = "Q: oi A: oi! tudo bem?"
        elif text_fn:
            text = text_fn(i)
        else:
            text = f"texto único {i}"
        out.append(_cand(cid, text, float(n - i)))
    return out


# ── off ==slice ───────────────────────────────────────────────────────────────

def test_off_e_slice_byte_identico():
    candidates = _ranked(10)
    for k in (0, 1, 5, 10, 20):
        assert _dedup_ranked(candidates, k, "off") == candidates[:k]


def test_off_preserva_identidade_dos_objetos():
    candidates = _ranked(5)
    out = _dedup_ranked(candidates, 3, "off")
    for a, b in zip(out, candidates[:3]):
        assert a is b


# ── lista vazia ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["off", "dedup", "random_pareado"])
def test_lista_vazia(mode):
    rng = random.Random(42) if mode == "random_pareado" else None
    assert _dedup_ranked([], 5, mode, rng=rng) == []


# ── 10×mesmo-hash (espécime #4, IDs distintos) ──────────────────────────────

def test_dedup_10x_mesmo_hash_ids_distintos():
    candidates = _ranked(10, same_hash=True)  # IDs distintos, texto idêntico
    out = _dedup_ranked(candidates, 5, "dedup")
    # só existe 1 conteúdo único -> refill não acha substituto -> 1 resultado
    assert len(out) == 1
    assert out[0] is candidates[0]  # representante = primeira ocorrência (maior score)


def test_dedup_10x_mesmo_hash_normalizacao_whitespace_case():
    candidates = [
        _cand("a", "Q: oi  A: oi!", 10.0),
        _cand("b", "  q: OI   a: OI!  ", 9.0),   # mesmo conteúdo, whitespace/case diferentes
        _cand("c", "texto realmente diferente", 8.0),
    ]
    out = _dedup_ranked(candidates, 3, "dedup")
    ids = [c["id"] for c in out]
    assert ids == ["a", "c"]  # "b" colapsado no hash de "a"; refill traz "c"


# ── pares mesmo-ID (fenômeno D) ──────────────────────────────────────────────

def test_dedup_pares_mesmo_id_cross_camada():
    candidates = [
        _cand("dup", "texto episódico", 10.0),
        _cand("dup", "texto episódico", 9.0),   # mesma entry, cópia semântica (mesmo ID)
        _cand("outro", "texto distinto", 8.0),
    ]
    out = _dedup_ranked(candidates, 3, "dedup")
    ids = [c["id"] for c in out]
    assert ids == ["dup", "outro"]
    assert out[0]["ranking_score"] == 10.0  # representante = maior score (primeira ocorrência)


# ── mix (ID duplicado + hash duplicado + únicos) ────────────────────────────

def test_dedup_mix_id_e_hash():
    candidates = [
        _cand("A", "conteudo A", 10.0),
        _cand("A", "conteudo A copia semantica", 9.5),   # dup por ID (D)
        _cand("B", "conteudo B", 9.0),
        _cand("C", "CONTEUDO   b", 8.5),                  # dup por hash de "B" (A-no-resultado)
        _cand("D", "conteudo D", 8.0),
    ]
    out = _dedup_ranked(candidates, 5, "dedup")
    ids = [c["id"] for c in out]
    assert ids == ["A", "B", "D"]  # "A"(2a) removido por ID, "C" removido por hash


# ── k > candidatos ───────────────────────────────────────────────────────────

def test_dedup_k_maior_que_candidatos_unicos():
    candidates = _ranked(3)  # todos únicos
    out = _dedup_ranked(candidates, 10, "dedup")
    assert len(out) == 3
    assert out == candidates


def test_off_k_maior_que_candidatos():
    candidates = _ranked(3)
    assert _dedup_ranked(candidates, 10, "off") == candidates


# ── random_pareado ───────────────────────────────────────────────────────────

def test_random_pareado_exige_rng():
    candidates = _ranked(5, same_hash=True)
    with pytest.raises(ValueError):
        _dedup_ranked(candidates, 3, "random_pareado", rng=None)


def test_random_pareado_tamanho_e_reprodutibilidade():
    candidates = _ranked(10, same_hash=True)  # dedup removeria 4 (k=5, 1 único)
    out1 = _dedup_ranked(candidates, 5, "random_pareado", rng=random.Random("seed-fixa"))
    out2 = _dedup_ranked(candidates, 5, "random_pareado", rng=random.Random("seed-fixa"))
    assert len(out1) == 5
    assert len(out2) == 5
    assert [c["id"] for c in out1] == [c["id"] for c in out2]  # reprodutível por seed


def test_random_pareado_seeds_diferentes_podem_divergir():
    candidates = [_cand(f"id-{i}", f"texto {i}", float(20 - i)) for i in range(10)]
    # sem duplicatas -> d=0 -> random_pareado == off para QUALQUER seed
    out = _dedup_ranked(candidates, 5, "random_pareado", rng=random.Random(1))
    assert out == candidates[:5]


def test_random_pareado_remove_d_e_faz_refill():
    # 5 duplicatas de hash (id-0..id-4) + 5 únicos (id-5..id-9), k=5
    dup_block = _ranked(5, same_hash=True)
    unique_block = [_cand(f"u-{i}", f"unico {i}", float(4 - i)) for i in range(5)]
    for i, c in enumerate(dup_block):
        c["ranking_score"] = float(10 - i)
    candidates = dup_block + unique_block
    out = _dedup_ranked(candidates, 5, "random_pareado", rng=random.Random("d"))
    assert len(out) == 5  # |resultado| = k, sempre


# ── mode desconhecido ────────────────────────────────────────────────────────

def test_modo_desconhecido_leva_erro():
    with pytest.raises(ValueError):
        _dedup_ranked(_ranked(3), 2, "bogus")
