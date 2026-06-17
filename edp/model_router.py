"""
edp.model_router — Roteamento dinâmico de modelo por complexidade.

Princípio:
  - Você descreveu: oscila rápido entre simples/técnico/profundo, esquece de trocar
  - Roteador escolhe automaticamente Haiku/Sonnet/Opus baseado no turno
  - Modo HÍBRIDO: sempre informa qual modelo + custo estimado
  - Você pode forçar manualmente a qualquer momento

Heurística (sem LLM extra, ~5ms por turno):
  - Conta tokens, conectivos lógicos, termos filosóficos, termos técnicos
  - Considera o modelo do turno anterior (continuidade)
  - Detecta confirmações curtas → Haiku
  - Detecta filosofia/prova/multi-parte → Opus
  - Detecta análise técnica estruturada → Sonnet
"""
from __future__ import annotations
from typing import Optional, List, Tuple
import re
import logging

logger = logging.getLogger("edp.model_router")


# ── Catálogo de modelos disponíveis ──────────────────────────────────────────
MODELS = {
    # Preços atualizados 12/06/2026 conforme docs oficiais Anthropic.
    # Correções: haiku era 0.80/4.00 (preço haiku-3.5); opus-4-7 era
    # 15/75 (preço Opus 4.1/4 antigos). Fable 5 custa 2× Opus, não 3×.
    "claude-haiku-4-5":  {"price_in": 1.00,  "price_out": 5.00,  "tier": 1},
    "claude-sonnet-4-6": {"price_in": 3.00,  "price_out": 15.00, "tier": 2},
    "claude-opus-4-7":   {"price_in": 5.00,  "price_out": 25.00, "tier": 3},
    # ── Tiers superiores p/ câmara de eco (11/06/2026) ──────────────
    # Visíveis APENAS para escolher_modelos_B (que escolhe por tier).
    # O router de turnos nomeia modelos explicitamente (linhas ~304+),
    # então o roteamento diário NÃO muda com estas entradas.
    # Efeito na câmara: A=opus-4-7 → B=opus-4-8 (antes: auto-refutação);
    # A=opus-4-8 → B=fable-5; A=fable-5 → auto-refutação c/ veto.
    "claude-opus-4-8":   {"price_in": 5.00,  "price_out": 25.00, "tier": 4},
    "claude-fable-5":    {"price_in": 10.00, "price_out": 50.00, "tier": 5},
}
DEFAULT_MODEL = "claude-haiku-4-5"


# ── Marcadores de complexidade ───────────────────────────────────────────────

# Confirmações simples → sempre Haiku
SIMPLE_CONFIRM_REGEX = re.compile(
    r"^(?:ok|sim|nao|não|ya|yes|no|obrigado|valeu|entendi|certo|beleza"
    r"|legal|bom|otimo|ótimo|perfeito|exato|isso|claro)\b[.!?]?\s*$",
    re.IGNORECASE,
)

# Saudações → Haiku
GREETING_REGEX = re.compile(
    r"^(?:oi|olá|ola|bom dia|boa tarde|boa noite|e aí|salve|hey|hi|hello)"
    r"\b.{0,30}$",
    re.IGNORECASE,
)

# Comandos simples → Haiku
SIMPLE_COMMAND_REGEX = re.compile(
    r"\b(?:mostra|lista|abre|fecha|salva|gera lista|me da|me dá|copia)\b",
    re.IGNORECASE,
)

# Conectivos lógicos → indica raciocínio
LOGICAL_CONNECTIVES = [
    r"\bportanto\b", r"\blogo\b", r"\bse\s+\w+\s+então\b",
    r"\bimplica\b", r"\bporque\b", r"\bpor que\b", r"\bcomo assim\b",
    r"\bdaí\b", r"\bentão\b", r"\bassim\b", r"\bdesse modo\b",
    r"\bsegue-se\b", r"\bconcluindo\b",
]
_LOGIC_REGEX = re.compile("|".join(LOGICAL_CONNECTIVES), re.IGNORECASE)

