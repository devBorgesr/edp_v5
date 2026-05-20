"""edp.tools — Tool calling foundation (read-only inicial)."""
from .schemas import (
    ToolDefinition, ToolParameter, ToolCall, ToolResult,
)
from .registry import (
    register, get_registry, execute, list_schemas, list_names,
    ToolRegistry,
)
from .sandbox import (
    PathSandbox, SandboxError, default_sandbox,
)

# Auto-registro de builtins ao importar pacote
from .builtins import read_file, list_dir, search_code  # noqa: F401

__all__ = [
    "ToolDefinition", "ToolParameter", "ToolCall", "ToolResult",
    "register", "get_registry", "execute", "list_schemas", "list_names",
    "ToolRegistry",
    "PathSandbox", "SandboxError", "default_sandbox",
]
