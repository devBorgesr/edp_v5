"""
edp.lab.exp009 — Encarnação em CODIGO do Experimento 009 (despriorizar session_summary).

Espelha preregistro_experimento_009.md. CONGELADO apos o primeiro disparo real.

Categoria RETRIEVAL-QUALITY (inaugurada pelo exp008): mede o que o retrieve
SELECIONA, antes do modelo. O LLM nunca e chamado. Custo de API zero.

Pergunta: remover os privilegios de nascenca das session_summary (prioridade alta
+ verified) devolve as memorias de conteudo ao top-5 sem quebrar o caso legitimo
(query que pede resumo)?

Mecânica (§7 do pre-registro; infra herdada do measure_ss_dominance.py):
  - EDP_BASE_DIR aponta para uma COPIA da producao (guarda recusa 'edp_data').
  - snapshot pristine do dir da sessao + RESTORE antes de CADA query — o retrieve
    REAL muta e salva (memory.py:871-872, 879-880); cada query roda no estado
    original. Verificacao final de no-divergencia (hash == snapshot).
  - manipulacao de cada condicao e feita nos JSONs do CLONE (apos restore, antes
    de instanciar o MemoryStore). Nunca no snapshot, nunca na producao, nunca em
    edp/*.py.
  - retrieve REAL: MemoryStore.retrieve(query, top_k=10, min_score=0.0)
    (edp/memory.py:1612). Embedding singleton carrega UMA vez no processo.
  - Trava EDP_LAB_ARMED para o disparo real; --dry-run e a prova-no-espelho.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from statistics import mean
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger("edp.lab.exp009")

# ── Constantes CONGELADAS (§8 do pre-registro) ────────────────────────────────
EXPERIMENTO = "009"
TOP_K       = 10
MIN_SCORE   = 0.0
SS          = "session_summary"
MODELO_LABEL = "retrieval::no-llm"

COND_BASELINE   = "baseline"
COND_GRAVADOR   = "trat_gravador"
COND_TRIVIAL    = "trat_trivial"
COND_COMBINADO  = "trat_combinado"
COND_SRCW       = "trat_gravador_srcw"   # §EXPLORATORIO — fora do confirmatorio

CONDICOES = [
    {"rotulo": COND_BASELINE,  "papel": "entries como no snapshot",              "tipo": "baseline"},
    {"rotulo": COND_GRAVADOR,  "papel": "SS: prioridade=media + hypothesis",      "tipo": "tratamento"},
    {"rotulo": COND_TRIVIAL,   "papel": "SS triviais fora do indice",             "tipo": "tratamento"},
    {"rotulo": COND_COMBINADO, "papel": "gravador + trivial",                     "tipo": "tratamento"},
    {"rotulo": COND_SRCW,      "papel": "EXPLORATORIO: gravador + src_weight 1.0", "tipo": "exploratorio"},
]

# Limiar H1 (§6) — congelado
H1_SS_TOP5_VAGAS_MAX   = 0.40   # %SS top-5 vagas deve cair ABAIXO disto
H1_REDIS_MIN_FRAC      = 2/3    # Redis no top-5 em >= 2/3 das queries 4b
H1_GUARDA_MIN_QUERIES  = 1      # >=1 query de resumo com >=1 SS no top-5

# ── Dataset CONGELADO (§4) ────────────────────────────────────────────────────

VAGUE_QUERIES = [
    "vamos continuar nossa conversa",
    "continuando o que falávamos",
    "o que a gente tinha concluído mesmo?",
    "me lembra o que discutimos",
    "voltando ao que estávamos vendo",
    "sobre o que conversamos até agora",
]

# 4b — Redis: alvo = QUALQUER um destes ids (memorias de conteudo ja validadas)
REDIS_TARGET_IDS = {
    "0c78fa08-8a51-4a04-ad15-2b23d0800a0b",
    "a5ef2402-c1c0-4404-93c1-5b23bf8e2a3e",
    "4c57ed7a-c275-4155-93eb-e1efa5a164d5",
}
REDIS_QUERIES = [
    "vamos continuar a conversa sobre Redis e Memcached",
    "me lembra o que a gente concluiu sobre cache de sessões web com Redis",
    "voltando ao assunto do Redis para sessões web",
]

# 4c — outras especificas: needle resolve para memorias NAO-SS (validar no dry-run;
# se ambiguo/0-match, o pesquisador troca por {"id": "..."} ANTES de armar).
SPECIFIC_QUERIES = [
    {"query": "continuando nossa conversa sobre transformers e atenção em LLMs", "needle": "transformer"},
    {"query": "me lembra o que vimos sobre FAISS e busca vetorial",              "needle": "faiss"},
    {"query": "voltando ao que discutimos sobre embeddings de frases",           "needle": "embedding"},
    {"query": "sobre o RAG e as alucinações que a gente discutiu",               "needle": "rag"},
    {"query": "continuando o papo sobre desempenho de Python em tempo real",     "needle": "python"},
    {"query": "me lembra da nossa discussão sobre memória episódica do EDP",     "needle": "episódic"},
]

# 4d — GUARDA (caso legitimo): SS no top-5 aqui e SUCESSO
GUARD_QUERIES = [
    "me dá um resumo do que consolidamos",
    "o que ficou registrado como resumo da sessão passada?",
    "qual foi o resumo das nossas últimas sessões?",
]

# ── Regra de "trivial" (§3a) — CONGELADA ─────────────────────────────────────
_PREFIX_RE = re.compile(r"^\s*\[session_summary\]\s*", re.IGNORECASE)


def useful_text(text: str) -> str:
    return re.sub(r"\s+", " ", _PREFIX_RE.sub("", text or "")).strip()


def is_trivial_ss(entry: dict) -> bool:
    """SS trivial: <80 chars uteis OU comeca com 'nada' OU 'primeira mensagem da
    conversa'. So se aplica a entries session_summary."""
    if entry.get("source_type") != SS:
        return False
    t = useful_text(entry.get("text") or "")
    low = t.lower()
    return (
        len(t) < 80
        or low.startswith("nada")
        or "primeira mensagem da conversa" in low
    )


