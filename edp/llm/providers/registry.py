"""
edp.llm.providers.registry — Factory + descoberta de providers.

Uso:
    from edp.llm.providers import get_provider, list_providers

    provider = get_provider("anthropic", api_key="sk-ant-...", model="claude-sonnet-4-6")
    response = provider.complete(request)

    print(list_providers())  # ["anthropic", "ollama", "openai", "lmstudio"]
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Type, List, Optional

from .base import LLMProviderBase, ProviderConfig

logger = logging.getLogger("edp.llm.providers.registry")


# ── Registro de providers ────────────────────────────────────────────────────

_PROVIDERS: Dict[str, Type[LLMProviderBase]] = {}


def register_provider(name: str, cls: Type[LLMProviderBase]) -> None:
    """Registra um provider. Idempotente."""
    _PROVIDERS[name] = cls
    logger.debug("[registry] provider registrado: %s -> %s", name, cls.__name__)


def list_providers() -> List[str]:
    """Lista nomes de providers disponíveis."""
    return sorted(_PROVIDERS.keys())


def get_provider_class(name: str) -> Type[LLMProviderBase]:
    """Retorna a classe de um provider."""
    name = name.lower().strip()
    if name not in _PROVIDERS:
        raise ValueError(
            f"Provider '{name}' desconhecido. Disponíveis: {list_providers()}"
        )
    return _PROVIDERS[name]


def get_provider(
    name: str,
    model: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout_s: float = 300.0,
    **extra,
) -> LLMProviderBase:
    """
    Factory: cria provider configurado.

    Resolução de api_key (em ordem):
      1. Argumento explícito
      2. extra["api_key"]
      3. Variável de ambiente padronizada por provider
    """
    cls = get_provider_class(name)

    # Auto-discover API key se não fornecida
    if api_key is None:
        env_var = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai":    "OPENAI_API_KEY",
        }.get(name.lower(), "")
        if env_var:
            api_key = os.environ.get(env_var, "")

    config = ProviderConfig(
        api_key=api_key or "",
        model=model,
        base_url=base_url or "",
        timeout_s=timeout_s,
        extra=extra,
    )
    return cls(config)


# ── Auto-registro de providers conhecidos ────────────────────────────────────

def _autoload() -> None:
    """Tenta carregar todos os providers. Falhas individuais não quebram o conjunto."""
    try:
        from .anthropic import AnthropicProvider
        register_provider("anthropic", AnthropicProvider)
    except Exception as e:
        logger.debug("[registry] AnthropicProvider não carregado: %s", e)

    try:
        from .ollama import OllamaProvider
        register_provider("ollama", OllamaProvider)
    except Exception as e:
        logger.debug("[registry] OllamaProvider não carregado: %s", e)


_autoload()
