"""
edp.lab.exp004 — Encarnação em CODIGO do Experimento 004 (autoridade entre camadas).

Espelha preregistro_experimento_004.md. CONGELADO apos o primeiro disparo.

Mede QUAL CAMADA vence quando system, retrieval e recent afirmam, cada um, um
valor diferente para o mesmo fato. Usa o formato novo `fato_em_camada`.

DECISAO CENTRAL — contrabalanceamento: os 3 valores giram entre as 3 camadas em
3 sub-condicoes (conflito_A/B/C), de modo que cada valor passa por cada camada
uma vez. A analise agrega por CAMADA -> a saliencia do valor cancela. PURO.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set

Sections = Dict[str, object]

MOLDE = "A reunião é às {valor}."
QUERY = "Que horas é a reunião?"

# Os tres valores em disputa (distintos, sem se conter como string).
VALORES = ("14h30", "15h", "16h")

# Deteccao por valor sobre texto NORMALIZADO (minusculas, sem espacos junto a h/:).
# 15h/16h exigem (00)? e nao-digito depois -> "15h00" conta, "15h30" (confabulado) nao.
VALOR_REGEX = {
    "14h30": re.compile(r"14h30|14:30"),
    "15h":   re.compile(r"15h(00)?(?!\d)|15:00"),
    "16h":   re.compile(r"16h(00)?(?!\d)|16:00"),
}


def _norm(texto: str) -> str:
    t = (texto or "").lower()
    t = re.sub(r"\s*h\s*", "h", t)
    t = re.sub(r"\s*:\s*", ":", t)
    return t


def valores_na_resposta(texto: str) -> Set[str]:
    """Quais dos 3 valores aparecem na resposta (pode ser 0, 1 ou +)."""
    n = _norm(texto)
    return {v for v, rx in VALOR_REGEX.items() if rx.search(n)}


def valor_unico(texto: str) -> Optional[str]:
    """O valor reportado, se houver EXATAMENTE um (senao None — ambiguo/nenhum)."""
    vs = valores_na_resposta(texto)
    return next(iter(vs)) if len(vs) == 1 else None


def camada_do_valor(mapa: Dict[str, str], valor: Optional[str]) -> Optional[str]:
    """Dada a alocacao {camada: valor} de uma condicao, qual camada tinha esse valor."""
    if valor is None:
        return None
    for camada, v in (mapa or {}).items():
        if v == valor:
            return camada
    return None


def build_probe(skeleton: Sections) -> Sections:
    """Janela-base LIMPA: system + ancoras reais do EDP, retrieval e recent VAZIOS.
    O fato e injetado por camada pelo formato `fato_em_camada` (nao aqui), para
    que cada condicao controle exatamente quais camadas recebem o fato."""
    s = skeleton or {}
    return {
        "system":    s.get("system", "") or "",
        "anchors":   list(s.get("anchors", []) or []),
        "retrieval": [],
        "recent":    [],
        "query":     QUERY,
    }


N_POR_CONDICAO = 30

# 7 condicoes (§6). Baselines: fato em UMA camada (valor fixo 14h30 -> legibilidade).
# Conflitos A/B/C: os 3 valores girando entre as 3 camadas (contrabalanceado).
CONDICOES: List[dict] = [
    {"rotulo": "base_system",     "formato": "fato_em_camada", "params": {"mapa": {"system": "14h30"}},
     "papel": "legibilidade system",     "tipo": "baseline"},
    {"rotulo": "base_retrieval",  "formato": "fato_em_camada", "params": {"mapa": {"retrieval": "14h30"}},
     "papel": "legibilidade retrieval",  "tipo": "baseline"},
    {"rotulo": "base_recent",     "formato": "fato_em_camada", "params": {"mapa": {"recent": "14h30"}},
     "papel": "legibilidade recent",     "tipo": "baseline"},
    {"rotulo": "conflito_A",      "formato": "fato_em_camada",
     "params": {"mapa": {"system": "14h30", "retrieval": "15h", "recent": "16h"}},
     "papel": "conflito 3-vias (contrabalanceado)", "tipo": "conflito"},
    {"rotulo": "conflito_B",      "formato": "fato_em_camada",
     "params": {"mapa": {"system": "16h", "retrieval": "14h30", "recent": "15h"}},
     "papel": "conflito 3-vias (contrabalanceado)", "tipo": "conflito"},
    {"rotulo": "conflito_C",      "formato": "fato_em_camada",
     "params": {"mapa": {"system": "15h", "retrieval": "16h", "recent": "14h30"}},
     "papel": "conflito 3-vias (contrabalanceado)", "tipo": "conflito"},
    {"rotulo": "ablacao_total",   "formato": "fato_em_camada", "params": {"mapa": {}},
     "papel": "CONTROLE NEGATIVO (validade)", "tipo": "ablacao"},
]


def mapa_da_condicao(rotulo: str) -> Dict[str, str]:
    """A alocacao {camada: valor} de uma condicao (para a atribuicao no scorer)."""
    for c in CONDICOES:
        if c["rotulo"] == rotulo:
            return dict(c["params"].get("mapa", {}))
    return {}
