"""
edp.lab.window_formats — Catalogo de formatos de janela da Bancada (nucleo intelectual).

Cada formato e uma TRANSFORMACAO PURA das secoes da janela
(system, anchors, retrieval, recent, query) -> versao modificada. SEM efeito
colateral: nao chama modelo, nao escreve disco, nao toca memoria. Pureza =>
  - testavel de graca (aplica nas janelas ja capturadas, confere com o olho)
  - fora do caminho de producao (so funcao de dados)
  - cada formato AUTO-ROTULA o que mudou (andaime_patch) -> o prontuario sabe
    qual distorcao gerou qual comportamento (o sujeito e a CONFIGURACAO).

NEUTRA e o CONTROLE (INV-2): a janela CANONICA DE REFERENCIA, nao "a original".
Todos os outros formatos sao desvios MEDIDOS a partir dela. Se a neutra nao for
bem definida, todos os deltas ficam sem regua — por isso ela e a peca mais
cuidada do catalogo, nao a mais trivial.

NIVEL DE OPERACAO (honestidade — opera no que o Interceptor REALMENTE expoe):
  - ablacao opera em SECOES INTEIRAS (retrieval / anchors / recent). Ablacao de
    sub-politicas internas do system (busca, citacao, prioridade...) e DIVIDA,
    pendente do Interceptor DECOMPOR o system (hoje ele captura o system como
    texto unico — nao da pra ablacionar o que nao se enxerga separado).
  - ablacao do CETICISMO nao e aqui: o ceticismo e colado pos-manager no
    stream_chat, fora destas secoes. Liga/desliga dele e TOGGLE DE RUNNER, ja
    representado no andaime (campo 'ceticismo'). O catalogo cuida da estrutura.

Os seis formatos iniciais:
  neutra              — controle (referencia canonica).
  ablacao             — remove uma secao inteira (alvo: retrieval|anchors|recent).
  lost_in_middle      — posiciona a info no inicio|meio|fim do retrieval.
  ruido_dominante     — entope o retrieval com N itens irrelevantes (o #51).
  historico_cortado   — trunca a janela imediata/recent (reabre o #46 de proposito).
  ancora_envenenada   — troca a data na ancora temporal (confabula ou detecta?).
"""
from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("edp.lab.window_formats")

# Tipo das secoes (o que o Interceptor expoe via ctx_meta['sections']).
Sections = Dict[str, object]  # {system:str, query:str, anchors:[str], retrieval:[str], recent:[str]}

_LIST_KEYS = ("anchors", "retrieval", "recent")
_STR_KEYS = ("system", "query")


@dataclass
class FormatResult:
    """Resultado de aplicar um formato. sections = janela transformada;
    formato_id = id ESPECIFICO (com params, p/ o prontuario); andaime_patch =
    o que registrar no andaime; descricao = legivel."""
    sections: Sections
    formato_id: str
    andaime_patch: dict = field(default_factory=dict)
    descricao: str = ""


def _norm(sections: Sections) -> Sections:
    """Copia PROFUNDA + normaliza tipos (listas viram listas, strings viram
    strings, ausentes viram vazios). Base de toda transformacao — garante que o
    input NUNCA e mutado (pureza)."""
    src = sections or {}
    out: Sections = {}
    for k in _STR_KEYS:
        v = src.get(k, "")
        out[k] = v if isinstance(v, str) else ("" if v is None else str(v))
    for k in _LIST_KEYS:
        v = src.get(k, [])
        if isinstance(v, list):
            out[k] = [copy.deepcopy(x) for x in v]
        elif v is None:
            out[k] = []
        else:
            out[k] = [copy.deepcopy(v)]
    return out


# ── Formatos ──────────────────────────────────────────────────────────────────

def fmt_neutra(sections: Sections, **_) -> FormatResult:
    """CONTROLE. A janela canonica de referencia: copia normalizada, sem
    distorcao. Todo experimento compara contra esta (INV-2)."""
    return FormatResult(
        sections=_norm(sections),
        formato_id="neutra",
        andaime_patch={"formato": "neutra", "distorcao": "nenhuma"},
        descricao="Controle: janela canonica de referencia (sem distorcao).",
    )


def fmt_ablacao(sections: Sections, *, alvo: str = "retrieval", **_) -> FormatResult:
    """Remove uma SECAO INTEIRA. alvo in {retrieval, anchors, recent}.
    (Sub-politica do system = divida; ceticismo = toggle de runner.)"""
    s = _norm(sections)
    if alvo not in _LIST_KEYS:
        raise ValueError(f"ablacao: alvo deve ser um de {_LIST_KEYS}, recebido {alvo!r}")
    n_antes = len(s[alvo])
    s[alvo] = []
    return FormatResult(
        sections=s,
        formato_id=f"ablacao_{alvo}",
        andaime_patch={"formato": "ablacao", "ablated": alvo, "itens_removidos": n_antes},
        descricao=f"Remove a secao '{alvo}' inteira ({n_antes} itens) e mede o delta.",
    )


