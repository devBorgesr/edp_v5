"""
edp.tools.registry — Registro central de tools + executor.

Use:
    from edp.tools import registry, ToolCall

    # Registra tool
    @registry.register
    def my_tool(arg: str) -> str:
        ...

    # Lista para enviar ao LLM
    tools_schemas = registry.list_schemas("anthropic")

    # Executa
    result = registry.execute(ToolCall(call_id="x", tool_name="my_tool", arguments={"arg": "v"}))
"""
from __future__ import annotations

import logging
import time
import threading
from typing import Any, Callable, Dict, List, Optional

from .schemas import ToolDefinition, ToolCall, ToolResult, ToolParameter

logger = logging.getLogger("edp.tools.registry")


class ToolRegistry:
    """Registro thread-safe de tools."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}
        self._lock = threading.RLock()

    def register(self, tool: ToolDefinition) -> ToolDefinition:
        """Registra uma tool. Idempotente."""
        if tool.handler is None:
            raise ValueError(f"Tool {tool.name} sem handler")
        with self._lock:
            self._tools[tool.name] = tool
            logger.info("[tools] registrada: %s (readonly=%s)", tool.name, tool.readonly)
        return tool

    def unregister(self, name: str) -> None:
        with self._lock:
            self._tools.pop(name, None)

    def get(self, name: str) -> Optional[ToolDefinition]:
        with self._lock:
            return self._tools.get(name)

    def list(self) -> List[ToolDefinition]:
        with self._lock:
            return list(self._tools.values())

    def list_names(self) -> List[str]:
        with self._lock:
            return sorted(self._tools.keys())

    def list_schemas(self, format: str = "anthropic") -> List[dict]:
        """Retorna schemas para enviar ao LLM."""
        with self._lock:
            tools = list(self._tools.values())
        if format == "anthropic":
            return [t.to_anthropic_schema() for t in tools]
        elif format == "openai":
            return [t.to_openai_schema() for t in tools]
        else:
            raise ValueError(f"format '{format}' não suportado")

    def execute(
        self,
        call: ToolCall,
        timeout_s: float = 30.0,
        allow_writes: bool = False,
    ) -> ToolResult:
        """
        Executa uma tool com timeout.

        Args:
            call: ToolCall do LLM
            timeout_s: máximo de tempo de execução
            allow_writes: se False, bloqueia tools com readonly=False
        """
        t_start = time.perf_counter()
        tool = self.get(call.tool_name)
        if tool is None:
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                success=False,
                output=None,
                error=f"Tool '{call.tool_name}' não registrada. "
                      f"Disponíveis: {self.list_names()}",
            )

        if not tool.readonly and not allow_writes:
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                success=False,
                output=None,
                error=f"Tool '{call.tool_name}' requer write permission "
                      f"(allow_writes=False)",
            )

        # Executa com timeout via threading
        result_container = [None]
        error_container  = [None]

        def _run():
            try:
                result_container[0] = tool.handler(**call.arguments)
            except Exception as e:
                error_container[0] = e

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=timeout_s)

        latency = (time.perf_counter() - t_start) * 1000.0

        if thread.is_alive():
            # Thread ainda rodando = timeout
            logger.warning(
                "[tools] '%s' TIMEOUT após %.1fs (continua em background)",
                call.tool_name, timeout_s,
            )
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                success=False,
                output=None,
                error=f"Timeout após {timeout_s}s",
                latency_ms=latency,
            )

        if error_container[0] is not None:
            err = error_container[0]
            logger.warning(
                "[tools] '%s' erro: %s: %s",
                call.tool_name, type(err).__name__, err,
            )
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                success=False,
                output=None,
                error=f"{type(err).__name__}: {err}",
                latency_ms=latency,
            )

        logger.info(
            "[tools] '%s' ok | lat=%.1fms",
            call.tool_name, latency,
        )
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.tool_name,
            success=True,
            output=result_container[0],
            latency_ms=latency,
        )


# Instância global default
_global_registry = ToolRegistry()


def register(tool: ToolDefinition) -> ToolDefinition:
    return _global_registry.register(tool)


def get_registry() -> ToolRegistry:
    return _global_registry


def execute(call: ToolCall, **kwargs) -> ToolResult:
    return _global_registry.execute(call, **kwargs)


def list_schemas(format: str = "anthropic") -> List[dict]:
    return _global_registry.list_schemas(format)


def list_names() -> List[str]:
    return _global_registry.list_names()
