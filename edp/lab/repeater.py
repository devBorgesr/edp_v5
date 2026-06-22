"""
edp.lab.repeater — Orquestrador da Bancada (Repetidor / Repeater do Burp).

Primeira peca que JUNTA as quatro da base: nao inventa nada, costura na ordem
certa, com os invariantes embutidos. Uma "variante" = uma janela + um formato,
rodada N vezes e gravada.

Fluxo de run_variant:
  1. (paranoico, INV-1) tira fingerprint da memoria cognitiva de PRODUCAO.
  2. abre experimental_session() -> sessao de lab ISOLADA (escopo do registro;
     blinda o futuro se o envio um dia passar pelo stream_chat que escreve).
  3. aplica o FORMATO do catalogo na janela-base -> janela distorcida + patch
     do andaime (o que mudou).
  4. RENDERIZA a janela final UMA VEZ (verdade gravada == o que foi enviado).
  5. roda o SAMPLER N vezes (trava ARMED, teto de N e de custo) com um caller
     fiado no client conectado (complete_ex -> texto + custo real).
  6. grava no PRONTUARIO: janela enviada (completa), secoes, N respostas, e o
     ANDAIME completo (base + patch do formato + ceticismo) -> INV-3.
  7. (apos fechar a sessao) tira o fingerprint de novo e confirma NAO-VAZAMENTO
     -> o aceite da Fase 2, embutido em cada experimento.

HONESTIDADE: o envio via complete_ex NAO escreve memoria, entao o isolamento no
envio e trivialmente satisfeito (nada toca a memoria; o prontuario fica noutro
diretorio). O experimental_session + a verificacao de vazamento sao cinto-e-
suspensorio e blindagem de futuro. O Repetidor NAO inclui o controle (neutra)
sozinho — isso e papel do Rodizio, que garante a neutra em todo lote.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .window_formats import apply as apply_format
from .sampler import sample, make_runtime_caller, render_window, SampleResult
from .prontuario import get_prontuario
from .isolation import experimental_session, cognitive_fingerprint, verify_no_leak

logger = logging.getLogger("edp.lab.repeater")

Sections = dict


@dataclass
class VariantRecord:
    """Resultado de uma variante: o que foi gravado + o veredito de isolamento."""
    run_id: str
    formato_id: str
    n_run: int = 0
    n_ok: int = 0
    distinct: int = 0
    cost_usd: float = 0.0
    leak_ok: Optional[bool] = None          # None se a verificacao foi pulada
    janela_enviada: str = ""
    andaime: dict = field(default_factory=dict)
    sample: Optional[SampleResult] = None


def run_variant(
    *,
    sections: Sections,
    query: str,
    formato: str,
    client,
    modelo: str,
    andaime_base: dict,
    formato_params: Optional[dict] = None,
    n: int = 5,
    n_max: int = 50,
    cost_max_usd: float = 1.0,
    ceticismo: bool = True,
    store=None,
    prod_session: str = "default",
    verify_isolation: bool = True,
    scope: str = "sprint",
    purge: bool = True,
    require_armed: bool = True,
) -> VariantRecord:
    """Roda UMA variante (janela + formato) N vezes e grava no prontuario.

    sections/query — janela-base (origem livre) + pergunta.
    formato/formato_params — qual distorcao do catalogo e seus parametros.
    client — LLMClient CONECTADO (ex.: get_runtime(lab.session_id)._client).
    modelo — nome do modelo-alvo (para o registro).
    andaime_base — campos de comensurabilidade (code_sha, embedding_version,
                   prompt_version, model_version, policy_layers, skills_loaded,
                   tools_available). O patch do formato + ceticismo sao mesclados.
    """
    store = store or get_prontuario()

    # 1) paranoico: estado da cognitive de producao ANTES.
    before = cognitive_fingerprint(prod_session) if verify_isolation else None

    fr = apply_format(formato, sections, **(formato_params or {}))
    janela = render_window(fr.sections, ceticismo=ceticismo)  # verdade gravada == enviada

    # andaime completo: base (config) + patch do formato (a variavel) + ceticismo.
    andaime = dict(andaime_base or {})
    andaime.update(fr.andaime_patch)
    andaime["ceticismo"] = ceticismo
    andaime.setdefault("model_version", modelo)

    rec = VariantRecord(run_id="", formato_id=fr.formato_id, andaime=andaime, janela_enviada=janela)

    # 2) sessao de lab isolada (escopo do registro + blindagem de futuro).
    with experimental_session(scope=scope, purge=purge) as lab:
        # 5) caller fiado no client conectado, MESMO ceticismo do render (garante
        #    que o enviado == o gravado, pois render_window e deterministico).
        caller = make_runtime_caller(client, ceticismo=ceticismo)
        sr = sample(
            caller, fr.sections, query,
            n=n, n_max=n_max, cost_max_usd=cost_max_usd, require_armed=require_armed,
        )

        # 6) grava no prontuario (escopo = a sessao de lab).
        run_id = store.record_run(
            modelo=modelo,
            formato_id=fr.formato_id,
            andaime=andaime,
            janela_enviada=janela,
            secoes=fr.sections,
            respostas=sr.responses,
            scope=lab.session_id,
            metricas=sr.as_dict(),
        )

    # 7) NAO-VAZAMENTO: cognitive de producao inalterada? (aceite da Fase 2)
    if verify_isolation:
        after = cognitive_fingerprint(prod_session)
        rec.leak_ok = verify_no_leak(before, after)
        if not rec.leak_ok:
            logger.warning(
                "[repeater] VAZAMENTO detectado! cognitive de '%s' mudou durante o "
                "experimento (run_id=%s). Investigue antes de confiar no registro.",
                prod_session, run_id,
            )

    rec.run_id = run_id
    rec.sample = sr
    rec.n_run, rec.n_ok, rec.distinct, rec.cost_usd = sr.n_run, sr.n_ok, sr.distinct, sr.cost_total_usd
    logger.info(
        "[repeater] variante '%s' | run_id=%s | n=%d ok=%d distintas=%d custo=$%.4f | leak_ok=%s",
        fr.formato_id, run_id, sr.n_run, sr.n_ok, sr.distinct, sr.cost_total_usd, rec.leak_ok,
    )
    return rec


# ── Helper: carregar janela-base de uma captura real do Interceptor ───────────
@dataclass
class CapturedWindow:
    sections: Sections
    query: str


def load_window_from_debug(path: str, *, index: int = -1) -> CapturedWindow:
    """Carrega uma janela REAL capturada pelo Interceptor (context_debug.jsonl).
    index=-1 = a ultima capturada. Devolve secoes + query daquele turno, prontos
    para distorcer no run_variant. (Fonte alternativa: janela sintetica = montar
    o dict de secoes na mao.)"""
    import json
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    if not rows:
        raise ValueError(f"context_debug vazio ou ilegivel: {path}")
    rec = rows[index]
    return CapturedWindow(
        sections=rec.get("sections", {}) or {},
        query=rec.get("query", "") or "",
    )
