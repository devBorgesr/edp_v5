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
import re
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


def _format_gap(seconds: float) -> str:
    """
    Formata duração em string compacta para janela imediata cognitive.

    Faixas:
      < 60s            → "Xs"
      < 60min          → "Xmin"
      < 24h            → "XhYYmin" ou "Xh"
      >= 24h           → "X dias"

    Robusto: valores inválidos/negativos retornam "0s".
    Internamente o EDP preserva timestamp com microssegundos (para
    calibradores Pareto/Gauss/Bayes); aqui só humanizamos para o LLM.
    """
    try:
        s = float(seconds)
        if s < 0:
            return "0s"
        if s < 60:
            return f"{int(s)}s"
        minutes = s / 60.0
        if minutes < 60:
            return f"{int(minutes)}min"
        hours = minutes / 60.0
        if hours < 24:
            h = int(hours)
            m = int((hours - h) * 60)
            if m > 0:
                return f"{h}h{m:02d}min"
            return f"{h}h"
        days = hours / 24.0
        d = int(days)
        return f"{d} dia" if d == 1 else f"{d} dias"
    except Exception:
        return "0s"


def _format_temporal_label_cognitive(
    label: str,
    entry_ts: float,
    is_most_recent: bool,
    gap_seconds: float,
) -> str:
    """
    Renderização temporal híbrida para janela imediata em modo cognitive.

    Formato (Commit 3 — decisão Renato 02/06/2026):
      [turno anterior · 02/06/2026 17:48 · há 12min]      ← mais recente: gap até NOW
      [2 turnos atrás · 02/06/2026 17:45 · +3min até próx] ← gap até o próximo turno

    Componentes:
      - label original (preservado): "turno anterior", "N turnos atrás"
      - data DD/MM/YYYY HH:MM (segundo omitido para LLM; preservado interno)
      - gap formatado contextualmente:
        * "há X" para o turno mais recente (gap até NOW)
        * "+X até próx" para turnos anteriores (gap até próximo)

    Aplicação:
      - Apenas modo cognitive (decisão D2=α)
      - Sprint mantém label simples (sem data/gap)

    Robustez: timestamp inválido → retorna label original sem decoração.
    """
    try:
        ts = float(entry_ts)
        if ts <= 0:
            return label
        dt = time.localtime(ts)
        date_str = (
            f"{dt.tm_mday:02d}/{dt.tm_mon:02d}/{dt.tm_year} "
            f"{dt.tm_hour:02d}:{dt.tm_min:02d}"
        )
        gap_str = _format_gap(gap_seconds)
        if is_most_recent:
            return f"{label} · {date_str} · há {gap_str}"
        return f"{label} · {date_str} · +{gap_str} até próx"
    except Exception:
        return label


# ── Commit 3c.α (Renato, 04/06/2026) ─────────────────────────────────────────
# Bloco histórico cronológico compacto para janela imediata cognitive.
#
# Motivação: cognitive tem JANELA_IMEDIATA_N=6 turnos + cap=4000 no turno
# mais recente. Em sessões longas (10+ turnos no dia), turnos antigos
# saem da janela e retrieval semântico falha em perguntas meta-conver-
# sacionais ("o que conversamos hoje?", "quais foram as perguntas anterio-
# res?"). Similaridade cosseno entre meta-pergunta e tópicos diversos
# (saudação, técnica, civilidade) é baixa → retrieval ignora.
#
# Solução: bloco histórico SEMPRE presente em cognitive listando até
# 12 perguntas (Q only, não Q+A) da SESSÃO ATUAL, em ordem cronológica
# reversa (mais recente primeiro). Custo: ~300-500 tokens fixos.
#
# Detecção de sessão atual: gap temporal > 4h entre turnos cronológicos
# = fronteira de sessão. Algoritmo puro sobre timestamps existentes
# (sem schema novo). Commit 3c.β no futuro adiciona `session_marker`
# persistente para boost no retrieval semântico.
#
# Aplicação: APENAS cognitive (decisão Renato). Sprint preserva
# comportamento atual (janela imediata já generosa em sprint).
SESSION_GAP_THRESHOLD_SEC = 4 * 3600   # 4h sem atividade = fim de sessão
HISTORICO_MAX_TURNS = 12               # cap de turnos no bloco histórico
HISTORICO_TRUNCATE_CHARS = 80          # cap de chars por linha (pergunta)

# Caps da janela imediata por modo operacional. Estava como literal DENTRO de
# `_retrieve_context` (peça 2.5a); içado aqui em 12/08 porque a telemetria de
# formato precisa do MESMO mapa — e duplicar `mode -> caps` em dois lugares é
# exatamente o antipadrão que a auditoria de constantes catalogou
# (`SESSION_GAP_THRESHOLD_SEC` definido duas vezes, concordando por
# coincidência de manutenção). Uma fonte, dois leitores.
#
# Motivo dos valores (preservado da peça 2.5a): cognitive tem turno-1 enxuto
# (4000) porque cap fixo de 12000 produzia inflação crônica em conversa técnica
# contínua; sprint paga 12000 conscientemente para auto-referência em código
# longo. Caps -2 a -6 iguais nos dois modos — validação da 2.5a preservada.
CAPS_POR_MODO = {
    "sprint":    (12000, 3000, 3000, 1500, 1500, 1500),
    "cognitive": (4000,  3000, 3000, 1500, 1500, 1500),
}

FORMAT_STATE_SCHEMA = 1