# ══════════════════════════════════════════════════════════════════════════════
# Infra de isolamento (herdada do measure_ss_dominance.py)
# ══════════════════════════════════════════════════════════════════════════════

def _die(msg: str, code: int = 2):
    print(f"\n[ERRO] {msg}\n", file=sys.stderr)
    sys.exit(code)


def resolve_paths() -> Tuple[str, str, str, str]:
    base = os.environ.get("EDP_BASE_DIR")
    if not base:
        _die(
            "EDP_BASE_DIR não está setado. Aponte para uma CÓPIA da produção:\n"
            "    robocopy C:\\edp_data C:\\edp_data_exp009 /E\n"
            "    $env:EDP_BASE_DIR = \"C:\\edp_data_exp009\"\n"
            "NUNCA rode sobre C:\\edp_data."
        )
    base = os.path.abspath(base)
    if not os.path.isdir(base):
        _die(f"EDP_BASE_DIR não é um diretório existente: {base}")
    if os.path.basename(base.rstrip("/\\")).lower() == "edp_data" and \
       os.environ.get("ALLOW_PROD", "0").lower() not in ("1", "true", "yes", "on"):
        _die(
            f"EDP_BASE_DIR aponta para '{base}' — parece a PRODUÇÃO. Este experimento "
            "só roda sobre CÓPIA. (Se tem CERTEZA que é cópia, ALLOW_PROD=1.)"
        )
    session = os.environ.get("EDP_SESSION_ID", "default")
    scope   = os.environ.get("EDP_SCOPE", "cognitive")
    sess_dir = os.path.join(base, "sessions", f"{session}_{scope}")
    if not os.path.isdir(sess_dir):
        _die(f"Diretório da sessão não existe: {sess_dir}")
    return base, session, scope, sess_dir


def _dir_fingerprint(path: str) -> str:
    h = hashlib.sha256()
    for root, _d, files in sorted(os.walk(path)):
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            h.update(os.path.relpath(fp, path).encode("utf-8"))
            try:
                with open(fp, "rb") as f:
                    h.update(f.read())
            except Exception:
                pass
    return h.hexdigest()[:16]


class SessionGuard:
    """Snapshot pristine + restore-antes-de-cada-query + no-divergencia final."""

    def __init__(self, sess_dir: str):
        self.sess_dir = sess_dir
        self._tmp = tempfile.mkdtemp(prefix="exp009_snapshot_")
        self.snapshot = os.path.join(self._tmp, "pristine")
        shutil.copytree(sess_dir, self.snapshot)
        self.snapshot_hash = _dir_fingerprint(self.snapshot)

    def restore(self):
        if os.path.isdir(self.sess_dir):
            shutil.rmtree(self.sess_dir)
        shutil.copytree(self.snapshot, self.sess_dir)

    def verify_no_divergence(self) -> bool:
        return _dir_fingerprint(self.sess_dir) == self.snapshot_hash

    def cleanup(self):
        shutil.rmtree(self._tmp, ignore_errors=True)


def _load_json_entries(path: str):
    """(estrutura_original, lista_de_entries). Preserva shape dict/list no save."""
    if not os.path.exists(path):
        return None, []
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None, []
    if isinstance(data, dict):
        return data, data.get("entries", [])
    if isinstance(data, list):
        return data, data
    return None, []


