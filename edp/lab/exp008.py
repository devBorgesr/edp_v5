"""
edp.lab.exp008 — Encarnação em CODIGO do Experimento 008 (qualidade de retrieval).

Espelha preregistro_experimento_008.md. CONGELADO apos o primeiro disparo.

CATEGORIA NOVA na Bancada: mede a QUALIDADE DO RETRIEVE (o que o retrieve
SELECIONA, ANTES do modelo), nao o COMPORTAMENTO DO MODELO. O LLM NAO entra na
metrica primaria. Por isso NAO passa pelo Rodizio/sampler (que manda ao modelo) —
tem runner proprio que chama o retrieve REAL e pontua Recall@K / MRR.

Pergunta: casar contra cognitive_decisions (concepts/domain) no retrieve recupera
a memoria-alvo no top-K mais vezes que o retrieve atual (so similaridade vetorial)?

Anti-mock (§7 do pre-registro):
  - retrieve REAL: MemoryStore.retrieve (edp/memory.py:1612), nao reimplementado.
  - memorias REAIS: clone read-only do default_cognitive (com embeddings reais).
  - isolamento: retrieve MUTA E PERSISTE (memory.py:871-880) -> roda sobre um
    CLONE numa sessao __lab__ dedicada (isolation.experimental_session), purgada.
    fingerprint anti-vazamento antes/depois. Producao NUNCA e chamada no retrieve.
  - SEM tocar edp/memory.py: o tratamento e uma funcao de re-rank de LEITURA,
    vive 100% aqui.

PURO em relacao a producao: edp/lab importa de edp/, nunca o contrario (INV-5).
"""
from __future__ import annotations

import copy
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("edp.lab.exp008")


# ── Constantes CONGELADAS (§9 do pre-registro) ────────────────────────────────
EXPERIMENTO     = "008"
BETA            = 0.25          # peso do overlap concepts/domain no tratamento
POOL_SIZE       = 100           # candidatos por query (retrieve REAL)
K3              = 3
K5              = 5
MIN_PAIRS       = 15           # abaixo disso o experimento ABORTA
MAX_PAIRS       = 20
MIN_CONCEPTS    = 2            # entry precisa de >=2 concepts para entrar no universo
MIN_SCORE_POOL  = 0.0          # nao corta por threshold — mede ranking puro
LAYERS          = ["episodic"]  # isola o cognitive episodic
QUERY_TEMPLATE  = "continuando nossa conversa sobre {domain}, o que tínhamos concluído?"

STOPWORDS = frozenset({
    "por", "com", "sobre", "para", "que", "the", "and",
    "a", "o", "de", "da", "do", "em", "no", "na", "nossa", "nosso",
    "what", "was", "our",
})

ROTULO_BASELINE  = "baseline"
ROTULO_TRATAMENTO = "tratamento"
ROTULO_CONTROLE   = "tratamento_control_shuffle"

CONDICOES = [
    {"rotulo": ROTULO_BASELINE,  "papel": "retrieve atual (controle)",        "tipo": "baseline"},
    {"rotulo": ROTULO_TRATAMENTO, "papel": "similaridade + concepts/domain",   "tipo": "tratamento"},
    {"rotulo": ROTULO_CONTROLE,   "papel": "CONTROLE NEGATIVO (campo errado)", "tipo": "controle"},
]

# Constante do andaime: marca que este experimento nao usa LLM.
MODELO_LABEL = "retrieval::no-llm"

FIELD_COGNITIVE_DECISIONS = "cognitive_decisions"


# ── Tokenizacao e overlap (CONGELADOS) ────────────────────────────────────────
_TOKEN_SPLIT = re.compile(r"[^0-9a-zA-Zà-úÀ-Ú]+")


def tokens(s: str) -> set:
    """minusculas; split em nao-alfanumerico; remove <3 chars e stopwords."""
    if not s:
        return set()
    out = set()
    for t in _TOKEN_SPLIT.split(s.lower()):
        if len(t) >= 3 and t not in STOPWORDS:
            out.add(t)
    return out


