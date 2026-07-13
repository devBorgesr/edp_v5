#!/usr/bin/env python3
"""
exp012_fase4_backfill_dryrun.py — exp012 Fase 4 (backfill): DRY-RUN, SÓ LEITURA.

Aplica write_provenance.classify() no ESTRATO A (R4 puro — negacao_textual OR
kw_continuidade; prov sem n_mem_prompt) sobre as entries episódicas/semânticas
JÁ EXISTENTES no store-alvo (o backlog sem carimbo — PR-6 ao vivo, 12/07/2026:
peso-piso e exclusão do híbrido não tocam esse backlog porque ele nunca passou
pelo write-path do exp012).

NÃO GRAVA NADA — só LISTA (id, query, features, key_assertion) o que
carimbaria como not_found. A passada REAL (gravar answer_class de fato) é um
script separado, só depois de validar esta lista contra gt_rotulacao.csv e
com OK explícito do pesquisador.

key_assertion é coletado (entry["cognitive_decisions"]["key_assertion"], já
materializado pelo extractor de cognitive_decisions — custo zero) só para
ANÁLISE de um possível 3º sinal em exp012-v3. NÃO participa da decisão desta
fase.

USO (servidor parado; aponte para CÓPIA — nunca produção):
  $env:EDP_BASE_DIR="C:\\edp_data_hybrid_test"
  python exp012_fase4_backfill_dryrun.py [session_id]
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from edp.write_provenance import classify, kw_continuidade, negacao_textual

QA = re.compile(r"^Q:\s*(.*?)\nA:\s*(.*)$", re.S)  # mesmo parse de extract_ground_truth.py


def die(msg):
    print(f"[ERRO] {msg}")
    sys.exit(2)


base = os.environ.get("EDP_BASE_DIR") or die("EDP_BASE_DIR não setado")
if os.path.basename(base.rstrip("/\\")).lower() == "edp_data":
    die("aponte para CÓPIA, não produção")
sid = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EDP_SESSION_ID", "default")

candidatas = []
vistas = 0
for scope in ("cognitive", "sprint"):
    sd = os.path.join(base, "sessions", f"{sid}_{scope}")
    for fname in ("episodic.json", "semantic.json"):
        p = os.path.join(sd, fname)
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            print(f"[AVISO] {p}: {e}")
            continue
        entries = d.get("entries", d) if isinstance(d, dict) else d
        for e in entries:
            if not isinstance(e, dict):
                continue
            vistas += 1
            m = QA.match(e.get("text") or "")
            if not m:
                continue
            q, a = m.group(1), m.group(2)
            cls = classify({}, q, a)  # prov={} -> n_mem_prompt AUSENTE -> estrato A (R4 puro)
            if cls != "not_found":
                continue
            candidatas.append({
                "id": e.get("id"),
                "scope": scope, "arquivo": fname,
                "query": q[:120],
                "negacao_textual": negacao_textual(a),
                "kw_continuidade": kw_continuidade(q),
                "key_assertion": (e.get("cognitive_decisions") or {}).get("key_assertion"),
                "ja_tem_answer_class": bool(e.get("answer_class")),
            })

print(f"store={base} sid={sid} | entries vistas={vistas} | candidatas (estrato A, R4 puro)={len(candidatas)}")
for c in candidatas:
    print(f"  {c['id']} [{c['scope']}/{c['arquivo']}] neg={c['negacao_textual']} kw={c['kw_continuidade']} "
          f"ja_classificado={c['ja_tem_answer_class']} key_assertion={c['key_assertion']!r}")
    print(f"      query: {c['query']}")
print(f"\n[DRY-RUN] nada foi gravado. {len(candidatas)} candidata(s) listada(s) acima.")
