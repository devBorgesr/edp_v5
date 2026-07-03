"""
edp.lab.exp010 — Encarnação em CODIGO do Experimento 010 (retrieval híbrido).

Espelha preregistro_experimento_010.md. CONGELADO apos o primeiro disparo real.

Categoria RETRIEVAL-QUALITY: mede o que o retrieve SELECIONA. LLM nunca e
chamado; custo de API zero. Compara o hot path atual (cosine puro,
MemoryStore.retrieve, edp/memory.py:1612) com o HybridRetriever REAL
(BM25+vetorial+RRF, edp/retrieval_hybrid.py:105), que existe mas esta DESLIGADO.
Este experimento decide se vale liga-lo — NAO liga nada, so mede.

Fatos mecanicos acomodados (§1a do pre-registro):
  - RRF tem escala ~1/(60+rank) (max ~0.016): min_score DEVE ser 0.0, senao o
    RRF retorna vazio (RETRIEVAL_MIN_SIM=0.20 zeraria). Congelado MIN_SCORE=0.0;
    o dry-run VERIFICA RRF nao-vazio.
  - BUG REGISTRADO (nao corrigido — mudaria o sujeito medido): em
    retrieval_hybrid.py:213-215, no bloco MMR, a 1a atribuicao a sc_list indexa
    `fused` (lista de tuplas) como dict — codigo morto, sobrescrito pela 2a
    atribuicao (:216-219), correta. Resultado final ok. Limpar SE promover.
  - Comparacao declarada: baseline inclui a pilha de governanca de producao;
    o hibrido e cru. Comparamos os dois sujeitos REAIS como sao.

Isolamento (padrao exp009): EDP_BASE_DIR numa COPIA + snapshot pristine +
restore antes de CADA query (retrieve real MUTA, memory.py:871-880) +
no-divergencia final. Indice do hibrido construido do snapshot (read-only).
Embeddings: os REAIS salvos nas entries; query_emb via edp.embeddings (mesmo
modelo do retrieve), calculado 1x por query e reusado nas condicoes hibridas.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger("edp.lab.exp010")

# ── Constantes CONGELADAS (§8) ────────────────────────────────────────────────
EXPERIMENTO  = "010"
TOP_K        = 10
MIN_SCORE    = 0.0        # §1a.1 — RRF tem escala ~0.016; 0.20 zeraria o RRF
SS           = "session_summary"
MODELO_LABEL = "retrieval::no-llm"

HYB_ALPHA      = 0.5
HYB_RRF_K      = 60
HYB_MMR_LAMBDA = 0.5

COND_BASELINE = "baseline_cosine"
COND_RRF      = "hibrido_rrf"
COND_RRF_MMR  = "hibrido_rrf_mmr"
COND_WEIGHTED = "hibrido_weighted"   # §EXPLORATORIA

CONDICOES = [
    {"rotulo": COND_BASELINE, "papel": "hot path atual (cosine puro)",          "tipo": "baseline"},
    {"rotulo": COND_RRF,      "papel": "BM25+vetorial+RRF",                      "tipo": "tratamento"},
    {"rotulo": COND_RRF_MMR,  "papel": "RRF + MMR (diversidade)",                "tipo": "tratamento"},
    {"rotulo": COND_WEIGHTED, "papel": "EXPLORATORIA: soma ponderada alpha=0.5", "tipo": "exploratorio"},
]

# Limiar H1 (§6) — congelado
H1_REPEAT_DROP_PP = 0.15   # H1-REPETICAO: repeat_rate(mmr) <= baseline - 15pp
REDFLAG_SS_PP     = 0.10   # red flag: %SS vagas do hibrido > baseline + 10pp

# ── Dataset CONGELADO (§4) — ordem do plano congelada (metrica de repeticao) ──
VAGUE_QUERIES = [
    "vamos continuar nossa conversa",
    "continuando o que falávamos",
    "o que a gente tinha concluído mesmo?",
    "me lembra o que discutimos",
    "voltando ao que estávamos vendo",
    "sobre o que conversamos até agora",
]

REDIS_TARGET_IDS = {
    "0c78fa08-8a51-4a04-ad15-2b23d0800a0b",
    "4c57ed7a-c275-4155-93eb-e1efa5a164d5",
}
REDIS_QUERIES = [
    "vamos continuar a conversa sobre Redis e Memcached",
    "me lembra o que a gente concluiu sobre cache de sessões web com Redis",
    "voltando ao assunto do Redis para sessões web",
]

# Needles do exp009 que RESOLVERAM no store real. FAISS OMITIDA (§4 — sem
# memoria de conteudo no store, provado no exp009; sem ground truth nao ha
# Recall a medir). Trocar needle->{"id": ...} no dry-run se ambiguo.
SPECIFIC_QUERIES = [
    {"query": "continuando nossa conversa sobre transformers e atenção em LLMs", "needle": "transformer"},
    {"query": "voltando ao que discutimos sobre embeddings de frases",           "needle": "embedding"},
    {"query": "sobre o RAG e as alucinações que a gente discutiu",               "needle": "rag"},
    {"query": "continuando o papo sobre desempenho de Python em tempo real",     "needle": "python"},
    {"query": "me lembra da nossa discussão sobre memória episódica do EDP",     "needle": "episódic"},
]

GUARD_QUERIES = [
    "me dá um resumo do que consolidamos",
    "o que ficou registrado como resumo da sessão passada?",
    "qual foi o resumo das nossas últimas sessões?",
]


# ══════════════════════════════════════════════════════════════════════════════
# Isolamento (padrao exp009)
# ══════════════════════════════════════════════════════════════════════════════

def _die(msg: str, code: int = 2):
    print(f"\n[ERRO] {msg}\n", file=sys.stderr)
    sys.exit(code)


def resolve_paths() -> Tuple[str, str, str, str]:
    base = os.environ.get("EDP_BASE_DIR")
    if not base:
        _die(
            "EDP_BASE_DIR não está setado. Aponte para uma CÓPIA da produção:\n"
            "    robocopy C:\\edp_data C:\\edp_data_exp010 /E\n"
            "    $env:EDP_BASE_DIR = \"C:\\edp_data_exp010\""
        )
    base = os.path.abspath(base)
    if not os.path.isdir(base):
        _die(f"EDP_BASE_DIR não é um diretório existente: {base}")
    if os.path.basename(base.rstrip("/\\")).lower() == "edp_data" and \
       os.environ.get("ALLOW_PROD", "0").lower() not in ("1", "true", "yes", "on"):
        _die(f"EDP_BASE_DIR aponta para '{base}' — parece a PRODUÇÃO. Só roda sobre CÓPIA.")
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
    def __init__(self, sess_dir: str):
        self.sess_dir = sess_dir
        self._tmp = tempfile.mkdtemp(prefix="exp010_snapshot_")
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


def _load_entries(path: str) -> list:
    if not os.path.exists(path):
        return []
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("entries", [])
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def read_snapshot_entries(snapshot_dir: str) -> list:
    out = []
    for name in ("episodic.json", "semantic.json"):
        out.extend(_load_entries(os.path.join(snapshot_dir, name)))
    return out


def resolve_needle(entries: list, spec: dict) -> Set[str]:
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


# ══════════════════════════════════════════════════════════════════════════════
# Indice hibrido REAL (construido do snapshot; embeddings REAIS das entries)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class HybridIndex:
    retriever: object
    ids:       List[str]
    entries:   List[dict]

    def stats(self) -> dict:
        return self.retriever.stats()


def build_hybrid_index(entries: list) -> HybridIndex:
    """Alimenta o HybridRetriever REAL com textos + embeddings REAIS (mesmos do
    baseline). Universo: entries do scope com id, texto e embedding."""
    import numpy as np
    from edp.retrieval_hybrid import HybridRetriever  # o sujeito REAL

    usable = [
        e for e in entries
        if e.get("id") and (e.get("text") or "").strip() and e.get("embedding")
    ]
    texts = [e["text"] for e in usable]
    embs = np.array([e["embedding"] for e in usable], dtype=np.float32)
    hr = HybridRetriever(alpha=HYB_ALPHA, rrf_k=HYB_RRF_K)
    hr.add(texts, embs)
    return HybridIndex(retriever=hr, ids=[e["id"] for e in usable], entries=usable)


def hybrid_search(index: HybridIndex, query: str, query_emb, *,
                  method: str, mmr: bool) -> list:
    """Chama o search REAL e mapeia indices -> entries (com id). Retorna lista
    no formato comum do experimento: dicts com id/text/source_type/scores."""
    res = index.retriever.search(
        query, query_emb,
        top_k=TOP_K, min_score=MIN_SCORE,        # §1a.1: 0.0 obrigatorio p/ RRF
        method=method, mmr=mmr, mmr_lambda=HYB_MMR_LAMBDA,
    )
    out = []
    for pos, i in enumerate(res.indices):
        e = index.entries[i] if i < len(index.entries) else {}
        out.append({
            "id":            index.ids[i] if i < len(index.ids) else "",
            "text":          e.get("text", ""),
            "source_type":   e.get("source_type"),
            "ranking_score": res.scores[pos] if pos < len(res.scores) else None,
            "bm25_score":    res.bm25_scores[pos] if pos < len(res.bm25_scores) else None,
            "vector_score":  res.vector_scores[pos] if pos < len(res.vector_scores) else None,
        })
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Metricas por query + repeticao (espelha retrieval_monitor.py:113-118)
# ══════════════════════════════════════════════════════════════════════════════

def measure_query(res: list, ss_ids: Set[str], target_ids: Optional[Set[str]]) -> dict:
    top5, top10 = res[:5], res[:10]
    def _is_ss(r): return r.get("id") in ss_ids
    m = {
        "n_results":      len(res),
        "ss_count_top5":  sum(_is_ss(r) for r in top5),
        "ss_count_top10": sum(_is_ss(r) for r in top10),
        "slots_top5":     len(top5),
        "slots_top10":    len(top10),
        "top5_ids":       [r.get("id", "") for r in top5],
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
        m["target_in_top3"] = bool(rank and rank <= 3)
        m["target_in_top5"] = bool(rank and rank <= 5)
        m["reciprocal_rank"] = (1.0 / rank) if rank else 0.0
    return m


def repetition_stats(top5_seq: List[List[str]]) -> dict:
    """Sobre a sequencia (ordem congelada do plano) de top-5 por query:
    repeat_rate = fracao de pares consecutivos com |A∩B| >= min(2, |B|)
    (criterio literal do monitor); overlap_frac = media de |A∩B|/5."""
    pairs = 0
    repeats = 0
    overlaps = []
    for a, b in zip(top5_seq, top5_seq[1:]):
        sa, sb = set(x for x in a if x), set(x for x in b if x)
        if not sb:
            continue
        pairs += 1
        inter = len(sa & sb)
        overlaps.append(inter / 5.0)
        if inter >= min(2, len(sb)):
            repeats += 1
    return {
        "pairs": pairs,
        "repeat_rate": (repeats / pairs) if pairs else 0.0,
        "overlap_frac": (sum(overlaps) / len(overlaps)) if overlaps else 0.0,
    }


def exemplos_top5(res: list, ss_ids: Set[str], target_ids: Optional[Set[str]]) -> list:
    out = []
    for pos, r in enumerate(res[:5], 1):
        out.append({
            "posicao":       pos,
            "id":            r.get("id", ""),
            "ranking_score": r.get("ranking_score"),
            "bm25_score":    r.get("bm25_score"),
            "vector_score":  r.get("vector_score"),
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
class Exp010Result:
    dry_run: bool = True
    armed: bool = False
    n_queries: int = 0
    runs_gravados: int = 0
    leak_ok: Optional[bool] = None
    ss_total: int = 0
    index_stats: dict = field(default_factory=dict)
    rrf_nonempty_ok: Optional[bool] = None
    unresolved: List[str] = field(default_factory=list)
    resumo: Dict[str, dict] = field(default_factory=dict)


def _gather_andaime(dry_run: bool) -> dict:
    try:
        from .run_once import gather_andaime
        a = gather_andaime(MODELO_LABEL)
    except Exception:
        a = {"model_version": MODELO_LABEL, "fonte_andaime": "exp010"}
    a["experimento"] = EXPERIMENTO
    a["dry_run"] = bool(dry_run)
    a["hyb_alpha"], a["hyb_rrf_k"], a["hyb_mmr_lambda"] = HYB_ALPHA, HYB_RRF_K, HYB_MMR_LAMBDA
    return a


def run_exp010(*, dry_run: bool = False, record: bool = True) -> Exp010Result:
    res = Exp010Result(dry_run=dry_run)
    armed = os.environ.get("EDP_LAB_ARMED", "0").strip().lower() in ("1", "true", "yes", "on")
    res.armed = armed
    if not dry_run and not armed:
        raise RuntimeError(
            "RECUSADO: disparo real do exp010 exige EDP_LAB_ARMED=1. "
            "Rode --dry-run primeiro (prova-no-espelho)."
        )

    base, session, scope, sess_dir = resolve_paths()
    try:
        from edp.memory import MemoryStore
        from edp.embeddings import embed_one
    except Exception as e:
        _die(f"não consegui importar edp (rode da raiz do repo): {e}")

    guard = SessionGuard(sess_dir)
    try:
        snap_entries = read_snapshot_entries(guard.snapshot)
        ss_ids: Set[str] = {
            e["id"] for e in snap_entries
            if e.get("id") and e.get("source_type") == SS
        }
        res.ss_total = len(ss_ids)

        # Indice hibrido REAL, do snapshot (read-only)
        index = build_hybrid_index(snap_entries)
        res.index_stats = index.stats()

        # Plano na ORDEM CONGELADA (§4) — a repeticao depende dela
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

        # query_emb 1x por query, reusado nas condicoes hibridas (justica §3)
        query_embs = {q: embed_one(q) for _g, q, _t in plan}

        store = None
        if record:
            from .prontuario import get_prontuario
            store = get_prontuario()
        andaime_base = _gather_andaime(dry_run)

        acc: Dict[str, dict] = {}
        for cond in CONDICOES:
            rot = cond["rotulo"]
            a = acc.setdefault(rot, {
                "vaga_ss5": 0, "vaga_n5": 0, "esp_ss5": 0, "esp_n5": 0,
                "hit3": 0, "hit5": 0, "resolved": 0, "rr": 0.0,
                "redis_hit5": 0, "redis_n": 0,
                "guarda_com_ss": 0, "guarda_n": 0,
                "top5_seq": [],
            })
            for qi, (grupo, query, tids) in enumerate(plan):
                guard.restore()   # estado original antes de CADA query
                if rot == COND_BASELINE:
                    mem = MemoryStore(session)
                    if scope != "cognitive":
                        try:
                            mem.set_scope(scope)
                        except Exception:
                            pass
                    resq = mem.retrieve(query, top_k=TOP_K, min_score=MIN_SCORE)  # memory.py:1612
                else:
                    method = "weighted" if rot == COND_WEIGHTED else "rrf"
                    resq = hybrid_search(index, query, query_embs[query],
                                         method=method, mmr=(rot == COND_RRF_MMR))

                m = measure_query(resq, ss_ids, tids)
                ex = exemplos_top5(resq, ss_ids, tids)
                a["top5_seq"].append(m["top5_ids"])

                if grupo == "vaga":
                    a["vaga_ss5"] += m["ss_count_top5"]; a["vaga_n5"] += m["slots_top5"]
                elif grupo in ("redis", "especifica"):
                    a["esp_ss5"] += m["ss_count_top5"]; a["esp_n5"] += m["slots_top5"]
                    if m.get("target_resolved"):
                        a["resolved"] += 1
                        a["hit3"] += int(m["target_in_top3"])
                        a["hit5"] += int(m["target_in_top5"])
                        a["rr"] += m["reciprocal_rank"]
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
                                "index_stats": res.index_stats,
                            },
                            respostas=ex,
                            scope=f"copy::{session}_{scope}",
                            metricas={**{k: v for k, v in m.items() if k != "top5_ids"},
                                      "top5_ids": m["top5_ids"],
                                      "condicao": rot, "grupo": grupo,
                                      "experimento": EXPERIMENTO},
                        )
                        res.runs_gravados += 1
                    except Exception as e:
                        logger.warning("[exp010] record_run falhou (%s/q%d): %s", rot, qi, e)

        # verificacao §1a.1: RRF nao-vazio com min_score=0.0
        rrf_seq = acc.get(COND_RRF, {}).get("top5_seq", [])
        res.rrf_nonempty_ok = bool(rrf_seq) and all(len([i for i in t if i]) > 0 for t in rrf_seq)

        # sanity em memoria (canonico: --score)
        for rot, a in acc.items():
            rep = repetition_stats(a["top5_seq"])
            res.resumo[rot] = {
                "vaga_ss_top5": (a["vaga_ss5"] / a["vaga_n5"]) if a["vaga_n5"] else 0.0,
                "esp_ss_top5":  (a["esp_ss5"] / a["esp_n5"]) if a["esp_n5"] else 0.0,
                "recall5":      (a["hit5"] / a["resolved"]) if a["resolved"] else 0.0,
                "mrr":          (a["rr"] / a["resolved"]) if a["resolved"] else 0.0,
                "redis_hit5":   f"{a['redis_hit5']}/{a['redis_n']}",
                "guarda":       f"{a['guarda_com_ss']}/{a['guarda_n']}",
                "repeat_rate":  rep["repeat_rate"],
                "overlap_frac": rep["overlap_frac"],
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
# Scorer pos-coleta (canonico) — §5/§6
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Cond010:
    rotulo: str
    n_queries: int = 0
    recall3: float = 0.0
    recall5: float = 0.0
    recall5_lo: float = 0.0
    recall5_hi: float = 0.0
    mrr: float = 0.0
    resolved: int = 0
    vaga_ss5: float = 0.0
    vaga_ss5_lo: float = 0.0
    vaga_ss5_hi: float = 0.0
    esp_ss5: float = 0.0
    redis_hit5: int = 0
    redis_n: int = 0
    guarda_com_ss: int = 0
    guarda_n: int = 0
    repeat_rate: float = 0.0
    overlap_frac: float = 0.0


@dataclass
class Score010:
    n_registros_total: int = 0
    n_reais: int = 0
    n_dry_run: int = 0
    condicoes: List[Cond010] = field(default_factory=list)
    h1_conteudo: Optional[bool] = None
    h1_conteudo_cond: Optional[str] = None
    h1_repeticao: Optional[bool] = None
    h1_global: Optional[bool] = None
    guarda_fails: Dict[str, bool] = field(default_factory=dict)
    red_flag_ss: List[str] = field(default_factory=list)


def score_010(store=None, only_real: bool = True) -> Score010:
    from .scorer import _wilson
    from .prontuario import get_prontuario
    store = store or get_prontuario()
    res = Score010()

    # (condicao) -> agregados; (condicao) -> {query_idx: top5_ids} p/ repeticao
    agg: Dict[str, dict] = {}
    seqs: Dict[str, Dict[int, List[str]]] = {}
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
        qi = a.get("query_idx")
        met = row.get("metricas") or {}
        s = agg.setdefault(rot, {
            "vaga_ss5": 0, "vaga_n5": 0, "esp_ss5": 0, "esp_n5": 0,
            "hit3": 0, "hit5": 0, "resolved": 0, "rr": 0.0,
            "redis_hit5": 0, "redis_n": 0, "guarda_com_ss": 0, "guarda_n": 0, "nq": 0,
        })
        s["nq"] += 1
        if qi is not None:
            seqs.setdefault(rot, {})[qi] = list(met.get("top5_ids") or [])
        if grupo == "vaga":
            s["vaga_ss5"] += int(met.get("ss_count_top5", 0)); s["vaga_n5"] += int(met.get("slots_top5", 0))
        elif grupo in ("redis", "especifica"):
            s["esp_ss5"] += int(met.get("ss_count_top5", 0)); s["esp_n5"] += int(met.get("slots_top5", 0))
            if met.get("target_resolved"):
                s["resolved"] += 1
                s["hit3"] += int(bool(met.get("target_in_top3")))
                s["hit5"] += int(bool(met.get("target_in_top5")))
                s["rr"] += float(met.get("reciprocal_rank", 0.0) or 0.0)
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
        r5lo, r5hi = _wilson(s["hit5"], s["resolved"]) if s["resolved"] else (0.0, 0.0)
        v5lo, v5hi = _wilson(s["vaga_ss5"], s["vaga_n5"]) if s["vaga_n5"] else (0.0, 0.0)
        seq = [ids for _qi, ids in sorted(seqs.get(rot, {}).items())]
        rep = repetition_stats(seq)
        res.condicoes.append(Cond010(
            rotulo=rot, n_queries=s["nq"],
            recall3=(s["hit3"] / s["resolved"]) if s["resolved"] else 0.0,
            recall5=(s["hit5"] / s["resolved"]) if s["resolved"] else 0.0,
            recall5_lo=r5lo, recall5_hi=r5hi,
            mrr=(s["rr"] / s["resolved"]) if s["resolved"] else 0.0,
            resolved=s["resolved"],
            vaga_ss5=(s["vaga_ss5"] / s["vaga_n5"]) if s["vaga_n5"] else 0.0,
            vaga_ss5_lo=v5lo, vaga_ss5_hi=v5hi,
            esp_ss5=(s["esp_ss5"] / s["esp_n5"]) if s["esp_n5"] else 0.0,
            redis_hit5=s["redis_hit5"], redis_n=s["redis_n"],
            guarda_com_ss=s["guarda_com_ss"], guarda_n=s["guarda_n"],
            repeat_rate=rep["repeat_rate"], overlap_frac=rep["overlap_frac"],
        ))

    por = {c.rotulo: c for c in res.condicoes}
    base = por.get(COND_BASELINE)
    if base is None:
        return res

    def _guarda_ok(c: Cond010) -> bool:
        return c.guarda_com_ss >= 1

    # H1-CONTEUDO: Recall@5 e MRR estritamente > baseline, com guarda ok
    for rot in (COND_RRF, COND_RRF_MMR):
        c = por.get(rot)
        if not c:
            continue
        res.guarda_fails[rot] = not _guarda_ok(c)
        if _guarda_ok(c) and c.recall5 > base.recall5 and c.mrr > base.mrr:
            if res.h1_conteudo is not True:
                res.h1_conteudo = True
                res.h1_conteudo_cond = rot
    if res.h1_conteudo is not True and (COND_RRF in por or COND_RRF_MMR in por):
        res.h1_conteudo = False

    # H1-REPETICAO: mmr <= baseline - 15pp, com guarda ok
    mmr_c = por.get(COND_RRF_MMR)
    if mmr_c is not None:
        res.h1_repeticao = _guarda_ok(mmr_c) and (
            mmr_c.repeat_rate <= base.repeat_rate - H1_REPEAT_DROP_PP
        )

    if res.h1_conteudo is not None or res.h1_repeticao is not None:
        res.h1_global = bool(res.h1_conteudo) or bool(res.h1_repeticao)

    # red flag: %SS vagas do hibrido > baseline + 10pp
    for rot in (COND_RRF, COND_RRF_MMR, COND_WEIGHTED):
        c = por.get(rot)
        if c and c.vaga_ss5 > base.vaga_ss5 + REDFLAG_SS_PP:
            res.red_flag_ss.append(rot)
    return res


def report_010(res: Score010) -> None:
    line = "=" * 100
    print("\n" + line)
    print("SCORER — Experimento 010  (retrieval híbrido BM25+RRF vs cosine puro)")
    print(line)
    print(f"  registros: {res.n_registros_total} (reais={res.n_reais} | dry_run={res.n_dry_run})\n")
    print(f"  {'condicao':<20}{'Recall@5 [IC]':<22}{'MRR':<8}{'Redis t5':<10}"
          f"{'%SS t5 vagas [IC]':<24}{'repeat':<9}{'overlap':<9}guarda")
    for c in res.condicoes:
        r = f"{c.recall5*100:>5.1f}% [{c.recall5_lo:.2f},{c.recall5_hi:.2f}]"
        v = f"{c.vaga_ss5*100:>5.1f}% [{c.vaga_ss5_lo:.2f},{c.vaga_ss5_hi:.2f}]"
        print(f"  {c.rotulo:<20}{r:<22}{c.mrr:.3f}  {c.redis_hit5}/{c.redis_n:<8}"
              f"{v:<24}{c.repeat_rate*100:>5.1f}%   {c.overlap_frac*100:>5.1f}%   "
              f"{c.guarda_com_ss}/{c.guarda_n}")

    print(f"\n  VEREDITO (limiares congelados §6):")
    if res.h1_conteudo:
        print(f"    H1-CONTEÚDO CONFIRMADA em '{res.h1_conteudo_cond}': Recall@5 e MRR "
              f"estritamente acima do baseline, guarda intacta.")
    elif res.h1_conteudo is False:
        print(f"    H1-CONTEÚDO não confirmada (Recall@5/MRR não superam o baseline com guarda ok).")
    if res.h1_repeticao is not None:
        print(f"    H1-REPETIÇÃO ({COND_RRF_MMR} ≤ baseline − 15pp): "
              f"{'CONFIRMADA' if res.h1_repeticao else 'não confirmada'}.")
    for rot, failed in res.guarda_fails.items():
        if failed:
            print(f"    [guarda] '{rot}' zerou o caso legítimo (0 queries de resumo com SS no top-5).")
    for rot in res.red_flag_ss:
        print(f"    [red flag] '{rot}': %SS nas vagas excede baseline em >10pp — BM25 pode estar "
              f"re-inflando summaries por match léxico.")
    if res.h1_global is not None:
        if res.h1_global:
            print(f"    --> H1 GLOBAL: híbrido melhora (conteúdo e/ou repetição). Candidato ao hot")
            print(f"        path — LIMPAR o bug do MMR (retrieval_hybrid.py:213-215) antes de promover.")
        else:
            print(f"    --> H0: híbrido não melhora pelos limiares. O resíduo pede outro fix")
            print(f"        (ex.: canal de injeção separado).")
    print(line)
    print("  (hibrido_weighted é exploratória — comparar RRF vs weighted gera hipótese, não confirma.)")


def audit_010(store=None, max_por_grupo: int = 3):
    """Top-5 real por condicao para as queries-chave (redis/especificas), com
    bm25/vec scores no hibrido — para VER de onde veio o ganho (léxico?)."""
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
    print("\n" + "=" * 100)
    print("AUDITORIA — Exp 010, top-5 por condição (grupos redis/especifica/vaga)")
    print("=" * 100)
    for cond in CONDICOES:
        for grupo in ("redis", "especifica", "vaga", "guarda"):
            blobs = by.get((cond["rotulo"], grupo), [])[:max_por_grupo]
            if not blobs:
                continue
            print(f"\n── {cond['rotulo']} × {grupo}:")
            for b in blobs:
                print(f"   Q: {b.get('secoes', {}).get('query', '?')}")
                for ex in b.get("respostas", [])[:5]:
                    tag = "  <<SS" if ex.get("is_ss") else ("  <<ALVO" if ex.get("is_target") else "")
                    parts = [f"score={ex.get('ranking_score')}"]
                    if ex.get("bm25_score") is not None:
                        parts.append(f"bm25={ex.get('bm25_score')}")
                    if ex.get("vector_score") is not None:
                        parts.append(f"vec={ex.get('vector_score')}")
                    print(f"     {ex.get('posicao')}. {' '.join(parts)} "
                          f"[{str(ex.get('source_type')):16}] {ex.get('text_preview','')[:60]}{tag}")
    print("=" * 100)
    return by


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def _print_fire_report(res: Exp010Result):
    line = "=" * 100
    print("\n" + line)
    print(f"EXP 010 — {'DRY-RUN (prova-no-espelho)' if res.dry_run else 'DISPARO REAL'}")
    print(line)
    print(f"  armado             : EDP_LAB_ARMED={'1' if res.armed else '0'}")
    print(f"  queries no plano   : {res.n_queries} "
          f"({len(VAGUE_QUERIES)} vagas + {len(REDIS_QUERIES)} redis + "
          f"{len(SPECIFIC_QUERIES)} específicas + {len(GUARD_QUERIES)} guarda) "
          f"× {len(CONDICOES)} condições  [FAISS omitida — §4]")
    print(f"  runs gravados      : {res.runs_gravados}")
    print(f"  SS no store        : {res.ss_total}")
    print(f"  índice híbrido REAL: {res.index_stats}  (HybridRetriever, retrieval_hybrid.py:105)")
    print(f"  RRF não-vazio c/ min_score=0.0 (§1a.1)? {res.rrf_nonempty_ok}")
    print(f"  isolamento (final) : cópia == snapshot pristine? {res.leak_ok}")
    if res.unresolved:
        print(f"\n  [AVISO] alvos NÃO resolvidos (troque needle→id antes de armar):")
        for q in res.unresolved:
            print(f"    - {q}")
    print(f"\n  resumo rápido (sanity — veredito canônico é --score):")
    print(f"    {'condicao':<20}{'Recall@5':<10}{'MRR':<7}{'Redis':<7}{'%SS vagas':<11}"
          f"{'repeat':<8}{'overlap':<9}guarda")
    for cond in CONDICOES:
        r = res.resumo.get(cond["rotulo"])
        if not r:
            continue
        print(f"    {cond['rotulo']:<20}{r['recall5']*100:>5.1f}%{'':<4}{r['mrr']:.3f}  "
              f"{r['redis_hit5']:<7}{r['vaga_ss_top5']*100:>6.1f}%{'':<4}"
              f"{r['repeat_rate']*100:>5.1f}%  {r['overlap_frac']*100:>5.1f}%   {r['guarda']}")
    print(line)
    if res.dry_run:
        print("  Revise: alvos, índice, RRF não-vazio. Se ok:")
        print("    EDP_LAB_ARMED=1 python -m edp.lab.exp010        (disparo real, congela)")
        print("    python -m edp.lab.exp010 --score                 (Wilson + veredito §6)")
        print("    python -m edp.lab.exp010 --audit                  (top-5 com bm25/vec scores)")


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        description="Experimento 010 — retrieval híbrido BM25+RRF vs cosine puro (retrieval-quality).")
    p.add_argument("--dry-run", action="store_true",
                   help="prova-no-espelho: encanamento inteiro + retrievers REAIS, sem armar.")
    p.add_argument("--no-record", action="store_true", help="não grava no prontuário.")
    p.add_argument("--score", action="store_true", help="pontua o prontuário (Wilson + veredito §6).")
    p.add_argument("--audit", action="store_true", help="amostra top-5 com bm25/vec scores.")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.score:
        report_010(score_010(only_real=True))
        return 0
    if args.audit:
        audit_010()
        return 0
    try:
        res = run_exp010(dry_run=args.dry_run, record=not args.no_record)
    except RuntimeError as e:
        print(f"\n[RECUSADO/ERRO] {e}")
        return 1
    _print_fire_report(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
