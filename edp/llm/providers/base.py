"""
edp.llm.providers.base — Interface abstrata para providers LLM.

Princípios:
  - Contract-first: toda implementação concreta segue esta interface
  - Async-friendly: streams como iteradores
  - Type-safe: tipos explícitos para evitar drift entre providers
  - Métricas embutidas: cada chamada expõe (latency_ms, first_token_ms, tokens, cost_estimate)
  - Erros tipados: TimeoutError, RateLimitError, AuthError, ProviderError
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Iterator, Optional, List, Dict, Any

logger = logging.getLogger("edp.llm.providers")


# ── Tipos compartilhados ─────────────────────────────────────────────────────

class ProviderError(Exception):
    """Erro genérico de provider."""

class AuthError(ProviderError):
    """API key inválida ou ausente."""

class RateLimitError(ProviderError):
    """Quota excedida — retry após backoff."""

class ProviderTimeout(ProviderError):
    """Provider não respondeu dentro do limite."""

class ContentFilterError(ProviderError):
    """Conteúdo bloqueado pelo provider (safety)."""


class Role(str, Enum):
    SYSTEM    = "system"
    USER      = "user"
    ASSISTANT = "assistant"
    TOOL      = "tool"


@dataclass
class Message:
    role:    Role
    content: str
    name:    Optional[str] = None  # para tool messages

    def to_dict(self) -> dict:
        d = {"role": self.role.value, "content": self.content}
        if self.name:
            d["name"] = self.name
        return d


@dataclass
class CompletionRequest:
    """Pedido de completion (síncrono ou stream)."""
    messages:    List[Message]
    system:      Optional[str]  = None
    temperature: float          = 0.7
    max_tokens:  int            = 2048
    stop:        Optional[List[str]] = None
    metadata:    Dict[str, Any] = field(default_factory=dict)


@dataclass
class CompletionMetrics:
    latency_ms:        float = 0.0
    first_token_ms:    float = 0.0
    prompt_tokens:     int   = 0
    completion_tokens: int   = 0
    total_tokens:      int   = 0
    cost_usd:          float = 0.0


@dataclass
class CompletionResponse:
    text:    str
    model:   str
    metrics: CompletionMetrics
    stop_reason: Optional[str] = None
    raw:     Optional[dict]    = None  # resposta crua do provider


@dataclass
class ProviderConfig:
    """Configuração genérica de provider."""
    api_key:     str          = ""
    base_url:    str          = ""
    model:       str          = ""
    timeout_s:   float        = 300.0
    max_retries: int          = 2
    backoff_s:   float        = 1.0
    extra:       Dict[str, Any] = field(default_factory=dict)


# ── Interface abstrata ───────────────────────────────────────────────────────

class LLMProviderBase(ABC):
    """
    Interface abstrata para todos os providers LLM.

    Implementações concretas:
      - AnthropicProvider (Claude)
      - OpenAIProvider (GPT)
      - OllamaProvider (local)
      - LMStudioProvider (local)

    Cada provider deve implementar:
      - complete()    — chamada síncrona
      - stream()      — streaming via iterator
      - validate()    — testa credenciais sem custo
      - estimate_cost() — estimativa de custo (None se local/gratuito)

    Erros propagados:
      - AuthError       — 401/403
      - RateLimitError  — 429
      - ProviderTimeout — sem resposta no timeout
      - ContentFilterError — 400 com filtro de safety
      - ProviderError   — outros erros
    """

    name: str = "base"

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self._validate_config()

    def _validate_config(self) -> None:
        """Valida configuração mínima. Override para validações específicas."""
        if not self.config.model:
            raise ValueError(f"[{self.name}] model é obrigatório")

    @abstractmethod
    def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Completion síncrono. Bloqueia até resposta completa."""
        ...

    @abstractmethod
    def stream(self, request: CompletionRequest) -> Iterator[str]:
        """Streaming. Yield de chunks de texto."""
        ...

    @abstractmethod
    def validate(self) -> bool:
        """Testa credenciais. True se OK, False ou exception se falhou."""
        ...

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """
        Estimativa de custo em USD.
        Default: 0.0 (providers locais não cobram).
        Override em providers comerciais.
        """
        return 0.0

    def list_models(self) -> List[str]:
        """Lista de modelos disponíveis. Default: apenas o configurado."""
        return [self.config.model]

    def __repr__(self) -> str:
        return f"<{type(self).__name__} model={self.config.model}>"


# ── Mask de credenciais para logs ────────────────────────────────────────────

def mask_key(key: str) -> str:
    """Mascara API key para logs (mostra 4 primeiros + 4 últimos chars)."""
    if not key or len(key) < 12:
        return "***"
    return f"{key[:4]}...{key[-4:]}"