def _save_json_entries(path: str, original, entries: list):
    if isinstance(original, dict):
        original = dict(original)
        original["entries"] = entries
        payload = original
    else:
        payload = entries
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def read_snapshot_entries(snapshot_dir: str) -> list:
    out = []
    for name in ("episodic.json", "semantic.json"):
        _o, es = _load_json_entries(os.path.join(snapshot_dir, name))
        out.extend(e for e in es if isinstance(e, dict))
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Manipulação por condição — SO no clone (apos restore, antes do MemoryStore)
# ══════════════════════════════════════════════════════════════════════════════

def apply_condition(sess_dir: str, rotulo: str) -> None:
    """Edita episodic.json/semantic.json do clone conforme a condicao (§3)."""
    if rotulo == COND_BASELINE:
        return
    gravador = rotulo in (COND_GRAVADOR, COND_COMBINADO, COND_SRCW)
    trivial  = rotulo in (COND_TRIVIAL, COND_COMBINADO)
    for name in ("episodic.json", "semantic.json"):
        path = os.path.join(sess_dir, name)
        original, entries = _load_json_entries(path)
        if not entries:
            continue
        changed = False
        kept = []
        for e in entries:
            if isinstance(e, dict) and e.get("source_type") == SS:
                if trivial and is_trivial_ss(e):
                    changed = True
                    continue  # excluida do indice de retrieval
                if gravador:
                    if e.get("prioridade") != "media" or e.get("epistemic_status") != "hypothesis":
                        e = dict(e)
                        e["prioridade"] = "media"
                        e["epistemic_status"] = "hypothesis"
                        changed = True
            kept.append(e)
        if changed:
            _save_json_entries(path, original, kept)


class _SrcWeightPatch:
    """Patch EM MEMORIA (processo do lab) do src_weight de session_summary -> 1.0.
    Exploratorio (§3). Nenhum arquivo tocado; restaurado ao sair."""

    def __enter__(self):
        from edp.memory_classifier import SOURCE_TYPE_WEIGHTS
        self._d = SOURCE_TYPE_WEIGHTS
        self._old = self._d.get(SS)
        self._d[SS] = 1.0
        return self

    def __exit__(self, *a):
        if self._old is None:
            self._d.pop(SS, None)
        else:
            self._d[SS] = self._old


# ══════════════════════════════════════════════════════════════════════════════
# Alvos, retrieve e metricas por query
# ══════════════════════════════════════════════════════════════════════════════

def resolve_needle(entries: list, spec: dict) -> Set[str]:
    """needle/id -> set de ids NAO-SS. Vazio = nao resolvido."""
    if spec.get("id"):
        for e in entries:
            if e.get("id") == spec["id"]:
                return set() if e.get("source_type") == SS else {spec["id"]}
        return set()
    needle = (spec.get("needle") or "").lower().strip()
    if not needle:
        return set()
    return {
        e["id"] for e in entries
        if e.get("id") and e.get("source_type") != SS
        and needle in (e.get("text") or "").lower()
    }


def run_query(MemoryStore, session_id: str, scope: str, query: str) -> list:
    mem = MemoryStore(session_id)
    if scope != "cognitive":
        try:
            mem.set_scope(scope)
        except Exception:
            pass
    return mem.retrieve(query, top_k=TOP_K, min_score=MIN_SCORE)  # memory.py:1612


def measure_query(res: list, ss_ids: Set[str], target_ids: Optional[Set[str]]) -> dict:
    top5, top10 = res[:5], res[:10]
    def _is_ss(r): return r.get("id") in ss_ids
    m = {
        "n_results":      len(res),
        "ss_count_top5":  sum(_is_ss(r) for r in top5),
        "ss_count_top10": sum(_is_ss(r) for r in top10),
        "slots_top5":     len(top5),
        "slots_top10":    len(top10),
    }
    if target_ids is not None:
        m["target_resolved"] = bool(target_ids)
        rank = None
        if target_ids:
            for pos, r in enumerate(res, 1):
                if r.get("id") in target_ids:
                    rank = pos
                    break
        m["target_rank"] = rank
        m["target_in_top5"] = bool(rank and rank <= 5)
        m["reciprocal_rank"] = (1.0 / rank) if rank else 0.0
    return m


