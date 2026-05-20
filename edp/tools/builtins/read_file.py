"""edp.tools.builtins.read_file — Lê arquivo do projeto."""
from __future__ import annotations

import logging
from typing import Optional

from ..registry import register
from ..sandbox import default_sandbox, SandboxError
from ..schemas import ToolDefinition, ToolParameter

logger = logging.getLogger("edp.tools.read_file")


def _read_file(path: str, max_lines: Optional[int] = None) -> dict:
    """Implementação real."""
    sandbox = default_sandbox()
    try:
        resolved = sandbox.resolve(path)
        sandbox.validate_file_read(resolved)
    except SandboxError as e:
        return {"error": str(e), "path": path}

    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            if max_lines:
                lines = []
                for i, line in enumerate(f):
                    if i >= max_lines:
                        break
                    lines.append(line)
                content = "".join(lines)
                truncated = i + 1 >= max_lines
            else:
                content = f.read()
                truncated = False
    except OSError as e:
        return {"error": f"erro ao ler: {e}", "path": path}

    return {
        "path":      str(resolved),
        "content":   content,
        "size":      len(content),
        "truncated": truncated,
        "lines":     content.count("\n") + 1,
    }


# Registro
register(ToolDefinition(
    name="read_file",
    description=(
        "Lê o conteúdo de um arquivo do projeto. "
        "Retorna o texto do arquivo. "
        "Use max_lines para limitar arquivos grandes."
    ),
    parameters=[
        ToolParameter("path", "string", "Caminho do arquivo (relativo ao projeto ou absoluto)"),
        ToolParameter("max_lines", "integer", "Limite de linhas (opcional)", required=False),
    ],
    handler=_read_file,
    readonly=True,
))
