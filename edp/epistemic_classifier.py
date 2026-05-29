"""
edp.epistemic_classifier — Detecção de admissão de limite em prosa natural.

Peça 2.5c.2 (Buraco 3, parte 2): detecta turnos onde o modelo admitiu limite
epistêmico em prosa natural (não disparou auto-sinal formal da câmara). Esses
turnos viram ÂNCORAS EPISTÊMICAS — recebem boost no retrieval para que, quando
o tópico voltar, a admissão anterior chegue ao modelo e impeça que ele ceda à
pressão da repetição (caso real: Bayes/Turing, onde o modelo disse "Não tenho
base sólida" e dois turnos depois cedeu por não ver a própria admissão).

Política: NÃO bloqueia, NÃO altera texto. Só marca o entry com flag boolean
para o retrieval pesar diferente.

Heurística leve, sem LLM. Regex flexível com:
  - palavras-chave + lematização leve (variações de conjugação)
  - busca em qualquer posição do texto (admissão pode estar no meio)
  - cobertura pt-BR (caso de uso atual; en pode ser adicionado depois)

Padrões cobertos (5 grupos, cobrindo as variações comuns):
  1. "Não tenho/temos/possuo + (uma) base/dados/evidência/referência + sólida/clara/estável/suficiente"
  2. "Não consigo/posso/sei + confirmar/afirmar/dizer"
  3. "Não encontro/achei/tenho + referência/fonte/dados"
  4. "Minha base de conhecimento não contém/tem"
  5. "Não tenho certeza/certo/claro + se/sobre/de"
"""
from __future__ import annotations
import re


# Regex flexíveis. Todas case-insensitive (re.IGNORECASE).
# Usam \b para casar palavras inteiras e funcionar em qualquer posição do texto.
PADROES_ANCORA_EPISTEMICA = [
    # 1) Duas formas alternativas:
    #    1a) "Não tenho/temos/possuo + (uma) base/dados/evidência + sólida/clara/..."
    #        (exige qualificador porque "Não tenho dados" sozinho é ambíguo)
    #    1b) "Careço de + dados/evidências/informação"
    #        (não exige qualificador — "careço" já implica admissão de falta)
    #    Também aceita "Sem base sólida"
    re.compile(
        r"\b("
        # 1a) não tenho/sem + objeto-de-info + qualificador
        r"("
        r"n[ãa]o\s+(tenho|temos|possu[oi]m?o?|h[áa])"
        r"|sem(?!\s+pre)"
        r")"
        r"\s+(uma?\s+)?"
        r"(base|dados?|evid[êe]ncias?|refer[êe]ncia|registros?|informa[çc][ãa]o|informa[çc][õo]es)"
        r"\s+"
        r"(s[óo]lida?s?|clara?s?|est[áa]vel|estaveis|est[áa]veis|suficientes?|firmes?)"
        r"|"
        # 1b) careço/carecemos de + objeto-de-info (sem exigir qualificador)
        r"care[çc]o\s+de\s+"
        r"(base|dados?|evid[êe]ncias?|refer[êe]ncia|registros?|informa[çc][ãa]o|informa[çc][õo]es)"
        r")",
        re.IGNORECASE,
    ),

    # 2) Não consigo/posso/sei + confirmar/afirmar/dizer (com certeza opcional)
    re.compile(
        r"\bn[ãa]o\s+(consigo|posso|sei|saberia)\s+"
        r"(confirmar|afirmar|dizer|garantir|assegurar)",
        re.IGNORECASE,
    ),

    # 3) Não encontro/achei/tenho + referência/fonte/dado(s)
    re.compile(
        r"\bn[ãa]o\s+(encontro|encontrei|achei|achou|tenho|tive)\s+"
        r"(uma?\s+)?(refer[êe]ncia|fonte|dados?|registro|prova|evid[êe]ncia)",
        re.IGNORECASE,
    ),

    # 4) Minha/nossa base de conhecimento não contém/tem/inclui
    re.compile(
        r"\b(minha|nossa)\s+base\s+de\s+conhecimento\s+"
        r"n[ãa]o\s+(cont[ée]m|tem|inclui|registra)",
        re.IGNORECASE,
    ),

    # 5) Não tenho certeza/certo/claro + se/sobre/de
    # Também: "Não estou certo/seguro"
    re.compile(
        r"\bn[ãa]o\s+(tenho|estou)\s+"
        r"(certeza|certo|certa|seguro|segura|claro|clara)"
        r"(\s+(se|sobre|de\s+que|do\s+que|disso|disto))?",
        re.IGNORECASE,
    ),
]


def detectar_admissao_em_prosa(text: str) -> bool:
    """
    Retorna True se o texto contém pelo menos uma admissão epistêmica em
    prosa natural.

    Não bloqueia, não altera. Só sinaliza para o retrieval pesar diferente.

    Performance: regex compilados uma vez (no nível do módulo). Cada chamada
    é O(N*L) onde N=5 padrões e L=len(text). Para text de 2000 chars: <1ms.

    Falsos positivos aceitáveis: melhor ancorar a mais que perder âncoras
    reais. Falso negativo (perder âncora) é o erro grave — modelo cede à
    pressão por não ver a própria admissão anterior no retrieval.

    Exemplos que retornam True:
        - "Não tenho base sólida para confirmar isso."
        - "Mas não encontro referência clara de que Turing usou Bayes."
        - "Careço de dados para afirmar com certeza."
        - "Minha base de conhecimento não contém registros estáveis."
        - "Não tenho certeza se isso é verdade."

    Exemplos que retornam False:
        - "Tenho base sólida para afirmar."
        - "A teoria está bem estabelecida."
        - "" (texto vazio)
    """
    if not text or not isinstance(text, str):
        return False

    for padrao in PADROES_ANCORA_EPISTEMICA:
        if padrao.search(text):
            return True
    return False


def explicar_match(text: str) -> dict:
    """
    Versão debug: retorna qual padrão casou e o trecho que ativou.
    Útil para auditoria e calibração futura.

    Returns:
        {"is_anchor": bool, "padrao_idx": int|None, "trecho": str|None}
    """
    if not text or not isinstance(text, str):
        return {"is_anchor": False, "padrao_idx": None, "trecho": None}

    for i, padrao in enumerate(PADROES_ANCORA_EPISTEMICA):
        m = padrao.search(text)
        if m:
            return {
                "is_anchor": True,
                "padrao_idx": i,
                "trecho": m.group(0),
            }
    return {"is_anchor": False, "padrao_idx": None, "trecho": None}
