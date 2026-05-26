"""
llm_adapter.py — EDP v3.3 LLM Adapter

Integra o EDP como camada cognitiva persistente sobre LLMs locais e cloud.

Provedores suportados:
  ollama     → http://localhost:11434  (Llama3, Mistral, Phi3, etc.)
  lm_studio  → http://localhost:1234   (qualquer modelo GGUF)
  openai     → https://api.openai.com  (GPT-4, etc.)
  custom     → qualquer endpoint OpenAI-compatible

Fluxo:
  user query
  → EDP.retrieve(context)     ← memória relevante
  → EDP.compress(context)     ← reduz tokens
  → LLM.complete(prompt)      ← modelo local/cloud
  → EDP.store(response)       ← aprende com resposta
  → user response

Uso:
  from edp.llm_adapter import EDPRuntime, LLMProvider

  runtime = EDPRuntime(session_id="minha_sessao")
  runtime.connect_ollama(model="llama3:8b")
  
  response = runtime.chat("Como funciona RAG?")
  print(response.text)
  print(f"Contexto usado: {response.tokens_context}/{response.tokens_total}")
"""
from __future__ import annotations

import json
import logging
import threading
import time
import os
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from enum import Enum
from typing import Generator, List, Optional

from .clock import now as _now  # Peça 0.2a — relógio interno robusto

logger = logging.getLogger("edp.llm_adapter")


def _format_relative_time(ts: float, now: float) -> str:
    """
    Formata timestamp em texto relativo curto (pt-BR).

    Resoluções:
      < 1 min     → "agora"
      < 1 hora    → "há Xmin"
      mesmo dia   → "há Xh"
      ontem       → "ontem"
      < 7 dias    → "há X dias"
      < 30 dias   → "há X semanas"
      >= 30 dias  → "há X meses"

    Robusto: timestamps inválidos retornam "".
    """
    try:
        delta = now - float(ts)
        if delta < 0:
            return ""
        if delta < 60:
            return "agora"
        if delta < 3600:
            return f"há {int(delta // 60)}min"
        if delta < 86400:
            return f"há {int(delta // 3600)}h"
        if delta < 172800:
            return "ontem"
        if delta < 604800:
            return f"há {int(delta // 86400)} dias"
        if delta < 2592000:
            return f"há {int(delta // 604800)} semanas"
        return f"há {int(delta // 2592000)} meses"
    except Exception:
        return ""


# ── Enums ──────────────────────────────────────────────────────────────────────

class LLMProvider(str, Enum):
    OLLAMA    = "ollama"
    LM_STUDIO = "lm_studio"
    OPENAI    = "openai"
    ANTHROPIC = "anthropic"
    CUSTOM    = "custom"


# ── Tipos ─────────────────────────────────────────────────────────────────────

@dataclass
class LLMConfig:
    provider:    LLMProvider = LLMProvider.OLLAMA
    model:       str         = "llama3:8b"
    base_url:    str         = "http://localhost:11434"
    api_key:     str         = ""
    timeout_s:   float       = 300.0
    max_retries: int         = 3
    temperature: float       = 0.7
    max_tokens:  int         = 2048
    stream:      bool        = False


@dataclass
class ChatResponse:
    text:             str
    model:            str
    session_id:       str
    tokens_context:   int   = 0   # tokens do contexto EDP injetado
    tokens_prompt:    int   = 0   # tokens totais do prompt
    tokens_generated: int   = 0   # tokens gerados pelo modelo
    latency_ms:       float = 0.0
    memory_hits:      int   = 0   # entradas de memória recuperadas
    compression_pct:  float = 0.0 # redução aplicada pelo pipeline
    mode:             str   = "normal"


@dataclass
class LLMMetrics:
    total_requests:    int   = 0
    total_errors:      int   = 0
    total_timeouts:    int   = 0
    avg_latency_ms:    float = 0.0
    avg_tokens_gen:    float = 0.0
    avg_memory_hits:   float = 0.0
    avg_first_token_ms: float = 0.0
    avg_throughput_tps: float = 0.0   # tokens por segundo
    last_latency_ms:   float = 0.0
    last_tokens:       int   = 0
    _EMA: float = 0.2

    def record(self, ms: float, tokens: int, hits: int,
               first_token_ms: float = 0.0) -> None:
        """Registra métrica de turno completo (chat ou stream)."""
        self.total_requests += 1
        self.last_latency_ms = ms
        self.last_tokens     = tokens
        self.avg_latency_ms  = self._EMA * ms    + (1 - self._EMA) * self.avg_latency_ms
        self.avg_tokens_gen  = self._EMA * tokens + (1 - self._EMA) * self.avg_tokens_gen
        self.avg_memory_hits = self._EMA * hits   + (1 - self._EMA) * self.avg_memory_hits
        if first_token_ms > 0:
            self.avg_first_token_ms = (
                self._EMA * first_token_ms +
                (1 - self._EMA) * self.avg_first_token_ms
            )
        # Throughput: tokens/s baseado em (ms - first_token_ms) = tempo de geração real
        if tokens > 0 and ms > first_token_ms > 0:
            gen_time_s = (ms - first_token_ms) / 1000.0
            if gen_time_s > 0:
                tps = tokens / gen_time_s
                self.avg_throughput_tps = (
                    self._EMA * tps +
                    (1 - self._EMA) * self.avg_throughput_tps
                )

    def record_error(self, is_timeout: bool = False) -> None:
        self.total_errors += 1
        if is_timeout:
            self.total_timeouts += 1


# ── LLM Client ────────────────────────────────────────────────────────────────

