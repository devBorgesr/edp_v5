"""
edp.lab.sampler — Substrato estatistico da Bancada (resolve o nao-determinismo).

O PROBLEMA: um LLM nao e deterministico. A mesma janela produz respostas
diferentes. Logo UMA resposta a uma janela NAO e dado — e uma amostra. Sem
repeticao, voce confunde ruido de amostragem com efeito da estrutura.

O sampler pega UMA janela (secoes, do catalogo) e a roda N vezes contra o modelo,
devolvendo a DISTRIBUICAO de respostas. E a primeira peca da Bancada que CHAMA O
MODELO DE VERDADE — entao tem tres protecoes que as pecas puras nao precisavam:

  1. TRAVA (INV-5): so dispara se EDP_LAB_ARMED=1. Desarmado, levanta excecao
     (falha ALTO — experimento que custa dinheiro nao roda por engano/silencio).
  2. TETO DE N: clampa N ao maximo (n_max) — protege contra N gigante.
  3. TETO DE CUSTO: acumula o custo real (CompletionResponse.metrics.cost_usd) e
     PARA se passar de cost_max_usd, devolvendo resultado parcial marcado.

ARQUITETURA: o modelo e chamado por uma COSTURA INJETAVEL (ModelCaller). Isso
deixa o nucleo testavel de graca (caller falso) e o caller real fiado no
runtime isolado (_client.complete_ex). O sujeito do experimento e o EDP+modelo:
o caller renderiza a janela do jeito do EDP e envia pelo provider do EDP.

RENDER: raw DETERMINISTICO (cabecalhos exatos do EDP), NAO pelo ContextWindowManager
— de proposito: o budget do manager poderia aparar a distorcao montada e
contaminar a medida. O ceticismo e TOGGLE no render (ablacao de runner, registrada
no andaime). O prontuario grava a janela renderizada final (verdade), entao o que
quer que o render produza fica visivel.
"""
from __future__ import annotations

import logging
import os
import statistics
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger("edp.lab.sampler")

# Tipo das secoes (mesmo do window_formats / Interceptor).
Sections = dict


class LabNotArmedError(RuntimeError):
    """Levantada quando o sampler tenta disparar com a Bancada DESARMADA."""


def _armed() -> bool:
    return os.environ.get("EDP_LAB_ARMED", "0").strip().lower() in ("1", "true", "yes", "on")


# ── Resultado de UMA chamada ao modelo ────────────────────────────────────────
@dataclass
class CallResult:
    text: str
    cost_usd: float = 0.0
    ok: bool = True
    error: Optional[str] = None


# Costura injetavel: recebe (secoes, pergunta) e devolve UMA resposta do modelo.
ModelCaller = Callable[[Sections, str], CallResult]


# ── Resultado de N amostras ───────────────────────────────────────────────────
@dataclass
class SampleResult:
    responses: List[str] = field(default_factory=list)   # so as OK, na ordem
    call_results: List[CallResult] = field(default_factory=list)  # todas (inc. falhas)
    n_requested: int = 0
    n_max: int = 0
    n_run: int = 0
    n_ok: int = 0
    n_err: int = 0
    distinct: int = 0          # nº de respostas EXATAS distintas (proxy de estabilidade)
    len_min: int = 0
    len_max: int = 0
    len_mean: float = 0.0
    cost_total_usd: float = 0.0
    stopped_reason: str = "completo"   # "completo" | "cost_ceiling"

    def as_dict(self) -> dict:
        return {
            "n_requested": self.n_requested, "n_max": self.n_max, "n_run": self.n_run,
            "n_ok": self.n_ok, "n_err": self.n_err, "distinct": self.distinct,
            "len_min": self.len_min, "len_max": self.len_max, "len_mean": round(self.len_mean, 1),
            "cost_total_usd": round(self.cost_total_usd, 6), "stopped_reason": self.stopped_reason,
        }


def sample(
    caller: ModelCaller,
    sections: Sections,
    query: str,
    *,
    n: int = 5,
    n_max: int = 50,
    cost_max_usd: float = 1.0,
    require_armed: bool = True,
) -> SampleResult:
    """Roda a MESMA janela N vezes contra o modelo (via caller). Devolve a
    distribuicao. Protecoes: trava ARMED, teto de N, teto de custo.

    n          — quantas amostras (clampado a n_max).
    n_max      — teto duro de amostras.
    cost_max_usd — para de amostrar se o custo acumulado passar disto.
    require_armed — se True (default), exige EDP_LAB_ARMED=1 (senao levanta).
    """
    if require_armed and not _armed():
        raise LabNotArmedError(
            "sampler RECUSADO: EDP_LAB_ARMED != 1. A Bancada esta DESARMADA. "
            "Experimentos que chamam o modelo nao rodam desarmados (protege "
            "producao e custo). Defina EDP_LAB_ARMED=1 para armar."
        )

    n_eff = max(0, min(int(n), int(n_max)))
    if n_eff < int(n):
        logger.warning("[sampler] N clampado: pedido=%d -> rodando=%d (n_max=%d)", n, n_eff, n_max)

    res = SampleResult(n_requested=int(n), n_max=int(n_max))
    cost = 0.0
    for i in range(n_eff):
        if cost > cost_max_usd:
            res.stopped_reason = "cost_ceiling"
            logger.warning(
                "[sampler] teto de custo atingido apos %d amostras (custo=%.4f > %.4f)",
                i, cost, cost_max_usd,
            )
            break
        try:
            cr = caller(sections, query)
        except Exception as e:
            cr = CallResult(text="", cost_usd=0.0, ok=False, error=f"{type(e).__name__}: {e}")
        if not isinstance(cr, CallResult):
            cr = CallResult(text="", ok=False, error="caller retornou tipo invalido")
        res.call_results.append(cr)
        res.n_run += 1
        cost += float(cr.cost_usd or 0.0)
        if cr.ok:
            res.n_ok += 1
            res.responses.append(cr.text)
        else:
            res.n_err += 1

    res.cost_total_usd = cost
    _compute_stats(res)
    logger.info(
        "[sampler] %d amostras | ok=%d err=%d distintas=%d custo=$%.4f | %s",
        res.n_run, res.n_ok, res.n_err, res.distinct, res.cost_total_usd, res.stopped_reason,
    )
    return res


