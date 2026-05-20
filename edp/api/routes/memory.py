"""edp.api.routes.memory — Endpoints de memória cognitiva."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...runtime import get_memory, is_valid, get_error
from ..schemas import AddMemoryRequest, RetrieveRequest
from ._sidebar import SIDEBAR_CSS, SIDEBAR_JS, render_sidebar

router = APIRouter(tags=["memory"])


@router.get("/memory")
async def list_memory(session_id: str = "default", top_k: int = 20):
    """Lista entradas de memória da sessão + estatísticas."""
    mem = get_memory(session_id)
    if not is_valid(mem):
        return {
            "entries": [],
            "stats":   {},
            "error":   get_error(mem),
        }
    try:
        stats   = mem.stats()
        entries = [
            {
                "id":         e.get("id", ""),
                "text":       e.get("text", "")[:200],
                "prioridade": e.get("prioridade", "media"),
                "acessos":    e.get("acessos", 0),
                "score":      e.get("score_inicial", 0),
            }
            for e in list(mem.episodic.entries)[:top_k]
        ]
        return {"entries": entries, "stats": stats}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/memory/add")
async def add_memory(req: AddMemoryRequest):
    """Adiciona entrada manual à memória."""
    mem = get_memory(req.session_id)
    if not is_valid(mem):
        raise HTTPException(503, get_error(mem) or "MemoryStore indisponível")
    try:
        entry = mem.add(req.text, score=req.score, prioridade=req.prioridade)
        # Garante flush em batch mode
        if hasattr(mem.episodic, "flush"):
            mem.episodic.flush()
        return {"added": True, "id": entry.get("id", "")}
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════
# MEMORY GOVERNANCE SYSTEM
# ═══════════════════════════════════════════════════════════════════════

from fastapi import Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import Optional, List


class MemoryUpdateRequest(BaseModel):
    text:             Optional[str]       = None
    epistemic_status: Optional[str]       = Field(
        None,
        description="verified | hypothesis | stale | contradicted | quarantined"
    )
    confidence:       Optional[float]     = Field(None, ge=0.0, le=1.0)
    prioridade:       Optional[str]       = None
    source_type:      Optional[str]       = Field(
        None,
        description="user_input | llm_response | meta_conversation | session_summary | external | unknown"
    )
    tags:             Optional[List[str]] = None
    note:             Optional[str]       = None


class BatchDeleteRequest(BaseModel):
    filter_status: Optional[str] = None
    filter_source: Optional[str] = None
    confirm:       bool          = False


@router.get("/memory/list")
async def memory_list(
    session_id:      str           = "default",
    limit:           int           = Query(100, ge=1, le=500),
    offset:          int           = Query(0, ge=0),
    filter_status:   Optional[str] = None,
    filter_source:   Optional[str] = None,
    search:          Optional[str] = None,
    layer:           str           = Query("episodic", description="episodic | semantic | all"),
):
    """
    Lista entries de memória com filtros e paginação. SEM embedding (payload grande).

    Filtros:
      - filter_status: verified | hypothesis | stale | contradicted | quarantined
      - filter_source: substring do source (e.g., 'llm', 'user', 'tool')
      - search: substring no texto (case-insensitive)
      - layer: episodic (default) | semantic | all
    """
    if layer not in ("episodic", "semantic", "all"):
        raise HTTPException(400, f"layer invalido: '{layer}'. Use 'episodic', 'semantic' ou 'all'.")
    mem = get_memory(session_id)
    if not is_valid(mem):
        raise HTTPException(503, get_error(mem) or "MemoryStore indisponível")
    try:
        if layer == "episodic":
            entries = mem.episodic.list_entries(
                limit=limit, offset=offset,
                filter_status=filter_status, filter_source=filter_source, search=search,
            )
            # Garante campo layer no payload
            for e in entries:
                e.setdefault("layer", "episodic")
            total_pool = len(mem.episodic)
        elif layer == "semantic":
            # SemanticMemory pode não ter list_entries; filtra in-memory
            sem_entries = list(mem.semantic.entries)
            # Aplica filtros básicos manualmente (mesma semântica)
            entries = _filter_entries_manual(
                sem_entries, filter_status, filter_source, search, limit, offset
            )
            for e in entries:
                e["layer"] = "semantic"
            total_pool = len(mem.semantic.entries)
        else:  # all
            epi = mem.episodic.list_entries(
                limit=10**6, offset=0,
                filter_status=filter_status, filter_source=filter_source, search=search,
            )
            for e in epi:
                e.setdefault("layer", "episodic")
            sem = _filter_entries_manual(
                list(mem.semantic.entries), filter_status, filter_source, search, 10**6, 0
            )
            for e in sem:
                e["layer"] = "semantic"
            combined = epi + sem
            entries  = combined[offset:offset + limit]
            total_pool = len(mem.episodic) + len(mem.semantic.entries)

        return {
            "entries":       entries,
            "count":         len(entries),
            "total":         total_pool,
            "layer":         layer,
            "filter_status": filter_status,
            "filter_source": filter_source,
            "search":        search,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


def _filter_entries_manual(
    entries: list, filter_status, filter_source, search, limit, offset
):
    """Filtro in-memory para SemanticMemory (que não tem list_entries)."""
    out = []
    s_lower = (search or "").lower().strip()
    src_low = (filter_source or "").lower().strip()
    for e in entries:
        if filter_status and e.get("epistemic_status", "hypothesis") != filter_status:
            continue
        if src_low and src_low not in str(e.get("source", "")).lower():
            continue
        if s_lower:
            text = str(e.get("text", "")) + " " + str(e.get("Q", "")) + " " + str(e.get("A", ""))
            if s_lower not in text.lower():
                continue
        # Cópia sem embedding (consistente com episodic.list_entries)
        copy = {k: v for k, v in e.items() if k != "embedding"}
        out.append(copy)
    return out[offset:offset + limit]


@router.get("/memory/stats")
async def memory_stats(session_id: str = "default"):
    """Estatísticas agregadas (por status, total, etc.)."""
    mem = get_memory(session_id)
    if not is_valid(mem):
        raise HTTPException(503, get_error(mem) or "MemoryStore indisponível")
    try:
        return {
            "total":     len(mem.episodic),
            "by_status": mem.episodic.stats_by_status(),
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/memory/trajectory")
async def memory_trajectory(session_id: str = "default"):
    """
    Retorna grafo derivacional sobre memórias episódicas.

    Sprint 1 do roadmap fractal: mapeia a geometria do pensamento.
    - Raízes: ideias-mãe
    - Folhas: conclusões recentes
    - Abandonados: ramos não retomados há >7 dias
    - Loops: clusters revisitados várias vezes
    """
    try:
        from ...trajectory import build_trajectory
    except Exception as e:
        raise HTTPException(500, f"trajectory indisponível: {e}")

    mem = get_memory(session_id)
    if not is_valid(mem):
        raise HTTPException(503, "memory indisponível")

    result = build_trajectory(mem)
    return result


@router.get("/memory/retrieve_debug")
async def retrieve_debug(
    query: str,
    session_id: str = "default",
    top_k: int = 10,
    min_score: float = 0.05,
):
    """
    Debug do retrieval: roda a query e mostra TODAS as memórias encontradas
    com scores, sem aplicar multiplicadores epistêmicos.

    Útil para investigar por que uma memória esperada NÃO está sendo trazida.

    Diferente do retrieve normal usado no chat (que aplica multiplicadores
    e top-5), aqui retornamos top_k=10 com min_score baixo (0.05) para ver
    o gradiente completo.
    """
    mem = get_memory(session_id)
    if not is_valid(mem):
        raise HTTPException(503, "memory indisponível")

    try:
        results = mem.retrieve(
            query=query,
            top_k=top_k,
            min_score=min_score,
        )
        # Formata para resposta legível
        formatted = []
        for r in results:
            formatted.append({
                "id":              r.get("id"),
                "ranking_score":   r.get("ranking_score"),
                "text_snippet":    (r.get("text") or "")[:120],
                "source_type":     r.get("source_type"),
                "status":          r.get("epistemic_status", "hypothesis"),
                "acessos":         r.get("acessos", 0),
                "timestamp":       r.get("timestamp"),
            })
        return {
            "query":      query,
            "session_id": session_id,
            "min_score":  min_score,
            "n_results":  len(formatted),
            "results":    formatted,
        }
    except Exception as e:
        logger.exception("[retrieve_debug] falhou")
        raise HTTPException(500, str(e))


@router.post("/memory/route_test")
async def route_test(text: str, previous_model: Optional[str] = None):
    """
    Testa o roteador de modelo com texto arbitrário.

    Útil para validar regras antes de usar no chat.
    """
    try:
        from ...model_router import route_model, format_router_badge
    except Exception as e:
        raise HTTPException(500, f"model_router indisponível: {e}")

    result = route_model(text, previous_model=previous_model)
    result["badge"] = format_router_badge(result)
    return result


@router.post("/memory/affect_test")
async def memory_affect_test(
    text: str,
    session_id: str = "default",
):
    """
    Testa a calibração afetiva com um texto qualquer.

    Não persiste. Apenas mostra o que o sistema detectaria.
    Útil para você (admin) calibrar/auditar as heurísticas.
    """
    try:
        from ...affective_calibration import (
            calibrate_affect, format_calibration_for_prompt,
        )
    except Exception as e:
        raise HTTPException(500, f"affective_calibration indisponível: {e}")

    mem = get_memory(session_id)
    mem_obj = mem if is_valid(mem) else None

    result = calibrate_affect(text, mem_obj)
    result["formatted_prompt_injection"] = format_calibration_for_prompt(result)
    return result


@router.post("/memory/summarize_session")
async def memory_summarize_session(session_id: str = "default"):
    """
    Força geração de resumo da sessão atual.

    Útil para teste/debug. Em uso normal, o resumo é gerado automaticamente
    ao desconectar do websocket.

    Returns: {summary, label, reused, entry_id} ou {ok: False, reason}
    """
    from ...session_summary import generate_session_summary
    from ...runtime import get_runtime as _gr

    mem = get_memory(session_id)
    if not is_valid(mem):
        raise HTTPException(503, "memory indisponível")

    rt = _gr(session_id)
    if not is_valid(rt):
        raise HTTPException(503, "runtime indisponível")

    result = generate_session_summary(mem, rt, session_id=session_id)
    if not result:
        return {"ok": False, "reason": "sessão curta, LLM não conectado, ou erro interno"}
    return {"ok": True, **result}


@router.get("/memory/topics")
async def memory_topics(session_id: str = "default"):
    """
    Lista todos os tópicos emergentes (topic_tag) com contagem de summaries
    e total de memórias da sessão associadas.

    Returns: lista ordenada por contagem.
    """
    mem = get_memory(session_id)
    if not is_valid(mem):
        raise HTTPException(503, "memory indisponível")

    # Agrupa summaries por topic_tag
    topics: dict[str, dict] = {}
    all_entries = mem.episodic.entries + mem.semantic.entries

    for e in all_entries:
        if e.get("source_type") != "session_summary":
            continue
        tag = e.get("topic_tag")
        if not tag:
            continue
        if tag not in topics:
            topics[tag] = {
                "tag":       tag,
                "summaries": [],
                "first_seen": e.get("timestamp"),
                "last_seen":  e.get("timestamp"),
            }
        topics[tag]["summaries"].append({
            "text":      e.get("text", "")[:200],
            "timestamp": e.get("timestamp"),
            "session":   e.get("session_id"),
        })
        ts = e.get("timestamp", 0)
        if ts < topics[tag]["first_seen"]:
            topics[tag]["first_seen"] = ts
        if ts > topics[tag]["last_seen"]:
            topics[tag]["last_seen"] = ts

    # Adiciona contagem
    result = []
    for tag, data in topics.items():
        data["count"] = len(data["summaries"])
        result.append(data)
    result.sort(key=lambda x: x["count"], reverse=True)

    return {
        "topics": result,
        "total":  len(result),
    }


@router.post("/memory/reclassify_all")
async def memory_reclassify_all(
    session_id: str = "default",
    dry_run: bool = False,
):
    """
    Reclassifica source_type de TODAS as memórias existentes (episódica + semântica).

    Útil para retroagir governança em memórias antigas que foram criadas antes
    do classificador existir.

    Não destrói nada. Apenas atualiza o campo source_type usando heurística.

    Args:
      dry_run: se True, calcula mas NÃO grava — só retorna preview.
    """
    mem = get_memory(session_id)
    if not is_valid(mem):
        raise HTTPException(503, get_error(mem) or "MemoryStore indisponível")

    try:
        from ...memory_classifier import classify_memory
    except Exception as e:
        raise HTTPException(500, f"classifier indisponível: {e}")

    distribution: dict[str, int] = {}
    changed = 0
    total   = 0

    def _reclassify(entries_list):
        nonlocal changed, total
        for e in entries_list:
            total += 1
            text   = e.get("text", "") or ""
            source = e.get("source", "") or ""
            new_type = classify_memory(text, source)
            distribution[new_type] = distribution.get(new_type, 0) + 1
            old_type = e.get("source_type")
            if old_type != new_type:
                changed += 1
                if not dry_run:
                    e["source_type"] = new_type

    _reclassify(mem.episodic.entries)
    _reclassify(mem.semantic.entries)

    if not dry_run:
        mem.episodic._dirty = True
        mem.episodic.save()
        mem.semantic.save()

    return {
        "dry_run":      dry_run,
        "total":        total,
        "changed":      changed,
        "distribution": distribution,
        "episodic":     len(mem.episodic.entries),
        "semantic":     len(mem.semantic.entries),
    }


@router.post("/memory/consolidate")
async def memory_consolidate(
    session_id: str = "default",
    mode: str = Query("promote_only", description="promote_only (seguro, não-destrutivo) | full (merge + promote, destrutivo)"),
    promote_threshold: int = Query(3, ge=1, le=100, description="acessos mínimos para promover episódica → semântica"),
):
    """
    Dispara consolidação manual: promove memórias episódicas muito acessadas
    para a camada semântica.

    **Modos:**

    - `promote_only` (padrão): SÓ promove. Memórias episódicas permanecem intactas.
      Seguro, não-destrutivo. Recomendado para primeira execução.

    - `full`: promove + MESCLA memórias episódicas similares.
      Destrutivo: memórias similares são unificadas. Use com cuidado.

    **Resultado:**
    Memórias promovidas aparecem em `/memory/list` com `layer=semantic` e
    são retornadas em retrieval com prioridade alta (durabilidade maior).
    """
    if mode not in ("promote_only", "full"):
        raise HTTPException(400, f"mode invalido: '{mode}'. Use 'promote_only' ou 'full'.")

    mem = get_memory(session_id)
    if not is_valid(mem):
        raise HTTPException(503, get_error(mem) or "MemoryStore indisponível")

    try:
        if mode == "promote_only":
            from ...consolidation import consolidate_promote_only
            result = consolidate_promote_only(mem, promote_threshold=promote_threshold)
        else:  # full
            from ...consolidation import consolidate
            result = consolidate(mem, promote_threshold=promote_threshold)
            result["mode"] = "full"

        # Adiciona contadores finais para UI
        result["episodic_total"] = len(mem.episodic.entries)
        result["semantic_total"] = len(mem.semantic.entries)
        return result
    except Exception as e:
        raise HTTPException(500, f"consolidação falhou: {e}")


# IMPORTANTE: /memory/review precisa vir ANTES de /memory/{entry_id}
# para não ser capturado como entry_id.
@router.get("/memory/review", response_class=HTMLResponse)
async def memory_review_ui():
    """UI HTML para review e edição de memórias."""
    html = (
        MEMORY_REVIEW_HTML
        .replace("<!--SIDEBAR_CSS-->", SIDEBAR_CSS)
        .replace("<!--SIDEBAR_HTML-->", render_sidebar(current_page="memory"))
        .replace("<!--SIDEBAR_JS-->", SIDEBAR_JS)
    )
    return HTMLResponse(content=html)


@router.get("/memory/dominance")
async def memory_dominance(
    session_id: str = "default",
    limit:      int = Query(20, ge=1, le=100),
):
    """
    Dominance metrics: quais memórias estão monopolizando o retrieval.

    Retorna top-N memórias por `acessos` (contagem de vezes que apareceram
    em algum retrieval bem-sucedido). Útil para diagnosticar saturação:
    se 5 memórias respondem 75% dos turnos, elas dominam.

    Calcula também:
      - total_retrievals: soma de acessos de todas as memórias
      - top_n_share: % do total concentrado no top-N
      - gini: índice de concentração [0=igualitário, 1=monopólio total]
    """
    mem = get_memory(session_id)
    if not is_valid(mem):
        raise HTTPException(503, get_error(mem) or "MemoryStore indisponível")
    try:
        entries = mem.episodic.entries
        if not entries:
            return {
                "total_memories": 0,
                "total_retrievals": 0,
                "top_n": [],
                "top_n_share": 0.0,
                "gini": 0.0,
                "warning": None,
            }

        # Ranking por acessos
        ranked = sorted(
            entries,
            key=lambda e: e.get("acessos", 0),
            reverse=True,
        )
        total_retrievals = sum(e.get("acessos", 0) for e in entries)

        top_n = []
        for e in ranked[:limit]:
            acessos    = int(e.get("acessos", 0))
            last_acc   = e.get("ultimo_acesso", 0)
            share      = (acessos / total_retrievals * 100) if total_retrievals else 0.0
            top_n.append({
                "id":               e.get("id", ""),
                "text_preview":     (e.get("text", "") or "")[:120],
                "source":           e.get("source", "?"),
                "epistemic_status": e.get("epistemic_status", "hypothesis"),
                "acessos":          acessos,
                "share_pct":        round(share, 2),
                "last_access_ts":   last_acc,
                "confidence":       e.get("confidence", 0.65),
            })

        # Share do top-N
        top_n_acessos = sum(item["acessos"] for item in top_n)
        top_n_share   = (top_n_acessos / total_retrievals * 100) if total_retrievals else 0.0

        # Gini coefficient (concentração)
        if total_retrievals and len(entries) > 1:
            accs = sorted([e.get("acessos", 0) for e in entries])
            n    = len(accs)
            cum  = sum((2 * i - n + 1) * a for i, a in enumerate(accs))
            gini = cum / (n * sum(accs)) if sum(accs) > 0 else 0.0
        else:
            gini = 0.0

        # Warning operacional
        warning = None
        if total_retrievals >= 20:
            if top_n_share >= 75 and limit >= 5:
                warning = (
                    f"DOMINANCIA ALTA: top {limit} memórias concentram "
                    f"{top_n_share:.0f}% dos retrievals. "
                    f"Considere diversificar ou marcar memórias dominantes "
                    f"como 'stale' para penalizar."
                )
            elif gini >= 0.7:
                warning = (
                    f"DISTRIBUICAO MUITO DESIGUAL (Gini={gini:.2f}). "
                    f"Algumas poucas memórias dominam o retrieval."
                )

        return {
            "total_memories":     len(entries),
            "total_retrievals":   total_retrievals,
            "top_n":              top_n,
            "top_n_share":        round(top_n_share, 2),
            "gini":               round(gini, 3),
            "warning":            warning,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


# ─────────────────────────────────────────────────────────────────────
# PR3 v3.13.6 — Co-occurrence (campo de interações entre ideias)
# ─────────────────────────────────────────────────────────────────────

@router.get("/memory/co_occurrence")
async def memory_co_occurrence(
    session_id: str = "default",
    id:         str = "",
    top_k:      int = 10,
):
    """
    Retorna co-ocorrências de memórias.

    Modos:
      - Sem `id`: retorna estatísticas globais + top memórias com mais conexões
      - Com `id`: retorna as `top_k` memórias mais co-occorridas com essa ID

    Co-ocorrência = quantas vezes duas memórias apareceram juntas no retrieval
    (top-5 por similaridade, exclui janela imediata).

    Sinal emergente do uso real: ideias que interagem ficam ligadas.
    """
    try:
        from ...co_occurrence import CoOccurrenceTracker
    except ImportError as e:
        raise HTTPException(503, f"CoOccurrenceTracker indisponível: {e}")

    try:
        tracker = CoOccurrenceTracker(session_id)
    except Exception as e:
        raise HTTPException(500, f"falha ao inicializar tracker: {e}")

    # Modo 1: consulta específica
    if id:
        results = tracker.get_top_co_occurred(id, top_k=top_k)

        # Enriquecer com info da memória (texto, source_type) se disponível
        mem = get_memory(session_id)
        if is_valid(mem):
            entries_by_id = {e.get("id"): e for e in mem.episodic.entries}
            enriched = []
            for r in results:
                related_id = r["id"]
                related_entry = entries_by_id.get(related_id, {})
                enriched.append({
                    "id":           related_id,
                    "count":        r["count"],
                    "last_seen":    r["last_seen"],
                    "text_snippet": (related_entry.get("text") or "")[:100],
                    "source_type":  related_entry.get("source_type"),
                    "status":       related_entry.get("epistemic_status"),
                })
            results = enriched

        return {
            "query_id":   id,
            "session_id": session_id,
            "n_results":  len(results),
            "results":    results,
        }

    # Modo 2: visão geral
    stats = tracker.stats()

    # Top memórias com mais conexões (mais "atratores")
    top_attractors = sorted(
        tracker.pairs.items(),
        key=lambda kv: len(kv[1]),
        reverse=True,
    )[:top_k]

    # Enriquecer
    mem = get_memory(session_id)
    entries_by_id = {}
    if is_valid(mem):
        entries_by_id = {e.get("id"): e for e in mem.episodic.entries}

    enriched_attractors = []
    for memory_id, neighbors in top_attractors:
        entry = entries_by_id.get(memory_id, {})
        # Soma de todos os counts (peso total de conexões)
        total_strength = sum(n["count"] for n in neighbors.values())
        enriched_attractors.append({
            "id":              memory_id,
            "n_neighbors":     len(neighbors),
            "total_strength":  total_strength,
            "text_snippet":    (entry.get("text") or "")[:100],
            "source_type":     entry.get("source_type"),
            "status":          entry.get("epistemic_status"),
        })

    return {
        "session_id":   session_id,
        "stats":        stats,
        "top_attractors": enriched_attractors,
    }


@router.get("/memory/{entry_id}")
async def memory_get(entry_id: str, session_id: str = "default"):
    """Detalhes de 1 entry."""
    mem = get_memory(session_id)
    if not is_valid(mem):
        raise HTTPException(503, get_error(mem) or "MemoryStore indisponível")
    try:
        entry = mem.episodic.get_entry(entry_id)
        if entry is None:
            raise HTTPException(404, f"entry '{entry_id}' não encontrada")
        return entry
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.patch("/memory/{entry_id}")
async def memory_update(
    entry_id:   str,
    req:        MemoryUpdateRequest,
    session_id: str = "default",
):
    """
    Atualiza campos de uma entry.

    Atualizáveis: text, epistemic_status, confidence, prioridade, tags, note.

    NOTA: Atualizar 'text' NÃO recomputa o embedding automaticamente.
    O embedding fica defasado. Considere marcar como 'stale' se mudar texto.
    """
    mem = get_memory(session_id)
    if not is_valid(mem):
        raise HTTPException(503, get_error(mem) or "MemoryStore indisponível")
    try:
        ok = mem.episodic.update_entry(
            entry_id=entry_id,
            text=req.text,
            epistemic_status=req.epistemic_status,
            confidence=req.confidence,
            prioridade=req.prioridade,
            source_type=req.source_type,
            tags=req.tags,
            note=req.note,
        )
        if not ok:
            raise HTTPException(404, f"entry '{entry_id}' não encontrada")
        return {"updated": True, "id": entry_id}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/memory/{entry_id}")
async def memory_delete(entry_id: str, session_id: str = "default"):
    """Apaga 1 entry."""
    mem = get_memory(session_id)
    if not is_valid(mem):
        raise HTTPException(503, get_error(mem) or "MemoryStore indisponível")
    try:
        ok = mem.episodic.delete_entry(entry_id)
        if not ok:
            raise HTTPException(404, f"entry '{entry_id}' não encontrada")
        return {"deleted": True, "id": entry_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/memory/batch_delete")
async def memory_batch_delete(req: BatchDeleteRequest, session_id: str = "default"):
    """
    Apaga em batch por filtro. REQUER confirm=True no body.

    Exemplos:
      - {"filter_status": "contradicted", "confirm": true}  → apaga todas contradicted
      - {"filter_source": "llm:qwen2.5", "confirm": true}   → apaga origens de qwen
    """
    mem = get_memory(session_id)
    if not is_valid(mem):
        raise HTTPException(503, get_error(mem) or "MemoryStore indisponível")
    try:
        removed = mem.episodic.delete_by_filter(
            filter_status=req.filter_status,
            filter_source=req.filter_source,
            confirm=req.confirm,
        )
        return {"deleted_count": removed}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════
# UI DE REVIEW (HTML embedded)
# ═══════════════════════════════════════════════════════════════════════

MEMORY_REVIEW_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EDP - Memory Review</title>
<!--SIDEBAR_CSS-->
<style>
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background:#0f172a; color:#e2e8f0; margin:0; padding:0; }
.page-content { padding:20px; padding-top:64px; max-width:1400px; margin:0 auto; }
h1 { color:#f1f5f9; border-bottom:2px solid #334155; padding-bottom:10px;
     display:flex; align-items:center; gap:10px; margin-top:0; }
.back-link { color:#06b6d4; text-decoration:none; font-size:14px; font-weight:normal; }

.stats { background:#1e293b; padding:14px; border-radius:8px; margin-bottom:16px;
         display:grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
         gap:10px; }
.stat { background:#0f172a; padding:8px 10px; border-radius:6px; }
.stat-label { font-size:10px; color:#94a3b8; text-transform:uppercase; }
.stat-value { font-size:20px; font-weight:bold; color:#22d3ee; margin-top:2px; }
.stat.verified .stat-value { color:#86efac; }
.stat.hypothesis .stat-value { color:#fde68a; }
.stat.contradicted .stat-value { color:#fca5a5; }
.stat.stale .stat-value { color:#cbd5e1; }
.stat.quarantined .stat-value { color:#f97316; }

.filters { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px;
           align-items:center; }
.filters select, .filters input { background:#1e293b; color:#e2e8f0;
                                   border:1px solid #334155; padding:6px 10px;
                                   border-radius:4px; font-size:13px; }
.filters input[type="text"] { flex:1; min-width:200px; }
.filters button { background:#075985; color:#fff; border:none;
                  padding:6px 14px; border-radius:4px; cursor:pointer;
                  font-size:13px; }
.filters button:hover { background:#0e7490; }
.filters button.danger { background:#7f1d1d; }
.filters button.danger:hover { background:#991b1b; }

.entry { background:#1e293b; padding:12px; border-radius:8px; margin-bottom:10px;
         border-left:3px solid #64748b; }
.entry.verified { border-left-color:#16a34a; }
.entry.hypothesis { border-left-color:#ca8a04; }
.entry.contradicted { border-left-color:#dc2626; }
.entry.stale { border-left-color:#64748b; }
.entry.quarantined { border-left-color:#ea580c; }

.entry-head { display:flex; justify-content:space-between; align-items:center;
              gap:8px; font-size:11px; color:#94a3b8; margin-bottom:8px;
              flex-wrap:wrap; }

/* Painel de Dominance */
.dominance-panel { background:#1e293b; padding:12px 14px; border-radius:8px;
                   margin-bottom:14px; border-left:3px solid #06b6d4; }

/* Painel de Consolidação */
.consolidate-panel { background:#1e293b; padding:12px 14px; border-radius:8px;
                     margin-bottom:14px; border-left:3px solid #a855f7; }
.consolidate-panel h3 { margin:0 0 8px 0; font-size:13px; color:#d8b4fe;
                         display:flex; justify-content:space-between; align-items:center; }
.consolidate-row { display:flex; gap:10px; align-items:center; flex-wrap:wrap;
                   font-size:12px; color:#cbd5e1; margin:6px 0; }
.consolidate-row label { color:#94a3b8; }
.consolidate-row select, .consolidate-row input { background:#0f172a;
                   color:#e2e8f0; border:1px solid #334155; padding:4px 8px;
                   border-radius:4px; font-size:12px; }
.btn-consolidate { background:#7e22ce; color:#fff; border:none;
                   padding:6px 14px; border-radius:4px; cursor:pointer;
                   font-size:12px; }
.btn-consolidate:hover { background:#9333ea; }
.btn-consolidate:disabled { background:#475569; cursor:not-allowed; }
.consolidate-result { background:#0f172a; padding:8px 10px; border-radius:4px;
                      font-size:11px; color:#a3e635; margin-top:8px;
                      font-family:monospace; white-space:pre-wrap; }
.consolidate-warn { background:#7f1d1d; color:#fecaca; padding:6px 10px;
                    border-radius:4px; font-size:11px; margin-top:6px; }
.dominance-panel h3 { margin:0 0 8px 0; font-size:13px; color:#67e8f9;
                       display:flex; justify-content:space-between; }
.dom-summary { display:flex; gap:14px; font-size:11px; color:#94a3b8;
               flex-wrap:wrap; margin-bottom:8px; }
.dom-summary b { color:#e2e8f0; }
.dom-warning { background:#7f1d1d; color:#fecaca; padding:6px 10px;
               border-radius:4px; font-size:11px; margin:6px 0; }
.dom-table { width:100%; font-size:11px; border-collapse:collapse;
             margin-top:4px; }
.dom-table th { text-align:left; color:#64748b; font-weight:normal;
                padding:3px 6px; border-bottom:1px solid #334155; }
.dom-table td { padding:3px 6px; border-bottom:1px solid #1e293b;
                color:#cbd5e1; }
.dom-table .acc-bar { display:inline-block; height:4px; background:#0e7490;
                      border-radius:2px; vertical-align:middle; margin-right:4px; }

/* Badge de source_type (governança) */
.badge-stype { display:inline-block; padding:1px 6px; border-radius:3px;
               font-size:9px; font-weight:600; text-transform:uppercase;
               margin-right:4px; letter-spacing:0.3px; }
.stype-meta   { background:#7f1d1d; color:#fecaca; }
.stype-llm    { background:#1e3a8a; color:#bfdbfe; }
.stype-user   { background:#14532d; color:#bbf7d0; }
.stype-ext    { background:#7c2d12; color:#fed7aa; }
.stype-unk    { background:#1f2937; color:#9ca3af; }
.badge { padding:2px 8px; border-radius:3px; font-size:10px; font-weight:bold;
         text-transform:uppercase; }
.badge.verified { background:#14532d; color:#86efac; }
.badge.hypothesis { background:#713f12; color:#fde68a; }
.badge.contradicted { background:#7f1d1d; color:#fca5a5; }
.badge.stale { background:#334155; color:#cbd5e1; }
.badge.quarantined { background:#7c2d12; color:#fdba74; }

/* Badges de SOURCE (origem da memória) */
.badge-source { padding:2px 8px; border-radius:3px; font-size:10px; font-weight:bold;
                text-transform:uppercase; margin-right:6px; }
.badge-source.user { background:#1e3a8a; color:#bfdbfe; }
.badge-source.claude { background:#065f46; color:#a7f3d0; }
.badge-source.openai { background:#047857; color:#6ee7b7; }
.badge-source.qwen { background:#9a3412; color:#fed7aa; }
.badge-source.ollama-local { background:#9a3412; color:#fed7aa; }
.badge-source.tool { background:#5b21b6; color:#ddd6fe; }
.badge-source.external { background:#475569; color:#cbd5e1; }
.badge-source.system { background:#1e293b; color:#94a3b8; }
.badge-source.unknown { background:#374151; color:#9ca3af; }

.text { background:#0f172a; padding:8px; border-radius:4px; font-size:13px;
        color:#cbd5e1; white-space:pre-wrap; overflow-wrap:break-word;
        margin-bottom:8px; }
.text-edit { width:100%; background:#0f172a; color:#e2e8f0;
             border:1px solid #334155; padding:6px; border-radius:4px;
             font-family:inherit; font-size:13px; min-height:60px;
             margin-bottom:8px; }

.actions { display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
.actions button { background:#334155; color:#e2e8f0; border:none;
                  padding:4px 10px; border-radius:3px; cursor:pointer;
                  font-size:11px; }
.actions button:hover { background:#475569; }
.actions button.save { background:#15803d; }
.actions button.delete { background:#7f1d1d; }
.actions select { background:#334155; color:#e2e8f0; border:none;
                  padding:4px 6px; border-radius:3px; font-size:11px; }

.meta { font-size:10px; color:#64748b; margin-top:6px; }
.empty { text-align:center; padding:40px; color:#64748b; }
.confirm-dialog { background:#7f1d1d; padding:10px; border-radius:6px;
                  margin-bottom:10px; font-size:13px; }
.confirm-dialog button { margin-left:8px; }
</style>
</head>
<body>
<!--SIDEBAR_HTML-->

<div class="page-content">
<h1>Memory Review</h1>

<div class="stats" id="stats">
  <div class="stat"><div class="stat-label">Carregando...</div></div>
</div>

<div class="dominance-panel" id="dominance-panel" style="display:none;">
  <h3>
    <span>Dominância de Retrieval</span>
    <a href="#" onclick="loadDominance(); return false;" style="color:#06b6d4;font-size:11px;text-decoration:none;">↻ atualizar</a>
  </h3>
  <div class="dom-summary" id="dom-summary"></div>
  <div id="dom-warning"></div>
  <div id="dom-table"></div>
</div>

<div class="consolidate-panel">
  <h3>
    <span>Consolidação Semântica</span>
    <span id="consolidate-counts" style="color:#94a3b8;font-size:11px;font-weight:normal;"></span>
  </h3>
  <div style="font-size:11px;color:#94a3b8;margin-bottom:8px;">
    Promove memórias episódicas muito acessadas para a camada semântica (durabilidade maior).
  </div>
  <div class="consolidate-row">
    <label>Modo:</label>
    <select id="consolidate-mode">
      <option value="promote_only" selected>Promover apenas (seguro)</option>
      <option value="full">Promover + Mesclar (destrutivo)</option>
    </select>
    <label>Limiar de acessos:</label>
    <input type="number" id="consolidate-threshold" value="3" min="1" max="100" style="width:60px;">
    <button class="btn-consolidate" id="btn-consolidate" onclick="runConsolidate()">Executar</button>
    <button class="btn-consolidate" id="btn-reclassify" onclick="runReclassify()"
            style="background:#0e7490;margin-left:6px;">Reclassificar Source-Types</button>
  </div>
  <div id="consolidate-warn"></div>
  <div id="consolidate-result"></div>
</div>

<div class="consolidate-panel" style="border-color:#a78bfa;">
  <h3>
    <span>🔍 Padrões Observados</span>
    <a href="#" onclick="loadTrajectory(); return false;"
       style="font-size:11px;color:#c4b5fd;font-weight:400;">↻ calcular</a>
  </h3>
  <p style="font-size:11px;color:#94a3b8;margin:2px 0 8px 0;">
    Loops temáticos (retornos repetidos) e últimas memórias da sessão.
    Análise local, sob demanda.
  </p>
  <div id="trajectory-result" style="margin-top:6px;font-size:12px;">
    <span style="color:#64748b;">Clique em "calcular" para gerar.</span>
  </div>
</div>

<div class="consolidate-panel" style="border-color:#0e7490;">
  <h3>
    <span>Tópicos Emergentes (session summaries)</span>
    <a href="#" onclick="loadTopics(); return false;"
       style="font-size:11px;color:#7dd3fc;font-weight:400;">↻ atualizar</a>
  </h3>
  <p style="font-size:11px;color:#94a3b8;margin:2px 0 8px 0;">
    Resumos gerados ao fim de cada sessão. Tags semânticas reusadas
    quando similaridade ≥ 0.75 com tópico existente.
  </p>
  <div class="consolidate-row">
    <button class="btn-consolidate" onclick="forceSummarize()"
            style="background:#0e7490;">Gerar resumo agora</button>
    <span id="summarize-result" style="font-size:11px;color:#94a3b8;
                                       margin-left:10px;"></span>
  </div>
  <div id="topics-list" style="margin-top:10px;font-size:12px;"></div>
</div>

<div class="consolidate-panel" style="border-color:#f59e0b;">
  <h3>
    <span>🪐 Campo de Co-Ocorrência</span>
    <a href="#" onclick="loadCoOccurrence(); return false;"
       style="font-size:11px;color:#fcd34d;font-weight:400;">↻ atualizar</a>
  </h3>
  <p style="font-size:11px;color:#94a3b8;margin:2px 0 8px 0;">
    Memórias que aparecem juntas em retrievals formam "atratores".
    Sinal emergente: ideias que interagem ganham peso uma sobre a outra.
  </p>
  <div id="cooccurrence-result" style="margin-top:6px;font-size:12px;">
    <span style="color:#64748b;">Clique em "atualizar" para ver o campo.</span>
  </div>
</div>

<div class="filters">
  <select id="f-layer">
    <option value="episodic" selected>Episódica</option>
    <option value="semantic">Semântica</option>
    <option value="all">Todas (epi + sem)</option>
  </select>
  <select id="f-stype">
    <option value="">Todo tipo</option>
    <option value="user_input">User Input</option>
    <option value="llm_response">LLM Response</option>
    <option value="meta_conversation">Meta-Conversa</option>
    <option value="external">External</option>
    <option value="unknown">Unknown</option>
  </select>
  <select id="f-status">
    <option value="">Todos status</option>
    <option value="verified">verified</option>
    <option value="hypothesis">hypothesis</option>
    <option value="stale">stale</option>
    <option value="contradicted">contradicted</option>
    <option value="quarantined">quarantined</option>
  </select>
  <select id="f-source">
    <option value="">Toda origem</option>
    <option value="user">você (user)</option>
    <option value="claude">claude (todos)</option>
    <option value="haiku">claude-haiku</option>
    <option value="sonnet">claude-sonnet</option>
    <option value="gpt">openai (gpt)</option>
    <option value="qwen">qwen (local)</option>
    <option value="phi3">phi3 (local)</option>
    <option value="llama">llama (local)</option>
    <option value="tool">tools</option>
    <option value="external">external (importado)</option>
  </select>
  <input type="text" id="f-search" placeholder="buscar no texto...">
  <button onclick="loadAll()">Atualizar</button>
  <button class="danger" onclick="confirmBatchDelete()">Apagar filtrados</button>
</div>

<div id="entries-list">Carregando...</div>

<script>
const SESSION = 'default';

async function loadStats() {
  try {
    const r = await fetch('/memory/stats?session_id=' + SESSION);
    const d = await r.json();
    const el = document.getElementById('stats');
    let html = '';
    html += stat('Total', d.total || 0);
    const statuses = ['verified', 'hypothesis', 'stale', 'contradicted', 'quarantined'];
    for (const s of statuses) {
      const n = (d.by_status && d.by_status[s]) || 0;
      html += stat(s, n, s);
    }
    el.innerHTML = html;
  } catch(e) { console.error(e); }
}

function stat(label, val, klass) {
  return '<div class="stat ' + (klass || '') + '">' +
         '<div class="stat-label">' + label + '</div>' +
         '<div class="stat-value">' + val + '</div></div>';
}

async function loadEntries() {
  try {
    const status = document.getElementById('f-status').value;
    const source = document.getElementById('f-source').value;
    const search = document.getElementById('f-search').value;
    const layer  = document.getElementById('f-layer').value || 'episodic';
    const stype  = document.getElementById('f-stype') ? document.getElementById('f-stype').value : '';
    let url = '/memory/list?session_id=' + SESSION + '&limit=200&layer=' + layer;
    if (status) url += '&filter_status=' + status;
    if (source) url += '&filter_source=' + source;
    if (search) url += '&search=' + encodeURIComponent(search);

    const r = await fetch(url);
    const d = await r.json();
    let entries = d.entries || [];
    // Filtro de source_type aplicado no cliente (server-side filtra outros campos)
    if (stype) {
      entries = entries.filter(e => (e.source_type || 'unknown') === stype);
    }
    const list = document.getElementById('entries-list');
    if (entries.length === 0) {
      list.innerHTML = '<div class="empty">Nenhuma memória para este filtro.</div>';
      return;
    }
    let html = '';
    for (const e of entries) {
      const status = e.epistemic_status || 'hypothesis';
      const date = e.timestamp ? new Date(e.timestamp * 1000).toLocaleString('pt-BR') : '-';
      const updated = e.updated_at ? ' | atualizada: ' + new Date(e.updated_at * 1000).toLocaleString('pt-BR') : '';
      const id = e.id || '';
      const lay = e.layer || 'episodic';
      const layBadge = lay === 'semantic'
        ? '<span class="badge" style="background:#7e22ce;color:#fff;">SEMANTIC</span>'
        : '<span class="badge" style="background:#0f172a;color:#94a3b8;border:1px solid #334155;">episodic</span>';
      const stypeBadge = sourceTypeBadge(e.source_type);

      html += '<div class="entry ' + status + '" id="entry_' + id + '">' +
                '<div class="entry-head">' +
                  '<span>' +
                    layBadge + ' ' + stypeBadge + ' ' +
                    '<span class="badge-source ' + sourceCategory(e.source) + '" title="Origem: ' + escapeHtml(e.source || 'desconhecido') + '">' + sourceLabel(e.source) + '</span>' +
                    '<b>' + id.slice(0, 12) + '</b>' +
                    ' | conf=' + (e.confidence != null ? e.confidence.toFixed(2) : '-') +
                    ' | acessos=' + (e.acessos || 0) + ' | ' + date + updated +
                  '</span>' +
                  '<span class="badge ' + status + '">' + status + '</span>' +
                '</div>' +
                '<textarea class="text-edit" id="txt_' + id + '">' + escapeHtml(e.text || '') + '</textarea>' +
                (e.human_note ? '<div class="text" style="font-style:italic;">Nota: ' + escapeHtml(e.human_note) + '</div>' : '') +
                '<div class="actions">' +
                  'Status: <select id="status_' + id + '">' +
                    statusOption('verified', status) +
                    statusOption('hypothesis', status) +
                    statusOption('stale', status) +
                    statusOption('contradicted', status) +
                    statusOption('quarantined', status) +
                  '</select>' +
                  '<button class="save" onclick="saveEntry(\\'' + id + '\\')">💾 Salvar</button>' +
                  '<button class="delete" onclick="deleteEntry(\\'' + id + '\\')">🗑 Apagar</button>' +
                '</div>' +
                '<div class="meta">' +
                  'prio=' + (e.prioridade || 'media') +
                  ' | embed_model=' + (e.embedding_model || 'n/a') +
                '</div>' +
              '</div>';
    }
    list.innerHTML = html;
  } catch(e) {
    console.error(e);
    document.getElementById('entries-list').innerHTML = '<div class="empty">Erro: ' + e + '</div>';
  }
}

function statusOption(value, selected) {
  return '<option value="' + value + '"' + (value === selected ? ' selected' : '') + '>' + value + '</option>';
}

// Classifica source em categoria visual
function sourceCategory(source) {
  if (!source) return 'unknown';
  const s = source.toLowerCase();
  if (s === 'user' || s.startsWith('user:')) return 'user';
  if (s.includes('claude')) return 'claude';
  if (s.includes('gpt') || s.includes('openai')) return 'openai';
  if (s.includes('qwen')) return 'qwen';
  if (s.includes('phi') || s.includes('llama') || s.includes('mistral') || s.includes('ollama')) return 'ollama-local';
  if (s.startsWith('tool:')) return 'tool';
  if (s.startsWith('external:')) return 'external';
  if (s === 'system' || s.startsWith('system:')) return 'system';
  return 'unknown';
}

// Label curto e legível
function sourceLabel(source) {
  if (!source) return '?';
  const s = source.toLowerCase();
  if (s === 'user') return 'você';
  if (s.includes('claude-haiku')) return 'haiku';
  if (s.includes('claude-sonnet')) return 'sonnet';
  if (s.includes('claude-opus')) return 'opus';
  if (s.includes('claude')) return 'claude';
  if (s.includes('gpt-4o')) return 'gpt-4o';
  if (s.includes('gpt')) return 'gpt';
  if (s.includes('qwen')) return 'qwen';
  if (s.includes('phi3')) return 'phi3';
  if (s.includes('llama')) return 'llama';
  if (s.startsWith('tool:')) return source.replace('tool:', '');
  if (s.startsWith('external:')) return source.replace('external:', '');
  return source.length > 12 ? source.slice(0, 12) + '...' : source;
}

async function saveEntry(id) {
  const text = document.getElementById('txt_' + id).value;
  const status = document.getElementById('status_' + id).value;
  try {
    const r = await fetch('/memory/' + id + '?session_id=' + SESSION, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ text: text, epistemic_status: status }),
    });
    if (!r.ok) {
      const err = await r.text();
      alert('Erro: ' + err);
      return;
    }
    await loadAll();
  } catch(e) { alert('Erro: ' + e); }
}

async function deleteEntry(id) {
  if (!confirm('Apagar esta memória? Não pode ser desfeito.')) return;
  try {
    const r = await fetch('/memory/' + id + '?session_id=' + SESSION, {
      method: 'DELETE',
    });
    if (!r.ok) {
      alert('Erro: ' + await r.text());
      return;
    }
    await loadAll();
  } catch(e) { alert('Erro: ' + e); }
}

async function confirmBatchDelete() {
  const status = document.getElementById('f-status').value;
  const source = document.getElementById('f-source').value;
  if (!status && !source) {
    alert('Aplique pelo menos 1 filtro antes de apagar em lote.');
    return;
  }
  const filters = [];
  if (status) filters.push('status=' + status);
  if (source) filters.push('source=' + source);
  if (!confirm('Apagar TODAS as memórias com ' + filters.join(' e ') + '?\\nNão pode ser desfeito.')) return;

  try {
    const r = await fetch('/memory/batch_delete?session_id=' + SESSION, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        filter_status: status || null,
        filter_source: source || null,
        confirm: true,
      }),
    });
    if (!r.ok) {
      alert('Erro: ' + await r.text());
      return;
    }
    const d = await r.json();
    alert('Apagadas: ' + d.deleted_count);
    await loadAll();
  } catch(e) { alert('Erro: ' + e); }
}

function escapeHtml(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

function sourceTypeBadge(st) {
  if (!st || st === 'unknown') return '<span class="badge-stype stype-unk">?</span>';
  const map = {
    'meta_conversation': ['stype-meta', 'META'],
    'llm_response':      ['stype-llm',  'LLM'],
    'user_input':        ['stype-user', 'USER'],
    'external':          ['stype-ext',  'EXT'],
  };
  const [cls, label] = map[st] || ['stype-unk', '?'];
  return '<span class="badge-stype ' + cls + '" title="source_type: ' + st + '">' + label + '</span>';
}

async function loadTrajectory() {
  const box = document.getElementById('trajectory-result');
  box.innerHTML = '<span style="color:#94a3b8;">Calculando padrões...</span>';
  try {
    const r = await fetch('/memory/trajectory?session_id=' + SESSION);
    const d = await r.json();
    const s = d.stats || {};

    let html = '';

    // Bloco de estatísticas básicas
    html += '<div style="background:#0f172a;padding:10px;border-radius:5px;margin-bottom:8px;">';
    html += '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;text-align:center;">';
    html += '<div><div style="color:#a78bfa;font-size:18px;font-weight:600;">' + (s.total_nodes||0) + '</div><div style="color:#64748b;font-size:10px;">memórias</div></div>';
    html += '<div><div style="color:#f59e0b;font-size:18px;font-weight:600;">' + (s.loops||0) + '</div><div style="color:#64748b;font-size:10px;">loops</div></div>';
    html += '<div><div style="color:#86efac;font-size:18px;font-weight:600;">' + (s.span_days||0) + 'd</div><div style="color:#64748b;font-size:10px;">de uso</div></div>';
    html += '</div></div>';

    // Folhas recentes (últimas memórias)
    if (d.leaves && d.leaves.length > 0) {
      html += '<div style="margin-top:8px;"><div style="color:#86efac;font-weight:600;margin-bottom:4px;">🍃 Onde você parou (últimas memórias)</div>';
      for (const leaf of d.leaves.slice(0, 5)) {
        html += '<div style="background:#1e293b;padding:6px 8px;border-radius:4px;margin-bottom:3px;border-left:3px solid #86efac;">';
        html += '<div style="color:#bbf7d0;font-size:10px;">' + formatRelativeTime(leaf.timestamp) + '</div>';
        html += '<div style="color:#e2e8f0;">' + escapeHtml(leaf.text) + '</div>';
        html += '</div>';
      }
      html += '</div>';
    }

    // Loops
    if (d.loops && d.loops.length > 0) {
      html += '<div style="margin-top:8px;"><div style="color:#f59e0b;font-weight:600;margin-bottom:4px;">🔁 Loops Temáticos (retornos repetidos)</div>';
      for (const loop of d.loops) {
        html += '<div style="background:#1e293b;padding:6px 8px;border-radius:4px;margin-bottom:3px;border-left:3px solid #f59e0b;">';
        html += '<div style="color:#fcd34d;font-size:10px;">' + loop.size + ' memórias · ' + loop.span_days + ' dias entre primeiro e último</div>';
        html += '<div style="color:#e2e8f0;">' + escapeHtml(loop.first) + '</div>';
        html += '</div>';
      }
      html += '</div>';
    }

    if (s.total_nodes === 0) {
      html = '<div style="color:#64748b;">Memórias insuficientes para análise.</div>';
    }

    box.innerHTML = html;
  } catch (e) {
    box.innerHTML = '<div style="color:#ef4444;">Erro: ' + escapeHtml(String(e)) + '</div>';
  }
}

function formatRelativeTime(ts) {
  if (!ts) return '-';
  const now = Date.now() / 1000;
  const delta = now - ts;
  if (delta < 60) return 'agora';
  if (delta < 3600) return 'há ' + Math.floor(delta / 60) + 'min';
  if (delta < 86400) return 'há ' + Math.floor(delta / 3600) + 'h';
  if (delta < 172800) return 'ontem';
  if (delta < 604800) return 'há ' + Math.floor(delta / 86400) + ' dias';
  if (delta < 2592000) return 'há ' + Math.floor(delta / 604800) + ' sem';
  return 'há ' + Math.floor(delta / 2592000) + ' meses';
}

async function loadTopics() {
  try {
    const r = await fetch('/memory/topics?session_id=' + SESSION);
    const d = await r.json();
    const list = document.getElementById('topics-list');
    if (!d.topics || d.topics.length === 0) {
      list.innerHTML = '<div style="color:#64748b;">Nenhum tópico ainda. Resumos são gerados ao desconectar do chat.</div>';
      return;
    }
    let html = '<table style="width:100%;border-collapse:collapse;">';
    html += '<thead><tr style="text-align:left;border-bottom:1px solid #334155;">' +
            '<th style="padding:4px 6px;">Tag</th>' +
            '<th style="padding:4px 6px;">Aparições</th>' +
            '<th style="padding:4px 6px;">Primeiro</th>' +
            '<th style="padding:4px 6px;">Último</th>' +
            '<th style="padding:4px 6px;">Último resumo</th>' +
            '</tr></thead><tbody>';
    for (const t of d.topics) {
      const lastSummary = t.summaries[t.summaries.length - 1] || {};
      const summaryText = (lastSummary.text || '').replace(/^\\[session_summary\\]\\s*/, '');
      html += '<tr style="border-bottom:1px solid #1e293b;">' +
                '<td style="padding:4px 6px;"><code style="color:#7dd3fc;">' + escapeHtml(t.tag) + '</code></td>' +
                '<td style="padding:4px 6px;">' + t.count + '</td>' +
                '<td style="padding:4px 6px;color:#64748b;">' + formatRelativeTime(t.first_seen) + '</td>' +
                '<td style="padding:4px 6px;color:#64748b;">' + formatRelativeTime(t.last_seen) + '</td>' +
                '<td style="padding:4px 6px;color:#94a3b8;">' + escapeHtml(summaryText.slice(0, 80)) + '</td>' +
                '</tr>';
    }
    html += '</tbody></table>';
    list.innerHTML = html;
  } catch (e) {
    document.getElementById('topics-list').innerHTML =
      '<div style="color:#ef4444;">Erro: ' + escapeHtml(String(e)) + '</div>';
  }
}

async function forceSummarize() {
  const resultEl = document.getElementById('summarize-result');
  resultEl.textContent = 'Gerando...';
  try {
    const r = await fetch('/memory/summarize_session?session_id=' + SESSION, { method: 'POST' });
    const d = await r.json();
    if (!d.ok) {
      resultEl.textContent = 'Não gerou: ' + (d.reason || 'erro');
      return;
    }
    const reuseTag = d.reused ? ' (reusada)' : ' (nova)';
    resultEl.textContent = 'OK: ' + d.label + reuseTag + ' — ' + d.summary.slice(0, 60);
    loadTopics();
  } catch (e) {
    resultEl.textContent = 'Erro: ' + String(e);
  }
}

async function runReclassify() {
  const btn = document.getElementById('btn-reclassify');
  const resultEl = document.getElementById('consolidate-result');
  const ok = confirm(
    'Reclassificar TODAS as memórias por source_type (heurístico). ' +
    'Não destrói nada, apenas adiciona/atualiza o campo source_type. ' +
    'Continuar?'
  );
  if (!ok) return;

  btn.disabled = true;
  btn.textContent = 'Executando...';
  try {
    const r = await fetch('/memory/reclassify_all?session_id=' + SESSION, { method: 'POST' });
    const d = await r.json();
    if (!r.ok) {
      resultEl.textContent = 'Erro: ' + (d.detail || JSON.stringify(d));
    } else {
      let lines = [];
      lines.push('Reclassificação concluída');
      lines.push('Total: ' + d.total + ' | Alteradas: ' + d.changed);
      lines.push('Distribuição:');
      Object.entries(d.distribution || {}).forEach(([k, v]) => {
        lines.push('  ' + k + ': ' + v);
      });
      resultEl.textContent = lines.join(String.fromCharCode(10));
      loadAll();
    }
  } catch (e) {
    resultEl.textContent = 'Falha: ' + String(e);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Reclassificar Source-Types';
  }
}

function loadAll() {
  loadStats();
  loadEntries();
  loadDominance();
  loadTopics();
  updateConsolidateCounts();
}

async function updateConsolidateCounts() {
  try {
    const r = await fetch('/memory/stats?session_id=' + SESSION);
    const d = await r.json();
    if (d && typeof d.episodic !== 'undefined') {
      document.getElementById('consolidate-counts').textContent =
        'episódica: ' + d.episodic + ' | semântica: ' + d.semantic;
    }
  } catch(e) {}
}

async function runConsolidate() {
  const mode  = document.getElementById('consolidate-mode').value;
  const thr   = parseInt(document.getElementById('consolidate-threshold').value, 10) || 3;
  const btn   = document.getElementById('btn-consolidate');
  const warnEl   = document.getElementById('consolidate-warn');
  const resultEl = document.getElementById('consolidate-result');

  // Confirmação extra se for modo destrutivo
  if (mode === 'full') {
    const ok = confirm(
      'MODO DESTRUTIVO: memorias episodicas similares serao MESCLADAS (originais perdidas). ' +
      'Recomendado fazer backup antes (Copy-Item C:\\edp_data C:\\edp_backup_X). ' +
      'Continuar?'
    );
    if (!ok) return;
  }

  btn.disabled = true;
  btn.textContent = 'Executando...';
  warnEl.innerHTML = '';
  resultEl.textContent = '';

  try {
    const url = '/memory/consolidate?session_id=' + SESSION +
                '&mode=' + mode +
                '&promote_threshold=' + thr;
    const r = await fetch(url, { method: 'POST' });
    const d = await r.json();

    if (!r.ok) {
      warnEl.innerHTML = '<div class="consolidate-warn">Erro: ' +
        escapeHtml(d.detail || JSON.stringify(d)) + '</div>';
    } else {
      let lines = [];
      lines.push('Modo: ' + d.mode);
      lines.push('Limiar: ' + d.threshold + ' acessos');
      if (d.mode === 'promote_only') {
        lines.push('Escaneadas: ' + d.scanned);
        lines.push('Promovidas: ' + d.promoted);
        lines.push('Já estavam: ' + d.already_promoted);
        lines.push('Abaixo do limiar: ' + d.skipped);
      } else {
        lines.push('Antes: ' + d.before);
        lines.push('Depois: ' + d.after);
        lines.push('Mescladas: ' + d.merged);
        lines.push('Promovidas: ' + d.promoted);
      }
      lines.push('---');
      lines.push('Episódica total: ' + d.episodic_total);
      lines.push('Semântica total: ' + d.semantic_total);
      resultEl.textContent = lines.join(String.fromCharCode(10));

      // Refresh UI
      loadAll();
    }
  } catch (e) {
    warnEl.innerHTML = '<div class="consolidate-warn">Falha: ' + escapeHtml(String(e)) + '</div>';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Executar';
  }
}

async function loadDominance() {
  try {
    const r = await fetch('/memory/dominance?session_id=' + SESSION + '&limit=10');
    const d = await r.json();
    const panel = document.getElementById('dominance-panel');
    if (!d.total_retrievals || d.total_retrievals === 0) {
      panel.style.display = 'none';
      return;
    }
    panel.style.display = 'block';

    // Summary
    document.getElementById('dom-summary').innerHTML =
      '<span>Total retrievals: <b>' + d.total_retrievals + '</b></span>' +
      '<span>Memórias: <b>' + d.total_memories + '</b></span>' +
      '<span>Top 10 concentra: <b>' + d.top_n_share + '%</b></span>' +
      '<span>Gini: <b>' + d.gini + '</b></span>';

    // Warning
    const warnEl = document.getElementById('dom-warning');
    warnEl.innerHTML = d.warning
      ? '<div class="dom-warning">⚠ ' + escapeHtml(d.warning) + '</div>'
      : '';

    // Tabela top-N
    if (!d.top_n || d.top_n.length === 0) {
      document.getElementById('dom-table').innerHTML = '';
      return;
    }
    const maxAcc = d.top_n[0].acessos || 1;
    let html = '<table class="dom-table">' +
               '<thead><tr><th>#</th><th>Memória</th><th>Source</th>' +
               '<th>Status</th><th style="text-align:right;">Acessos</th>' +
               '<th style="text-align:right;">%</th></tr></thead><tbody>';
    d.top_n.forEach((m, idx) => {
      const barW = Math.max(2, Math.round((m.acessos / maxAcc) * 60));
      html += '<tr>' +
                '<td>' + (idx + 1) + '</td>' +
                '<td title="' + escapeHtml(m.text_preview) + '">' +
                  escapeHtml((m.text_preview || '').slice(0, 60)) +
                  (m.text_preview.length > 60 ? '...' : '') +
                '</td>' +
                '<td><span class="badge-source ' + sourceCategory(m.source) + '">' +
                  sourceLabel(m.source) + '</span></td>' +
                '<td><span class="badge ' + m.epistemic_status + '">' +
                  m.epistemic_status + '</span></td>' +
                '<td style="text-align:right;">' +
                  '<span class="acc-bar" style="width:' + barW + 'px;"></span>' +
                  m.acessos +
                '</td>' +
                '<td style="text-align:right;">' + m.share_pct + '%</td>' +
              '</tr>';
    });
    html += '</tbody></table>';
    document.getElementById('dom-table').innerHTML = html;
  } catch(e) {
    console.error('loadDominance:', e);
  }
}

async function loadCoOccurrence() {
  const el = document.getElementById('cooccurrence-result');
  el.innerHTML = '<span style="color:#94a3b8;">Carregando...</span>';
  try {
    const r = await fetch('/memory/co_occurrence?session_id=' + SESSION + '&top_k=10');
    if (!r.ok) {
      el.innerHTML = '<span style="color:#f87171;">Erro ao carregar (' + r.status + ')</span>';
      return;
    }
    const d = await r.json();
    const stats = d.stats || {};
    const attractors = d.top_attractors || [];

    if (!stats.total_unique_pairs || stats.total_unique_pairs === 0) {
      el.innerHTML = '<span style="color:#64748b;">Ainda sem dados. ' +
                     'Use o chat normalmente — pares se formam conforme memórias aparecem juntas em retrievals.</span>';
      return;
    }

    let html = '<div style="display:flex;gap:14px;font-size:11px;color:#94a3b8;' +
               'margin-bottom:10px;flex-wrap:wrap;">' +
               '<span>Pares únicos: <b style="color:#fcd34d;">' +
                 stats.total_unique_pairs + '</b></span>' +
               '<span>Co-ocorrências totais: <b style="color:#fcd34d;">' +
                 stats.total_co_occurrences + '</b></span>' +
               '<span>Memórias rastreadas: <b style="color:#fcd34d;">' +
                 stats.tracked_memories + '</b></span>' +
               '</div>';

    if (attractors.length === 0) {
      html += '<span style="color:#64748b;">Sem atratores ainda.</span>';
      el.innerHTML = html;
      return;
    }

    const maxNeighbors = attractors[0].n_neighbors || 1;
    html += '<table style="width:100%;border-collapse:collapse;font-size:11px;">' +
            '<thead><tr style="text-align:left;border-bottom:1px solid #334155;color:#94a3b8;">' +
            '<th style="padding:4px 6px;">#</th>' +
            '<th style="padding:4px 6px;">Memória (atrator)</th>' +
            '<th style="padding:4px 6px;">Source</th>' +
            '<th style="padding:4px 6px;text-align:right;">Vizinhos</th>' +
            '<th style="padding:4px 6px;text-align:right;">Força total</th>' +
            '<th style="padding:4px 6px;"></th>' +
            '</tr></thead><tbody>';

    attractors.forEach((a, idx) => {
      const barW = Math.max(2, Math.round((a.n_neighbors / maxNeighbors) * 60));
      const snippet = (a.text_snippet || '').replace(/^\\[session_summary\\]\\s*/, '');
      const truncated = snippet.length > 70 ? snippet.slice(0, 70) + '...' : snippet;
      const stype = a.source_type || 'unknown';
      html += '<tr style="border-bottom:1px solid #1e293b;">' +
                '<td style="padding:4px 6px;color:#64748b;">' + (idx + 1) + '</td>' +
                '<td style="padding:4px 6px;" title="' + escapeHtml(a.id) + '">' +
                  '<span style="color:#cbd5e1;">' + escapeHtml(truncated) + '</span>' +
                '</td>' +
                '<td style="padding:4px 6px;">' +
                  '<span style="font-size:10px;color:#94a3b8;">' +
                    escapeHtml(stype.slice(0, 18)) + '</span>' +
                '</td>' +
                '<td style="padding:4px 6px;text-align:right;">' +
                  '<span style="display:inline-block;height:8px;background:#f59e0b;' +
                  'opacity:0.5;width:' + barW + 'px;margin-right:4px;border-radius:2px;"></span>' +
                  '<b style="color:#fcd34d;">' + a.n_neighbors + '</b>' +
                '</td>' +
                '<td style="padding:4px 6px;text-align:right;color:#94a3b8;">' +
                  a.total_strength +
                '</td>' +
                '<td style="padding:4px 6px;">' +
                  '<a href="#" onclick="showCoOccurrenceFor(\\'' + a.id + '\\'); return false;" ' +
                  'style="font-size:10px;color:#7dd3fc;">ver vizinhos</a>' +
                '</td>' +
              '</tr>';
    });
    html += '</tbody></table>';
    html += '<div id="cooccurrence-detail" style="margin-top:10px;"></div>';
    el.innerHTML = html;
  } catch(e) {
    console.error('loadCoOccurrence:', e);
    el.innerHTML = '<span style="color:#f87171;">Erro: ' + escapeHtml(String(e)) + '</span>';
  }
}

async function showCoOccurrenceFor(memId) {
  const detailEl = document.getElementById('cooccurrence-detail');
  if (!detailEl) return;
  detailEl.innerHTML = '<span style="color:#94a3b8;font-size:11px;">Carregando vizinhos...</span>';
  try {
    const r = await fetch('/memory/co_occurrence?session_id=' + SESSION +
                          '&id=' + encodeURIComponent(memId) + '&top_k=10');
    if (!r.ok) {
      detailEl.innerHTML = '<span style="color:#f87171;font-size:11px;">Erro</span>';
      return;
    }
    const d = await r.json();
    if (!d.results || d.results.length === 0) {
      detailEl.innerHTML = '<span style="color:#64748b;font-size:11px;">Sem vizinhos.</span>';
      return;
    }

    let html = '<div style="font-size:11px;color:#fcd34d;margin-bottom:6px;">' +
               '↳ Memórias co-occorridas com <code style="color:#7dd3fc;">' +
                 escapeHtml(memId.slice(0, 8)) + '...</code>:</div>';
    html += '<table style="width:100%;border-collapse:collapse;font-size:11px;">' +
            '<tbody>';
    d.results.forEach((n, idx) => {
      const snippet = (n.text_snippet || '').replace(/^\\[session_summary\\]\\s*/, '');
      const truncated = snippet.length > 70 ? snippet.slice(0, 70) + '...' : snippet;
      html += '<tr style="border-bottom:1px solid #1e293b;">' +
                '<td style="padding:3px 6px;color:#64748b;">' + (idx + 1) + '</td>' +
                '<td style="padding:3px 6px;color:#cbd5e1;">' + escapeHtml(truncated) + '</td>' +
                '<td style="padding:3px 6px;text-align:right;color:#fcd34d;font-weight:600;">' +
                  n.count + 'x</td>' +
              '</tr>';
    });
    html += '</tbody></table>';
    detailEl.innerHTML = html;
  } catch(e) {
    console.error('showCoOccurrenceFor:', e);
    detailEl.innerHTML = '<span style="color:#f87171;font-size:11px;">Erro: ' +
                         escapeHtml(String(e)) + '</span>';
  }
}

document.getElementById('f-status').addEventListener('change', loadAll);
document.getElementById('f-source').addEventListener('change', loadAll);
document.getElementById('f-search').addEventListener('input', () => {
  clearTimeout(window._searchTimer);
  window._searchTimer = setTimeout(loadAll, 400);
});

loadAll();
setInterval(loadStats, 30000);
</script>
</div>
<!--SIDEBAR_JS-->
</body>
</html>
"""


@router.post("/retrieve")
async def retrieve(req: RetrieveRequest):
    """Retrieval semântico direto da memória."""
    mem = get_memory(req.session_id)
    if not is_valid(mem):
        raise HTTPException(503, get_error(mem) or "MemoryStore indisponível")
    try:
        results = mem.retrieve(req.query, top_k=req.top_k, min_score=req.min_score)
        return {
            "query":   req.query,
            "results": [
                {
                    "text":  r.get("text", "")[:300],
                    "score": r.get("ranking_score", 0),
                    "layer": r.get("layer", "episodic"),
                }
                for r in results
            ],
            "total": len(results),
        }
    except Exception as e:
        raise HTTPException(500, str(e))
