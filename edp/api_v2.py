"""
api_v2.py — EDP v3.3 REST API + Dashboard

Endpoints:
  POST /chat                — chat com contexto cognitivo EDP
  GET  /health              — health completo do runtime
  GET  /metrics             — métricas de performance
  GET  /memory              — entradas de memória da sessão
  POST /memory/add          — adiciona entrada manual
  POST /retrieve            — retrieval semântico direto
  GET  /snapshot            — snapshot geracional atual
  POST /snapshot/create     — força checkpoint
  GET  /modes               — histórico de modos operacionais
  WS   /ws/chat/{session}   — streaming de chat via WebSocket
  GET  /dashboard           — painel HTML de observabilidade

Uso:
  uvicorn edp.api_v2:app --host 0.0.0.0 --port 8000 --reload

  # Ou via run.py:
  python run.py serve

Demo rápido:
  curl -X POST http://localhost:8000/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "Como funciona RAG?", "session_id": "demo"}'
"""
from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

try:
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, JSONResponse
    from pydantic import BaseModel, Field
    _FASTAPI_OK = True
except ImportError:
    _FASTAPI_OK = False

from .config import API_HOST, API_PORT

# ── App ───────────────────────────────────────────────────────────────────────

if _FASTAPI_OK:
    app = FastAPI(
        title="EDP v3.3 — Cognitive Runtime API",
        description="Persistent Cognitive Layer for Local AI",
        version="3.3.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )
else:
    app = None  # FastAPI não instalado — usar API v1 (api.py)

# ── Runtime global (lazy init por session_id) ──────────────────────────────────

_runtimes: Dict[str, object]  = {}
_memories: Dict[str, object]  = {}

def _get_runtime(session_id: str) -> object:
    """
    Retorna runtime ou um dict {error: str} se falhou.
    NUNCA retorna None silenciosamente — propaga o erro de init.
    """
    if session_id not in _runtimes:
        try:
            from .llm_adapter import EDPRuntime
            _runtimes[session_id] = EDPRuntime(session_id=session_id)
            print(f"[runtime] inicializado: session={session_id}")
        except Exception as e:
            import traceback
            err = f"{type(e).__name__}: {e}"
            print(f"[runtime] FALHA init session={session_id}: {err}")
            traceback.print_exc()
            _runtimes[session_id] = {"__init_error__": err}
    return _runtimes[session_id]

def _get_memory(session_id: str) -> object:
    """
    Retorna memory ou um dict {error: str} se falhou.
    """
    if session_id not in _memories:
        try:
            from .memory import MemoryStore
            _memories[session_id] = MemoryStore(session_id)
            print(f"[memory] inicializado: session={session_id}")
        except Exception as e:
            import traceback
            err = f"{type(e).__name__}: {e}"
            print(f"[memory] FALHA init session={session_id}: {err}")
            traceback.print_exc()
            _memories[session_id] = {"__init_error__": err}
    return _memories[session_id]

def _is_valid(obj) -> bool:
    """True se o objeto não é um dict de erro."""
    return obj is not None and not (isinstance(obj, dict) and "__init_error__" in obj)


# ── Modelos Pydantic ──────────────────────────────────────────────────────────

if _FASTAPI_OK:
    class ChatRequest(BaseModel):
        message:    str              = Field(..., min_length=1, max_length=10000)
        session_id: str              = Field(default="default", max_length=64)
        system:     Optional[str]    = None
        provider:   Optional[str]    = None  # "ollama" | "lm_studio" | "openai"
        model:      Optional[str]    = None
        base_url:   Optional[str]    = None
        stream:     bool             = False

    class ChatResponseModel(BaseModel):
        text:             str
        session_id:       str
        model:            str
        latency_ms:       float
        memory_hits:      int
        tokens_generated: int
        compression_pct:  float
        mode:             str

    class AddMemoryRequest(BaseModel):
        text:       str   = Field(..., min_length=1)
        score:      float = Field(default=0.5, ge=0.0, le=1.0)
        prioridade: str   = Field(default="media")
        session_id: str   = Field(default="default")

    class RetrieveRequest(BaseModel):
        query:      str   = Field(..., min_length=1)
        session_id: str   = Field(default="default")
        top_k:      int   = Field(default=5, ge=1, le=20)
        min_score:  float = Field(default=0.20, ge=0.0, le=1.0)

    class ConnectRequest(BaseModel):
        provider:   str   = "ollama"
        model:      str   = "llama3:8b"
        base_url:   str   = "http://localhost:11434"
        api_key:    str   = ""
        session_id: str   = "default"


# ── Endpoints ─────────────────────────────────────────────────────────────────

if _FASTAPI_OK and app:

    @app.get("/health")
    async def health():
        """Health completo do runtime cognitivo."""
        try:
            from . import metrics as M
            m = M.summary(from_file=False)
        except Exception:
            m = {}
        return {
            "status":    "ok",
            "version":   "3.3.0",
            "timestamp": time.time(),
            "sessions":  list(_runtimes.keys()),
            "metrics":   m,
        }

    @app.post("/connect")
    async def connect_llm(req: ConnectRequest):
        """Conecta um LLM local ao runtime EDP."""
        runtime = _get_runtime(req.session_id)
        if runtime is None:
            raise HTTPException(503, "EDPRuntime indisponível")

        try:
            from .llm_adapter import LLMProvider
            if req.provider == "ollama":
                ok = runtime.connect_ollama(req.model, req.base_url)
            elif req.provider == "lm_studio":
                ok = runtime.connect_lm_studio(req.model, req.base_url)
            elif req.provider == "openai":
                ok = runtime.connect_openai(req.api_key, req.model)
            else:
                ok = runtime.connect_custom(req.base_url, req.model)
        except Exception as e:
            raise HTTPException(500, str(e))

        if not ok:
            raise HTTPException(503, f"Servidor {req.provider} indisponível em {req.base_url}")

        models = runtime.list_available_models()
        return {"connected": True, "provider": req.provider, "model": req.model, "models": models}

    @app.post("/chat", response_model=ChatResponseModel)
    async def chat(req: ChatRequest):
        """
        Chat com memória cognitiva EDP.
        Sempre responde — mesmo sem LLM conectado, retorna análise cognitiva.
        """
        runtime = _get_runtime(req.session_id)
        memory  = _get_memory(req.session_id)

        # Conecta LLM se especificado e ainda não conectado
        if runtime is not None and req.provider and req.model:
            if not runtime.is_connected():
                try:
                    if req.provider == "ollama":
                        runtime.connect_ollama(req.model, req.base_url or "http://localhost:11434")
                    elif req.provider == "lm_studio":
                        runtime.connect_lm_studio(req.model, req.base_url or "http://localhost:1234")
                except Exception as e:
                    print(f"[/chat] connect falhou: {e}")

        # Sempre executa pipeline cognitivo
        compression_pct = 0.0
        memory_hits     = 0
        retrieved_blocks: list = []

        try:
            from .pipeline import run_pipeline
            pres = run_pipeline(req.message, req.message, session_id=req.session_id)
            compression_pct = pres.reduction_pct
        except Exception as e:
            print(f"[/chat] pipeline falhou: {e}")

        if memory is not None:
            try:
                retrieved = memory.retrieve(req.message, top_k=5, min_score=0.20)
                retrieved_blocks = [r.get("text", "")[:200] for r in retrieved]
                memory_hits = len(retrieved_blocks)
            except Exception:
                pass

        # Tenta LLM se conectado
        llm_used = False
        if runtime is not None and runtime.is_connected():
            try:
                response = runtime.chat(req.message, system=req.system or "")
                return ChatResponseModel(
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

        # Fallback: resposta cognitiva sem LLM
        text_parts = ["[EDP — modo análise cognitiva sem LLM]\n"]
        text_parts.append(f"Compressão aplicada: {compression_pct:.0f}% redução de tokens.\n")
        if retrieved_blocks:
            text_parts.append(f"\nMemória recuperada ({memory_hits} entries):\n")
            for i, blk in enumerate(retrieved_blocks[:3], 1):
                text_parts.append(f"  [{i}] {blk}\n")
        else:
            text_parts.append("\nNenhuma memória anterior relevante encontrada.\n")
        text_parts.append("\nPara obter respostas geradas, conecte um LLM via /connect.")

        # Armazena na memória
        if memory is not None:
            try:
                memory.add(req.message, score=0.5, prioridade="media")
            except Exception:
                pass

        return ChatResponseModel(
            text="".join(text_parts),
            session_id=req.session_id,
            model="edp-cognitive-only",
            latency_ms=0.0,
            memory_hits=memory_hits,
            tokens_generated=0,
            compression_pct=compression_pct,
            mode="no_llm",
        )

    @app.websocket("/ws/chat/{session_id}")
    async def ws_chat(websocket: WebSocket, session_id: str):
        """
        WebSocket robusto:
          - Valida runtime/memory antes de usar
          - Timeout no stream_chat
          - finally sempre envia 'done'
          - Logs estruturados de cada stage
        """
        import asyncio
        await websocket.accept()
        print(f"[WS] conectado session={session_id}")

        runtime = _get_runtime(session_id)
        memory  = _get_memory(session_id)

        # ── Health check inicial ──
        runtime_ok = _is_valid(runtime)
        memory_ok  = _is_valid(memory)

        if not runtime_ok:
            err = runtime.get("__init_error__") if isinstance(runtime, dict) else "runtime None"
            print(f"[WS] runtime inválido: {err}")
            await websocket.send_json({"type": "warn", "error": f"runtime degradado: {err}"})

        if not memory_ok:
            err = memory.get("__init_error__") if isinstance(memory, dict) else "memory None"
            print(f"[WS] memory inválido: {err}")

        try:
            while True:
                try:
                    data = await websocket.receive_json()
                except WebSocketDisconnect:
                    raise
                except Exception as e:
                    print(f"[WS] receive_json falhou: {e}")
                    break

                message = (data.get("message") or "").strip()
                print(f"[WS] msg recebida session={session_id} len={len(message)}")

                if not message:
                    await websocket.send_json({"type": "error", "error": "mensagem vazia"})
                    continue

                # Variáveis do turno (resetadas a cada mensagem)
                full_text       = ""
                llm_used        = False
                pipeline_ok     = False
                memory_hits     = 0
                compression_pct = 0.0
                retrieved_blocks: list = []

                try:
                    await websocket.send_json({
                        "type":       "start",
                        "session_id": session_id,
                        "stage":      "pipeline",
                    })

                    # ── Stage 1: Pipeline cognitivo (com timeout) ──
                    try:
                        from .pipeline import run_pipeline
                        loop = asyncio.get_event_loop()
                        pres = await asyncio.wait_for(
                            loop.run_in_executor(
                                None,
                                lambda: run_pipeline(message, message, session_id=session_id)
                            ),
                            timeout=60.0,  # 60s — cold start do modelo de embedding leva ~40s
                        )
                        compression_pct = pres.reduction_pct
                        pipeline_ok = True
                        print(f"[WS] pipeline ok | reduction={compression_pct:.1f}%")
                    except asyncio.TimeoutError:
                        print(f"[WS] pipeline TIMEOUT (60s) — pulando, segue para LLM")
                    except Exception as e:
                        print(f"[WS] pipeline falhou: {type(e).__name__}: {e}")

                    # ── Stage 2: Retrieval da memória ──
                    if memory_ok:
                        try:
                            retrieved = memory.retrieve(message, top_k=5, min_score=0.20)
                            retrieved_blocks = [r.get("text", "")[:200] for r in retrieved]
                            memory_hits = len(retrieved_blocks)
                            print(f"[WS] memory | hits={memory_hits}")
                        except Exception as e:
                            print(f"[WS] memory.retrieve falhou: {e}")

                    await websocket.send_json({
                        "type":            "pipeline_done",
                        "compression_pct": round(compression_pct, 1),
                        "memory_hits":     memory_hits,
                        "retrieved":       retrieved_blocks[:3],
                        "pipeline_ok":     pipeline_ok,
                    })

                    # ── Stage 3: LLM com timeout AMPLO ──
                    if runtime_ok and runtime.is_connected():
                        print(f"[WS] LLM stream iniciando")
                        await websocket.send_json({
                            "type":  "llm_start",
                            "model": runtime._llm_config.model,
                        })
                        try:
                            chunks: list = []

                            # Stream com timeout AMPLO para LLMs lentos em CPU
                            #  - Primeiro chunk: até 90s (phi3 leva ~30-40s no load + prompt_eval)
                            #  - Chunks seguintes: 30s cada
                            #  - Total: 300s
                            async def _stream_with_timeout():
                                loop = asyncio.get_event_loop()
                                gen  = runtime.stream_chat(message)
                                first_chunk = True

                                async def _next_chunk():
                                    return await loop.run_in_executor(None, lambda: next(gen, None))

                                while True:
                                    # Primeiro chunk tem timeout maior (cold start do modelo)
                                    chunk_timeout = 90.0 if first_chunk else 30.0
                                    try:
                                        chunk = await asyncio.wait_for(_next_chunk(), timeout=chunk_timeout)
                                    except asyncio.TimeoutError:
                                        kind = "primeiro" if first_chunk else "seguinte"
                                        print(f"[WS] LLM {kind} chunk timeout ({chunk_timeout}s)")
                                        break
                                    if chunk is None:
                                        break
                                    if first_chunk:
                                        print(f"[WS] LLM primeiro chunk recebido")
                                        first_chunk = False
                                    chunks.append(chunk)
                                    await websocket.send_json({"type": "chunk", "text": chunk})

                            await asyncio.wait_for(_stream_with_timeout(), timeout=300.0)
                            full_text = "".join(chunks)
                            llm_used  = bool(full_text)
                            print(f"[WS] LLM done | tokens~{len(full_text.split())}")
                        except asyncio.TimeoutError:
                            print(f"[WS] LLM TIMEOUT TOTAL (300s)")
                            await websocket.send_json({
                                "type":  "warn",
                                "error": "LLM timeout (300s) - modelo muito lento, tente um modelo menor",
                            })
                        except Exception as e:
                            print(f"[WS] LLM falhou: {type(e).__name__}: {e}")
                            await websocket.send_json({
                                "type":  "warn",
                                "error": f"LLM indisponivel: {e}",
                            })

                    # ── Fallback: resposta cognitiva (sempre, se LLM não respondeu) ──
                    if not llm_used:
                        print(f"[WS] gerando resposta cognitiva (sem LLM ou LLM falhou)")
                        parts = ["[EDP — modo análise cognitiva]\n\n"]

                        if not runtime_ok:
                            parts.append("⚠ Runtime indisponível — apenas análise estrutural.\n\n")
                        elif not runtime.is_connected():
                            parts.append("⚠ Nenhum LLM conectado.\n\n")

                        if pipeline_ok:
                            parts.append(f"Pipeline: {compression_pct:.0f}% compressão de tokens.\n")
                        if retrieved_blocks:
                            parts.append(f"\nMemória recuperada ({memory_hits} entradas):\n")
                            for i, blk in enumerate(retrieved_blocks[:3], 1):
                                parts.append(f"  [{i}] {blk}\n")
                        else:
                            parts.append("Nenhuma memória anterior relevante.\n")

                        parts.append(
                            "\nPara obter respostas geradas, conecte um LLM "
                            "(Ollama/LM Studio) pelo formulário acima."
                        )
                        full_text = "".join(parts)
                        # Stream chunks de feedback visual
                        for line in full_text.split("\n"):
                            await websocket.send_json({"type": "chunk", "text": line + "\n"})

                    # ── Stage 4: Armazena na memória ──
                    if memory_ok and full_text and llm_used:
                        try:
                            combined = f"Q: {message[:200]}\nA: {full_text[:400]}"
                            memory.add(combined, score=0.65, prioridade="media")
                            print(f"[WS] armazenado em memória")
                        except Exception as e:
                            print(f"[WS] memory.add falhou: {e}")

                    metrics = {}
                    if runtime_ok:
                        try:
                            metrics = runtime.llm_metrics()
                        except Exception:
                            pass

                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    print(f"[WS] erro no turno: {e}")
                    try:
                        await websocket.send_json({
                            "type":  "error",
                            "error": f"{type(e).__name__}: {e}",
                        })
                    except Exception:
                        pass
                finally:
                    # GARANTE 'done' SEMPRE — frontend nunca fica esperando
                    try:
                        await websocket.send_json({
                            "type":            "done",
                            "text":            full_text,
                            "llm_used":        llm_used,
                            "memory_hits":     memory_hits,
                            "compression_pct": round(compression_pct, 1),
                            "metrics":         metrics if 'metrics' in dir() else {},
                        })
                        print(f"[WS] done session={session_id} llm_used={llm_used}")
                    except Exception as e:
                        print(f"[WS] envio de 'done' falhou: {e}")
                        break

        except WebSocketDisconnect:
            print(f"[WS] desconectado session={session_id}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[WS] erro fatal: {e}")

    @app.get("/memory")
    async def get_memory(session_id: str = "default", top_k: int = 20):
        """Lista entradas de memória da sessão."""
        mem = _get_memory(session_id)
        if mem is None:
            return {"entries": [], "stats": {}}
        try:
            stats = mem.stats()
            entries = [
                {
                    "id":       e.get("id", ""),
                    "text":     e.get("text", "")[:200],
                    "prioridade": e.get("prioridade", "media"),
                    "acessos":  e.get("acessos", 0),
                    "score":    e.get("score_inicial", 0),
                }
                for e in list(mem.episodic.entries)[:top_k]
            ]
            return {"entries": entries, "stats": stats}
        except Exception as e:
            raise HTTPException(500, str(e))

    @app.post("/memory/add")
    async def add_memory(req: AddMemoryRequest):
        """Adiciona entrada manual à memória."""
        mem = _get_memory(req.session_id)
        if mem is None:
            raise HTTPException(503, "MemoryStore indisponível")
        try:
            entry = mem.add(req.text, score=req.score, prioridade=req.prioridade)
            return {"added": True, "id": entry.get("id", "")}
        except Exception as e:
            raise HTTPException(500, str(e))

    @app.post("/retrieve")
    async def retrieve(req: RetrieveRequest):
        """Retrieval semântico direto da memória."""
        mem = _get_memory(req.session_id)
        if mem is None:
            raise HTTPException(503, "MemoryStore indisponível")
        try:
            results = mem.retrieve(req.query, top_k=req.top_k, min_score=req.min_score)
            return {
                "query":   req.query,
                "results": [
                    {
                        "text":   r.get("text", "")[:300],
                        "score":  r.get("ranking_score", 0),
                        "layer":  r.get("layer", "episodic"),
                    }
                    for r in results
                ],
                "total": len(results),
            }
        except Exception as e:
            raise HTTPException(500, str(e))

    @app.get("/metrics")
    async def get_metrics():
        """Métricas de performance do sistema."""
        try:
            from . import metrics as M
            return M.summary(from_file=True)
        except Exception as e:
            raise HTTPException(500, str(e))

    @app.get("/snapshot")
    async def get_snapshot(session_id: str = "default"):
        """Estado atual do snapshot manager."""
        runtime = _get_runtime(session_id)
        if runtime is None:
            return {"error": "runtime indisponível"}
        health = runtime.health()
        return {
            "session_id": session_id,
            "memory":     health.get("memory", {}),
            "metrics":    health.get("metrics", {}),
        }

    @app.get("/modes")
    async def get_modes():
        """Histórico de modos operacionais do MetaStability."""
        # Lê do metrics log
        try:
            from .config import METRICS_LOG
            from pathlib import Path
            import json as _json
            log = Path(METRICS_LOG)
            if not log.exists():
                return {"modes": [], "current": "unknown"}
            modes = []
            for line in log.read_text(encoding="utf-8").splitlines()[-100:]:
                try:
                    e = _json.loads(line)
                    if "mode" in e.get("event", ""):
                        modes.append(e)
                except Exception:
                    pass
            return {"modes": modes[-20:], "total_logged": len(modes)}
        except Exception:
            return {"modes": [], "current": "unknown"}

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard():
        """Painel de observabilidade cognitiva."""
        return HTMLResponse(_DASHBOARD_HTML)


# ── Dashboard HTML ─────────────────────────────────────────────────────────────

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EDP v3.3 - Cognitive Runtime Dashboard</title>
<style>
  :root {
    --bg: #0a0a0f; --surface: #12121a; --border: #1e1e2e;
    --accent: #7c3aed; --accent2: #06b6d4; --text: #e2e8f0;
    --muted: #64748b; --ok: #22c55e; --warn: #f59e0b; --err: #ef4444;
    --font: 'JetBrains Mono', 'Fira Code', monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--font);
         font-size: 13px; min-height: 100vh; }
  header { background: var(--surface); border-bottom: 1px solid var(--border);
           padding: 16px 24px; display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 18px; color: var(--accent); letter-spacing: 1px; }
  header .badge { background: var(--accent); color: white; border-radius: 4px;
                  padding: 2px 8px; font-size: 11px; }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ok);
                animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
  main { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
         gap: 16px; padding: 24px; }
  .card { background: var(--surface); border: 1px solid var(--border);
          border-radius: 8px; padding: 16px; }
  .card h2 { font-size: 12px; text-transform: uppercase; letter-spacing: 1px;
             color: var(--muted); margin-bottom: 12px; }
  .metric { display: flex; justify-content: space-between; padding: 6px 0;
            border-bottom: 1px solid var(--border); }
  .metric:last-child { border: none; }
  .metric .label { color: var(--muted); }
  .metric .value { color: var(--text); font-weight: bold; }
  .value.ok   { color: var(--ok); }
  .value.warn { color: var(--warn); }
  .value.err  { color: var(--err); }
  .bar-wrap { margin-top: 8px; }
  .bar-label { display: flex; justify-content: space-between; color: var(--muted);
               margin-bottom: 4px; font-size: 11px; }
  .bar { background: var(--border); border-radius: 4px; height: 6px; overflow: hidden; }
  .bar-fill { height: 100%; border-radius: 4px; transition: width 0.5s ease; }
  .chat-area { grid-column: 1 / -1; }
  .chat-box { height: 320px; overflow-y: auto; background: var(--bg);
              border: 1px solid var(--border); border-radius: 6px; padding: 12px;
              margin-bottom: 8px; }
  .msg { margin-bottom: 12px; }
  .msg.user .bubble { background: var(--accent); color: white; border-radius: 12px 12px 2px 12px;
                      padding: 8px 12px; display: inline-block; max-width: 80%; }
  .msg.assistant .bubble { background: var(--surface); border: 1px solid var(--border);
                           border-radius: 12px 12px 12px 2px; padding: 8px 12px;
                           display: inline-block; max-width: 80%; white-space: pre-wrap; }
  .msg.user { text-align: right; }
  .input-row { display: flex; gap: 8px; }
  .input-row input { flex: 1; background: var(--surface); border: 1px solid var(--border);
                     color: var(--text); border-radius: 6px; padding: 10px 14px; font-family: inherit; }
  .input-row input:focus { outline: none; border-color: var(--accent); }
  .input-row button { background: var(--accent); color: white; border: none;
                      border-radius: 6px; padding: 10px 20px; cursor: pointer;
                      font-family: inherit; font-size: 13px; }
  .input-row button:hover { opacity: 0.85; }
  .connect-row { display: flex; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
  .connect-row input { flex: 1; min-width: 120px; background: var(--surface);
                       border: 1px solid var(--border); color: var(--text);
                       border-radius: 6px; padding: 8px 12px; font-family: inherit; }
  .connect-row select { background: var(--surface); border: 1px solid var(--border);
                        color: var(--text); border-radius: 6px; padding: 8px 12px; }
  .connect-row button { background: var(--accent2); color: white; border: none;
                        border-radius: 6px; padding: 8px 16px; cursor: pointer; }
  #conn-status { font-size: 11px; color: var(--muted); }
  #conn-status.ok  { color: var(--ok); }
  #conn-status.err { color: var(--err); }
  pre { background: var(--bg); border: 1px solid var(--border); border-radius: 4px;
        padding: 8px; overflow-x: auto; font-size: 11px; color: var(--muted);
        max-height: 200px; overflow-y: auto; }
</style>
</head>
<body>

<header>
  <div class="status-dot" id="status-dot"></div>
  <h1>EDP v3.3</h1>
  <span class="badge">Cognitive Runtime</span>
  <span style="margin-left:auto;color:var(--muted);font-size:11px" id="last-update">-</span>
</header>

<main>

  <!-- HEALTH -->
  <div class="card">
    <h2>Runtime Health</h2>
    <div id="health-metrics">
      <div class="metric"><span class="label">Status</span><span class="value" id="h-status">...</span></div>
      <div class="metric"><span class="label">Versao</span><span class="value" id="h-version">...</span></div>
      <div class="metric"><span class="label">Sessoes ativas</span><span class="value" id="h-sessions">...</span></div>
    </div>
  </div>

  <!-- MEMÓRIA -->
  <div class="card">
    <h2>Memoria Cognitiva</h2>
    <div id="memory-metrics">
      <div class="metric"><span class="label">Episodica</span><span class="value" id="m-episodic">...</span></div>
      <div class="metric"><span class="label">Semantica</span><span class="value" id="m-semantic">...</span></div>
      <div class="metric"><span class="label">Working</span><span class="value" id="m-working">...</span></div>
      <div class="metric"><span class="label">Total</span><span class="value" id="m-total">...</span></div>
    </div>
  </div>

  <!-- LLM METRICS -->
  <div class="card">
    <h2>LLM Performance</h2>
    <div class="metric"><span class="label">Modelo</span><span class="value" id="llm-model">-</span></div>
    <div class="metric"><span class="label">Provider</span><span class="value" id="llm-provider">-</span></div>
    <div class="metric"><span class="label">Latencia media</span><span class="value" id="llm-lat">-</span></div>
    <div class="metric"><span class="label">Requests total</span><span class="value" id="llm-req">-</span></div>
    <div class="metric"><span class="label">Memory hits medio</span><span class="value" id="llm-hits">-</span></div>
    <div class="metric"><span class="label">Erros</span><span class="value" id="llm-err">-</span></div>
  </div>

  <!-- RETRIEVAL METRICS -->
  <div class="card">
    <h2>Retrieval Stats</h2>
    <div id="ret-metrics">
      <div class="metric"><span class="label">Score medio</span><span class="value" id="r-score">...</span></div>
      <div class="metric"><span class="label">Compressao media</span><span class="value" id="r-comp">...</span></div>
      <div class="metric"><span class="label">Cache hits</span><span class="value" id="r-cache">...</span></div>
    </div>
  </div>

  <!-- CHAT INTEGRADO -->
  <div class="card chat-area">
    <h2>Chat Cognitivo</h2>

    <div class="connect-row">
      <select id="provider"><option value="ollama">Ollama</option>
        <option value="lm_studio">LM Studio</option>
        <option value="openai">OpenAI</option></select>
      <input id="model-input" placeholder="modelo (ex: llama3:8b)" value="llama3:8b">
      <input id="url-input" placeholder="URL base" value="http://localhost:11434">
      <button onclick="connectLLM()">Conectar</button>
      <span id="conn-status">Desconectado</span>
    </div>

    <div class="chat-box" id="chat-box"></div>

    <div class="input-row">
      <input id="chat-input" placeholder="Digite sua mensagem..." onkeydown="if(event.key==='Enter') sendMsg()">
      <button onclick="sendMsg()" id="send-btn">Enviar</button>
    </div>
  </div>

  <!-- SNAPSHOT -->
  <div class="card">
    <h2>Snapshot Info</h2>
    <pre id="snapshot-data">Carregando...</pre>
  </div>

  <!-- MÉTRICAS RAW -->
  <div class="card">
    <h2>Metricas de Sistema</h2>
    <pre id="raw-metrics">Carregando...</pre>
  </div>

</main>

<script>
window.onerror = function(msg, src, line, col, err) {
  console.error('[GLOBAL ERROR]', msg, 'line:', line, 'col:', col, err);
  return false;
};
console.log('[dashboard] script carregado');

(function() {
'use strict';

const SESSION = "default";

async function fetchJSON(url) {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return r.json();
  } catch { return null; }
}

function el(id) { return document.getElementById(id); }

function setVal(id, val, cls) {
  const e = el(id);
  if (!e) return;
  e.textContent = val;
  e.className = 'value' + (cls ? ' ' + cls : '');
}

function colorStatus(val) {
  if (typeof val === 'number') {
    if (val > 0.7) return 'err';
    if (val > 0.4) return 'warn';
    return 'ok';
  }
  if (val === 'ok' || val === 'NORMAL') return 'ok';
  if (val === 'EMERGENCY' || val === 'CRITICAL') return 'err';
  return 'warn';
}

async function refreshHealth() {
  const data = await fetchJSON('/health');
  if (!data) { setVal('h-status', 'OFFLINE', 'err'); return; }
  setVal('h-status', data.status, 'ok');
  setVal('h-version', data.version, '');
  setVal('h-sessions', (data.sessions || []).length, '');
  el('last-update').textContent = new Date().toLocaleTimeString();
  document.getElementById('status-dot').style.background = '#22c55e';
}

async function refreshMemory() {
  const data = await fetchJSON('/memory?session_id=' + SESSION + '&top_k=5');
  if (!data || !data.stats) return;
  const s = data.stats;
  setVal('m-episodic', s.episodic ?? '?', '');
  setVal('m-semantic', s.semantic ?? '?', '');
  setVal('m-working',  s.working  ?? '?', '');
  setVal('m-total',    s.total    ?? '?', '');
}

async function refreshMetrics() {
  const data = await fetchJSON('/metrics');
  if (!data) return;
  el('raw-metrics').textContent = JSON.stringify(data, null, 2).slice(0, 1200);
  if (data.retrieval_score) {
    const rs = data.retrieval_score;
    setVal('r-score', rs.mean?.toFixed(3) ?? '?', '');
  }
  if (data.compression_ratio) {
    setVal('r-comp', (data.compression_ratio.mean * 100).toFixed(1) + '%', '');
  }
  if (data.cache_hit) {
    setVal('r-cache', (data.cache_hit.mean * 100).toFixed(0) + '%', '');
  }
}

async function refreshSnapshot() {
  const data = await fetchJSON('/snapshot?session_id=' + SESSION);
  if (!data) return;
  el('snapshot-data').textContent = JSON.stringify(data, null, 2).slice(0, 800);
}

async function connectLLM() {
  const provider = el('provider').value;
  const model    = el('model-input').value;
  const url      = el('url-input').value;
  el('conn-status').textContent = 'Conectando...';
  el('conn-status').className   = '';
  try {
    const r = await fetch('/connect', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({provider, model, base_url: url, session_id: SESSION}),
    });
    if (r.ok) {
      const d = await r.json();
      el('conn-status').textContent = 'Conectado: ' + d.model;
      el('conn-status').className   = 'ok';
      setVal('llm-model',    d.model,    '');
      setVal('llm-provider', provider,   '');
      // Reconecta WS para garantir que está usando runtime conectado
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.close();
      }
      setTimeout(connectWS, 300);
    } else {
      const err = await r.json();
      el('conn-status').textContent = 'Erro: ' + (err.detail || 'falhou');
      el('conn-status').className   = 'err';
    }
  } catch(e) {
    el('conn-status').textContent = 'Erro: ' + e.message;
    el('conn-status').className   = 'err';
  }
}

