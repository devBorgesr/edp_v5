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
import edp.api.routes.mode as mode
import edp.api.routes.cognitive_decisions as cognitive_decisions
import edp.api.routes.lineage as lineage
import edp.api.routes.live_feed as live_feed

__all__ = [
    "health", "memory", "metrics", "llm", "websocket",
    "dashboard_state", "providers", "flags",
    "mode", "cognitive_decisions", "lineage",
    "live_feed",
]
