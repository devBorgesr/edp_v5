"""
api.py — FastAPI profissional do EDP v3.
Endpoints completos: process, memory, consolidate, prune, stats, metrics, health.
Suporte a threshold dinâmico, top_k dinâmico, debug mode, profiling mode.
"""
from __future__ import annotations

import time
from typing import Literal
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .config import API_VERSION
from .pipeline import run_pipeline, tokenize, PipelineResult
from .memory import MemoryStore
from .consolidation import consolidate, cluster_entries, explain_cluster
from .filters import classificar_spam, classificar_lixo
from . import metrics as M
from . import cache as C

# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    from .embeddings import get_model
    get_model()
    print(f"[EDP v3 API] Modelo carregado. Pronto.")
    yield

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="EDP v3 — Entropic Decomposition Processing",
    description=(
        "Infraestrutura para memória cognitiva persistente, "
        "compressão semântica e retrieval contextual híbrido."
    ),
    version=API_VERSION,
    lifespan=lifespan,
)

# ── Schemas ───────────────────────────────────────────────────────────────────

class ProcessRequest(BaseModel):
    text:           str   = Field(..., min_length=1)
    question:       str   = Field(..., min_length=1)
    session_id:     str | None = None
    prioridade:     Literal["alta", "media", "baixa"] = "media"
    save_to_memory: bool  = True
    debug:          bool  = False
    profile:        bool  = False

class ProcessResponse(BaseModel):
    context:         list[str]
    context_str:     str
    tokens_original: int
    tokens_final:    int
    reduction_pct:   float
    spam_in_context: bool
    lixo_in_context: bool
    logs:            list[dict]
    memory_retrieved: list[dict] = []
    trace:           list[dict]  = []
    metrics:         dict        = {}
    latency_ms:      float       = 0.0

class MemoryAddRequest(BaseModel):
    session_id: str
    text:       str = Field(..., min_length=1)
    score:      float = Field(default=0.5, ge=0.0, le=1.0)
    prioridade: Literal["alta", "media", "baixa"] = "media"

class MemoryQueryRequest(BaseModel):
    session_id: str
    query:      str = Field(..., min_length=1)
    top_k:      int   = Field(default=5, ge=1, le=50)
    min_score:  float = Field(default=0.20, ge=0.0, le=1.0)
    layers:     list[Literal["working", "episodic", "semantic"]] | None = None

class MemoryEntryOut(BaseModel):
    id:            str
    text:          str
    timestamp:     float
    score_inicial: float
    acessos:       int
    ultimo_acesso: float
    prioridade:    str
    layer:         str = "episodic"
    ranking_score: float = 0.0

class SetPrioridadeRequest(BaseModel):
    session_id:  str
    entry_id:    str
    prioridade:  Literal["alta", "media", "baixa"]

# ── Rotas básicas ─────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")

@app.get("/health")
def health():
    return {"status": "ok", "version": API_VERSION}

# ── Process ───────────────────────────────────────────────────────────────────

@app.post("/process", response_model=ProcessResponse)
def process(req: ProcessRequest):
    """
    Processa texto pelo pipeline EDP v3 completo.
    - Filtra spam/lixo
    - Comprime por relevância semântica
    - Salva na memória episódica (se session_id)
    - Recupera memória relevante multi-camada
    """
    t0 = time.perf_counter()

    # pipeline
    result: PipelineResult = run_pipeline(
        text=req.text,
        question=req.question,
        debug=req.debug,
    )

    ctx = result.context_str
    spam_in = classificar_spam(ctx)[0] if ctx else False
    lixo_in = classificar_lixo(ctx)[0] if ctx else False
    memory_retrieved: list[dict] = []

    if req.session_id:
        mem = MemoryStore(req.session_id)

        if req.save_to_memory:
            for bloco in result.context:
                if bloco.strip():
                    mem.add(bloco, score=0.5, prioridade=req.prioridade)

        raw = mem.retrieve(req.question, top_k=5)
        memory_retrieved = [
            {k: v for k, v in r.items() if k != "embedding"}
            for r in raw
        ]

    latency = round((time.perf_counter() - t0) * 1000, 2)

    return ProcessResponse(
        context=result.context,
        context_str=ctx,
        tokens_original=result.tokens_original,
        tokens_final=result.tokens_final,
        reduction_pct=round(result.reduction_pct, 2),
        spam_in_context=spam_in,
        lixo_in_context=lixo_in,
        logs=result.logs,
        memory_retrieved=memory_retrieved,
        trace=result.trace if req.debug else [],
        metrics=result.metrics if req.profile else {},
        latency_ms=latency,
    )

# ── Memory ────────────────────────────────────────────────────────────────────