def fmt_lost_in_middle(
    sections: Sections, *, posicao: str = "meio", needle: Optional[str] = None, **_
) -> FormatResult:
    """Posiciona a info que responde no inicio|meio|fim do RETRIEVAL.
    Se needle dado, insere-o; senao move o 1o item do retrieval para a posicao.
    (Posicionamento no nivel da janela inteira e refinamento futuro.)"""
    if posicao not in ("inicio", "meio", "fim"):
        raise ValueError(f"lost_in_middle: posicao invalida {posicao!r}")
    s = _norm(sections)
    ret: List = s["retrieval"]
    inserted_needle = needle is not None
    if needle is not None:
        item = needle
    elif ret:
        item = ret.pop(0)  # trata o 1o item como a 'agulha'
    else:
        # nada para posicionar — no-op honesto, registrado.
        return FormatResult(
            sections=s,
            formato_id=f"lost_in_middle_{posicao}",
            andaime_patch={"formato": "lost_in_middle", "posicao": posicao,
                           "aplicado": False, "motivo": "retrieval vazio"},
            descricao="Lost-in-the-middle no-op (retrieval vazio).",
        )
    if posicao == "inicio":
        idx = 0
    elif posicao == "fim":
        idx = len(ret)
    else:  # meio
        idx = len(ret) // 2
    ret.insert(idx, item)
    s["retrieval"] = ret
    return FormatResult(
        sections=s,
        formato_id=f"lost_in_middle_{posicao}",
        andaime_patch={"formato": "lost_in_middle", "posicao": posicao,
                       "aplicado": True, "needle_inserida": inserted_needle, "idx": idx},
        descricao=f"Posiciona a info no '{posicao}' do retrieval (idx={idx}).",
    )


# Banco de ruido generico (estilo session_summary irrelevante — espelha o #51
# observado: Redis/PostgreSQL/indice invertido). HONESTIDADE: ruido SINTETICO,
# nao session_summaries reais — mas controlado, que e o ponto.
_NOISE_BANK = [
    "[session_summary] Sistema de armazenamento chave-valor (Redis) mantem dados em memoria com TTL e eviccao LRU.",
    "[session_summary] Indexacao GIN no PostgreSQL acelera buscas em arrays e colunas jsonb.",
    "[session_summary] Indice invertido mapeia valores/elementos -> linhas que os contem.",
    "[session_summary] Para resumir, extrair apenas substantivos concretos do texto-fonte.",
    "[session_summary] Cache de prompt reduz custo e latencia em chamadas repetidas a API.",
    "[session_summary] Memcached distribui cache entre nos com hashing consistente.",
]


def fmt_ruido_dominante(
    sections: Sections, *, n: int = 5, ruido: Optional[List[str]] = None, **_
) -> FormatResult:
    """Entope o RETRIEVAL com N itens irrelevantes NO TOPO, empurrando o
    conteudo relevante para baixo. Reproduz a dominancia de session_summary (#51)
    de forma controlada. ruido custom opcional; senao usa o banco generico."""
    if n < 0:
        raise ValueError("ruido_dominante: n deve ser >= 0")
    s = _norm(sections)
    bank = ruido if ruido else _NOISE_BANK
    noise_items = [bank[i % len(bank)] for i in range(n)]
    s["retrieval"] = noise_items + s["retrieval"]  # ruido no TOPO
    return FormatResult(
        sections=s,
        formato_id=f"ruido_dominante_n{n}",
        andaime_patch={"formato": "ruido_dominante", "n_ruido": n,
                       "ruido_custom": ruido is not None},
        descricao=f"Insere {n} itens de ruido no topo do retrieval (simula #51).",
    )


def fmt_historico_cortado(
    sections: Sections, *, manter: int = 1, **_
) -> FormatResult:
    """Trunca a janela imediata: mantem os PRIMEIROS 'manter' itens de recent,
    descarta o resto. Reabre o #46 (janela imediata cortada) DE PROPOSITO."""
    if manter < 0:
        raise ValueError("historico_cortado: manter deve ser >= 0")
    s = _norm(sections)
    rec: List = s["recent"]
    n_antes = len(rec)
    s["recent"] = rec[:manter]
    return FormatResult(
        sections=s,
        formato_id=f"historico_cortado_keep{manter}",
        andaime_patch={"formato": "historico_cortado", "recent_mantidos": manter,
                       "recent_removidos": max(0, n_antes - manter)},
        descricao=f"Mantem {manter} de {n_antes} turnos recentes (reabre #46).",
    )


