"""
runtime/context_window_manager.py — Gestor de contexto cognitivo

Aloca tokens dentro da janela do LLM por prioridade:

  Nível 1 (sempre):    system_prompt + query atual
  Nível 2 (âncoras):   primeiros 2 turnos da sessão
  Nível 3 (recentes):  últimos N turnos
  Nível 4 (retrieval): top-K memórias relevantes
  Nível 5 (overflow):  sumarização recursiva (futuro)

Estimação de tokens: aproximada (4 chars ≈ 1 token para PT/EN).
Para precisão real usaria tiktoken, mas evitamos dependência extra.

Por que esse design:
  - Garante que system prompt e query NUNCA são cortados
  - Mantém continuidade (âncoras + recentes)
  - Maximiza relevância (retrieval semântico)
  - Algoritmo guloso: corta primeiro o que tem menor prioridade

Trade-offs:
  - Estimação de tokens é aproximada (margem de ~10%)
  - Não faz sumarização ainda (próxima sprint)
  - Não considera tokens de formatação especial do LLM
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("edp.context_window")


# ── Janelas conhecidas por modelo ──────────────────────────────────────────────
KNOWN_WINDOWS = {
    # Ollama / local
    "phi3":           4096,
    "phi3:latest":    4096,
    "phi3:mini":      4096,
    "phi3:medium":    128000,
    "llama3":         8192,
    "llama3:8b":      8192,
    "llama3.1":       128000,
    "llama3.2":       128000,
    "qwen2.5":        32768,
    "qwen2.5:0.5b":   32768,
    "gemma2":         8192,
    "gemma2:2b":      8192,
    "mistral":        32768,
    # Anthropic Claude (cloud, janelas grandes - hardware cap NÃO se aplica)
    "claude-opus-4-7":     200000,
    "claude-opus-4-6":     200000,
    "claude-sonnet-4-6":   200000,
    "claude-haiku-4-5":    200000,
    "claude-3-5-sonnet":   200000,
    "claude-3-5-haiku":    200000,
    "claude-3-opus":       200000,
    # OpenAI (cloud)
    "gpt-4o":              128000,
    "gpt-4o-mini":         128000,
    "gpt-4-turbo":         128000,
    "gpt-4.1":             128000,
    # Fallback
    "default":        4096,
}

# Modelos cloud — hardware cap NÃO se aplica (são processados em servidor remoto)
CLOUD_PREFIXES = ("claude-", "gpt-")


def is_cloud_model(model_name: str) -> bool:
    """True se modelo é cloud (Anthropic, OpenAI, etc.)."""
    name = (model_name or "").lower().strip()
    return any(name.startswith(p) for p in CLOUD_PREFIXES)


def estimate_tokens(text: str) -> int:
    """Estimativa rápida: 1 token ≈ 4 chars (média PT/EN)."""
    return max(1, len(text) // 4)


def get_window_size(model_name: str) -> int:
    """
    Retorna janela de contexto do modelo, com fallback conservador.
    Usa longest-prefix-match para evitar 'llama3' casar antes de 'llama3.1'.
    """
    name = (model_name or "").lower().strip()
    if name in KNOWN_WINDOWS:
        return KNOWN_WINDOWS[name]
    # Longest-prefix-match: testa chaves mais longas primeiro
    keys_sorted = sorted(
        (k for k in KNOWN_WINDOWS if k != "default"),
        key=len, reverse=True,
    )
    for key in keys_sorted:
        if name.startswith(key):
            return KNOWN_WINDOWS[key]
    return KNOWN_WINDOWS["default"]


@dataclass
class ContextBudget:
    total_window:     int
    reserve_response: int = 1024     # reserva para gerar resposta
    system_tokens:    int = 0
    query_tokens:     int = 0
    anchor_tokens:    int = 0
    recent_tokens:    int = 0
    retrieval_tokens: int = 0

    @property
    def available_input(self) -> int:
        """Tokens disponíveis para input total."""
        return self.total_window - self.reserve_response

    @property
    def used(self) -> int:
        return (self.system_tokens + self.query_tokens +
                self.anchor_tokens + self.recent_tokens + self.retrieval_tokens)

    @property
    def remaining(self) -> int:
        return self.available_input - self.used


@dataclass
class BuiltContext:
    """Resultado do gerenciador: contexto pronto para enviar ao LLM."""
    system:    str
    query:     str
    anchors:   List[str]    = field(default_factory=list)
    recent:    List[str]    = field(default_factory=list)
    retrieval: List[str]    = field(default_factory=list)
    budget:    Optional[ContextBudget] = None

    def to_prompt(self) -> str:
        """Renderiza contexto como prompt formatado."""
        parts = [self.system, ""]

        if self.anchors:
            parts.append("=== HISTÓRICO ANTERIOR (início da sessão) ===")
            parts.extend(self.anchors)
            parts.append("")

        if self.retrieval:
            parts.append("=== MEMÓRIAS RELEVANTES ===")
            parts.extend(self.retrieval)
            parts.append("")

        if self.recent:
            parts.append("=== TURNOS RECENTES ===")
            parts.extend(self.recent)
            parts.append("")

        parts.append("=== PERGUNTA ATUAL ===")
        parts.append(self.query)
        return "\n".join(parts)

    def to_metrics(self) -> dict:
        if not self.budget:
            return {}
        return {
            "window_total":      self.budget.total_window,
            "available_input":   self.budget.available_input,
            "used_tokens":       self.budget.used,
            "remaining":         self.budget.remaining,
            "system_tokens":     self.budget.system_tokens,
            "query_tokens":      self.budget.query_tokens,
            "anchor_tokens":     self.budget.anchor_tokens,
            "recent_tokens":     self.budget.recent_tokens,
            "retrieval_tokens":  self.budget.retrieval_tokens,
        }


class ContextWindowManager:
    """
    Constrói contexto otimizado dentro do budget de tokens.

    Algoritmo (greedy por prioridade):
      1. Aloca system + query (nunca corta)
      2. Adiciona âncoras enquanto cabem
      3. Adiciona retrieval enquanto cabe
      4. Adiciona turnos recentes do mais novo para o mais antigo

    Use:
      mgr = ContextWindowManager(model_name="phi3:latest")
      ctx = mgr.build(
          system_prompt="Você é um assistente.",
          query="Qual meu nome?",
          recent_turns=["Q: Oi A: Olá!", "Q: Sou João A: Prazer!"],
          retrieval=["[memória anterior]"],
      )
      prompt = ctx.to_prompt()
    """

    def __init__(
        self,
        model_name:       str = "default",
        reserve_response: int = 1024,
        max_anchors:      int = 2,
        max_recent:       int = 5,
        max_retrieval:    int = 5,
        hardware_limit:   Optional[int] = None,
    ) -> None:
        """
        Args:
            hardware_limit: teto absoluto imposto pelo hardware.
                None = auto-detecta via env EDP_HARDWARE_CTX_LIMIT (default 4096 em 8GB).
                Janela efetiva = min(model_window, hardware_limit).

        Por que isso importa:
            Declarar window=32768 em 8GB de RAM causa swap silencioso quando
            o prompt cresce. O modelo até aceita, mas a inferência fica 10x mais
            lenta e o WebSocket morre de keepalive. Cap é o piso da estabilidade.
        """
        import os as _os
        model_window  = get_window_size(model_name)
        is_cloud = is_cloud_model(model_name)

        if hardware_limit is None:
            if is_cloud:
                # Modelos cloud não rodam no seu hardware — sem cap.
                # Mas mantemos limite razoável para custo: 32k tokens default.
                # Override via EDP_CLOUD_CTX_LIMIT se quiser usar janela maior.
                try:
                    hardware_limit = int(_os.environ.get("EDP_CLOUD_CTX_LIMIT", "32000"))
                except ValueError:
                    hardware_limit = 32000
            else:
                # Local: hardware cap importa (RAM real do usuário)
                try:
                    hardware_limit = int(_os.environ.get("EDP_HARDWARE_CTX_LIMIT", "4096"))
                except ValueError:
                    hardware_limit = 4096

        effective = min(model_window, hardware_limit)

        self.model_window   = model_window
        self.hardware_limit = hardware_limit
        self.window         = effective
        self.reserve        = reserve_response
        self.max_anchors    = max_anchors
        self.max_recent     = max_recent
        self.max_retrieval  = max_retrieval
        self.model_name     = model_name

        if effective < model_window:
            logger.info(
                "[ContextWindow] modelo=%s window=%d (capped from model_max=%d) reserve=%d",
                model_name, effective, model_window, reserve_response,
            )
        else:
            logger.info(
                "[ContextWindow] modelo=%s window=%d reserve=%d",
                model_name, effective, reserve_response,
            )

    def build(
        self,
        system_prompt: str,
        query:         str,
        recent_turns:  Optional[List[str]] = None,
        retrieval:     Optional[List[str]] = None,
        all_history:   Optional[List[str]] = None,
    ) -> BuiltContext:
        recent_turns = recent_turns or []
        retrieval    = retrieval or []
        all_history  = all_history or []

        budget = ContextBudget(
            total_window=self.window,
            reserve_response=self.reserve,
            system_tokens=estimate_tokens(system_prompt),
            query_tokens=estimate_tokens(query),
        )

        ctx = BuiltContext(
            system=system_prompt,
            query=query,
            budget=budget,
        )

        # Verifica se sistema + query já estouram
        if budget.used > budget.available_input:
            logger.warning("[ContextWindow] system+query estouram budget — truncando query")
            # corta query do final
            max_query_chars = (budget.available_input - budget.system_tokens) * 4
            ctx.query = query[:max(100, max_query_chars)]
            budget.query_tokens = estimate_tokens(ctx.query)
            return ctx

        # ── Nível 2: âncoras (primeiros 2 turnos) ──
        if all_history and len(all_history) >= 2:
            anchors_candidates = all_history[:self.max_anchors]
            for anchor in anchors_candidates:
                cost = estimate_tokens(anchor)
                if budget.remaining >= cost:
                    ctx.anchors.append(anchor)
                    budget.anchor_tokens += cost
                else:
                    break

        # ── Nível 3: retrieval (top-K relevantes) ──
        for item in retrieval[:self.max_retrieval]:
            cost = estimate_tokens(item)
            if budget.remaining >= cost:
                ctx.retrieval.append(item)
                budget.retrieval_tokens += cost
            else:
                break

        # ── Nível 4: turnos recentes (do mais novo para o mais antigo) ──
        recent_reversed = list(reversed(recent_turns[-self.max_recent:]))
        added_recent: List[str] = []
        for turn in recent_reversed:
            cost = estimate_tokens(turn)
            if budget.remaining >= cost:
                added_recent.insert(0, turn)
                budget.recent_tokens += cost
            else:
                break
        ctx.recent = added_recent

        logger.debug("[ContextWindow] %d/%d tokens (s=%d q=%d a=%d r=%d ret=%d)",
                     budget.used, budget.available_input,
                     budget.system_tokens, budget.query_tokens,
                     budget.anchor_tokens, budget.recent_tokens, budget.retrieval_tokens)
        return ctx