@app.post("/memory/add")
def memory_add(req: MemoryAddRequest):
    """Adiciona entrada diretamente na memória episódica."""
    mem   = MemoryStore(req.session_id)
    entry = mem.add(req.text, score=req.score, prioridade=req.prioridade)
    return {"ok": True, "id": entry["id"], "prioridade": entry["prioridade"]}

@app.post("/memory/query", response_model=list[MemoryEntryOut])
def memory_query(req: MemoryQueryRequest):
    """Recupera entradas de memória relevantes para uma query."""
    mem = MemoryStore(req.session_id)
    raw = mem.retrieve(
        req.query,
        top_k=req.top_k,
        min_score=req.min_score,
        layers=req.layers,
    )
    return [
        MemoryEntryOut(**{k: v for k, v in r.items() if k != "embedding"})
        for r in raw
    ]

@app.post("/memory/consolidate/{session_id}")
def memory_consolidate(
    session_id: str,
    threshold: float = Query(default=None, ge=0.0, le=1.0, description="Threshold de similaridade para clustering"),
    promote_threshold: int = Query(default=3, ge=1, description="Mín. de acessos para promoção a semântica"),
):
    """
    Roda consolidação semântica na memória episódica.
    Clusters com sim >= threshold são fundidos.
    Entradas com acessos >= promote_threshold vão para SemanticMemory.
    """
    mem    = MemoryStore(session_id)
    result = consolidate(mem, threshold=threshold, promote_threshold=promote_threshold)
    return result

@app.post("/memory/prune/{session_id}")
def memory_prune(
    session_id: str,
    keep: int = Query(default=None, ge=1, description="Máximo de entradas a manter"),
):
    """Poda memória episódica mantendo as entradas com maior score."""
    mem = MemoryStore(session_id)
    return mem.prune(keep=keep)

@app.get("/memory/stats/{session_id}")
def memory_stats(session_id: str):
    """Estatísticas da memória por camada."""
    mem = MemoryStore(session_id)
    return mem.stats()

@app.get("/memory/{session_id}/report")
def memory_report(session_id: str):
    """Relatório textual da memória episódica."""
    mem = MemoryStore(session_id)
    return {"report": mem.report()}

@app.get("/memory/{session_id}/entries")
def memory_entries(session_id: str, layer: str = "episodic"):
    """Lista entradas de uma camada específica."""
    mem = MemoryStore(session_id)
    if layer == "semantic":
        entries = mem.semantic.entries
    else:
        entries = mem.episodic.entries
    return {
        "session_id": session_id,
        "layer":      layer,
        "count":      len(entries),
        "entries":    [{k: v for k, v in e.items() if k != "embedding"} for e in entries],
    }

@app.get("/memory/{session_id}/clusters")
def memory_clusters(
    session_id: str,
    threshold: float = Query(default=None, ge=0.0, le=1.0),
):
    """Visualiza clusters semânticos sem aplicar merge."""
    mem        = MemoryStore(session_id)
    entries    = mem.episodic.entries
    embeddings = mem.episodic.all_embeddings()
    if not entries:
        return {"clusters": []}
    clusters = cluster_entries(entries, embeddings, threshold)
    return {
        "n_clusters": len(clusters),
        "threshold":  threshold,
        "clusters":   [explain_cluster(entries, c) for c in clusters],
    }

@app.post("/memory/set-prioridade")
def set_prioridade(req: SetPrioridadeRequest):
    """Atualiza prioridade de uma entrada."""
    mem = MemoryStore(req.session_id)
    try:
        mem.set_prioridade(req.entry_id, req.prioridade)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True, "entry_id": req.entry_id, "prioridade": req.prioridade}