def exemplos_top5(res: list, ss_ids: Set[str], target_ids: Optional[Set[str]]) -> list:
    out = []
    for pos, r in enumerate(res[:5], 1):
        out.append({
            "posicao":       pos,
            "id":            r.get("id", ""),
            "ranking_score": r.get("ranking_score"),
            "source_type":   r.get("source_type"),
            "is_ss":         r.get("id") in ss_ids,
            "is_target":     bool(target_ids) and r.get("id") in (target_ids or set()),
            "text_preview":  (r.get("text") or "").replace("\n", " ")[:110],
        })
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Exp009Result:
    dry_run: bool = True
    armed: bool = False
    n_queries: int = 0
    runs_gravados: int = 0
    leak_ok: Optional[bool] = None
    ss_total: int = 0
    trivial_picked: List[dict] = field(default_factory=list)
    session_boost_dist: Dict[str, int] = field(default_factory=dict)
    unresolved: List[str] = field(default_factory=list)
    resumo: Dict[str, dict] = field(default_factory=dict)   # sanity em memoria


def _gather_andaime(dry_run: bool) -> dict:
    try:
        from .run_once import gather_andaime
        a = gather_andaime(MODELO_LABEL)
    except Exception:
        a = {"model_version": MODELO_LABEL, "fonte_andaime": "exp009"}
    a["experimento"] = EXPERIMENTO
    a["dry_run"] = bool(dry_run)
    return a


def _session_boost_dist(sess_dir: str, ss_ids: Set[str], MemoryStore, session: str) -> Dict[str, int]:
    """Qual session_boost as SS receberiam AGORA (§1: reporte honesto no dry-run).
    Compara o session_marker de cada SS episodica com o marker corrente do store."""
    dist = {"x1.60_atual": 0, "x0.85_antiga": 0, "x1.00_legacy": 0, "semantic_na": 0}
    try:
        mem = MemoryStore(session)
        current = getattr(mem.episodic, "_current_session_marker", None)
        epi_ids = {e.get("id") for e in mem.episodic.entries if isinstance(e, dict)}
        for e in mem.episodic.entries:
            if not isinstance(e, dict) or e.get("id") not in ss_ids:
                continue
            marker = e.get("session_marker")
            if marker and current and marker == current:
                dist["x1.60_atual"] += 1
            elif marker:
                dist["x0.85_antiga"] += 1
            else:
                dist["x1.00_legacy"] += 1
        dist["semantic_na"] = len([i for i in ss_ids if i not in epi_ids])
    except Exception as e:
        logger.warning("[exp009] session_boost dist falhou: %s", e)
    return dist


