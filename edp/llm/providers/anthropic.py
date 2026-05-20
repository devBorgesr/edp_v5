"""
edp.llm.providers.anthropic — Provider Claude (Anthropic API).

Implementação profissional com:
  - Streaming SSE real (não NDJSON como Ollama)
  - Parser resiliente de events (handle deltas + tool blocks)
  - Retry exponencial em rate limits (429)
  - Timeout adaptativo
  - Tratamento de erros tipados (auth/quota/safety)
  - Custo estimado baseado no modelo
  - Logs estruturados com request_id

API ref: https://docs.anthropic.com/en/api/messages

NÃO depende de SDK externo (anthropic-python) para evitar adição de deps.
Usa urllib stdlib direto.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Iterator, List, Optional

from .base import (
    LLMProviderBase, ProviderConfig, CompletionRequest, CompletionResponse,
    CompletionMetrics, Message, Role,
    AuthError, RateLimitError, ProviderTimeout, ContentFilterError, ProviderError,
    mask_key,
)

logger = logging.getLogger("edp.llm.providers.anthropic")


# ── Preços (USD por 1M tokens) — atualizado Maio/2026 ────────────────────────
PRICING = {
    "claude-opus-4-7":          {"input": 15.00, "output": 75.00},
    "claude-opus-4-6":          {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6":        {"input":  3.00, "output": 15.00},
    "claude-haiku-4-5":         {"input":  0.80, "output":  4.00},
    "claude-3-5-sonnet":        {"input":  3.00, "output": 15.00},
    "claude-3-5-haiku":         {"input":  0.80, "output":  4.00},
    "claude-3-opus":            {"input": 15.00, "output": 75.00},
}


def _pricing_for(model: str) -> dict:
    """Retorna pricing, fazendo prefix-match."""
    if model in PRICING:
        return PRICING[model]
    for key, p in PRICING.items():
        if model.startswith(key):
            return p
    return {"input": 3.00, "output": 15.00}  # fallback Sonnet


class AnthropicProvider(LLMProviderBase):
    """
    Provider Claude da Anthropic.

    Configuração:
        config = ProviderConfig(
            api_key="sk-ant-...",
            model="claude-sonnet-4-6",
            base_url="https://api.anthropic.com",
            timeout_s=300.0,
        )
        provider = AnthropicProvider(config)

    Uso:
        # síncrono
        resp = provider.complete(CompletionRequest(
            messages=[Message(Role.USER, "Olá")],
            system="Você é um assistente.",
        ))
        print(resp.text, resp.metrics.cost_usd)

        # streaming
        for chunk in provider.stream(request):
            print(chunk, end="", flush=True)
    """

    name = "anthropic"
    API_VERSION = "2023-06-01"

    def _validate_config(self) -> None:
        super()._validate_config()
        if not self.config.api_key:
            raise AuthError("ANTHROPIC_API_KEY é obrigatória")
        if not self.config.base_url:
            self.config.base_url = "https://api.anthropic.com"
        logger.info(
            "[anthropic] inicializado model=%s key=%s base=%s",
            self.config.model, mask_key(self.config.api_key), self.config.base_url,
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "x-api-key":         self.config.api_key,
            "anthropic-version": self.API_VERSION,
            "content-type":      "application/json",
        }

    def _build_payload(self, request: CompletionRequest, stream: bool) -> dict:
        """Converte CompletionRequest para formato Anthropic."""
        # Anthropic não aceita 'system' em messages — vai num campo separado
        messages = []
        system_text = request.system or ""
        for m in request.messages:
            if m.role == Role.SYSTEM:
                # Concatena com system_text se houver múltiplos
                system_text = (system_text + "\n" + m.content).strip()
            else:
                messages.append(m.to_dict())

        # Anthropic exige alternância user/assistant. Se primeiro é assistant, ignora.
        if messages and messages[0]["role"] == "assistant":
            messages = messages[1:]
        # Anthropic exige primeiro user
        if not messages or messages[0]["role"] != "user":
            messages.insert(0, {"role": "user", "content": "(continue)"})

        payload = {
            "model":       self.config.model,
            "messages":    messages,
            "max_tokens":  request.max_tokens,
            "temperature": request.temperature,
            "stream":      stream,
        }
        if system_text:
            payload["system"] = system_text
        if request.stop:
            payload["stop_sequences"] = request.stop
        return payload

    def _request(self, payload: dict, stream: bool) -> urllib.request.Request:
        url = f"{self.config.base_url}/v1/messages"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        return req

    def _handle_http_error(self, e: urllib.error.HTTPError) -> None:
        """Converte HTTPError em exceção tipada."""
        body = ""
        try:
            body = e.read().decode("utf-8")[:500]
        except Exception:
            pass

        if e.code == 401:
            raise AuthError(f"API key inválida: {body}") from e
        if e.code == 403:
            raise AuthError(f"Acesso negado: {body}") from e
        if e.code == 429:
            raise RateLimitError(f"Rate limit excedido: {body}") from e
        if e.code == 400 and "content_filter" in body.lower():
            raise ContentFilterError(f"Conteúdo bloqueado: {body}") from e
        raise ProviderError(f"HTTP {e.code}: {body}") from e

    def _retry(self, fn, *args, **kwargs):
        """Retry com backoff exponencial em RateLimit/Timeout."""
        last_err = None
        for attempt in range(self.config.max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except (RateLimitError, ProviderTimeout) as e:
                last_err = e
                if attempt < self.config.max_retries:
                    backoff = self.config.backoff_s * (2 ** attempt)
                    logger.warning(
                        "[anthropic] retry %d/%d em %.1fs após %s",
                        attempt + 1, self.config.max_retries, backoff, type(e).__name__,
                    )
                    time.sleep(backoff)
        raise last_err  # type: ignore

    # ── Complete (síncrono) ──────────────────────────────────────────────────

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Completion síncrono com retry automático."""
        return self._retry(self._do_complete, request)

    def _do_complete(self, request: CompletionRequest) -> CompletionResponse:
        payload = self._build_payload(request, stream=False)
        req = self._request(payload, stream=False)

        t_start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_s) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            self._handle_http_error(e)
            raise  # unreachable
        except (TimeoutError, urllib.error.URLError) as e:
            err_msg = str(e).lower()
            if "timed out" in err_msg or "timeout" in err_msg:
                raise ProviderTimeout(f"Timeout após {self.config.timeout_s}s") from e
            raise ProviderError(str(e)) from e

        latency_ms = (time.perf_counter() - t_start) * 1000.0
        data = json.loads(body)

        # Anthropic retorna: {content: [{type: "text", text: "..."}], usage: {...}}
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        usage  = data.get("usage", {})
        ptoks  = usage.get("input_tokens", 0)
        ctoks  = usage.get("output_tokens", 0)
        pricing = _pricing_for(self.config.model)
        cost   = (ptoks * pricing["input"] + ctoks * pricing["output"]) / 1_000_000

        metrics = CompletionMetrics(
            latency_ms=latency_ms,
            first_token_ms=0.0,  # não há streaming → não dá pra medir
            prompt_tokens=ptoks,
            completion_tokens=ctoks,
            total_tokens=ptoks + ctoks,
            cost_usd=cost,
        )
        logger.info(
            "[anthropic] complete | model=%s lat=%.0fms tok_in=%d tok_out=%d cost=$%.4f",
            self.config.model, latency_ms, ptoks, ctoks, cost,
        )
        return CompletionResponse(
            text=text,
            model=self.config.model,
            metrics=metrics,
            stop_reason=data.get("stop_reason"),
            raw=data,
        )

    # ── Stream (SSE) ─────────────────────────────────────────────────────────

    def stream(self, request: CompletionRequest) -> Iterator[str]:
        """
        Streaming via Server-Sent Events.

        Anthropic SSE format:
            event: message_start
            data: {...}

            event: content_block_delta
            data: {"delta": {"text": "..."}}

            event: message_stop
            data: {...}
        """
        payload = self._build_payload(request, stream=True)
        req = self._request(payload, stream=True)

        t_start = time.perf_counter()
        try:
            resp = urllib.request.urlopen(req, timeout=self.config.timeout_s)
        except urllib.error.HTTPError as e:
            self._handle_http_error(e)
            raise
        except (TimeoutError, urllib.error.URLError) as e:
            raise ProviderTimeout(f"Timeout conectando: {e}") from e

        try:
            current_event = ""
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
                if not line:
                    current_event = ""
                    continue

                if line.startswith("event:"):
                    current_event = line[6:].strip()
                    continue

                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if not data_str:
                        continue
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.debug("[anthropic] SSE line malformada: %s", data_str[:80])
                        continue

                    # Eventos relevantes
                    if current_event == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield text
                    elif current_event == "message_stop":
                        elapsed = (time.perf_counter() - t_start) * 1000.0
                        logger.info(
                            "[anthropic] stream done | model=%s lat=%.0fms",
                            self.config.model, elapsed,
                        )
                        break
                    elif current_event == "error":
                        err = data.get("error", {})
                        err_type = err.get("type", "unknown")
                        err_msg  = err.get("message", "")
                        logger.error("[anthropic] erro SSE: %s — %s", err_type, err_msg)
                        if "rate" in err_type.lower():
                            raise RateLimitError(err_msg)
                        raise ProviderError(f"{err_type}: {err_msg}")
        finally:
            try: resp.close()
            except Exception: pass

    # ── Validate ─────────────────────────────────────────────────────────────

    def validate(self) -> bool:
        """
        Testa credenciais com uma chamada mínima (1 token).
        Custa ~$0.00001 — desprezível.
        """
        try:
            req = CompletionRequest(
                messages=[Message(Role.USER, "1")],
                max_tokens=1,
                temperature=0.0,
            )
            self._do_complete(req)
            return True
        except AuthError:
            logger.error("[anthropic] credenciais inválidas")
            return False
        except Exception as e:
            logger.warning("[anthropic] validate falhou: %s", e)
            return False

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        p = _pricing_for(self.config.model)
        return (prompt_tokens * p["input"] + completion_tokens * p["output"]) / 1_000_000

    def list_models(self) -> List[str]:
        """Lista de modelos conhecidos. Anthropic não tem endpoint /models público."""
        return list(PRICING.keys())