def concept_tokens(cd: Optional[dict]) -> set:
    """C = tokens(concepts) ∪ tokens(domain) de um cognitive_decisions."""
    if not isinstance(cd, dict):
        return set()
    C: set = set()
    dom = cd.get("domain")
    if isinstance(dom, str):
        C |= tokens(dom)
    cs = cd.get("concepts")
    if isinstance(cs, list):
        for c in cs:
            if isinstance(c, str):
                C |= tokens(c)
    return C


def overlap(query_tokens: set, cd: Optional[dict]) -> float:
    """|Q ∩ C| / |C|. 0.0 se a entry nao tem decisions (degrada para baseline)."""
    C = concept_tokens(cd)
    if not C:
        return 0.0
    return len(query_tokens & C) / len(C)


# ── Ground truth: regra CONGELADA (§6) ────────────────────────────────────────
@dataclass
class ProbePair:
    query:      str
    target_id:  str
    domain:     str
    key_assertion: str


def build_dataset(entries: List[dict]) -> List[ProbePair]:
    """Materializa os pares (query -> target_id) das memorias REAIS por regra.

    Universo: entries com cognitive_decisions valido (domain nao-vazio,
    >=MIN_CONCEPTS concepts, id e embedding presentes). Ordena por id, pega no
    maximo 1 alvo por domain (variedade), ate MAX_PAIRS. Query via template fixo
    usando SO o domain. NAO cita concepts nem o texto da memoria.
    """
    universo: List[dict] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        if not e.get("id"):
            continue
        if e.get("embedding") is None:
            continue
        cd = e.get(FIELD_COGNITIVE_DECISIONS)
        if not isinstance(cd, dict):
            continue
        dom = (cd.get("domain") or "").strip()
        cs = cd.get("concepts")
        if not dom:
            continue
        if not isinstance(cs, list) or len([c for c in cs if isinstance(c, str) and c.strip()]) < MIN_CONCEPTS:
            continue
        universo.append(e)

    universo.sort(key=lambda e: str(e.get("id")))

    pares: List[ProbePair] = []
    dominios_vistos: set = set()
    for e in universo:
        cd = e[FIELD_COGNITIVE_DECISIONS]
        dom = cd["domain"].strip()
        dom_key = dom.lower()
        if dom_key in dominios_vistos:
            continue
        dominios_vistos.add(dom_key)
        pares.append(ProbePair(
            query=QUERY_TEMPLATE.format(domain=dom),
            target_id=str(e["id"]),
            domain=dom,
            key_assertion=str(cd.get("key_assertion", "")),
        ))
        if len(pares) >= MAX_PAIRS:
            break
    return pares


# ── Re-rank experimental (TRATAMENTO) — so leitura, so no lab ──────────────────
def _decisions_for_index(pool: List[dict], i: int, shuffle: bool) -> Optional[dict]:
    """cognitive_decisions usado para a entry i. shuffle=True -> usa o do vizinho
    (i+1) mod N (campo ERRADO) — o controle negativo de validade."""
    n = len(pool)
    if n == 0:
        return None
    idx = ((i + 1) % n) if shuffle else i
    return pool[idx].get(FIELD_COGNITIVE_DECISIONS)


def rerank(pool: List[dict], query: str, *, shuffle: bool) -> List[dict]:
    """Re-ordena o MESMO pool por treatment_score = ranking_score + BETA*overlap.
    Retorna nova lista (nao muta o pool). Estavel: empA desempata por ranking_score."""
    Q = tokens(query)
    scored: List[Tuple[float, float, int, dict]] = []
    for i, item in enumerate(pool):
        base = float(item.get("ranking_score", 0.0))
        cd = _decisions_for_index(pool, i, shuffle)
        ov = overlap(Q, cd)
        treat = base + BETA * ov
        # guarda o treatment_score e o overlap no item retornado (exemplos/§4)
        enriched = dict(item)
        enriched["_overlap"] = round(ov, 4)
        enriched["_treatment_score"] = round(treat, 4)
        scored.append((treat, base, i, enriched))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [t[3] for t in scored]


