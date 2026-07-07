#!/usr/bin/env python3
"""
exp011_guardas.py — Guardas 5 e 6 do exp011 (§1.5 do pré-registro da Fase 1).

Chamada direta (zero chat, zero LLM), snapshot/restore por medição.
USO (servidor parado):
    $env:EDP_BASE_DIR="C:\\edp_data_fase0"; $env:EDP_HYBRID_RETRIEVAL="1"
    python exp011_guardas.py --guarda5          # roda OFF e ON internamente
    python exp011_guardas.py --guarda6

GUARDA 5 — query sem memória correspondente ("buracos de minhoca"): sem crash,
prompt bem-formado, metadados presentes, remaining>=0, OFF vs ON reportados.
GUARDA 6 — ground truth exp009/010: para cada (query, needles), alvo no CP1
(mem.retrieve) deve chegar ao CP3 (prompt) com flag ON. CP1-sim-CP3-não = FALHA.
"""
from __future__ import annotations
import argparse, importlib, os, sys

G5_QUERY = "me fale sobre buracos de minhoca e viagem no tempo"
# (query, needle-que-identifica-o-alvo-no-texto) — ids/needles do ground truth
# exp009/010 validados no store real; EDITÁVEL.
G6 = [
    ("vamos continuar a conversa sobre Redis e Memcached", "chave-valor"),
    ("continuando nossa conversa sobre transformers e atenção em LLMs", "transformer"),
    ("voltando ao que discutimos sobre embeddings de frases", "embedding"),
    ("sobre o RAG e as alucinações que a gente discutiu", "RAG"),
    ("continuando o papo sobre desempenho de Python em tempo real", "Python"),
]
META_NEEDLES = ["ÂNCORA TEMPORAL"]  # metadados que não podem sumir (g5)


def _rt():
    from edp.runtime.registry import get_runtime, is_valid
    sid = os.environ.get("EDP_SESSION_ID", "default")
    rt = get_runtime(sid)
    assert is_valid(rt), "runtime inválido"
    return rt


def _render(rt, q):
    sysp = getattr(rt, "SYSTEM_TEMPLATE", None) or "Assistente.\n{context}\n"
    rendered, meta = rt._build_enriched_context(q, sysp)
    b = (meta or {}).get("budget") or {}
    return rendered, b


def guarda5():
    import edp.config as C
    flag = "ON" if C.EDP_CTX_SLOTS else "OFF"
    rt = _rt()
    try:
        rendered, b = _render(rt, G5_QUERY)
    except Exception as e:
        print(f"  [g5 {flag}] CRASH: {type(e).__name__}: {e} -> FALHA"); return 1
    metas = {m: (m in rendered) for m in META_NEEDLES}
    ok = all(metas.values()) and (b.get("remaining", 0) >= 0)
    print(f"  [g5 {flag}] rendered_len={len(rendered)} remaining={b.get('remaining')} "
          f"retrieval_tokens={b.get('retrieval_tokens')} metadados_presentes={metas} "
          f"-> {'PASSA' if ok else 'FALHA'}")
    return 0 if ok else 1


def guarda6():
    import edp.config as C
    flag = "ON" if C.EDP_CTX_SLOTS else "OFF"
    rt = _rt(); mem = rt._memory
    rc = 0
    print(f"  [g6 {flag}] alvo-no-CP1 deve chegar ao CP3:")
    for q, needle in G6:
        r1 = mem.retrieve(q, top_k=5, min_score=0.20)
        cp1 = any(needle.lower() in (r.get("text") or "").lower() for r in r1)
        rendered, _ = _render(rt, q)
        cp3 = needle.lower() in rendered.lower()
        verdict = "ok" if (not cp1 or cp3) else "FALHA (CP1 sim, CP3 não)"
        if cp1 and not cp3: rc = 1
        print(f"    {q[:52]:52} CP1={cp1} CP3={cp3} -> {verdict}")
    return rc


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--guarda5", action="store_true")
    p.add_argument("--guarda6", action="store_true")
    a = p.parse_args()
    base = os.environ.get("EDP_BASE_DIR", "")
    if os.path.basename(base.rstrip("/\\")).lower() == "edp_data":
        print("[ERRO] rode sobre CÓPIA, não produção."); return 2
    rc = 0
    if a.guarda5: rc |= guarda5()
    if a.guarda6: rc |= guarda6()
    if not (a.guarda5 or a.guarda6): p.print_help(); return 2
    print("  (retrieve muta acessos — rode sobre cópia; restaure se for repetir)")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