# Termos filosóficos/abstratos → indica profundidade
PHILOSOPHICAL_TERMS = {
    "consciência", "consciencia", "alma", "intenção", "intencao",
    "monismo", "dualismo", "verdade", "sentido", "essência", "essencia",
    "ontológico", "ontologico", "epistêmico", "epistemico", "fenomenológico",
    "fenomenologico", "metafísic", "metafisic", "qualia", "causalidade",
    "livre-arbítrio", "livre arbitrio", "determinismo", "emergência",
    "emergencia", "fundamental", "fundação", "fundacao",
}

# Termos técnicos densos → indica análise estruturada
TECHNICAL_TERMS = {
    # CS / IA
    "embedding", "transformer", "retrieval", "softmax", "gradient",
    "regularização", "overfitting", "tokenização", "atenção", "perplexity",
    "fine-tuning", "rlhf", "prompt", "context window",
    # Cripto / matemática
    "sha-256", "sha256", "hash", "criptografia", "nonce", "merkle",
    "blockchain", "fatoração", "fatoracao", "polinomial", "exponencial",
    "logaritmo", "primalidade", "ecdsa", "rsa",
    # Quântica
    "qubit", "superposição", "superposicao", "entropia", "shor", "grover",
    "decoerência", "decoerencia", "schrödinger", "quântic", "quantic",
    "interferência", "interferencia",
    # Sistemas
    "kernel", "thread", "concorrência", "concorrencia", "deadlock", "race",
    "throughput", "latência", "latencia", "cache", "queue",
    # Termodinâmica/física
    "termodinâmica", "termodinamica", "entropia", "irreversibilidade",
    "landauer", "boltzmann", "shannon",
}

# Pedidos de prova/derivação → Opus
PROOF_REQUEST_REGEX = re.compile(
    r"\b(?:prov(?:e|a|ar)|deriv(?:e|a|ar)|demonstr(?:e|a|ar)|fundament(?:e|a|ar)"
    r"|justifiqu(?:e|ar)|por que matemati|formali(?:ze|zar))",
    re.IGNORECASE,
)

# Pedidos de comparação/análise → Sonnet+
STRUCTURED_REQUEST_REGEX = re.compile(
    r"\b(?:compar(?:e|a|ar)|contrast(?:e|ar)|analis(?:e|ar)|avalia|explique"
    r"|como funciona|qual a (?:diferença|relação|relacao)|por que (?:funciona|não))",
    re.IGNORECASE,
)


def _count_technical_terms(text_lower: str) -> int:
    """Conta termos técnicos únicos no texto."""
    return sum(1 for t in TECHNICAL_TERMS if t in text_lower)


def _count_philosophical_terms(text_lower: str) -> int:
    """Conta termos filosóficos únicos no texto."""
    return sum(1 for t in PHILOSOPHICAL_TERMS if t in text_lower)


def _count_logical_connectives(text: str) -> int:
    """Conta marcadores lógicos."""
    return len(set(m.group(0).lower() for m in _LOGIC_REGEX.finditer(text)))


def _count_question_parts(text: str) -> int:
    """Conta partes de pergunta multi-parte (1, 2, 3... ou separadores)."""
    # Numeração explícita: "1)", "1.", "primeiro", etc
    numbered = len(re.findall(r"\b[123456]\)\s|\b[123456]\.\s", text))
    if numbered >= 2:
        return numbered
    # Separadores de parte: "e também", "além disso"
    separators = len(re.findall(
        r"\b(?:e também|além disso|ainda|outra coisa|e por fim)",
        text, re.IGNORECASE
    ))
    return separators + 1  # +1 pela primeira pergunta implícita


