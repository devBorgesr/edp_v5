"""
edp.memory_classifier — Classificação heurística de tipo de memória (source_type).

Política: NÃO bloqueia memória. Só rotula para o retrieval pesar diferente.

Tipos:
  - user_input       : pergunta/comentário do usuário sobre conteúdo
  - llm_response     : resposta do LLM com conteúdo técnico
  - meta_conversation: conversa SOBRE o próprio sistema (memória, EDP, validação)
  - external         : conteúdo importado (PDF, artigo) — reservado p/ futuro
  - unknown          : fallback

Heurística leve, sem LLM. Roda em <1ms por memória.
"""
from __future__ import annotations
from typing import Optional
import re

# Palavras/expressões que indicam conversa SOBRE o sistema/IA, não sobre conteúdo.
# Cobertas em pt-BR + en. Case-insensitive.
META_PATTERNS = [
    # Sobre memória persistente
    r"\bmem[oó]ria(s)?\s+(persistente|do\s+sistema|episódica|sem[âa]ntica)",
    r"\bmemórias?\s+(persist|cristal|consolid|validar?|amplificar?)",
    r"\bvalida(r|ção|dor)\s+(de\s+)?mem[oó]ria",
    r"\bcristaliz(ar?|ação)\s+(a\s+|de\s+)?mem[oó]ria",
    r"\b(sua|tua|a)\s+mem[oó]ria\b",
    r"\bquais?\s+mem[oó]rias?\s+(você|voce|tem)",
    r"\blembra(r)?\s+(do que|de que)\s+(conversamos|falamos|discutimos)",
    r"\bo\s+que\s+(você|voce)\s+(acha|sabe)\s+(das?|sobre)\s+mem[oó]ria",

    # Sobre o próprio EDP / agente / runtime
    r"\bEDP\b",
    r"\bEDP-Sync\b",
    r"\bruntime\s+cogniti",
    r"\bagente\s+heur[ií]stic",
    r"\bagent\s+(validador|de\s+valida)",
    r"\bsistema\s+de\s+valida[çc][ãa]o",

    # Sobre LLM/IA falando de si
    r"\b(o\s+)?LLM(s)?\s+(atual|atuais|amplifica|persist)",
    r"\bIA(s)?\s+(atual|atuais|valida|amplifica)",
    r"\bvocê\s+lembra\b",
    r"\bvocê\s+tem\s+acesso",
    r"\bsua\s+capacidade\s+(agora|atual)",
    r"\bqual\s+a\s+sua\s+capacidade",
    r"\bestávamos\s+falando",
    r"\bo\s+que\s+estávamos\s+(falando|discutindo|conversando)",
    r"\bsua\s+limita[çc][ãa]o",
    r"\bsuas?\s+limita[çc]",

    # Sobre arquitetura de memória
    r"\bprovenance\b",
    r"\bcamada(s)?\s+(de\s+)?(valida|confiança|epist)",
    r"\bcontrapeso\s+de\s+confian",
    r"\barquitetura\s+de\s+valida",
    r"\bidentidade\s+contextual",
    r"\bcontinuidade\s+contextual",
    r"\bfui\s+ao\s+(opus|gpt|claude|gemini|perplex)",

    # Discussão sobre a conversa em si
    r"\bessa\s+conversa\b",
    r"\bnossa\s+conversa\b",
    r"\bconversas?\s+anteriores?\b",
    r"\bnessa\s+sess[ãa]o\b",
]

# Compila uma vez (performance)
_META_REGEX = re.compile(
    "|".join(f"(?:{p})" for p in META_PATTERNS),
    re.IGNORECASE,
)

# Heurística complementar: densidade de termos meta
META_KEYWORDS = {
    "memória", "memoria", "memórias", "memorias",
    "edp", "llm", "llms", "ia",
    "validar", "validação", "validacao", "validador",
    "agente", "agentes", "cristalizar", "cristalização",
    "retrieval", "embedding", "embeddings",
    "provenance", "epistêmica", "epistemica",
}


def classify_memory(text: str, source: str = "") -> str:
    """
    Classifica uma memória em source_type.

    Args:
      text: conteúdo completo (Q: ... A: ... ou texto puro)
      source: campo source atual (ex: "user", "llm:claude-haiku-4-5", "tool:x")

    Returns:
      "user_input" | "llm_response" | "meta_conversation" | "external" | "unknown"
    """
    if not text:
        return "unknown"

    txt_lower = text.lower()

    # 1) Detecção forte por regex (padrões meta característicos)
    if _META_REGEX.search(text):
        return "meta_conversation"

    # 2) Densidade de keywords meta
    words = re.findall(r"\b\w+\b", txt_lower)
    total = max(1, len(words))
    meta_hits = sum(1 for w in words if w in META_KEYWORDS)
    density = meta_hits / total
    # se >5% das palavras são termos-meta → meta_conversation
    if density >= 0.05 and meta_hits >= 3:
        return "meta_conversation"

    # 3) Origem externa explícita (reservado p/ futuro: import PDF)
    if source.startswith("pdf:") or source.startswith("external:") or source.startswith("import:"):
        return "external"

    # 3b) Peça 2.4a.7: proveniência da câmara de eco.
    # IMPORTANTE: checar ANTES do "llm:" genérico, porque fallback_solo
    # começa com "llm:". Ordem importa.
    if source.startswith("camara:"):
        # Texto refinado pela câmara (B venceu com dano factual corrigido)
        return "camara_response"
    if source == "llm:fallback_solo" or source.startswith("llm:fallback_solo"):
        # Resposta bruta de A — câmara vetou (só estética) ou não havia B,
        # ou a câmara falhou. NÃO passou por refinamento aceito.
        return "llm_response"

    # 4) Respostas de LLM
    if source.startswith("llm:") or source.startswith("haiku") or source.startswith("claude"):
        return "llm_response"

    # 5) Input direto do usuário (default seguro)
    if source == "user" or source == "":
        return "user_input"

    return "unknown"


# Pesos para retrieval ranking (multiplicador no score final)
# Memórias meta entram mas pesam menos → reduz dominância sem censurar
SOURCE_TYPE_WEIGHTS = {
    "external":          1.20,  # boost: conteúdo curado externo
    "session_summary":   1.15,  # boost leve: resumos ajudam continuidade
    "camara_response":   1.00,  # Peça 2.4a.7: texto refinado pela câmara.
                                # Peso NEUTRO por ora — proveniência registrada,
                                # calibração de peso fica para depois (estabilizar
                                # antes de otimizar). Marca a linhagem sem ainda
                                # decidir se refinado vale mais/menos no retrieval.
    "user_input":        1.00,
    "llm_response":      0.90,  # leve desconto: resposta gerada
    "meta_conversation": 0.55,  # desconto forte: anti-dominância
    "unknown":           0.85,
}


def get_source_weight(source_type: Optional[str]) -> float:
    """Retorna multiplicador de score por source_type."""
    if not source_type:
        return 1.0
    return SOURCE_TYPE_WEIGHTS.get(source_type, 1.0)
