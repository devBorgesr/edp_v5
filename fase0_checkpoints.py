#!/usr/bin/env python3
"""
fase0_checkpoints.py — FASE 0: memória real vs. negações — CONFIRMAÇÃO DE MECANISMOS.

ZERO conserto. Só medição, por CHAMADA DIRETA aos métodos reais de produção
(mem.retrieve, _retrieve_context, _build_enriched_context) — SEM websocket, SEM
turno de chat, NENHUMA negação nova gravada (regra anti-recontaminação).

USO (Windows PowerShell, servidor EDP PARADO):

  # TESTE 1 — store LIMPO (cópia NOVA da produção):
  robocopy C:\\edp_data C:\\edp_data_fase0 /E
  $env:EDP_BASE_DIR = "C:\\edp_data_fase0"
  $env:EDP_HYBRID_RETRIEVAL = "1"
  python fase0_checkpoints.py --teste1

  # TESTE 2 — store CONTAMINADO (como está, não limpar):
  $env:EDP_BASE_DIR = "C:\\edp_data_hybrid_test"
  python fase0_checkpoints.py --teste2

Isolamento: snapshot pristine do dir da sessão + restore antes de CADA checkpoint
(o retrieve muta acessos++, memory.py:871; _retrieve_context grava co_occurrence).
Ao final, restore + verificação de no-divergência — o store fica como entrou.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile

QUERY = "vamos continuar a conversa sobre Redis e Memcached"   # canônica

# Ids rastreados (memórias REAIS de conteúdo Redis, do lineage fa6df49e).
# Overrides por env (só para validar o encanamento em outro store; a rodada
# oficial usa os defaults): EDP_FASE0_TRACKED="id1,id2" EDP_FASE0_TARGET=...
TRACKED = [t.strip() for t in os.environ.get(
    "EDP_FASE0_TRACKED", "4c57ed7a,0c78fa08,bf15dc2d,29d5ded2,7c7d6ce9").split(",") if t.strip()]
TARGET  = os.environ.get("EDP_FASE0_TARGET", "4c57ed7a")
NEEDLE_TARGET = os.environ.get("EDP_FASE0_NEEDLE", "chave-valor")  # prova o alvo no prompt

NEG_RE = re.compile(r"n[ãa]o encontro", re.IGNORECASE)


def _die(m, c=2):
    print(f"\n[ERRO] {m}\n", file=sys.stderr); sys.exit(c)


def _fp(path):
    h = hashlib.sha256()
    for root, _d, fs in sorted(os.walk(path)):
        for fn in sorted(fs):
            p = os.path.join(root, fn)
            h.update(os.path.relpath(p, path).encode())
            try: h.update(open(p, "rb").read())
            except Exception: pass
    return h.hexdigest()[:16]


class Guard:
    def __init__(self, sess_dir):
        self.sess_dir = sess_dir
        self._tmp = tempfile.mkdtemp(prefix="fase0_")
        self.snap = os.path.join(self._tmp, "pristine")
        shutil.copytree(sess_dir, self.snap)
        self.hash = _fp(self.snap)
    def restore(self):
        if os.path.isdir(self.sess_dir): shutil.rmtree(self.sess_dir)
        shutil.copytree(self.snap, self.sess_dir)
    def ok(self): return _fp(self.sess_dir) == self.hash
    def clean(self): shutil.rmtree(self._tmp, ignore_errors=True)


def _entries(sess_dir):
    out = []
    for n in ("episodic.json", "semantic.json"):
        p = os.path.join(sess_dir, n)
        if not os.path.exists(p): continue
        try: d = json.load(open(p, encoding="utf-8"))
        except Exception: continue
        if isinstance(d, dict): d = d.get("entries", [])
        out += [e for e in d if isinstance(e, dict)]
    return out


def _short(i): return str(i or "")[:8]


def _paths():
    base = os.environ.get("EDP_BASE_DIR") or _die(
        "EDP_BASE_DIR não setado. Aponte para a CÓPIA (nunca C:\\edp_data direto).")
    base = os.path.abspath(base)
    if os.path.basename(base.rstrip("/\\")).lower() == "edp_data" and \
       os.environ.get("ALLOW_PROD", "0") != "1":
        _die(f"'{base}' parece a PRODUÇÃO. Rode sobre cópia (robocopy).")
    sid = os.environ.get("EDP_SESSION_ID", "default")
    sc  = os.environ.get("EDP_SCOPE", "cognitive")
    sd  = os.path.join(base, "sessions", f"{sid}_{sc}")
    os.path.isdir(sd) or _die(f"sessão não existe: {sd}")
    return base, sid, sd


def contar_negacoes(entries):
    """(b) do Teste 1: negações sobre Redis no store — reportar ANTES de assumir limpo."""
    negs = [e for e in entries
            if NEG_RE.search(e.get("text") or "") and "redis" in (e.get("text") or "").lower()]
    return negs


# ═══════════════ TESTE 1 — 3 checkpoints por id (chamada direta) ═══════════════

def teste1():
    base, sid, sess_dir = _paths()
    flag = os.environ.get("EDP_HYBRID_RETRIEVAL")
    print("=" * 88)
    print(f"FASE 0 / TESTE 1 — 3 checkpoints | base={base}")
    print(f"  EDP_HYBRID_RETRIEVAL={flag!r}  (esperado '1')")
    guard = Guard(sess_dir)
    try:
        snap_entries = _entries(guard.snap)
        negs = contar_negacoes(snap_entries)
        print(f"\n  (b) NEGAÇÕES sobre Redis neste store: {len(negs)}")
        for n in negs[:6]:
            print(f"      id={_short(n.get('id'))} | {(n.get('text') or '')[:80]}")
        if negs:
            print("      >>> ATENÇÃO: store NÃO está limpo — a leitura do Teste 1 muda (reportar).")
        texto_por_id = { _short(e.get("id")): (e.get("text") or "") for e in snap_entries }
        presentes = [t for t in TRACKED if t in texto_por_id]
        print(f"  ids rastreados presentes no store: {presentes} (de {TRACKED})")
        if TARGET not in texto_por_id:
            print(f"  >>> OBSTÁCULO: alvo {TARGET} não existe neste store — CP1-3 sem sentido aqui.")
            return 1

        from edp.runtime.registry import get_runtime, is_valid
        rt = get_runtime(sid)
        if not is_valid(rt):
            print(">>> OBSTÁCULO: get_runtime inválido — chamada direta inviável. PARANDO (sem turno vivo)."); return 1
        mem = rt._memory

        def acha(texto_blocos: str):
            """quais ids rastreados têm o próprio texto (primeiros 60 chars) no material"""
            hits = []
            for t in presentes:
                needle = re.sub(r"\s+", " ", texto_por_id[t][:60]).strip()
                if needle and needle[:40] in re.sub(r"\s+", " ", texto_blocos):
                    hits.append(t)
            return hits

        # ── CP1: mem.retrieve (o MESMO do websocket:716) ──────────────────────
        guard.restore()
        r1 = mem.retrieve(QUERY, top_k=5, min_score=0.20)
        ids1 = [_short(r.get("id")) for r in r1]
        print(f"\n  CP1 mem.retrieve(top_k=5): ids={ids1}")
        for r in r1:
            print(f"      {_short(r.get('id'))} score={r.get('ranking_score')} len={len(r.get('text') or '')}"
                  f"{'  <<NEGACAO' if NEG_RE.search(r.get('text') or '') else ''}"
                  f"{'  <<ALVO' if _short(r.get('id'))==TARGET else ''}")
        cp1 = TARGET in ids1

        # ── CP2: _retrieve_context (retrieve interno :2334 + seen_ids :2340) ──
        guard.restore()
        # instrumenta EM PROCESSO (não altera edp/): captura o retrieve interno
        from edp.memory import MemoryStore
        captured = {}
        orig = MemoryStore.retrieve
        def spy(self, q, **kw):
            res = orig(self, q, **kw)
            captured.setdefault("cp2_retrieve", [ _short(x.get("id")) for x in res ])
            return res
        MemoryStore.retrieve = spy
        try:
            blocks, hits = rt._retrieve_context(QUERY)
        finally:
            MemoryStore.retrieve = orig
        blocos_txt = "\n".join(blocks)
        ids2 = acha(blocos_txt)
        # seen_ids reconstruído: janela imediata = últimos 6 turnos não-summary
        conv = sorted([e for e in snap_entries
                       if e.get("source_type") != "session_summary" and e.get("timestamp")],
                      key=lambda e: e.get("timestamp", 0))
        seen_janela = [_short(e.get("id")) for e in conv[-6:]]
        print(f"\n  CP2 _retrieve_context: blocks={len(blocks)} hits={hits}")
        print(f"      retrieve interno (:2334) devolveu: {captured.get('cp2_retrieve')}")
        print(f"      ids rastreados PRESENTES nos blocks: {ids2}")
        print(f"      janela imediata (fonte do seen_ids, últimos 6): {seen_janela}")
        dentro = captured.get("cp2_retrieve") or []
        if TARGET in dentro and TARGET not in ids2:
            causa = "seen_ids skip (:2340-2342)" if TARGET in seen_janela else "descartado pós-retrieve (outra causa)"
            print(f"      >>> {TARGET} ENTROU no retrieve interno e MORREU nos blocks — causa: {causa}")
        cp2 = TARGET in ids2

        # ── CP3: _build_enriched_context → prompt final ───────────────────────
        guard.restore()
        system_prompt = getattr(rt, "SYSTEM_TEMPLATE", None) or \
            "Voce e um assistente.\n{context}\n"
        rendered, meta = rt._build_enriched_context(QUERY, system_prompt)
        ids3 = acha(rendered)
        tem_needle = NEEDLE_TARGET in rendered
        print(f"\n  CP3 _build_enriched_context: rendered_len={len(rendered)}")
        print(f"      '{NEEDLE_TARGET}' no prompt? {tem_needle}")
        print(f"      ids rastreados no prompt final: {ids3}")
        print(f"      budget={ (meta.get('budget') or {}) if isinstance(meta, dict) else '?' }")
        cp3 = TARGET in ids3 or tem_needle

        print(f"\n  VEREDITO T1: {TARGET} — CP1={'OK' if cp1 else 'MORRE'} | "
              f"CP2={'OK' if cp2 else 'MORRE'} | CP3={'OK' if cp3 else 'MORRE'}")
        return 0
    finally:
        guard.restore()
        print(f"\n  isolamento: store restaurado == snapshot? {guard.ok()}")
        guard.clean()


# ═══════════════ TESTE 2 — BM25 isolado + RRF (store contaminado) ══════════════

def teste2():
    base, sid, sess_dir = _paths()
    print("=" * 88)
    print(f"FASE 0 / TESTE 2 — eco de query, BM25 isolado | base={base}")
    guard = Guard(sess_dir)   # leitura pura, mas garante estado
    try:
        entries = [e for e in _entries(guard.snap)
                   if e.get("id") and (e.get("text") or "").strip() and e.get("embedding")]
        negs_ids = { _short(e["id"]) for e in contar_negacoes(entries) }
        print(f"  entries indexáveis={len(entries)} | negações Redis detectadas={len(negs_ids)}: {sorted(negs_ids)}")

        import numpy as np
        from edp.retrieval_hybrid import HybridRetriever, BM25

        def rotulo(e):
            s = _short(e.get("id"))
            if s in negs_ids: return "NEGACAO"
            if s in TRACKED:  return "CONTEUDO-REAL"
            return ""

        # (a/b) BM25 PURO — braço lexical isolado
        bm = BM25(); bm.fit([e["text"] for e in entries])
        ranking = bm.score(QUERY, top_k=len(entries))
        print(f"\n  RANKING BM25 PURO (query canônica) — top-12 de {len(ranking)} com score>0:")
        for pos, (i, s) in enumerate(ranking[:12], 1):
            e = entries[i]
            print(f"    {pos:>2}. bm25={s:7.3f} id={_short(e['id'])} [{rotulo(e):13}] {(e['text'] or '')[:64]!r}")
        rank_of = { _short(entries[i]["id"]): pos for pos, (i, _s) in enumerate(ranking, 1) }
        print(f"    posições: negações={ {n: rank_of.get(n) for n in sorted(negs_ids)} } | "
              f"alvo {TARGET}={rank_of.get(TARGET)} | demais={ {t: rank_of.get(t) for t in TRACKED} }")

        # (c) híbrido completo RRF
        from edp.embeddings import embed_one
        hr = HybridRetriever()
        hr.add([e["text"] for e in entries],
               np.array([e["embedding"] for e in entries], dtype=np.float32))
        res = hr.search(QUERY, embed_one(QUERY), top_k=10, min_score=0.0, method="rrf", mmr=False)
        print(f"\n  RANKING RRF (híbrido completo) — top-10:")
        for pos, i in enumerate(res.indices, 1):
            e = entries[i]
            print(f"    {pos:>2}. rrf={res.scores[pos-1]:.6f} bm25={res.bm25_scores[pos-1]:.3f} "
                  f"vec={res.vector_scores[pos-1]:.3f} id={_short(e['id'])} [{rotulo(e):13}] {(e['text'] or '')[:56]!r}")
        top5 = [_short(entries[i]["id"]) for i in res.indices[:5]]
        print(f"\n  top-5 RRF: {top5} | negações no top-5: {[t for t in top5 if t in negs_ids]} | "
              f"alvo presente: {TARGET in top5}")
        return 0
    finally:
        guard.restore(); guard.clean()


def main(argv=None):
    p = argparse.ArgumentParser(description="FASE 0 — checkpoints por chamada direta (zero conserto).")
    p.add_argument("--teste1", action="store_true")
    p.add_argument("--teste2", action="store_true")
    a = p.parse_args(argv)
    if not (a.teste1 or a.teste2):
        p.print_help(); return 2
    rc = 0
    if a.teste1: rc |= teste1()
    if a.teste2: rc |= teste2()
    print("\n  produção intocada (só a cópia em EDP_BASE_DIR); nenhum turno de chat; edp/ sem diffs.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
