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
from starlette.websockets import WebSocketState


def _ws_alive(ws) -> bool:
    """Dívida #45 (10/06/2026): conexão ainda viva nos dois sentidos?"""
    try:
        return (
            ws.client_state == WebSocketState.CONNECTED
            and ws.application_state == WebSocketState.CONNECTED
        )
    except Exception:
        return False


async def _safe_send(ws, payload: dict) -> bool:
    """
    Dívida #45 (10/06/2026): send que NUNCA explode o turno.

    Bug original: cliente recarregava a página no meio do turno → conexão
    fechava → próximo send levantava RuntimeError("Cannot call send once a
    close message has been sent") → turno zumbi morria com traceback cru e
    a pergunta do usuário era perdida ANTES do LLM responder.

    Com _safe_send: conexão morta → False silencioso. O turno segue,
    a resposta (se gerada) é salva na memória, zero traceback.
    """
    if not _ws_alive(ws):
        return False
    try:
        await ws.send_json(payload)
        return True
    except Exception as e:
        logger.debug("[WS] send descartado (conexão morta): %s", e)
        return False


from ...runtime import get_runtime, get_memory, is_valid, get_error
from ...runtime.pressure_governor import get_governor, PressureLevel
from ...runtime.inference_queue import get_queue, QueueFull, QueueTimeout
from ...clock import now as _now  # Peça 0.2b — relógio interno robusto

logger = logging.getLogger("edp.ws")


# ── Helpers da peça 2.6e (Bug B fix — 2026-05-30) ───────────────────────────
# Detecção robusta de mensagens de "continuação" em modo sectioned.
# Necessário porque match exato falhou em produção: "contenue" (typo) não
# foi reconhecido, destruindo tarefa de 10 seções.

_CONTINUATION_BASE = {
    # palavra-alvo: distância máxima de edição tolerada
    "continue":    2,   # contenue, continui, contunue
    "continuar":   2,   # continuir, continaur
    "continua":    2,
    "proxima":     2,   # próxima (sem acento já vem normalizado)
    "próxima":     2,
    "prossegue":   2,
    "prossegui":   2,   # variante imperativa
    "prosseguir":  2,
    "seguir":      1,
    "siga":        1,
    "vai":         0,   # palavras de 3 letras: zero tolerância
    "vamos":       1,
    "ok":          0,
    "next":        1,
    "go":          0,
    "avance":      1,
    "avancar":     1,
    "avançar":     1,
    "dale":        0,   # gíria comum BR
    "manda":       1,
    "bora":        0,
}

# Regex de padrões que cobrem variações gramaticais
import re as _re_continuation
_CONTINUATION_REGEX = _re_continuation.compile(
    r"^("
    r"continu[ae]?r?|cont[ie]nu[ae]?|"
    r"pross?eg[uia]+r?|pross?ig[ae]?|"
    r"pr[oóø]xim[ao]|"
    r"si[gj]a|seguir?|"
    r"vai|vamos|"
    r"ok|next|go|"
    r"avan[çc]a[rs]?|"
    r"dale|manda|bora"
    r")$",
    _re_continuation.IGNORECASE,
)