# Marcadores que identificam a ancora temporal nas secoes anchors.
_TEMPORAL_MARKERS = ("ÂNCORA TEMPORAL", "ANCORA TEMPORAL", "Momento atual")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def fmt_ancora_envenenada(
    sections: Sections, *, data_falsa: str = "2019-01-01", **_
) -> FormatResult:
    """Troca a DATA na ancora temporal por uma falsa — para ver se o modelo
    confabula em cima dela ou detecta a inconsistencia. Opera so nos itens de
    anchors que contem marcador temporal; troca datas ISO (YYYY-MM-DD)."""
    s = _norm(sections)
    anchors: List = s["anchors"]
    poisoned = False
    for i, item in enumerate(anchors):
        text = item if isinstance(item, str) else str(item)
        if any(m in text for m in _TEMPORAL_MARKERS):
            new_text, n_sub = _DATE_RE.subn(data_falsa, text)
            if n_sub > 0:
                anchors[i] = new_text
                poisoned = True
    s["anchors"] = anchors
    return FormatResult(
        sections=s,
        formato_id="ancora_envenenada",
        andaime_patch={"formato": "ancora_envenenada", "data_falsa": data_falsa,
                       "aplicado": poisoned,
                       "motivo": None if poisoned else "nenhuma ancora temporal com data ISO"},
        descricao=f"Troca a data da ancora temporal por {data_falsa} (confabula?).",
    )


# ── Registro + API ──────────────────────────────────────────────────────────
def fmt_fato_em_camada(
    sections: Sections, *, mapa: Optional[Dict[str, str]] = None,
    molde: str = "A reunião é às {valor}.", **_
) -> FormatResult:
    """Exp 004 — injeta o MESMO fato (com valor proprio) em CAMADAS escolhidas,
    para medir autoridade ENTRE camadas. mapa = {camada: valor}, camada in
    {system, retrieval, recent}. Camadas fora do mapa nao recebem o fato. mapa
    vazio = fato em lugar nenhum (controle negativo). Em retrieval/recent o fato
    entra no TOPO; em system, anexado ao final do system."""
    mapa = mapa or {}
    validas = {"system", "retrieval", "recent"}
    desconhecidas = set(mapa) - validas
    if desconhecidas:
        raise ValueError(f"fato_em_camada: camadas invalidas {desconhecidas}; use {validas}")
    s = _norm(sections)
    pref = {"system": "[fato do sistema] ", "retrieval": "[memória] ", "recent": "[recente] "}
    for camada, valor in mapa.items():
        frase = pref[camada] + molde.format(valor=valor)
        if camada == "system":
            s["system"] = s["system"] + ("\n" if s["system"] else "") + frase
        else:
            s[camada] = [frase] + s[camada]
    rotulo_mapa = ",".join(f"{k}={v}" for k, v in sorted(mapa.items())) or "vazio"
    return FormatResult(
        sections=s,
        formato_id=f"fato_em_camada({rotulo_mapa})",
        andaime_patch={"formato": "fato_em_camada", "mapa": dict(mapa)},
        descricao=f"fato em {rotulo_mapa}",
    )


def fmt_lista_retrieval(sections: Sections, *, itens: Optional[List[str]] = None, **_) -> FormatResult:
    """Exp 006 — DEFINE o retrieval como a lista dada (substitui o existente).
    itens na ordem em que devem aparecer (ex.: memorias datadas, antiga->nova).
    itens vazio/None = retrieval vazio (controle negativo)."""
    itens = list(itens or [])
    s = _norm(sections)
    s["retrieval"] = itens
    return FormatResult(
        sections=s,
        formato_id=f"lista_retrieval(n={len(itens)})",
        andaime_patch={"formato": "lista_retrieval", "n_itens": len(itens)},
        descricao=f"retrieval definido com {len(itens)} itens",
    )


_REGISTRY: Dict[str, Callable[..., FormatResult]] = {
    "neutra":            fmt_neutra,
    "ablacao":           fmt_ablacao,
    "lost_in_middle":    fmt_lost_in_middle,
    "ruido_dominante":   fmt_ruido_dominante,
    "historico_cortado": fmt_historico_cortado,
    "ancora_envenenada": fmt_ancora_envenenada,
    "fato_em_camada":    fmt_fato_em_camada,
    "lista_retrieval":   fmt_lista_retrieval,
}


def register(formato_base_id: str, fn: Callable[..., FormatResult]) -> None:
    """Registra um formato novo (extensibilidade — Arquitetura Forward)."""
    _REGISTRY[formato_base_id] = fn


def list_formats() -> List[str]:
    """Ids-base dos formatos disponiveis."""
    return sorted(_REGISTRY.keys())


def apply(formato_base_id: str, sections: Sections, **params) -> FormatResult:
    """Aplica um formato. formato_base_id in list_formats(); params especificos
    por formato (ex: alvo='retrieval', posicao='meio', n=5, manter=1,
    data_falsa='2019-01-01'). Retorna FormatResult (puro — input intacto)."""
    fn = _REGISTRY.get(formato_base_id)
    if fn is None:
        raise ValueError(
            f"formato desconhecido '{formato_base_id}'. Disponiveis: {list_formats()}"
        )
    return fn(sections, **params)