def route_model(
    user_message: str,
    previous_model: Optional[str] = None,
    available_models: Optional[List[str]] = None,
    task_context: Optional[dict] = None,
) -> dict:
    """
    Escolhe o modelo ideal para a próxima resposta.

    Args:
        user_message: texto da pergunta atual
        previous_model: modelo usado no turno anterior (None = primeira mensagem)
        available_models: lista de modelos disponíveis (default: todos do MODELS)
        task_context: dict opcional com sinais de tarefa em curso. Aceita:
            - sectioned_active (bool): se modo sectioned está ativo
            - task_anchor_active (bool): se há tarefa multi-seção em andamento
          Peça 2.6d (M2): quando ambos True e mensagem é curta (ex: "continue"),
          o router preserva o modelo do turno anterior em vez de rebaixar para
          Haiku por "mensagem muito curta". Razão empírica (sábado 30/05/2026):
          tarefa de 10 seções foi rebaixada Sonnet→Haiku no turno 2 porque
          "continue" tem 1 palavra. Resultado: queda de coesão arquitetural
          medida por avaliador externo (5.5/10 em consistência de tecnologia).

    Returns:
        dict com:
            - model: nome do modelo escolhido
            - tier: 1 (haiku), 2 (sonnet), 3 (opus)
            - reason: explicação curta
            - signals: dict com contadores das heurísticas
            - estimated_cost_per_turn: estimativa em USD
    """
    if not user_message:
        return _default_response()

    available = available_models or list(MODELS.keys())
    text = user_message.strip()
    text_lower = text.lower()
    n_chars = len(text)
    n_words = len(text.split())

    # ── Peça 2.6d M2 (2026-05-30): preservação em tarefa sectioned ────
    # Se sectioned ativo + tarefa em andamento + mensagem curta (típica
    # de continuação: "continue", "/next", "siga"), preserva modelo do
    # turno anterior. Nunca rebaixa em contexto de tarefa técnica em curso.
    _ctx = task_context or {}
    _in_sectioned_task = bool(
        _ctx.get("sectioned_active") and _ctx.get("task_anchor_active")
    )
    if _in_sectioned_task and previous_model and n_words < 10:
        prev_tier = MODELS.get(previous_model, {}).get("tier", 1)
        # Só preserva se previous era Sonnet/Opus (não desce de Haiku)
        if prev_tier >= 2 and previous_model in available:
            return _response(
                model=previous_model,
                reason=f"sectioned ativo: preserva {previous_model} (continuação de tarefa)",
                signals={
                    "sectioned_preserve": True,
                    "prev_tier":          prev_tier,
                    "words":              n_words,
                },
                available=available,
            )
    # ── fim da preservação sectioned ──────────────────────────────────

    # ── Detectores de simplicidade (forçam Haiku) ──────────────────────
    if SIMPLE_CONFIRM_REGEX.match(text):
        return _response(
            model="claude-haiku-4-5",
            reason="confirmação simples",
            signals={"simple_confirm": True},
            available=available,
        )

    if GREETING_REGEX.match(text):
        return _response(
            model="claude-haiku-4-5",
            reason="saudação",
            signals={"greeting": True},
            available=available,
        )

    # ── Coleta sinais (movido para antes do filtro de curta) ──────────
    tech_count = _count_technical_terms(text_lower)
    philo_count = _count_philosophical_terms(text_lower)
    logic_count = _count_logical_connectives(text)
    n_parts = _count_question_parts(text)
    is_proof = bool(PROOF_REQUEST_REGEX.search(text))
    is_structured = bool(STRUCTURED_REQUEST_REGEX.search(text))
    is_simple_command = bool(SIMPLE_COMMAND_REGEX.search(text)) and n_words < 10

    # Mensagem curta SEM densidade técnica/filosófica/estrutural = Haiku
    if n_words <= 4 and tech_count == 0 and philo_count == 0 and not is_structured and not is_proof:
        return _response(
            model="claude-haiku-4-5",
            reason=f"mensagem muito curta ({n_words} palavras)",
            signals={"short": True, "words": n_words},
            available=available,
        )

    signals = {
        "n_words":         n_words,
        "n_chars":         n_chars,
        "tech_count":      tech_count,
        "philo_count":     philo_count,
        "logic_count":     logic_count,
        "n_parts":         n_parts,
        "is_proof":        is_proof,
        "is_structured":   is_structured,
        "is_simple_cmd":   is_simple_command,
    }

    # ── Score de profundidade ─────────────────────────────────────────
    # Cada sinal contribui com peso. >5 = Opus, 2-5 = Sonnet, <2 = Haiku.
    depth_score = 0
    reasons = []

    if is_proof:
        depth_score += 4
        reasons.append("pedido de prova/derivação")

    # Filosofia tem peso maior (era a fraqueza dos testes)
    if philo_count >= 3:
        depth_score += 5
        reasons.append(f"{philo_count} termos filosóficos")
    elif philo_count == 2:
        depth_score += 4
        reasons.append(f"{philo_count} termos filosóficos")
    elif philo_count == 1:
        depth_score += 2
        reasons.append("1 termo filosófico")

    if tech_count >= 3:
        depth_score += 2
        reasons.append(f"{tech_count} termos técnicos")
    elif tech_count >= 1:
        depth_score += 1

    if logic_count >= 2:
        depth_score += 2
        reasons.append(f"{logic_count} conectivos lógicos")
    elif logic_count == 1:
        depth_score += 1

    if n_parts >= 3:
        depth_score += 2
        reasons.append("pergunta multi-parte")

    if is_structured:
        depth_score += 2
        reasons.append("análise estruturada")

    # Pergunta muito longa = provavelmente profunda
    if n_words > 50:
        depth_score += 1
        reasons.append(f"texto longo ({n_words} palavras)")

    # ── Continuidade: não desce abruptamente ──────────────────────────
    # Se conversa estava em Sonnet/Opus, mantém pelo menos esse tier
    # para follow-ups curtos (já filtrados acima — mas como guard adicional)
    prev_tier = MODELS.get(previous_model, {}).get("tier", 1) if previous_model else 1

    # ── Decisão final ─────────────────────────────────────────────────
    if depth_score >= 5:
        chosen = "claude-opus-4-7"
        reason = "profundidade alta: " + ", ".join(reasons[:3]) if reasons else "alta complexidade"
    elif depth_score >= 2:
        chosen = "claude-sonnet-4-6"
        reason = "complexidade média: " + ", ".join(reasons[:3]) if reasons else "média complexidade"
    else:
        chosen = "claude-haiku-4-5"
        reason = "pergunta simples"

    # Continuidade: se anterior era Opus e atual não é confirmação curta,
    # mantém pelo menos Sonnet (evita oscilação brusca em follow-ups)
    if prev_tier == 3 and MODELS[chosen]["tier"] == 1 and n_words >= 5:
        chosen = "claude-sonnet-4-6"
        reason += " · mantido em Sonnet por continuidade (vinha de Opus)"
    elif prev_tier == 2 and MODELS[chosen]["tier"] == 1 and n_words >= 12:
        chosen = "claude-sonnet-4-6"
        reason += " · mantido em Sonnet por continuidade"

    return _response(
        model=chosen,
        reason=reason,
        signals=signals,
        available=available,
        depth_score=depth_score,
    )


