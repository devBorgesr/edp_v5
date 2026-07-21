#!/usr/bin/env python3
"""
scripts/censo_exp017.py — Censo cego Fase 0 (exp017), T2.

READ-ONLY ABSOLUTO: só `open(..., "r")` + `json.load`. Nunca instancia
MemoryStore, nunca escreve no store. Roda no Windows contra o store real
do fase0 (a VM não enxerga esse store — por isso o script fica pronto
aqui e a execução real é do pesquisador).

USO:
  $env:EDP_BASE_DIR = "C:\\edp_data_fase0"
  python scripts/censo_exp017.py

Fenômenos medidos (PRE_REGISTRO_EXP017.md):
  A — duplicação no STORE, POR CAMADA: N registros de texto idêntico
      (hash normalizado), IDs distintos, MESMA camada.
  D — duplicação CROSS-CAMADA por promoção: mesmo ID presente em
      episódica E semântica.

Hash normalizado: strip + casefold + colapso de whitespace — MESMA
função usada em conta_catalogo.py/discrimina_par.py (19/07), para que a
validação de sanidade abaixo reproduza os achados daquela rodada.
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys


def die(msg: str) -> None:
    print(f"[ERRO] {msg}")
    sys.exit(2)


def norm(text: str) -> str:
    """strip + casefold + colapso de whitespace (padrão 19/07)."""
    return re.sub(r"\s+", " ", (text or "").strip().casefold())


def load_entries(path: str) -> list[dict]:
    if not os.path.exists(path):
        print(f"  [aviso] {path} não existe — tratando como camada vazia")
        return []
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    entries = raw.get("entries") if isinstance(raw, dict) else raw
    return entries or []


def fenomeno_a(entries: list[dict], camada: str) -> dict:
    """Clusters n>1 por hash normalizado, dentro de UMA camada."""
    by_hash: dict[str, list[dict]] = collections.defaultdict(list)
    for e in entries:
        h = norm(e.get("text", ""))
        by_hash[h].append(e)

    clusters = {h: es for h, es in by_hash.items() if h and len(es) > 1}
    n_total = len(entries)
    n_duplicado = sum(len(es) for es in clusters.values())
    pct = (n_duplicado / n_total * 100) if n_total else 0.0

    return {
        "camada": camada,
        "n_total": n_total,
        "n_duplicado": n_duplicado,
        "pct_A": pct,
        "clusters": clusters,  # hash -> [entries]
    }


def fenomeno_d(ep_entries: list[dict], sem_entries: list[dict]) -> dict:
    """Interseção de IDs entre camadas — mesmo ID em episódica E semântica."""
    ep_ids = {e.get("id") for e in ep_entries if e.get("id")}
    sem_ids = {e.get("id") for e in sem_entries if e.get("id")}
    inter = ep_ids & sem_ids
    return {"ids": sorted(inter), "n": len(inter)}


def top_clusters(resultados_a: list[dict], k: int = 10) -> list[tuple]:
    """Top-k clusters combinando as camadas, ordenado por tamanho desc."""
    todos = []
    for res in resultados_a:
        for h, es in res["clusters"].items():
            todos.append((len(es), res["camada"], h, es))
    todos.sort(key=lambda t: -t[0])
    return todos[:k]


def validacao_sanidade(resultados_a: list[dict], d: dict) -> bool:
    """Os achados de 19/07 devem reaparecer. Se não, o script está errado."""
    ok = True

    cluster_oi_10 = any(
        len(es) == 10 and h.startswith("q: oi")
        for res in resultados_a
        for h, es in res["clusters"].items()
    )
    if not cluster_oi_10:
        print("  [SANIDADE FALHOU] cluster 10x 'Q: oi' (write-side) não reapareceu")
        ok = False
    else:
        print("  [sanidade OK] cluster 10x 'Q: oi' presente")

    alvo_d = {"f54471a1", "31162822"}
    achados_d = {i for i in d["ids"] if any(i.startswith(a) for a in alvo_d)}
    if len(achados_d) < len(alvo_d):
        print(
            f"  [SANIDADE FALHOU] IDs D esperados (f54471a1.../31162822...) "
            f"não reapareceram — achados: {sorted(achados_d)}"
        )
        ok = False
    else:
        print("  [sanidade OK] f54471a1.../31162822... presentes na lista D")

    return ok


def main() -> int:
    base = os.environ.get("EDP_BASE_DIR")
    if not base:
        die("EDP_BASE_DIR não setado (aponte para o store fase0)")
    if os.path.basename(base.rstrip("/\\")).lower() == "edp_data":
        die("aponte para a CÓPIA fase0, não para produção")

    sid = os.environ.get("EDP_SESSION_ID", "default")
    sd = os.path.join(base, "sessions", f"{sid}_cognitive")

    ep_path = os.path.join(sd, "episodic.json")
    sem_path = os.path.join(sd, "semantic.json")

    print(f"== censo_exp017 — {sd} ==")
    ep_entries = load_entries(ep_path)
    sem_entries = load_entries(sem_path)

    res_ep = fenomeno_a(ep_entries, "episodic")
    res_sem = fenomeno_a(sem_entries, "semantic")
    resultados_a = [res_ep, res_sem]

    d = fenomeno_d(ep_entries, sem_entries)

    print("\n-- Fenômeno A (duplicação write-side, por camada) --")
    for res in resultados_a:
        print(
            f"  {res['camada']:10} total={res['n_total']:5} "
            f"duplicado={res['n_duplicado']:5} %A={res['pct_A']:.1f}%"
        )

    print("\n-- Top-10 clusters (tamanho, camada, preview 70ch, IDs) --")
    for n, camada, h, es in top_clusters(resultados_a, 10):
        ids = [str(e.get("id", "?")) for e in es]
        print(f"  {n:3}x [{camada:8}] {h[:70]!r}")
        print(f"        IDs: {ids}")

    print(f"\n-- Fenômeno D (cross-camada, mesmo ID) --")
    print(f"  contagem D = {d['n']}")
    print(f"  IDs D = {d['ids']}")

    print("\n-- Validação de sanidade (achados de 19/07) --")
    ok = validacao_sanidade(resultados_a, d)

    print("\n" + "=" * 60)
    print(f"VEREDITO SANIDADE: {'OK' if ok else 'FALHOU — revisar script/dados antes de usar os números'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
