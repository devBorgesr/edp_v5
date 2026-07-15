#!/usr/bin/env python3
"""
exp016_dryrun.py — exp016 Etapa 0 (3ª classe de veneno: desqualificação
auto-referente). DRY-RUN, SÓ LEITURA.

Estende o mecanismo provado do exp012 (answer_class → peso-piso + exclusão
do híbrido, edp/memory.py:717-719 e :1731-1745) para uma 3ª classe de
"veneno": desqualificação auto-referente do modelo sobre suas próprias
memórias ("eu inventei", "foi fabricada por mim"). Ver
RELATORIO_ETAPA0_EXP016.md para P1-P6 completos.

NÃO GRAVA NADA — só LISTA (id, camada, query, features DISQ, key_assertion,
ja_tem_answer_class). `find_candidatas()` é IMPORTÁVEL por um eventual script
de passada real (mesmo padrão de exp012_fase4_backfill_apply.py importando
exp012_fase4_backfill_dryrun.find_candidatas).

Regra DISQ v1 (P3, CONGELADA em 15/07/2026 ANTES de qualquer dado — ver
RELATORIO_ETAPA0_EXP016.md). Regex sobre o texto da RESPOSTA. Exemplares-
fonte: episodic.json, backup sessions_backup_exp013, ~L61871 e ~L62297
(13/07 00h52-56). [ãa]/[óo]/[íi]/[êe] no mesmo padrão de NEG
(edp/write_provenance.py:31) — robustez a mangling cp1252 (ver commit
df0e3fa). SEM colisão verificada com NEG (nenhum padrão DISQ usa os verbos
encontro/tenho/há/localizo).

key_assertion é coletado (entry["cognitive_decisions"]["key_assertion"], já
materializado pelo extractor de cognitive_decisions quando presente — custo
zero) só para ANÁLISE de possível sinal auxiliar, mesmo padrão da Fase 4 do
exp012 (P3 desta etapa). NÃO participa da decisão desta fase.

PREDIÇÕES pré-registradas (P4 — ver relatório): DEVE pegar as 2
desqualificações de 13/07 (todas as camadas onde existirem). NÃO DEVE pegar
os 3 conteúdos Redis genuínos, os 10 LEGITIMO_META, o summary tóxico
31162822 (1ª geração — confabulação, não desqualificação; se a regex pegar
é achado a reportar, não a esconder), as negações de 1ª classe já
carimbadas pela regra R4/NEG do exp012, ou correções/retratações humanas.

USO (servidor parado; aponte para CÓPIA — nunca produção):
  $env:EDP_BASE_DIR="C:\\edp_data_exp016"
  python exp016_dryrun.py [session_id]
"""
import json, os, re, sys

QA = re.compile(r"^Q:\s*(.*?)\nA:\s*(.*)$", re.S)  # mesmo parse de extract_ground_truth.py

# ── P3: regra DISQ v1, congelada ANTES do dry-run ──────────────────────────
DISQ_PATTERNS = [
    re.compile(r"fabricad[ao]s?\s+por\s+mim", re.I),
    re.compile(r"eu\s+(a\s+|as\s+)?inventei", re.I),
    re.compile(
        r"n[ãa]o\s+(é|são|e|sao)\s+(uma\s+)?"
        r"mem[óo]ri(a|as)\s+(sua|genu[íi]na|aut[êe]ntica|real)",
        re.I,
    ),
    re.compile(r"n[ãa]o\s+corresponde\w*\s+a\s+nenhuma\s+pergunta\s+sua", re.I),
]


def disq_features(resposta: str) -> dict:
    """{padrao_idx: bool} — NÃO decide sozinho, só coleta (mesma disciplina da Fase 4)."""
    return {i: bool(p.search(resposta or "")) for i, p in enumerate(DISQ_PATTERNS)}


def is_disq(resposta: str) -> bool:
    """v1: qualquer padrão bate → candidata. Ajuste de threshold é decisão futura."""
    return any(v for v in disq_features(resposta).values())


# 4 camadas que o mecanismo write-path/quarentena do exp012 cobre (memory.py:
# peso-piso só episodic; exclusão do híbrido cobre as duas — ver P1 do relatório)
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
    candidatas: lista de dicts — id, scope, arquivo, path, query, features_disq,
    key_assertion, ja_tem_answer_class, ja_tem_cognitive_decisions."""
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
            if not is_disq(a):
                continue
            cog_dec = e.get("cognitive_decisions") or {}
            candidatas.append({
                "id": e.get("id"),
                "scope": scope, "arquivo": fname, "path": p,
                "query": q[:120],
                "features_disq": disq_features(a),
                "key_assertion": cog_dec.get("key_assertion"),
                "ja_tem_answer_class": bool(e.get("answer_class")),
                "ja_tem_cognitive_decisions": bool(e.get("cognitive_decisions")),
            })
    return candidatas, vistas


def main():
    base = os.environ.get("EDP_BASE_DIR") or die("EDP_BASE_DIR não setado")
    if os.path.basename(base.rstrip("/\\")).lower() == "edp_data":
        die("aponte para CÓPIA, não produção")
    sid = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EDP_SESSION_ID", "default")

    candidatas, vistas = find_candidatas(base, sid)
    print(f"store={base} sid={sid} | entries vistas={vistas} | candidatas (DISQ v1)={len(candidatas)}")
    for c in candidatas:
        padroes_bateram = [i for i, v in c["features_disq"].items() if v]
        print(f"  {c['id']} [{c['scope']}/{c['arquivo']}] padroes={padroes_bateram} "
              f"ja_tem_answer_class={c['ja_tem_answer_class']} "
              f"ja_tem_cognitive_decisions={c['ja_tem_cognitive_decisions']} "
              f"key_assertion={c['key_assertion']!r}")
        print(f"      query: {c['query']}")
    print(f"\n[DRY-RUN] nada foi gravado. {len(candidatas)} candidata(s) listada(s) acima.")


if __name__ == "__main__":
    main()
