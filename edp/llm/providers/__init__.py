"""edp.llm.providers — Camada unificada de providers."""
from .base import (
    LLMProviderBase, ProviderConfig,
    CompletionRequest, CompletionResponse, CompletionMetrics,
    Message, Role,
    ProviderError, AuthError, RateLimitError, ProviderTimeout, ContentFilterError,
)
from .registry import (
    register_provider, list_providers, get_provider, get_provider_class,
)

__all__ = [
    "LLMProviderBase", "ProviderConfig",
    "CompletionRequest", "CompletionResponse", "CompletionMetrics",
    "Message", "Role",
    "ProviderError", "AuthError", "RateLimitError", "ProviderTimeout", "ContentFilterError",
    "register_provider", "list_providers", "get_provider", "get_provider_class",
]
