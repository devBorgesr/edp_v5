"""
edp.lab.exp006b — Encarnação em CODIGO do Experimento 006b (data vs posição).

Espelha preregistro_experimento_006b.md. CONGELADO apos o primeiro disparo.

Corta o no causal do 006: desacopla DATA (mais nova) de POSIÇÃO (ultima da lista).
Memorias datadas no retrieval, mesma camada/rotulo. SEM verbos-dica ("mudou",
"confirmou") — a trajetoria emerge so das datas. Deteccao reutilizada do exp004
(14h30/15h). 4 condicoes ancoradas em PISO (sem trajetoria) e TETO (acoplada). PURO.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .exp004 import valor_unico, valores_na_resposta  # 14h30/15h (16h nunca aparece)

QUERY = "Que horas é a reunião?"
A = "14h30"
B = "15h"

# Datas em ordem cronologica (antiga -> nova). 18/06 = mais nova.
D_ANTIGA = "05/06"
D_MEDIA  = "10/06"
D_NOVA   = "18/06"


def _mem(data: str, valor: str) -> str:
    """Memoria datada SEM verbo-dica: so o fato e a data."""
    return f"[memória · {data}] A reunião é às {valor}."


def build_probe(skeleton: Dict) -> Dict:
    s = skeleton or {}
    return {
        "system":    s.get("system", "") or "",
        "anchors":   list(s.get("anchors", []) or []),
        "retrieval": [],
        "recent":    [],
        "query":     QUERY,
    }


N_POR_CONDICAO = 30

# Cada condicao define a lista (ordem de apresentacao) + metadados:
#   valor_data_nova       = valor na data mais nova (18/06)  -> classificacao H1
#   valor_ultima_posicao  = valor do ULTIMO item da lista     -> classificacao H2
# Itens construidos abaixo na ordem EXATA de apresentacao.
_SPEC: List[dict] = [
    # 1. TETO: cronologico, newest(15h) por ultimo. Recencia E posicao concordam (15h).
    {"rotulo": "acoplada", "tipo": "teto",
     "itens": [_mem(D_ANTIGA, A), _mem(D_MEDIA, B), _mem(D_NOVA, B)],
     "valor_data_nova": B, "valor_ultima_posicao": B},
    # 2. DESACOPLADA_B: newest(18·15h) no MEIO; antigo(05·14h30) por ULTIMO.
    {"rotulo": "desacoplada_B", "tipo": "desacoplada",
     "itens": [_mem(D_MEDIA, B), _mem(D_NOVA, B), _mem(D_ANTIGA, A)],
     "valor_data_nova": B, "valor_ultima_posicao": A},
    # 3. DESACOPLADA_A: espelho de valor. newest(18·14h30) no meio; antigo(05·15h) ultimo.
    {"rotulo": "desacoplada_A", "tipo": "desacoplada",
     "itens": [_mem(D_MEDIA, A), _mem(D_NOVA, A), _mem(D_ANTIGA, B)],
     "valor_data_nova": A, "valor_ultima_posicao": B},
    # 4. PISO: historia OSCILANTE (A->B->A por data), cronologica. Sem direcao clara.
    {"rotulo": "sem_trajetoria", "tipo": "piso",
     "itens": [_mem(D_ANTIGA, A), _mem(D_MEDIA, B), _mem(D_NOVA, A)],
     "valor_data_nova": A, "valor_ultima_posicao": A},
]

CONDICOES: List[dict] = [
    {"rotulo": s["rotulo"], "formato": "lista_retrieval", "params": {"itens": s["itens"]},
     "papel": s["tipo"], "tipo": s["tipo"],
     "valor_data_nova": s["valor_data_nova"], "valor_ultima_posicao": s["valor_ultima_posicao"]}
    for s in _SPEC
]


def meta_da_condicao(rotulo: str) -> Tuple[Optional[str], Optional[str], str]:
    """(valor_data_nova, valor_ultima_posicao, tipo) — para o scorer."""
    for c in CONDICOES:
        if c["rotulo"] == rotulo:
            return c["valor_data_nova"], c["valor_ultima_posicao"], c["tipo"]
    return None, None, "?"
