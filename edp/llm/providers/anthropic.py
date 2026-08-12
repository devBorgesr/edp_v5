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
    # Atualizado 12/06/2026 conforme docs oficiais (claude.com/pricing).
    # Correções: opus-4-7 era 15/75 (preço do Opus 4.1/4 antigos — o real
    # é 5/25); haiku-4-5 era 0.80/4.00 (preço do haiku-3.5 — o real é 1/5).
    "claude-fable-5":           {"input": 10.00, "output": 50.00},
    "claude-opus-4-8":          {"input":  5.00, "output": 25.00},
    "claude-opus-4-7":          {"input":  5.00, "output": 25.00},
    "claude-opus-4-6":          {"input":  5.00, "output": 25.00},
    "claude-sonnet-4-6":        {"input":  3.00, "output": 15.00},
    "claude-haiku-4-5":         {"input":  1.00, "output":  5.00},
    "claude-3-5-sonnet":        {"input":  3.00, "output": 15.00},
    "claude-3-5-haiku":         {"input":  0.80, "output":  4.00},
    "claude-3-opus":            {"input": 15.00, "output": 75.00},
}


# ── Modelos que rejeitam parâmetro `temperature` (dívida #11) ────────────────
# Anthropic descontinuou `temperature` em Opus 4.7+ (HTTP 400 retorna
# "temperature is deprecated for this model"). Mantém lista explícita: nova
# versão que rejeitar precisa ser adicionada aqui. Alternativa avaliada foi
# retry-on-error, mas adiciona latência a cada chamada — manter lista é
# mais simples até virar problema.
MODELS_REJECTING_TEMPERATURE = {
    # Doc oficial (12/06/2026): temperature/top_p/top_k retornam HTTP 400
    # em Opus 4.7 E POSTERIORES — inclui 4.8 e a família Mythos/Fable.
    # Sem estas entradas, a 1ª escalada da câmara p/ esses juízes
    # morreria em 400 → fallback (bug latente prevenido).
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-fable-5",
    "claude-mythos-5",
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

        # Dívida #47 (12/06/2026): modelo efetivo = override por request
        # (câmara de eco) ou o modelo do config (caminho normal).
        eff_model = request.model or self.config.model
        payload = {
            "model":       eff_model,
            "messages":    messages,
            "max_tokens":  request.max_tokens,
            "stream":      stream,
        }
        # Dívida #11 (2026-05-27): Opus 4.7+ rejeita `temperature`.
        # Omite o parâmetro para modelos listados em MODELS_REJECTING_TEMPERATURE.
        # #47: o gate usa o modelo EFETIVO — um override p/ Opus a partir de
        # config Haiku deve omitir temperature também.
        if eff_model not in MODELS_REJECTING_TEMPERATURE:
            payload["temperature"] = request.temperature
        if system_text:
            payload["system"] = system_text
        if request.stop:
            payload["stop_sequences"] = request.stop
        return payload

    @staticmethod
    def _medir_prompt(payload: dict) -> tuple[int, int, int, str]:
        """
        Mede o prompt já montado, para a Fase 1 da calibração de tokens
        (lab_edp_novo/docs/sujeito_edp/AUDITORIA_FASE1_TOKENS.md).

        Mede o PAYLOAD, não o `CompletionRequest`: `_build_payload` move
        mensagens `system` para o campo `system`, injeta `"(continue)"` quando
        falta um primeiro turno de user, e descarta um primeiro `assistant`.
        Medir antes disso contaria caracteres que não foram enviados e deixaria
        de contar os que foram — a razão sairia enviesada com cara de medida.

        Devolve (text_chars, system_chars, n_messages, texto) — `texto` é o
        mesmo conteúdo concatenado, reaproveitado para a classificação de
        conteúdo, para não percorrer o prompt duas vezes.
        """
        system_text = payload.get("system") or ""
        mensagens = payload.get("messages") or []
        partes = [system_text] if system_text else []
        for m in mensagens:
            c = m.get("content")
            if isinstance(c, str):
                if c:
                    partes.append(c)
            elif isinstance(c, list):
                # formato multimodal: [{"type": "text", "text": "..."}, ...]
                for b in c:
                    if isinstance(b, dict) and b.get("text"):
                        partes.append(str(b["text"]))
        # Parte vazia é OMITIDA, não juntada: um bloco sem texto (imagem, por
        # exemplo) somaria um "\n" fantasma ao total. Um char por bloco
        # não-textual é pequeno e SISTEMÁTICO — exatamente o tipo de viés que
        # se esconde numa razão com cara de medida.
        texto = "\n".join(partes)
        return len(texto), len(system_text), len(mensagens), texto

    def _telemetria_tokens(
        self, payload: dict, req: urllib.request.Request,
        usage: Optional[dict], modo: str, eff_model: str,
    ) -> None:
        """
        Fase 1: emite o par (chars, tokens reais). Nunca propaga exceção e
        nunca altera a resposta — ver EDP_TOKEN_TELEMETRY em config.py.

        O gate da flag fica AQUI, antes de medir: com a flag OFF o caminho é
        um `if` e mais nada — nenhuma string é construída, nenhum prompt é
        percorrido. `emit_token_usage` re-checa a flag como rede de segurança,
        não como gate primário.
        """
        try:
            from ...config import EDP_TOKEN_TELEMETRY
            if not EDP_TOKEN_TELEMETRY:
                return
            from ...runtime.pareto_store import emit_token_usage
            text_chars, system_chars, n_msgs, texto = self._medir_prompt(payload)
            emit_token_usage(
                model=eff_model,
                modo=modo,
                usage=usage,
                text_chars=text_chars,
                system_chars=system_chars,
                payload_bytes=len(req.data or b""),
                n_messages=n_msgs,
                amostra_texto=texto,
            )
        except Exception as e:
            logger.debug("[anthropic] telemetria de tokens falhou: %s", e)

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

    def _do_complete(self, request: CompletionRequest,
                     telemetria: bool = True) -> CompletionResponse:
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
        # #47: custo/log/response refletem o modelo EFETIVO da chamada
        eff_model = request.model or self.config.model
        pricing = _pricing_for(eff_model)
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
            eff_model, latency_ms, ptoks, ctoks, cost,
        )
        # Fase 1 (12/08/2026): `usage` cru, não `ptoks`/`ctoks` — os extraídos
        # já têm default 0, que apagaria a distinção entre "veio 0" e "não veio".
        if telemetria:
            self._telemetria_tokens(payload, req, usage, "complete", eff_model)
        return CompletionResponse(
            text=text,
            model=eff_model,
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
            data: {... "message": {"usage": {...}}}

            event: content_block_delta
            data: {"delta": {"text": "..."}}

            event: message_delta
            data: {"delta": {"stop_reason": "end_turn|max_tokens|...",
                             "stop_sequence": null},
                   "usage": {"output_tokens": N}}

            event: message_stop
            data: {...}

        Diagnóstico (2026-05-31): captura stop_reason e usage para investigar
        cortes em Opus 4.7 Tier 1. Cortes observados ~1100 tokens (Java 21
        sectioned) sem 429 nos logs. Hipóteses: max_tokens implícito do tier,
        end_turn precoce, ou bug no parser SSE.
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

        # ── Captura de headers de rate limit (informação "golden") ──────────
        # Anthropic retorna em cada resposta:
        #   anthropic-ratelimit-output-tokens-remaining
        #   anthropic-ratelimit-output-tokens-limit
        #   anthropic-ratelimit-output-tokens-reset
        # Headers ficam disponíveis ANTES do stream começar a fluir.
        try:
            _rl_out_rem   = resp.headers.get("anthropic-ratelimit-output-tokens-remaining")
            _rl_out_limit = resp.headers.get("anthropic-ratelimit-output-tokens-limit")
            _rl_out_reset = resp.headers.get("anthropic-ratelimit-output-tokens-reset")
            _rl_req_rem   = resp.headers.get("anthropic-ratelimit-requests-remaining")
            if _rl_out_rem or _rl_out_limit:
                logger.info(
                    "[anthropic] rate_limit headers | model=%s | "
                    "out_remaining=%s out_limit=%s out_reset=%s req_remaining=%s",
                    self.config.model, _rl_out_rem, _rl_out_limit,
                    _rl_out_reset, _rl_req_rem,
                )
        except Exception as e:
            logger.debug("[anthropic] falha ao ler headers de rate limit: %s", e)

        # Estado capturado durante o stream para diagnóstico
        _stop_reason = None
        _output_tokens_reported = None
        _input_tokens_reported = None
        # Fase 1 (12/08/2026): os dois `usage` CRUS. No streaming o objeto vem
        # partido em dois eventos SSE — `message_start` traz input_tokens (e os
        # campos de cache, se prompt caching estiver ligado) e `message_delta`
        # traz output_tokens. Guardar os dicts inteiros, em vez de só os dois
        # inteiros acima, é o que mantém amostras cacheadas separáveis das
        # limpas no futuro (ver emit_token_usage).
        _usage_start: Optional[dict] = None
        _usage_delta: Optional[dict] = None

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
                    elif current_event == "message_start":
                        # message_start carrega usage.input_tokens
                        try:
                            _usage_start = (
                                data.get("message", {}).get("usage") or None
                            )
                            _input_tokens_reported = (
                                (_usage_start or {}).get("input_tokens")
                            )
                        except Exception:
                            pass
                    elif current_event == "message_delta":
                        # message_delta carrega stop_reason e output_tokens finais
                        try:
                            _delta = data.get("delta", {})
                            _stop_reason = _delta.get("stop_reason")
                            _usage = data.get("usage", {})
                            _usage_delta = _usage or None
                            _output_tokens_reported = _usage.get("output_tokens")
                        except Exception:
                            pass
                    elif current_event == "message_stop":
                        elapsed = (time.perf_counter() - t_start) * 1000.0
                        logger.info(
                            "[anthropic] stream done | model=%s lat=%.0fms | "
                            "stop_reason=%s | tok_in=%s tok_out=%s",
                            self.config.model, elapsed,
                            _stop_reason or "(não reportado)",
                            _input_tokens_reported or "?",
                            _output_tokens_reported or "?",
                        )
                        # Sinal explícito quando stop_reason é max_tokens
                        if _stop_reason == "max_tokens":
                            logger.warning(
                                "[anthropic] CORTE POR MAX_TOKENS | model=%s "
                                "output_tokens=%s — modelo atingiu limite de saída",
                                self.config.model, _output_tokens_reported,
                            )
                        # Fase 1 (12/08/2026): emite só aqui, no message_stop.
                        # Antes disso o `usage` está incompleto por construção
                        # (output_tokens só chega no message_delta), e stream
                        # abandonado pelo consumidor nunca chega aqui — que é o
                        # comportamento certo: amostra parcial é descartada,
                        # não completada com zero.
                        self._telemetria_tokens(
                            payload, req,
                            {**(_usage_start or {}), **(_usage_delta or {})},
                            "stream",
                            payload.get("model") or self.config.model,
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
            # Fase 1: `telemetria=False` de propósito. Esta chamada existe para
            # testar credencial — prompt "1", max_tokens=1 — e não é um turno.
            # Uma amostra de ~1 token entraria no dataset como se fosse uso
            # real e puxaria a razão chars/token do estrato inteiro, porque em
            # prompt minúsculo o andaime JSON domina (medido no smoke: 379
            # bytes de fio para 194 chars de texto).
            self._do_complete(req, telemetria=False)
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