# ── Serializacao dos exemplos (§4: registrar o que veio, certo e errado) ──────
def _exemplo(item: dict, posicao: int, target_id: str) -> dict:
    cd = item.get(FIELD_COGNITIVE_DECISIONS) or {}
    return {
        "posicao":          posicao,                      # 1-indexada
        "id":               item.get("id", ""),
        "is_target":        item.get("id") == target_id,
        "ranking_score":    item.get("ranking_score"),
        "treatment_score":  item.get("_treatment_score"),
        "overlap":          item.get("_overlap"),
        "domain":           (cd.get("domain") if isinstance(cd, dict) else None),
        "key_assertion":    (cd.get("key_assertion") if isinstance(cd, dict) else None),
        "text_preview":     (item.get("text") or "")[:120],
    }


def _target_rank(topk: List[dict], target_id: str) -> Optional[int]:
    """Posicao 1-indexada do alvo na lista, ou None se ausente."""
    for pos, item in enumerate(topk, 1):
        if item.get("id") == target_id:
            return pos
    return None


def _metricas_da_lista(topk: List[dict], target_id: str) -> dict:
    rank = _target_rank(topk[:K5], target_id)
    return {
        "target_rank":      rank,                                   # None se fora do top-5
        "recall_at_3":      1 if (rank is not None and rank <= K3) else 0,
        "recall_at_5":      1 if (rank is not None and rank <= K5) else 0,
        "reciprocal_rank":  (1.0 / rank) if (rank is not None and rank <= K5) else 0.0,
    }


# ── Carregamento das memorias reais (clone READ-ONLY) ─────────────────────────
def load_cognitive_clone(prod_session: str = "default") -> List[dict]:
    """Clone READ-ONLY das entries do scope cognitive de producao — SEM chamar
    retrieve, SEM mutar, SEM salvar.

    DISCO PRIMEIRO (verdade da casa): le episodic.json direto — garantidamente
    nao-mutante e o mesmo source que health_index e a rota /cognitive_decisions
    consultam (entries persistidas, com embedding + cognitive_decisions). Evita
    de proposito construir get_memory(), cujo __init__ roda _migrate_legacy_
    session_files (memory.py:1386) — um possivel WRITE em producao.

    Fallback (so se o disco falhar): registry em RAM.
    """
    # 1) DISCO — read-only, nao-mutante (path de cognitive_fingerprint)
    try:
        import json
        from .isolation import _sessions_root
        p = _sessions_root() / f"{prod_session}_cognitive" / "episodic.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("entries", [])
            if isinstance(data, list) and data:
                return copy.deepcopy(data)
    except Exception as e:
        logger.warning("[exp008] clone do disco falhou (%s); tentando registry", e)
    # 2) registry (fallback; instancia em RAM, shape com embeddings)
    try:
        from ..runtime.registry import get_memory, is_valid
        mem = get_memory(prod_session)
        if is_valid(mem):
            cog = getattr(mem, "_cognitive_view", None)
            if cog is not None:
                return copy.deepcopy(list(cog.episodic.entries))
    except Exception as e:
        logger.warning("[exp008] registry clone falhou: %s", e)
    return []


# ── Runner do experimento (categoria retrieval — runner proprio) ──────────────
@dataclass
class Exp008Result:
    n_pares:      int = 0
    leak_ok:      Optional[bool] = None
    dry_run:      bool = True
    armed:        bool = False
    aborted:      Optional[str] = None
    runs_gravados: int = 0
    fingerprint_before: dict = field(default_factory=dict)
    fingerprint_after:  dict = field(default_factory=dict)
    pares:        List[ProbePair] = field(default_factory=list)
    # resumo rapido em memoria (a analise CANONICA e pos-coleta via scorer)
    resumo:       Dict[str, dict] = field(default_factory=dict)