def snapshot_formato(modo: str) -> dict:
    """
    Fotografa o regime de formato NO INSTANTE em que o turno nasce.

    É snapshot, não referência: a pergunta que a Fase 2 vai fazer é "como o EDP
    estava quando esta observação nasceu", não "como o EDP está agora". Guardar
    um ponteiro para estrutura viva responderia a segunda por acidente — e o
    dado já teria sido gravado com o valor errado sem ninguém notar.

    Grava o ESTADO EFETIVO, não só a configuração que o implica: `modo="sprint"`
    obriga quem analisar a reconstruir "logo, o cap era 12000"; `caps=(12000,…)`
    já responde. Configuração é causa indireta; o que entrou no prompt é o que
    interessa.

    `EDP_USE_CTX_MGR` entra explicitamente porque é lido de `os.environ` direto
    aqui (`stream_chat`), não de `config.py` — o teste de completude varre
    `config.py` e NÃO o pegaria. Ele troca `_build_enriched_context` pelo
    caminho de fallback, que monta o prompt de outro jeito: é a flag mais
    disruptiva do conjunto e a única fora do módulo de config.
    """
    flags = {}
    try:
        from . import config as _cfg
        for nome in _cfg.FORMAT_STATE_FLAGS:
            flags[nome] = bool(getattr(_cfg, nome, None))
    except Exception as e:
        logger.debug("[formato] leitura de flags falhou: %s", e)
    flags["EDP_USE_CTX_MGR"] = os.environ.get("EDP_USE_CTX_MGR", "1") == "1"

    return {
        "schema_version": FORMAT_STATE_SCHEMA,
        "mode":           modo,
        "caps":           list(CAPS_POR_MODO.get(modo, CAPS_POR_MODO["cognitive"])),
        "flags":          flags,
    }


# LIMITE DECLARADO: este snapshot captura o regime de RUNTIME (modo, flags,
# caps), não o de CÓDIGO. Constantes que só mudam com deploy — `JANELA_IMEDIATA_N`
# (local a `_retrieve_context`), os caps de `BLOCO_CAP_CHARS`, o texto dos
# templates de prompt — não podem variar entre duas amostras da mesma build, e
# por isso não ajudam a estratificar. Mas mudam entre builds, e nada aqui
# detecta isso: duas amostras de versões diferentes do código podem ter hashes
# de formato IDÊNTICOS. Coleta que atravesse um deploy que mexa em composição
# de prompt precisa ser cortada na data do deploy à mão. Fechar isso exigiria
# carimbar o commit em cada amostra — decisão para a Fase 2, não para esta.


def _detect_sessao_atual(entries_sorted_asc: list, now_ts: float) -> list:
    """
    Identifica as entries que pertencem à SESSÃO ATUAL via gap temporal.

    Entrada: lista de entries ORDENADA por timestamp ascendente (mais antigo
    primeiro). now_ts é o instante atual.

    Algoritmo:
      - Percorre entries do FINAL (mais recente) para o INÍCIO
      - Inclui na sessão atual enquanto gap entre entries consecutivos
        for <= SESSION_GAP_THRESHOLD_SEC
      - Para no primeiro gap > threshold (fronteira de sessão anterior)

    Retorna: sublista cronológica ascendente das entries da sessão atual.

    Robustez: timestamps inválidos (None, 0) tratam como gap infinito
    (não pertencem à sessão atual).
    """
    if not entries_sorted_asc:
        return []
    try:
        # Percorre do mais recente para o mais antigo coletando sessão atual
        sessao_atual_reverse = []
        for i in range(len(entries_sorted_asc) - 1, -1, -1):
            e_atual = entries_sorted_asc[i]
            ts_atual = e_atual.get("timestamp")
            if ts_atual is None or float(ts_atual) <= 0:
                # timestamp inválido: encerra sessão aqui
                break
            ts_atual_f = float(ts_atual)
            if i == len(entries_sorted_asc) - 1:
                # Mais recente: gap até NOW deve ser razoável; se > threshold,
                # mesmo assim incluímos (é o turno atual ou imediatamente anterior)
                sessao_atual_reverse.append(e_atual)
                continue
            # Para entries anteriores ao mais recente: verifica gap até PRÓXIMO
            e_proximo = entries_sorted_asc[i + 1]
            ts_proximo = e_proximo.get("timestamp")
            if ts_proximo is None or float(ts_proximo) <= 0:
                break
            gap = float(ts_proximo) - ts_atual_f
            if gap > SESSION_GAP_THRESHOLD_SEC:
                # Fronteira de sessão: e_atual pertence à sessão ANTERIOR
                break
            sessao_atual_reverse.append(e_atual)
        # Reverte para ordem cronológica ascendente
        return list(reversed(sessao_atual_reverse))
    except Exception:
        return []