def run_exp009(*, dry_run: bool = False, record: bool = True) -> Exp009Result:
    res = Exp009Result(dry_run=dry_run)
    armed = os.environ.get("EDP_LAB_ARMED", "0").strip().lower() in ("1", "true", "yes", "on")
    res.armed = armed
    if not dry_run and not armed:
        raise RuntimeError(
            "RECUSADO: disparo real do exp009 exige EDP_LAB_ARMED=1. "
            "Rode --dry-run primeiro (prova-no-espelho) e revise a saida."
        )

    base, session, scope, sess_dir = resolve_paths()
    try:
        from edp.memory import MemoryStore
    except Exception as e:
        _die(f"não consegui importar edp.memory (rode da raiz do repo): {e}")

    guard = SessionGuard(sess_dir)
    try:
        snap_entries = read_snapshot_entries(guard.snapshot)
        ss_ids: Set[str] = {
            e["id"] for e in snap_entries
            if e.get("id") and e.get("source_type") == SS
        }
        res.ss_total = len(ss_ids)
        res.trivial_picked = [
            {"id": e.get("id"), "acessos": e.get("acessos", 0),
             "preview": useful_text(e.get("text") or "")[:90]}
            for e in snap_entries if is_trivial_ss(e)
        ]

        # dataset: (grupo, query, target_ids|None)
        plan: List[Tuple[str, str, Optional[Set[str]]]] = []
        plan += [("vaga", q, None) for q in VAGUE_QUERIES]
        plan += [("redis", q, set(REDIS_TARGET_IDS)) for q in REDIS_QUERIES]
        for spec in SPECIFIC_QUERIES:
            tids = resolve_needle(snap_entries, spec)
            if not tids:
                res.unresolved.append(spec["query"])
            plan.append(("especifica", spec["query"], tids))
        plan += [("guarda", q, None) for q in GUARD_QUERIES]
        res.n_queries = len(plan)

        # dry-run: session_boost das SS (estado restaurado)
        guard.restore()
        res.session_boost_dist = _session_boost_dist(sess_dir, ss_ids, MemoryStore, session)

        store = None
        if record:
            from .prontuario import get_prontuario
            store = get_prontuario()
        andaime_base = _gather_andaime(dry_run)

        acc: Dict[str, dict] = {}
        for cond in CONDICOES:
            rot = cond["rotulo"]
            a = acc.setdefault(rot, {
                "vaga_ss5": 0, "vaga_n5": 0, "vaga_ss10": 0, "vaga_n10": 0,
                "esp_ss5": 0, "esp_n5": 0,
                "esp_hit5": 0, "esp_res": 0, "esp_rr": 0.0,
                "redis_hit5": 0, "redis_n": 0,
                "guarda_com_ss": 0, "guarda_n": 0,
            })
            patch = _SrcWeightPatch() if rot == COND_SRCW else None
            if patch:
                patch.__enter__()
            try:
                for qi, (grupo, query, tids) in enumerate(plan):
                    guard.restore()                    # estado original (isolamento)
                    apply_condition(sess_dir, rot)     # manipulacao SO no clone
                    resq = run_query(MemoryStore, session, scope, query)
                    m = measure_query(resq, ss_ids, tids)
                    ex = exemplos_top5(resq, ss_ids, tids)

                    if grupo == "vaga":
                        a["vaga_ss5"] += m["ss_count_top5"];  a["vaga_n5"] += m["slots_top5"]
                        a["vaga_ss10"] += m["ss_count_top10"]; a["vaga_n10"] += m["slots_top10"]
                    elif grupo in ("redis", "especifica"):
                        a["esp_ss5"] += m["ss_count_top5"];   a["esp_n5"] += m["slots_top5"]
                        if m.get("target_resolved"):
                            a["esp_res"] += 1
                            a["esp_hit5"] += int(m["target_in_top5"])
                            a["esp_rr"] += m["reciprocal_rank"]
                        if grupo == "redis":
                            a["redis_n"] += 1
                            a["redis_hit5"] += int(m.get("target_in_top5", False))
                    elif grupo == "guarda":
                        a["guarda_n"] += 1
                        a["guarda_com_ss"] += int(m["ss_count_top5"] >= 1)

                    if record and store is not None:
                        andaime = dict(andaime_base)
                        andaime.update({
                            "condicao_rotulo": rot, "condicao_papel": cond["papel"],
                            "condicao_tipo": cond["tipo"], "grupo": grupo, "query_idx": qi,
                        })
                        try:
                            store.record_run(
                                modelo=MODELO_LABEL,
                                formato_id=f"retrieval_{rot}",
                                andaime=andaime,
                                janela_enviada=query,
                                secoes={
                                    "query": query, "grupo": grupo,
                                    "target_ids": sorted(tids) if tids else [],
                                    "ss_total_store": res.ss_total,
                                },
                                respostas=ex,
                                scope=f"copy::{session}_{scope}",
                                metricas={**{k: v for k, v in m.items()},
                                          "condicao": rot, "grupo": grupo,
                                          "experimento": EXPERIMENTO},
                            )
                            res.runs_gravados += 1
                        except Exception as e:
                            logger.warning("[exp009] record_run falhou (%s/q%d): %s", rot, qi, e)
            finally:
                if patch:
                    patch.__exit__(None, None, None)

        # sanity em memoria (analise canonica: --score)
        for rot, a in acc.items():
            res.resumo[rot] = {
                "vaga_ss_top5":  (a["vaga_ss5"] / a["vaga_n5"]) if a["vaga_n5"] else 0.0,
                "esp_ss_top5":   (a["esp_ss5"] / a["esp_n5"]) if a["esp_n5"] else 0.0,
                "esp_recall5":   (a["esp_hit5"] / a["esp_res"]) if a["esp_res"] else 0.0,
                "esp_mrr":       (a["esp_rr"] / a["esp_res"]) if a["esp_res"] else 0.0,
                "redis_hit5":    f"{a['redis_hit5']}/{a['redis_n']}",
                "guarda_com_ss": f"{a['guarda_com_ss']}/{a['guarda_n']}",
            }

        guard.restore()
        res.leak_ok = guard.verify_no_divergence()
        return res
    finally:
        try:
            guard.restore()
        except Exception:
            pass
        guard.cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# Scorer pos-coleta (canonico) — le o prontuario, aplica §5/§6
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Cond009:
    rotulo: str
    vaga_ss5: float = 0.0
    vaga_ss5_lo: float = 0.0
    vaga_ss5_hi: float = 0.0
    vaga_ss10: float = 0.0
    esp_ss5: float = 0.0
    esp_recall5: float = 0.0
    esp_recall5_lo: float = 0.0
    esp_recall5_hi: float = 0.0
    esp_mrr: float = 0.0
    redis_hit5: int = 0
    redis_n: int = 0
    guarda_com_ss: int = 0
    guarda_n: int = 0
    n_queries: int = 0