def _response(
    model: str,
    reason: str,
    signals: dict,
    available: List[str],
    depth_score: int = 0,
) -> dict:
    """Constrói resposta padronizada, fazendo fallback se modelo indisponível."""
    if model not in available:
        # Fallback: usa o disponível de tier mais próximo
        target_tier = MODELS[model]["tier"]
        best = min(
            available,
            key=lambda m: abs(MODELS.get(m, {"tier": 1})["tier"] - target_tier),
        )
        model = best

    info = MODELS.get(model, MODELS[DEFAULT_MODEL])
    # Estimativa: ~500 tokens in + ~200 tokens out
    est_cost = (500 / 1_000_000 * info["price_in"]) + (200 / 1_000_000 * info["price_out"])

    return {
        "model":                     model,
        "tier":                      info["tier"],
        "reason":                    reason,
        "signals":                   signals,
        "depth_score":               depth_score,
        "estimated_cost_per_turn":   round(est_cost, 4),
    }


def _default_response() -> dict:
    """Resposta default quando input é vazio."""
    return _response(
        model=DEFAULT_MODEL,
        reason="default",
        signals={},
        available=list(MODELS.keys()),
    )


def format_router_badge(routing: dict) -> str:
    """
    Formata badge curto para mostrar ao usuário.

    Exemplo: "[Sonnet · $0.005 · 2 termos técnicos]"
    """
    if not routing:
        return ""
    model = routing.get("model", "?")
    short_name = model.replace("claude-", "").replace("-4-5", "").replace("-4-6", "").replace("-4-7", "")
    short_name = short_name.capitalize()
    cost = routing.get("estimated_cost_per_turn", 0)
    reason = routing.get("reason", "")
    return f"[{short_name} · ~${cost:.4f} · {reason}]"


