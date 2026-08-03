#!/usr/bin/env python3
"""
scripts/calibracao_h1_exp017.py — T5 (Fase 1): dup_rate@10 nas queries de
calibração, condições OFF e ON.

Contra o store real (a VM não o enxerga — roda no Windows do pesquisador).
Cada query mede `mem.retrieve(q, top_k=10)` DIRETO no store — literal do
critério de PRE_REGISTRO_EXP017.md: "PASSA H1 sse, com flag ON: dup_rate@10
= 0 (por ID E por hash) nos retrieves de calibração". Não passa pelo
context_builder/retrieval_kept — esse é o ponto de medição do H2/repeat_rate
(script separado, medir_repeat_exp017.py), não do H1.

Queries de calibração CONGELADAS (pré-dado, derivadas da Fase 0 — nenhuma
outra):
  C1 "oi"                                            — espécime #4 (10x "oi"
      write-side, PRE_REGISTRO_EXP017.md motivação #4) + achado D (dupla
      cross-camada mesmo ID, f54471a1/31162822)
  C2 "me lembra o que discutimos"                     — dup 1/5, Fase 0 (T5)
  C3 "qual foi a última vez que ajustamos o piso       — dup 2/5, Fase 0 (T5)
      do NOT_FOUND_FLOOR?"
  C4 "voltando ao que estávamos vendo"                 — dup 1/5, Fase 0 (T5)
  C5 "pode resumir o que ficou pendente no exp016?"     — dup 1/5, Fase 0 (T5)

SANIDADE por query (OFF): a condição OFF precisa reproduzir dup>0 (id OU
hash) — senão o espécime evaporou do store entre a Fase 0 e esta rodada
(dado mutável: consolidação, decay, poda) e o resultado de calibração NÃO É
VÁLIDO para essa query.

Veredito PARCIAL de H1 impresso ao final — só a perna "dup_rate@10 == 0 com
ON". As outras pernas do critério PASSA H1 (R1 CP3, R2 Recall@5, R3 %SS,
suite pytest) são medidas por suite_regressao_fase1.py. Este script NÃO
decide H1 sozinho — quem decide é o pesquisador (PRE_REGISTRO_EXP017.md).

Snapshot/restore por medição — mesmo padrão de medir_repeat_exp017.py e
suite_regressao_fase1.py: restore() ANTES de cada query, para toda medição
partir do mesmo estado (retrieve muta acessos/ultimo_acesso).

USO (servidor parado):
  $env:EDP_BASE_DIR = "C:\\edp_data_fase0"   # ou a cópia atual do store
  python scripts/calibracao_h1_exp017.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile


def die(msg: str) -> None:
    print(f"[ERRO] {msg}")
    sys.exit(2)


# ── Queries de calibração CONGELADAS (pré-dado) ────────────────────────────
# Lista literal — não recalcular em runtime, não reordenar, não adicionar,
# não remover, não reescrever.
CALIBRACAO: list[tuple[str, str]] = [
    ("C1", "oi"),
    ("C2", "me lembra o que discutimos"),
    ("C3", "qual foi a última vez que ajustamos o piso do NOT_FOUND_FLOOR?"),
    ("C4", "voltando ao que estávamos vendo"),
    ("C5", "pode resumir o que ficou pendente no exp016?"),
]
assert len(CALIBRACAO) == 5, "lista de calibração congelada não bate — não adicionar/remover queries"


def _normalize_text(text) -> str:
    """strip + casefold + colapso de whitespace — MESMA normalização do
    censo (scripts/censo_exp017.py) e de _dedup_ranked (edp/memory/store.py)."""
    import re
    return re.sub(r"\s+", " ", (text or "").strip().casefold())


def dup_stats(results: list[dict]) -> dict:
    """dup_rate@10 literal: k=len(results), dup_id/dup_hash = quantas
    ENTRADAS a mais existem além do número de valores únicos (id/hash)."""
    ids = [r.get("id") for r in results]
    hashes = [_normalize_text(r.get("text")) for r in results]
    k = len(results)
    dup_id = (k - len(set(ids))) if k else 0
    dup_hash = (k - len(set(hashes))) if k else 0
    return {"k": k, "dup_id": dup_id, "dup_hash": dup_hash}


def main() -> int:
    base = os.environ.get("EDP_BASE_DIR")
    if not base:
        die("EDP_BASE_DIR não setado (aponte para o store a calibrar)")
    if os.path.basename(base.rstrip("/\\")).lower() == "edp_data":
        die("aponte para uma CÓPIA do store, não para produção")

    sid = os.environ.get("EDP_SESSION_ID", "default")
    sd = os.path.join(base, "sessions", f"{sid}_cognitive")
    if not os.path.isdir(sd):
        die(f"sessão não encontrada: {sd}")

    tmp = tempfile.mkdtemp(prefix="calibracao_h1_exp017_")
    snap = os.path.join(tmp, "pristine")
    shutil.copytree(sd, snap)

    def restore():
        shutil.rmtree(sd, ignore_errors=True)
        shutil.copytree(snap, sd)

    import edp.config as edp_config
    from edp.runtime.registry import get_runtime

    def rodar_condicao(nome: str, dedup_on: bool) -> list[dict]:
        edp_config.EDP_RETRIEVE_DEDUP = dedup_on
        restore()
        rt = get_runtime(sid)
        mem = rt._memory

        print(f"\n== condição {nome} (EDP_RETRIEVE_DEDUP={dedup_on}) ==")
        linhas = []
        for cod, q in CALIBRACAO:
            restore()
            results = mem.retrieve(q, top_k=10)
            stats = dup_stats(results)
            linhas.append({"cod": cod, "query": q, **stats})
            print(
                f"    [{cod}] {q[:60]:60} k={stats['k']:2} "
                f"dup_id={stats['dup_id']}/{stats['k']} "
                f"dup_hash={stats['dup_hash']}/{stats['k']}"
            )

        edp_config.EDP_RETRIEVE_DEDUP = False  # reset defensivo entre condições
        restore()
        return linhas

    res_off = rodar_condicao("OFF", False)
    res_on = rodar_condicao("ON", True)
    restore()  # deixa o store exatamente como encontrou

    print("\n" + "=" * 78)
    print("SANIDADE — OFF deve reproduzir dup>0 (id OU hash) em CADA query")
    print("(senão o espécime evaporou do store desde a Fase 0 — resultado inválido)")
    print("=" * 78)
    sanidade_ok = True
    for linha in res_off:
        dup_presente = (linha["dup_id"] > 0) or (linha["dup_hash"] > 0)
        status = "OK" if dup_presente else "FALHOU — espécime evaporou"
        if not dup_presente:
            sanidade_ok = False
        print(f"  [{linha['cod']}] {linha['query'][:50]:50} {status}")

    print("\n" + "=" * 78)
    print("VEREDITO PARCIAL H1 — só a perna dup_rate@10==0 com ON.")
    print("R1/R2/R3 e a suite pytest (as outras pernas do critério PASSA H1) são")
    print("medidas por suite_regressao_fase1.py — este script não decide H1 sozinho.")
    print("=" * 78)
    h1_dup_zero = True
    for linha in res_on:
        zerou = (linha["dup_id"] == 0) and (linha["dup_hash"] == 0)
        if not zerou:
            h1_dup_zero = False
        print(
            f"  [{linha['cod']}] dup_id={linha['dup_id']} dup_hash={linha['dup_hash']} "
            f"{'zerou' if zerou else 'NÃO ZEROU'}"
        )

    print()
    if not sanidade_ok:
        print(
            "[ALERTA] sanidade FALHOU em >=1 query — reportar ao pesquisador antes "
            "de tirar qualquer conclusão de H1 a partir desta rodada."
        )
    print(
        f"dup_rate@10 == 0 (id E hash) em TODAS as queries de calibração com ON? "
        f"{'SIM' if h1_dup_zero else 'NÃO'}"
    )

    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