def run_exp008(
    *,
    prod_session: str = "default",
    dry_run: bool = False,
    record: bool = True,
) -> Exp008Result:
    """Roda o Experimento 008. Para CADA par (query->alvo):
      pool = MemoryStore.retrieve REAL (sobre o clone isolado)
      baseline = pool[:K]; tratamento/controle = rerank(pool)[:K]
      grava 1 run por (query x condicao) no prontuario, com exemplos.

    Trava de armado (§8): disparo REAL exige EDP_LAB_ARMED=1. dry_run=True roda
    o encanamento inteiro (incl. retrieve REAL) sem armar e marca dry_run=True.

    Isolamento (§7): clone read-only -> sessao __lab__ dedicada -> retrieve no
    clone -> purga. fingerprint antes/depois prova no-leak (INV-5).
    """
    from .isolation import experimental_session, cognitive_fingerprint, verify_no_leak

    res = Exp008Result(dry_run=dry_run)

    # Gate de armado (so o disparo REAL).
    armed = os.environ.get("EDP_LAB_ARMED", "0").strip().lower() in ("1", "true", "yes", "on")
    res.armed = armed
    if not dry_run and not armed:
        raise RuntimeError(
            "RECUSADO: disparo real do exp008 exige EDP_LAB_ARMED=1. "
            "Use a prova-no-espelho (python -m edp.lab.exp008 --dry-run) primeiro."
        )

    # Fingerprint ANTES (INV-5).
    res.fingerprint_before = cognitive_fingerprint(prod_session)

    # Clone read-only das memorias reais + dataset por regra congelada.
    entries = load_cognitive_clone(prod_session)
    pares = build_dataset(entries)
    res.pares = pares
    res.n_pares = len(pares)
    if len(pares) < MIN_PAIRS:
        res.aborted = (
            f"decisions insuficientes: {len(pares)} pares < MIN_PAIRS={MIN_PAIRS}. "
            f"Rode o cognitive_decisions extractor mais antes do 008."
        )
        res.fingerprint_after = cognitive_fingerprint(prod_session)
        res.leak_ok = verify_no_leak(res.fingerprint_before, res.fingerprint_after)
        logger.warning("[exp008] ABORTADO: %s", res.aborted)
        return res

    store = None
    if record:
        from .prontuario import get_prontuario
        store = get_prontuario()

    andaime_base = _gather_andaime(dry_run)

    # resumo em memoria (sanity; analise canonica e o scorer pos-coleta)
    acc = {c["rotulo"]: {"r3": 0, "r5": 0, "rr": 0.0, "n": 0} for c in CONDICOES}

    with experimental_session(scope="cognitive", purge=True) as lab:
        mem = lab.memory
        try:
            mem.set_scope("cognitive")
        except Exception:
            pass
        # injeta o clone na sessao isolada — toda mutacao do retrieve cai aqui.
        # Fix A: o retrieve lê de mem._episodic.entries, não de mem.entries.
        # Injetamos nos dois para garantir que o retrieve REAL veja os dados
        # com cognitive_decisions. (Descoberto via inspect_cognitive.py)
        _clone = copy.deepcopy(entries)
        mem.entries = _clone
        # Injeta na estrutura interna onde o retrieve busca:
        if hasattr(mem, "_episodic") and hasattr(mem._episodic, "entries"):
            mem._episodic.entries = _clone
        elif hasattr(mem, "_cognitive") and hasattr(mem._cognitive, "episodic"):
            mem._cognitive.episodic.entries = _clone

        for qi, par in enumerate(pares):
            # POOL via retrieve REAL (sobre o clone). min_score=0.0 -> ranking puro.
            pool = mem.retrieve(par.query, top_k=POOL_SIZE, min_score=MIN_SCORE_POOL, layers=LAYERS)

            # Fix B: session_summary tem embedding próximo de qualquer query de
            # "continuação de conversa" — domina o pool e empurra alvos reais
            # para fora do top-K. Filtramos ANTES do re-rank (decisão de método
            # documentada no pré-registro: session_summary não é memória-alvo).
            pool = [
                e for e in pool
                if e.get("source_type") != "session_summary"
                and not str(e.get("text", "")).startswith("[session_summary]")
            ]

            listas = {
                ROTULO_BASELINE:   list(pool),                          # ja ordenado por ranking_score
                ROTULO_TRATAMENTO: rerank(pool, par.query, shuffle=False),
                ROTULO_CONTROLE:   rerank(pool, par.query, shuffle=True),
            }

            for cond in CONDICOES:
                rot = cond["rotulo"]
                topk = listas[rot][:K5]
                met = _metricas_da_lista(topk, par.target_id)
                exemplos = [_exemplo(it, pos, par.target_id) for pos, it in enumerate(topk, 1)]

                acc[rot]["n"]  += 1
                acc[rot]["r3"] += met["recall_at_3"]
                acc[rot]["r5"] += met["recall_at_5"]
                acc[rot]["rr"] += met["reciprocal_rank"]

                if record and store is not None:
                    andaime = dict(andaime_base)
                    andaime["condicao_rotulo"] = rot
                    andaime["condicao_papel"]  = cond["papel"]
                    andaime["query_idx"]       = qi
                    try:
                        store.record_run(
                            modelo=MODELO_LABEL,
                            formato_id=f"retrieval_{rot}",
                            andaime=andaime,
                            janela_enviada=par.query,                # o que foi "enviado" ao retrieve
                            secoes={
                                "query":         par.query,
                                "target_id":     par.target_id,
                                "target_domain": par.domain,
                                "target_key_assertion": par.key_assertion,
                                "pool_size":     len(pool),
                            },
                            respostas=exemplos,                       # EXEMPLOS: o que veio, certo/errado
                            scope="__lab__cognitive",
                            metricas={**met, "condicao": rot, "experimento": EXPERIMENTO},
                        )
                        res.runs_gravados += 1
                    except Exception as e:
                        logger.warning("[exp008] record_run falhou (q%d/%s): %s", qi, rot, e)

    # Fingerprint DEPOIS + veredito de no-leak (INV-5).
    res.fingerprint_after = cognitive_fingerprint(prod_session)
    res.leak_ok = verify_no_leak(res.fingerprint_before, res.fingerprint_after)

    for rot, a in acc.items():
        n = max(a["n"], 1)
        res.resumo[rot] = {
            "n":         a["n"],
            "recall_at_3": a["r3"] / n,
            "recall_at_5": a["r5"] / n,
            "mrr":         a["rr"] / n,
        }
    return res