@dataclass
class Score009:
    n_registros_total: int = 0
    n_reais: int = 0
    n_dry_run: int = 0
    condicoes: List[Cond009] = field(default_factory=list)
    h1_confirmada: Optional[bool] = None
    h1_condicao: Optional[str] = None
    h0_vence: Optional[bool] = None
    falhas: Dict[str, List[str]] = field(default_factory=dict)


def score_009(store=None, only_real: bool = True) -> Score009:
    from .scorer import _wilson
    from .prontuario import get_prontuario
    store = store or get_prontuario()
    res = Score009()

    agg: Dict[str, dict] = {}
    for row in store.query_index():
        res.n_registros_total += 1
        blob = store.get_blob(row.get("run_id"))
        if not blob:
            continue
        a = blob.get("andaime", {}) or {}
        if a.get("experimento") != EXPERIMENTO:
            continue
        if bool(a.get("dry_run", False)):
            res.n_dry_run += 1
            if only_real:
                continue
        else:
            res.n_reais += 1
        rot = a.get("condicao_rotulo") or "?"
        grupo = a.get("grupo") or "?"
        met = row.get("metricas") or {}
        s = agg.setdefault(rot, {
            "vaga_ss5": 0, "vaga_n5": 0, "vaga_ss10": 0, "vaga_n10": 0,
            "esp_ss5": 0, "esp_n5": 0, "esp_hit5": 0, "esp_res": 0, "esp_rr": 0.0,
            "redis_hit5": 0, "redis_n": 0, "guarda_com_ss": 0, "guarda_n": 0, "nq": 0,
        })
        s["nq"] += 1
        if grupo == "vaga":
            s["vaga_ss5"] += int(met.get("ss_count_top5", 0));   s["vaga_n5"] += int(met.get("slots_top5", 0))
            s["vaga_ss10"] += int(met.get("ss_count_top10", 0)); s["vaga_n10"] += int(met.get("slots_top10", 0))
        elif grupo in ("redis", "especifica"):
            s["esp_ss5"] += int(met.get("ss_count_top5", 0));    s["esp_n5"] += int(met.get("slots_top5", 0))
            if met.get("target_resolved"):
                s["esp_res"] += 1
                s["esp_hit5"] += int(bool(met.get("target_in_top5")))
                s["esp_rr"] += float(met.get("reciprocal_rank", 0.0) or 0.0)
            if grupo == "redis":
                s["redis_n"] += 1
                s["redis_hit5"] += int(bool(met.get("target_in_top5")))
        elif grupo == "guarda":
            s["guarda_n"] += 1
            s["guarda_com_ss"] += int(int(met.get("ss_count_top5", 0)) >= 1)

    for cond in CONDICOES:
        rot = cond["rotulo"]
        s = agg.get(rot)
        if not s:
            continue
        v5lo, v5hi = _wilson(s["vaga_ss5"], s["vaga_n5"]) if s["vaga_n5"] else (0.0, 0.0)
        r5lo, r5hi = _wilson(s["esp_hit5"], s["esp_res"]) if s["esp_res"] else (0.0, 0.0)
        res.condicoes.append(Cond009(
            rotulo=rot,
            vaga_ss5=(s["vaga_ss5"] / s["vaga_n5"]) if s["vaga_n5"] else 0.0,
            vaga_ss5_lo=v5lo, vaga_ss5_hi=v5hi,
            vaga_ss10=(s["vaga_ss10"] / s["vaga_n10"]) if s["vaga_n10"] else 0.0,
            esp_ss5=(s["esp_ss5"] / s["esp_n5"]) if s["esp_n5"] else 0.0,
            esp_recall5=(s["esp_hit5"] / s["esp_res"]) if s["esp_res"] else 0.0,
            esp_recall5_lo=r5lo, esp_recall5_hi=r5hi,
            esp_mrr=(s["esp_rr"] / s["esp_res"]) if s["esp_res"] else 0.0,
            redis_hit5=s["redis_hit5"], redis_n=s["redis_n"],
            guarda_com_ss=s["guarda_com_ss"], guarda_n=s["guarda_n"],
            n_queries=s["nq"],
        ))

    # ── veredito §6 (limiares congelados) ──
    por = {c.rotulo: c for c in res.condicoes}

    def _checks(c: Cond009) -> List[str]:
        fails = []
        if not (c.vaga_ss5 < H1_SS_TOP5_VAGAS_MAX):
            fails.append(f"%SS top-5 vagas = {c.vaga_ss5*100:.1f}% (exigido < {H1_SS_TOP5_VAGAS_MAX*100:.0f}%)")
        if not (c.redis_n and c.redis_hit5 / c.redis_n >= H1_REDIS_MIN_FRAC):
            fails.append(f"Redis top-5 = {c.redis_hit5}/{c.redis_n} (exigido >= 2/3)")
        if not (c.guarda_com_ss >= H1_GUARDA_MIN_QUERIES):
            fails.append(f"guarda = {c.guarda_com_ss}/{c.guarda_n} resumo c/ SS (exigido >= 1)")
        return fails

    for rot in (COND_GRAVADOR, COND_COMBINADO):
        c = por.get(rot)
        if not c:
            continue
        fails = _checks(c)
        res.falhas[rot] = fails
        if not fails and res.h1_confirmada is not True:
            res.h1_confirmada = True
            res.h1_condicao = rot
    if res.h1_confirmada is not True and (COND_GRAVADOR in res.falhas or COND_COMBINADO in res.falhas):
        res.h1_confirmada = False
        comb = por.get(COND_COMBINADO)
        if comb is not None:
            dominancia_persiste = (
                comb.vaga_ss5 >= H1_SS_TOP5_VAGAS_MAX
                or (comb.redis_n and comb.redis_hit5 / comb.redis_n < H1_REDIS_MIN_FRAC)
            )
            res.h0_vence = bool(dominancia_persiste)
    return res


