"""
edp.ingest.websocket_receiver — WebSocket /stream: porta de entrada de
eventos do sensor (extensão) para a memória do EDP.

Ver WEBSOCKET_API.md para o contrato completo (autenticação, schema de
evento, exemplos).

Comunicação estritamente unidirecional (sensor -> EDP): o único retorno do
servidor é pong (heartbeat) ou error (evento inválido/falha de
processamento) — o EDP nunca envia comandos de volta ao sensor.
"""
from __future__ import annotations

import logging
import secrets
import threading
from logging.handlers import RotatingFileHandler
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import config
from ..runtime import get_memory, is_valid, get_error
from .consolidator import consolidate_session
from .events import (
    extract_ids,
    extract_text,
    normalize_timestamp,
    registry_key,
    validate_event,
)
from .session_index import get_session_index

logger = logging.getLogger("edp.live_feed")
_logger_lock = threading.Lock()


def _ensure_file_handler() -> None:
    """
    Cria o RotatingFileHandler de auditoria sob demanda (na 1ª conexão),
    não no import do módulo — BASE_DIR pode não existir/ser gravável ainda
    nesse momento (mesma razão pela qual EpisodicMemory cria seu scope_dir
    lazily, só quando uma sessão é de fato instanciada). Também mantém o
    módulo importável em testes que redirecionam config.BASE_DIR via
    monkeypatch antes do primeiro uso.
    """
    if logger.handlers:
        return
    with _logger_lock:
        if logger.handlers:
            return
        try:
            config.LIVE_FEED_LOG.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                config.LIVE_FEED_LOG, maxBytes=5 * 1024 * 1024, backupCount=3,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            # Diagnóstico cruzado exportador<->EDP (2026-08-03): propagate=False
            # escondia TODO "event=" do console/stdout do servidor — quem
            # rodava `python run.py serve` só via "WebSocket /stream [accepted]"
            # / "connection open" (linhas do access log do uvicorn, que não
            # passam por este logger) e concluía "nenhum evento chega ao EDP",
            # quando na verdade os eventos chegavam e eram rejeitados/aceitos
            # normalmente — só não apareciam no console. live_feed.log continua
            # existindo como trilha de auditoria dedicada; propagar ao root
            # também é necessário para operar o serve no dia a dia.
        except Exception as e:
            logging.getLogger("edp.ingest").warning(
                "[stream] não foi possível criar live_feed.log (%s) — "
                "auditoria de eventos seguirá só no logger root", e,
            )


router = APIRouter(tags=["live_feed"])

# Eventos que valem um pouco mais de score do que deltas finos (thinking),
# para que _prune() (EpisodicMemory) tenha um critério de desempate melhor
# que "mais antigo sobrevive" quando a sessão excede EDP_EPISODIC_SIZE.
_HIGH_VALUE_TYPES = frozenset({"response_end", "tool_call", "tool_result"})


def _extract_token(websocket: WebSocket) -> Optional[str]:
    token = websocket.query_params.get("token")
    if token:
        return token
    proto = websocket.headers.get("sec-websocket-protocol")
    if proto:
        return proto.split(",")[0].strip()
    return None


def _token_ok(token: Optional[str]) -> bool:
    expected = config.EDP_LIVE_FEED_TOKEN
    if not expected:
        return True
    if not token:
        return False
    return secrets.compare_digest(token, expected)


async def _safe_send(websocket: WebSocket, payload: dict) -> None:
    try:
        await websocket.send_json(payload)
    except Exception as e:
        logger.debug("[stream] send falhou (conexão morta?): %s", e)