# ═══════════════════════════════════════════════════════════════════════════
# Peça 2.1 — Câmara de eco: roteamento de refutadores
# ═══════════════════════════════════════════════════════════════════════════
#
# Princípio (Trindade sem divindade):
#   - Pai = Usuário, Filho = Modelo, Espírito = EDP (espelho)
#   - Câmara de eco: Modelo A gera, Modelo(s) B refuta(m), todos com mesmo
#     contexto. Sinal de presença = fidelidade do espelho.
#   - B emerge ACIMA de A na hierarquia. Quando A=Opus, não há refutador
#     acima (ápice — contexto já curado).
#
# Esta peça (2.1) entrega apenas as DECISÕES:
#   - escolher_modelos_B: dado A, retorna refutadores acima
#   - forca_camara_detectada: detecta gatilho manual do usuário
#   - deve_ativar_camara: combina sinais para decidir ativação
#
# Não EXECUTA câmara — peça 2.2 fará isso. Aqui só decide.
# ═══════════════════════════════════════════════════════════════════════════


# ── Gatilhos manuais de ativação ──────────────────────────────────────────
# Palavras/expressões que o usuário usa quando quer EXPLICITAMENTE
# que a câmara seja ativada (override de qualquer heurística).
FORCA_CAMARA_REGEX = re.compile(
    r"\b(?:verifica(?:\s+isso)?|verific(?:a|ar)|refut(?:a|e|ar)"
    r"|tenho\s+dúvida|tenho\s+duvida|questiona(?:\s+isso)?"
    r"|valida(?:\s+isso)?|valid(?:e|ar)|cheq(?:a|ue|uar)"
    r"|tem\s+certeza|tem\s+ceteza|confer(?:e|ir)|conferi(?:r|ndo)"
    r"|com\s+rigor|com\s+cuidado|cuidadosamente|seriamente|com\s+atenção"
    r"|câmara|camara\s+de\s+eco)\b",
    re.IGNORECASE,
)