def _gather_andaime(dry_run: bool) -> dict:
    """Andaime real (Bug 4) reusando run_once.gather_andaime; marca experimento."""
    try:
        from .run_once import gather_andaime
        a = gather_andaime(MODELO_LABEL)
    except Exception:
        a = {"model_version": MODELO_LABEL, "fonte_andaime": "exp008"}
    a["experimento"] = EXPERIMENTO
    a["dry_run"] = bool(dry_run)
    a["beta"] = BETA
    a["pool_size"] = POOL_SIZE
    return a


# ══════════════════════════════════════════════════════════════════════════════
# Scorer pos-coleta (analise CANONICA — le o prontuario, sem retrieve, sem custo)
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class CondRetrieval:
    rotulo: str
    papel: str
    n: int
    r3: int
    r5: int
    recall_at_3: float
    recall_at_5: float
    r3_lo: float
    r3_hi: float
    r5_lo: float
    r5_hi: float
    mrr: float


@dataclass
class Retrieval008:
    n_registros_total: int = 0
    n_reais: int = 0
    n_dry_run: int = 0
    condicoes: List[CondRetrieval] = field(default_factory=list)
    # pareado tratamento vs baseline (por query): posicao do alvo
    pareado_subiu: int = 0
    pareado_desceu: int = 0
    pareado_igual: int = 0
    # veredito (criterio §5, travado)
    setup_valido: Optional[bool] = None       # controle shuffle NAO supera baseline
    h1_confirmada: Optional[bool] = None       # Recall@5 trat > base, ICs separados
    suporte_mrr_e_pareado: Optional[bool] = None


