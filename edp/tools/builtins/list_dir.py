"""edp.tools.builtins.list_dir — Lista conteúdo de diretório."""
from __future__ import annotations

from ..registry import register
from ..sandbox import default_sandbox, SandboxError
from ..schemas import ToolDefinition, ToolParameter


# Diretórios que NUNCA são listados
HIDDEN_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv",
               ".pytest_cache", ".mypy_cache", ".idea", ".vscode"}


def _list_dir(path: str, show_hidden: bool = False) -> dict:
    sandbox = default_sandbox()
    try:
        resolved = sandbox.resolve(path)
        sandbox.validate_dir_list(resolved)
    except SandboxError as e:
        return {"error": str(e), "path": path}

    entries = []
    try:
        for item in sorted(resolved.iterdir()):
            name = item.name
            if not show_hidden:
                if name.startswith(".") or name in HIDDEN_DIRS:
                    continue
            entries.append({
                "name":     name,
                "type":     "dir" if item.is_dir() else "file",
                "size":     item.stat().st_size if item.is_file() else None,
            })
    except OSError as e:
        return {"error": f"erro listando: {e}", "path": path}

    return {
        "path":    str(resolved),
        "entries": entries,
        "count":   len(entries),
    }


register(ToolDefinition(
    name="list_dir",
    description=(
        "Lista arquivos e subdiretórios de um diretório. "
        "Por default não inclui itens ocultos (.*, __pycache__, node_modules, .git)."
    ),
    parameters=[
        ToolParameter("path", "string", "Caminho do diretório"),
        ToolParameter("show_hidden", "boolean", "Mostrar itens ocultos", required=False),
    ],
    handler=_list_dir,
    readonly=True,
))
