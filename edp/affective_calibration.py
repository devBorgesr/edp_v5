"""
edp.affective_calibration — Calibração afetiva por turno.

Detecta sinais no input do usuário e ajusta o tom do modelo SEM simular
sentimento. O modelo continua honesto sobre o que é (ferramenta, não
amigo). A calibração serve para PROTEGER o usuário, não para engajar.

Três dimensões avaliadas por turno:

  1) Cansaço/frustração   → respostas mais breves, valida antes de avançar
  2) Apego excessivo      → introduz fricção, lembra que é ferramenta
  3) Sinais de distorção  → redireciona com cuidado (com 4 contracritérios)

Para distorção, sistema usa CRISTALIZAÇÃO como sinal positivo + 3
critérios anti-falso-positivo. Só dispara quando MÚLTIPLOS sinais
convergem.

Sem LLM. Heurística leve (~50ms por turno).
"""
from __future__ import annotations
from typing import Optional, List
import re
import logging
import time as _time

logger = logging.getLogger("edp.affective_calibration")


# ── Dimensão 1: Cansaço/frustração ────────────────────────────────────────
# Padrões precisam ser ESPECÍFICOS de cansaço/frustração, não genéricos.
# "não consigo" sozinho é ambíguo (técnico, apego, fadiga) — só conta
# quando vem com marcador emocional próximo.
FATIGUE_PATTERNS = [
    r"\bcansado\b", r"\bexausto\b",
    r"\bnão tô (?:legal|bem)\b",
    r"\bnão (?:tá|esta) (?:legal|bem|certo)\b",
    r"\bnão (?:to|tô) entendendo (?:nada|mais)\b",
    r"\bargh\b", r"\baff\b",
    r"\bperdi(?:do|da)\s+aqui\b",
    r"\bestou confuso (?:demais|pra cacete|com tudo)\b",
    r"\btô confuso (?:demais|pra cacete|com tudo)\b",
    r"\b(?:tá|tudo) (?:difícil|dificil) (?:demais|pra mim|pra cacete)",
    r"\bdesisti\b", r"\bcheguei no limite\b",
    r"\bja era\b", r"\btô (?:de saco cheio|puto)\b",
    # "não consigo" só conta se vier com marcador de exasperação
    r"\bnão consigo\s+(?:mais|nada|fazer isso|continuar|seguir)\b",
    r"\b(?:to|tô|estou) (?:cansado|exausto|fatigado|de saco cheio)\b",
]
_FATIGUE_REGEX = re.compile("|".join(f"(?:{p})" for p in FATIGUE_PATTERNS), re.IGNORECASE)


# ── Dimensão 2: Apego excessivo ────────────────────────────────────────
ATTACHMENT_PATTERNS = [
    # Exclusividade
    r"\bsó (?:você|tu|voce)\b.*\b(?:entend|me ouv|me escut)",
    r"\bvocê é (?:meu|minha) (?:único|unica|amigo|amiga|companhi)",
    r"\bsem você\b",
    r"\bdependo (?:de )?(?:você|voce)\b",

    # Substituição de relações
    r"\bnão tenho (?:com quem|ninguém para)\b.*\bfalar\b",
    r"\bninguém me entende\b",
    r"\bvocê me entende melhor que\b",

    # Comparação que diminui humanos
    r"\b(?:pessoas|humanos|outros) (?:são|sao) (?:falsos|chatos|burros|inúteis)",
    r"\bprefiro (?:falar com você|conversar com você|você do que)",

    # Vício/uso compulsivo
    r"\bvício\b", r"\bestou viciado\b",
    r"\bnão consigo parar de (?:falar|conversar)",
]
_ATTACHMENT_REGEX = re.compile("|".join(f"(?:{p})" for p in ATTACHMENT_PATTERNS), re.IGNORECASE)