def report_009(res: Score009) -> None:
    line = "=" * 96
    print("\n" + line)
    print("SCORER — Experimento 009  (despriorizar session_summary: dominância vs caso legítimo)")
    print(line)
    print(f"  registros: {res.n_registros_total} (reais={res.n_reais} | dry_run={res.n_dry_run})\n")
    hdr = (f"  {'condicao':<22}{'%SS t5 vagas [IC]':<24}{'%SS t5 esp':<12}"
           f"{'Recall@5 esp [IC]':<22}{'MRR':<7}{'Redis t5':<10}{'guarda'}")
    print(hdr)
    for c in res.condicoes:
        v = f"{c.vaga_ss5*100:>5.1f}% [{c.vaga_ss5_lo:.2f},{c.vaga_ss5_hi:.2f}]"
        r = f"{c.esp_recall5*100:>5.1f}% [{c.esp_recall5_lo:.2f},{c.esp_recall5_hi:.2f}]"
        print(f"  {c.rotulo:<22}{v:<24}{c.esp_ss5*100:>5.1f}%{'':<5}{r:<22}"
              f"{c.esp_mrr:.3f}  {c.redis_hit5}/{c.redis_n:<8}{c.guarda_com_ss}/{c.guarda_n}")

    print(f"\n  VEREDITO (limiares congelados no pré-registro §6):")
    print(f"    H1 exige, em trat_gravador OU trat_combinado: %SS t5 vagas < 40% "
          f"E Redis t5 >= 2/3 E guarda >= 1.")
    if res.h1_confirmada:
        print(f"    --> H1 CONFIRMADA na condição '{res.h1_condicao}': remover os privilégios de")
        print(f"        nascença devolve o conteúdo ao top-5 sem zerar o caso legítimo.")
    elif res.h1_confirmada is False:
        for rot, fails in res.falhas.items():
            if fails:
                print(f"    '{rot}' falhou: " + "; ".join(fails))
        if res.h0_vence:
            print(f"    --> H0 VENCE: dominância persiste em trat_combinado — o problema é")
            print(f"        embedding genérico/acessos, não os boosts de nascença. Próximo fix:")
            print(f"        canal de injeção separado / embedding (opção c/d do diagnóstico).")
        else:
            print(f"    --> H1 não confirmada pelos limiares, mas dominância NÃO persiste em")
            print(f"        trat_combinado — resultado misto; ver qual guarda falhou acima.")
    else:
        print(f"    (sem registros reais suficientes — rode o disparo armado antes do --score)")
    print(line)
    print("  (Exploratória trat_gravador_srcw NÃO entra no confirmatório — gera hipótese.)")
    print("  (Exemplos por query no blob do prontuário; use --audit.)")


