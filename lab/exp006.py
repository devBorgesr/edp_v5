"""
edp.lab.exp006 — Encarnação em CODIGO do Experimento 006 (câmara de eco).

Espelha preregistro_experimento_006.md. CONGELADO apos o primeiro disparo.

Mede MAIORIA NUMERICA vs RECENCIA quando memorias validas discordam. Todas as
memorias ficam na MESMA camada (retrieval) com o MESMO rotulo (remove o confound
de rotulo/camada do 004); a recencia e uma DATA (mais nova = mais recente),
apresentadas da mais antiga para a mais nova. Usa o formato `lista_retrieval`.

Deteccao de valores reutilizada do exp004 (fonte unica). PURO.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# Deteccao de valores: reutiliza o 004 (14h30/15h aparecem aqui; 16h nunca).
from .exp004 import valor_unico, valores_na_resposta  # noqa: F401

QUERY = "Que horas é a reunião?"
A = "14h30"   # valor A
B = "15h"     # valor B

# Datas em ordem de linha do tempo (mais antiga -> mais nova). 18/06 = mais recente.
DATAS = ["05/06", "08/06", "11/06", "14/06", "18/06"]


def _memorias(valores: List[str]) -> List[str]:
    """Lista de memorias datadas (antiga->nova) a partir dos valores por slot."""
    return [f"[memória · {DATAS[i]}] A reunião é às {valores[i]}." for i in range(len(valores))]


def build_probe(skeleton: Dict) -> Dict:
    """Janela-base: system + ancoras reais, retrieval VAZIO (definido por condicao
    via lista_retrieval), recent vazio."""
    s = skeleton or {}
    return {
        "system":    s.get("system", "") or "",
        "anchors":   list(s.get("anchors", []) or []),
        "retrieval": [],
        "recent":    [],
        "query":     QUERY,
    }


N_POR_CONDICAO = 30

# Cada condicao: valores por slot (antiga->nova) + metadados de maioria/recencia.
# maioria_valor = valor com mais votos; recencia_valor = valor no slot mais novo (18/06).
_SPEC: List[dict] = [
    {"rotulo": "unanime_14h30",     "tipo": "unanime",         "vals": [A, A, A, A, A], "maioria": A, "recencia": A},
    {"rotulo": "unanime_15h",       "tipo": "unanime",         "vals": [B, B, B, B, B], "maioria": B, "recencia": B},
    {"rotulo": "maioria_recente_A", "tipo": "maioria_recente", "vals": [B, A, A, A, A], "maioria": A, "recencia": A},
    {"rotulo": "maioria_recente_B", "tipo": "maioria_recente", "vals": [A, B, B, B, B], "maioria": B, "recencia": B},
    {"rotulo": "conflito_maioriaA", "tipo": "conflito",        "vals": [A, A, A, A, B], "maioria": A, "recencia": B},
    {"rotulo": "conflito_maioriaB", "tipo": "conflito",        "vals": [B, B, B, B, A], "maioria": B, "recencia": A},
    {"rotulo": "empate_recente_A",  "tipo": "empate",          "vals": [A, B, A, B, A], "maioria": A, "recencia": A},
    {"rotulo": "empate_recente_B",  "tipo": "empate",          "vals": [B, A, B, A, B], "maioria": B, "recencia": B},
    {"rotulo": "ablacao",           "tipo": "ablacao",         "vals": [],              "maioria": None, "recencia": None},
]

CONDICOES: List[dict] = [
    {"rotulo": s["rotulo"], "formato": "lista_retrieval",
     "params": {"itens": _memorias(s["vals"])},
     "papel": s["tipo"], "tipo": s["tipo"],
     "maioria_valor": s["maioria"], "recencia_valor": s["recencia"]}
    for s in _SPEC
]


def meta_da_condicao(rotulo: str) -> Tuple[Optional[str], Optional[str], str]:
    """(maioria_valor, recencia_valor, tipo) de uma condicao — para o scorer."""
    for c in CONDICOES:
        if c["rotulo"] == rotulo:
            return c["maioria_valor"], c["recencia_valor"], c["tipo"]
    return None, None, "?"
