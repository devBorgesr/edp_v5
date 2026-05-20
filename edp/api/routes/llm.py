"""edp.api.routes.llm — Endpoints de conexão LLM e chat síncrono."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...runtime import get_runtime, get_memory, is_valid, get_error
from ..schemas import ConnectRequest, ConnectResponse, ChatRequest, ChatResponse

router = APIRouter(tags=["llm"])


@router.post("/connect", response_model=ConnectResponse)
async def connect_llm(req: ConnectRequest):
    """Conecta um provider LLM ao runtime da sessão."""
    runtime = get_runtime(req.session_id)
    if not is_valid(runtime):
        raise HTTPException(503, get_error(runtime) or "Runtime indisponível")

    try:
        if req.provider == "ollama":
            ok = runtime.connect_ollama(req.model, req.base_url)
        elif req.provider == "lm_studio":
            ok = runtime.connect_lm_studio(req.model, req.base_url)
        elif req.provider == "openai":
            ok = runtime.connect_openai(req.api_key, req.model)
        elif req.provider == "anthropic":
            ok = runtime.connect_anthropic(req.api_key, req.model)
        else:
            ok = runtime.connect_custom(req.base_url, req.model)
    except Exception as e:
        raise HTTPException(500, f"Falha ao conectar: {e}")

    if not ok:
        raise HTTPException(
            503,
            f"Servidor {req.provider} indisponível em {req.base_url}"
        )

    models = runtime.list_available_models()
    return ConnectResponse(
        connected=True,
        provider=req.provider,
        model=req.model,
        models=models,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    Chat síncrono com memória cognitiva EDP.
    Sempre responde — mesmo sem LLM conectado, retorna análise cognitiva.
    """
    runtime = get_runtime(req.session_id)
    memory  = get_memory(req.session_id)

    # Auto-connect LLM se especificado
    if is_valid(runtime) and req.provider and req.model:
        if not runtime.is_connected():
            try:
                if req.provider == "ollama":
                    runtime.connect_ollama(req.model, req.base_url or "http://localhost:11434")
                elif req.provider == "lm_studio":
                    runtime.connect_lm_studio(req.model, req.base_url or "http://localhost:1234")
            except Exception as e:
                print(f"[/chat] connect falhou: {e}")

    # Pipeline cognitivo (sempre executa)
    compression_pct  = 0.0
    memory_hits      = 0
    retrieved_blocks: list = []

    try:
        from ...pipeline import run_pipeline
        pres = run_pipeline(req.message, req.message, session_id=req.session_id)
        compression_pct = pres.reduction_pct
    except Exception as e:
        print(f"[/chat] pipeline falhou: {e}")

    if is_valid(memory):
        try:
            retrieved = memory.retrieve(req.message, top_k=5, min_score=0.20)
            retrieved_blocks = [r.get("text", "")[:200] for r in retrieved]
            memory_hits = len(retrieved_blocks)
        except Exception:
            pass

    # Tenta LLM
    if is_valid(runtime) and runtime.is_connected():
        try:
            response = runtime.chat(req.message, system=req.system or "")
            return ChatResponse(
                text=response.text,
                session_id=response.session_id,
                model=response.model,
                latency_ms=response.latency_ms,
                memory_hits=response.memory_hits or memory_hits,
                tokens_generated=response.tokens_generated,
                compression_pct=response.compression_pct or compression_pct,
                mode=response.mode,
            )
        except Exception as e:
            print(f"[/chat] LLM falhou: {e}")

    # Fallback cognitivo
    text_parts = ["[EDP — análise cognitiva sem LLM]\n"]
    text_parts.append(f"Compressão: {compression_pct:.0f}% redução de tokens.\n")
    if retrieved_blocks:
        text_parts.append(f"\nMemória recuperada ({memory_hits} entradas):\n")
        for i, blk in enumerate(retrieved_blocks[:3], 1):
            text_parts.append(f"  [{i}] {blk}\n")
    else:
        text_parts.append("\nNenhuma memória anterior relevante.\n")
    text_parts.append("\nConecte um LLM via /connect para respostas geradas.")

    # Armazena mesmo sem LLM
    if is_valid(memory):
        try:
            memory.add(req.message, score=0.5, prioridade="media")
            if hasattr(memory.episodic, "flush"):
                memory.episodic.flush()
        except Exception:
            pass

    return ChatResponse(
        text="".join(text_parts),
        session_id=req.session_id,
        model="edp-cognitive-only",
        latency_ms=0.0,
        memory_hits=memory_hits,
        tokens_generated=0,
        compression_pct=compression_pct,
        mode="no_llm",
    )