def audit_009(store=None, max_por_grupo: int = 4):
    """Top-5 real por (condicao x grupo), marcando SS e alvo. Texto de verdade."""
    from .prontuario import get_prontuario
    store = store or get_prontuario()
    by: Dict[Tuple[str, str], list] = {}
    for row in store.query_index():
        blob = store.get_blob(row.get("run_id"))
        if not blob:
            continue
        a = blob.get("andaime", {}) or {}
        if a.get("experimento") != EXPERIMENTO or a.get("dry_run"):
            continue
        key = (a.get("condicao_rotulo") or "?", a.get("grupo") or "?")
        by.setdefault(key, []).append(blob)
    print("\n" + "=" * 96)
    print("AUDITORIA — Exp 009, top-5 por condição × grupo (SS e alvo marcados)")
    print("=" * 96)
    for cond in CONDICOES:
        for grupo in ("vaga", "redis", "especifica", "guarda"):
            blobs = by.get((cond["rotulo"], grupo), [])[:max_por_grupo]
            if not blobs:
                continue
            print(f"\n── {cond['rotulo']} × {grupo}:")
            for b in blobs:
                print(f"   Q: {b.get('secoes', {}).get('query', '?')}")
                for ex in b.get("respostas", [])[:5]:
                    tag = "  <<SS" if ex.get("is_ss") else ("  <<ALVO" if ex.get("is_target") else "")
                    sc = ex.get("ranking_score")
                    sc = f"{sc:.3f}" if isinstance(sc, (int, float)) else sc
                    print(f"     {ex.get('posicao')}. score={sc} [{str(ex.get('source_type')):16}] "
                          f"{ex.get('text_preview','')[:70]}{tag}")
    print("=" * 96)
    return by


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def _print_fire_report(res: Exp009Result):
    line = "=" * 96
    print("\n" + line)
    print(f"EXP 009 — {'DRY-RUN (prova-no-espelho)' if res.dry_run else 'DISPARO REAL'}")
    print(line)
    print(f"  armado             : EDP_LAB_ARMED={'1' if res.armed else '0'}")
    print(f"  queries no plano   : {res.n_queries} "
          f"({len(VAGUE_QUERIES)} vagas + {len(REDIS_QUERIES)} redis + "
          f"{len(SPECIFIC_QUERIES)} específicas + {len(GUARD_QUERIES)} guarda) "
          f"× {len(CONDICOES)} condições")
    print(f"  runs gravados      : {res.runs_gravados}")
    print(f"  SS no store        : {res.ss_total}")
    print(f"  isolamento (final) : cópia == snapshot pristine? {res.leak_ok}")

    print(f"\n  session_boost das SS no ambiente (§1 — leitura honesta):")
    for k, v in res.session_boost_dist.items():
        print(f"    {k:<14}: {v}")

    print(f"\n  trat_trivial pegaria {len(res.trivial_picked)} SS (REVISE antes de armar):")
    for t in res.trivial_picked[:20]:
        print(f"    id={str(t['id'])[:12]:12} acc={t['acessos']:>3} | {t['preview']}")
    if len(res.trivial_picked) > 20:
        print(f"    ... +{len(res.trivial_picked)-20}")

    if res.unresolved:
        print(f"\n  [AVISO] específicas com alvo NÃO resolvido (troque needle→id antes de armar):")
        for q in res.unresolved:
            print(f"    - {q}")

    print(f"\n  resumo rápido (sanity — o veredito canônico é --score):")
    print(f"    {'condicao':<22}{'%SS t5 vagas':<14}{'%SS t5 esp':<12}{'Recall@5':<10}{'MRR':<7}{'Redis':<8}guarda")
    for cond in CONDICOES:
        r = res.resumo.get(cond["rotulo"])
        if not r:
            continue
        print(f"    {cond['rotulo']:<22}{r['vaga_ss_top5']*100:>6.1f}%{'':<7}"
              f"{r['esp_ss_top5']*100:>6.1f}%{'':<5}{r['esp_recall5']*100:>5.1f}%{'':<4}"
              f"{r['esp_mrr']:.3f}  {r['redis_hit5']:<8}{r['guarda_com_ss']}")
    print(line)
    if res.dry_run:
        print("  Revise: pares/alvos, lista do trat_trivial, session_boost. Se ok:")
        print("    EDP_LAB_ARMED=1 python -m edp.lab.exp009        (disparo real, congela)")
        print("    python -m edp.lab.exp009 --score                 (Wilson + veredito §6)")
        print("    python -m edp.lab.exp009 --audit                  (exemplos de top-5)")


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        description="Experimento 009 — despriorizar session_summary (retrieval-quality).")
    p.add_argument("--dry-run", action="store_true",
                   help="prova-no-espelho: encanamento inteiro + retrieve REAL, sem armar (dry_run=True).")
    p.add_argument("--no-record", action="store_true", help="não grava no prontuário.")
    p.add_argument("--score", action="store_true", help="pontua o prontuário (Wilson + veredito §6).")
    p.add_argument("--audit", action="store_true", help="amostra os top-5 por condição×grupo.")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.score:
        report_009(score_009(only_real=True))
        return 0
    if args.audit:
        audit_009()
        return 0
    try:
        res = run_exp009(dry_run=args.dry_run, record=not args.no_record)
    except RuntimeError as e:
        print(f"\n[RECUSADO/ERRO] {e}")
        return 1
    _print_fire_report(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