let ws = null;
let wsReady = false;
let wsConnecting = false;

function connectWS() {
  // GUARD: ja conectado ou conectando - nao cria duplicata
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    console.log('[WS] ja existe (state=' + ws.readyState + ') - abortando reconnect');
    return;
  }
  if (wsConnecting) {
    console.log('[WS] connect ja em andamento - abortando');
    return;
  }
  wsConnecting = true;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url   = proto + '://' + location.host + '/ws/chat/' + SESSION;
  console.log('[WS] conectando:', url);
  ws = new WebSocket(url);

  ws.onopen = () => {
    console.log('[WS] aberto');
    wsReady = true;
    wsConnecting = false;
  };
  ws.onmessage = (ev) => {
    let d;
    try { d = JSON.parse(ev.data); }
    catch (e) { console.error('[WS] JSON invalido:', ev.data); return; }
    console.log('[WS] msg:', d.type);

    if (d.type === 'start') {
      addMsg('assistant', '');
      setStage('Processando pipeline...');
    } else if (d.type === 'pipeline_done') {
      const ok = d.pipeline_ok ? '[OK]' : '[!]';
      setStage(`${ok} Pipeline | ${d.compression_pct}% compressao | ${d.memory_hits} memorias`);
    } else if (d.type === 'llm_start') {
      setStage(`LLM ${d.model} gerando...`);
    } else if (d.type === 'chunk') {
      const last = el('chat-box').lastElementChild;
      if (last && last.classList.contains('assistant')) {
        last.querySelector('.bubble').textContent += d.text;
      } else {
        addMsg('assistant', d.text);
      }
      el('chat-box').scrollTop = el('chat-box').scrollHeight;
    } else if (d.type === 'done') {
      el('send-btn').disabled = false;
      setStage(d.llm_used ? '[OK] Resposta gerada' : '[OK] Resposta cognitiva (sem LLM)');
      if (d.metrics) {
        if (d.metrics.avg_latency_ms != null)  setVal('llm-lat',  Math.round(d.metrics.avg_latency_ms) + 'ms', '');
        if (d.metrics.total_requests != null)  setVal('llm-req',  d.metrics.total_requests, '');
        if (d.metrics.avg_memory_hits != null) setVal('llm-hits', d.metrics.avg_memory_hits.toFixed(1), '');
        if (d.metrics.total_errors != null)    setVal('llm-err',  d.metrics.total_errors, d.metrics.total_errors > 0 ? 'err' : 'ok');
      }
      setTimeout(() => setStage(''), 4000);
    } else if (d.type === 'warn') {
      const last = el('chat-box').lastElementChild;
      if (last && last.classList.contains('assistant')) {
        last.querySelector('.bubble').textContent += String.fromCharCode(10) + '[!] ' + d.error + String.fromCharCode(10);
      } else {
        addMsg('assistant', '[!] ' + d.error);
      }
    } else if (d.type === 'error') {
      addMsg('assistant', '[X] Erro: ' + d.error);
      el('send-btn').disabled = false;
      setStage('Erro');
    }
  };
  ws.onerror = (e) => {
    console.error('[WS] erro', e);
    wsReady = false;
    wsConnecting = false;
  };
  ws.onclose = () => {
    console.log('[WS] fechado');
    wsReady = false;
    wsConnecting = false;
    el('send-btn').disabled = false;
  };
}