def _levenshtein(a: str, b: str) -> int:
    """Distância de edição entre duas strings (algoritmo clássico DP).
    Pequeno o suficiente para palavras de até ~20 chars."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev_row = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr_row = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr_row.append(min(
                curr_row[j - 1] + 1,        # insertion
                prev_row[j] + 1,            # deletion
                prev_row[j - 1] + cost,     # substitution
            ))
        prev_row = curr_row
    return prev_row[-1]


# Commit 2 dos Dois Exocórtices (2026-05-31): Camada 4 — referência
# contextual a seção/parte/etapa. Caso real validado em produção (P3):
# "preciso da Seção 3/3. voce nao gerou." (6 palavras) destruía âncora
# porque excedia limite de 5 palavras das camadas 1-3. Solução: quando
# há tarefa ativa, mensagens até 20 palavras com referência explícita a
# estrutura de seções são tratadas como continuação contextual.
_SECTION_REFERENCE_REGEX = _re_continuation.compile(
    r"(?:"
    r"se[çc][ãa]o\s*\d+|"                 # "seção 3", "secao 3"
    r"\d+\s*/\s*\d+|"                     # "3/3", "2 / 5"
    r"parte\s*\d+|"                       # "parte 2"
    r"etapa\s*\d+|"                       # "etapa 3"
    r"cap[íi]tulo\s*\d+|"                 # "capítulo 4"
    r"passo\s*\d+|"                       # "passo 1"
    r"pr[oóø]xim[ao]\s+(?:se[çc][ãa]o|parte|etapa|cap[íi]tulo|passo)|"
    r"(?:falt[ao]u?|gera[rs]?|mostr[ae]r?|entreg[aue]+|fa[çc]a)\s+"
    r"(?:a\s+|essa\s+|esta\s+|aquela\s+)?(?:se[çc][ãa]o|parte|etapa|cap[íi]tulo|passo)|"
    r"n[aã]o\s+(?:gerou|entregou|fez|mostrou)|"
    r"(?:continu[ae]?|prossegu[ie])\s+(?:a\s+|com\s+(?:a\s+)?|de\s+(?:a\s+)?)?(?:se[çc][ãa]o|parte|etapa|tarefa)|"
    r"da\s+se[çc][ãa]o\s+\d"              # "da seção 3"
    r")",
    _re_continuation.IGNORECASE,
)


def _detect_continuation(msg_normalized: str, task_active: bool = False) -> bool:
    """Detecta mensagens de continuação em sectioned com 4 camadas:
    1. Match exato no conjunto base (rápido, palavras curtas)
    2. Regex de padrões comuns (variações gramaticais)
    3. Levenshtein ≤ tolerância por palavra-alvo (typos curtos)
    4. Referência contextual a seção/parte (Commit 2 — só se task_active)

    Limites:
      - Mensagens > 5 palavras: precisam camada 4 + task_active para passar
      - Mensagens > 20 palavras: nunca passam pela camada 4 (provavel desafio)
      - Mensagens > 60 palavras: nunca tratadas como continuação
        (guarda absoluta — texto longo = nova tarefa)
      - "?" bloqueia camadas 1-3, mas NÃO bloqueia camada 4 quando
        task_active=True (perguntas contextuais sobre tarefa em curso)

    Args:
        msg_normalized: mensagem normalizada (lowercase, strip)
        task_active: True se há tarefa em curso (anchor não vazio)

    Returns:
        True se deve ser tratada como continuação
    """
    if not msg_normalized:
        return False
    words = msg_normalized.split()
    n_words = len(words)

    # Guarda absoluta: textos muito longos NUNCA são continuação
    # (provavel novo desafio, mesmo com palavras-chave dentro)
    if n_words > 60:
        return False

    # Camadas 1-3 (comportamento legado): mensagens curtas sem "?"
    if n_words <= 5 and "?" not in msg_normalized:
        # Camada 1: match exato
        if msg_normalized in _CONTINUATION_BASE:
            return True
        # Camada 2: regex
        if _CONTINUATION_REGEX.match(msg_normalized):
            return True
        # Camada 3: Levenshtein (typos)
        if n_words <= 2:
            for target, max_dist in _CONTINUATION_BASE.items():
                if max_dist == 0:
                    continue
                if abs(len(words[0]) - len(target)) <= max_dist:
                    if _levenshtein(words[0], target) <= max_dist:
                        return True

    # Camada 4 (Commit 2): referência contextual.
    # Ativa apenas se há tarefa em curso E mensagem cabe em 20 palavras.
    # Sobrescreve regra do "?" — perguntas contextuais sobre tarefa ativa
    # ("você pode gerar a seção 3?") são tratadas como continuação.
    if task_active and n_words <= 20:
        if _SECTION_REFERENCE_REGEX.search(msg_normalized):
            return True

    return False


# ── fim dos helpers da peça 2.6e + Commit 2 ─────────────────────────────────

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
                await _safe_send(websocket, {
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
        await _safe_send(websocket, {"type": "warn", "error": f"runtime degradado: {err}"})

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
            # Peça 2.6a (2026-05-30): log do modo a cada turno (auditoria visual)
            _current_mode = "cognitive"
            _sectioned = False
            try:
                if hasattr(runtime, "get_operational_mode"):
                    _current_mode = runtime.get_operational_mode()
                if hasattr(runtime, "is_sectioned_active"):
                    _sectioned = runtime.is_sectioned_active()
            except Exception:
                pass
            logger.info(
                "[WS] msg recebida session=%s len=%d mode=%s sectioned=%s",
                session_id, len(message), _current_mode,
                "on" if _sectioned else "off",
            )

            if not message:
                await _safe_send(websocket, {"type": "error", "error": "mensagem vazia"})
                continue

            # ── Interceptação de comando /mode (peça 2.6a) ─────────────────
            # Sintaxe aceita:
            #   /mode sprint       → ativa modo sprint
            #   /mode cognitive    → ativa modo cognitive
            #   /mode status       → mostra modo atual
            #   /mode              → idem status
            # Comandos NÃO são enviados ao LLM. Custo: zero tokens.
            #
            # 2.6a.fix (2026-05-30): aceitar /mode case-insensitive E enviar
            # respostas no formato que o frontend já trata (start/chunk/done)
            # em vez de tipos novos (mode_status/mode_change). Diagnóstico:
            # frontend ignorava tipos desconhecidos, deixando chat em
            # "Enviando..." infinito. Solução: reusar canal normal de chat.
            if message.lower().startswith("/mode"):
                parts = message.lower().split()
                target = parts[1] if len(parts) >= 2 else "status"

                if not hasattr(runtime, "set_operational_mode"):
                    await _safe_send(websocket, {
                        "type":  "error",
                        "error": "comando /mode indisponível (runtime sem suporte)",
                    })
                    continue

                # Sinaliza start para o frontend sair do estado "Enviando..."
                await _safe_send(websocket, {
                    "type":       "start",
                    "session_id": session_id,
                    "stage":      "command",
                })

                if target == "status":
                    current = runtime.get_operational_mode()
                    response_text = f"[modo atual: {current}]"
                else:
                    result = runtime.set_operational_mode(target)
                    if result.get("ok"):
                        response_text = f"[{result.get('message', '')}]"
                    else:
                        response_text = f"[erro: {result.get('message', 'modo inválido')}]"

                # Envia como chunk normal (frontend exibe no chat)
                await _safe_send(websocket, {
                    "type": "chunk",
                    "text": response_text,
                })
                # Encerra ciclo (frontend libera input)
                await _safe_send(websocket, {
                    "type":     "done",
                    "session_id": session_id,
                    "llm_used": False,
                })
                logger.info("[mode] comando processado: target=%s", target)
                continue
            # ── fim da interceptação /mode ─────────────────────────────────

            # ── Interceptação de /sectioned (peça 2.6b) ─────────────────────
            # Sintaxe:
            #   /sectioned        → ativa (exige sprint mode)
            #   /sectioned on     → idem
            #   /sectioned off    → desativa
            #   /sectioned status → mostra estado
            if message.lower().startswith("/sectioned"):
                if not hasattr(runtime, "set_sectioned"):
                    await _safe_send(websocket, {
                        "type":  "error",
                        "error": "comando /sectioned indisponível (runtime sem suporte)",
                    })
                    continue

                parts = message.lower().split()
                target = parts[1] if len(parts) >= 2 else "on"

                await _safe_send(websocket, {
                    "type":       "start",
                    "session_id": session_id,
                    "stage":      "command",
                })

                if target == "status":
                    active = runtime.is_sectioned_active()
                    response_text = (
                        f"[sectioned: {'ativo' if active else 'inativo'}]"
                    )
                elif target in ("off", "false", "0", "no"):
                    result = runtime.set_sectioned(False)
                    response_text = f"[{result.get('message', '')}]"
                else:  # on, true, 1, yes, ou vazio
                    result = runtime.set_sectioned(True)
                    if result.get("ok"):
                        response_text = f"[{result.get('message', '')}]"
                    else:
                        response_text = f"[erro: {result.get('message', '')}]"

                await _safe_send(websocket, {"type": "chunk", "text": response_text})
                await _safe_send(websocket, {
                    "type":      "done",
                    "session_id": session_id,
                    "llm_used":  False,
                })
                logger.info("[sectioned] comando processado: target=%s", target)
                continue
            # ── fim da interceptação /sectioned ─────────────────────────────

            # ── Interceptação /task (peça 2.6c) ─────────────────────────────
            # Sintaxe:
            #   /task            → status (idem /task status)
            #   /task status     → mostra estado da âncora atual
            #   /task clear      → limpa âncora (encerra tarefa em curso)
            if message.lower().startswith("/task"):
                if not hasattr(runtime, "get_task_anchor"):
                    await _safe_send(websocket, {
                        "type":  "error",
                        "error": "comando /task indisponível",
                    })
                    continue

                parts = message.lower().split()
                target = parts[1] if len(parts) >= 2 else "status"

                await _safe_send(websocket, {
                    "type":       "start",
                    "session_id": session_id,
                    "stage":      "command",
                })

                if target == "clear":
                    result = runtime.clear_task()
                    response_text = (
                        "[tarefa limpa]" if result.get("cleared")
                        else "[nenhuma tarefa ativa]"
                    )
                else:  # status (default)
                    task = runtime.get_task_anchor()
                    if task is None:
                        response_text = "[nenhuma tarefa em curso]"
                    else:
                        delivered = task.get("sections_delivered", [])
                        total = task.get("expected_total") or "?"
                        lines = [
                            f"[tarefa em curso: {len(delivered)}/{total} seções]",
                            f"Desafio: {task['challenge'][:200]}...",
                        ]
                        if delivered:
                            lines.append("Entregues:")
                            for s in delivered:
                                lines.append(f"  • {s['n']}/{s['total']} — {s['title']}")
                        response_text = "\n".join(lines)

                await _safe_send(websocket, {"type": "chunk", "text": response_text})
                await _safe_send(websocket, {
                    "type":      "done",
                    "session_id": session_id,
                    "llm_used":  False,
                })
                logger.info("[task] comando processado: target=%s", target)
                continue
            # ── fim da interceptação /task ──────────────────────────────────

            # ── Atalho /next (peça 2.6b) ────────────────────────────────────
            # Comando /next vira "continue" antes de seguir para o LLM.
            # Funciona em qualquer modo, mas só faz sentido com sectioned
            # ativo. Se sectioned inativo, vira mensagem normal "continue".
            # Variantes ativadas: /next, /n, /siga
            _msg_normalized = message.lower().strip()
            if _msg_normalized in ("/next", "/n", "/siga", "/proxima", "/próxima"):
                if hasattr(runtime, "is_sectioned_active") and runtime.is_sectioned_active():
                    message = "continue"
                    logger.info("[sectioned] /next → 'continue' (sectioned ativo)")
                else:
                    # Avisa que /next só faz sentido com sectioned
                    await _safe_send(websocket, {
                        "type":       "start",
                        "session_id": session_id,
                        "stage":      "command",
                    })
                    await _safe_send(websocket, {
                        "type": "chunk",
                        "text": ("[/next só funciona com modo sectioned ativo. "
                                 "Use '/mode sprint' + '/sectioned' primeiro.]"),
                    })
                    await _safe_send(websocket, {
                        "type":      "done",
                        "session_id": session_id,
                        "llm_used":  False,
                    })
                    logger.info("[sectioned] /next ignorado (sectioned inativo)")
                    continue
            # ── fim do atalho /next ─────────────────────────────────────────

            # ── Peça 2.6c: detectar início de tarefa multi-seção ──────────
            # Quando sectioned está ativo e a mensagem do usuário NÃO é
            # comando de continuação, considera que é uma nova tarefa.
            # Inicia rastreamento via _task_anchor. Se já havia tarefa,
            # start_task substitui (usuário pode estar mudando de desafio).
            #
            # Detecção: é "continue" se for /next-variante OU palavras
            # típicas. Senão, é início de nova tarefa.
            #
            # 2.6e Bug B fix (2026-05-30): detecção robusta a typos.
            # Commit 2 (2026-05-31): adiciona Camada 4 — referência contextual.
            # Caso real P3: "preciso da Seção 3/3. voce nao gerou." (6 palavras)
            # destruía âncora porque excedia limite das camadas 1-3.
            #
            # Estratégia: detecta task_active ANTES de chamar _detect_continuation
            # e passa como sinal. A função decide camada apropriada baseada nisso.
            _task_active_now = False
            if _sectioned and hasattr(runtime, "get_task_anchor"):
                try:
                    _task_active_now = runtime.get_task_anchor() is not None
                except Exception:
                    pass
            _is_continuation = _detect_continuation(
                _msg_normalized, task_active=_task_active_now
            )
            # Log estruturado para auditoria da decisão (instrumentação obrigatória
            # do plano de 5 commits — cada decisão de cada princípio loga).
            if _sectioned:
                _n_words = len(_msg_normalized.split())
                _layer_hit = "none"
                if _is_continuation:
                    if _n_words <= 5 and "?" not in _msg_normalized:
                        _layer_hit = "1-3 (exata/regex/typo)"
                    elif _task_active_now and _n_words <= 20:
                        if _SECTION_REFERENCE_REGEX.search(_msg_normalized):
                            _layer_hit = "4 (ref contextual)"
                logger.info(
                    "[continuation] detect=%s | layer=%s | words=%d | task_active=%s | has_q=%s",
                    _is_continuation, _layer_hit, _n_words,
                    _task_active_now, "?" in _msg_normalized,
                )

            # Heurística de proteção de estado herdada de 2.6e: se camada 4 não
            # bateu mas anchor ativa + msg curta sem "?", ainda preserva por
            # default (princípio "na dúvida, preserva estado").
            if _sectioned and not _is_continuation and _task_active_now:
                try:
                    _is_short = len(_msg_normalized.split()) < 5
                    _no_question = "?" not in _msg_normalized
                    if _is_short and _no_question:
                        _is_continuation = True
                        logger.info(
                            "[task] heurística fallback: preservando tarefa "
                            "(msg curta sem '?', task ativa)"
                        )
                except Exception:
                    pass
            if (
                _sectioned and not _is_continuation
                and hasattr(runtime, "start_task")
            ):
                try:
                    runtime.start_task(message)
                    logger.info(
                        "[task] iniciando nova tarefa (continuation=False) | "
                        "challenge_len=%d", len(message),
                    )
                except Exception as e:
                    logger.debug("[task] start_task falhou: %s", e)
            # ── fim da detecção de início de tarefa ───────────────────────

            # ── Estado do turno ─────────────────────────────────────────────
            full_text       = ""
            llm_used        = False
            pipeline_ok     = False
            memory_hits     = 0
            compression_pct = 0.0
            retrieved_blocks: list = []
            metrics: dict = {}
            # Commit B (10/06/2026): estado do lineage por turno
            lineage_retrieved: list = []
            lineage_quality:   dict = {}
            # Feature/odysseus-ui: fontes enriquecidas + conflitos
            sources_ui:    list = []
            conflict_count: int = 0

            try:
                await _safe_send(websocket, {
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

                    # ── Commit A (Sessão 2, 08/06/2026): Quality Score ──────────
                    # Envia score composto + verdict ao cliente quando
                    # aggregate_score disponível e feature flag ativa.
                    # NÃO derruba turno se falhar — falha silenciosa OK aqui.
                    try:
                        from ...runtime.quality_score import (
                            get_quality_exposer, is_quality_score_enabled,
                        )
                        if is_quality_score_enabled() and pres.aggregate_score is not None:
                            qresult = get_quality_exposer().from_score_vector(
                                pres.aggregate_score
                            )
                            if qresult is not None:
                                # Commit B: guarda p/ lineage no fim do turno
                                lineage_quality = {
                                    "score":   qresult.score,
                                    "verdict": qresult.verdict,
                                }
                                await _safe_send(websocket, {
                                    "type":       "quality",
                                    "score":      qresult.score,
                                    "verdict":    qresult.verdict,
                                    "components": qresult.components,
                                    "source":     qresult.source,
                                })
                                logger.info(
                                    "[WS] quality enviado | score=%.3f verdict=%s",
                                    qresult.score, qresult.verdict,
                                )
                    except Exception as qerr:
                        logger.warning(
                            "[WS] quality_score falhou (não-fatal): %s: %s",
                            type(qerr).__name__, str(qerr)[:120],
                        )
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
                        lineage_retrieved = retrieved  # Commit B: captura p/ lineage

                        # ── Feature/odysseus-ui: fontes enriquecidas ─────────────
                        for r in retrieved[:5]:
                            rel = _format_relative_time(r.get("timestamp"), _now) \
                                  if r.get("timestamp") else ""
                            sources_ui.append({
                                "text":        (r.get("text") or "")[:120],
                                "rel_time":    rel,
                                "source_type": r.get("source_type") or "unknown",
                                "epistemic":   r.get("epistemic_status") or "hypothesis",
                                "score":       round(float(r.get("ranking_score", 0.0)), 3),
                            })
                        # ── Feature/odysseus-ui: detecção de conflito ────────────
                        try:
                            from ...runtime.contradiction_flagger import get_flagger
                            conflict_count = get_flagger().scan_results(retrieved)
                        except Exception as _ce:
                            logger.debug("[WS] contradiction scan falhou: %s", _ce)

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

                await _safe_send(websocket, {
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
                        await _safe_send(websocket, {
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
                                # Peça 2.6d M2: contexto de tarefa em curso
                                _task_ctx = {
                                    "sectioned_active": False,
                                    "task_anchor_active": False,
                                }
                                try:
                                    if hasattr(runtime, "is_sectioned_active"):
                                        _task_ctx["sectioned_active"] = runtime.is_sectioned_active()
                                    if hasattr(runtime, "get_task_anchor"):
                                        _task_ctx["task_anchor_active"] = runtime.get_task_anchor() is not None
                                except Exception:
                                    pass
                                routing = route_model(
                                    user_message=message,
                                    previous_model=prev_model,
                                    task_context=_task_ctx,
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
                                await _safe_send(websocket, {
                                    "type":     "router_decision",
                                    "model":    chosen_model,
                                    "tier":     routing["tier"],
                                    "reason":   routing["reason"],
                                    "cost":     routing["estimated_cost_per_turn"],
                                    "badge":    format_router_badge(routing),
                                })
                            except Exception as e:
                                logger.debug("[WS] router falhou: %s", e)

                        # ── Dívida #45 (10/06/2026): gate pré-LLM ──────────
                        # Cliente recarregou/caiu durante o pipeline?
                        # Abortar AQUI economiza a chamada de API inteira
                        # e mata o turno zumbi na origem.
                        if not _ws_alive(websocket):
                            logger.info(
                                "[WS] conexão morta — turno abortado antes do LLM"
                            )
                            raise WebSocketDisconnect(code=1006)

                        logger.info(
                            "[WS] LLM stream iniciando | pressure=%s ram=%.2fGB cloud=%s",
                            pressure.level.value, pressure.available_gb, model_is_cloud,
                        )
                        await _safe_send(websocket, {
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
                                    # ── Peça 2.4a.7: rastreamento de proveniência ──
                                    # Marca a linhagem do texto final para gravação.
                                    # None = resposta normal de A (source padrão llm:<model>).
                                    # "camara:<B>" = câmara venceu (B refinou, dano factual).
                                    # "llm:fallback_solo" = veto de topo / sem B / câmara falhou.
                                    camara_source = None

                                    async def _stream_with_timeout():
                                        loop = asyncio.get_event_loop()
                                        gen  = runtime.stream_chat(message)
                                        first_chunk = True

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
                                                await _safe_send(websocket, {
                                                    "type": "chunk", "text": chunk
                                                })
                                            except Exception as e:
                                                # WS morreu mid-stream — cancela
                                                logger.warning(
                                                    "[WS] send falhou mid-stream, cancelando: %s", e
                                                )
                                                cancel_token.cancel("ws_send_failed")
                                                break

                                    await asyncio.wait_for(
                                        _stream_with_timeout(),
                                        timeout=LLM_TOTAL_TIMEOUT_S,
                                    )
                                    full_text = "".join(chunks)
                                    # ── Write-after-confirm: só conta como "usado"
                                    #    se completou sem cancelamento
                                    if cancel_token.is_cancelled:
                                        # Cancel genuíno (timeout, ws_send_failed, etc.) — NÃO é câmara.
                                        # Ajuste 1 (2.4a.6): a câmara não é mais ativada por cancel
                                        # mid-stream. O stream completa inteiro e a detecção roda no
                                        # texto final consolidado (on_llm_end) — ver abaixo.
                                        llm_used = False
                                        logger.info(
                                            "[WS] LLM aborted (cancelled) | reason=%s tokens=%d",
                                            cancel_token.cancel_reason,
                                            len(full_text.split()),
                                        )
                                    else:
                                        # ── Ajuste 1 (2.4a.6): detecção de auto-sinal no texto FINAL ──
                                        # A terminou de gerar. Varremos o texto completo consolidado
                                        # uma única vez. Se A admitiu limite com frase-padrão de alta
                                        # confiança, ativamos a câmara — B recebe a PREMISSA INTEIRA,
                                        # não um fragmento cortado. Sem desperdício de tokens, sem ruído
                                        # de cancelamento. B julga o raciocínio completo de A.
                                        from edp.echo_chamber import detectar_auto_sinal_de_limite
                                        try:
                                            auto_sinal = detectar_auto_sinal_de_limite(full_text)
                                        except Exception as e:
                                            logger.debug("[WS] auto-sinal check (final) falhou: %s", e)
                                            auto_sinal = {"detectado": False}

                                        ativa_camara = (
                                            auto_sinal.get("detectado")
                                            and auto_sinal.get("confianca") == "alta"
                                        )

                                        if ativa_camara:
                                            logger.info(
                                                "[WS] auto-sinal no texto final | confianca=alta "
                                                "trecho='%s' texto_A=%d chars",
                                                auto_sinal["trecho"][:80],
                                                len(full_text),
                                            )
                                            chamber_result = None
                                            try:
                                                chamber = runtime.get_echo_chamber()
                                                if chamber is None:
                                                    logger.warning(
                                                        "[WS] camara indisponível — get_echo_chamber() retornou None"
                                                    )
                                                else:
                                                    from edp.model_router import escolher_modelos_B
                                                    modelo_A = runtime._client._cfg.model if runtime._client else "claude-haiku-4-5"
                                                    modelos_B = escolher_modelos_B(modelo_A)
                                                    if not modelos_B:
                                                        logger.warning(
                                                            "[WS] sem modelo B disponível para A=%s (A já é topo)",
                                                            modelo_A,
                                                        )
                                                    else:
                                                        modelo_B = modelos_B[0]
                                                        from edp.memory import _get_edp_lifetime
                                                        edp_lifetime = _get_edp_lifetime()
                                                        edp_sid = edp_lifetime.get("edp_session_id", "unknown")
                                                        logger.info(
                                                            "[WS] camara executar | A=%s B=%s edp_sid=%s texto_A=%d chars",
                                                            modelo_A, modelo_B, edp_sid[:8] if edp_sid else "?",
                                                            len(full_text),
                                                        )
                                                        loop = asyncio.get_event_loop()

                                                        # ── Ponte thread→async para eventos (2.4a.3b) ──
                                                        # Dívida #45: usa _safe_send — conexão morta
                                                        # não explode a thread do stream.
                                                        def _emit_threadsafe(payload: dict):
                                                            try:
                                                                fut = asyncio.run_coroutine_threadsafe(
                                                                    _safe_send(websocket, payload), loop
                                                                )
                                                                fut.result(timeout=2.0)
                                                            except Exception as e:
                                                                logger.debug("[WS] emit camara event falhou: %s", e)

                                                        def _on_camara_iniciada(info: dict):
                                                            logger.info(
                                                                "[WS] camara_iniciada | A=%s B=%s",
                                                                info.get("modelo_A"), info.get("modelo_B"),
                                                            )
                                                            _emit_threadsafe({
                                                                "type":      "camara_iniciada",
                                                                "camara_id": info.get("camara_id"),
                                                                "modelo_A":  info.get("modelo_A"),
                                                                "modelo_B":  info.get("modelo_B"),
                                                                "trecho_A":  info.get("trecho_A"),
                                                            })

                                                        def _on_fase_b_completa(info: dict):
                                                            checks = info.get("checks", {})
                                                            logger.info(
                                                                "[WS] camara_fase_b_completa | B=%s checks=%d latencia_B=%dms",
                                                                info.get("modelo_B"),
                                                                len(checks) if checks else 0,
                                                                int(info.get("latencia_ms_B", 0)),
                                                            )
                                                            _emit_threadsafe({
                                                                "type":             "camara_fase_b_completa",
                                                                "camara_id":        info.get("camara_id"),
                                                                "modelo_B":         info.get("modelo_B"),
                                                                "checks":           checks,
                                                                "latencia_ms_b":    info.get("latencia_ms_B"),
                                                                "tem_reformulacao": info.get("tem_reformulacao"),
                                                            })

                                                        chamber_result = await loop.run_in_executor(
                                                            None,
                                                            lambda: chamber.executar(
                                                                user_message=message,
                                                                # Dívida #52 (13/06/2026): a câmara avaliava com
                                                                # contexto_completo="" — B julgava A num vácuo e
                                                                # acusava de "confabulação" o uso legítimo de memória
                                                                # que B não via. Verificado em 2 disparos (267bb6ad:
                                                                # A citou GOFAI/EDP reais → −3/13; b94e85b5: referente
                                                                # no turno anterior → 9/13). Passa o MESMO material
                                                                # factual que A viu (janela imediata + memórias por
                                                                # similaridade) para o passo 2 (B) e o passo 3 (A reavalia).
                                                                contexto_completo=(
                                                                    "\n".join(retrieved_blocks)
                                                                    if retrieved_blocks else ""
                                                                ),
                                                                modelo_A=modelo_A,
                                                                modelo_B=modelo_B,
                                                                edp_session_id=edp_sid,
                                                                block_id=None,
                                                                texto_A_ja_gerado=full_text,
                                                                auto_sinal_confianca=auto_sinal.get("confianca"),
                                                                on_camara_iniciada=_on_camara_iniciada,
                                                                on_fase_b_completa=_on_fase_b_completa,
                                                            ),
                                                        )
                                            except Exception as e:
                                                logger.warning(
                                                    "[WS] camara falhou: %s: %s",
                                                    type(e).__name__, e, exc_info=True,
                                                )
                                                chamber_result = None

                                            if chamber_result and chamber_result.get("sucesso") and chamber_result.get("texto_final"):
                                                texto_final = chamber_result["texto_final"]
                                                camara_id = chamber_result.get("camara_id")
                                                vencedor = chamber_result.get("vencedor")
                                                logger.info(
                                                    "[WS] camara done | vencedor=%s concordancia=%d%% texto_final=%d chars camara_id=%s",
                                                    vencedor,
                                                    chamber_result.get("concordancia", 0),
                                                    len(texto_final),
                                                    camara_id,
                                                )
                                                try:
                                                    await _safe_send(websocket, {
                                                        "type":          "camara_resultado",
                                                        "texto_final":   texto_final,
                                                        "texto_A":       full_text,
                                                        "modelo_A":      modelo_A,
                                                        "modelo_B":      modelo_B,
                                                        "vencedor":      vencedor,
                                                        "concordancia":  chamber_result.get("concordancia"),
                                                        "score_A":       chamber_result.get("score_A"),
                                                        "camara_id":     camara_id,
                                                    })
                                                except Exception as e:
                                                    logger.warning("[WS] camara send falhou: %s", e)
                                                full_text = texto_final
                                                llm_used = True
                                                # ── Peça 2.4a.7: proveniência conforme vencedor ──
                                                # B venceu (refinou, dano factual corrigido) → camara:<B>.
                                                # A venceu (veto de topo agiu, ou A prevaleceu) → texto
                                                # é o original honesto de A, marca como fallback_solo
                                                # (não foi refinamento aceito de B).
                                                if vencedor == "B":
                                                    camara_source = f"camara:{modelo_B}"
                                                else:
                                                    camara_source = "llm:fallback_solo"
                                                logger.info(
                                                    "[WS] proveniência | source=%s (vencedor=%s)",
                                                    camara_source, vencedor,
                                                )
                                            else:
                                                # Fallback (a) TEMPORÁRIO (Dívida #12): câmara não pode
                                                # falhar — diferencial do EDP. Mantém texto completo de A.
                                                nota_falha = (
                                                    "\n\n_[câmara indisponível — resposta de A mantida]_"
                                                )
                                                full_text = full_text + nota_falha
                                                llm_used = bool(full_text.strip())
                                                camara_source = "llm:fallback_solo"  # 2.4a.7
                                                logger.info(
                                                    "[WS] camara fallback (a) ativado | full_text=%d chars source=llm:fallback_solo",
                                                    len(full_text),
                                                )
                                        else:
                                            # Sem auto-sinal — resposta normal de A, sem câmara
                                            llm_used = bool(full_text)
                                            logger.info("[WS] LLM done | tokens~%d", len(full_text.split()))
                                except asyncio.TimeoutError:
                                    logger.warning("[WS] LLM TIMEOUT TOTAL (%.0fs)", LLM_TOTAL_TIMEOUT_S)
                                    try:
                                        await _safe_send(websocket, {
                                            "type":  "warn",
                                            "error": f"LLM timeout ({LLM_TOTAL_TIMEOUT_S:.0f}s)",
                                        })
                                    except Exception:
                                        pass
                                except Exception as e:
                                    logger.warning("[WS] LLM falhou: %s: %s", type(e).__name__, e)
                                    try:
                                        await _safe_send(websocket, {
                                            "type":  "warn",
                                            "error": f"LLM indisponivel: {e}",
                                        })
                                    except Exception:
                                        pass
                        except QueueFull as e:
                            logger.warning("[WS] queue cheia session=%s: %s", session_id, e)
                            await _safe_send(websocket, {
                                "type":  "warn",
                                "error": "Sistema ocupado (fila cheia). Aguarde.",
                            })
                        except QueueTimeout as e:
                            logger.warning("[WS] queue timeout session=%s: %s", session_id, e)
                            await _safe_send(websocket, {
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
                        await _safe_send(websocket, {
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
                        # ── Peça 2.4a.7: se a câmara rodou, camara_source carrega
                        # a linhagem real (camara:<B> ou llm:fallback_solo). Senão,
                        # source padrão llm:<model> de uma resposta normal de A.
                        if camara_source:
                            source = camara_source
                        else:
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

                # ── Peça 2.6c: parsear saída e atualizar âncora de tarefa ──
                # Só ativo quando há tarefa em curso (modo sectioned ativo +
                # _task_anchor populado). Parser determinístico de formato
                # contratado: "## Seção N/M — Título". Se modelo seguiu o
                # formato (system prompt obriga), registra a seção entregue.
                # Se completar M de M, anchor é auto-limpa.
                if (
                    llm_used and full_text and runtime_ok
                    and hasattr(runtime, "register_section_delivered")
                    and runtime.get_task_anchor() is not None
                ):
                    try:
                        parse_result = runtime.register_section_delivered(full_text)
                        if parse_result.get("parsed"):
                            logger.info(
                                "[task] parser ok: seção %d/%d entregue%s",
                                parse_result.get("n"),
                                parse_result.get("total"),
                                " (COMPLETA)" if parse_result.get("complete") else "",
                            )
                        else:
                            logger.debug(
                                "[task] parser não detectou seção: %s",
                                parse_result.get("reason"),
                            )
                    except Exception as e:
                        logger.debug("[task] parser falhou: %s", e)

                if runtime_ok:
                    try:
                        metrics = runtime.llm_metrics()
                    except Exception:
                        pass

            except Exception as e:
                traceback.print_exc()
                logger.error("[WS] erro no turno: %s", e)
                try:
                    await _safe_send(websocket, {
                        "type":  "error",
                        "error": f"{type(e).__name__}: {e}",
                    })
                except Exception:
                    pass

            finally:
                # ── Commit B (10/06/2026): Lineage Tracking ──────────────
                # Grava origem da resposta (source_entries + modelo +
                # quality + marker). Só para turnos reais (llm_used).
                # NUNCA derruba o turno — try/except total.
                try:
                    from ...runtime.lineage import (
                        get_lineage_tracker, is_lineage_enabled,
                    )
                    if is_lineage_enabled() and llm_used and full_text:
                        _marker = None
                        try:
                            _epi = getattr(
                                getattr(memory, "_cognitive_view", None),
                                "episodic", None,
                            )
                            _marker = getattr(
                                _epi, "_current_session_marker", None,
                            )
                        except Exception:
                            pass
                        _model = None
                        try:
                            _model = getattr(
                                getattr(runtime, "_llm_config", None),
                                "model", None,
                            )
                        except Exception:
                            pass
                        _rec = get_lineage_tracker().build(
                            session_id=session_id,
                            retrieved=lineage_retrieved,
                            model_used=_model,
                            quality_score=lineage_quality.get("score"),
                            quality_verdict=lineage_quality.get("verdict"),
                            session_marker=_marker,
                        )
                        get_lineage_tracker().persist(_rec)
                        await _safe_send(websocket, {
                            "type":           "lineage",
                            "response_id":    _rec.response_id,
                            "n_sources":      _rec.n_sources,
                            "sources":        _rec.source_entries[:5],
                            "sources_ui":     sources_ui,
                            "conflict_count": conflict_count,
                            "model_used":     _rec.model_used,
                        })
                        logger.info(
                            "[WS] lineage gravado | response_id=%s n_sources=%d model=%s",
                            _rec.response_id[:8], _rec.n_sources, _model,
                        )
                except Exception as lerr:
                    logger.warning(
                        "[WS] lineage falhou (não-fatal): %s: %s",
                        type(lerr).__name__, str(lerr)[:120],
                    )

                # GARANTE 'done' SEMPRE
                try:
                    await _safe_send(websocket, {
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