def escolher_modelos_B(
    modelo_A: str,
    available_models: Optional[List[str]] = None,
) -> List[str]:
    """
    Dado o modelo A (gerador), retorna lista de modelos B (refutadores)
    ACIMA de A na hierarquia.

    Princípio: B emerge acima de A. Todos terão acesso ao MESMO contexto
    que A recebeu — peça 2.2 garante isso. Aqui só decidimos quem.

    Args:
        modelo_A: nome do modelo gerador (ex: "claude-haiku-4-5")
        available_models: lista de modelos disponíveis (default: todos)

    Returns:
        Lista de modelos refutadores, ordenada do MAIS PRÓXIMO ao MAIS ALTO.
        Se modelo_A é o topo (ex: Opus), retorna [modelo_A] — auto-refutação.
        A câmara aplicará VETO ASSIMÉTRICO nesse caso (peça 2.4a.6): Opus-B
        só vence se detectar dano factual (confabulacao/projecao_sem_dado/
        perda_de_fio); refinamentos estilísticos são vetados em favor de A.

    Exemplos:
        escolher_modelos_B("claude-haiku-4-5")  → ["claude-sonnet-4-6", "claude-opus-4-7"]
        escolher_modelos_B("claude-sonnet-4-6") → ["claude-opus-4-7"]
        escolher_modelos_B("claude-opus-4-7")   → ["claude-opus-4-7"]  (auto-refutação)
    """
    available = available_models or list(MODELS.keys())

    if modelo_A not in MODELS:
        # Modelo desconhecido: sem refutador (segurança)
        return []

    tier_A = MODELS[modelo_A]["tier"]

    # Refutadores: modelos COM TIER SUPERIOR a A, disponíveis
    refutadores = [
        m for m in available
        if m in MODELS
        and MODELS[m]["tier"] > tier_A
        and m != modelo_A
    ]

    # Ordena por tier ascendente (mais próximo de A primeiro)
    refutadores.sort(key=lambda m: MODELS[m]["tier"])

    # ── Peça 2.4a.6: política de topo (auto-refutação) ──────────────
    # Se A é o topo da hierarquia (sem refutador acima), B = A mesmo.
    # A câmara aplicará veto assimétrico: Opus-B só substitui Opus-A se
    # provar dano factual real, não por estética. Garante refinamento no
    # teto sem abrir mão da honestidade nua de A.
    if not refutadores and modelo_A in available:
        return [modelo_A]

    return refutadores


def forca_camara_detectada(user_message: str) -> bool:
    """
    Detecta gatilhos manuais do usuário que forçam ativação da câmara,
    independente da heurística normal.

    Examples (todos retornam True):
        "verifica isso aí"
        "tenho dúvida"
        "refuta esse argumento"
        "valida com rigor"
        "cheq a fonte"
        "câmara"  (palavra explícita)

    Returns:
        True se o usuário sinalizou querer câmara explicitamente.
    """
    if not user_message:
        return False
    return bool(FORCA_CAMARA_REGEX.search(user_message))


