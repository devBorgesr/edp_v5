"""
edp.tools.schemas — Tipos de tool calling.

Compatível com formato Anthropic (`tools` array em messages API).
Conversível para formato OpenAI function calling.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolParameter:
    name:        str
    type:        str           # "string", "integer", "number", "boolean", "array", "object"
    description: str
    required:    bool          = True
    enum:        Optional[List[str]] = None


@dataclass
class ToolDefinition:
    """
    Definição de uma tool que o LLM pode chamar.

    Exemplo:
        ToolDefinition(
            name="read_file",
            description="Lê o conteúdo de um arquivo",
            parameters=[
                ToolParameter("path", "string", "Caminho do arquivo"),
                ToolParameter("max_lines", "integer", "Limite de linhas", required=False),
            ],
        )
    """
    name:        str
    description: str
    parameters:  List[ToolParameter] = field(default_factory=list)
    handler:     Optional[Callable]  = None
    readonly:    bool = True   # se True, não pede permissão extra

    def to_anthropic_schema(self) -> dict:
        """Converte para formato Anthropic tools API."""
        props: Dict[str, Any] = {}
        required: List[str]  = []
        for p in self.parameters:
            schema: Dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                schema["enum"] = p.enum
            props[p.name] = schema
            if p.required:
                required.append(p.name)
        return {
            "name":         self.name,
            "description":  self.description,
            "input_schema": {
                "type":       "object",
                "properties": props,
                "required":   required,
            },
        }

    def to_openai_schema(self) -> dict:
        """Converte para formato OpenAI function calling."""
        anthropic = self.to_anthropic_schema()
        return {
            "type": "function",
            "function": {
                "name":        anthropic["name"],
                "description": anthropic["description"],
                "parameters":  anthropic["input_schema"],
            },
        }


@dataclass
class ToolCall:
    """Chamada de tool emitida pelo LLM."""
    call_id:   str
    tool_name: str
    arguments: Dict[str, Any]


@dataclass
class ToolResult:
    """Resultado de execução de tool."""
    call_id:   str
    tool_name: str
    success:   bool
    output:    Any
    error:     Optional[str] = None
    latency_ms: float        = 0.0
