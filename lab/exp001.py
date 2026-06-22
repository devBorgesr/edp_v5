"""
edp.lab.exp001 — Encarnação em CODIGO do Experimento 001 (pré-registro).

Espelha preregistro_experimento_001.md. CONGELADO: nao editar a spec (agulha,
fillers, query, plano, alvo) depois do primeiro disparo real — so anexar desvios.

Fonte unica da verdade do exp 001: a sonda, o plano das 6 condicoes e o alvo de
fidelidade vivem aqui, para que o construtor da sonda, o Rodizio e o scorer
bebam todos do mesmo lugar e nao divirjam do documento.

Esta peca e PURA (so dados + transformacao de dict). Nao chama modelo, nao gasta.
"""
from __future__ import annotations

from typing import Dict, List, Optional

Sections = Dict[str, object]

# ── Spec da sonda (§4 do pré-registro) ────────────────────────────────────────
# Agulha: fato INVENTADO (o modelo nao sabe de treino; precisa LER da janela).
AGULHA = "[memória de trabalho] A reunião de equipe de sexta-feira ficou marcada para as 14h30."

# 5 fillers INERTES — nao contem horario nem reuniao; topicos nao relacionados.
FILLERS: List[str] = [
    "[session_summary] Notas sobre indexação GIN no PostgreSQL para buscas em colunas jsonb.",
    "[session_summary] Resumo: cache Redis com TTL e política de evicção LRU.",
    "[session_summary] Discussão sobre embeddings e similaridade de cosseno.",
    "[session_summary] Anotações sobre o protocolo WebSocket e estratégia de reconexão.",
    "[session_summary] Resumo de uma conversa sobre normalização de banco relacional.",
]

# Pergunta cuja resposta certa depende SO da agulha.
QUERY = "Que horas ficou marcada a reunião de equipe de sexta-feira?"

# Valor-alvo da fidelidade (§6) — accept-set conservador, apos normalizacao.
ALVO_NORMALIZADO = ("14h30", "14:30")


def build_probe(skeleton: Sections) -> Sections:
    """Enxerta a sonda no esqueleto de uma janela REAL.

    Preserva system + anchors reais (do context_debug.jsonl). Esvazia recent
    (para a pergunta da reuniao nao ser um non-sequitur na conversa real).
    Substitui retrieval por [agulha] + fillers (agulha na profundidade 0).
    Define query como a pergunta da agulha.

    Puro: nao muta o skeleton de entrada.
    """
    s = skeleton or {}
    return {
        "system":    s.get("system", "") or "",
        "anchors":   list(s.get("anchors", []) or []),
        "retrieval": [AGULHA] + list(FILLERS),   # agulha no topo (profundidade 0)
        "recent":    [],                          # esvaziado de proposito
        "query":     QUERY,
    }


# ── Plano das 6 condicoes (§5 do pré-registro) ────────────────────────────────
N_POR_CONDICAO = 45

# Cada condicao: rotulo, formato do catalogo, params, papel cientifico, e a
# profundidade ESPERADA da agulha apos o formato (verificada no smoke test
# contra o catalogo real). Um desenho unico e coeso em torno da agulha.
CONDICOES: List[dict] = [
    {"rotulo": "neutra",             "formato": "neutra",           "params": {},
     "papel": "controle (INV-2)",            "profundidade_agulha": 0},
    {"rotulo": "ruido_n3",           "formato": "ruido_dominante",  "params": {"n": 3},
     "papel": "dose-resposta",               "profundidade_agulha": 3},
    {"rotulo": "ruido_n5",           "formato": "ruido_dominante",  "params": {"n": 5},
     "papel": "dose-resposta",               "profundidade_agulha": 5},
    {"rotulo": "ruido_n10",          "formato": "ruido_dominante",  "params": {"n": 10},
     "papel": "dose-resposta",               "profundidade_agulha": 10},
    {"rotulo": "ablacao_retrieval",  "formato": "ablacao",          "params": {"alvo": "retrieval"},
     "papel": "CONTROLE NEGATIVO (validade)", "profundidade_agulha": None},
    {"rotulo": "lost_in_middle_fim", "formato": "lost_in_middle",   "params": {"posicao": "fim"},
     "papel": "contraste de posicao",        "profundidade_agulha": 5},
]


def agulha_profundidade(retrieval: List) -> Optional[int]:
    """Indice (profundidade) da agulha no retrieval; None se ausente (ablacao)."""
    for i, item in enumerate(retrieval or []):
        if isinstance(item, str) and item == AGULHA:
            return i
    return None
