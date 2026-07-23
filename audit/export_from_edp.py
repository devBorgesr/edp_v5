#!/usr/bin/env python3
"""
audit/export_from_edp.py — adaptador EDP → JSONL de audit/retrieval_audit.py.

Este arquivo PODE importar edp (vive do lado EDP da fronteira; T2/T5 do
degrau 1). Roda uma lista de queries (uma por linha, texto puro) contra
um store EDP real e emite o JSONL consumido pelo auditor.

Uso:
    export EDP_BASE_DIR=/caminho/para/edp_data
    python audit/export_from_edp.py queries.txt -o export.jsonl --top-k 10

Nota: mem.retrieve() muta estatísticas de acesso do store (não é
read-only). Para uma cópia de produção isso é irrelevante; rodando
direto contra produção, os contadores de acesso mudam.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Exporta retrieves de um store EDP para JSONL.")
    ap.add_argument("queries", help="Arquivo texto com uma query por linha")
    ap.add_argument("-o", "--output", required=True, help="Caminho do JSONL de saída")
    ap.add_argument("--top-k", type=int, default=10, help="top_k passado a mem.retrieve() (default: 10)")
    ap.add_argument("--session-id", default="default", help="EDP_SESSION_ID (default: 'default')")
    args = ap.parse_args(argv)

    base = os.environ.get("EDP_BASE_DIR")
    if not base:
        print("[erro] EDP_BASE_DIR não setado (aponte para o store a auditar)", file=sys.stderr)
        return 2

    queries_path = Path(args.queries)
    if not queries_path.exists():
        print(f"[erro] arquivo de queries não encontrado: {args.queries}", file=sys.stderr)
        return 2
    queries = [ln.strip() for ln in queries_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not queries:
        print("[erro] arquivo de queries vazio", file=sys.stderr)
        return 2

    from edp.runtime.registry import get_memory, is_valid, get_error
    mem = get_memory(args.session_id)
    if not is_valid(mem):
        print(f"[erro] falha ao inicializar memória: {get_error(mem)}", file=sys.stderr)
        return 2

    n_ok = 0
    with open(args.output, "w", encoding="utf-8") as out:
        for q in queries:
            try:
                results = mem.retrieve(q, top_k=args.top_k)
            except Exception as e:
                print(f"[aviso] query falhou, pulando: {q[:60]!r} ({type(e).__name__}: {e})", file=sys.stderr)
                continue
            record = {
                "query": q,
                "results": [
                    {
                        "id": r.get("id"),
                        "text": r.get("text", ""),
                        "score": r.get("ranking_score"),
                    }
                    for r in results
                ],
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            n_ok += 1

    print(f"Export escrito em {args.output} ({n_ok}/{len(queries)} queries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
