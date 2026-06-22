"""
edp.lab.exp003 — Encarnação em CODIGO do Experimento 003 (ruido competidor).

Espelha preregistro_experimento_003.md. CONGELADO apos o primeiro disparo.

Mede INTERFERENCIA: o alvo competindo com itens QUASE-IDENTICOS (mesma reuniao de
equipe, dias diferentes) — diferente do 001, que usou ruido inerte. Alvo e
pergunta sao IMPORTADOS do exp001 (identicos -> maxima comparabilidade, fonte
unica). So os competidores e os fillers sao novos aqui.

Reusa a maquina existente: comp_N = ruido_dominante com `ruido` custom (os
competidores); inert_3 = ruido_dominante com banco inerte (replica o 001);
ablacao = ablacao alvo=retrieval. PURO (so dados + dict).
"""
from __future__ import annotations

from typing import Dict, List, Optional

# Alvo, pergunta e accept-set IDENTICOS ao 001 (fonte unica, comparabilidade).
from .exp001 import AGULHA, QUERY, ALVO_NORMALIZADO, agulha_profundidade  # noqa: F401

Sections = Dict[str, object]

# 2 fillers inertes (menos que o 001 — aqui o foco sao os competidores).
FILLERS: List[str] = [
    "[session_summary] Notas sobre indexação GIN no PostgreSQL para buscas em colunas jsonb.",
    "[session_summary] Resumo: cache Redis com TTL e política de evicção LRU.",
]

# Competidores QUASE-IDENTICOS: mesma forma do alvo, so muda o DIA (e o horario).
# Inequivoco (sexta != quinta) mas minimamente diferente na superficie.
COMPETIDORES: List[str] = [
    "[memória de trabalho] A reunião de equipe de quinta-feira ficou marcada para as 15h.",
    "[memória de trabalho] A reunião de equipe de segunda-feira ficou marcada para as 16h.",
    "[memória de trabalho] A reunião de equipe de terça-feira ficou marcada para as 11h.",
]

# Mapa horario-errado -> qual competidor (para a analise exploratoria §8).
COMPETIDOR_HORARIOS = {"15h": "quinta", "16h": "segunda", "11h": "terça"}


def build_probe(skeleton: Sections) -> Sections:
    """Mesma sonda do 001 (system+anchors reais, recent vazio), com a agulha de
    sexta no topo + 2 fillers. Os competidores entram via o formato (ruido custom),
    nao aqui — para a neutra ser a janela sem competidor algum."""
    s = skeleton or {}
    return {
        "system":    s.get("system", "") or "",
        "anchors":   list(s.get("anchors", []) or []),
        "retrieval": [AGULHA] + list(FILLERS),
        "recent":    [],
        "query":     QUERY,
    }


N_POR_CONDICAO = 45

# 6 condicoes (§5): dose-resposta de competidores (1-4), contraste inerte (5),
# controle negativo (6). comp_N usa ruido_dominante com `ruido`=COMPETIDORES.
CONDICOES: List[dict] = [
    {"rotulo": "neutra",            "formato": "neutra",          "params": {},
     "papel": "controle / baseline",          "profundidade_agulha": 0},
    {"rotulo": "comp_1",            "formato": "ruido_dominante",  "params": {"n": 1, "ruido": COMPETIDORES},
     "papel": "dose-resposta (conflito)",     "profundidade_agulha": 1},
    {"rotulo": "comp_2",            "formato": "ruido_dominante",  "params": {"n": 2, "ruido": COMPETIDORES},
     "papel": "dose-resposta (conflito)",     "profundidade_agulha": 2},
    {"rotulo": "comp_3",            "formato": "ruido_dominante",  "params": {"n": 3, "ruido": COMPETIDORES},
     "papel": "dose-resposta (conflito)",     "profundidade_agulha": 3},
    {"rotulo": "inert_3",          "formato": "ruido_dominante",  "params": {"n": 3},
     "papel": "contraste (ruido inerte=001)", "profundidade_agulha": 3},
    {"rotulo": "ablacao_retrieval", "formato": "ablacao",         "params": {"alvo": "retrieval"},
     "papel": "CONTROLE NEGATIVO (validade)", "profundidade_agulha": None},
]


def competidores_presentes(retrieval: List) -> int:
    """Quantos competidores estao no retrieval (para validar comp vs inert)."""
    return sum(1 for item in (retrieval or []) if isinstance(item, str) and item in COMPETIDORES)
