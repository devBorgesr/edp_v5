#!/usr/bin/env python3
"""
measure_ss_dominance.py — Quantifica, sobre uma CÓPIA dos dados reais, o quanto
memórias source_type=="session_summary" dominam o retrieve do EDP.

Encarna a §2 + o anexo do DIAGNOSTICO_SESSION_SUMMARY.md como script executável.
SOMENTE LEITURA + MEDIÇÃO. Não altera código de produção. Opera exclusivamente
sob EDP_BASE_DIR — que DEVE apontar para uma CÓPIA, nunca para C:\\edp_data.

────────────────────────────────────────────────────────────────────────────────
COMO RODAR (Windows PowerShell, servidor EDP PARADO):

    robocopy C:\\edp_data C:\\edp_data_diag /E      # 1) protege a produção (cópia)
    $env:EDP_BASE_DIR = "C:\\edp_data_diag"          # 2) aponta o EDP para a cópia
    python measure_ss_dominance.py                    # 3) mede

    # opcionais:
    #   $env:EDP_SESSION_ID = "default"   (default: "default")
    #   $env:EDP_SCOPE      = "cognitive" (default: "cognitive")

Linux/macOS:
    cp -r /caminho/edp_data /caminho/edp_data_diag
    EDP_BASE_DIR=/caminho/edp_data_diag python3 measure_ss_dominance.py

────────────────────────────────────────────────────────────────────────────────
AS TRÊS COISAS DELICADAS (implementadas abaixo):

1. ISOLAMENTO CONTRA MUTAÇÃO. O retrieve REAL muta e salva
   (edp/memory.py:871-872 acessos++/ultimo_acesso; :879-880 self.save()). Rodar N
   queries em sequência acumularia acessos e distorceria o access_boost DA PRÓPRIA
   medição. Solução: tira um SNAPSHOT pristine do diretório da sessão no início e,
   ANTES DE CADA query, apaga o dir da sessão e restaura do snapshot. Cada query
   roda contra o estado original. No fim, restaura mais uma vez e confere que a
   cópia de trabalho == snapshot (sem divergência). A produção nunca é referenciada.

2. RETRIEVE REAL, não reimplementação. Instancia o MemoryStore real e chama
   memory.retrieve(query, top_k=10, min_score=0.0) — a mesma função do hot path
   (edp/memory.py:1612). O EDP descobre os dados via EDP_BASE_DIR.

3. MÉTRICAS EXATAS do diagnóstico, VAGAS vs ESPECÍFICAS em separado:
   - % médio do top-10 que é session_summary
   - % médio do top-5  que é session_summary
   - específicas: quantas com o alvo expulso do top-5; o rank do alvo
   - exemplos: top-5 real (id, source_type, rank_score; marca SS e alvo)
────────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from statistics import mean

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG — edite aqui (queries e alvos). O resto do script não precisa mudar.
# ══════════════════════════════════════════════════════════════════════════════

TOP_K     = 10      # pool amplo, para ver as posições (§2 do diagnóstico)
MIN_SCORE = 0.0     # sem corte por threshold — mede ranking puro

# ── Queries VAGAS de continuação (SEM alvo específico) ─────────────────────────
# São aberturas de conversa que não casam com nenhuma memória concreta — é onde,
# segundo o diagnóstico, os session_summary tomam os primeiros lugares.
VAGUE_QUERIES = [
    "vamos continuar nossa conversa",
    "continuando o que falávamos",
    "o que a gente tinha concluído mesmo?",
    "me lembra o que discutimos",
    "voltando ao que estávamos vendo",
    "sobre o que conversamos até agora",
]

# ── Queries ESPECÍFICAS (COM alvo conhecido) ───────────────────────────────────
# Cada item: {"query": <texto>, e UM de: "id": <id exato da memória-alvo>
#                                    ou   "needle": <substring do texto do alvo>}
#
# COMO AJUSTAR AOS SEUS DADOS REAIS:
#   1. Abra  <EDP_BASE_DIR>/sessions/<sessao>_<scope>/episodic.json (e semantic.json).
#   2. Ache a memória do tópico que você quer testar (ex.: a do Redis).
#   3. Copie o "id" dela para "id", OU deixe um "needle" (trecho único do texto,
#      ex.: "redis") que identifique aquela memória NÃO-summary.
#   O harness resolve "needle" -> id(s) lendo o snapshot pristine. Se não resolver
#   (0 ou match só em session_summary), marca "alvo não resolvido" e ainda mede a
#   fração de session_summary no top-K (que não depende de conhecer o alvo).
#
# Os exemplos abaixo são placeholders realistas — troque pelos tópicos DO SEU store.
SPECIFIC_QUERIES = [
    {"query": "Redis e Memcached para cache de sessões web", "needle": "redis"},
    {"query": "transformers em LLMs e mecanismo de atenção",  "needle": "transformer"},
    {"query": "FAISS e busca vetorial em alta dimensão",       "needle": "faiss"},
    {"query": "embeddings vetoriais e semântica de frases",    "needle": "embedding"},
    {"query": "RAG e alucinações em modelos de linguagem",     "needle": "rag"},
    {"query": "Python e desempenho em tempo real",             "needle": "python"},
]

N_EXAMPLES = 3   # quantas queries de cada tipo imprimir o top-5 detalhado

SS = "session_summary"


# ══════════════════════════════════════════════════════════════════════════════
# Infra — resolução de paths, snapshot/restore, segurança
# ══════════════════════════════════════════════════════════════════════════════

def _die(msg: str, code: int = 2):
    print(f"\n[ERRO] {msg}\n", file=sys.stderr)
    sys.exit(code)


def _resolve_paths():
    """EDP_BASE_DIR deve estar setado e apontar para uma CÓPIA. Recusa rodar se
    parecer a produção (basename == 'edp_data') a menos que ALLOW_PROD=1."""
    base = os.environ.get("EDP_BASE_DIR")
    if not base:
        _die(
            "EDP_BASE_DIR não está setado. Aponte-o para uma CÓPIA dos dados:\n"
            "    robocopy C:\\edp_data C:\\edp_data_diag /E\n"
            "    $env:EDP_BASE_DIR = \"C:\\edp_data_diag\"\n"
            "NUNCA rode sobre C:\\edp_data (produção)."
        )
    base = os.path.abspath(base)
    if not os.path.isdir(base):
        _die(f"EDP_BASE_DIR não é um diretório existente: {base}")

    # Guarda de segurança: não deixa apontar para a produção 'edp_data' por engano.
    if os.path.basename(base.rstrip("/\\")).lower() == "edp_data" and \
       os.environ.get("ALLOW_PROD", "0").lower() not in ("1", "true", "yes", "on"):
        _die(
            f"EDP_BASE_DIR aponta para '{base}', que parece a PRODUÇÃO (edp_data).\n"
            "Este harness só roda sobre CÓPIA. Faça a cópia (robocopy) e aponte para ela.\n"
            "(Se você tem CERTEZA que já é uma cópia, defina ALLOW_PROD=1 — por sua conta.)"
        )

    session = os.environ.get("EDP_SESSION_ID", "default")
    scope   = os.environ.get("EDP_SCOPE", "cognitive")
    sess_dir = os.path.join(base, "sessions", f"{session}_{scope}")
    if not os.path.isdir(sess_dir):
        _die(
            f"Diretório da sessão não existe: {sess_dir}\n"
            f"Verifique EDP_SESSION_ID (='{session}') e EDP_SCOPE (='{scope}'), e que a\n"
            f"cópia contém sessions/{session}_{scope}/."
        )
    return base, session, scope, sess_dir


def _dir_fingerprint(path: str) -> str:
    """Hash estável do conteúdo de um diretório (nomes + bytes), p/ provar no-divergência."""
    h = hashlib.sha256()
    for root, _dirs, files in sorted(os.walk(path)):
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, path)
            h.update(rel.encode("utf-8"))
            try:
                with open(fp, "rb") as f:
                    h.update(f.read())
            except Exception:
                pass
    return h.hexdigest()[:16]


class SessionGuard:
    """Snapshot pristine + restore-antes-de-cada-query. Nunca toca fora de sess_dir."""

    def __init__(self, sess_dir: str):
        self.sess_dir = sess_dir
        self._tmp = tempfile.mkdtemp(prefix="ss_dominance_snapshot_")
        self.snapshot = os.path.join(self._tmp, "pristine")
        shutil.copytree(sess_dir, self.snapshot)
        self.snapshot_hash = _dir_fingerprint(self.snapshot)

    def restore(self):
        """Apaga o dir da sessão e restaura do snapshot pristine."""
        if os.path.isdir(self.sess_dir):
            shutil.rmtree(self.sess_dir)
        shutil.copytree(self.snapshot, self.sess_dir)

    def verify_no_divergence(self) -> bool:
        """Após restaurar, confirma que a cópia de trabalho == snapshot."""
        return _dir_fingerprint(self.sess_dir) == self.snapshot_hash

    def cleanup(self):
        shutil.rmtree(self._tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════════
# Leitura do store (do snapshot pristine) — inventário + resolução de alvos
# ══════════════════════════════════════════════════════════════════════════════

def _load_entries(path: str) -> list:
    if not os.path.exists(path):
        return []
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("entries", [])
    return data if isinstance(data, list) else []


def read_store(snapshot_dir: str) -> list:
    """Todas as entries do scope (episodic + semantic) a partir do snapshot."""
    entries = []
    for name in ("episodic.json", "semantic.json"):
        entries.extend(_load_entries(os.path.join(snapshot_dir, name)))
    return [e for e in entries if isinstance(e, dict)]


def inventory(entries: list) -> dict:
    from collections import Counter
    st = Counter(e.get("source_type", "<none>") for e in entries)
    ss = [e for e in entries if e.get("source_type") == SS]
    ss_acc = [e.get("acessos", 0) for e in ss]
    norm_acc = [e.get("acessos", 0) for e in entries if e.get("source_type") != SS]
    return {
        "total": len(entries),
        "by_source_type": dict(st),
        "n_session_summary": len(ss),
        "ss_acessos": sorted(ss_acc, reverse=True),
        "ss_acessos_mean": (mean(ss_acc) if ss_acc else 0.0),
        "ss_acessos_max": (max(ss_acc) if ss_acc else 0),
        "normal_acessos_mean": (mean(norm_acc) if norm_acc else 0.0),
        "normal_acessos_max": (max(norm_acc) if norm_acc else 0),
    }


def resolve_targets(entries: list, spec: dict) -> set:
    """Resolve o alvo de uma query específica -> conjunto de ids NÃO-summary.
    Aceita "id" (exato) ou "needle" (substring do texto). Retorna set (vazio se
    não resolveu)."""
    if spec.get("id"):
        # confirma que o id existe e não é summary
        for e in entries:
            if e.get("id") == spec["id"]:
                return set() if e.get("source_type") == SS else {spec["id"]}
        return set()
    needle = (spec.get("needle") or "").lower().strip()
    if not needle:
        return set()
    ids = set()
    for e in entries:
        if e.get("source_type") == SS:
            continue  # o alvo específico é uma memória concreta, não um summary
        if needle in (e.get("text") or "").lower():
            eid = e.get("id")
            if eid:
                ids.add(eid)
    return ids


# ══════════════════════════════════════════════════════════════════════════════
# Medição — roda o RETRIEVE REAL, uma query por vez, com restore antes de cada
# ══════════════════════════════════════════════════════════════════════════════

def _is_ss(r: dict) -> bool:
    return r.get("source_type") == SS


def run_query(MemoryStore, session_id: str, scope: str, query: str) -> list:
    """Instancia o MemoryStore REAL e chama o retrieve REAL. O guard já restaurou
    o dir; aqui carregamos fresh e medimos 1 query."""
    mem = MemoryStore(session_id)
    if scope != "cognitive":
        try:
            mem.set_scope(scope)
        except Exception:
            pass
    return mem.retrieve(query, top_k=TOP_K, min_score=MIN_SCORE)  # edp/memory.py:1612


def measure(guard: SessionGuard, MemoryStore, session_id: str, scope: str,
            queries: list, targets_per_query=None) -> list:
    """Roda cada query isolada (restore antes) e coleta métricas por query."""
    out = []
    for i, q in enumerate(queries):
        guard.restore()  # ← estado original ANTES de cada query (isolamento §1)
        try:
            res = run_query(MemoryStore, session_id, scope, q)
        except Exception as e:
            out.append({"query": q, "error": f"{type(e).__name__}: {e}"})
            continue
        top10 = res[:10]
        top5 = res[:5]
        n10 = max(len(top10), 1)
        n5 = max(len(top5), 1)
        rec = {
            "query": q,
            "n_results": len(res),
            "ss_frac_top10": sum(_is_ss(r) for r in top10) / n10,
            "ss_frac_top5": sum(_is_ss(r) for r in top5) / n5,
            "ss_count_top10": sum(_is_ss(r) for r in top10),
            "ss_count_top5": sum(_is_ss(r) for r in top5),
            "results": res,
        }
        if targets_per_query is not None:
            tids = targets_per_query[i]
            rec["target_resolved"] = bool(tids)
            rank = None
            if tids:
                for pos, r in enumerate(res, 1):
                    if r.get("id") in tids:
                        rank = pos
                        break
            rec["target_rank"] = rank
            rec["target_in_top5"] = (rank is not None and rank <= 5)
            rec["target_expelled"] = bool(tids) and not rec["target_in_top5"]
            # quantos session_summary estão ACIMA do alvo (textura do "empurrão")
            rec["ss_above_target"] = (
                sum(1 for r in (res[: rank - 1] if rank else res) if _is_ss(r))
            )
        out.append(rec)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Relatório
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_pct(x: float) -> str:
    return f"{x*100:.1f}%"


def print_examples(records: list, tipo: str, n: int):
    print(f"\n  ── Exemplos de top-5 ({tipo}) — o número não é o achado, os exemplos são:")
    shown = 0
    for rec in records:
        if rec.get("error") or shown >= n:
            continue
        shown += 1
        tids = None
        print(f"\n  Q: {rec['query']}")
        extra = ""
        if "target_rank" in rec:
            if not rec["target_resolved"]:
                extra = "  [alvo não resolvido]"
            else:
                extra = (f"  alvo rank={rec['target_rank']} "
                         f"{'(no top5)' if rec['target_in_top5'] else '(EXPULSO do top5)'}")
        print(f"     top-5 SS={rec['ss_count_top5']}/5{extra}")
        for pos, r in enumerate(rec["results"][:5], 1):
            tag = ""
            if _is_ss(r):
                tag = "  <<SESSION_SUMMARY"
            elif rec.get("target_resolved") and r.get("id") in _target_ids_of(rec):
                tag = "  <<ALVO"
            sc = r.get("ranking_score")
            sc_s = f"{sc:.3f}" if isinstance(sc, (int, float)) else str(sc)
            txt = (r.get("text") or "").replace("\n", " ")[:58]
            print(f"       {pos:>2}. score={sc_s} [{str(r.get('source_type')):16}] "
                  f"id={str(r.get('id'))[:12]:12} | {txt}{tag}")


# guarda os target ids no próprio record para o print (evita recomputar)
def _target_ids_of(rec):
    return rec.get("_target_ids", set())


def report(inv: dict, vague_recs: list, spec_recs: list, guard: SessionGuard,
           base: str, session: str, scope: str):
    line = "=" * 92
    print("\n" + line)
    print("MEDIÇÃO — dominância de session_summary no retrieve (retrieve REAL, sobre CÓPIA)")
    print(line)
    print(f"  EDP_BASE_DIR : {base}")
    print(f"  sessão/scope : {session}_{scope}")
    print(f"  retrieve     : memory.retrieve(query, top_k={TOP_K}, min_score={MIN_SCORE})  [edp/memory.py:1612]")

    # ── Inventário ──
    print(f"\n  INVENTÁRIO (do snapshot pristine):")
    print(f"    entries totais        : {inv['total']}")
    print(f"    session_summary       : {inv['n_session_summary']}")
    print(f"    por source_type       : {inv['by_source_type']}")
    if inv["n_session_summary"]:
        print(f"    acessos session_summary: {inv['ss_acessos']} "
              f"(média={inv['ss_acessos_mean']:.1f}, máx={inv['ss_acessos_max']})")
    print(f"    acessos normais        : média={inv['normal_acessos_mean']:.1f}, "
          f"máx={inv['normal_acessos_max']}")

    # ── Tabela VAGAS ──
    ok_vague = [r for r in vague_recs if not r.get("error")]
    print(f"\n  QUERIES VAGAS DE CONTINUAÇÃO (n={len(ok_vague)}) — sem alvo específico:")
    print(f"    {'% top-10 = session_summary (médio)':46}: "
          f"{_fmt_pct(mean(r['ss_frac_top10'] for r in ok_vague)) if ok_vague else '—'}")
    print(f"    {'% top-5  = session_summary (médio)':46}: "
          f"{_fmt_pct(mean(r['ss_frac_top5'] for r in ok_vague)) if ok_vague else '—'}")

    # ── Tabela ESPECÍFICAS ──
    ok_spec = [r for r in spec_recs if not r.get("error")]
    resolved = [r for r in ok_spec if r.get("target_resolved")]
    expelled = [r for r in resolved if r.get("target_expelled")]
    print(f"\n  QUERIES ESPECÍFICAS (n={len(ok_spec)}; alvo resolvido em {len(resolved)}):")
    print(f"    {'% top-10 = session_summary (médio)':46}: "
          f"{_fmt_pct(mean(r['ss_frac_top10'] for r in ok_spec)) if ok_spec else '—'}")
    print(f"    {'% top-5  = session_summary (médio)':46}: "
          f"{_fmt_pct(mean(r['ss_frac_top5'] for r in ok_spec)) if ok_spec else '—'}")
    if resolved:
        print(f"    {'alvo específico EXPULSO do top-5':46}: {len(expelled)}/{len(resolved)}")
        print(f"    {'ranks do alvo (None = fora do top-10)':46}: "
              f"{[r.get('target_rank') for r in resolved]}")
    unresolved = [r for r in ok_spec if not r.get("target_resolved")]
    if unresolved:
        print(f"    [aviso] {len(unresolved)} específica(s) com alvo NÃO resolvido "
              f"(ajuste id/needle p/ seu store): {[r['query'][:30] for r in unresolved]}")

    # ── Exemplos ──
    print_examples(vague_recs, "VAGAS", N_EXAMPLES)
    print_examples(spec_recs, "ESPECÍFICAS", N_EXAMPLES)

    # ── Isolamento / no-divergência ──
    guard.restore()
    no_div = guard.verify_no_divergence()
    print(f"\n  ISOLAMENTO:")
    print(f"    cópia de trabalho restaurada == snapshot pristine? {no_div} "
          f"(hash={guard.snapshot_hash})")
    print(f"    produção (C:\\edp_data) NUNCA foi referenciada — tudo sob EDP_BASE_DIR acima.")

    # ── Resumo de uma linha ──
    v5 = _fmt_pct(mean(r['ss_frac_top5'] for r in ok_vague)) if ok_vague else "—"
    s5 = _fmt_pct(mean(r['ss_frac_top5'] for r in ok_spec)) if ok_spec else "—"
    print("\n" + line)
    print(f"RESUMO: session_summary ocupa {v5} do top-5 em queries VAGAS vs {s5} em "
          f"ESPECÍFICAS; alvo expulso em {len(expelled)}/{len(resolved)} específicas.")
    print(line)


# ══════════════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    base, session, scope, sess_dir = _resolve_paths()

    # Import do EDP só APÓS validar EDP_BASE_DIR (config lê a env no import).
    try:
        from edp.memory import MemoryStore
    except Exception as e:
        _die(f"não consegui importar edp.memory (rode a partir da raiz do repo EDP): {e}")

    guard = SessionGuard(sess_dir)
    try:
        entries = read_store(guard.snapshot)
        inv = inventory(entries)

        # resolve alvos das específicas a partir do snapshot pristine
        spec_target_ids = [resolve_targets(entries, s) for s in SPECIFIC_QUERIES]

        vague_recs = measure(guard, MemoryStore, session, scope, VAGUE_QUERIES)
        spec_recs = measure(
            guard, MemoryStore, session, scope,
            [s["query"] for s in SPECIFIC_QUERIES],
            targets_per_query=spec_target_ids,
        )
        # anexa os ids resolvidos aos records (para marcar o alvo nos exemplos)
        for rec, tids in zip(spec_recs, spec_target_ids):
            rec["_target_ids"] = tids

        report(inv, vague_recs, spec_recs, guard, base, session, scope)
        return 0
    finally:
        # deixa a cópia de trabalho pristine e limpa o snapshot temporário
        try:
            guard.restore()
        except Exception:
            pass
        guard.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