def _build_historico_cronologico_compacto(
    entries_sorted_asc: list,
    current_mode: str,
    now_ts: float,
) -> Optional[str]:
    """
    Constrói o bloco histórico cronológico compacto para janela imediata.

    APENAS em modo cognitive (decisão Renato 04/06/2026).
    Sprint retorna None (preserva comportamento atual).

    Formato emitido:
        [histórico cronológico — últimos N turnos da sessão atual]
        07:02 Q: "ok obrigado"
        07:01 Q: "voce nao lembra de nada..."
        06:59 Q: "o que elas podem fazer no mundo real"
        ...

    Estrutura:
      - Apenas Q (pergunta do usuário), sem A (resposta do modelo)
      - Truncado em 80 chars por linha
      - Mais recente primeiro (ordem cronológica reversa)
      - Cap de 12 turnos máximo
      - Apenas turnos da SESSÃO ATUAL (gap < 4h entre eles)

    Robustez: lista vazia, mode != cognitive, ou exceção → retorna None
    (caller deve verificar e não inserir bloco no contexto).
    """
    if current_mode != "cognitive":
        return None
    if not entries_sorted_asc:
        return None
    try:
        # 1. Filtra apenas entries da sessão atual
        sessao = _detect_sessao_atual(entries_sorted_asc, now_ts)
        if not sessao:
            return None

        # 2. Filtra entries válidas para o histórico cronológico.
        #
        # CORREÇÃO (Commit 3c.α-fix2, Renato 04/06/2026):
        # A arquitetura do EDP grava Q+A em UM ÚNICO entry com
        # source_type=llm_response (formato: "Q: <pergunta>\nA: <resposta>",
        # ver websocket.py:482 e llm_adapter.py:2446). Filtro anterior
        # exigia source_type=user_input, o que excluía TODAS as conversas
        # reais. Resultado: bloco histórico sempre retornava None.
        #
        # Decisão arquitetural confirmada (llm_adapter.py:1601-1606):
        # NÃO existe entry separado de user_input — gravar separado causaria
        # duplicação e envenenamento do retrieval por similaridade.
        #
        # Filtro novo: aceita todos source_types EXCETO os que não são
        # conversas reais (session_summary = resumo automático;
        # meta_conversation = governança anti-loop interna).
        entries_validas = [
            e for e in sessao
            if e.get("source_type") not in ("session_summary", "meta_conversation")
        ]
        if not entries_validas:
            return None

        # 3. Pega as últimas N (mais recentes)
        ultimas = entries_validas[-HISTORICO_MAX_TURNS:]

        # 4. Constrói as linhas em ordem cronológica REVERSA (mais recente primeiro)
        #    Extrai APENAS a parte Q: do texto combinado Q+A.
        linhas = []
        # Regex confiável: padrão arquitetural fixo "Q: ... \nA: ..."
        # (confirmado em llm_adapter.py:2446 e websocket.py linha equivalente).
        # re.DOTALL permite que . case com \n dentro da pergunta multi-linha.
        Q_EXTRACTOR = re.compile(r"^Q:\s*(.+?)\s*\nA:\s*", re.DOTALL)
        for entry in reversed(ultimas):
            ts = entry.get("timestamp")
            if ts is None or float(ts) <= 0:
                continue
            dt = time.localtime(float(ts))
            hora_str = f"{dt.tm_hour:02d}:{dt.tm_min:02d}"
            texto = (entry.get("text") or "").strip()
            if not texto:
                continue
            # Extrai APENAS a pergunta Q (não a resposta A) do texto combinado
            match = Q_EXTRACTOR.match(texto)
            if match:
                texto_limpo = match.group(1).strip()
            else:
                # Fallback: entry sem padrão Q+A (raro, possivelmente legado).
                # Usa texto inteiro, com remoção defensiva de prefixos.
                texto_limpo = texto.lstrip("Q:").lstrip(":").strip()
            # Trunca em 80 chars, adicionando ... se cortou
            if len(texto_limpo) > HISTORICO_TRUNCATE_CHARS:
                texto_limpo = texto_limpo[:HISTORICO_TRUNCATE_CHARS - 3] + "..."
            # Escapa aspas duplas para não quebrar formato
            texto_limpo = texto_limpo.replace('"', "'")
            # Normaliza quebras de linha internas (perguntas multi-linha viram 1 linha)
            texto_limpo = texto_limpo.replace("\n", " ").replace("\r", " ")
            texto_limpo = re.sub(r"\s+", " ", texto_limpo)
            linhas.append(f'{hora_str} Q: "{texto_limpo}"')

        if not linhas:
            return None

        # 5. Monta bloco final
        n_turnos = len(linhas)
        cabecalho = (
            f"[histórico cronológico — últimos {n_turnos} turno"
            f"{'s' if n_turnos != 1 else ''} da sessão atual]"
        )
        return cabecalho + "\n" + "\n".join(linhas)
    except Exception:
        return None


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
    max_tokens:  int         = 8192  # 2026-05-31 (diagnóstico α): subido de
                                     # 4096 para 8192. Investigação revelou
                                     # via stop_reason=max_tokens que cortes em
                                     # Java 21 sectioned (Opus + Sonnet) eram
                                     # causados pelo próprio EDP, não pela API.
                                     # Headers da Anthropic mostraram
                                     # out_remaining=10000/10000 no momento do
                                     # corte — não era rate limit, era teto
                                     # interno. 8192 cobre seções técnicas
                                     # densas (Resilience4j YAML + sealed
                                     # interfaces + orchestrator: ~5500 toks
                                     # observados). Limite por requisição da
                                     # API: 64K Sonnet, 32K Opus — 8192 é
                                     # seguro. Custo: só conta tokens gerados,
                                     # não o teto. Comentário original abaixo:
                                     # Peça 2.5e (2026-05-29): subido de 2048
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
                # Dívida #47 (12/06/2026): modelo lido NA HORA da chamada,
                # não no init do provider. Faz o override temporário da
                # câmara (_llm_call_for_chamber) finalmente ter efeito.
                model=self._cfg.model,
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

    def complete_ex(self, prompt: str, system: str = ""):
        """
        Fase 1 — Câmara Adaptativa (12/06/2026): como complete(), mas
        retorna o CompletionResponse COMPLETO (text + metrics.cost_usd
        real + modelo efetivo). Disponível só na rota Anthropic; outras
        rotas retornam None e o caller usa complete() como fallback.
        """
        if self._cfg.provider == LLMProvider.ANTHROPIC:
            from .llm.providers import CompletionRequest, Message, Role
            req = CompletionRequest(
                messages=[Message(Role.USER, prompt)],
                system=system,
                temperature=self._cfg.temperature,
                max_tokens=self._cfg.max_tokens,
                model=self._cfg.model,  # #47: lido na hora da chamada
            )
            return self._anthropic_provider.complete(req)
        return None

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
        # Commit 1 dos Dois Exocórtices (2026-05-31): troca scope da memória
        # ao mudar de modo. cognitive → biblioteca longitudinal (hipocampo).
        # sprint → biblioteca de trabalho (gânglios da base). Isolamento
        # arquitetural que resolve P1 (memória residual entre sessões).
        #
        # BUG FIX (2026-05-31 11:35): sincronizar scope em AMBAS as instâncias
        # de MemoryStore (self._memory do EDPRuntime + a do registry). Eram
        # instâncias diferentes — antes só registry trocava de scope, mas
        # retrieval usa self._memory. Vazamento cognitive↔sprint detectado
        # em produção (modelo em sprint encontrava memórias do cognitive).
        #
        # Estratégia: tenta primeiro self._memory (que é usado por retrieval).
        # Em paralelo, atualiza a instância do registry para coerência cross-
        # endpoint. Dívida #24 (eliminar duplicação) fica para revisão futura.
        _scopes_synced = 0
        try:
            if self._memory is not None and hasattr(self._memory, "set_scope"):
                self._memory.set_scope(mode_lower)
                _scopes_synced += 1
                logger.info(
                    "[mode] scope sincronizado em self._memory (retrieval ativo): %s",
                    mode_lower,
                )
        except Exception as e:
            logger.warning(
                "[mode] falha ao sincronizar scope em self._memory: %s", e
            )
        try:
            from .runtime.registry import get_memory, is_valid
            _registry_mem = get_memory(self.session_id)
            # Só atualiza se for instância DIFERENTE de self._memory
            if (is_valid(_registry_mem)
                and hasattr(_registry_mem, "set_scope")
                and _registry_mem is not self._memory):
                _registry_mem.set_scope(mode_lower)
                _scopes_synced += 1
                logger.info(
                    "[mode] scope sincronizado em registry.memory (endpoints): %s",
                    mode_lower,
                )
        except Exception as e:
            logger.warning(
                "[mode] falha ao sincronizar scope em registry.memory: %s", e
            )
        if _scopes_synced == 0:
            logger.warning(
                "[mode] NENHUMA instância de memory sincronizada — "
                "isolamento pode estar quebrado"
            )
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

        # ── Commit 3b (Renato, 04/06/2026) ────────────────────────────────────
        # Pareto event logger: emite mode_switched para telemetria. Permite
        # análise de Bayes (qual modo é dominante? quando troca?) e correlação
        # com qualidade de retrieval.
        try:
            from .runtime.pareto_store import emit_mode_switched
            emit_mode_switched(
                session_id=str(self.session_id),
                from_mode=previous,
                to_mode=mode_lower,
            )
        except Exception as e:
            logger.debug("[mode] pareto emit falhou: %s", e)

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

        Commit 3b-task (Renato, 04/06/2026): adiciona started_at (epoch float)
        para cálculo de duration_sec quando task_completed é emitido.
        """
        previous = self._task_anchor
        self._task_anchor = {
            "challenge": (challenge or "")[:2000],
            "sections_delivered": [],
            "expected_total": None,
            "started_at_turn": None,  # opcional, debug
            "started_at": _now(),     # Commit 3b-task: timestamp para duration_sec
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

            # ── Commit 3b-task (Renato, 04/06/2026) ──────────────────────────
            # Pareto event logger: task_started disparado APÓS primeira seção
            # entregue (que revela expected_total). Antes desse momento
            # expected_total=None e evento perderia informação. Marca a
            # entry inicial efetiva da tarefa.
            try:
                from .runtime.pareto_store import emit_task_started
                emit_task_started(
                    session_id=str(self.session_id),
                    expected_total=int(total),
                )
            except Exception as e:
                logger.debug("[task] pareto emit_task_started falhou: %s", e)
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

            # ── Commit 3b-task (Renato, 04/06/2026) ──────────────────────────
            # Pareto event logger: task_completed com duração total. Calculada
            # via started_at (set em start_task). Lê ANTES de _task_anchor=None
            # para não perder informação.
            try:
                from .runtime.pareto_store import emit_task_completed
                started_at = self._task_anchor.get("started_at") or _now()
                duration = _now() - float(started_at)
                emit_task_completed(
                    session_id=str(self.session_id),
                    n_secoes=int(len(delivered_ns)),
                    duration_sec=float(duration),
                )
            except Exception as e:
                logger.debug("[task] pareto emit_task_completed falhou: %s", e)

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

        # Peça 2.6f (07/08/2026). Import local, como em EDP_GRAPH_VIEWER:
        # falha de import não pode derrubar a formatação da âncora.
        try:
            from .config import EDP_ANCHOR_COMPACT as _anchor_compact
        except Exception:
            _anchor_compact = False

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
                # Peça 2.6f (07/08/2026): com EDP_ANCHOR_COMPACT=1 esta linha
                # sai. Medido: custa 9.100 de 11.486 chars em 10 seções × 6
                # decisões × 120 chars (79% do bloco), e o consolidado abaixo
                # já lista cada chave com sua seção de origem. O que se perdia
                # ao omitir — o registro de RE-decisão — passa a ser carregado
                # pelo consolidado como cadeia de mudança.
                if s.get("decisions") and not _anchor_compact:
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
                        elif _anchor_compact and str(v) != str(consolidated[k]["value"]):
                            # Peça 2.6f: a chave foi RE-decidida numa seção
                            # posterior. Sem isto, omitir a linha por-seção
                            # perderia a mudança — que é justamente o sinal
                            # que a continuidade de decisão precisa ver.
                            # Cresce só quando há mudança real, não por seção.
                            consolidated[k].setdefault("changes", []).append(
                                {"value": v, "section": s["n"]}
                            )
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
                    # Peça 2.6f: cadeia de re-decisão, quando houve
                    for ch in info.get("changes", []):
                        lines.append(
                            f"    ↳ ALTERADA na Seção {ch['section']}: "
                            f"{str(ch['value'])[:200]}"
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

        # ── Commit 3b (Renato, 04/06/2026) ────────────────────────────────────
        # Pareto event logger: correlation_id por turno (mesmo padrão do
        # stream_chat). Necessário no caminho non-streaming também porque
        # session_summary, validações internas e chamadas externas via API
        # podem usar chat() em vez de stream_chat().
        try:
            from .runtime.pareto_store import (
                new_correlation_id, set_current_correlation_id,
                set_current_format_state,
            )
            _cid = new_correlation_id()
            set_current_correlation_id(_cid)
            # Fase 1: snapshot do regime de formato, na mesma thread e no mesmo
            # instante do correlation_id — os dois respondem "que turno é este"
            # e "em que regime ele rodou", e separá-los abriria janela para um
            # descrever um turno e o outro descrever o seguinte.
            set_current_format_state(
                snapshot_formato(getattr(self, "_operational_mode", "cognitive"))
            )
        except Exception as e:
            logger.debug("[chat] pareto correlation_id falhou: %s", e)

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

        # ── Commit 3b (Renato, 04/06/2026) ────────────────────────────────────
        # Pareto event logger: gera correlation_id único para este turno.
        # Eventos emitidos pelos hooks (memory_added, memory_accessed) ao
        # longo do turno serão correlacionados via este ID — preparação para
        # Bayes condicional (P(memory_accessed | mode_switched) etc).
        # thread-local: cada chamada a stream_chat (executado em thread
        # separada pelo FastAPI) tem seu próprio correlation_id.
        try:
            from .runtime.pareto_store import (
                new_correlation_id, set_current_correlation_id,
                set_current_format_state,
            )
            _cid = new_correlation_id()
            set_current_correlation_id(_cid)
            set_current_format_state(  # Fase 1 — ver nota em chat()
                snapshot_formato(getattr(self, "_operational_mode", "cognitive"))
            )
        except Exception as e:
            logger.debug("[stream_chat] pareto correlation_id falhou: %s", e)

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
        # ── Dívida #46b (14/06/2026): usar a MESMA instância de MemoryStore do
        # registry — a que o websocket grava (get_memory) e o cog_dec lê — em vez
        # de criar uma instância PRÓPRIA isolada. Antes, MemoryStore(session_id)
        # criava a instância "A" do adapter, congelada no estado do disco no boot:
        # a janela imediata E o retrieve (tudo que usa self._memory) liam de A e
        # NUNCA viam os turnos da sessão viva, gravados na instância "B" via
        # get_memory. Resultado: continuidade quebrada para tópicos novos (turno
        # 7/GIN). get_memory é idempotente e usa o mesmo RLock reentrante por
        # sessão, então a criação aninhada (get_runtime → __init__ → get_memory)
        # é segura. Fallback para instância própria se o registry falhar.
        try:
            from .runtime.registry import get_memory, is_valid
            _mem = get_memory(self.session_id)
            if is_valid(_mem):
                self._memory = _mem
            else:
                from .memory import MemoryStore
                self._memory = MemoryStore(self.session_id)
                logger.warning(
                    "[EDPRuntime] #46b: registry.get_memory inválido — usando "
                    "instância própria (continuidade da sessão pode degradar)"
                )
        except Exception as e:
            logger.warning("[EDPRuntime] MemoryStore indisponível: %s", e)

        try:
            from .pipeline import run_pipeline
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
        # Fase 1 (12/08/2026): a câmara roda DENTRO do turno, na MESMA thread —
        # então herdaria o `format_state` do turno por acidente. Isso seria pior
        # que não rotular: o prompt da câmara tem composição própria (system de
        # refutação, sem retrieval nem âncora), e carimbá-lo com o regime do
        # prompt principal afirmaria algo falso. Limpo antes e restauro depois:
        # a amostra da câmara sai com `format_state=None` e a Fase 2 a exclui
        # do estrato do turno em vez de contaminá-lo.
        _fmt_turno = None
        try:
            from .runtime.pareto_store import (
                get_current_format_state as _get_fmt,
                set_current_format_state as _set_fmt,
            )
            _fmt_turno = _get_fmt()
            _set_fmt(None)
        except Exception as e:
            logger.debug("[camara] isolamento de format_state falhou: %s", e)
            _set_fmt = None
        try:
            # Override temporário do modelo para esta chamada específica
            original_model = self._client._cfg.model
            self._client._cfg.model = modelo
            try:
                # Fase 1 (12/06/2026): rota com métricas — custo REAL do
                # juiz entra no camara_outcome. Fallback: complete normal.
                resp_ex = None
                try:
                    resp_ex = self._client.complete_ex(user, system=system)
                except Exception:
                    resp_ex = None
                if resp_ex is not None:
                    text = resp_ex.text or ""
                    cost = float(getattr(resp_ex.metrics, "cost_usd", 0.0) or 0.0)
                else:
                    text = self._client.complete(user, system=system)
                    cost = 0.0
            finally:
                self._client._cfg.model = original_model
                if _set_fmt is not None:
                    _set_fmt(_fmt_turno)   # devolve o regime ao turno
            latency_ms = (_time.perf_counter() - t0) * 1000.0
            return {
                "text": text or "",
                "cost_usd": cost,
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
            CAPS_POR_POSICAO = list(
                CAPS_POR_MODO.get(current_mode, CAPS_POR_MODO["cognitive"])
            )
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

                    # ── Commit 3c.α (Renato, 04/06/2026) ───────────────────────
                    # Bloco histórico cronológico compacto: lista até 12 turnos
                    # da SESSÃO ATUAL (gap < 4h entre eles), apenas perguntas
                    # do usuário, truncadas em 80 chars, mais recente primeiro.
                    # APENAS em cognitive (sprint não recebe).
                    # Resolve UX: meta-pergunta ("o que conversamos hoje?") que
                    # não casa semanticamente via retrieval cosseno e perde
                    # turnos fora da janela imediata (N=6).
                    try:
                        historico_block = _build_historico_cronologico_compacto(
                            real_entries,
                            current_mode,
                            _now(),
                        )
                        if historico_block:
                            blocks.append(historico_block)
                            _debug_immediate.append({
                                "tipo":         "historico_cronologico",
                                "n_linhas":     historico_block.count("\n"),
                                "tamanho_chars": len(historico_block),
                            })
                    except Exception as e:
                        logger.debug(
                            "[retrieve_context] historico cronologico falhou: %s", e
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

                    # ── Commit 3 (renderização temporal cognitive) ────────────
                    # Decisão Renato 02/06/2026: em modo cognitive, label
                    # ganha data+hora+gap formatado (Opção 1 inline, formato
                    # híbrido α). Sprint preserva label simples (decisão D2=α).
                    # Internamente o timestamp microssegundos é preservado
                    # nas memórias para uso futuro pelos calibradores
                    # (Pareto/Gauss/Bayes — Commits 3-5).
                    _now_for_gap = _now()
                    for i, (entry, label, cap) in enumerate(
                        zip(recent_entries, labels_used, caps_reverse)
                    ):
                        txt = (entry.get("text") or "")[:cap]
                        if not txt:
                            continue
                        eid = entry.get("id")
                        if eid:
                            seen_ids.add(eid)

                        # Label final depende do modo:
                        if current_mode == "cognitive":
                            entry_ts = float(entry.get("timestamp") or 0.0)
                            is_most_recent = (i == n_recent - 1)
                            if is_most_recent:
                                gap = max(0.0, _now_for_gap - entry_ts) if entry_ts > 0 else 0.0
                            else:
                                next_ts = float(
                                    recent_entries[i + 1].get("timestamp") or 0.0
                                )
                                gap = max(0.0, next_ts - entry_ts) if (entry_ts > 0 and next_ts > 0) else 0.0
                            final_label = _format_temporal_label_cognitive(
                                label, entry_ts, is_most_recent, gap
                            )
                        else:  # sprint: comportamento legado preservado
                            final_label = label

                        blocks.append(f"[{final_label}] {txt}")
                        # Debug: registra o que entrou na janela imediata
                        _debug_immediate.append({
                            "id":           eid,
                            "label":        label,          # label original (sem data)
                            "label_final":  final_label,    # label efetivo (com data se cognitive)
                            "text":         txt,
                            "cap_aplicado": cap,
                            "source_type":  entry.get("source_type"),
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

            # ── exp017 Fase 0 — EDP_RETRIEVE_SHUFFLE (default OFF) ───────────
            # Controle negativo (H2, PRE_REGISTRO_EXP017.md): embaralha a
            # ORDEM do top-k já pronto ANTES do loop abaixo consumi-lo —
            # ZERO remoção, mesmo conjunto. Ponto fixado no T1b (RELATORIO_
            # T1_EXP017.md): downstream (ContextWindowManager.build(),
            # context_window_manager.py:321-327) é guloso e sensível à ordem
            # — reordenar aqui pode mudar quais itens sobrevivem ao corte de
            # budget, sem tocar no conjunto retornado pelo retrieve.
            # Instrumento de MEDIÇÃO — nunca produção; mutuamente exclusiva
            # com EDP_RETRIEVE_DEDUP / EDP_RETRIEVE_RANDOM_DROP (Fase 1, exp017
            # T3) — guard compartilhado em
            # config.resolve_retrieve_instrumentation_exp017.
            try:
                from .config import (
                    EDP_RETRIEVE_SHUFFLE, EDP_SHUFFLE_SEED,
                    EDP_RETRIEVE_DEDUP as _dd017, EDP_RETRIEVE_RANDOM_DROP as _rd017,
                    resolve_retrieve_instrumentation_exp017 as _resolve017,
                )
                _mode017 = _resolve017(_dd017, EDP_RETRIEVE_SHUFFLE, _rd017)
                if _mode017 == "shuffle" and len(results) > 1:
                    import hashlib
                    import random as _random
                    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
                    rng = _random.Random(f"{EDP_SHUFFLE_SEED}:{query_hash}")
                    results = list(results)
                    rng.shuffle(results)
            except Exception as _e_shuffle:
                logger.debug("[exp017] shuffle falhou (ignorado): %s", _e_shuffle)
            # exp011: coleta paralela dos blocos de MEMORIA RECUPERADA (mesmos
            # objetos str de `blocks`) para o split metadados/retrieval no
            # _build_enriched_context. Bookkeeping puro — nao altera `blocks`.
            _sim_blocks: list = []
            # exp017 Fase 0 (T4): id(bloco) -> entry_id, mesmo par mecânico de
            # `_sim_blocks` (RELATORIO_T1_EXP017.md, ponto (ii)) — propagação
            # read-only para resolver IDs do retrieval_kept após o builder
            # (ctx.retrieval é List[str], sem ID nenhum). Não altera `blocks`
            # nem nenhum fluxo existente.
            _sim_id_map: dict = {}
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
                _b = prefix + txt
                blocks.append(_b)
                _sim_blocks.append(_b)  # exp011: mesmo objeto, split por id()
                if eid:
                    _sim_id_map[id(_b)] = eid  # exp017 T4: propagação read-only
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
            #
            # Commit 1 dos Dois Exocórtices (2026-05-31), decisão α:
            # CoOccurrenceTracker é primariamente cognitivo (hipocampo).
            # Em modo sprint (gânglios da base), retrieval ocorre em memória
            # de trabalho — registrar essas co-ocorrências contaminaria o
            # perfil acumulado do exocórtex cognitivo. Skip se modo != cognitive.
            _allow_co_occurrence = (self._operational_mode == "cognitive")
            if not _allow_co_occurrence:
                logger.debug(
                    "[co_occurrence] skip — modo=%s (tracker ativo apenas em cognitive)",
                    self._operational_mode,
                )
            if (self._co_occurrence and len(co_occurrence_ids) >= 2
                    and _allow_co_occurrence):
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

            self._last_similarity_blocks = _sim_blocks  # exp011
            self._last_similarity_ids = _sim_id_map      # exp017 T4
            return blocks, len(blocks)
        except Exception as e:
            logger.debug("[retrieve_context] erro: %s", e)
            self._last_similarity_blocks = []  # exp011
            self._last_similarity_ids = {}      # exp017 T4
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
            max_recent=5,
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
                # ── Dívida #46 (14/06/2026): JANELA IMEDIATA ÍNTEGRA ──────────
                # Os N=4 turnos CONVERSACIONAIS mais recentes da SESSÃO VIVA
                # entram COMPLETOS (Q+A), sem truncar. Orçamento folga (~28k
                # tokens livres): cabem inteiros — o resumo semântico no teto
                # fica para quando o budget apertar.
                #
                # ── Dívida #46c (14/06/2026): UNIFICAR a definição de "turno
                # recente" com a do histórico cronológico, em vez de recalcular.
                # Duas correções nascem disto:
                #   (1) Usar _detect_sessao_atual (gap 4h) — a MESMA função que o
                #       histórico cronológico usa e que acerta os turnos de hoje.
                #       Antes, entries[-N:] global misturava sessões: turnos de
                #       4h atrás invadiam a janela e empurravam os de agora.
                #   (2) Selecionar turno por FORMA (texto começa com "Q:" e tem
                #       "A:"), NÃO por source_type. O classificador rotulou o
                #       turno do Luhn como "meta_conversation" (rótulo errado →
                #       Dívida #46d), e o filtro por categoria o excluía. O
                #       form-check inclui o turno apesar do rótulo. Verificado
                #       que nenhum gerador não-conversacional emite texto
                #       começando com "Q:" (session_summary → "[session_summary]",
                #       cog_dec → JSON), então a forma isola turnos reais sem
                #       depender de a lista de source_types estar completa.
                #
                # Recibo histórico: o _build_historico_cronologico_compacto já
                # sofreu este exato padrão (Commit 3c.α-fix2, 04/06/2026) — filtrar
                # "o que é um turno" por source_type excluía conversas reais. A
                # lição não persistiu para o código do ζ; #46c a reaplica.
                _Q_RE = re.compile(r"^\s*Q:\s*.+\bA:\s*", re.DOTALL)

                def _eh_turno_conversacional(e: dict) -> bool:
                    return bool(_Q_RE.match((e.get("text", "") or "")))

                sessao_viva = _detect_sessao_atual(entries, now_ts)
                turnos_conv = [e for e in sessao_viva if _eh_turno_conversacional(e)]
                # Fallback defensivo: se a sessão viva não tiver turnos com forma
                # Q/A (ex: sessão recém-iniciada só com summaries), relaxa para a
                # base inteira filtrada por forma — evita janela vazia, sem voltar
                # a depender de source_type.
                if not turnos_conv:
                    turnos_conv = [e for e in entries if _eh_turno_conversacional(e)]

                N_RECENT_INTEGRO = 4
                for e in turnos_conv[-N_RECENT_INTEGRO:]:
                    txt = (e.get("text", "") or "")   # ÍNTEGRO — sem [:300]
                    if not txt:
                        continue
                    ts  = e.get("timestamp")
                    rel = _format_relative_time(ts, now_ts) if ts else ""
                    prefix = f"[{rel}] " if rel else ""
                    recent_turns.append(prefix + txt)

                logger.info(
                    "[ctx] janela imediata INTEGRA #46/#46c | turnos=%d (N=%d) | "
                    "sessao_viva=%d entries",
                    len(recent_turns), N_RECENT_INTEGRO, len(sessao_viva),
                )    
                
                # Primeiros 2 (anchors): contexto inicial
                for e in entries[:5]:
                    txt = (e.get("text", "") or "")[:300]
                    if not txt:
                        continue
                    ts  = e.get("timestamp")
                    rel = _format_relative_time(ts, now_ts) if ts else ""
                    prefix = f"[{rel}] " if rel else ""
                    all_history.append(prefix + txt)

                # ── Commit 3c.α-fix (Renato, 04/06/2026) ──────────────────────
                # CORREÇÃO: o caminho REAL de produção é _build_enriched_context,
                # NÃO _retrieve_context (que é apenas fallback). Implementação
                # inicial do 3c.α em _retrieve_context não tinha efeito visível
                # porque ContextWindowManager constrói recent_turns separadamente
                # neste bloco e bypassa o retrieval do fallback.
                #
                # Solução: insere o bloco histórico cronológico como ÚLTIMO
                # item de recent_turns. ContextWindowManager faz
                # recent_turns[-max_recent:] (max_recent=3 default), então
                # APPEND no final garante que entra no slice. Apenas em
                # cognitive — função retorna None em sprint (decisão D2=α).
                # Bloco contém até 12 perguntas da sessão atual (gap <4h),
                # truncadas em 80 chars, ordem reversa cronológica.
                try:
                    current_mode = getattr(self, "_operational_mode", "cognitive")
                    historico_block = _build_historico_cronologico_compacto(
                        entries,
                        current_mode,
                        now_ts,
                    )
                    if historico_block:
                        recent_turns.append(historico_block)
                        logger.info(
                            "[ctx] historico_cronologico injetado | mode=%s | "
                            "linhas=%d | chars=%d",
                            current_mode,
                            historico_block.count("\n"),
                            len(historico_block),
                        )
                except Exception as e:
                    logger.debug(
                        "[ctx] historico_cronologico falhou: %s", e
                    )
                # ── fim Commit 3c.α-fix ───────────────────────────────────────

        except Exception:
            pass

        # ── Dívida #46 (opção b, 14/06/2026): a janela imediata agora vem
        # ÍNTEGRA via recent_turns (acima). Remove de `blocks` os itens de
        # janela imediata do _retrieve_context ("[turno anterior", "[N turnos
        # atrás") para NÃO duplicar (lá vinham truncados pelo max_retrieval=5)
        # nem gastar os slots de retrieval. Mantém âncora temporal, histórico
        # cronológico, bloco atual, retrieval por similaridade e summaries.
        try:
            import re as _re46
            _JANELA_RE = _re46.compile(r"^\s*\[(?:turno anterior|\d+\s+turnos?)\b")
            _antes = len(blocks)
            blocks = [b for b in blocks if not _JANELA_RE.match(b or "")]
            _removidos = _antes - len(blocks)
            if _removidos:
                logger.info(
                    "[ctx] #46 filtro duplicata | janela_imediata removida de "
                    "blocks=%d | retrieval restante=%d",
                    _removidos, len(blocks),
                )
        except Exception as _e46b:
            logger.debug("[ctx] #46 filtro blocks falhou: %s", _e46b)

        # Constrói contexto otimizado
        # ── exp011 (EDP_CTX_SLOTS, default OFF): metadados fora da contagem ──
        # OFF: chamada IDENTICA a atual (metadata=None e no-op no manager).
        # ON: separa `blocks` por identidade — memorias recuperadas (coletadas
        # em _last_similarity_blocks pelo _retrieve_context) vao ao slot
        # retrieval[:max_retrieval]; o resto (ancora temporal, historico, bloco
        # atual, summaries) vira `metadata`, sempre incluido (budget-checked).
        from .config import EDP_CTX_SLOTS as _CTX_SLOTS
        _meta_blocks = None
        _retr_blocks = blocks
        if _CTX_SLOTS:
            try:
                _sim = getattr(self, "_last_similarity_blocks", None) or []
                _sim_ids = {id(b) for b in _sim}
                if _sim_ids:
                    _meta_blocks = [b for b in blocks if id(b) not in _sim_ids]
                    _retr_blocks = [b for b in blocks if id(b) in _sim_ids]
            except Exception:
                _meta_blocks, _retr_blocks = None, blocks
        ctx = mgr.build(
            system_prompt=system_prompt.replace("{context}", "").strip(),
            query=query,
            recent_turns=recent_turns,
            retrieval=_retr_blocks,
            all_history=all_history,
            metadata=_meta_blocks,
        )

        # exp012 Camada A (fonte): memórias de conteúdo que CHEGARAM ao prompt.
        # Exato por id() via _last_similarity_blocks (exp011); com CTX_SLOTS
        # OFF a lista é mista e memórias raramente sobrevivem (Defeito 1) —
        # sinal documentadamente aproximado nesse modo.
        try:
            _simset = {id(b) for b in (getattr(self, "_last_similarity_blocks", None) or [])}
            self._last_ctx_provenance = {
                "n_mem_prompt": sum(1 for b in ctx.retrieval if id(b) in _simset),
                "retrieval_tokens": (ctx.budget.retrieval_tokens if ctx.budget else None),
            }
        except Exception:
            self._last_ctx_provenance = None

        # ── exp017 Fase 0 (T4) — dup_rate@k no retrieval_kept (log-only) ──────
        # Ponto (ii) fixado no T1b (RELATORIO_T1_EXP017.md): ctx.retrieval é
        # List[str] sem ID; resolve via _last_similarity_ids (id(bloco) ->
        # entry_id, propagado read-only em _retrieve_context). Instrumentação
        # NUNCA derruba um retrieve — try/except envolve tudo, só loga.
        try:
            _sim_id_map = getattr(self, "_last_similarity_ids", None) or {}
            _kept_all = list(getattr(ctx, "retrieval", None) or [])
            _kept_sim = [b for b in _kept_all if id(b) in _sim_id_map]
            _kept_ids = [_sim_id_map[id(b)] for b in _kept_sim]

            import re as _re017

            def _norm017(t: str) -> str:
                return _re017.sub(r"\s+", " ", (t or "").strip().casefold())

            _kept_hashes = [_norm017(b) for b in _kept_sim]

            # exp017 T5: expõe o retrieval_kept por ID/hash para o script de
            # medição (scripts/medir_repeat_exp017.py) ler diretamente, sem
            # parsear log — mesmo espírito read-only do resto do T4.
            self._last_kept_ids     = list(_kept_ids)
            self._last_kept_hashes  = list(_kept_hashes)

            _k = len(_kept_sim)
            if _k:
                _dup_id   = _k - len(set(_kept_ids))
                _dup_hash = _k - len(set(_kept_hashes))
                logger.info(
                    "[exp017] dup_rate id=%d/%d hash=%d/%d",
                    _dup_id, _k, _dup_hash, _k,
                )

            # E3 FIX (diagnóstico de truncamento): a versão original comparava
            # kept contra len(_sim_blocks) — TODO bloco de similaridade,
            # inclusive os sem `eid` truthy. Mas `_sim_id_map` só recebe
            # entrada quando `eid` é truthy (linha ~2399-2400) e `_kept_sim`
            # só conta blocos presentes em `_sim_id_map` — um bloco sem id
            # NUNCA pode aparecer em "kept" por construção, mas sempre
            # contava em "offered". Isso inflava truncado=True mesmo sem
            # nenhum corte real de budget (context_window_manager.py:320-327).
            # Comparação correta: kept vs offered_mapeado, populações
            # comensuráveis (ambas via _sim_id_map). offered_total fica só
            # como dado informativo (cobertura de id(), não decide truncado).
            _sim_blocks_snapshot = getattr(self, "_last_similarity_blocks", None) or []
            _n_offered_mapeado = len(_sim_id_map)
            _n_offered_total   = len(_sim_blocks_snapshot)
            _n_kept            = _k
            _truncado = _n_kept < _n_offered_mapeado
            logger.info(
                "[exp017] truncamento kept=%d offered_mapeado=%d offered_total=%d "
                "truncado=%s",
                _n_kept, _n_offered_mapeado, _n_offered_total, _truncado,
            )
            # exp017 Fase 0 (FIX E3, T2): expõe as contagens, mesmo espírito
            # read-only do _last_kept_ids — script lê direto, sem parsear log.
            self._last_trunc = {
                "kept":             _n_kept,
                "offered_mapeado":  _n_offered_mapeado,
                "offered_total":    _n_offered_total,
            }

            # E4 (emenda): cobertura de id() — blocos em ctx.retrieval cujo
            # id() não resolve no mapa. Esperado incluir metadados (âncora
            # temporal, bloco atual, histórico — nunca tiveram entry_id); um
            # volume muito acima disso pode indicar que o builder recriou a
            # string (quebrando a identidade do truque id()).
            _unmapped = [b for b in _kept_all if id(b) not in _sim_id_map]
            if _unmapped:
                logger.debug(
                    "[exp017] cobertura id(): %d/%d bloco(s) em ctx.retrieval "
                    "sem entry_id resolvido (metadados esperados) | preview=%s",
                    len(_unmapped), len(_kept_all),
                    [b[:40].replace(chr(10), " ") for b in _unmapped[:5]],
                )
        except Exception as _e017:
            logger.debug("[exp017] instrumentacao T4 falhou (ignorada): %s", _e017)
            self._last_kept_ids, self._last_kept_hashes = [], []
            self._last_trunc = {}

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

        # ── INSTRUMENTAÇÃO TEMPORÁRIA — Dívida #46 (continuidade conversacional) ──
        # REMOVER após o diagnóstico.
        try:
            _blocks = blocks or []
            logger.warning(
                "[ctx-DEBUG #46] rendered_len=%d | recent=%d anchors=%d | blocks_total=%d",
                len(rendered), len(ctx.recent), len(ctx.anchors), len(_blocks),
            )
            logger.warning(
                "[ctx-DEBUG #46] recent_lens=%s | retrieval_kept=%s",
                [len(r) for r in ctx.recent],
                [len(b) for b in getattr(ctx, "retrieval", [])],
            )
            logger.warning(
                "[ctx-DEBUG #46] blocks_preview(ordem cronologica?)=%s",
                [b[:60].replace(chr(10), " ") for b in _blocks],
            )
            logger.warning(
                "[ctx-DEBUG #46] === PROMPT REAL (ate 6000 chars) ===\n%s",
                rendered[:6000],
            )
        except Exception as _e46:
            logger.warning("[ctx-DEBUG #46] instrumentacao falhou: %s", _e46)
        # ── FIM INSTRUMENTAÇÃO TEMPORÁRIA #46 ──
       
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
