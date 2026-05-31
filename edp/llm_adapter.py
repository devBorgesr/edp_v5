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
    max_tokens:  int         = 4096  # Peça 2.5e (2026-05-29): subido de 2048
                                     # para 4096. Diagnóstico: desafios técnicos
                                     # legítimos (arquitetura, design review)
                                     # eram cortados no meio da palavra final.
                                     # Caso real: resposta sobre microsserviços
                                     # Java+SpringBoot terminou em "preserv" (sic)
                                     # ao bater no teto de 2048. 4096 (~3000
                                     # palavras) cobre desafio técnico longo
                                     # sem abrir porta para inflação infinita
                                     # (8192 seria absurdo). Demais defesas
                                     # contra completude_forcada continuam
                                     # ativas via CETICISMO_DEFAULT + câmara.
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
        # Peça 2.4a.1 (2026-05-27): câmara de eco lazy.
        # Instanciada na primeira chamada de get_echo_chamber() para evitar
        # custo de inicialização quando câmara não é usada (a maioria dos turnos).
        self._echo_chamber = None

        # Peça 2.6a (2026-05-30): modo operacional bimodal.
        # "cognitive" (default) — janela enxuta, cap turno-1 = 4000, EDP em
        #   identidade original. Retrieval só vê blocos cognitivos.
        # "sprint" — janela expandida, cap turno-1 = 12000, suporta auto-
        #   referência sobre código longo. Retrieval vê ambos os tipos.
        # Estado SEMPRE inicia em cognitive ao boot (não persiste entre
        # sessões). Trocado via comando /mode sprint|cognitive no chat ou
        # via endpoint HTTP POST /mode/{name}. Categorização de blocos
        # (campo mode no entry) fica para próxima sprint — versão mínima
        # aqui altera apenas caps de retrieval.
        self._operational_mode: str = "cognitive"
        logger.info("[boot] modo operacional inicial = cognitive")

        # Peça 2.6b (2026-05-30): modo sectioned — entrega-por-seção.
        # Quando ativo, o LLM recebe system prompt extra instruindo a
        # entregar UMA seção por turno, terminando com indicação clara
        # de "Aguardo /next para continuar". Restrito a sprint mode
        # (decisão de design: seções acumulam contexto, cognitive cap=4000
        # estouraria rápido). Auto-desativa se usuário voltar para cognitive.
        # Estado inicia sempre False ao boot.
        self._sectioned_active: bool = False

        # Peça 2.6c (2026-05-30): bloco de âncora de tarefa em curso.
        # Resolve limite descoberto em uso real da 2.6b: janela imediata
        # é cega para "tarefa em curso" — mistura turnos não relacionados
        # da sessão inteira. Em tarefa de 10 seções, conforme avança, a
        # janela imediata perde as Seções antigas porque outros turnos
        # competem por slots. Modelo regenera Seção 2 três vezes.
        #
        # Solução: estado dedicado que persiste durante a tarefa, injetado
        # como Camada 0.5 do payload (após âncora temporal, antes da
        # janela imediata). Contém:
        #   - challenge: texto original do desafio (truncado)
        #   - sections_delivered: lista de {n, total, title, summary}
        #   - expected_total: int (extraído da primeira seção)
        #
        # Parser determinístico: o system prompt do sectioned obriga
        # formato "## Seção N/M — Título". Sem heurística, parser trivial.
        #
        # Estado em memória apenas (reinicia perde tarefa — aceito).
        # Auto-cria na primeira mensagem em sectioned mode que não é /next.
        # Auto-limpa ao desativar sectioned ou completar M de M seções.
        self._task_anchor: dict | None = None

        self._init_edp_subsystems()

    # ── Modo operacional bimodal (peça 2.6a) ──────────────────────────────────

    def get_operational_mode(self) -> str:
        """Retorna o modo operacional atual ('cognitive' ou 'sprint')."""
        return self._operational_mode

    def set_operational_mode(self, mode: str) -> dict:
        """
        Troca o modo operacional. Validação estrita: aceita apenas
        'cognitive' ou 'sprint'. Loga a transição.

        Peça 2.6b: ao voltar para cognitive, auto-desativa sectioned
        (sectioned é restrito a sprint).

        Returns:
            dict com 'ok' (bool), 'mode' (str), 'message' (str)
        """
        mode_lower = mode.strip().lower()
        if mode_lower not in ("cognitive", "sprint"):
            return {
                "ok": False,
                "mode": self._operational_mode,
                "message": f"modo inválido: '{mode}' (use 'cognitive' ou 'sprint')",
            }
        previous = self._operational_mode
        if mode_lower == previous:
            return {
                "ok": True,
                "mode": mode_lower,
                "message": f"já está em modo {mode_lower}",
            }
        self._operational_mode = mode_lower
        logger.info("[mode] transição: %s → %s", previous, mode_lower)
        # Peça 2.6b: auto-desativa sectioned ao sair de sprint
        sectioned_msg = ""
        if mode_lower == "cognitive" and self._sectioned_active:
            self._sectioned_active = False
            logger.info("[sectioned] auto-desativado (saiu de sprint)")
            sectioned_msg = " (sectioned desativado automaticamente)"
        # Mensagem com aviso de custo se entrando em sprint
        if mode_lower == "sprint":
            msg = ("modo SPRINT ativado — janela imediata expandida para "
                   "12000 chars no turno anterior. ATENÇÃO: custo por "
                   "resposta pode aumentar 2-5× em conversas técnicas "
                   "longas. Use /mode cognitive para voltar.")
        else:
            msg = ("modo COGNITIVE ativado — janela imediata enxuta (cap "
                   "4000 chars). Identidade padrão do EDP restaurada.")
        return {
            "ok": True, "mode": mode_lower,
            "message": msg + sectioned_msg, "previous": previous,
        }

    # ── Modo sectioned — entrega-por-seção (peça 2.6b) ───────────────────────

    def is_sectioned_active(self) -> bool:
        """Retorna True se o modo entrega-por-seção está ativo."""
        return self._sectioned_active

    def set_sectioned(self, active: bool) -> dict:
        """
        Ativa ou desativa modo sectioned (entrega-por-seção).

        Restrição: só pode ser ativado quando o modo operacional é 'sprint'.
        Razão: seções acumulam no contexto; o cap turno-1=4000 do modo
        cognitive estouraria com 3-4 seções de código denso. Sprint tem
        cap=12000, suporta o acúmulo.

        Returns:
            dict com 'ok' (bool), 'active' (bool), 'message' (str)
        """
        if active:
            if self._operational_mode != "sprint":
                return {
                    "ok": False,
                    "active": self._sectioned_active,
                    "message": ("modo sectioned exige sprint ativo. "
                                "Use '/mode sprint' primeiro, depois '/sectioned'."),
                }
            if self._sectioned_active:
                return {
                    "ok": True,
                    "active": True,
                    "message": "sectioned já está ativo",
                }
            self._sectioned_active = True
            logger.info("[sectioned] ativado")
            return {
                "ok": True,
                "active": True,
                "message": ("modo SECTIONED ativado — o LLM entregará UMA "
                            "seção por turno. Use '/next' (ou 'continue', "
                            "'próxima') para avançar. Use '/sectioned off' "
                            "para desativar."),
            }
        else:
            if not self._sectioned_active:
                return {
                    "ok": True,
                    "active": False,
                    "message": "sectioned já está inativo",
                }
            self._sectioned_active = False
            logger.info("[sectioned] desativado")
            # Peça 2.6c: ao desativar sectioned, limpa âncora de tarefa
            if self._task_anchor is not None:
                logger.info("[task] âncora limpa (sectioned desativado)")
                self._task_anchor = None
            return {
                "ok": True,
                "active": False,
                "message": "modo SECTIONED desativado. Resposta volta ao formato único.",
            }

    # ── Âncora de tarefa em curso (peça 2.6c) ────────────────────────────────

    def get_task_anchor(self) -> dict | None:
        """Retorna o estado atual da tarefa em curso, ou None se inativa."""
        return self._task_anchor

    def start_task(self, challenge: str) -> dict:
        """
        Inicia rastreamento de tarefa multi-seção. Chamado automaticamente
        quando: sectioned ativo + mensagem do usuário NÃO é /next/continue.

        Trunca challenge a 2000 chars para caber na âncora.
        Limpa estado anterior se houver.
        """
        previous = self._task_anchor
        self._task_anchor = {
            "challenge": (challenge or "")[:2000],
            "sections_delivered": [],
            "expected_total": None,
            "started_at_turn": None,  # opcional, debug
        }
        if previous is not None:
            logger.info("[task] estado anterior substituído")
        logger.info("[task] iniciada (challenge=%d chars)", len(challenge or ""))
        return {"ok": True, "task": self._task_anchor}

    def clear_task(self) -> dict:
        """Limpa estado da tarefa. Chamado quando completa todas as seções
        ou quando o usuário emite /task clear."""
        had = self._task_anchor is not None
        self._task_anchor = None
        if had:
            logger.info("[task] limpa explicitamente")
        return {"ok": True, "cleared": had}

    def register_section_delivered(self, llm_response: str) -> dict:
        """
        Parser determinístico de saída do LLM.

        Formato CONTRATADO via system prompt do sectioned:
            ## Seção N/M — Título

        Extração: regex sobre `## Seção (\\d+)/(\\d+) — (.+?)$` na primeira
        linha que casar. Sem heurística: ou casa exato, ou ignora silenciosamente.

        Se casar:
          - Atualiza expected_total se ainda None
          - Adiciona {n, total, title, summary} a sections_delivered
          - Se n >= total, marca tarefa como completa (limpa anchor)

        Returns:
            dict com {parsed: bool, n?: int, total?: int, complete?: bool}
        """
        import re
        if self._task_anchor is None:
            return {"parsed": False, "reason": "task_inactive"}
        if not llm_response or not isinstance(llm_response, str):
            return {"parsed": False, "reason": "empty_response"}

        # Formato contratado — busca a primeira ocorrência
        # Aceita variações leves: travessão, hífen, en-dash; espaços flexíveis
        # Mas o número e o "Seção" são literais.
        pattern = re.compile(
            r"##\s*Se[çc][ãa]o\s+(\d+)\s*/\s*(\d+)\s*[—\-–:]\s*(.+?)$",
            re.IGNORECASE | re.MULTILINE,
        )
        match = pattern.search(llm_response)
        if not match:
            logger.debug("[task] parser: formato não detectado na resposta")
            return {"parsed": False, "reason": "format_not_found"}

        n = int(match.group(1))
        total = int(match.group(2))
        title = match.group(3).strip()[:120]

        # Resumo: primeiras 200 chars de texto após o cabeçalho
        # 2.6e M1: remove blocos HTML invisíveis do summary para não duplicar
        head_end = match.end()
        raw_after_head = llm_response[head_end:head_end + 600].strip()
        # Remove o bloco <!-- decisions: ... --> do summary (será extraído separadamente)
        cleaned = re.sub(
            r"<!--\s*decisions\s*:.*?-->",
            "",
            raw_after_head,
            flags=re.DOTALL | re.IGNORECASE,
        )
        summary = re.sub(r"\s+", " ", cleaned).strip()[:200]

        # ── Peça 2.6e M1 (2026-05-30): extrair decisões técnicas ──────
        # Formato contratado via system prompt:
        #   <!-- decisions: {"messaging":"...","language":"...",...} -->
        # Parser determinístico — JSON dentro do comentário HTML.
        # Modelo declara explicitamente; sistema só extrai e propaga.
        # Se JSON falhar, registra warning mas não impede registro da seção.
        decisions = None
        decisions_pattern = re.compile(
            r"<!--\s*decisions\s*:\s*(\{.*?\})\s*-->",
            re.DOTALL | re.IGNORECASE,
        )
        dmatch = decisions_pattern.search(llm_response)
        if dmatch:
            import json
            raw_json = dmatch.group(1)
            try:
                decisions = json.loads(raw_json)
                if not isinstance(decisions, dict):
                    logger.warning(
                        "[task] decisions parsed mas não é dict: %s",
                        type(decisions).__name__,
                    )
                    decisions = None
                else:
                    logger.info(
                        "[task] decisões extraídas: %d chaves (%s)",
                        len(decisions),
                        ", ".join(list(decisions.keys())[:5]),
                    )
            except json.JSONDecodeError as e:
                logger.warning(
                    "[task] JSON de decisions inválido (seção %d): %s",
                    n, str(e)[:100],
                )
                decisions = None
        else:
            logger.debug("[task] bloco <!-- decisions --> não encontrado na seção %d", n)
        # ── fim da extração de decisões ───────────────────────────────

        # Atualiza expected_total se ainda não fixado
        if self._task_anchor["expected_total"] is None:
            self._task_anchor["expected_total"] = total
            logger.info("[task] total de seções definido: %d", total)
        elif self._task_anchor["expected_total"] != total:
            # Modelo mudou o total — anota mas não rejeita
            logger.warning(
                "[task] total inconsistente: anchor=%d, resposta=%d",
                self._task_anchor["expected_total"], total,
            )

        # Adiciona à lista (evita duplicata se mesma seção for re-entregue)
        existing_ns = {s["n"] for s in self._task_anchor["sections_delivered"]}
        if n in existing_ns:
            logger.warning("[task] Seção %d re-entregue (substituindo)", n)
            self._task_anchor["sections_delivered"] = [
                s for s in self._task_anchor["sections_delivered"] if s["n"] != n
            ]
        self._task_anchor["sections_delivered"].append({
            "n": n, "total": total, "title": title, "summary": summary,
            "decisions": decisions,  # Peça 2.6e M1: pode ser None se não veio
        })
        # Ordena para apresentação consistente
        self._task_anchor["sections_delivered"].sort(key=lambda s: s["n"])

        logger.info(
            "[task] seção registrada: %d/%d — %s%s",
            n, total, title[:40],
            f" (decisions: {len(decisions)} chaves)" if decisions else "",
        )

        # Tarefa completa?
        delivered_ns = {s["n"] for s in self._task_anchor["sections_delivered"]}
        complete = len(delivered_ns) >= total and max(delivered_ns) >= total
        if complete:
            logger.info("[task] COMPLETA (%d/%d entregues)", len(delivered_ns), total)
            # Limpa estado — tarefa finalizada
            self._task_anchor = None
            return {"parsed": True, "n": n, "total": total, "complete": True}

        return {"parsed": True, "n": n, "total": total, "complete": False}

    def format_task_anchor(self) -> str | None:
        """
        Formata o estado da âncora como bloco texto para injetar no payload.
        Retorna None se não há tarefa ativa.
        """
        if self._task_anchor is None:
            return None

        ta = self._task_anchor
        delivered = ta["sections_delivered"]
        total = ta["expected_total"] or "?"

        lines = ["[ÂNCORA DE TAREFA EM CURSO]"]
        lines.append(f"Desafio: {ta['challenge'][:800]}")
        if len(ta['challenge']) > 800:
            lines.append("[...desafio truncado...]")
        lines.append("")

        if delivered:
            lines.append(f"Seções já entregues ({len(delivered)} de {total}):")
            for s in delivered:
                lines.append(f"  • Seção {s['n']}/{s['total']} — {s['title']}")
                if s.get("summary"):
                    lines.append(f"    Resumo: {s['summary']}")
                # Peça 2.6e M1: incluir decisões da seção (se houver)
                if s.get("decisions"):
                    dec_summary = []
                    for k, v in list(s["decisions"].items())[:6]:
                        v_short = str(v)[:120]
                        dec_summary.append(f"{k}={v_short}")
                    lines.append(f"    Decisões: {' | '.join(dec_summary)}")

            # ── Peça 2.6e M1: bloco consolidado de decisões arquiteturais ──
            # Agrega todas as decisões já tomadas em todas as seções.
            # Modelo deve respeitar estas decisões nas seções seguintes.
            consolidated = {}
            for s in delivered:
                if s.get("decisions"):
                    for k, v in s["decisions"].items():
                        # Última decisão sobre a chave prevalece (registra apenas
                        # se ainda não estiver no consolidado, para preservar a
                        # decisão original feita na Seção mais antiga)
                        if k not in consolidated:
                            consolidated[k] = {"value": v, "from_section": s["n"]}
            if consolidated:
                lines.append("")
                lines.append("DECISÕES ARQUITETURAIS JÁ ESTABELECIDAS:")
                lines.append("(Mantenha estas decisões nas próximas seções. "
                             "Mudar requer justificativa explícita.)")
                for k, info in consolidated.items():
                    v_str = str(info["value"])[:200]
                    lines.append(
                        f"  • {k} (def. Seção {info['from_section']}): {v_str}"
                    )

            # Próxima esperada
            delivered_ns = {s["n"] for s in delivered}
            if isinstance(total, int):
                next_n = next(
                    (i for i in range(1, total + 1) if i not in delivered_ns),
                    None,
                )
                if next_n:
                    lines.append("")
                    lines.append(f"PRÓXIMA SEÇÃO ESPERADA: Seção {next_n}/{total}")
                    lines.append("NÃO regenere seções já entregues acima. "
                                 "Avance para a próxima usando as decisões já estabelecidas.")
        else:
            lines.append("Nenhuma seção entregue ainda. Comece pela Seção 1.")

        return "\n".join(lines)

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

        # ── Peça 2.4a.2b (2026-05-27): injetar CETICISMO_DEFAULT no system prompt ─
        # Sem isso, o modelo A admite limite com linguagem livre ("não existe",
        # "não posso") que não casa com a regex de auto-sinal. Com CETICISMO
        # injetado, A aprende MÉTODO (extrair máximo) + LIMITE EPISTÊMICO (admitir
        # com precisão usando frases-padrão). A regex então detecta a admissão
        # e a câmara é ativada.
        # Injeta sempre (a-pleno) — não condicional ao roteador.
        try:
            from .echo_chamber import CETICISMO_DEFAULT
            if CETICISMO_DEFAULT not in sys_prompt:
                sys_prompt = f"{sys_prompt}\n\n---\n\n{CETICISMO_DEFAULT}"
        except Exception as e:
            logger.debug("[stream_chat] CETICISMO_DEFAULT indisponível: %s", e)

        # ── Peça 2.6b (2026-05-30): injetar SECTIONED_INSTRUCTION se ativo ───
        # Quando o usuário ativa /sectioned, o modelo recebe instrução
        # explícita para entregar UMA seção por turno e aguardar comando
        # de continuação. Coesão entre seções é preservada pela janela
        # imediata da peça 2.5a — modelo vê o desafio original + seções
        # já entregues em até 6 turnos anteriores.
        #
        # 2.6c (2026-05-30): formato contratado para parser determinístico.
        # Modelo OBRIGADO a abrir cada seção com "## Seção N/M — Título".
        # Isso permite parser sem heurística + injeção de âncora de tarefa.
        try:
            if getattr(self, "_sectioned_active", False):
                SECTIONED_INSTRUCTION = (
                    "\n\n---\n\n"
                    "INSTRUÇÃO DE FORMATO — MODO SECTIONED ATIVO:\n"
                    "Se a pergunta do usuário envolve tarefa com MÚLTIPLAS partes "
                    "claramente delimitadas (seções numeradas, etapas, capítulos), "
                    "siga esta política:\n"
                    "\n"
                    "1. Entregue APENAS UMA seção/parte por turno. Não tente "
                    "comprimir várias.\n"
                    "\n"
                    "2. FORMATO OBRIGATÓRIO de cabeçalho — abra cada seção com "
                    "exatamente esta linha (preserve o '/' entre números e o "
                    "travessão '—' antes do título):\n"
                    "   `## Seção N/M — Título da Seção`\n"
                    "   Onde N é o número da seção atual e M é o total de seções "
                    "da tarefa. Exemplo: `## Seção 3/10 — API Gateway`\n"
                    "   Este formato é parseado mecanicamente pelo sistema. "
                    "Variações quebram o rastreamento.\n"
                    "\n"
                    "3. FORMATO OBRIGATÓRIO de fim de seção — ANTES de 'Aguardando "
                    "comando', inclua um bloco HTML invisível registrando as "
                    "decisões técnicas tomadas nesta seção:\n"
                    "   `<!-- decisions: {\"chave1\":\"valor1\",\"chave2\":\"valor2\"} -->`\n"
                    "   O JSON deve ser válido. Chaves típicas: messaging, language, "
                    "database, patterns, contracts, framework, cache, resilience, "
                    "auth, observability — adapte ao domínio. Valores devem ser "
                    "descritivos e específicos (ex: 'Apache Kafka 3.7 com particionamento "
                    "por payment_id', não apenas 'Kafka'). Este bloco é parseado "
                    "mecanicamente e propagado para as próximas seções.\n"
                    "\n"
                    "4. Termine a resposta com a linha:\n"
                    "   'Aguardando comando para continuar (/next ou \"continue\").'\n"
                    "\n"
                    "5. Mantenha decisões arquiteturais consistentes entre seções "
                    "— você verá um bloco [ÂNCORA DE TAREFA EM CURSO] no contexto "
                    "listando o desafio, seções já entregues E as DECISÕES "
                    "ARQUITETURAIS JÁ ESTABELECIDAS. Use esse bloco como verdade "
                    "absoluta. Se precisar mudar uma decisão estabelecida, "
                    "JUSTIFIQUE EXPLICITAMENTE por que a mudança é necessária.\n"
                    "\n"
                    "6. Se a pergunta atual já é uma resposta completa em si "
                    "(não é multi-parte), responda normalmente e ignore esta "
                    "política.\n"
                    "\n"
                    "7. Se o usuário enviar '/next', 'continue', 'próxima', "
                    "'vai' ou variantes equivalentes (incluindo typos como "
                    "'contenue', 'continuir'), retome de onde parou e entregue "
                    "a PRÓXIMA SEÇÃO NÃO ENTREGUE conforme indicado no bloco de "
                    "âncora de tarefa. NÃO regenere seções já listadas."
                )
                if SECTIONED_INSTRUCTION not in sys_prompt:
                    sys_prompt = sys_prompt + SECTIONED_INSTRUCTION
                    logger.debug("[sectioned] system prompt extra injetado")
        except Exception as e:
            logger.debug("[stream_chat] SECTIONED_INSTRUCTION falhou: %s", e)

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

    # ── Peça 2.4a.1: Câmara de eco lazy ────────────────────────────────────

    def _llm_call_for_chamber(self, modelo: str, system: str, user: str) -> dict:
        """
        Callback que a EchoChamber usa para chamar LLM síncrono (não-streaming).

        Retorna dict com {text, cost_usd, latency_ms} esperado pela câmara.

        Usa o mesmo cliente do runtime (`self._client`) que já tem credenciais
        configuradas. Permite à câmara invocar modelos diferentes do principal
        (ex: Sonnet para refutar Haiku) sem reinicializar provider.
        """
        if self._client is None:
            return {
                "text": "[ERRO: nenhum LLM conectado para câmara]",
                "cost_usd": 0.0,
                "latency_ms": 0.0,
            }
        import time as _time
        t0 = _time.perf_counter()
        try:
            # Override temporário do modelo para esta chamada específica
            original_model = self._client._cfg.model
            self._client._cfg.model = modelo
            try:
                text = self._client.complete(user, system=system)
            finally:
                self._client._cfg.model = original_model
            latency_ms = (_time.perf_counter() - t0) * 1000.0
            return {
                "text": text or "",
                "cost_usd": 0.0,  # custo real é registrado pelo provider
                "latency_ms": latency_ms,
            }
        except Exception as e:
            logger.warning("[EDPRuntime] llm_call_for_chamber falhou: %s", e)
            return {
                "text": f"[ERRO câmara: {e}]",
                "cost_usd": 0.0,
                "latency_ms": (_time.perf_counter() - t0) * 1000.0,
            }

    def get_echo_chamber(self):
        """
        Retorna instância da EchoChamber para esta sessão.

        Lazy: cria na primeira chamada e cacheia. Reusa em chamadas subsequentes.
        Razão: instanciar EchoChamber carrega dados de `default_camara.json`,
        custo desnecessário se câmara nunca é ativada nesta sessão.

        Returns:
            EchoChamber configurada para esta sessão, ou None se módulo indisponível.
        """
        if self._echo_chamber is not None:
            return self._echo_chamber

        try:
            from .echo_chamber import EchoChamber
            from pathlib import Path
            import os
            base_env = os.environ.get("EDP_BASE_DIR", str(Path.home() / "edp_data"))
            base_dir = Path(base_env) / "sessions"
            self._echo_chamber = EchoChamber(
                session_id=self.session_id,
                base_dir=base_dir,
                llm_caller=self._llm_call_for_chamber,
            )
            logger.info(
                "[EDPRuntime] EchoChamber instanciada | session=%s",
                self.session_id,
            )
            return self._echo_chamber
        except Exception as e:
            logger.warning("[EDPRuntime] EchoChamber indisponível: %s", e)
            return None

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

        Pipeline (v3.15 + peças 2.5a + 2.5b):
          1. SEMPRE inclui últimos 6 turnos da sessão (janela imediata expandida)
             com cap de chars VARIÁVEL — turnos mais recentes ganham mais espaço.
             Garante presença contínua da thread cognitiva atual, evitando o
             "reset no meio da conversa" que causava condescendência (modelo
             perdia o próprio "Não tenho base sólida" de 3 turnos atrás).

             Caps (decrescentes pela ordem cronológica reversa):
               turno-1 (mais recente): 12000 chars (peça 2.5a.refact)
               turnos -2, -3:          3000 chars
               turnos -4, -5, -6:      1500 chars

             Total máximo: ~22500 chars na janela imediata (capacidade
             aumentada para suportar auto-referência em respostas técnicas
             longas — caso real onde o modelo precisava refletir sobre
             própria resposta de ~10000 chars).

          2. Bloco ativo (peça 2.5b): injeta os 3 entries MAIS ANTIGOS do
             bloco aberto que ainda não estão na janela imediata. Cobre a
             "zona morta" do meio da conversa — turnos da mesma linha de
             investigação que escaparam da janela imediata mas pertencem
             ao bloco vivo. Cap 1500 chars por entry. Label: [bloco atual].

          3. Retrieval normal por similaridade (top_k=5, deduplicando os
             já incluídos nas camadas 1 e 2).

        Três camadas claras no payload:
          [janela imediata] → últimos 6 turnos (cronológico, sempre)
          [bloco atual]     → início da linha de investigação atual
          [retrieval]       → associações semânticas históricas

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

            # ── Camada 0: ÂNCORA TEMPORAL ABSOLUTA (peça 2.5d) ───────────────
            # Peça 2.5d (2026-05-29): quarto buraco da "alma" — cegueira temporal.
            #
            # Diagnóstico (caso real desta sprint): durante a discussão sobre
            # qualidade vs urgência da apresentação para o tech lead, o LLM
            # interlocutor (Claude) confabulou que "a apresentação é amanhã"
            # quando na verdade era na segunda-feira seguinte. Sem âncora
            # temporal no payload, o modelo "preenche" tempo a partir de pistas
            # contextuais — exatamente o mesmo padrão do caso 16c659ea ("17
            # minutos"), só que aqui foi o próprio Claude da apresentação
            # cometendo a falha em tempo real.
            #
            # Solução: injetar timestamp absoluto no TOPO do payload (antes da
            # janela imediata), em formato híbrido ISO 8601 + texto humano em
            # pt-BR. Usa edp.clock (não datetime.now() cru) para herdar:
            #   - Sincronização HTTP/NTP nativa
            #   - Marca temporal_unverified quando clock está em fallback
            #
            # Se is_verified() == False (clock em fallback offline), a âncora
            # avisa explicitamente — honestidade temporal nativa, conforme o
            # padrão do EDP de marcar incerteza em vez de esconder.
            try:
                from . import clock as _clock_mod
                t_now_epoch = _clock_mod.now()
                clock_verified = _clock_mod.is_verified()

                from datetime import datetime, timezone, timedelta
                # UTC -03:00 (Brasília sem horário de verão atualmente)
                # Decisão: fixar -03:00 evita dependência de tzdata no Windows
                tz_brt = timezone(timedelta(hours=-3))
                dt_local = datetime.fromtimestamp(t_now_epoch, tz=tz_brt)

                # ISO 8601 inequívoco
                iso = dt_local.strftime("%Y-%m-%d %H:%M:%S %z")
                # Forma humana pt-BR
                dias_semana_pt = [
                    "segunda-feira", "terça-feira", "quarta-feira",
                    "quinta-feira", "sexta-feira", "sábado", "domingo",
                ]
                meses_pt = [
                    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
                    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
                ]
                dia_semana = dias_semana_pt[dt_local.weekday()]
                mes_nome = meses_pt[dt_local.month - 1]
                humano = (
                    f"{dia_semana}, {dt_local.day} de {mes_nome} de {dt_local.year}, "
                    f"{dt_local.hour:02d}h{dt_local.minute:02d}"
                )

                if clock_verified:
                    ancora_txt = (
                        "[ÂNCORA TEMPORAL]\n"
                        f"Momento atual: {iso} ({humano}).\n"
                        "Use esta informação se a conversa exigir referência ao "
                        "tempo presente. NÃO confabule datas, durações ou dias "
                        "da semana — você tem o tempo absoluto aqui."
                    )
                else:
                    # Clock em fallback (sem rede / NTP/HTTP indisponíveis)
                    ancora_txt = (
                        "[ÂNCORA TEMPORAL — modo fallback]\n"
                        f"Momento estimado: {iso} ({humano}). "
                        "ATENÇÃO: clock do EDP está em fallback (sem sincronização "
                        "online verificada). Pode haver desvio. Trate como "
                        "estimativa, não como verdade absoluta."
                    )
                blocks.append(ancora_txt)
            except Exception as e:
                logger.debug("[retrieve_context] âncora temporal falhou: %s", e)

            # ── Camada 0.5: ÂNCORA DE TAREFA EM CURSO (peça 2.6c) ────────────
            # Resolve limite descoberto em 2.6b: janela imediata é cega para
            # "tarefa em curso", mistura turnos não relacionados. Em tarefa
            # multi-seção (10 seções), modelo perdia visibilidade das Seções
            # já entregues quando outros turnos competiam por slots.
            #
            # Solução: estado dedicado por sessão, injetado antes da janela
            # imediata. Lista o desafio + seções já entregues (com título e
            # resumo) + próxima esperada. Modelo trata como verdade absoluta
            # de "o que já foi feito".
            #
            # Só ativo quando _task_anchor está populado (modo sectioned +
            # tarefa em andamento). Em outros casos, bloco é omitido.
            try:
                if hasattr(self, "format_task_anchor"):
                    task_block = self.format_task_anchor()
                    if task_block:
                        blocks.append(task_block)
                        logger.debug(
                            "[task_anchor] injetada (%d seções entregues)",
                            len(self._task_anchor.get("sections_delivered", [])),
                        )
            except Exception as e:
                logger.debug("[retrieve_context] task_anchor falhou: %s", e)

            # ── Janela imediata: últimos N turnos da sessão atual ───────────
            # Peça 2.5a (2026-05-28): expandida de 2 → 6 turnos com cap variável.
            # Diagnóstico: o EDP estava resetando o modelo no meio de conversas
            # longas. Modelo perdia a própria admissão "Não tenho base sólida"
            # de 3 turnos atrás e cedia à pressão do usuário inventando resposta
            # plausível (caso real: Bayes/Turing — modelo inventou Fourier+Titanic).
            # Janela curta = sem thread cognitiva = condescendência crônica.
            #
            # Cap variável (decrescente pela ordem cronológica reversa):
            #   turno mais recente (-1):       4000 chars (frescura preservada)
            #   turnos -2, -3:                 3000 chars (debate denso recente)
            #   turnos -4, -5, -6:             1500 chars (âncora cronológica)
            # Total máximo: ~16500 chars na janela imediata.
            #
            # Hotfix v3.13.2 mantido: filtra session_summaries para não poluírem
            # como "turno anterior".
            # Peça 1 (v3.13.9) mantido: ordena por timestamp antes de [-N:].
            # Janela imediata mantém precedência sobre retrieval por similaridade.
            JANELA_IMEDIATA_N = 6
            # Caps por posição (mais recente primeiro: index 0 = turno-1).
            # Peça 2.6a (2026-05-30): caps adaptativos por modo operacional.
            # Modo cognitive (default): turno-1 = 4000 (estado pré-2.5a.refact,
            #   identidade enxuta do EDP, retrieval semântico tem espaço,
            #   custo controlado). Diagnóstico em produção: cap 12000 fixo
            #   produzia inflação crônica em conversas técnicas contínuas
            #   (cada turno gera código long → janela sempre cheia → retrieval
            #   vira ruído → custo sobe consistentemente).
            # Modo sprint: turno-1 = 12000 (peça 2.5a.refact preservada para
            #   auto-referência em código longo). Trade-off pago consciente-
            #   mente quando usuário ativa /mode sprint.
            # Demais caps (-2 a -6) inalterados em ambos modos: validação da
            # peça 2.5a original preservada.
            current_mode = getattr(self, "_operational_mode", "cognitive")
            if current_mode == "sprint":
                CAPS_POR_POSICAO = [12000, 3000, 3000, 1500, 1500, 1500]
            else:  # cognitive (default)
                CAPS_POR_POSICAO = [4000, 3000, 3000, 1500, 1500, 1500]
            # Labels pela ordem cronológica (mais antigo primeiro → mais novo por último)
            LABELS_POR_DISTANCIA = [
                "6 turnos atrás",
                "5 turnos atrás",
                "4 turnos atrás",
                "3 turnos atrás",
                "2 turnos atrás",
                "turno anterior",
            ]
            try:
                if hasattr(self._memory, "episodic"):
                    real_entries = sorted(
                        [
                            e for e in self._memory.episodic.entries
                            if e.get("source_type") != "session_summary"
                        ],
                        key=lambda e: e.get("timestamp", 0),
                    )
                    recent_entries = real_entries[-JANELA_IMEDIATA_N:]
                    # Pega os labels finais (alinhados à direita) caso haja menos que N
                    n_recent = len(recent_entries)
                    labels_used = LABELS_POR_DISTANCIA[-n_recent:]
                    # Caps: do mais recente para o mais antigo
                    # recent_entries[-1] é o mais recente → cap[0]
                    # recent_entries[-2] é -2 → cap[1] etc.
                    # Construímos caps_used na mesma ordem cronológica dos entries
                    # (do mais antigo ao mais novo): reverte CAPS_POR_POSICAO
                    # para alinhar ao slice atual.
                    caps_reverse = list(reversed(CAPS_POR_POSICAO[:n_recent]))
                    # caps_reverse[i] é o cap para recent_entries[i] (i=0 mais antigo)

                    for entry, label, cap in zip(recent_entries, labels_used, caps_reverse):
                        txt = (entry.get("text") or "")[:cap]
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
                            "cap_aplicado": cap,
                            "source_type": entry.get("source_type"),
                        })
            except Exception as e:
                logger.debug("[retrieve_context] janela imediata falhou: %s", e)

            # ── Bloco ativo: espinha narrativa da linha de investigação ──────
            # Peça 2.5b (2026-05-28): Buraco 2 — ressuscitar block_id no retrieval.
            #
            # Diagnóstico: a infraestrutura de blocks (peça 2.0) gravava o
            # block_id em cada entry mas o retrieval IGNORAVA completamente
            # essa estrutura. Resultado: entries do MESMO bloco aberto (a
            # conversa viva atual), que escapavam da janela imediata por
            # estarem 7+ turnos atrás, caíam numa zona morta — recentes demais
            # para o retrieval semântico priorizar (peso temporal decai),
            # antigos demais para a janela imediata (que pega só 6).
            #
            # Solução: depois da janela imediata, busca o bloco ativo da
            # sessão e injeta os 3 entries MAIS ANTIGOS dele que ainda não
            # estão em seen_ids. Isso preserva o início da linha de
            # investigação atual sem reembedding e sem inflar contexto.
            #
            # Cap por entry: 1500 chars (compatível com turnos -4 a -6 da
            # janela imediata; bloco é "continuação cronológica" dela).
            #
            # Resiliência: tudo dentro de try/except. Bloco vazio, sem
            # session_id, ou erro em get_active_block → segue sem entries
            # do bloco (não quebra retrieval).
            BLOCO_ENTRIES_N = 3
            BLOCO_CAP_CHARS = 1500
            _debug_bloco: list[dict] = []
            try:
                if hasattr(self._memory, "blocks") and hasattr(self._memory, "episodic"):
                    # (P3) Estratégia (a) com fallback (b):
                    # 1º) tenta pegar edp_session_id do último entry da
                    #     janela imediata (já está em RAM)
                    # 2º) fallback: lê do lifetime via _get_edp_lifetime
                    edp_sid = None
                    if recent_entries:
                        edp_sid = recent_entries[-1].get("edp_session_id")
                    if not edp_sid:
                        try:
                            lifetime = self._memory._get_edp_lifetime()
                            edp_sid = lifetime.get("edp_session_id")
                        except Exception:
                            edp_sid = None

                    if edp_sid:
                        # Busca bloco ativo. get_active_block CRIA se não existir,
                        # mas isso é benigno: novo bloco vazio, lista vazia, segue.
                        active_block = self._memory.blocks.get_active_block(edp_sid)
                        entry_ids_no_bloco = list(active_block.entry_ids or [])

                        # Filtra os que JÁ estão na janela imediata (seen_ids).
                        # Sobram os entries do bloco ainda não cobertos.
                        ids_nao_vistos = [
                            eid for eid in entry_ids_no_bloco
                            if eid not in seen_ids
                        ]

                        if ids_nao_vistos:
                            # Mapeia id → entry para resolver rapidamente
                            mapa_entries = {
                                e.get("id"): e
                                for e in self._memory.episodic.entries
                                if e.get("id") in ids_nao_vistos
                            }
                            # Ordena por timestamp (mais antigo primeiro) e
                            # pega os N primeiros — os turnos do INÍCIO da
                            # linha de investigação, que a janela imediata
                            # (que pega o fim) não cobre.
                            entries_do_bloco = sorted(
                                [e for e in mapa_entries.values() if e],
                                key=lambda e: e.get("timestamp", 0),
                            )[:BLOCO_ENTRIES_N]

                            for entry in entries_do_bloco:
                                txt = (entry.get("text") or "")[:BLOCO_CAP_CHARS]
                                if not txt:
                                    continue
                                eid = entry.get("id")
                                if eid:
                                    seen_ids.add(eid)
                                blocks.append(f"[bloco atual] {txt}")
                                _debug_bloco.append({
                                    "id":          eid,
                                    "text":        txt,
                                    "source_type": entry.get("source_type"),
                                    "timestamp":   entry.get("timestamp"),
                                })
                            logger.debug(
                                "[retrieve_context] bloco ativo | %d entries injetados (de %d não vistos)",
                                len(entries_do_bloco), len(ids_nao_vistos),
                            )
            except Exception as e:
                logger.debug("[retrieve_context] bloco ativo falhou: %s", e)

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
