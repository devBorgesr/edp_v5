"""
edp.observability.logger — Logger estruturado com correlation IDs.

Drop-in: módulos antigos continuam usando logging.getLogger() normal.
Para usar observabilidade nova:

    from edp.observability import get_logger, request_context

    log = get_logger("edp.api")
    with request_context() as rid:
        log.info("processing", extra={"req_id": rid, "session": "x"})

Saída JSON ativada via env:
    EDP_LOG_JSON=1  → logs em JSON
    EDP_LOG_LEVEL=DEBUG → nível
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Optional


# ── Thread-local para correlation IDs ────────────────────────────────────────
_ctx = threading.local()


def current_request_id() -> Optional[str]:
    """Retorna request_id da thread atual, se houver."""
    return getattr(_ctx, "request_id", None)


def current_session_id() -> Optional[str]:
    return getattr(_ctx, "session_id", None)


@contextmanager
def request_context(request_id: Optional[str] = None,
                    session_id: Optional[str] = None):
    """
    Context manager que injeta IDs no thread-local.

    Use:
        with request_context(session_id="default") as rid:
            log.info("foo")  # automaticamente inclui rid
    """
    prev_rid = getattr(_ctx, "request_id", None)
    prev_sid = getattr(_ctx, "session_id", None)
    rid = request_id or uuid.uuid4().hex[:12]
    _ctx.request_id = rid
    if session_id:
        _ctx.session_id = session_id
    try:
        yield rid
    finally:
        _ctx.request_id = prev_rid
        _ctx.session_id = prev_sid


# ── Formatter JSON ───────────────────────────────────────────────────────────

class JSONFormatter(logging.Formatter):
    """Formatter que serializa LogRecord como JSON estruturado."""

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts":      time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level":   record.levelname,
            "logger":  record.name,
            "msg":     record.getMessage(),
        }
        rid = current_request_id()
        if rid:
            data["req_id"] = rid
        sid = current_session_id()
        if sid:
            data["session"] = sid

        # Extras do record (passados via extra={...})
        for key, val in record.__dict__.items():
            if key in ("args", "msg", "name", "levelname", "levelno", "pathname",
                       "filename", "module", "exc_info", "exc_text", "stack_info",
                       "lineno", "funcName", "created", "msecs", "relativeCreated",
                       "thread", "threadName", "processName", "process", "message",
                       "taskName"):
                continue
            try:
                json.dumps(val)
                data[key] = val
            except (TypeError, ValueError):
                data[key] = str(val)

        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)

        return json.dumps(data, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    """Formatter humano com request_id quando presente."""

    def format(self, record: logging.LogRecord) -> str:
        rid = current_request_id()
        rid_part = f"[{rid}]" if rid else ""
        base = f"{time.strftime('%H:%M:%S')} {record.levelname:7s} {record.name}{rid_part} {record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


# ── Configuração global ──────────────────────────────────────────────────────

_configured = False


def configure_logging(level: Optional[str] = None,
                      use_json: Optional[bool] = None) -> None:
    """
    Configura logging global. Idempotente.

    Args:
        level: 'DEBUG', 'INFO', 'WARNING'. Default: env EDP_LOG_LEVEL ou INFO.
        use_json: True para JSON, False para humano. Default: env EDP_LOG_JSON.
    """
    global _configured
    if _configured:
        return

    level_str = (level or os.environ.get("EDP_LOG_LEVEL", "INFO")).upper()
    json_mode = use_json
    if json_mode is None:
        json_mode = os.environ.get("EDP_LOG_JSON", "0") == "1"

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter() if json_mode else HumanFormatter())

    root = logging.getLogger("edp")
    root.setLevel(getattr(logging, level_str, logging.INFO))

    # Evita duplicação se já há handlers
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Retorna logger configurado. Garante configuração na primeira chamada.
    """
    configure_logging()
    return logging.getLogger(name)
