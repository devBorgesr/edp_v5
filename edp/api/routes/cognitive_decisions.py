"""edp.api.routes.cognitive_decisions — Leitura das decisões cognitivas extraídas.

α (Tier 3, 13/06/2026): expõe via REST o que o cognitive_decisions extractor
produz em background (key_assertion + concepts + domain por entry). Read-only,
JSON.

Crítico: lê do scope COGNITIVE diretamente (via _cognitive_view), independente
do scope ativo do runtime. As decisions só existem no cognitive; se o scope
ativo estiver em 'sprint', a property delegada não as veria. Mesmo padrão
canônico usado pelo próprio extractor.

Endpoints:
  GET /cognitive_decisions  — lista paginada + stats (total, unique_domains)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ...runtime import get_memory, is_valid, get_error
from ...runtime.cognitive_decisions import FIELD_COGNITIVE_DECISIONS

logger = logging.getLogger("edp.api.cognitive_decisions")

router = APIRouter(tags=["cognitive_decisions"])


def _read_cognitive_entries(session_id: str):
    """Entries do scope cognitive, via _cognitive_view (não a property delegada).

    Retorna (entries, None) em sucesso ou (None, mensagem_erro) em falha.
    O scope ativo pode estar em 'sprint' — usar a view direta garante que
    enxergamos as decisions, que vivem só no cognitive.
    """
    mem = get_memory(session_id)
    if not is_valid(mem):
        return None, get_error(mem)
    cog_view = getattr(mem, "_cognitive_view", None)
    if cog_view is None:
        return None, "_cognitive_view indisponível"
    try:
        return list(cog_view.episodic.entries), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


@router.get("/cognitive_decisions")
async def list_cognitive_decisions(
    session_id: str = "default",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    domain: Optional[str] = Query(
        None, description="Filtro exato por domínio técnico (ex: 'IA cognição')"
    ),
):
    """Decisões cognitivas extraídas das entries do scope cognitive.

    Cada decisão: entry_id, key_assertion, concepts, domain, extracted_at,
    model_used. Os stats incluem unique_domains — o mesmo número que alimenta
    domain_coverage no CHI, agora inspecionável via API.

    O filtro de domínio afeta a lista paginada (total_matching), mas NÃO os
    stats globais (total, unique_domains), que refletem todo o cognitive.
    """
    entries, err = _read_cognitive_entries(session_id)
    if entries is None:
        return {"decisions": [], "stats": {}, "error": err}

    all_decisions = []
    domains_seen = set()
    for e in entries:
        if not isinstance(e, dict):
            continue
        cd = e.get(FIELD_COGNITIVE_DECISIONS)
        if not cd or not isinstance(cd, dict):
            continue
        dom = cd.get("domain", "") or ""
        domains_seen.add(dom)
        all_decisions.append({
            "entry_id":      e.get("id", ""),
            "key_assertion": cd.get("key_assertion", ""),
            "concepts":      cd.get("concepts", []),
            "domain":        dom,
            "extracted_at":  cd.get("extracted_at"),
            "model_used":    cd.get("model_used", "unknown"),
        })

    # Filtro opcional ANTES da paginação
    if domain is not None:
        filtered = [d for d in all_decisions if d["domain"] == domain]
    else:
        filtered = all_decisions

    # Mais recentes primeiro; extracted_at ausente vai ao fim
    filtered.sort(key=lambda d: d.get("extracted_at") or 0.0, reverse=True)

    total_matching = len(filtered)
    page = filtered[offset:offset + limit]

    return {
        "decisions": page,
        "stats": {
            "total":          len(all_decisions),   # todas as decisions do cognitive
            "total_matching": total_matching,        # após filtro de domínio
            "unique_domains": len(domains_seen),      # global (alimenta o CHI)
            "domains":        sorted(d for d in domains_seen if d),
            "returned":       len(page),
            "limit":          limit,
            "offset":         offset,
        },
    }
