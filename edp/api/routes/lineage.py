"""edp.api.routes.lineage — Leitura da proveniência de respostas.

α (Tier 3, 13/06/2026): expõe via REST os registros de lineage que o
WebSocket grava por resposta. Read-only, JSON. Casca fina sobre
LineageTracker.get_history e get_by_response_id, que já existem e já rodam.

Sensibilidade: estes endpoints retornam APENAS metadados (response_id,
entry_ids, scores, source_type, timestamps, model_used). NÃO contêm o texto
das memórias — são os menos sensíveis da superfície da API. Para a discussão
de exposição de conteúdo (bind, auth, CORS, snippets em /memory), ver
Dívida #48 (território do item D / PII).

Endpoints:
  GET /lineage               — últimos N registros (paginação por last_n)
  GET /lineage/{response_id} — detalhe de uma resposta (404 se ausente)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from ...runtime.lineage import get_lineage_tracker

logger = logging.getLogger("edp.api.lineage")

router = APIRouter(tags=["lineage"])


@router.get("/lineage")
async def list_lineage(
    session_id: str = "default",
    last_n: int = Query(30, ge=1, le=200),
):
    """Últimos N registros de lineage da sessão.

    Cada registro: response_id, source_entries (entry_id/score/source_type/
    timestamp), model_used, n_sources, timestamp. Apenas metadados.
    """
    try:
        tracker = get_lineage_tracker()
        records = tracker.get_history(session_id=session_id, last_n=last_n)
        return {
            "lineage": records,
            "stats": {
                "returned": len(records),
                "last_n":   last_n,
            },
        }
    except Exception as e:
        logger.warning("[lineage] list falhou: %s", e)
        raise HTTPException(500, str(e))


@router.get("/lineage/{response_id}")
async def get_lineage_detail(
    response_id: str,
    session_id: str = "default",
):
    """Proveniência de uma resposta pelo response_id — completo ou prefixo.

    α (13/06/2026): aceita prefixo porque o log do EDP mostra só os 8 primeiros
    chars (ex: 'response_id=5b56a95b'). Resolução:
      - 0 matches  → 404
      - 1 match    → retorna o registro
      - >1 matches → 409 (prefixo ambíguo; peça mais chars)
    Um UUID completo (36 chars) casa só consigo mesmo, então o mesmo caminho
    serve para ID exato e prefixo.
    """
    if not response_id or not response_id.strip():
        raise HTTPException(400, "response_id vazio")
    try:
        tracker = get_lineage_tracker()
        # limit=2: basta para o endpoint distinguir 1 de "vários"
        matches = tracker.find_by_prefix(
            response_id, session_id=session_id, limit=2
        )
    except Exception as e:
        logger.warning("[lineage] detail falhou: %s", e)
        raise HTTPException(500, str(e))
    if not matches:
        raise HTTPException(
            404, f"lineage não encontrado: response_id={response_id}"
        )
    if len(matches) > 1:
        raise HTTPException(
            409,
            f"prefixo ambíguo: '{response_id}' casa com múltiplos registros — "
            f"forneça mais caracteres do response_id",
        )
    return matches[0]
