"""edp.api.routes.providers — Endpoints de descoberta de providers + tools."""
from __future__ import annotations

import os
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["providers"])


class ValidateProviderRequest(BaseModel):
    provider: str           = Field(..., description="ollama | anthropic | openai")
    api_key:  Optional[str] = None
    base_url: Optional[str] = None
    model:    str


@router.get("/providers")
async def list_providers_endpoint():
    """Lista providers disponíveis no sistema."""
    try:
        from ...llm.providers import list_providers
        return {
            "available": list_providers(),
            "anthropic_models": [
                "claude-opus-4-7", "claude-opus-4-6",
                "claude-sonnet-4-6", "claude-haiku-4-5",
                "claude-3-5-sonnet", "claude-3-5-haiku",
            ],
            "has_anthropic_key_in_env": bool(os.environ.get("ANTHROPIC_API_KEY")),
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/providers/validate")
async def validate_provider(req: ValidateProviderRequest):
    """Valida credenciais de um provider sem consumir muito (1 token)."""
    try:
        from ...llm.providers import get_provider
        provider = get_provider(
            name=req.provider,
            model=req.model,
            api_key=req.api_key,
            base_url=req.base_url,
        )
        ok = provider.validate()
        return {
            "provider": req.provider,
            "model":    req.model,
            "valid":    ok,
        }
    except Exception as e:
        return {
            "provider": req.provider,
            "model":    req.model,
            "valid":    False,
            "error":    f"{type(e).__name__}: {e}",
        }


@router.get("/tools")
async def list_tools_endpoint():
    """Lista tools registradas e seus schemas."""
    try:
        from ...tools import list_schemas, list_names
        return {
            "tools":   list_names(),
            "schemas": list_schemas("anthropic"),
        }
    except Exception as e:
        raise HTTPException(500, str(e))
