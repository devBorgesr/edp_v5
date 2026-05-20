"""edp.api.routes — Routers FastAPI separados por responsabilidade."""
from . import (
    health, memory, metrics, llm, websocket,
    dashboard_state, providers, flags,
)

__all__ = [
    "health", "memory", "metrics", "llm", "websocket",
    "dashboard_state", "providers", "flags",
]
