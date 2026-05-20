"""
edp.tools.sandbox — Proteções de segurança para tools que tocam filesystem.

Princípios:
  - Allow-list de diretórios permitidos
  - Bloqueio de path traversal (..)
  - Bloqueio de paths sensíveis (.git interno, .env, chaves SSH, etc.)
  - Resolução de symlinks antes de validar
  - Tamanho máximo de arquivo
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("edp.tools.sandbox")


# Paths que NUNCA podem ser lidos, mesmo se estiverem em allow-list
DENY_PATTERNS = [
    ".env",
    ".ssh",
    "id_rsa", "id_ed25519", "id_ecdsa",
    "credentials",
    "secret",
    "private_key",
]


# Extensões de arquivo permitidas para leitura
SAFE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".md", ".txt", ".rst",
    ".json", ".yaml", ".yml", ".toml", ".ini",
    ".html", ".css", ".scss",
    ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp",
    ".sh", ".bash",
    ".sql",
    ".xml",
    ".log",
    "",  # arquivos sem extensão (LICENSE, README, etc.)
}


# Tamanho máximo de arquivo (10 MB)
MAX_FILE_SIZE = 10 * 1024 * 1024


class SandboxError(Exception):
    """Violação de regra de segurança."""


class PathSandbox:
    """
    Sandbox de paths.

    Use:
        sandbox = PathSandbox(allowed_roots=["/home/user/project"])
        safe_path = sandbox.resolve("src/main.py")  # OK
        sandbox.resolve("../../etc/passwd")          # SandboxError
    """

    def __init__(
        self,
        allowed_roots: List[str],
        max_file_size: int = MAX_FILE_SIZE,
        extra_deny: Optional[List[str]] = None,
    ):
        # Resolve e normaliza roots
        self.allowed_roots = [Path(r).resolve() for r in allowed_roots]
        if not self.allowed_roots:
            raise ValueError("Pelo menos 1 allowed_root é obrigatório")
        self.max_file_size = max_file_size
        self.deny_patterns = list(DENY_PATTERNS) + list(extra_deny or [])
        logger.info(
            "[sandbox] criado | roots=%s deny=%s",
            [str(r) for r in self.allowed_roots], self.deny_patterns,
        )

    def resolve(self, user_path: str) -> Path:
        """
        Resolve path do usuário para path absoluto seguro.
        Lança SandboxError se violar regras.
        """
        if not user_path or not isinstance(user_path, str):
            raise SandboxError("path vazio ou inválido")

        # Bloqueia tentativas óbvias
        if "\x00" in user_path:
            raise SandboxError("null byte detectado em path")

        # Resolve absoluto (segue symlinks)
        try:
            candidate = Path(user_path).resolve()
        except (OSError, RuntimeError) as e:
            raise SandboxError(f"erro resolvendo path: {e}")

        # Confere allow-list de roots
        if not any(self._is_within(candidate, root) for root in self.allowed_roots):
            raise SandboxError(
                f"path '{user_path}' está fora dos diretórios permitidos: "
                f"{[str(r) for r in self.allowed_roots]}"
            )

        # Confere padrões de deny
        path_str = str(candidate).lower()
        for pattern in self.deny_patterns:
            if pattern.lower() in path_str:
                raise SandboxError(f"path contém padrão bloqueado: '{pattern}'")

        return candidate

    def _is_within(self, candidate: Path, root: Path) -> bool:
        """Confere se candidate é descendente de root."""
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False

    def validate_file_read(self, path: Path) -> None:
        """Valida que arquivo pode ser lido."""
        if not path.exists():
            raise SandboxError(f"arquivo não existe: {path}")
        if not path.is_file():
            raise SandboxError(f"não é arquivo: {path}")
        if path.suffix.lower() not in SAFE_EXTENSIONS:
            raise SandboxError(
                f"extensão '{path.suffix}' não permitida. "
                f"Permitidas: {sorted(SAFE_EXTENSIONS)}"
            )
        try:
            size = path.stat().st_size
        except OSError as e:
            raise SandboxError(f"erro lendo metadata: {e}")
        if size > self.max_file_size:
            raise SandboxError(
                f"arquivo muito grande: {size} bytes "
                f"(máximo: {self.max_file_size})"
            )

    def validate_dir_list(self, path: Path) -> None:
        if not path.exists():
            raise SandboxError(f"diretório não existe: {path}")
        if not path.is_dir():
            raise SandboxError(f"não é diretório: {path}")


def default_sandbox() -> PathSandbox:
    """
    Sandbox default: permite o diretório de trabalho atual.
    Pode ser overridado via EDP_TOOLS_ALLOWED_ROOTS=path1:path2.
    """
    env = os.environ.get("EDP_TOOLS_ALLOWED_ROOTS", "")
    roots = [r for r in env.split(os.pathsep) if r] if env else [os.getcwd()]
    return PathSandbox(allowed_roots=roots)