def _compute_stats(res: SampleResult) -> None:
    """Estatistica da distribuicao. distinct = respostas EXATAS unicas (proxy
    cru de estabilidade — o Comparador fara melhor). Comprimentos em chars."""
    oks = [r for r in res.responses]
    res.distinct = len(set(oks))
    if oks:
        lens = [len(r) for r in oks]
        res.len_min = min(lens)
        res.len_max = max(lens)
        res.len_mean = statistics.fmean(lens)


# ── Render: secoes -> prompt final (raw deterministico) ───────────────────────
# Cabecalhos EXATOS do EDP (mesmos que ContextWindowManager.to_prompt emite),
# mas SEM budget — para nao aparar a distorcao do experimento.

def render_window(sections: Sections, *, ceticismo: bool = True) -> str:
    """Renderiza as secoes no prompt final, do jeito do EDP, deterministico.
    ceticismo=True (default = producao) cola o bloco de ceticismo no fim;
    False ablaciona o ceticismo (toggle de runner, registrar no andaime)."""
    s = sections or {}
    parts: List[str] = [str(s.get("system", "") or "")]

    anchors = s.get("anchors") or []
    if anchors:
        parts += ["", "=== HISTÓRICO ANTERIOR (início da sessão) ==="] + [str(x) for x in anchors]
    retrieval = s.get("retrieval") or []
    if retrieval:
        parts += ["", "=== MEMÓRIAS RELEVANTES ==="] + [str(x) for x in retrieval]
    recent = s.get("recent") or []
    if recent:
        parts += ["", "=== TURNOS RECENTES ==="] + [str(x) for x in recent]
    parts += ["", "=== PERGUNTA ATUAL ===", str(s.get("query", "") or "")]

    rendered = "\n".join(parts)
    if ceticismo:
        try:
            from ..echo_chamber import CETICISMO_DEFAULT
            if CETICISMO_DEFAULT and CETICISMO_DEFAULT not in rendered:
                rendered = f"{rendered}\n\n---\n\n{CETICISMO_DEFAULT}"
        except Exception as e:
            logger.debug("[sampler] ceticismo indisponivel no render: %s", e)
    return rendered


# ── Caller real: fia no runtime isolado (EDP+modelo) ──────────────────────────
def make_runtime_caller(
    client,
    *,
    ceticismo: bool = True,
    render: Optional[Callable[[Sections], str]] = None,
) -> ModelCaller:
    """Constroi um ModelCaller a partir de um LLMClient CONECTADO (o _client do
    runtime isolado: get_runtime(lab.session_id)._client).

    Renderiza a janela do jeito do EDP (render_window) e envia via
    client.complete_ex (que devolve texto + custo real). Fallback para
    client.complete() (sem custo) se complete_ex nao existir/retornar None.

    O client tem que estar conectado a um provider (Anthropic). Em sessao de lab
    nova, conecte antes — ou passe o _client de um runtime ja conectado (o
    provider nao guarda estado de sessao; complete_ex NAO escreve memoria, entao
    nao ha contaminacao no envio)."""
    def _caller(sections: Sections, query: str) -> CallResult:
        try:
            system = (render(sections) if render
                      else render_window(sections, ceticismo=ceticismo))
            # 1) caminho rico: complete_ex -> CompletionResponse (text + cost real)
            resp = None
            try:
                fn = getattr(client, "complete_ex", None)
                if callable(fn):
                    resp = fn(query, system)
            except Exception as e:
                logger.debug("[sampler] complete_ex falhou, tentando complete: %s", e)
                resp = None
            if resp is not None:
                text = getattr(resp, "text", "") or ""
                metrics = getattr(resp, "metrics", None)
                cost = float(getattr(metrics, "cost_usd", 0.0) or 0.0)
                return CallResult(text=text, cost_usd=cost, ok=True)
            # 2) fallback: complete -> str (sem custo reportado)
            text = client.complete(query, system)
            return CallResult(text=text or "", cost_usd=0.0, ok=True)
        except Exception as e:
            return CallResult(text="", cost_usd=0.0, ok=False, error=f"{type(e).__name__}: {e}")

    return _caller