def _process_event(event: dict) -> Optional[str]:
    """
    Valida, extrai metadados e persiste um evento na memória do sensor.

    Retorna None se processado com sucesso, ou uma mensagem de erro se o
    evento é inválido. Exceções inesperadas (memória indisponível, etc.)
    propagam — o chamador decide o que fazer (loga e segue, nunca derruba
    a conexão por causa de 1 evento).
    """
    ok, reason = validate_event(event)
    if not ok:
        return reason

    ids             = extract_ids(event)
    conversation_id = ids["conversation_id"]
    session_id      = ids["session_id"]
    profile_id      = ids["profile_id"]
    event_type      = event["type"]

    # Contrato: event_timestamp é SEMPRE epoch seconds na memória (mesma
    # escala do timestamp interno do EDP). Um emissor em milissegundos é
    # coagido aqui, não rejeitado — ver normalize_timestamp() para o porquê.
    event_timestamp, ts_coerced = normalize_timestamp(event["timestamp"])
    if ts_coerced:
        logger.warning(
            "[stream] timestamp em milissegundos coagido para segundos "
            "(emissor desatualizado? esperado epoch seconds) "
            "conversation_id=%s event_type=%s",
            conversation_id, event_type,
        )

    key = registry_key(conversation_id)
    mem = get_memory(key)
    if not is_valid(mem):
        raise RuntimeError(f"memory indisponível: {get_error(mem)}")

    text  = extract_text(event)
    score = 0.5 if event_type in _HIGH_VALUE_TYPES else 0.35

    entry = mem.add(text=text, score=score, source="live_feed", confidence=0.3)
    entry_id = entry.get("id")
    metadata = {
        "event_type":      event_type,
        "event_timestamp": event_timestamp,
        "conversation_id": conversation_id,
        "session_id":      session_id,
        "profile_id":      profile_id,
    }
    # EpisodicMemory.add() copia o dict recebido — a mutação precisa ser
    # reaplicada na entry realmente persistida em episodic.entries.
    entry.update(metadata)
    for e in mem.episodic.entries:
        if e.get("id") == entry_id:
            e.update(metadata)
            break
    mem.episodic._dirty = True

    if profile_id:
        get_session_index().touch(profile_id, conversation_id, event_timestamp)

    if event_type == "response_end":
        mem.episodic.flush()
        get_session_index().flush()
        threading.Thread(
            target=consolidate_session, args=(conversation_id,), daemon=True,
        ).start()
        logger.info(
            "[stream] response_end -> consolidação disparada conversation_id=%s",
            conversation_id,
        )

    return None


def _audit_log(raw: dict) -> None:
    try:
        logger.info("event=%s", raw)
    except Exception:
        pass


@router.websocket("/stream")
async def stream(websocket: WebSocket) -> None:
    _ensure_file_handler()
    token = _extract_token(websocket)
    if not _token_ok(token):
        logger.warning("[stream] conexão rejeitada: token ausente/inválido")
        await websocket.close(code=4401)
        return
    if not config.EDP_LIVE_FEED_TOKEN:
        logger.warning(
            "[stream] EDP_LIVE_FEED_TOKEN não configurado — aceitando sem autenticação"
        )

    subprotocol = None
    proto_header = websocket.headers.get("sec-websocket-protocol")
    if proto_header:
        subprotocol = proto_header.split(",")[0].strip()
    await websocket.accept(subprotocol=subprotocol)
    logger.info("[stream] conectado")

    touched_conversations: set[str] = set()

    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.warning("[stream] receive_json falhou: %s", e)
                await _safe_send(
                    websocket, {"type": "error", "error": "payload não é JSON válido"}
                )
                continue

            if not isinstance(raw, dict):
                await _safe_send(
                    websocket, {"type": "error", "error": "payload não é um objeto JSON"}
                )
                continue

            if raw.get("type") == "ping":
                await _safe_send(websocket, {"type": "pong"})
                continue

            _audit_log(raw)

            try:
                error = _process_event(raw)
                if error:
                    logger.info("[stream] evento inválido: %s", error)
                    await _safe_send(websocket, {"type": "error", "error": error})
                else:
                    cid = raw.get("conversation_id")
                    if cid:
                        touched_conversations.add(cid)
            except Exception as e:
                logger.error(
                    "[stream] falha ao processar evento: %s: %s",
                    type(e).__name__, e,
                )
                await _safe_send(
                    websocket, {"type": "error", "error": f"falha interna: {e}"}
                )

    except WebSocketDisconnect:
        logger.info("[stream] desconectado")
    except Exception as e:
        logger.error("[stream] erro fatal: %s: %s", type(e).__name__, e)
    finally:
        for conversation_id in touched_conversations:
            try:
                mem = get_memory(registry_key(conversation_id))
                if is_valid(mem):
                    mem.episodic.flush()
            except Exception as e:
                logger.debug("[stream] flush final falhou conversation_id=%s: %s",
                             conversation_id, e)
        try:
            get_session_index().flush()
        except Exception as e:
            logger.debug("[stream] flush do session_index falhou: %s", e)
