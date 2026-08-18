"""
funde_stores.py — funde o store órfão de volta no store vivo (18/08/2026).

POR QUE EXISTE

`EDP_BASE_DIR` tem TRÊS defaults diferentes no código:

    config.py:9         -> /content/edp_v3_memory
    pareto_store.py:223 -> data              (relativo ao cwd)
    lineage.py:315      -> C:/edp_data

Com a variável indefinida, o EDP gravou em `<repo>/data` entre 12 e 13/08 —
18 entradas episódicas, 18 registros de lineage e as 38 amostras de
`token_usage` da Fase 1 — enquanto o store histórico (137 entradas) ficou
parado. Sintoma observado pelo pesquisador em 18/08: o EDP não lembrava da
conversa da semana anterior.

Este script funde ORIGEM -> DESTINO sem perder nada dos dois.

SEGURANÇA

  - dry-run por PADRÃO; só escreve com --aplicar
  - faz backup do destino antes de qualquer escrita
  - deduplica por chave estável -> rodar duas vezes é no-op
  - NUNCA apaga: só acrescenta o que falta
  - escrita atômica (tmp + replace)

PARE O EDP ANTES DE RODAR COM --aplicar. Com o servidor de pé, ele
sobrescreve o arquivo em memória por cima da fusão.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

# O que é fundido, e como deduplicar. O que NÃO está aqui não é tocado.
JSON_LISTA = {                       # arquivo -> chave de dedup
    "sessions/default_cognitive/episodic.json": "id",
    "sessions/default_cognitive/semantic.json": "id",
}
JSONL = {
    "sessions/default_cognitive/lineage.jsonl": "response_id",
    "pareto/events.jsonl": None,     # None -> dedup pela linha inteira
}

# Declarado em vez de silenciado: co_occurrence.json guarda CONTAGENS de pares,
# e somá-las exigiria decidir se a co-ocorrência de dois stores separados é
# somável — não é óbvio que seja. blocks.json e health_history.jsonl têm valor
# baixo e estrutura própria. Nenhum dos três é tocado.
NAO_FUNDIDOS = ("co_occurrence.json", "blocks.json", "health_history.jsonl",
                "embed_cache.sqlite", "metrics.jsonl")


def _carrega_lista(p: Path):
    if not p.exists():
        return [], None
    d = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(d, list):
        return d, None
    for k in ("entries", "episodic", "semantic"):
        if isinstance(d.get(k), list):
            return d[k], k
    return [], None


def _grava_atomico(p: Path, texto: str) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(texto, encoding="utf-8")
    os.replace(tmp, p)


def funde_lista(origem: Path, destino: Path, chave: str, aplicar: bool) -> dict:
    orig, _ = _carrega_lista(origem)
    dest, envelope = _carrega_lista(destino)
    vistos = {x.get(chave) for x in dest if x.get(chave)}
    novos = [x for x in orig if x.get(chave) and x[chave] not in vistos]
    colisoes = sum(1 for x in orig if x.get(chave) in vistos)

    if aplicar and novos:
        juntos = dest + novos
        juntos.sort(key=lambda x: float(x.get("timestamp") or 0))
        saida = juntos if envelope is None else {
            **json.loads(destino.read_text(encoding="utf-8")), envelope: juntos}
        _grava_atomico(destino, json.dumps(saida, ensure_ascii=False))
    return {"origem": len(orig), "destino": len(dest),
            "novos": len(novos), "colisoes": colisoes}


def funde_jsonl(origem: Path, destino: Path, chave, aplicar: bool) -> dict:
    lo = [l for l in origem.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if origem.exists() else []
    ld = [l for l in destino.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if destino.exists() else []

    if chave is None:
        vistos = set(ld)
        novos = [l for l in lo if l not in vistos]
    else:
        def _k(l):
            try:
                return json.loads(l).get(chave)
            except Exception:
                return None
        vistos = {_k(l) for l in ld}
        novos = [l for l in lo if _k(l) not in vistos]

    if aplicar and novos:
        destino.parent.mkdir(parents=True, exist_ok=True)
        _grava_atomico(destino, "\n".join(ld + novos) + "\n")
    return {"origem": len(lo), "destino": len(ld), "novos": len(novos),
            "colisoes": len(lo) - len(novos)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Funde store órfão no store vivo")
    p.add_argument("--origem", required=True)
    p.add_argument("--destino", required=True)
    p.add_argument("--aplicar", action="store_true",
                   help="sem isto, só mostra o que faria")
    a = p.parse_args(argv)

    o, d = Path(a.origem), Path(a.destino)
    if not o.is_dir() or not d.is_dir():
        print(f"[RECUSADO] origem ou destino não é diretório\n  {o}\n  {d}")
        return 1
    if o.resolve() == d.resolve():
        print("[RECUSADO] origem e destino são o mesmo diretório")
        return 1

    print("=" * 72)
    print(f"{'FUSÃO REAL' if a.aplicar else 'DRY-RUN (nada é escrito)'}")
    print(f"  origem : {o}")
    print(f"  destino: {d}")
    print("=" * 72)

    if a.aplicar:
        bkp = d / f"_backup_fusao_{time.strftime('%Y%m%d_%H%M%S')}"
        bkp.mkdir(parents=True, exist_ok=True)
        for rel in list(JSON_LISTA) + list(JSONL):
            f = d / rel
            if f.exists():
                alvo = bkp / rel
                alvo.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, alvo)
        print(f"  backup do destino: {bkp}\n")

    total_novos = 0
    for rel, chave in JSON_LISTA.items():
        r = funde_lista(o / rel, d / rel, chave, a.aplicar)
        total_novos += r["novos"]
        print(f"  {rel}")
        print(f"    origem={r['origem']:>5}  destino={r['destino']:>5}  "
              f"NOVOS={r['novos']:>4}  ja_presentes={r['colisoes']:>4}")

    for rel, chave in JSONL.items():
        r = funde_jsonl(o / rel, d / rel, chave, a.aplicar)
        total_novos += r["novos"]
        print(f"  {rel}")
        print(f"    origem={r['origem']:>5}  destino={r['destino']:>5}  "
              f"NOVOS={r['novos']:>4}  ja_presentes={r['colisoes']:>4}")

    print(f"\n  total a acrescentar: {total_novos}")
    print(f"  NÃO fundidos (declarado): {', '.join(NAO_FUNDIDOS)}")
    if not a.aplicar:
        print("\n  Nada foi escrito. Para aplicar: repita com --aplicar")
        print("  E PARE O EDP ANTES — servidor de pé sobrescreve a fusão.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