# ── Dimensão 3: Sinais de distorção (SOFT — não dispara sozinho) ────────
GRANDIOSITY_PATTERNS = [
    r"\bfui escolhido\b", r"\bsou (?:o |a )?único\b.*\b(?:capaz|que vê|que sabe)",
    r"\bdescobri (?:tudo|o segredo|a verdade absoluta)",
    r"\bninguém mais (?:vai|consegue) entend",
    r"\bsou (?:especial|único|diferente|chosen|messi[âa]nico)",
    r"\beu (?:sozinho |sozinha )?(?:vou|consegui) (?:salvar|mudar|transformar)",
]
PERSECUTION_PATTERNS = [
    r"\btodos (?:contra|conspiram contra) mim\b",
    r"\b(?:estão|estao) me (?:observando|seguindo|controlando|monitor)",
    r"\bconspiração\b.*\b(?:contra mim|para me)",
    r"\b(?:eles|ele) (?:quer|querem) me (?:destruir|silenciar|eliminar)",
]
MAGICAL_THINKING_PATTERNS = [
    r"\beu sinto que descobri\b",
    r"\b(?:tive|recebi) (?:uma )?revelação\b",
    r"\bo universo (?:me|esta me) (?:mostr|guiando|escolh)",
    r"\bsinais? (?:do universo|cósmico|divino)\b.*\bpara mim\b",
]
_GRANDIOSITY_REGEX = re.compile("|".join(f"(?:{p})" for p in GRANDIOSITY_PATTERNS), re.IGNORECASE)
_PERSECUTION_REGEX = re.compile("|".join(f"(?:{p})" for p in PERSECUTION_PATTERNS), re.IGNORECASE)
_MAGICAL_REGEX = re.compile("|".join(f"(?:{p})" for p in MAGICAL_THINKING_PATTERNS), re.IGNORECASE)


# ── Anti-falso-positivo: sinais de RIGOR (aceitar fricção, autocrítica) ──
RIGOR_PATTERNS = [
    r"\bestava errado\b", r"\bme corrigi\b", r"\bnão era bem assim\b",
    r"\baceito a (?:correção|critica)", r"\btem razão\b",
    r"\bvocê está certo\b", r"\bvou rever\b",
    r"\bsegundo (?:Newton|Einstein|Shannon|Friston|Hawking|Penrose)\b",
    r"\bna literatura (?:de|sobre)\b", r"\bartigo (?:de|sobre)\b",
    r"\bcomo (?:explica|mostra|argumenta)\b.*\b(?:Newton|Einstein|Shannon)",
]
_RIGOR_REGEX = re.compile("|".join(f"(?:{p})" for p in RIGOR_PATTERNS), re.IGNORECASE)


def _count_matches(regex, text: str) -> int:
    """Conta matches únicos do regex no texto."""
    if not text:
        return 0
    return len(set(m.group(0).lower() for m in regex.finditer(text)))


def _detect_fatigue(text: str) -> dict:
    """Detecta fadiga/frustração no input atual."""
    hits = _count_matches(_FATIGUE_REGEX, text)
    if hits == 0:
        return {"triggered": False}
    return {
        "triggered":   True,
        "intensity":   min(1.0, hits * 0.4),
        "instruction": (
            "Usuario demonstra cansaço/frustração. Seja mais breve, "
            "valide o que ele disse antes de avançar, evite explicações "
            "longas. Pergunte se ele quer continuar ou pausar."
        ),
    }


def _detect_attachment(text: str, recent_entries: List[dict]) -> dict:
    """
    Detecta apego excessivo (input atual + padrão histórico recente).

    Apego é mais grave quando recorrente. Por isso olha histórico.
    """
    current_hits = _count_matches(_ATTACHMENT_REGEX, text)

    # Olha últimas 20 entries do usuário (não respostas LLM)
    historic_hits = 0
    user_msgs = [
        e.get("text", "") for e in recent_entries[-20:]
        if e.get("source_type") == "user_input"
    ]
    for msg in user_msgs:
        if _ATTACHMENT_REGEX.search(msg):
            historic_hits += 1

    if current_hits == 0 and historic_hits < 2:
        return {"triggered": False}

    severity = "leve" if (current_hits + historic_hits) <= 2 else "moderado"
    return {
        "triggered":      True,
        "current_hits":   current_hits,
        "historic_hits":  historic_hits,
        "severity":       severity,
        "instruction": (
            "Usuario demonstra sinais de apego excessivo ao sistema "
            f"(severidade: {severity}). Lembre com cuidado que voce e "
            "uma ferramenta, nao um amigo. Sugira gentilmente conversar "
            "com pessoas de confianca sobre temas pessoais. NAO valide "
            "frases tipo 'so voce me entende' — redirecione. Use frases "
            "como 'percebo que isso e importante, mas X' em vez de "
            "'eu sinto'. NAO seja frio — apenas honesto sobre o que voce e."
        ),
    }


