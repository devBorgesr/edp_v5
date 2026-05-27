"""
edp.api.routes.websocket — WebSocket de chat cognitivo unificado.

Fluxo SEMPRE (em ordem):
  1. start          → cliente sabe que turno começou
  2. pipeline_done  → compressão + retrieval executados
  3. llm_start      → modelo começou a gerar (se conectado)
  4. chunk* (N)     → tokens streaming
  5. done           → SEMPRE enviado (mesmo em erro)

Robustez (v3.4 — sprint estabilidade):
  - Heartbeat em task separada (asyncio.create_task)
    → inferência pesada NÃO mata mais o keepalive
  - Cancel token cooperativo
    → quando WS desconecta, próximo chunk para de yield
  - Inference queue com semáforo
    → 1 inferência por vez em 8GB de RAM (evita contenção CPU)
  - Memory pressure governor
    → recusa novas inferências se RAM < 1.2GB
  - Write-after-confirm
    → memória só persiste se stream completou (sem alucinações órfãs)
"""
from __future__ import annotations

import asyncio
import logging
import traceback
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...runtime import get_runtime, get_memory, is_valid, get_error
from ...runtime.pressure_governor import get_governor, PressureLevel
from ...runtime.inference_queue import get_queue, QueueFull, QueueTimeout
from ...clock import now as _now  # Peça 0.2b — relógio interno robusto

logger = logging.getLogger("edp.ws")

router = APIRouter(tags=["websocket"])


# ── Timeouts (ajustados para LLMs lentos em CPU) ──────────────────────────────
PIPELINE_TIMEOUT_S   = 60.0   # cold start de embedding leva ~40s
LLM_FIRST_CHUNK_S    = 90.0   # phi3 em CPU leva ~30-50s para 1º token
LLM_NEXT_CHUNK_S     = 60.0   # ollama em CPU pode pausar até 30s entre chunks
LLM_TOTAL_TIMEOUT_S  = 600.0  # 10 min para respostas longas em CPU
HEARTBEAT_INTERVAL_S = 20.0   # ping a cada 20s


async def _heartbeat_loop(websocket: WebSocket, session_id: str, stop_event: asyncio.Event):
    """
    Heartbeat em task independente.

    Mantém o WebSocket vivo mesmo durante inferências longas (que rodam em
    executor thread e não bloqueiam o event loop em si, MAS o cliente pode
    perceber timeout se nenhuma mensagem chega por muito tempo).

    Envia 'heartbeat' a cada HEARTBEAT_INTERVAL_S. Para quando stop_event setado.
    """
    seq = 0
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=HEARTBEAT_INTERVAL_S)
                # Se chegou aqui sem timeout, stop_event foi setado
                return
            except asyncio.TimeoutError:
                pass

            seq += 1
            try:
                await websocket.send_json({
                    "type": "heartbeat",
                    "seq":  seq,
                    "ts":   _now(),
                })
            except Exception:
                # WS já morto — para silenciosamente
                logger.debug("[WS-hb] socket fechado, parando heartbeat seq=%d", seq)
                return
    except asyncio.CancelledError:
        logger.debug("[WS-hb] cancelado session=%s", session_id)
        raise


