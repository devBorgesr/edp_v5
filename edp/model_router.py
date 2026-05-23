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
    "claude-haiku-4-5":  {"price_in": 0.80,  "price_out": 4.00,  "tier": 1},
    "claude-sonnet-4-6": {"price_in": 3.00,  "price_out": 15.00, "tier": 2},
    "claude-opus-4-7":   {"price_in": 15.00, "price_out": 75.00, "tier": 3},
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
) -> dict:
    """
    Escolhe o modelo ideal para a próxima resposta.

    Args:
        user_message: texto da pergunta atual
        previous_model: modelo usado no turno anterior (None = primeira mensagem)
        available_models: lista de modelos disponíveis (default: todos do MODELS)

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
        Lista VAZIA se modelo_A é o topo (ex: Opus) — nesse caso, peça 2.2
        não ativará câmara para esse turno.

    Exemplos:
        escolher_modelos_B("claude-haiku-4-5")  → ["claude-sonnet-4-6", "claude-opus-4-7"]
        escolher_modelos_B("claude-sonnet-4-6") → ["claude-opus-4-7"]
        escolher_modelos_B("claude-opus-4-7")   → []
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
) -> dict:
    """
    Decide se a câmara de eco deve ser ativada para este turno.

    Combina sinais:
      1. Gatilho manual do usuário (override absoluto — sempre ativa)
      2. Tier do modelo A escolhido
      3. depth_score do routing
      4. (Peça 2.3 expandirá: histórico recente, comprimento, tipo detectado)

    Heurística inicial (peça 2.1 — refinada na peça 2.3):
      - Gatilho manual → SEMPRE ativa (com TODOS os refutadores acima)
      - A = Opus → NUNCA ativa (sem refutador acima)
      - A = Haiku + depth_score >= 1 → ativa (só Sonnet refuta — mais próximo)
      - A = Sonnet + depth_score >= 3 → ativa (Opus refuta)
      - Caso contrário → não ativa

    Diferença gatilho manual vs heurística:
      - Heurística normal: usa SÓ o refutador mais próximo (custo controlado)
      - Gatilho manual: traz TODOS os refutadores acima (usuário pediu rigor)

    Args:
        routing_decision: dict retornado por route_model()
        user_message: texto da pergunta do usuário
        history: histórico de turnos (peça 2.3 usará; aqui ignorado)

    Returns:
        dict com:
            - ativar: bool
            - motivo: str (explicação curta)
            - modelos_B: list[str] (vazia se não ativar)
            - forca_manual: bool (True se gatilho explícito do usuário)
    """
    modelo_A = routing_decision.get("model", DEFAULT_MODEL)
    depth_score = routing_decision.get("depth_score", 0)
    tier_A = MODELS.get(modelo_A, {}).get("tier", 1)

    # ── Override absoluto: gatilho manual do usuário ──────────────────
    if forca_camara_detectada(user_message):
        modelos_B = escolher_modelos_B(modelo_A)
        if not modelos_B:
            # Pediu câmara mas A já é Opus (topo) — não pode atender
            return {
                "ativar":       False,
                "motivo":       "gatilho manual, mas A é topo (Opus) — sem refutador acima",
                "modelos_B":    [],
                "forca_manual": True,
            }
        return {
            "ativar":       True,
            "motivo":       "gatilho manual do usuário",
            "modelos_B":    modelos_B,
            "forca_manual": True,
        }

    # ── A = Opus: ápice, sem refutador ────────────────────────────────
    if tier_A == 3:
        return {
            "ativar":       False,
            "motivo":       "A é Opus (ápice — sem refutador acima)",
            "modelos_B":    [],
            "forca_manual": False,
        }

    # ── A = Haiku: ativa câmara se houver qualquer profundidade ──────
    if tier_A == 1:
        if depth_score >= 1:
            modelos_B = escolher_modelos_B(modelo_A)
            return {
                "ativar":       True,
                "motivo":       f"Haiku + depth_score={depth_score} → Sonnet refuta",
                "modelos_B":    modelos_B[:1] if modelos_B else [],
                "forca_manual": False,
            }
        return {
            "ativar":       False,
            "motivo":       "Haiku + pergunta simples — sem necessidade de refutação",
            "modelos_B":    [],
            "forca_manual": False,
        }

    # ── A = Sonnet: ativa câmara se profundidade é alta ──────────────
    if tier_A == 2:
        if depth_score >= 3:
            modelos_B = escolher_modelos_B(modelo_A)
            return {
                "ativar":       True,
                "motivo":       f"Sonnet + depth_score={depth_score} → Opus refuta",
                "modelos_B":    modelos_B,
                "forca_manual": False,
            }
        return {
            "ativar":       False,
            "motivo":       f"Sonnet + depth_score={depth_score} insuficiente",
            "modelos_B":    [],
            "forca_manual": False,
        }

    # ── Default: não ativa ────────────────────────────────────────────
    return {
        "ativar":       False,
        "motivo":       f"tier desconhecido ({tier_A}) — não ativa",
        "modelos_B":    [],
        "forca_manual": False,
    }