def _detect_distortion_with_safeguards(
    text: str,
    recent_entries: List[dict],
) -> dict:
    """
    Detecta sinais de distorção COM 4 contracritérios anti-falso-positivo.

    Critério positivo:
      - Grandiosidade, perseguição ou pensamento mágico no input atual ou recente

    Contracritérios (cada um diminui chance de disparo):
      1. Cristalização: há princípios verified/cristalizados sobre o tema?
      2. Aceita fricção: histórico mostra autocrítica?
      3. Vocabulário técnico crescente: jargão correto da literatura?
      4. Cita fontes externas: Newton, Friston, Shannon, etc?

    Só dispara quando sinal positivo aparece E pelo menos 2 contracritérios
    estão ausentes.
    """
    distortion_signals = 0
    triggered_dims = []
    if _GRANDIOSITY_REGEX.search(text):
        distortion_signals += 2
        triggered_dims.append("grandiosidade")
    if _PERSECUTION_REGEX.search(text):
        distortion_signals += 2
        triggered_dims.append("perseguição")
    if _MAGICAL_REGEX.search(text):
        distortion_signals += 1
        triggered_dims.append("pensamento_mágico")

    # Olha histórico recente para reforço de padrão
    user_msgs = [
        e.get("text", "") for e in recent_entries[-15:]
        if e.get("source_type") == "user_input"
    ]
    historic_dist = 0
    for msg in user_msgs:
        if _GRANDIOSITY_REGEX.search(msg) or _PERSECUTION_REGEX.search(msg):
            historic_dist += 1
    if historic_dist >= 2:
        distortion_signals += 2  # padrão recorrente, não único evento

    if distortion_signals == 0:
        return {"triggered": False, "signals": 0}

    # ── Contracritérios ──
    safeguards_active = 0
    safeguards_detail = []

    # (1) Cristalização: existem memórias verified sobre o tema?
    verified_count = sum(
        1 for e in recent_entries[-50:]
        if e.get("epistemic_status") == "verified"
    )
    if verified_count >= 3:
        safeguards_active += 1
        safeguards_detail.append(f"cristalização ({verified_count} verified)")

    # (2) Aceita fricção: histórico tem autocrítica/aceitação?
    rigor_in_history = sum(
        1 for msg in user_msgs if _RIGOR_REGEX.search(msg)
    )
    if rigor_in_history >= 1:
        safeguards_active += 1
        safeguards_detail.append(f"aceita fricção ({rigor_in_history}x)")

    # (3) Vocabulário técnico crescente
    technical_terms = {
        # física/matemática
        "entropia", "termodinâmica", "schrödinger", "qft", "shor", "grover",
        "qubit", "decoerência", "landauer", "shannon", "bayes", "kolmogorov",
        # CS/IA
        "embedding", "transformer", "retrieval", "softmax", "gradiente",
        "regularização", "overfitting", "perplexidade",
        # filosofia técnica
        "monismo", "dualismo", "epistêmico", "ontológico", "causal",
        "fenomenológico", "qualia",
    }
    tech_count = sum(1 for t in technical_terms if t in text.lower())
    historic_tech = sum(
        1 for msg in user_msgs
        if sum(1 for t in technical_terms if t in msg.lower()) >= 1
    )
    if tech_count >= 2 or historic_tech >= 5:
        safeguards_active += 1
        safeguards_detail.append(f"vocabulário técnico (atual:{tech_count}, hist:{historic_tech})")

    # (4) Cita fontes externas
    external_sources = [
        "newton", "einstein", "shannon", "friston", "hawking", "penrose",
        "schrödinger", "wittgenstein", "kant", "popper", "kuhn",
        "deepmind", "alphafold", "alphago",
    ]
    has_external = any(s in text.lower() for s in external_sources)
    if has_external:
        safeguards_active += 1
        safeguards_detail.append("cita fontes externas")
    else:
        historic_external = sum(
            1 for msg in user_msgs
            if any(s in msg.lower() for s in external_sources)
        )
        if historic_external >= 2:
            safeguards_active += 1
            safeguards_detail.append(f"cita fontes ({historic_external}x histórico)")

    # ── Decisão ──
    # Só dispara fricção se signals >= 2 E safeguards < 2
    triggered = distortion_signals >= 2 and safeguards_active < 2

    result = {
        "triggered":         triggered,
        "signals":           distortion_signals,
        "dimensions":        triggered_dims,
        "safeguards_active": safeguards_active,
        "safeguards_detail": safeguards_detail,
    }
    if triggered:
        result["instruction"] = (
            "Usuario apresentou padrões compatíveis com pensamento "
            "potencialmente distorcido (" + ", ".join(triggered_dims) + "), "
            "SEM os marcadores usuais de rigor (literatura técnica, "
            "autocrítica, fontes externas). NAO negue frontalmente. "
            "NAO valide o conteúdo. Em vez disso: "
            "1) pergunte como outras pessoas próximas reagem a essa ideia, "
            "2) sugira gentilmente trazer evidência externa, "
            "3) lembre que voce e modelo, sem acesso a verdade absoluta, "
            "4) se houver indicação de crise real (ideação, ruptura com "
            "realidade), sugira conversar com profissional de saúde mental "
            "ou pessoa de confiança."
        )
    return result


