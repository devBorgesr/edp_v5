"""edp.tools.builtins.search_code — Busca textual em arquivos do projeto."""
from __future__ import annotations

import re

from ..registry import register
from ..sandbox import default_sandbox, SandboxError, SAFE_EXTENSIONS
from ..schemas import ToolDefinition, ToolParameter


SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv",
             ".pytest_cache", ".mypy_cache"}


def _search_code(
    query: str,
    path: str = ".",
    case_sensitive: bool = False,
    max_results: int = 50,
) -> dict:
    sandbox = default_sandbox()
    try:
        resolved = sandbox.resolve(path)
        sandbox.validate_dir_list(resolved)
    except SandboxError as e:
        return {"error": str(e), "path": path}

    if len(query) < 2:
        return {"error": "query muito curta (mín 2 chars)"}
    if len(query) > 200:
        return {"error": "query muito longa (máx 200 chars)"}

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(re.escape(query), flags)
    except re.error as e:
        return {"error": f"regex inválida: {e}"}

    results = []
    files_scanned = 0

    for item in resolved.rglob("*"):
        if any(part in SKIP_DIRS for part in item.parts):
            continue
        if not item.is_file():
            continue
        if item.suffix.lower() not in SAFE_EXTENSIONS:
            continue

        try:
            with open(item, "r", encoding="utf-8", errors="replace") as f:
                for line_no, line in enumerate(f, start=1):
                    if pattern.search(line):
                        results.append({
                            "file": str(item.relative_to(resolved)),
                            "line": line_no,
                            "text": line.rstrip("\n")[:200],
                        })
                        if len(results) >= max_results:
                            break
        except (OSError, UnicodeDecodeError):
            continue

        files_scanned += 1
        if len(results) >= max_results:
            break

    return {
        "query":         query,
        "results":       results,
        "count":         len(results),
        "files_scanned": files_scanned,
        "truncated":     len(results) >= max_results,
    }


register(ToolDefinition(
    name="search_code",
    description=(
        "Busca por uma string em arquivos do projeto (recursivo). "
        "Pula diretórios como .git, node_modules, __pycache__. "
        "Retorna até 50 resultados com arquivo + linha + trecho."
    ),
    parameters=[
        ToolParameter("query", "string", "Texto a buscar"),
        ToolParameter("path", "string", "Diretório a buscar (default: '.')", required=False),
        ToolParameter("case_sensitive", "boolean", "Diferenciar maiúsculas", required=False),
        ToolParameter("max_results", "integer", "Limite de resultados (default 50)", required=False),
    ],
    handler=_search_code,
    readonly=True,
))