def deve_ativar_camara(
    routing_decision: dict,
    user_message: str,
    history: Optional[List[dict]] = None,
    auto_sinal_de_A: Optional[dict] = None,
    usar_heuristica_legada: bool = False,
) -> dict:
    """
    Decide se a câmara de eco deve ser ativada.

    PEÇA 2.3 — ATIVAÇÃO POR AUTO-SINAL DO MODELO (não por heurísticas externas)

    Princípio (articulado pelo usuário 2026-05-24):
      "Uma boa forma de ativar a câmara é quando um modelo instruído diz
       'essa pergunta eu não consigo responder com confiança, seria
       especulação minha'. Então ativa a câmara e um modelo melhor analisa
       o que o modelo A gerou e reformula."

    Câmara é EXCLUSIVA, não rotineira. Não é "verificação extra que liga
    sempre". É reservada para questões difíceis onde a presença real
    importa. O Filho instruído (Camada 3 da janela com ceticismo +
    honestidade) RECONHECE quando está beirando especulação e admite.
    EDP-Espírito ouve a admissão e providencia o refinamento.

    Critério (em ordem de prioridade):
      1. Gatilho manual do usuário → SEMPRE ativa (override absoluto)
      2. Auto-sinal de A (modelo admitiu limite) → ativa
      3. A=Opus → NUNCA ativa (ápice, sem refutador acima)
      4. Heurística legada (apenas se usar_heuristica_legada=True) — rede
         de segurança opcional para fase de descoberta

    Args:
        routing_decision: dict retornado por route_model()
        user_message: texto da pergunta do usuário (para detectar gatilho manual)
        history: histórico recente (não usado em 2.3; reservado p/ peças futuras)
        auto_sinal_de_A: resultado de detectar_auto_sinal_de_limite() aplicado
                         à resposta de A. None se A ainda não respondeu.
        usar_heuristica_legada: se True, mantém a heurística da peça 2.1
                                (Haiku+ds>=1, Sonnet+ds>=3) como rede de segurança.
                                Default False — confiamos no auto-sinal.

    Returns:
        dict com:
            - ativar: bool
            - motivo: str (explicação curta)
            - modelos_B: list[str] (vazia se não ativar)
            - forca_manual: bool (True se gatilho explícito do usuário)
            - via_auto_sinal: bool (True se ativou por admissão de A)
    """
    modelo_A = routing_decision.get("model", DEFAULT_MODEL)
    tier_A = MODELS.get(modelo_A, {}).get("tier", 1)

    # ── Prioridade 1: Override manual do usuário ────────────────────────
    if forca_camara_detectada(user_message):
        modelos_B = escolher_modelos_B(modelo_A)
        if not modelos_B:
            return {
                "ativar":         False,
                "motivo":         "gatilho manual, mas A é topo (Opus) — sem refutador acima",
                "modelos_B":      [],
                "forca_manual":   True,
                "via_auto_sinal": False,
            }
        return {
            "ativar":         True,
            "motivo":         "gatilho manual do usuário",
            "modelos_B":      modelos_B,
            "forca_manual":   True,
            "via_auto_sinal": False,
        }

    # ── Prioridade 2: Auto-sinal de A (admissão de limite) ─────────────
    if auto_sinal_de_A and auto_sinal_de_A.get("detectado"):
        if tier_A == 3:
            # A é Opus e admitiu limite — sem refutador acima.
            # Caso raro e honesto: até Opus tem limites. Não ativa câmara.
            return {
                "ativar":         False,
                "motivo":         "Opus admitiu limite, mas é o topo — sem refutador acima",
                "modelos_B":      [],
                "forca_manual":   False,
                "via_auto_sinal": True,
            }
        modelos_B = escolher_modelos_B(modelo_A)
        confianca = auto_sinal_de_A.get("confianca", "media")
        # Auto-sinal só usa o refutador mais próximo (custo controlado)
        return {
            "ativar":         True,
            "motivo":         f"A={modelo_A} admitiu limite (confiança detecção: {confianca})",
            "modelos_B":      modelos_B[:1] if modelos_B else [],
            "forca_manual":   False,
            "via_auto_sinal": True,
        }

    # ── Prioridade 3: A=Opus nunca ativa heuristicamente ───────────────
    if tier_A == 3:
        return {
            "ativar":         False,
            "motivo":         "A é Opus (ápice — sem refutador acima)",
            "modelos_B":      [],
            "forca_manual":   False,
            "via_auto_sinal": False,
        }

    # ── Prioridade 4: Heurística legada (rede de segurança opcional) ───
    if usar_heuristica_legada:
        depth_score = routing_decision.get("depth_score", 0)
        if tier_A == 1 and depth_score >= 1:
            modelos_B = escolher_modelos_B(modelo_A)
            return {
                "ativar":         True,
                "motivo":         f"heurística legada: Haiku + ds={depth_score}",
                "modelos_B":      modelos_B[:1] if modelos_B else [],
                "forca_manual":   False,
                "via_auto_sinal": False,
            }
        if tier_A == 2 and depth_score >= 3:
            modelos_B = escolher_modelos_B(modelo_A)
            return {
                "ativar":         True,
                "motivo":         f"heurística legada: Sonnet + ds={depth_score}",
                "modelos_B":      modelos_B,
                "forca_manual":   False,
                "via_auto_sinal": False,
            }

    # ── Default: não ativa ────────────────────────────────────────────
    return {
        "ativar":         False,
        "motivo":         "sem auto-sinal de A e sem gatilho manual",
        "modelos_B":      [],
        "forca_manual":   False,
        "via_auto_sinal": False,
    }