def _por_condicao(store, only_real: bool):
    """Le o prontuario (exp 008), agrega por condicao e guarda, por query, a
    posicao do alvo em baseline e tratamento (para o sinal pareado)."""
    from .scorer import _wilson  # reuso da regua de Wilson da Bancada

    tot = dry = reais = 0
    agg: Dict[str, dict] = {}
    # rank do alvo por (query_idx, condicao) para o pareado
    rank_por_query: Dict[int, dict] = {}

    for row in store.query_index():
        tot += 1
        blob = store.get_blob(row.get("run_id"))
        if not blob:
            continue
        a = blob.get("andaime", {}) or {}
        if a.get("experimento") != EXPERIMENTO:
            continue
        is_dry = bool(a.get("dry_run", False))
        if is_dry:
            dry += 1
            if only_real:
                continue
        else:
            reais += 1
        rot = a.get("condicao_rotulo") or blob.get("formato_id", "?")
        met = (row.get("metricas") or {})
        slot = agg.setdefault(rot, {
            "papel": a.get("condicao_papel", ""), "n": 0, "r3": 0, "r5": 0, "rr": 0.0,
        })
        slot["n"]  += 1
        slot["r3"] += int(met.get("recall_at_3", 0) or 0)
        slot["r5"] += int(met.get("recall_at_5", 0) or 0)
        slot["rr"] += float(met.get("reciprocal_rank", 0.0) or 0.0)

        qi = a.get("query_idx")
        if qi is not None:
            tr = met.get("target_rank")
            rank_por_query.setdefault(qi, {})[rot] = tr

    return agg, rank_por_query, tot, dry, reais, _wilson


def score_retrieval_008(store=None, only_real: bool = True) -> Retrieval008:
    """Analise do Exp 008: Recall@3/@5 (Wilson) e MRR por condicao; sinal pareado
    tratamento vs baseline; veredito travado (§5): validade pelo controle shuffle,
    depois H1 por Recall@5 com ICs separados."""
    from .prontuario import get_prontuario
    store = store or get_prontuario()
    res = Retrieval008()

    agg, rank_por_query, tot, dry, reais, _wilson = _por_condicao(store, only_real)
    res.n_registros_total, res.n_dry_run, res.n_reais = tot, dry, reais

    por_rot: Dict[str, CondRetrieval] = {}
    for cond in CONDICOES:
        rot = cond["rotulo"]
        slot = agg.get(rot)
        if not slot:
            continue
        n = slot["n"]
        r3, r5 = slot["r3"], slot["r5"]
        r3lo, r3hi = _wilson(r3, n)
        r5lo, r5hi = _wilson(r5, n)
        c = CondRetrieval(
            rotulo=rot, papel=slot["papel"], n=n, r3=r3, r5=r5,
            recall_at_3=(r3 / n if n else 0.0), recall_at_5=(r5 / n if n else 0.0),
            r3_lo=r3lo, r3_hi=r3hi, r5_lo=r5lo, r5_hi=r5hi,
            mrr=(slot["rr"] / n if n else 0.0),
        )
        res.condicoes.append(c)
        por_rot[rot] = c

    # sinal pareado: posicao do alvo em tratamento vs baseline (menor = melhor)
    for qi, ranks in rank_por_query.items():
        rb = ranks.get(ROTULO_BASELINE)
        rt = ranks.get(ROTULO_TRATAMENTO)
        # None (fora do top-5) conta como pior que qualquer posicao -> usa K5+1
        nb = rb if rb is not None else (K5 + 1)
        nt = rt if rt is not None else (K5 + 1)
        if nt < nb:
            res.pareado_subiu += 1
        elif nt > nb:
            res.pareado_desceu += 1
        else:
            res.pareado_igual += 1

    base = por_rot.get(ROTULO_BASELINE)
    trat = por_rot.get(ROTULO_TRATAMENTO)
    ctrl = por_rot.get(ROTULO_CONTROLE)

    # §5.1 validade: controle shuffle NAO pode superar baseline (ICs separados)
    if base is not None and ctrl is not None:
        res.setup_valido = not (ctrl.r5_lo > base.r5_hi)
    elif base is not None:
        res.setup_valido = True  # sem controle no dado; nao bloqueia (informativo)

    # §5.2 H1: Recall@5 tratamento > baseline com ICs de Wilson separados
    if base is not None and trat is not None:
        res.h1_confirmada = bool(res.setup_valido) and (trat.r5_lo > base.r5_hi)
        # §5.3 suporte
        mrr_ok = trat.mrr > base.mrr
        pareado_ok = res.pareado_subiu > res.pareado_desceu
        res.suporte_mrr_e_pareado = bool(mrr_ok and pareado_ok)

    return res


