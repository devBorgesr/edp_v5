"""edp.api.routes — Routers FastAPI separados por responsabilidade."""
# Import absoluto por submódulo (não `from . import (...)`): a forma
# relativa combinada resolve "." para o próprio __init__.py em execução,
# o que o graphify lê como autociclo (`__init__.py -> __init__.py` em
# GRAPH_REPORT.md, "Import Cycles"). Mesmo comportamento em runtime —
# só muda a forma sintática do import, sem laço aparente no grafo.
import edp.api.routes.health as health
import edp.api.routes.memory as memory
import edp.api.routes.metrics as metrics
import edp.api.routes.llm as llm
import edp.api.routes.websocket as websocket
import edp.api.routes.dashboard_state as dashboard_state
import edp.api.routes.providers as providers
import edp.api.routes.flags as flags

__all__ = [
    "health", "memory", "metrics", "llm", "websocket",
    "dashboard_state", "providers", "flags",
]