function setStage(text) {
  let stage = el('stage-info');
  if (!stage) {
    stage = document.createElement('div');
    stage.id = 'stage-info';
    stage.style.cssText = 'font-size:11px;color:var(--muted);padding:6px 2px;min-height:18px;';
    el('chat-box').parentNode.insertBefore(stage, el('chat-box').nextSibling);
  }
  stage.textContent = text;
}

async function sendViaHTTP(text) {
  console.log('[HTTP] fallback');
  setStage('Enviando via HTTP (fallback)...');
  addMsg('assistant', '');
  try {
    const r = await fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text, session_id: SESSION}),
    });
    const d = await r.json();
    if (r.ok) {
      const last = el('chat-box').lastElementChild;
      if (last && last.classList.contains('assistant')) {
        last.querySelector('.bubble').textContent = d.text || '[sem resposta]';
      }
      setStage(`HTTP OK | ${d.memory_hits} memorias | ${d.latency_ms}ms`);
      if (d.latency_ms) setVal('llm-lat', Math.round(d.latency_ms) + 'ms', '');
    } else {
      addMsg('assistant', '[X] ' + (d.detail || 'erro HTTP ' + r.status));
      setStage('Erro HTTP');
    }
  } catch (e) {
    addMsg('assistant', '[X] Falha de rede: ' + e.message);
    setStage('Falha de rede');
  } finally {
    el('send-btn').disabled = false;
  }
}