def report_008(res: Retrieval008) -> None:
    print("\n" + "=" * 70)
    print("SCORER — Experimento 008  (qualidade de retrieval: concepts/domain)")
    print("=" * 70)
    print(f"  registros: {res.n_registros_total} (reais={res.n_reais} | dry_run={res.n_dry_run})\n")
    print(f"  {'condicao':<28}{'n':<4}{'Recall@3':<22}{'Recall@5':<22}{'MRR'}")
    ordem = [c["rotulo"] for c in CONDICOES]
    por = {c.rotulo: c for c in res.condicoes}
    for rot in ordem:
        c = por.get(rot)
        if not c:
            continue
        r3 = f"{c.recall_at_3*100:>4.0f}% [{c.r3_lo:.2f},{c.r3_hi:.2f}]"
        r5 = f"{c.recall_at_5*100:>4.0f}% [{c.r5_lo:.2f},{c.r5_hi:.2f}]"
        print(f"  {c.rotulo:<28}{c.n:<4}{r3:<22}{r5:<22}{c.mrr:.3f}")

    print(f"\n  PAREADO (alvo subiu/desceu no tratamento vs baseline, por query):")
    print(f"    subiu={res.pareado_subiu}  desceu={res.pareado_desceu}  igual={res.pareado_igual}")

    print(f"\n  VEREDITO (criterio travado no pre-registro §5):")
    if res.setup_valido is False:
        print("    controle shuffle SUPEROU baseline (ICs separados) -> setup SUSPEITO.")
        print("    O ganho seria estrutural (re-rank), nao semantico. NENHUM achado afirmado.")
    else:
        print("    controle negativo OK (shuffle nao supera baseline) -> setup VALIDO.")
        if res.h1_confirmada:
            print("    --> H1 CONFIRMADA: casar contra concepts/domain melhora o Recall@5")
            print("        (IC de Wilson do tratamento separado do baseline).")
        else:
            print("    --> H1 NAO confirmada: ICs de Recall@5 se tocam (ou tratamento nao > baseline).")
            print("        H0 nao rejeitada neste store/n. Dado valido, efeito nao provado.")
        if res.suporte_mrr_e_pareado is not None:
            print(f"    suporte secundario (MRR maior E pareado positivo): {res.suporte_mrr_e_pareado}")
    print("=" * 70)
    print("  (Exemplos por query — quais memorias vieram, certas e erradas — no blob")
    print("   do prontuario, campo 'respostas'. Use --audit para amostrar.)")


def audit_008(store=None, n_por_query: int = 5):
    """Mostra, por query (reais), o top-K de baseline vs tratamento, marcando o
    alvo. Texto/dominio integrais — para VER se o tratamento trouxe a memoria certa."""
    from .prontuario import get_prontuario
    store = store or get_prontuario()
    # agrupa por query_idx -> condicao -> exemplos
    por_query: Dict[int, dict] = {}
    metas: Dict[int, dict] = {}
    for row in store.query_index():
        blob = store.get_blob(row.get("run_id"))
        if not blob:
            continue
        a = blob.get("andaime", {}) or {}
        if a.get("experimento") != EXPERIMENTO or a.get("dry_run"):
            continue
        qi = a.get("query_idx")
        rot = a.get("condicao_rotulo")
        if qi is None or rot is None:
            continue
        por_query.setdefault(qi, {})[rot] = blob.get("respostas", []) or []
        metas[qi] = blob.get("secoes", {}) or {}

    print("\n" + "=" * 70)
    print("AUDITORIA — Exp 008, top-K por query (baseline vs tratamento)")
    print("=" * 70)
    for qi in sorted(por_query):
        m = metas.get(qi, {})
        print(f"\n── query #{qi}: {m.get('query','?')}")
        print(f"   alvo: id={m.get('target_id','?')} | domain={m.get('target_domain','?')}")
        print(f"   key_assertion: {m.get('target_key_assertion','')}")
        for rot in (ROTULO_BASELINE, ROTULO_TRATAMENTO):
            exemplos = por_query[qi].get(rot, [])[:n_por_query]
            print(f"   [{rot}]")
            for ex in exemplos:
                mark = "  <<< ALVO" if ex.get("is_target") else ""
                print(f"     {ex.get('posicao')}. id={ex.get('id')} "
                      f"score={ex.get('ranking_score')} treat={ex.get('treatment_score')} "
                      f"ov={ex.get('overlap')} dom={ex.get('domain')}{mark}")
    print("\n" + "=" * 70)
    return por_query