class LLMClient:
    """
    Cliente HTTP leve para LLMs locais.
    Sem dependências externas (usa urllib padrão).
    Suporta streaming via server-sent events.

    [v3.6] Quando provider == ANTHROPIC, delega para edp.llm.providers.anthropic
    (provider profissional já implementado com SSE real, retry, pricing).
    Resto do código (Ollama/OpenAI) permanece intacto.
    """

    def __init__(self, config: LLMConfig) -> None:
        self._cfg = config
        # Lazy-init de provider Anthropic (apenas quando necessário)
        self._anthropic_provider = None
        if config.provider == LLMProvider.ANTHROPIC:
            self._init_anthropic_provider()

    def _init_anthropic_provider(self):
        """Inicializa AnthropicProvider sob demanda."""
        if self._anthropic_provider is not None:
            return
        try:
            from .llm.providers import get_provider, ProviderConfig
            self._anthropic_provider = get_provider(
                name="anthropic",
                model=self._cfg.model,
                api_key=self._cfg.api_key,
                timeout_s=self._cfg.timeout_s,
            )
        except Exception as e:
            logger.error("[LLMClient] AnthropicProvider falhou: %s", e)
            raise

    def complete(self, prompt: str, system: str = "") -> str:
        """Completion síncrono. Retorna texto gerado."""
        # Anthropic via provider profissional
        if self._cfg.provider == LLMProvider.ANTHROPIC:
            from .llm.providers import CompletionRequest, Message, Role
            req = CompletionRequest(
                messages=[Message(Role.USER, prompt)],
                system=system,
                temperature=self._cfg.temperature,
                max_tokens=self._cfg.max_tokens,
            )
            resp = self._anthropic_provider.complete(req)
            return resp.text

        for attempt in range(self._cfg.max_retries):
            try:
                if self._cfg.provider == LLMProvider.OLLAMA:
                    return self._ollama_complete(prompt, system)
                else:
                    return self._openai_complete(prompt, system)
            except urllib.error.URLError as e:
                if attempt < self._cfg.max_retries - 1:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise RuntimeError(
                    f"LLM indisponível após {self._cfg.max_retries} tentativas: {e}"
                )
        return ""

    def stream(self, prompt: str, system: str = "") -> Generator[str, None, None]:
        """Streaming via generator. Yield de chunks de texto."""
        # Anthropic via provider profissional (SSE real)
        if self._cfg.provider == LLMProvider.ANTHROPIC:
            from .llm.providers import CompletionRequest, Message, Role
            req = CompletionRequest(
                messages=[Message(Role.USER, prompt)],
                system=system,
                temperature=self._cfg.temperature,
                max_tokens=self._cfg.max_tokens,
            )
            yield from self._anthropic_provider.stream(req)
            return

        if self._cfg.provider == LLMProvider.OLLAMA:
            yield from self._ollama_stream(prompt, system)
        else:
            yield from self._openai_stream(prompt, system)

    def is_available(self) -> bool:
        """Verifica se o servidor está disponível."""
        # Anthropic: validação via provider (chamada de 1 token ~$0.00001)
        if self._cfg.provider == LLMProvider.ANTHROPIC:
            try:
                return self._anthropic_provider.validate()
            except Exception as e:
                logger.warning("[LLMClient] Anthropic validate falhou: %s", e)
                return False
        try:
            url = f"{self._cfg.base_url}/api/tags" if "ollama" in self._cfg.provider else \
                  f"{self._cfg.base_url}/v1/models"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=3.0):
                return True
        except Exception:
            return False

    def list_models(self) -> List[str]:
        """Lista modelos disponíveis no servidor."""
        # Anthropic: usa lista hardcoded do provider
        if self._cfg.provider == LLMProvider.ANTHROPIC:
            try:
                return self._anthropic_provider.list_models()
            except Exception:
                return [self._cfg.model]
        try:
            if self._cfg.provider == LLMProvider.OLLAMA:
                url = f"{self._cfg.base_url}/api/tags"
                data = self._get_json(url)
                return [m["name"] for m in data.get("models", [])]
            else:
                url = f"{self._cfg.base_url}/v1/models"
                data = self._get_json(url)
                return [m["id"] for m in data.get("data", [])]
        except Exception:
            return []

    # ── Ollama ────────────────────────────────────────────────────────────────

    def _ollama_complete(self, prompt: str, system: str) -> str:
        url     = f"{self._cfg.base_url}/api/generate"
        payload = {
            "model":  self._cfg.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self._cfg.temperature,
                "num_predict": self._cfg.max_tokens,
            },
        }
        if system:
            payload["system"] = system

        data    = json.dumps(payload).encode()
        req     = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._cfg.timeout_s) as resp:
                result = json.loads(resp.read().decode())
            return result.get("response", "")
        except urllib.error.HTTPError as e:
            # Loga corpo da resposta para diagnóstico
            body = ""
            try: body = e.read().decode()[:300]
            except Exception: pass
            logger.error("[Ollama] HTTP %d em %s | model=%s | body=%s",
                         e.code, url, self._cfg.model, body)
            # 404 geralmente = modelo não existe ou nome errado
            if e.code == 404:
                raise RuntimeError(
                    f"Modelo '{self._cfg.model}' nao encontrado no Ollama. "
                    f"Tente: 'phi3:latest' ou 'llama3:8b'. Body: {body}"
                )
            raise

    def _ollama_stream(self, prompt: str, system: str) -> Generator[str, None, None]:
        url     = f"{self._cfg.base_url}/api/generate"
        payload = {
            "model":  self._cfg.model,
            "prompt": prompt,
            "stream": True,
            "options": {
                "temperature": self._cfg.temperature,
                "num_predict": self._cfg.max_tokens,
                "stop": [
                    "\nInstructions",
                    "\nInstruction:",
                    "**Instructions",
                    "\n\nInstructions",
                    "Incorporate the following",
                ],
            },
        }
        if system:
            payload["system"] = system

        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._cfg.timeout_s) as resp:
                for line in resp:
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line.decode())
                        token = chunk.get("response", "")
                        if token:
                            yield token
                        if chunk.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue
        except urllib.error.HTTPError as e:
            body = ""
            try: body = e.read().decode()[:300]
            except Exception: pass
            logger.error("[Ollama stream] HTTP %d em %s | model=%s | body=%s",
                         e.code, url, self._cfg.model, body)
            if e.code == 404:
                raise RuntimeError(
                    f"Modelo '{self._cfg.model}' nao encontrado. Use 'phi3:latest'."
                )
            raise

    # ── OpenAI-compatible (LM Studio, OpenAI) ────────────────────────────────

    def _openai_complete(self, prompt: str, system: str) -> str:
        url     = f"{self._cfg.base_url}/v1/chat/completions"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model":       self._cfg.model,
            "messages":    messages,
            "temperature": self._cfg.temperature,
            "max_tokens":  self._cfg.max_tokens,
            "stream":      False,
        }
        headers = {"Content-Type": "application/json"}
        if self._cfg.api_key:
            headers["Authorization"] = f"Bearer {self._cfg.api_key}"

        data = json.dumps(payload).encode()
        req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self._cfg.timeout_s) as resp:
            result = json.loads(resp.read().decode())
        return result["choices"][0]["message"]["content"]

    def _openai_stream(self, prompt: str, system: str) -> Generator[str, None, None]:
        url     = f"{self._cfg.base_url}/v1/chat/completions"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model":    self._cfg.model,
            "messages": messages,
            "stream":   True,
        }
        headers = {"Content-Type": "application/json"}
        if self._cfg.api_key:
            headers["Authorization"] = f"Bearer {self._cfg.api_key}"

        data = json.dumps(payload).encode()
        req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self._cfg.timeout_s) as resp:
            for line in resp:
                line = line.decode().strip()
                if not line.startswith("data: "):
                    continue
                chunk_str = line[6:]
                if chunk_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(chunk_str)
                    delta = chunk["choices"][0].get("delta", {})
                    token = delta.get("content", "")
                    if token:
                        yield token
                except (json.JSONDecodeError, KeyError):
                    continue

    def _get_json(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return json.loads(resp.read().decode())


# ── EDP Runtime ───────────────────────────────────────────────────────────────

class EDPRuntime:
    """
    Runtime cognitivo EDP integrado com LLM local.

    Fluxo de cada chat():
      1. pipeline.run_pipeline(text)          — comprime input
      2. memory.retrieve(query)               — recupera contexto relevante
      3. context_builder.build_context()      — monta contexto MMR
      4. llm.complete(prompt_com_contexto)    — chama modelo
      5. memory.add(response)                 — aprende com resposta
      6. cognitive_scheduler.evaluate()       — manutenção cognitiva

    Todos os subsistemas EDP ficam transparentes para o usuário.
    """

    SYSTEM_TEMPLATE = """Voce e um assistente conciso em portugues do Brasil.

Contexto da memoria (use SOMENTE se relevante para a pergunta atual):
{context}

VOCE TEM ACESSO A INFORMACAO TEMPORAL — IMPORTANTE:
- Cada memoria EM CONTEXTO comeca com tags entre colchetes
- TAGS DE TURNO RECENTE (PRIORIDADE MAXIMA):
  - "[turno anterior]"  = sua resposta IMEDIATAMENTE anterior nesta conversa
  - "[2 turnos atrás]"  = penúltimo Q/A desta conversa
- Tags de tempo (memorias mais antigas): "agora", "ha Xmin", "ha Xh",
  "ontem", "ha X dias", "ha X semanas", "ha X meses"
- Tags de tipo: "user_input", "llm_response", "meta_conversation",
  "external", "verified", "stale", "contested"

PRIORIDADE DE CONTEXTO — REGRA CRITICA:
- Quando o usuario usar pronomes ou referencias ("isso", "sua resposta",
  "o que voce disse", "qual a base", "quais conclusoes", "tirou", "falou"),
  ele se refere SEMPRE ao "[turno anterior]"
- NUNCA traga memorias antigas como resposta a follow-up
- Se o usuario perguntar "qual a base do seu talvez", busque o "talvez"
  no "[turno anterior]" — NAO em conversas de dias atras
- Memorias antigas servem para enriquecer, NAO para substituir o
  contexto imediato

QUANDO O USUARIO PERGUNTAR SOBRE HORARIO/RECENCIA/CONTINUIDADE:
- NAO diga "nao tenho acesso a horarios" — VOCE TEM
- NAO diga "nao tenho memoria entre sessoes" — VOCE TEM (via EDP)
- USE as tags entre colchetes para responder
- Exemplo correto: "Ontem voce me perguntou sobre X, ha 3 dias falamos de Y"
- Se nao houver tag temporal em uma memoria, diga "essa memoria nao tem
  data registrada" — mas NAO negue a capacidade em geral
- Memorias "meta_conversation" antigas podem ser ignoradas se nao forem
  relevantes para a pergunta atual

QUANDO O USUARIO CITAR UMA MEMORIA OU PERGUNTAR SOBRE O CONTEXTO DELA:
- O contexto em [Contexto da memoria] traz no maximo 200-600 caracteres
  por memoria — eh um SNIPPET, nao o texto completo
- Se o usuario citar uma memoria especifica e voce so ve um fragmento,
  diga claramente: "tenho apenas um fragmento dessa memoria, nao o texto
  completo" — NAO finja ter contexto que nao tem
- NAO invente expansao de siglas que o usuario nao confirmou
  (ex: "EDP" eh sigla do usuario; NAO invente "Estrutura de Padrao
  Dinamico" ou similar sem confirmacao)
- NAO inferir conclusoes que voce nao consegue ler diretamente no texto
- Se o usuario perguntar "voce tem o contexto?" e voce so tem fragmento,
  responda HONESTAMENTE: "tenho a tag/snippet mas o texto completo foi
  truncado" — depois ofereca responder com base no que tem

REGRAS ABSOLUTAS:
- Responda APENAS em portugues do Brasil
- Responda APENAS o que foi perguntado, NADA alem
- Se o usuario pediu "1 equacao", de APENAS 1 (nao 5, nao 10)
- Se o usuario pediu "em 20 palavras", respeite o limite
- NAO invente nomes de teoremas, autores ou formulas
- Se nao tem certeza de uma formula tecnica especifica, diga
  "Nao tenho certeza desta formula especifica" em vez de inventar
- Se nao sabe a resposta, diga "Nao sei" e PARE
- NAO escreva instrucoes adicionais apos sua resposta
- Termine sua resposta com ponto final e PARE"""

    def __init__(
        self,
        session_id: str       = "default",
        llm_config: Optional[LLMConfig] = None,
        max_context_tokens: int = 800,
        auto_compress:      bool = True,
    ) -> None:
        self.session_id          = session_id
        self._llm_config         = llm_config
        self._max_ctx_tokens     = max_context_tokens
        self._auto_compress      = auto_compress
        self._client: Optional[LLMClient] = None
        self._lock               = threading.Lock()
        self._metrics            = LLMMetrics()
        self._history:           List[dict] = []

        # Subsistemas EDP (lazy import para não crashar se módulo ausente)
        self._memory     = None
        self._pipeline   = None
        self._ctx_builder = None
        self._co_occurrence = None  # PR2 v3.13.6

        self._init_edp_subsystems()

    # ── Conexão com LLM ───────────────────────────────────────────────────────

    def connect_ollama(
        self,
        model:    str   = "llama3:8b",
        base_url: str   = "http://localhost:11434",
        **kwargs,
    ) -> bool:
        """Conecta ao Ollama. Retorna True se disponível."""
        cfg = LLMConfig(
            provider=LLMProvider.OLLAMA,
            model=model, base_url=base_url, **kwargs
        )
        return self._connect(cfg)

    def connect_lm_studio(
        self,
        model:    str   = "local-model",
        base_url: str   = "http://localhost:1234",
        **kwargs,
    ) -> bool:
        """Conecta ao LM Studio."""
        cfg = LLMConfig(
            provider=LLMProvider.LM_STUDIO,
            model=model, base_url=base_url, **kwargs
        )
        return self._connect(cfg)

    def connect_openai(
        self,
        api_key:  str,
        model:    str = "gpt-4o-mini",
        **kwargs,
    ) -> bool:
        """Conecta à API OpenAI."""
        cfg = LLMConfig(
            provider=LLMProvider.OPENAI,
            model=model,
            base_url="https://api.openai.com",
            api_key=api_key, **kwargs
        )
        return self._connect(cfg)

    def connect_anthropic(
        self,
        api_key:  str,
        model:    str = "claude-haiku-4-5",
        **kwargs,
    ) -> bool:
        """
        Conecta à API Anthropic Claude.

        Modelos recomendados:
          - claude-haiku-4-5 (default, ~US$ 0.80/M input, mais barato)
          - claude-sonnet-4-6 (qualidade alta, ~US$ 3/M input)
          - claude-opus-4-7 (raciocínio profundo, ~US$ 15/M input)
        """
        if not api_key:
            logger.error("[EDPRuntime] connect_anthropic: api_key obrigatória")
            return False
        cfg = LLMConfig(
            provider=LLMProvider.ANTHROPIC,
            model=model,
            base_url="https://api.anthropic.com",
            api_key=api_key,
            **kwargs,
        )
        return self._connect(cfg)

    def connect_custom(self, base_url: str, model: str, **kwargs) -> bool:
        """Conecta a qualquer endpoint OpenAI-compatible."""
        cfg = LLMConfig(
            provider=LLMProvider.CUSTOM,
            model=model, base_url=base_url, **kwargs
        )
        return self._connect(cfg)

    def _connect(self, cfg: LLMConfig) -> bool:
        client = LLMClient(cfg)
        if not client.is_available():
            logger.warning("[EDPRuntime] %s indisponível em %s", cfg.provider, cfg.base_url)
            return False
        with self._lock:
            self._client     = client
            self._llm_config = cfg
        logger.info("[EDPRuntime] conectado: %s model=%s", cfg.provider, cfg.model)
        return True

    # ── Interface principal ───────────────────────────────────────────────────

    def chat(
        self,
        user_message: str,
        system: str = "",
        store_to_memory: bool = True,
    ) -> ChatResponse:
        """
        Envia mensagem com contexto cognitivo EDP.
        Retorna ChatResponse com resposta + métricas cognitivas.

        Args:
            user_message: pergunta do usuário
            system: prompt de sistema (override do default)
            store_to_memory: se True (default), grava Q/A na memória episódica.
                Use False para chamadas internas do sistema (ex: gerar
                session_summary, validar memórias) que NÃO devem virar
                turnos visíveis na conversa do usuário.
        """
        t0 = time.perf_counter()

        if self._client is None:
            return ChatResponse(
                text="[EDP] Nenhum LLM conectado. Use connect_ollama() ou connect_lm_studio().",
                model="none", session_id=self.session_id,
            )

        # 1. Comprime input via pipeline EDP
        compressed_input, compression_pct, n_blocks = self._compress_input(user_message)

        # 2. Recupera contexto da memória EDP
        context_blocks, memory_hits = self._retrieve_context(user_message)

        # 3. Monta prompt com contexto
        context_str = "\n".join(context_blocks[:5]) if context_blocks else "Nenhum contexto disponível."
        sys_prompt  = system or self.SYSTEM_TEMPLATE.format(context=context_str)

        # 4. Chama LLM
        try:
            response_text = self._client.complete(compressed_input or user_message, sys_prompt)
        except Exception as e:
            logger.error("[EDPRuntime] LLM error: %s", e)
            response_text = f"[Erro ao chamar LLM: {e}]"
            self._metrics.total_errors += 1

        # 5. Armazena na memória EDP (opcional)
        # Hotfix v3.13.3: chamadas internas (session_summary, validações)
        # passam store_to_memory=False para não poluir histórico episódico.
        if store_to_memory and response_text and not response_text.startswith("[Erro"):
            self._store_to_memory(user_message, response_text)

        # 6. Atualiza histórico (in-memory, sem persistir como episódico)
        self._history.append({
            "role": "user",    "content": user_message,    "ts": _now()
        })
        self._history.append({
            "role": "assistant", "content": response_text, "ts": _now()
        })

        ms = (time.perf_counter() - t0) * 1000
        self._metrics.record(ms, len(response_text.split()), memory_hits)

        return ChatResponse(
            text=response_text,
            model=self._llm_config.model if self._llm_config else "unknown",
            session_id=self.session_id,
            tokens_context=sum(len(b.split()) for b in context_blocks),
            tokens_prompt=len((compressed_input or user_message).split()),
            tokens_generated=len(response_text.split()),
            latency_ms=round(ms, 1),
            memory_hits=memory_hits,
            compression_pct=compression_pct,
        )

    def stream_chat(self, user_message: str, system: str = "") -> Generator[str, None, None]:
        """
        Streaming de chat com instrumentação de métricas.

        Mede:
          - first_token_ms: tempo até receber 1º chunk (importante em CPU)
          - total_latency_ms: tempo total da resposta
          - tokens: contagem de chunks gerados
          - throughput_tps: tokens/segundo na fase de geração
          - memory_hits: quantidade de contexto recuperado

        Em caso de erro: registra total_errors. Em timeout: total_timeouts.
        """
        if self._client is None:
            yield "[EDP] Nenhum LLM conectado."
            return

        # ── Instrumentação ────────────────────────────────────────────────────
        t_start          = time.perf_counter()
        t_first_token    = 0.0
        chunk_count      = 0
        first_chunk_seen = False
        error_occurred   = False

        # ── Contexto: usa ContextWindowManager se habilitado, senão fallback ──
        use_ctx_mgr = os.environ.get("EDP_USE_CTX_MGR", "1") == "1"
        if use_ctx_mgr:
            try:
                sys_prompt, ctx_meta = self._build_enriched_context(
                    query=user_message,
                    system_prompt=system or self.SYSTEM_TEMPLATE,
                )
                memory_hits = ctx_meta.get("memory_hits", 0)
            except Exception as e:
                logger.warning("[stream_chat] context_manager falhou, usando fallback: %s", e)
                context_blocks, memory_hits = self._retrieve_context(user_message)
                context_str = "\n".join(context_blocks[:3]) if context_blocks else ""
                sys_prompt  = system or self.SYSTEM_TEMPLATE.format(
                    context=context_str or "Nenhum contexto."
                )
        else:
            context_blocks, memory_hits = self._retrieve_context(user_message)
            context_str = "\n".join(context_blocks[:3]) if context_blocks else ""
            sys_prompt  = system or self.SYSTEM_TEMPLATE.format(
                context=context_str or "Nenhum contexto."
            )

        full_response: list = []
        try:
            for chunk in self._client.stream(user_message, sys_prompt):
                if not first_chunk_seen:
                    t_first_token    = (time.perf_counter() - t_start) * 1000.0
                    first_chunk_seen = True
                chunk_count += 1
                full_response.append(chunk)
                yield chunk
        except Exception as e:
            error_occurred = True
            is_timeout = "timeout" in str(e).lower() or "timed out" in str(e).lower()
            self._metrics.record_error(is_timeout=is_timeout)
            logger.warning("[stream_chat] erro: %s", e)
            raise
        finally:
            # Registra métricas mesmo em erro (se gerou algum chunk)
            if not error_occurred and chunk_count > 0:
                total_ms = (time.perf_counter() - t_start) * 1000.0
                tokens   = len("".join(full_response).split())
                self._metrics.record(
                    ms=total_ms,
                    tokens=tokens,
                    hits=memory_hits,
                    first_token_ms=t_first_token,
                )
                logger.debug(
                    "[stream_chat] registrado | total=%.0fms 1ºtoken=%.0fms tokens=%d tps=%.2f",
                    total_ms, t_first_token, tokens,
                    self._metrics.avg_throughput_tps,
                )

        # Armazena resposta completa
        # Dívida #10 (2026-05-26): stream_chat NÃO chama _store_to_memory porque
        # o WebSocket (caller principal) já grava a versão completa
        # (Q[:4000]+A[:12000], source_type=llm_response) em websocket.py:482.
        # Chamar aqui causaria DUPLICAÇÃO: dois entries por turno (um truncado
        # em 200+400 com source_type=user_input, outro inteiro). O entry
        # truncado ganhava score maior no retrieval (maior similaridade focada
        # + peso 1.00 vs 0.90), envenenando o contexto do modelo.
        # chat() não-streaming continua usando _store_to_memory normalmente.
        # if full_response and not error_occurred:
        #     self._store_to_memory(user_message, "".join(full_response))

    def retrieve(self, query: str, top_k: int = 5) -> List[dict]:
        """Recupera entradas de memória relevantes para a query."""
        if self._memory is None:
            return []
        try:
            return self._memory.retrieve(query, top_k=top_k)
        except Exception:
            return []

    def memory_stats(self) -> dict:
        """Retorna estatísticas da memória EDP."""
        if self._memory is None:
            return {"status": "unavailable"}
        try:
            return self._memory.stats()
        except Exception:
            return {"status": "error"}

    def llm_metrics(self) -> dict:
        return {
            "total_requests":      self._metrics.total_requests,
            "total_errors":        self._metrics.total_errors,
            "total_timeouts":      self._metrics.total_timeouts,
            "avg_latency_ms":      round(self._metrics.avg_latency_ms, 1),
            "avg_first_token_ms":  round(self._metrics.avg_first_token_ms, 1),
            "avg_throughput_tps":  round(self._metrics.avg_throughput_tps, 2),
            "avg_tokens_gen":      round(self._metrics.avg_tokens_gen, 1),
            "avg_memory_hits":     round(self._metrics.avg_memory_hits, 2),
            "last_latency_ms":     round(self._metrics.last_latency_ms, 1),
            "last_tokens":         self._metrics.last_tokens,
            "model":               self._llm_config.model if self._llm_config else "none",
            "provider":            self._llm_config.provider if self._llm_config else "none",
        }

    def list_available_models(self) -> List[str]:
        """Lista modelos disponíveis no servidor conectado."""
        if self._client is None:
            return []
        return self._client.list_models()

    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_available()

    def health(self) -> dict:
        return {
            "session_id":    self.session_id,
            "llm_connected": self.is_connected(),
            "model":         self._llm_config.model if self._llm_config else None,
            "provider":      self._llm_config.provider if self._llm_config else None,
            "memory":        self.memory_stats(),
            "metrics":       self.llm_metrics(),
            "history_turns": len(self._history) // 2,
        }

    def reset_session(self) -> None:
        """Reseta histórico da sessão (mantém memória persistida)."""
        with self._lock:
            self._history.clear()
            logger.info("[EDPRuntime] sessão %s resetada", self.session_id)

    # ── Internos ──────────────────────────────────────────────────────────────

    def _init_edp_subsystems(self) -> None:
        try:
            from .memory import MemoryStore
            self._memory = MemoryStore(self.session_id)
        except Exception as e:
            logger.warning("[EDPRuntime] MemoryStore indisponível: %s", e)

        try:
            from .pipeline import run_pipeline, get_pipeline_memory
            self._pipeline = run_pipeline
        except Exception as e:
            logger.warning("[EDPRuntime] pipeline indisponível: %s", e)

        try:
            from .context_builder import build_context
            self._ctx_builder = build_context
        except Exception as e:
            logger.warning("[EDPRuntime] context_builder indisponível: %s", e)

        # PR2 v3.13.6 — CoOccurrenceTracker (observação passiva de pares retrieved)
        # Não modifica comportamento; apenas conta quando memórias aparecem juntas.
        try:
            from .co_occurrence import CoOccurrenceTracker
            self._co_occurrence = CoOccurrenceTracker(self.session_id)
            logger.info(
                "[EDPRuntime] CoOccurrenceTracker ativo | session=%s",
                self.session_id,
            )
        except Exception as e:
            self._co_occurrence = None
            logger.warning("[EDPRuntime] CoOccurrenceTracker indisponível: %s", e)

    def _compress_input(self, text: str) -> tuple[str, float, int]:
        """Comprime input via pipeline EDP. Retorna (texto, reduction_pct, n_blocks)."""
        if self._pipeline is None or not self._auto_compress:
            return text, 0.0, 0
        try:
            result = self._pipeline(text, text[:100], session_id=self.session_id)
            return result.context_str, result.reduction_pct, len(result.context)
        except Exception:
            return text, 0.0, 0

    def _retrieve_context(self, query: str) -> tuple[List[str], int]:
        """
        Recupera contexto da memória EDP enriquecido com metadado temporal.

        Pipeline (v3.10):
          1. SEMPRE inclui últimos 2 turnos da sessão (janela imediata)
             — independente de similaridade. Evita perda de presença
             conversacional quando o usuário faz pergunta de follow-up
             ("qual a base do seu talvez?", "quais conclusões você tirou?")
          2. Retrieval normal por similaridade (top_k=5, deduplicando os
             já incluídos na janela imediata)

        Tags:
          [turno anterior]  → último Q/A
          [2 turnos atrás]  → penúltimo Q/A
          [ontem, ...]      → demais por timestamp

        Retorna: lista de blocos prefixados + número de hits.
        """
        if self._memory is None:
            return [], 0
        try:
            now = _now()
            blocks: List[str] = []
            seen_ids: set = set()

            # Coletores para debug (v3.13.8)
            _debug_immediate: list[dict] = []
            _debug_similarity: list[dict] = []

            # ── Janela imediata: últimos 2 turnos da sessão atual ───────────
            # Estes ENTRAM sempre, mesmo que retrieval não os priorize.
            # Hotfix v3.13.2: filtra session_summaries para não poluírem como
            # "turno anterior" (causava resposta confusa quando summary era
            # o último entry gravado).
            # Peça 1 (v3.13.9): ordena por timestamp antes de pegar [-2:],
            # porque self._memory.episodic.entries NÃO está garantidamente
            # em ordem cronológica (operações como reclassify_all e repair
            # podem embaralhar). Sem este sort, [-2:] pode retornar turnos
            # de DIAS atrás como "turno anterior".
            try:
                if hasattr(self._memory, "episodic"):
                    real_entries = sorted(
                        [
                            e for e in self._memory.episodic.entries
                            if e.get("source_type") != "session_summary"
                        ],
                        key=lambda e: e.get("timestamp", 0),
                    )
                    recent_entries = real_entries[-2:]
                    # Mais antigo primeiro → mais novo por último
                    immediate_labels = ["2 turnos atrás", "turno anterior"]
                    if len(recent_entries) == 1:
                        immediate_labels = ["turno anterior"]
                    for entry, label in zip(recent_entries, immediate_labels):
                        # Dívida #9 (2026-05-25): cap de 600 chars cortava resposta
                        # técnica densa no meio (P2/P3 de explicações longas).
                        # Subido para 6000 chars — cobre resposta longa típica
                        # (~1500 tokens) sem inflar excessivamente. Janela imediata
                        # ainda é só 2 turnos, então cap absoluto é ~12000 chars.
                        txt = (entry.get("text") or "")[:6000]
                        if not txt:
                            continue
                        eid = entry.get("id")
                        if eid:
                            seen_ids.add(eid)
                        blocks.append(f"[{label}] {txt}")
                        # Debug: registra o que entrou na janela imediata
                        _debug_immediate.append({
                            "id":          eid,
                            "label":       label,
                            "text":        txt,
                            "source_type": entry.get("source_type"),
                        })
            except Exception as e:
                logger.debug("[retrieve_context] janela imediata falhou: %s", e)

            # ── Retrieval por similaridade ──────────────────────────────────
            results = self._memory.retrieve(query, top_k=5, min_score=0.20)
            # PR2: coleta IDs do retrieval por similaridade (exclui janela imediata)
            # para registrar co-ocorrência. Memórias que estão em seen_ids vieram
            # da janela imediata e NÃO contam (D2: excluir janela imediata).
            co_occurrence_ids: list[str] = []
            for r in results:
                eid = r.get("id")
                if eid and eid in seen_ids:
                    continue  # já incluído na janela imediata
                txt = r.get("text") or ""
                if not txt:
                    continue
                # Capturar ID para co-occurrence (só memórias que entraram no contexto)
                if eid:
                    co_occurrence_ids.append(eid)
                # Tempo relativo
                ts = r.get("timestamp")
                rel_time = _format_relative_time(ts, now) if ts else None
                stype = r.get("source_type")
                status = r.get("epistemic_status")

                tags: list = []
                if rel_time:
                    tags.append(rel_time)
                if stype and stype not in ("unknown", "user_input"):
                    tags.append(stype)
                if status and status in ("verified", "stale", "contested"):
                    tags.append(status)

                prefix = f"[{', '.join(tags)}] " if tags else ""
                blocks.append(prefix + txt)
                # Debug: registra entrada do retrieval
                _debug_similarity.append({
                    "id":               eid,
                    "ranking_score":    r.get("ranking_score", r.get("score", 0)),
                    "text":             txt,
                    "source_type":      stype,
                    "epistemic_status": status,
                })

            # PR2: registra co-ocorrência (observação passiva)
            # Decisão D1: top-5 (mantém comportamento atual do EDP)
            # Decisão D2: já excluiu janela imediata via seen_ids acima
            # Decisão D4: save atômico ocorre dentro do tracker
            if self._co_occurrence and len(co_occurrence_ids) >= 2:
                try:
                    n_pairs = self._co_occurrence.record_co_occurrence(co_occurrence_ids)
                    self._co_occurrence.save()
                    logger.debug(
                        "[co_occurrence] %d pares registrados (memórias: %d)",
                        n_pairs, len(co_occurrence_ids),
                    )
                except Exception as e:
                    # Falha em co-occurrence NUNCA deve quebrar retrieval
                    logger.warning("[co_occurrence] hook falhou (ignorado): %s", e)

            # v3.13.8: log do contexto completo para debug
            # Nunca quebra retrieval — try/except envolve tudo.
            try:
                from .context_debug import log_context
                log_context(
                    session_id=self.session_id,
                    user_message=query,
                    immediate_window=_debug_immediate,
                    similarity_results=_debug_similarity,
                    final_blocks=blocks,
                )
            except Exception as e:
                logger.debug("[context_debug] log falhou: %s", e)

            return blocks, len(blocks)
        except Exception as e:
            logger.debug("[retrieve_context] erro: %s", e)
            return [], 0

    def _build_enriched_context(
        self,
        query: str,
        system_prompt: str,
    ) -> tuple[str, dict]:
        """
        Constrói contexto enriquecido usando ContextWindowManager.

        Aplica:
          - Calibração afetiva por turno (v3.11)
          - Budget de tokens baseado na janela real do modelo
          - Priorização: system+query > âncoras > retrieval > recentes
          - Deduplicação por similaridade

        Compatibilidade: se ContextWindowManager indisponível, retorna
        contexto simples (formato antigo) — nunca falha.

        Returns:
            (sys_prompt_renderizado, metadata_dict)
        """
        # ── Calibração afetiva (v3.11) ───────────────────────────────────
        # Detecta cansaço, apego excessivo, sinais de distorção.
        # Injeta instruções específicas no SYSTEM_TEMPLATE deste turno.
        affect_meta = {"any_triggered": False}
        try:
            from .affective_calibration import (
                calibrate_affect, format_calibration_for_prompt,
            )
            affect_meta = calibrate_affect(query, self._memory)
            calibration_block = format_calibration_for_prompt(affect_meta)
            if calibration_block:
                # Injeta ANTES da seção {context} (alta prioridade)
                if "{context}" in system_prompt:
                    system_prompt = system_prompt.replace(
                        "{context}",
                        "{context}\n" + calibration_block,
                    )
                else:
                    system_prompt = system_prompt + "\n" + calibration_block
                logger.info(
                    "[affect] disparou | fadiga=%s apego=%s distorção=%s",
                    affect_meta["fatigue"].get("triggered", False),
                    affect_meta["attachment"].get("triggered", False),
                    affect_meta["distortion"].get("triggered", False),
                )
        except Exception as e:
            logger.debug("[affect] calibração falhou: %s", e)

        # Fallback gracioso se módulo indisponível
        try:
            from .runtime.context_window_manager import ContextWindowManager
        except Exception:
            blocks, hits = self._retrieve_context(query)
            ctx_str = "\n".join(blocks[:3]) if blocks else "Nenhum contexto."
            return (
                system_prompt.format(context=ctx_str) if "{context}" in system_prompt else system_prompt,
                {"memory_hits": hits, "manager_used": False,
                 "affect": affect_meta},
            )

        # Configura manager baseado no modelo atual
        model_name = self._llm_config.model if self._llm_config else "default"
        mgr = ContextWindowManager(
            model_name=model_name,
            reserve_response=512,
            max_anchors=2,
            max_recent=3,
            max_retrieval=5,
        )

        # Recupera memórias relevantes
        blocks, hits = self._retrieve_context(query)

        # Recupera últimos turnos como recent_turns (com tempo relativo)
        recent_turns: list = []
        all_history:  list = []
        try:
            now_ts = _now()
            if self._memory is not None and hasattr(self._memory, "episodic"):
                # Peça 1 (v3.13.9): ordena por timestamp antes de pegar
                # últimos/primeiros, pois a lista pode estar embaralhada.
                entries = sorted(
                    list(self._memory.episodic.entries),
                    key=lambda e: e.get("timestamp", 0),
                )
                # Últimos 5 turnos (recent): mais úteis quando datados
                for e in entries[-5:]:
                    txt = (e.get("text", "") or "")[:300]
                    if not txt:
                        continue
                    ts  = e.get("timestamp")
                    rel = _format_relative_time(ts, now_ts) if ts else ""
                    prefix = f"[{rel}] " if rel else ""
                    recent_turns.append(prefix + txt)
                # Primeiros 2 (anchors): contexto inicial
                for e in entries[:5]:
                    txt = (e.get("text", "") or "")[:300]
                    if not txt:
                        continue
                    ts  = e.get("timestamp")
                    rel = _format_relative_time(ts, now_ts) if ts else ""
                    prefix = f"[{rel}] " if rel else ""
                    all_history.append(prefix + txt)
        except Exception:
            pass

        # Constrói contexto otimizado
        ctx = mgr.build(
            system_prompt=system_prompt.replace("{context}", "").strip(),
            query=query,
            recent_turns=recent_turns,
            retrieval=blocks,
            all_history=all_history,
        )

        rendered = ctx.to_prompt()
        meta     = {
            "memory_hits":   hits,
            "manager_used":  True,
            "budget":        ctx.to_metrics(),
            "model":         model_name,
            "affect":        affect_meta,
        }
        logger.debug(
            "[ctx] %s tokens usados | hits=%d anchors=%d recent=%d",
            ctx.budget.used if ctx.budget else "?",
            hits, len(ctx.anchors), len(ctx.recent)
        )
        return rendered, meta

    def _store_to_memory(self, user_msg: str, response: str) -> None:
        """
        Armazena par pergunta-resposta na memória.

        Dívida #10 (2026-05-26): caps alinhados com websocket.py:474-475
        (Q[:4000] + A[:12000]) para evitar truncamento de respostas longas
        que chegavam ao retrieve com fragmentos. Source adicionado para
        memory_classifier categorizar como llm_response (peso 0.90) em vez
        de user_input (peso 1.00) — entry contém Q+A combinados, é resposta
        do LLM, não input puro do usuário.

        Chamado apenas por chat() não-streaming (API REST /llm/chat).
        stream_chat() não chama mais (anti-duplicação no fluxo WebSocket).
        """
        if self._memory is None:
            return
        try:
            combined = f"Q: {user_msg[:4000]}\nA: {response[:12000]}"
            source = "user"
            if self._llm_config:
                source = f"llm:{self._llm_config.model}"
            self._memory.add(combined, score=0.65, prioridade="media", source=source)
            # Flush assíncrono para não bloquear
            if hasattr(self._memory.episodic, "flush"):
                threading.Thread(
                    target=self._memory.episodic.flush, daemon=True
                ).start()
        except TypeError:
            # Fallback se memory.add não aceitar source (compat com versões antigas)
            try:
                self._memory.add(combined, score=0.65, prioridade="media")
                if hasattr(self._memory.episodic, "flush"):
                    threading.Thread(
                        target=self._memory.episodic.flush, daemon=True
                    ).start()
            except Exception as e:
                logger.debug("[EDPRuntime] store fallback falhou: %s", e)
        except Exception as e:
            logger.debug("[EDPRuntime] store falhou: %s", e)
