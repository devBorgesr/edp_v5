"""
edp.observability.tracing — Tracing leve baseado em context managers.

Não usa OpenTelemetry (overhead grande). Tracing local com:
  - spans aninhados
  - timing automático
  - export como JSON via callback

Use:
    from edp.observability import trace

    with trace("pipeline.run", session="default") as span:
        with trace("retrieve"):
            ...
        with trace("compress"):
            ...
"""
from __future__ import annotations

import logging
import time
import threading
import uuid
from contextlib import contextmanager
from typing import Any, Callable, List, Optional

logger = logging.getLogger("edp.tracing")


_active_spans = threading.local()


class Span:
    def __init__(self, name: str, parent_id: Optional[str] = None, **attrs):
        self.span_id   = uuid.uuid4().hex[:8]
        self.parent_id = parent_id
        self.name      = name
        self.start     = time.perf_counter()
        self.end:      Optional[float] = None
        self.attrs     = dict(attrs)
        self.events:   List[dict] = []

    @property
    def duration_ms(self) -> float:
        end = self.end if self.end is not None else time.perf_counter()
        return (end - self.start) * 1000.0

    def add_event(self, name: str, **data) -> None:
        self.events.append({
            "name": name,
            "ts":   time.perf_counter() - self.start,
            **data,
        })

    def set_attr(self, key: str, value: Any) -> None:
        self.attrs[key] = value

    def to_dict(self) -> dict:
        return {
            "span_id":     self.span_id,
            "parent_id":   self.parent_id,
            "name":        self.name,
            "duration_ms": round(self.duration_ms, 2),
            "attrs":       self.attrs,
            "events":      self.events,
        }


def _get_stack() -> list:
    stack = getattr(_active_spans, "stack", None)
    if stack is None:
        stack = []
        _active_spans.stack = stack
    return stack


def current_span() -> Optional[Span]:
    stack = _get_stack()
    return stack[-1] if stack else None


# Callbacks para export de spans finalizados
_exporters: List[Callable[[Span], None]] = []


def register_exporter(fn: Callable[[Span], None]) -> None:
    """Registra função que recebe cada span finalizado."""
    _exporters.append(fn)


@contextmanager
def trace(name: str, **attrs):
    """
    Context manager de tracing.

    Use:
        with trace("operation", session="x") as span:
            span.set_attr("model", "claude")
            ...
    """
    parent = current_span()
    span = Span(name, parent_id=parent.span_id if parent else None, **attrs)
    _get_stack().append(span)
    try:
        yield span
    except Exception as e:
        span.set_attr("error", str(e))
        span.set_attr("error_type", type(e).__name__)
        raise
    finally:
        span.end = time.perf_counter()
        _get_stack().pop()
        # Exporta para callbacks registrados
        for ex in _exporters:
            try:
                ex(span)
            except Exception as e:
                logger.debug("[tracing] exporter falhou: %s", e)
        # Log default
        logger.debug(
            "[span] %s dur=%.1fms attrs=%s",
            span.name, span.duration_ms, span.attrs,
        )