@app.delete("/memory/{session_id}/entry/{entry_id}")
def delete_entry(session_id: str, entry_id: str):
    """Remove entrada específica."""
    mem     = MemoryStore(session_id)
    deleted = mem.delete(entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Entrada não encontrada")
    return {"ok": True, "deleted": entry_id}

@app.delete("/memory/{session_id}")
def delete_session(session_id: str):
    """Remove sessão inteira do disco."""
    deleted = MemoryStore.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return {"ok": True, "deleted_session": session_id}

@app.get("/sessions")
def list_sessions():
    """Lista todas as sessões existentes."""
    return {"sessions": MemoryStore.list_sessions()}

# ── Métricas ──────────────────────────────────────────────────────────────────

@app.get("/metrics")
def get_metrics(from_file: bool = True):
    """Métricas de observabilidade completas."""
    return M.summary(from_file=from_file)

@app.post("/metrics/reset")
def reset_metrics():
    """Zera métricas in-memory (não apaga JSONL)."""
    M.reset()
    return {"ok": True}

@app.get("/cache/stats")
def cache_stats():
    """Estatísticas do cache de embeddings."""
    return C.stats()

@app.post("/cache/evict")
def cache_evict(keep: int = Query(default=None, ge=1)):
    """Evicta entradas LRU do cache."""
    total = C.evict_lru(keep)
    return {"ok": True, "total_before": total}

@app.post("/cache/purge-stale")
def cache_purge_stale():
    """Remove vetores com dimensão diferente da configuração atual."""
    n = C.purge_stale()
    return {"ok": True, "purged": n}

# ── Endpoints do upgrade ───────────────────────────────────────────────────────
@app.get("/memory/{session_id}/graph")
def memory_graph_ep(session_id: str, threshold: float = 0.70):
    from .memory_graph import build_from_entries, graph_stats
    mem = MemoryStore(session_id)
    n   = build_from_entries(mem.episodic.entries, threshold=threshold)
    return {"n_edges": n, "stats": graph_stats()}

@app.get("/memory/{session_id}/hubs")
def memory_hubs(session_id: str, top_k: int = 10):
    from .memory_graph import build_from_entries, get_memory_hubs
    mem = MemoryStore(session_id); build_from_entries(mem.episodic.entries)
    return {"hubs": [{"id": h[0], "degree": h[1], "hub_score": h[2]} for h in get_memory_hubs(top_k)]}

@app.post("/context/build")
def build_context_ep(session_id: str, query: str, top_k: int = 8, max_tokens: int = 600):
    from .context_builder import build_context
    mem = MemoryStore(session_id); r = build_context(query, mem, top_k=top_k, max_tokens=max_tokens)
    return {"context_str": r.context_str, "blocks": r.blocks, "token_count": r.token_count,
            "diversity": r.diversity, "coverage": r.coverage}

@app.get("/analytics/{session_id}/health")
def analytics_health(session_id: str):
    from .analytics import memory_health_report
    return memory_health_report(MemoryStore(session_id))

@app.get("/analytics/{session_id}/distribution")
def analytics_dist(session_id: str):
    from .analytics import memory_distribution
    return memory_distribution(MemoryStore(session_id))

@app.get("/analytics/{session_id}/drift")
def analytics_drift(session_id: str, window: int = 20):
    from .analytics import semantic_drift
    return semantic_drift(MemoryStore(session_id), window=window)

@app.get("/analytics/growth")
def analytics_growth():
    from .analytics import growth_stats
    return growth_stats()

@app.post("/memory/{session_id}/reinforce/{entry_id}")
def reinforce_ep(session_id: str, entry_id: str, boost: float = 0.10):
    mem = MemoryStore(session_id)
    if not mem.reinforce_memory(entry_id, boost=boost): raise HTTPException(404, "Não encontrada")
    return {"ok": True, "entry_id": entry_id}

@app.post("/memory/{session_id}/decay/{entry_id}")
def decay_ep(session_id: str, entry_id: str, factor: float = 0.50):
    mem = MemoryStore(session_id)
    if not mem.decay_memory(entry_id, factor=factor): raise HTTPException(404, "Não encontrada")
    return {"ok": True, "entry_id": entry_id}

@app.post("/memory/{session_id}/update-stats")
def update_stats_ep(session_id: str):
    return MemoryStore(session_id).update_usage_stats()

@app.post("/memory/{session_id}/auto-consolidate")
def auto_consolidate_ep(session_id: str, trigger_size: int = 50):
    from .consolidation import auto_consolidate
    return auto_consolidate(MemoryStore(session_id), trigger_size=trigger_size)

@app.post("/memory/{session_id}/merge-similar")
def merge_similar_ep(session_id: str, threshold: float = 0.90, dry_run: bool = False):
    from .consolidation import merge_similar_memories
    return merge_similar_memories(MemoryStore(session_id), threshold=threshold, dry_run=dry_run)

@app.post("/memory/{session_id}/backup")
def backup_ep(session_id: str):
    from .failsafe import incremental_backup
    return incremental_backup(MemoryStore(session_id))

@app.post("/memory/{session_id}/restore")
def restore_ep(session_id: str, backup_timestamp: int = None):
    from .failsafe import restore_backup
    return restore_backup(MemoryStore(session_id), backup_timestamp)

@app.get("/memory/{session_id}/validate")
def validate_ep(session_id: str):
    from .failsafe import validate_memory_json, detect_corruption
    mem = MemoryStore(session_id)
    ep_ok, ep_e = validate_memory_json(str(mem.episodic.path))
    sm_ok, sm_e = validate_memory_json(str(mem.semantic.path))
    return {"episodic_valid": ep_ok, "episodic_errors": ep_e,
            "semantic_valid": sm_ok, "semantic_errors": sm_e,
            "corruption": detect_corruption(mem)}

@app.get("/cache/memory-sizes")
def cache_mem_sizes():
    return C.cache_sizes()