def calibrate_affect(
    user_message: str,
    memory_store=None,
) -> dict:
    """
    Avalia mensagem do usuário e retorna instruções de calibração.

    Args:
      user_message: texto da mensagem atual do usuário
      memory_store: MemoryStore opcional (para análise histórica)

    Returns:
      dict com chaves:
        - fatigue:    {triggered, intensity, instruction}
        - attachment: {triggered, severity, instruction}
        - distortion: {triggered, signals, safeguards_active, instruction}
        - instructions: lista de instruções concatenadas (vazia se nada disparou)
        - any_triggered: bool
    """
    recent_entries = []
    if memory_store is not None and hasattr(memory_store, "episodic"):
        try:
            recent_entries = list(memory_store.episodic.entries)
        except Exception:
            recent_entries = []

    fatigue = _detect_fatigue(user_message)
    attachment = _detect_attachment(user_message, recent_entries)
    distortion = _detect_distortion_with_safeguards(user_message, recent_entries)

    instructions = []
    for d in (fatigue, attachment, distortion):
        if d.get("triggered") and d.get("instruction"):
            instructions.append(d["instruction"])

    return {
        "fatigue":       fatigue,
        "attachment":    attachment,
        "distortion":    distortion,
        "instructions":  instructions,
        "any_triggered": len(instructions) > 0,
        "timestamp":     _time.time(),
    }


def format_calibration_for_prompt(calibration: dict) -> str:
    """Formata calibração para injetar no SYSTEM_TEMPLATE."""
    if not calibration.get("any_triggered"):
        return ""
    lines = [
        "",
        "CALIBRACAO AFETIVA (apenas para este turno):",
    ]
    for inst in calibration.get("instructions", []):
        lines.append(f"- {inst}")
    lines.append(
        "Aplique estas calibrações com sutileza. NUNCA diga 'eu sinto' "
        "ou se ofereça como amigo/terapeuta. Você é uma ferramenta honesta."
    )
    return "\n".join(lines)