@router.websocket("/ws/chat/{session_id}")
async def ws_chat(websocket: WebSocket, session_id: str):
    """Chat cognitivo unificado via WebSocket com streaming."""
    await websocket.accept()
    logger.info("[WS] conectado session=%s", session_id)

    runtime = get_runtime(session_id)
    memory  = get_memory(session_id)
    runtime_ok = is_valid(runtime)
    memory_ok  = is_valid(memory)

    # Avisa cliente sobre estado degradado
    if not runtime_ok:
        err = get_error(runtime) or "runtime None"
        logger.warning("[WS] runtime inválido: %s", err)
        await websocket.send_json({"type": "warn", "error": f"runtime degradado: {err}"})

    if not memory_ok:
        err = get_error(memory) or "memory None"
        logger.warning("[WS] memory inválido: %s", err)

    # ── Heartbeat em task separada (INVARIANTE 2) ────────────────────────────
    hb_stop = asyncio.Event()
    hb_task = asyncio.create_task(
        _heartbeat_loop(websocket, session_id, hb_stop)
    )
    logger.debug("[WS] heartbeat task iniciada session=%s", session_id)

    try:
        # Flag para sair do loop quando 'done' não pode ser enviado
        ws_dead = False
        while True:
            if ws_dead:
                break
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.warning("[WS] receive_json falhou: %s", e)
                break

            message = (data.get("message") or "").strip()
            logger.info("[WS] msg recebida session=%s len=%d", session_id, len(message))

            if not message:
                await websocket.send_json({"type": "error", "error": "mensagem vazia"})
                continue

            # ── Estado do turno ─────────────────────────────────────────────
            full_text       = ""
            llm_used        = False
            pipeline_ok     = False
            memory_hits     = 0
            compression_pct = 0.0
            retrieved_blocks: list = []
            metrics: dict = {}

            try:
                await websocket.send_json({
                    "type":       "start",
                    "session_id": session_id,
                    "stage":      "pipeline",
                })

                # ── Pipeline cognitivo (com timeout) ─────────────────────────
                try:
                    from ...pipeline import run_pipeline
                    loop = asyncio.get_event_loop()
                    pres = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            lambda: run_pipeline(message, message, session_id=session_id)
                        ),
                        timeout=PIPELINE_TIMEOUT_S,
                    )
                    compression_pct = pres.reduction_pct
                    pipeline_ok = True
                    logger.info("[WS] pipeline ok | reduction=%.1f%%", compression_pct)
                except asyncio.TimeoutError:
                    logger.warning("[WS] pipeline TIMEOUT (%.0fs) — pulando", PIPELINE_TIMEOUT_S)
                except Exception as e:
                    logger.warning("[WS] pipeline falhou: %s", e)

                # ── Retrieval da memória ────────────────────────────────────
                if memory_ok:
                    try:
                        import time as _t
                        from ...llm_adapter import _format_relative_time
                        _now = _t.time()
                        retrieved_blocks = []
                        seen_ids: set = set()

                        # ── Janela imediata: últimos 2 turnos ─────────────
                        # Hotfix v3.13.2: filtra session_summaries (espelha
                        # mudança em llm_adapter para manter UI consistente)
                        # Peça 1 (v3.13.9): ordena por timestamp antes de [-2:],
                        # porque memory.episodic.entries não está garantidamente
                        # em ordem cronológica (operações como reclassify_all e
                        # repair_episodic podem embaralhar).
                        try:
                            real_entries = sorted(
                                [
                                    e for e in memory.episodic.entries
                                    if e.get("source_type") != "session_summary"
                                ],
                                key=lambda e: e.get("timestamp", 0),
                            )
                            recent = real_entries[-2:]
                            labels_immediate = ["2 turnos atrás", "turno anterior"]
                            if len(recent) == 1:
                                labels_immediate = ["turno anterior"]
                            for entry, label in zip(recent, labels_immediate):
                                txt = (entry.get("text") or "")[:200]
                                if not txt:
                                    continue
                                eid = entry.get("id")
                                if eid:
                                    seen_ids.add(eid)
                                retrieved_blocks.append(f"[{label}] {txt}")
                        except Exception as e:
                            logger.debug("[WS] janela imediata UI falhou: %s", e)

                        # ── Retrieval por similaridade (dedupe) ───────────
                        retrieved = memory.retrieve(message, top_k=5, min_score=0.20)
                        for r in retrieved:
                            rid = r.get("id")
                            if rid and rid in seen_ids:
                                continue
                            txt = (r.get("text", "") or "")[:200]
                            if not txt:
                                continue
                            ts    = r.get("timestamp")
                            stype = r.get("source_type")
                            rel   = _format_relative_time(ts, _now) if ts else ""
                            tags  = []
                            if rel:
                                tags.append(rel)
                            if stype and stype not in ("unknown", "user_input"):
                                tags.append(stype)
                            prefix = f"[{', '.join(tags)}] " if tags else ""
                            retrieved_blocks.append(prefix + txt)
                        memory_hits = len(retrieved_blocks)
                        logger.info("[WS] memory | hits=%d (incl janela imediata)", memory_hits)
                    except Exception as e:
                        logger.warning("[WS] memory.retrieve falhou: %s", e)

                await websocket.send_json({
                    "type":            "pipeline_done",
                    "compression_pct": round(compression_pct, 1),
                    "memory_hits":     memory_hits,
                    "retrieved":       retrieved_blocks[:3],
                    "pipeline_ok":     pipeline_ok,
                })

                # ── LLM streaming (se conectado) ────────────────────────────
                if runtime_ok and runtime.is_connected():
                    # ── Pressure check: só bloqueia inferência LOCAL ────────
                    # Cloud (Anthropic, OpenAI) não consome RAM significativa
                    governor = get_governor()
                    pressure = governor.read()

                    # Detecta se modelo é cloud
                    try:
                        from ...runtime.context_window_manager import is_cloud_model
                        model_is_cloud = is_cloud_model(runtime._llm_config.model)
                    except Exception:
                        model_is_cloud = False

                    if pressure.level == PressureLevel.CRITICAL and not model_is_cloud:
                        logger.warning(
                            "[WS] LLM LOCAL recusado por pressão de RAM | available=%.2fGB",
                            pressure.available_gb,
                        )
                        await websocket.send_json({
                            "type":  "warn",
                            "error": (
                                f"Sistema sob pressão de memória "
                                f"(RAM livre {pressure.available_gb:.2f}GB < 1.2GB). "
                                f"Inferência local adiada. Tente conectar Anthropic/OpenAI."
                            ),
                        })
                    else:
                        # ── Roteador de modelo dinâmico (v3.13) ──────────────
                        # Escolhe Haiku/Sonnet/Opus baseado na complexidade.
                        # Modo híbrido: troca automática + notifica usuário.
                        routing_info = None
                        if model_is_cloud:
                            try:
                                from ...model_router import route_model, format_router_badge
                                # Pega modelo do turno anterior (continuidade)
                                prev_model = getattr(runtime, "_last_routed_model", None)
                                routing = route_model(
                                    user_message=message,
                                    previous_model=prev_model,
                                )
                                chosen_model = routing["model"]
                                # Troca o modelo no config (provider lê de config.model)
                                if chosen_model != runtime._llm_config.model:
                                    logger.info(
                                        "[WS] router: %s → %s (%s)",
                                        runtime._llm_config.model, chosen_model,
                                        routing["reason"],
                                    )
                                    runtime._llm_config.model = chosen_model
                                    # Propaga para o provider real
                                    # _anthropic_provider vive em runtime._client (LLMClient),
                                    # não no runtime direto. Bug do v3.13.1 corrigido agora
                                    # de verdade.
                                    if (
                                        hasattr(runtime, "_client")
                                        and runtime._client is not None
                                        and hasattr(runtime._client, "_anthropic_provider")
                                        and runtime._client._anthropic_provider is not None
                                    ):
                                        runtime._client._anthropic_provider.config.model = chosen_model
                                    # Também atualiza config interna do LLMClient
                                    if hasattr(runtime, "_client") and runtime._client is not None:
                                        if hasattr(runtime._client, "_cfg"):
                                            runtime._client._cfg.model = chosen_model
                                runtime._last_routed_model = chosen_model
                                routing_info = routing
                                # Notifica frontend antes do llm_start
                                await websocket.send_json({
                                    "type":     "router_decision",
                                    "model":    chosen_model,
                                    "tier":     routing["tier"],
                                    "reason":   routing["reason"],
                                    "cost":     routing["estimated_cost_per_turn"],
                                    "badge":    format_router_badge(routing),
                                })
                            except Exception as e:
                                logger.debug("[WS] router falhou: %s", e)

                        logger.info(
                            "[WS] LLM stream iniciando | pressure=%s ram=%.2fGB cloud=%s",
                            pressure.level.value, pressure.available_gb, model_is_cloud,
                        )
                        await websocket.send_json({
                            "type":  "llm_start",
                            "model": runtime._llm_config.model,
                        })

                        # ── Inference Queue: 1 inferência por vez em 8GB ────
                        queue = get_queue()
                        try:
                            async with queue.slot(session_id=session_id,
                                                  timeout_s=60.0) as cancel_token:
                                # cancel_token será verificado a cada chunk
                                try:
                                    chunks: list = []

                                    async def _stream_with_timeout():
                                        loop = asyncio.get_event_loop()
                                        gen  = runtime.stream_chat(message)
                                        first_chunk = True
                                        # ── Peça 2.4a.2: estado de detecção de auto-sinal ────
                                        # Verifica em frase terminada (.!?) para evitar regex
                                        # em fragmento. Só após 500 chars acumulados — dá espaço
                                        # para o modelo aplicar MÉTODO (CETICISMO_DEFAULT) antes
                                        # de admitir limite. Sem espaço, A admitiria cedo demais
                                        # e a câmara receberia trabalho não-embasado.
                                        # Atualizado em 2.4a.2b (de 100 → 500) — dá espaço pro método.
                                        autosinal_min_chars = 500
                                        autosinal_last_check_len = 0
                                        from edp.echo_chamber import detectar_auto_sinal_de_limite

                                        async def _next_chunk():
                                            return await loop.run_in_executor(
                                                None, lambda: next(gen, None)
                                            )

                                        while True:
                                            # ── Cancel cooperativo ───────────
                                            if cancel_token.is_cancelled:
                                                logger.info(
                                                    "[WS] LLM cancelado mid-stream session=%s reason=%s",
                                                    session_id, cancel_token.cancel_reason,
                                                )
                                                break

                                            chunk_timeout = (
                                                LLM_FIRST_CHUNK_S if first_chunk
                                                else LLM_NEXT_CHUNK_S
                                            )
                                            try:
                                                chunk = await asyncio.wait_for(
                                                    _next_chunk(), timeout=chunk_timeout
                                                )
                                            except asyncio.TimeoutError:
                                                kind = "primeiro" if first_chunk else "seguinte"
                                                logger.warning(
                                                    "[WS] LLM %s chunk timeout (%.0fs)",
                                                    kind, chunk_timeout
                                                )
                                                break
                                            if chunk is None:
                                                break
                                            if first_chunk:
                                                logger.info("[WS] LLM primeiro chunk recebido")
                                                first_chunk = False
                                            chunks.append(chunk)
                                            try:
                                                await websocket.send_json({
                                                    "type": "chunk", "text": chunk
                                                })
                                            except Exception as e:
                                                # WS morreu mid-stream — cancela
                                                logger.warning(
                                                    "[WS] send falhou mid-stream, cancelando: %s", e
                                                )
                                                cancel_token.cancel("ws_send_failed")
                                                break

                                            # ── Peça 2.4a.2: detecção de auto-sinal mid-stream ──
                                            # Só roda se: (1) chunk termina frase (. ! ?),
                                            # (2) acumulado >= 100 chars, (3) cresceu desde
                                            # última verificação. Detecção em frase COMPLETA
                                            # evita regex em fragmento.
                                            if chunk and chunk.rstrip().endswith((".", "!", "?")):
                                                texto_acumulado = "".join(chunks)
                                                if (len(texto_acumulado) >= autosinal_min_chars
                                                        and len(texto_acumulado) > autosinal_last_check_len):
                                                    autosinal_last_check_len = len(texto_acumulado)
                                                    try:
                                                        auto_sinal = detectar_auto_sinal_de_limite(
                                                            texto_acumulado
                                                        )
                                                    except Exception as e:
                                                        logger.debug(
                                                            "[WS] auto-sinal check falhou: %s", e
                                                        )
                                                        auto_sinal = {"detectado": False}
                                                    if (auto_sinal.get("detectado")
                                                            and auto_sinal.get("confianca") == "alta"):
                                                        # Frase-padrão completa detectada
                                                        # → cancela stream para 2.4a.3 ativar câmara
                                                        logger.info(
                                                            "[WS] auto-sinal mid-stream | confianca=alta "
                                                            "trecho='%s' chars_acumulados=%d",
                                                            auto_sinal["trecho"][:80],
                                                            len(texto_acumulado),
                                                        )
                                                        cancel_token.cancel(
                                                            "camara_ativada_por_auto_sinal"
                                                        )
                                                        break

                                    await asyncio.wait_for(
                                        _stream_with_timeout(),
                                        timeout=LLM_TOTAL_TIMEOUT_S,
                                    )
                                    full_text = "".join(chunks)
                                    # ── Write-after-confirm: só conta como "usado"
                                    #    se completou sem cancelamento
                                    if cancel_token.is_cancelled:
                                        llm_used = False
                                        logger.info(
                                            "[WS] LLM aborted (cancelled) | tokens parciais=%d descartados",
                                            len(full_text.split()),
                                        )
                                    else:
                                        llm_used = bool(full_text)
                                        logger.info("[WS] LLM done | tokens~%d", len(full_text.split()))
                                except asyncio.TimeoutError:
                                    logger.warning("[WS] LLM TIMEOUT TOTAL (%.0fs)", LLM_TOTAL_TIMEOUT_S)
                                    try:
                                        await websocket.send_json({
                                            "type":  "warn",
                                            "error": f"LLM timeout ({LLM_TOTAL_TIMEOUT_S:.0f}s)",
                                        })
                                    except Exception:
                                        pass
                                except Exception as e:
                                    logger.warning("[WS] LLM falhou: %s: %s", type(e).__name__, e)
                                    try:
                                        await websocket.send_json({
                                            "type":  "warn",
                                            "error": f"LLM indisponivel: {e}",
                                        })
                                    except Exception:
                                        pass
                        except QueueFull as e:
                            logger.warning("[WS] queue cheia session=%s: %s", session_id, e)
                            await websocket.send_json({
                                "type":  "warn",
                                "error": "Sistema ocupado (fila cheia). Aguarde.",
                            })
                        except QueueTimeout as e:
                            logger.warning("[WS] queue timeout session=%s: %s", session_id, e)
                            await websocket.send_json({
                                "type":  "warn",
                                "error": "Timeout esperando slot de inferência.",
                            })

                # ── Fallback cognitivo ───────────────────────────────────────
                if not llm_used:
                    parts = ["[EDP - modo analise cognitiva]\n\n"]
                    if not runtime_ok:
                        parts.append("Runtime indisponivel - apenas analise estrutural.\n\n")
                    elif not runtime.is_connected():
                        parts.append("Nenhum LLM conectado.\n\n")

                    if pipeline_ok:
                        parts.append(f"Pipeline: {compression_pct:.0f}% compressao.\n")
                    if retrieved_blocks:
                        parts.append(f"\nMemoria recuperada ({memory_hits} entradas):\n")
                        for i, blk in enumerate(retrieved_blocks[:3], 1):
                            parts.append(f"  [{i}] {blk}\n")
                    else:
                        parts.append("Nenhuma memoria anterior relevante.\n")

                    parts.append("\nConecte um LLM via formulario acima.")
                    full_text = "".join(parts)

                    for line in full_text.split("\n"):
                        await websocket.send_json({
                            "type": "chunk", "text": line + "\n"
                        })

                # ── Armazena na memória (write-after-confirm) ────────────────
                # Só persiste se:
                #   1. memory_ok (sistema funcionando)
                #   2. full_text não vazio
                #   3. llm_used (stream completou sem cancelamento)
                # Inferências canceladas, órfãs, ou parciais NÃO entram na memória.
                if memory_ok and full_text and llm_used:
                    try:
                        # Sanity cap generoso: 4000 chars input + 12000 output = ~16000 max
                        # Antes era 200/400 que cortava respostas técnicas no meio (bug).
                        # Para memória técnica densa (papers, código, explicações longas),
                        # 12000 chars cobre ~3000-4000 tokens — suficiente sem inflar disco.
                        msg_capped  = message[:4000]
                        resp_capped = full_text[:12000]
                        combined = f"Q: {msg_capped}\nA: {resp_capped}"
                        # Provenance: vem de LLM, é hipótese (não verificada)
                        # Confidence base 0.65 = score histórico do EDP
                        source = "user"
                        if runtime_ok and runtime._llm_config:
                            source = f"llm:{runtime._llm_config.model}"
                        memory.add(
                            combined,
                            score=0.65,
                            prioridade="media",
                            source=source,
                            confidence=0.65,
                            epistemic_status="hypothesis",
                        )
                        if hasattr(memory.episodic, "flush"):
                            memory.episodic.flush()
                    except TypeError:
                        # Fallback se memory.add não aceita kwargs novos (compat)
                        try:
                            memory.add(combined, score=0.65, prioridade="media")
                            if hasattr(memory.episodic, "flush"):
                                memory.episodic.flush()
                        except Exception as e:
                            logger.debug("[WS] memory.add fallback falhou: %s", e)
                    except Exception as e:
                        logger.debug("[WS] memory.add falhou: %s", e)

                if runtime_ok:
                    try:
                        metrics = runtime.llm_metrics()
                    except Exception:
                        pass

            except Exception as e:
                traceback.print_exc()
                logger.error("[WS] erro no turno: %s", e)
                try:
                    await websocket.send_json({
                        "type":  "error",
                        "error": f"{type(e).__name__}: {e}",
                    })
                except Exception:
                    pass

            finally:
                # GARANTE 'done' SEMPRE
                try:
                    await websocket.send_json({
                        "type":            "done",
                        "text":            full_text,
                        "llm_used":        llm_used,
                        "memory_hits":     memory_hits,
                        "compression_pct": round(compression_pct, 1),
                        "metrics":         metrics,
                    })
                    logger.info("[WS] done session=%s llm_used=%s", session_id, llm_used)
                except Exception as e:
                    logger.warning("[WS] envio de 'done' falhou: %s", e)
                    ws_dead = True   # sai do loop no próximo iter (sem break in finally)

    except WebSocketDisconnect:
        logger.info("[WS] desconectado session=%s", session_id)
        # ── Trigger session summary em background (não bloqueia disconnect) ──
        try:
            import threading
            from ...session_summary import generate_session_summary

            def _summarize_async():
                try:
                    mem = get_memory(session_id)
                    rt  = get_runtime(session_id)
                    if not is_valid(mem) or not is_valid(rt):
                        return
                    result = generate_session_summary(mem, rt, session_id=session_id)
                    if result:
                        logger.info(
                            "[WS] session_summary OK session=%s tag=%s reused=%s",
                            session_id, result.get("label"), result.get("reused"),
                        )
                except Exception as e:
                    logger.debug("[WS] summary background falhou: %s", e)

            threading.Thread(target=_summarize_async, daemon=True).start()
        except Exception as e:
            logger.debug("[WS] schedule summary falhou: %s", e)
    except Exception as e:
        traceback.print_exc()
        logger.error("[WS] erro fatal: %s", e)
    finally:
        # ── Para heartbeat task sempre ──────────────────────────────────────
        hb_stop.set()
        try:
            await asyncio.wait_for(hb_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            hb_task.cancel()
        except Exception as e:
            logger.debug("[WS] heartbeat cleanup: %s", e)
        logger.debug("[WS] handler finalizado session=%s", session_id)
