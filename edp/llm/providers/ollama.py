"""
edp.llm.providers.ollama — Provider Ollama via interface unificada.

Implementação que reusa o `LLMClient` existente em llm_adapter.py.
Não duplica código, não quebra nada — apenas adapta para LLMProviderBase.

Compatibilidade total: o EDP atual continua usando llm_adapter diretamente.
Esta é uma camada paralela para quem quer usar a API unificada.
"""
from __future__ import annotations

import logging
import time
from typing import Iterator, List

from .base import (
    LLMProviderBase, ProviderConfig, CompletionRequest, CompletionResponse,
    CompletionMetrics, Message, Role, ProviderError, ProviderTimeout,
)

logger = logging.getLogger("edp.llm.providers.ollama")


class OllamaProvider(LLMProviderBase):
    """
    Provider Ollama unificado.

    Internamente usa LLMClient (existente) para não duplicar lógica HTTP.
    """

    name = "ollama"

    def _validate_config(self) -> None:
        super()._validate_config()
        if not self.config.base_url:
            self.config.base_url = "http://localhost:11434"

    def _build_legacy_client(self):
        """Constrói LLMClient legado on-demand."""
        from ...llm_adapter import LLMClient, LLMConfig, LLMProvider as LegacyProvider
        legacy_config = LLMConfig(
            provider=LegacyProvider.OLLAMA,
            model=self.config.model,
            base_url=self.config.base_url,
            timeout_s=self.config.timeout_s,
            temperature=self.config.extra.get("temperature", 0.7),
            max_tokens=self.config.extra.get("max_tokens", 2048),
        )
        return LLMClient(legacy_config)

    def _messages_to_prompt(self, request: CompletionRequest) -> tuple:
        """Converte messages para (prompt, system) — Ollama usa formato simples."""
        system = request.system or ""
        prompt_parts = []
        for m in request.messages:
            if m.role == Role.SYSTEM:
                system = (system + "\n" + m.content).strip()
            elif m.role == Role.USER:
                prompt_parts.append(m.content)
            elif m.role == Role.ASSISTANT:
                prompt_parts.append(f"[assistant: {m.content}]")
        return "\n".join(prompt_parts), system

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        client = self._build_legacy_client()
        prompt, system = self._messages_to_prompt(request)

        t_start = time.perf_counter()
        try:
            text = client.complete(prompt, system)
        except Exception as e:
            err_msg = str(e).lower()
            if "timeout" in err_msg or "timed out" in err_msg:
                raise ProviderTimeout(str(e)) from e
            raise ProviderError(str(e)) from e

        latency_ms = (time.perf_counter() - t_start) * 1000.0
        tokens_est = len(text.split())
        return CompletionResponse(
            text=text,
            model=self.config.model,
            metrics=CompletionMetrics(
                latency_ms=latency_ms,
                first_token_ms=0.0,
                completion_tokens=tokens_est,
                total_tokens=tokens_est,
                cost_usd=0.0,
            ),
        )

    def stream(self, request: CompletionRequest) -> Iterator[str]:
        client = self._build_legacy_client()
        prompt, system = self._messages_to_prompt(request)
        try:
            yield from client.stream(prompt, system)
        except Exception as e:
            err_msg = str(e).lower()
            if "timeout" in err_msg or "timed out" in err_msg:
                raise ProviderTimeout(str(e)) from e
            raise ProviderError(str(e)) from e

    def validate(self) -> bool:
        """Verifica se Ollama está rodando e modelo existe."""
        try:
            import urllib.request, json
            url = f"{self.config.base_url}/api/tags"
            with urllib.request.urlopen(url, timeout=5) as r:
                data = json.loads(r.read().decode())
            models = [m.get("name", "") for m in data.get("models", [])]
            ok = self.config.model in models
            if not ok:
                logger.warning(
                    "[ollama] modelo %s não está disponível. Disponíveis: %s",
                    self.config.model, models,
                )
            return ok
        except Exception as e:
            logger.warning("[ollama] validate falhou: %s", e)
            return False

    def list_models(self) -> List[str]:
        try:
            import urllib.request, json
            url = f"{self.config.base_url}/api/tags"
            with urllib.request.urlopen(url, timeout=5) as r:
                data = json.loads(r.read().decode())
            return [m.get("name", "") for m in data.get("models", [])]
        except Exception:
            return [self.config.model]
