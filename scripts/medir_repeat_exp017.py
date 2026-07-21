#!/usr/bin/env python3
"""
scripts/medir_repeat_exp017.py — T5 (Fase 0): repeat_rate OFF vs SHUFFLE.

Contra o store real do fase0 (a VM não o enxerga — roda no Windows do
pesquisador). Mede repeat_rate em DUAS condições {OFF, SHUFFLE} e em DOIS
pontos por retrieve (emenda E1):
  - top-k BRUTO:      mem.retrieve(q, top_k=5, min_score=0.20) direto —
                       comparável ao histórico do retrieval_monitor (T1a).
                       Por construção, SHUFFLE não toca este ponto
                       (llm_adapter.py:2334, DEPOIS do retrieve) — espera-se
                       repeat(SHUFFLE)==repeat(OFF) aqui (tautológico,
                       PRE_REGISTRO_EXP017.md, seção "PONTO DE MEDIÇÃO").
  - retrieval_kept:   rt._build_enriched_context(q, ...) -> rt._last_kept_ids
                       (ponto (ii) do T1b, resolvido via propagação read-only
                       do T4). Ponto PRIMÁRIO — é aqui que SHUFFLE pode ter
                       efeito (ContextWindowManager.build() é guloso e
                       sensível à ordem, context_window_manager.py:320-327).

Também reporta, por query (E3 FIX, T2/T3): truncamento kept vs
offered_mapeado — lido direto de rt._last_trunc, sem parsear log.

USO (servidor parado):
  $env:EDP_BASE_DIR = "C:\\edp_data_fase0"
  python scripts/medir_repeat_exp017.py
  python scripts/medir_repeat_exp017.py --ordem agrupada   # E6

--ordem {intercalada,agrupada}: intercalada é a lista congelada do T5
(default); agrupada é a lista congelada do E6 (PRE_REGISTRO_EXP017.md
linhas 202-218) — mesmas 14 queries, reordenadas por pool. Nenhuma query é
adicionada, removida ou reescrita em nenhuma das duas.

Snapshot/restore por medição — mesmo padrão de suite_regressao_fase1.py:
restore() ANTES de cada query, para toda medição partir do mesmo estado
(retrieve muta acessos/ultimo_acesso).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile


def die(msg: str) -> None:
    print(f"[ERRO] {msg}")
    sys.exit(2)


# ── Queries fixas (T5) — ordem INTERCALADA (emenda E5) ────────────────────
# 3 do R2 (edp/lab/exp010.py:84-88, REDIS_QUERIES) + 6 do R3
# (edp/lab/exp009.py:70-77, VAGUE_QUERIES) + 5 neutras novas. Round-robin
# entre os três pools (R3 tem 6, R2 tem 3, N tem 5; pool exaurido é pulado)
# — evita blocos consecutivos do MESMO pool, que arriscariam efeito teto por
# proximidade semântica (ex: as 6 vagas do R3 são quase-paráfrases entre si).
# ORDEM LITERAL CONGELADA — não recalcular em runtime, não reordenar.
QUERIES: list[tuple[str, str]] = [
    ("R3", "vamos continuar nossa conversa"),
    ("R2", "vamos continuar a conversa sobre Redis e Memcached"),
    ("N",  "qual é a capital da Mongólia mesmo?"),
    ("R3", "continuando o que falávamos"),
    ("R2", "me lembra o que a gente concluiu sobre cache de sessões web com Redis"),
    ("N",  "me explica de novo como funciona o RRF no retrieval híbrido"),
    ("R3", "o que a gente tinha concluído mesmo?"),
    ("R2", "voltando ao assunto do Redis para sessões web"),
    ("N",  "qual foi a última vez que ajustamos o piso do NOT_FOUND_FLOOR?"),
    ("R3", "me lembra o que discutimos"),
    ("N",  "pode resumir o que ficou pendente no exp016?"),
    ("R3", "voltando ao que estávamos vendo"),
    ("N",  "o que a gente decidiu sobre o calibrador Bayes-vs-Gauss?"),
    ("R3", "sobre o que conversamos até agora"),
]
assert len(QUERIES) == 14, "3 (R2) + 6 (R3) + 5 (N) = 14 — lista congelada não bate"


# ── Queries — ordem AGRUPADA (E6, PRE_REGISTRO_EXP017.md linhas 202-218) ───
# Mesmas 14 queries do T5, MESMOS textos, relativa interna PRESERVADA;
# única dimensão alterada é o agrupamento por pool (R2, depois R3, depois
# N). Lista literal transcrita do pré-registro — não recalcular em
# runtime, não reordenar, nenhuma query adicionada/removida/reescrita.
QUERIES_AGRUPADA: list[tuple[str, str]] = [
    ("R2", "vamos continuar a conversa sobre Redis e Memcached"),
    ("R2", "me lembra o que a gente concluiu sobre cache de sessões web com Redis"),
    ("R2", "voltando ao assunto do Redis para sessões web"),
    ("R3", "vamos continuar nossa conversa"),
    ("R3", "continuando o que falávamos"),
    ("R3", "o que a gente tinha concluído mesmo?"),
    ("R3", "me lembra o que discutimos"),
    ("R3", "voltando ao que estávamos vendo"),
    ("R3", "sobre o que conversamos até agora"),
    ("N",  "qual é a capital da Mongólia mesmo?"),
    ("N",  "me explica de novo como funciona o RRF no retrieval híbrido"),
    ("N",  "qual foi a última vez que ajustamos o piso do NOT_FOUND_FLOOR?"),
    ("N",  "pode resumir o que ficou pendente no exp016?"),
    ("N",  "o que a gente decidiu sobre o calibrador Bayes-vs-Gauss?"),
]
assert len(QUERIES_AGRUPADA) == 14, "E6 agrupada: lista literal não bate com 14"
assert sorted(QUERIES_AGRUPADA) == sorted(QUERIES), (
    "E6 agrupada precisa ser uma REORDENAÇÃO exata das mesmas 14 queries do "
    "T5 — nenhuma adicionada, removida ou reescrita"
)


# ── Fórmula repeat_rate — espelha retrieval_monitor.py:113-118 (T1a) ──────
# overlap = set(atual) & set(anterior); repeat se len(overlap) >= min(2,k).
# Aplicada aqui ao PAR CONSECUTIVO na sequência executada (mesma semântica
# do monitor: turno atual vs turno imediatamente anterior).

def overlap_binario(atual_ids: list, anterior_ids: list) -> int:
    if not atual_ids or not anterior_ids:
        return 0
    ov = set(atual_ids) & set(anterior_ids)
    return 1 if len(ov) >= min(2, len(atual_ids)) else 0


def overlap_continuo(atual_ids: list, anterior_ids: list) -> float:
    """Emenda E2 — métrica contínua |∩|/k (o binário satura com k pequeno)."""
    if not atual_ids:
        return 0.0
    ov = set(atual_ids) & set(anterior_ids or [])
    return len(ov) / len(atual_ids)


def repeat_rate(seq_ids: list[list]) -> dict:
    """seq_ids: 1 lista de IDs por query, NA ORDEM EXECUTADA (QUERIES)."""
    binarios, continuos = [], []
    for i in range(1, len(seq_ids)):
        binarios.append(overlap_binario(seq_ids[i], seq_ids[i - 1]))
        continuos.append(overlap_continuo(seq_ids[i], seq_ids[i - 1]))
    n = len(binarios)
    return {
        "binario":        (sum(binarios) / n) if n else 0.0,
        "continuo_medio": (sum(continuos) / n) if n else 0.0,
        "n_pares":        n,
    }


def matriz_overlap(seq_ids: list[list]) -> list[list[float]]:
    """Matriz NxN |∩|/k entre TODOS os pares de queries (não só consecutivos)."""
    n = len(seq_ids)
    m = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            a, b = seq_ids[i], seq_ids[j]
            m[i][j] = (len(set(a) & set(b)) / len(a)) if a else 0.0
    return m


def _fmt_matriz(m: list[list[float]]) -> str:
    linhas = []
    header = "      " + " ".join(f"q{j:02}" for j in range(len(m)))
    linhas.append(header)
    for i, row in enumerate(m):
        linhas.append(f"q{i:02}  " + " ".join(f"{v:.2f}" for v in row))
    return "\n".join(linhas)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ordem", choices=["intercalada", "agrupada"], default="intercalada",
        help="intercalada (T5, default) ou agrupada (E6, PRE_REGISTRO_EXP017.md)",
    )
    args = parser.parse_args()
    queries = QUERIES if args.ordem == "intercalada" else QUERIES_AGRUPADA

    base = os.environ.get("EDP_BASE_DIR")
    if not base:
        die("EDP_BASE_DIR não setado (aponte para a cópia fase0)")
    if os.path.basename(base.rstrip("/\\")).lower() == "edp_data":
        die("aponte para a CÓPIA fase0, não para produção")

    from edp.config import EDP_HYBRID_RETRIEVAL as _H, EDP_CTX_SLOTS as _C
    if not (_H and _C):
        die(
            "EDP_HYBRID_RETRIEVAL e EDP_CTX_SLOTS precisam estar ON (default "
            "pós-Fase-1) — mesma exigência de suite_regressao_fase1.py."
        )

    sid = os.environ.get("EDP_SESSION_ID", "default")
    sd = os.path.join(base, "sessions", f"{sid}_cognitive")

    def fp(p):
        import hashlib
        h = hashlib.sha256()
        for r, _d, fs in sorted(os.walk(p)):
            for f in sorted(fs):
                q = os.path.join(r, f)
                h.update(os.path.relpath(q, p).encode())
                try:
                    h.update(open(q, "rb").read())
                except Exception:
                    pass
        return h.hexdigest()[:16]

    tmp = tempfile.mkdtemp(prefix="medir_repeat_exp017_")
    snap = os.path.join(tmp, "pristine")
    shutil.copytree(sd, snap)
    H = fp(snap)

    def restore():
        shutil.rmtree(sd, ignore_errors=True)
        shutil.copytree(snap, sd)

    import edp.config as edp_config
    from edp.runtime.registry import get_runtime

    def rodar_condicao(nome: str, shuffle_on: bool) -> dict:
        edp_config.EDP_RETRIEVE_SHUFFLE = shuffle_on
        restore()
        rt = get_runtime(sid)
        mem = rt._memory

        topk_seq, kept_seq = [], []
        dup_id_frac, dup_hash_frac = [], []
        # E3 (FIX): truncamento por query — kept vs offered_mapeado, lido
        # direto de rt._last_trunc (T2), sem parsear log.
        trunc_evento, trunc_gap = [], []

        print(f"\n== condição {nome} (EDP_RETRIEVE_SHUFFLE={shuffle_on}) ==")
        for pool, q in queries:
            restore()

            topk = [r.get("id") for r in mem.retrieve(q, top_k=5, min_score=0.20)]
            topk_seq.append(topk)

            rt._build_enriched_context(q, "{context}")
            kept = list(getattr(rt, "_last_kept_ids", None) or [])
            kept_hashes = list(getattr(rt, "_last_kept_hashes", None) or [])
            kept_seq.append(kept)

            trunc = getattr(rt, "_last_trunc", None) or {}
            t_kept = trunc.get("kept", len(kept))
            t_offered = trunc.get("offered_mapeado", len(kept))
            trunc_evento.append(1 if t_kept < t_offered else 0)
            trunc_gap.append(t_offered - t_kept)

            k = len(kept)
            dup_id = (k - len(set(kept))) if k else 0
            dup_hash = (k - len(set(kept_hashes))) if k else 0
            dup_id_frac.append(dup_id / k if k else 0.0)
            dup_hash_frac.append(dup_hash / k if k else 0.0)

            print(
                f"    [{pool}] {q[:48]:48} topk={len(topk):2} kept={k:2} "
                f"dup_id={dup_id}/{k} dup_hash={dup_hash}/{k} "
                f"trunc={t_kept}/{t_offered}"
            )

        edp_config.EDP_RETRIEVE_SHUFFLE = False  # reset defensivo entre condições
        restore()

        n_q = len(trunc_evento)
        return {
            "topk_seq": topk_seq,
            "kept_seq": kept_seq,
            "repeat_topk": repeat_rate(topk_seq),
            "repeat_kept": repeat_rate(kept_seq),
            "matriz_topk": matriz_overlap(topk_seq),
            "matriz_kept": matriz_overlap(kept_seq),
            "dup_id_medio": sum(dup_id_frac) / len(dup_id_frac) if dup_id_frac else 0.0,
            "dup_hash_medio": sum(dup_hash_frac) / len(dup_hash_frac) if dup_hash_frac else 0.0,
            "trunc_rate": (sum(trunc_evento) / n_q) if n_q else 0.0,
            "trunc_gap_medio": (sum(trunc_gap) / n_q) if n_q else 0.0,
        }

    res_off = rodar_condicao("OFF", False)
    res_shuffle = rodar_condicao("SHUFFLE", True)
    restore()  # deixa o store exatamente como encontrou

    print("\n" + "=" * 78)
    print("RESUMO — repeat_rate (binário overlap>=min(2,k) | contínuo médio |∩|/k)")
    print("=" * 78)
    for nome, res in (("OFF", res_off), ("SHUFFLE", res_shuffle)):
        rt_ = res["repeat_topk"]
        rk_ = res["repeat_kept"]
        print(f"\n-- {nome} --")
        print(
            f"  top-k bruto     : binario={rt_['binario']:.1%}  "
            f"continuo={rt_['continuo_medio']:.1%}  (n_pares={rt_['n_pares']})"
        )
        print(
            f"  retrieval_kept  : binario={rk_['binario']:.1%}  "
            f"continuo={rk_['continuo_medio']:.1%}  (n_pares={rk_['n_pares']})"
        )
        print(
            f"  dup_rate medio (T4, no kept): id={res['dup_id_medio']:.1%}  "
            f"hash={res['dup_hash_medio']:.1%}"
        )
        print(
            f"  truncamento (E3 FIX, kept vs offered_mapeado): "
            f"taxa={res['trunc_rate']:.1%}  gap_medio={res['trunc_gap_medio']:.2f}"
        )

    print("\n-- Matriz de sobreposição par-a-par (retrieval_kept) — OFF --")
    print(_fmt_matriz(res_off["matriz_kept"]))
    print("\n-- Matriz de sobreposição par-a-par (retrieval_kept) — SHUFFLE --")
    print(_fmt_matriz(res_shuffle["matriz_kept"]))

    diff_pp = (res_off["repeat_kept"]["binario"] - res_shuffle["repeat_kept"]["binario"]) * 100
    print("\n" + "=" * 78)
    print("NÚMEROS PARA AS DECISÕES DO PESQUISADOR (EXP017_FASE0.md) — não decide aqui:")
    print(f"  repeat_OFF (kept, binário)     = {res_off['repeat_kept']['binario']:.1%}")
    print(f"  repeat_SHUFFLE (kept, binário) = {res_shuffle['repeat_kept']['binario']:.1%}")
    print(f"  diff (OFF - SHUFFLE)           = {diff_pp:+.1f}pp")
    print(f"  gate degenerado (diff>=15pp)?    {'SIM — candidato a PARAR' if diff_pp >= 15 else 'não'}")
    print(f"  controle-reserva (|diff|<=5pp)?  {'SIM — candidato a ativar' if abs(diff_pp) <= 5 else 'não'}")
    print(
        "  [tautologia esperada] repeat top-k bruto OFF vs SHUFFLE deveria ser "
        f"IGUAL (SHUFFLE só atua pós-retrieve): "
        f"{res_off['repeat_topk']['binario']:.1%} vs {res_shuffle['repeat_topk']['binario']:.1%}"
    )

    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
