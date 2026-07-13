#!/usr/bin/env python3
"""
exp012_fase4_backfill_dryrun.py — exp012 Fase 4 (backfill): DRY-RUN, SÓ LEITURA.

Aplica write_provenance.classify() no ESTRATO A (R4 puro — negacao_textual OR
kw_continuidade; prov sem n_mem_prompt) sobre as entries episódicas/semânticas
JÁ EXISTENTES no store-alvo (o backlog sem carimbo — PR-6 ao vivo, 12/07/2026:
peso-piso e exclusão do híbrido não tocam esse backlog porque ele nunca passou
pelo write-path do exp012).

NÃO GRAVA NADA — só LISTA (id, query, features, key_assertion) o que
carimbaria como not_found. `find_candidatas()` é IMPORTADA por
exp012_fase4_backfill_apply.py (passada real) — mesma seleção, mesmo estrato
A, nenhuma regra nova, nenhum id hardcoded.

key_assertion é coletado (entry["cognitive_decisions"]["key_assertion"], já
materializado pelo extractor de cognitive_decisions — custo zero) só para
ANÁLISE de um possível 3º sinal em exp012-v3. NÃO participa da decisão desta
fase.

override_estrato_b: True quando a entry JÁ TEM ctx_provenance com
n_mem_prompt>0 (proveniência real que faria o write-path normal cair no
estrato B e NÃO quarentenar) — o backfill aplica estrato A mesmo assim
(autorização explícita do pesquisador p/ as negações do dia, 12/07). Derivado
genericamente do carimbo real de cada entry, não de uma lista de ids.

USO (servidor parado; aponte para CÓPIA — nunca produção):
  $env:EDP_BASE_DIR="C:\\edp_data_hybrid_test"
  python exp012_fase4_backfill_dryrun.py [session_id]
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from edp.write_provenance import classify, kw_continuidade, negacao_textual
# Nota: NÃO importa edp.memory — esse módulo puxa sklearn/torch (stack pesada
# do runtime), inadequado para um script offline standalone. Leitura própria,
# mesmo padrão de extract_ground_truth.py/exp012_calibracao.py (json puro).

QA = re.compile(r"^Q:\s*(.*?)\nA:\s*(.*)$", re.S)  # mesmo parse de extract_ground_truth.py

# 4 camadas que o mecanismo write-path/quarentena cobre (memory.py: peso-piso
# só episodic; exclusão do híbrido cobre as duas — ver exp012_fase4_backfill_apply.py)
LAYERS = (
    ("cognitive", "episodic.json"), ("cognitive", "semantic.json"),
    ("sprint", "episodic.json"), ("sprint", "semantic.json"),
)


def die(msg):
    print(f"[ERRO] {msg}")
    sys.exit(2)


def _load_entries(base, sid, scope, fname):
    p = os.path.join(base, "sessions", f"{sid}_{scope}", fname)
    if not os.path.exists(p):
        return None, p
    try:
        data = json.load(open(p, encoding="utf-8"))
    except Exception as e:
        print(f"[AVISO] {p}: {e}")
        return None, p
    entries = data.get("entries", data) if isinstance(data, dict) else data
    return entries, p


def find_candidatas(base: str, sid: str):
    """Varre as 4 camadas (LAYERS), retorna (candidatas, entries_vistas).
    candidatas: lista de dicts — id, scope, arquivo, path, query, features,
    key_assertion, ja_tem_answer_class, override_estrato_b."""
    candidatas = []
    vistas = 0
    for scope, fname in LAYERS:
        entries, p = _load_entries(base, sid, scope, fname)
        if entries is None:
            continue
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
            prov = e.get("ctx_provenance")
            n_mem = prov.get("n_mem_prompt") if isinstance(prov, dict) else None
            candidatas.append({
                "id": e.get("id"),
                "scope": scope, "arquivo": fname, "path": p,
                "query": q[:120],
                "negacao_textual": negacao_textual(a),
                "kw_continuidade": kw_continuidade(q),
                "key_assertion": (e.get("cognitive_decisions") or {}).get("key_assertion"),
                "ja_tem_answer_class": bool(e.get("answer_class")),
                "override_estrato_b": bool(n_mem is not None and n_mem > 0),
            })
    return candidatas, vistas


def main():
    base = os.environ.get("EDP_BASE_DIR") or die("EDP_BASE_DIR não setado")
    if os.path.basename(base.rstrip("/\\")).lower() == "edp_data":
        die("aponte para CÓPIA, não produção")
    sid = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EDP_SESSION_ID", "default")

    candidatas, vistas = find_candidatas(base, sid)
    print(f"store={base} sid={sid} | entries vistas={vistas} | candidatas (estrato A, R4 puro)={len(candidatas)}")
    for c in candidatas:
        print(f"  {c['id']} [{c['scope']}/{c['arquivo']}] neg={c['negacao_textual']} kw={c['kw_continuidade']} "
              f"ja_classificado={c['ja_tem_answer_class']} override_estrato_b={c['override_estrato_b']} "
              f"key_assertion={c['key_assertion']!r}")
        print(f"      query: {c['query']}")
    print(f"\n[DRY-RUN] nada foi gravado. {len(candidatas)} candidata(s) listada(s) acima.")


if __name__ == "__main__":
    main()