# ── CLI / prova-no-espelho ────────────────────────────────────────────────────
def _print_pares(res: Exp008Result) -> None:
    print("\n" + "=" * 70)
    print(f"EXP 008 — pares query -> alvo  (n={res.n_pares})")
    print("=" * 70)
    for i, p in enumerate(res.pares):
        print(f"  #{i:<2} alvo={p.target_id}")
        print(f"      domain        : {p.domain}")
        print(f"      key_assertion : {p.key_assertion[:80]}")
        print(f"      query         : {p.query}")
    print("=" * 70)


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Experimento 008 — qualidade de retrieval (concepts/domain).")
    p.add_argument("--dry-run", action="store_true",
                   help="prova-no-espelho: encanamento + retrieve REAL, sem armar, marca dry_run=True.")
    p.add_argument("--prod-session", default="default", help="sessao de producao a clonar (read-only).")
    p.add_argument("--no-record", action="store_true", help="nao grava no prontuario (so imprime).")
    p.add_argument("--score", action="store_true", help="pontua o prontuario (Recall/MRR), sem retrieve.")
    p.add_argument("--audit", action="store_true", help="amostra os top-K por query do prontuario.")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.score:
        report_008(score_retrieval_008(only_real=True))
        return 0
    if args.audit:
        audit_008()
        return 0

    try:
        res = run_exp008(
            prod_session=args.prod_session,
            dry_run=args.dry_run,
            record=not args.no_record,
        )
    except RuntimeError as e:
        print(f"\n[RECUSADO/ERRO] {e}")
        return 1

    _print_pares(res)
    print("\n" + "=" * 70)
    print("RESULTADO DO DISPARO (sanity em memoria — analise canonica: --score)")
    print("=" * 70)
    print(f"  modo            : {'DRY-RUN (prova-no-espelho)' if res.dry_run else 'REAL (armado)'}")
    print(f"  armado          : EDP_LAB_ARMED={'1' if res.armed else '0'}")
    print(f"  pares           : {res.n_pares} (min exigido={MIN_PAIRS})")
    if res.aborted:
        print(f"  ABORTADO        : {res.aborted}")
    print(f"  runs gravados   : {res.runs_gravados}")
    print(f"  retrieve REAL   : MemoryStore.retrieve chamado sobre clone isolado "
          f"({res.n_pares} queries x pool={POOL_SIZE})")
    print(f"  isolamento      : leak_ok={res.leak_ok} "
          f"(fingerprint cognitive de producao inalterado?)")
    print(f"    before hash   : {res.fingerprint_before.get('hash')}")
    print(f"    after  hash   : {res.fingerprint_after.get('hash')}")
    if not res.aborted:
        print(f"\n  resumo rapido (NAO e o veredito — rode --score para Wilson/veredito):")
        for rot in (ROTULO_BASELINE, ROTULO_TRATAMENTO, ROTULO_CONTROLE):
            r = res.resumo.get(rot)
            if r:
                print(f"    {rot:<28} R@3={r['recall_at_3']*100:>4.0f}%  "
                      f"R@5={r['recall_at_5']*100:>4.0f}%  MRR={r['mrr']:.3f}  (n={r['n']})")
    print("=" * 70)
    if res.dry_run:
        print("  Prova-no-espelho OK. Para o disparo REAL: EDP_LAB_ARMED=1 e rode sem --dry-run.")
        print("  Depois: python -m edp.lab.exp008 --score   (e --audit para os exemplos).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