function sendMsg() {
  const input = el('chat-input');
  const text  = input.value.trim();
  if (!text) return;

  addMsg('user', text);
  input.value = '';
  el('send-btn').disabled = true;
  setStage('Enviando...');

  // REUSA WS existente se aberto
  if (ws && ws.readyState === WebSocket.OPEN) {
    try {
      ws.send(JSON.stringify({message: text}));
      console.log('[WS] enviado');
    } catch (e) {
      console.error('[WS] send falhou', e);
      sendViaHTTP(text);
    }
    return;
  }

  // WS conectando? Espera 1s e tenta de novo
  if (ws && ws.readyState === WebSocket.CONNECTING) {
    setTimeout(() => {
      if (ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({message: text})); }
        catch (e) { sendViaHTTP(text); }
      } else {
        sendViaHTTP(text);
      }
    }, 1000);
    return;
  }

  // WS fechado/inexistente: cria UMA vez e tenta enviar
  connectWS();
  setTimeout(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      try { ws.send(JSON.stringify({message: text})); }
      catch (e) { sendViaHTTP(text); }
    } else {
      sendViaHTTP(text);
    }
  }, 1500);
}

// Conecta WS apenas UMA vez quando a pagina carrega
if (!window.__edpWsInit) {
  window.__edpWsInit = true;
  setTimeout(connectWS, 500);
}

function addMsg(role, text) {
  const box  = el('chat-box');
  const msg  = document.createElement('div');
  msg.className = 'msg ' + role;
  const bub  = document.createElement('div');
  bub.className = 'bubble';
  bub.textContent = text;
  msg.appendChild(bub);
  box.appendChild(msg);
  box.scrollTop = box.scrollHeight;
}

// (sendMsg foi reescrito acima com fallback HTTP)

// Refresh periodico
async function refresh() {
  await Promise.all([refreshHealth(), refreshMemory(), refreshMetrics(), refreshSnapshot()]);
}
refresh();
setInterval(refresh, 5000);

// Expoe funcoes no escopo global porque o HTML usa onclick="connectLLM()" etc.
window.connectLLM = connectLLM;
window.sendMsg    = sendMsg;
window.connectWS  = connectWS;

console.log('[dashboard] inicializado | session=' + SESSION);

})();  // fim do IIFE
</script>
</body>
</html>"""
