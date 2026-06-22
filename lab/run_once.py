"""
edp.lab.run_once — A PORTA da Bancada: roda UM experimento no EDP de verdade.

E onde a pilha (catalogo + sampler + prontuario + isolation + repeater) ENCOSTA
NA REALIDADE pela primeira vez. Por isso tem cuidados proprios — nao e script
descartavel:

  - GUARDA DE ARMADO: disparo real exige EDP_LAB_ARMED=1 (protege producao/custo).
  - DRY-RUN (--dry-run): roda o encanamento INTEIRO com um caller placeholder —
    SEM armar, SEM rede, SEM gastar. Na maquina real, exercita os stores REAIS
    (MemoryStore, isolation, prontuario, no-leak contra a default_cognitive
    real); so o MODELO e fingido. E o aceite paranoico da Fase 2, com seguranca.
  - CONEXAO VALIDADA: no real, conecta via EDPRuntime.connect_anthropic e so
    dispara se a conexao deu certo (senao falha claro, sem gastar).
  - ANDAIME REAL (Bug 4): coleta proveniencia de verdade (git sha, embedding,
    modelo) — registro nasce comparavel, nao com lixo hardcoded.
  - DIRECAO DE DEPENDENCIA: a porta importa o lab; producao NUNCA importa o lab.
    Por isso e um entry point (script), nao uma rota dentro do app.

Imports pesados (registry/runtime) sao PREGUICOSOS: o modulo carrega leve; o EDP
pesado so e puxado na hora do disparo real.

Uso:
  # 1) encanamento, sem armar nem gastar nem rede:
  set EDP_BASE_DIR=C:\\edp_data
  python -m edp.lab.run_once --dry-run --formato neutra ^
         --debug-jsonl C:\\edp_data\\context_debug.jsonl

  # 2) disparo REAL (gasta):
  set ANTHROPIC_API_KEY=sk-ant-...
  set EDP_LAB_ARMED=1
  python -m edp.lab.run_once --formato ruido_dominante --formato-params "{\\"n\\":5}" ^
         --n 5 --debug-jsonl C:\\edp_data\\context_debug.jsonl
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Callable, Optional

logger = logging.getLogger("edp.lab.run_once")


# ── Proveniencia (andaime real — Bug 4) ───────────────────────────────────────
def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        sha = (out.stdout or "").strip()
        return sha or "unknown"
    except Exception:
        return "unknown"


def _embedding_version() -> str:
    # tenta ler do EDP; senao usa o conhecido (ajustavel por env).
    env = os.environ.get("EDP_EMBED_MODEL")
    if env:
        return env
    try:
        from edp import config  # type: ignore
        for attr in ("EMBED_MODEL", "EMBEDDING_MODEL", "SENTENCE_MODEL"):
            v = getattr(config, attr, None)
            if v:
                return str(v)
    except Exception:
        pass
    return "sentence-transformers/all-MiniLM-L6-v2"


def _prompt_version() -> str:
    # nao inventa: se nao houver versao explicita, marca 'unknown'.
    return os.environ.get("EDP_PROMPT_VERSION", "unknown")


def gather_andaime(modelo: str) -> dict:
    """Monta o andaime_base com proveniencia REAL. policy_layers/skills/tools
    ficam [] por enquanto (honesto: o Interceptor ainda nao decompoe o system);
    populam quando a decomposicao existir."""
    return {
        "code_sha":          _git_sha(),
        "embedding_version": _embedding_version(),
        "prompt_version":    _prompt_version(),
        "model_version":     modelo,
        "policy_layers":     [],   # divida: pendente decomposicao do system
        "skills_loaded":     [],
        "tools_available":   [],
        "fonte_andaime":     "run_once",
    }


# ── Conexao real (seam — fundamentado em EDPRuntime.connect_anthropic) ─────────
def connect_client(modelo: str, api_key: Optional[str] = None):
    """Conecta a Anthropic e devolve um LLMClient conectado (com complete_ex).

    Constroi o LLMClient DIRETO — sem criar EDPRuntime/sessao/MemoryStore (mais
    leve na RAM, sem sessao orfa). Verificado no codigo: EDPRuntime._connect faz
    exatamente `client = LLMClient(cfg); if client.is_available(): self._client =
    client`. Construir o LLMClient direto e equivalente, sem o peso da sessao.

    Lazy import do EDP. Levanta erro claro se faltar key ou a conexao falhar —
    NUNCA segue para gastar sem conexao confirmada (is_available faz a checagem
    de ~1 token)."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("EDP_ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "API key ausente. Defina ANTHROPIC_API_KEY no ambiente antes do disparo real."
        )
    from edp.llm_adapter import LLMClient, LLMConfig, LLMProvider
    cfg = LLMConfig(
        provider=LLMProvider.ANTHROPIC,
        model=modelo,
        base_url="https://api.anthropic.com",
        api_key=key,
    )
    client = LLMClient(cfg)
    if not client.is_available():
        raise RuntimeError(
            f"Conexao Anthropic falhou (modelo={modelo}). Verifique a key/modelo. "
            "Nada foi gasto."
        )
    logger.info("[run_once] conectado a Anthropic (LLMClient direto) | modelo=%s", modelo)
    return client


# ── Caller de dry-run (placeholder, sem rede, sem custo) ──────────────────────
class _DryMetrics:
    cost_usd = 0.0


class _DryResp:
    metrics = _DryMetrics()

    def __init__(self, text: str):
        self.text = text


class _DryClient:
    """Client falso para dry-run: 'responde' sem rede, sem custo. Confirma que a
    janela montada chegaria ao envio (ecoa um resumo do que veria)."""
    def complete_ex(self, prompt: str, system: str):
        n_secs = system.count("===")
        return _DryResp(f"[DRY-RUN] janela recebida: {len(system)} chars, {n_secs} marcadores de secao.")

    def complete(self, prompt: str, system: str):
        return "[DRY-RUN] (fallback complete)"


# ── Aviso de RAM (respeita o constraint da maquina) ──────────────────────────
def _ram_advisory() -> Optional[float]:
    """Avisa (nao bloqueia) se a RAM disponivel esta baixa. Importa: o disparo
    carrega embeddings via o MemoryStore da sessao de lab. Em maquina constrita
    (8GB) com o servidor EDP rodando, um 2o processo pode estourar a RAM —
    rode com o servidor PARADO (1 processo)."""
    try:
        import psutil  # type: ignore
        avail_gb = psutil.virtual_memory().available / (1024 ** 3)
        if avail_gb < 1.5:
            logger.warning(
                "[run_once] RAM disponivel BAIXA (%.2fGB). Rode com o servidor EDP "
                "PARADO (1 processo) — carregar embeddings + servidor pode estourar.",
                avail_gb,
            )
        return avail_gb
    except Exception:
        return None


# ── A porta ───────────────────────────────────────────────────────────────────
def run_once(
    *,
    sections: dict,
    query: str,
    formato: str = "neutra",
    formato_params: Optional[dict] = None,
    modelo: str = "claude-haiku-4-5",
    n: int = 3,
    n_max: int = 50,
    cost_max_usd: float = 0.50,
    ceticismo: bool = True,
    dry_run: bool = False,
    prod_session: str = "default",
    connect: Callable = connect_client,
):
    """Roda UM experimento. dry_run=True: encanamento sem armar/gastar/rede.
    Senao: exige EDP_LAB_ARMED=1, conecta, dispara de verdade.

    NOTA: mesmo o dry-run, na maquina real, carrega o EDP e exercita os stores
    REAIS (MemoryStore, isolation, prontuario, no-leak) — so o MODELO e fingido.
    E o aceite paranoico da Fase 2, rodavel com seguranca."""
    from .repeater import run_variant

    _ram_advisory()
    andaime = gather_andaime(modelo)
    andaime["dry_run"] = bool(dry_run)

    if dry_run:
        client = _DryClient()
        require_armed = False
        logger.info("[run_once] DRY-RUN: sem armar, sem rede, sem custo.")
    else:
        if os.environ.get("EDP_LAB_ARMED", "0").strip().lower() not in ("1", "true", "yes", "on"):
            raise RuntimeError(
                "RECUSADO: disparo real exige EDP_LAB_ARMED=1. Use --dry-run para testar "
                "o encanamento sem armar, ou arme a Bancada quando for gastar de verdade."
            )
        client = connect(modelo)   # valida conexao (ou levanta)
        require_armed = True

    rec = run_variant(
        sections=sections, query=query, formato=formato, formato_params=formato_params,
        client=client, modelo=modelo, andaime_base=andaime,
        n=n, n_max=n_max, cost_max_usd=cost_max_usd, ceticismo=ceticismo,
        prod_session=prod_session, require_armed=require_armed,
    )
    return rec


# ── Relatorio (fecha o laco: le o prontuario de volta) ────────────────────────
def _report(rec) -> None:
    from .prontuario import get_prontuario
    print("\n" + "=" * 64)
    print("RESULTADO DO EXPERIMENTO")
    print("=" * 64)
    print(f"  run_id      : {rec.run_id}")
    print(f"  formato     : {rec.formato_id}")
    print(f"  amostras    : n_run={rec.n_run} ok={rec.n_ok} distintas={rec.distinct}")
    print(f"  custo total : ${rec.cost_usd:.4f}")
    print(f"  isolamento  : leak_ok={rec.leak_ok}  (cognitive de producao intacta?)")
    print(f"  ceticismo   : {rec.andaime.get('ceticismo')}  | code_sha={rec.andaime.get('code_sha')}")
    # le de volta do prontuario — confirma que o registro LANDOU
    store = get_prontuario()
    blob = store.get_blob(rec.run_id)
    if blob:
        print(f"\n  prontuario  : registro gravado e LIDO de volta")
        print(f"    janela enviada: {len(blob.get('janela_enviada',''))} chars")
        print(f"    respostas     : {len(blob.get('respostas',[]))}")
        print(f"    dir           : {store.stats().get('dir')}")
    else:
        print("\n  prontuario  : [ATENCAO] registro nao foi relido — investigue.")
    print("=" * 64)


# ── Runner genérico de experimento (sonda + plano congelado -> Rodizio) ───────
def _run_experiment(
    *,
    exp_id: str,
    probe: dict,
    query: str,
    plano: list,
    n: int,
    modelo: str,
    budget_max_usd: float,
    dry_run: bool,
    connect: Callable,
):
    """Roda um experimento (sonda + plano) via Rodizio, com dry-run/conexao/armar.
    Compartilhado entre exp001, exp003, etc."""
    from .rodizio import run_plan

    andaime = gather_andaime(modelo)
    andaime["experimento"] = exp_id
    andaime["dry_run"] = bool(dry_run)

    if dry_run:
        client = _DryClient()
        require_armed = False
        logger.info("[exp%s] DRY-RUN: %d condicoes x n=%d = %d chamadas (modelo fingido).",
                    exp_id, len(plano), n, len(plano) * n)
    else:
        if os.environ.get("EDP_LAB_ARMED", "0").strip().lower() not in ("1", "true", "yes", "on"):
            raise RuntimeError(
                "RECUSADO: disparo real exige EDP_LAB_ARMED=1. Use --dry-run para o encanamento."
            )
        client = connect(modelo)
        require_armed = True

    _ram_advisory()
    return run_plan(
        plano=plano, sections=probe, query=query,
        client=client, modelo=modelo, andaime_base=andaime,
        n=n, budget_max_usd=budget_max_usd, require_armed=require_armed,
    )


def _load_skeleton(debug_jsonl: str, index: int):
    from .repeater import load_window_from_debug
    win = load_window_from_debug(debug_jsonl, index=index)
    if not win.sections:
        raise RuntimeError("a janela-esqueleto nao tem secoes (context_debug tem 'sections'?).")
    return win


def run_exp001(*, debug_jsonl: str, index: int = -1, n: Optional[int] = None,
               modelo: str = "claude-haiku-4-5", budget_max_usd: float = 2.50,
               dry_run: bool = False, connect: Callable = connect_client):
    """Experimento 001 (ruido inerte): plano congelado em exp001 via Rodizio."""
    from . import exp001
    win = _load_skeleton(debug_jsonl, index)
    return _run_experiment(
        exp_id="001", probe=exp001.build_probe(win.sections), query=exp001.QUERY,
        plano=exp001.CONDICOES, n=(n or exp001.N_POR_CONDICAO), modelo=modelo,
        budget_max_usd=budget_max_usd, dry_run=dry_run, connect=connect)


def run_exp003(*, debug_jsonl: str, index: int = -1, n: Optional[int] = None,
               modelo: str = "claude-haiku-4-5", budget_max_usd: float = 2.50,
               dry_run: bool = False, connect: Callable = connect_client):
    """Experimento 003 (ruido competidor): plano congelado em exp003 via Rodizio."""
    from . import exp003
    win = _load_skeleton(debug_jsonl, index)
    return _run_experiment(
        exp_id="003", probe=exp003.build_probe(win.sections), query=exp003.QUERY,
        plano=exp003.CONDICOES, n=(n or exp003.N_POR_CONDICAO), modelo=modelo,
        budget_max_usd=budget_max_usd, dry_run=dry_run, connect=connect)


def run_exp004(*, debug_jsonl: str, index: int = -1, n: Optional[int] = None,
               modelo: str = "claude-haiku-4-5", budget_max_usd: float = 2.50,
               dry_run: bool = False, connect: Callable = connect_client):
    """Experimento 004 (autoridade entre camadas): plano congelado em exp004."""
    from . import exp004
    win = _load_skeleton(debug_jsonl, index)
    return _run_experiment(
        exp_id="004", probe=exp004.build_probe(win.sections), query=exp004.QUERY,
        plano=exp004.CONDICOES, n=(n or exp004.N_POR_CONDICAO), modelo=modelo,
        budget_max_usd=budget_max_usd, dry_run=dry_run, connect=connect)


def run_exp006(*, debug_jsonl: str, index: int = -1, n: Optional[int] = None,
               modelo: str = "claude-haiku-4-5", budget_max_usd: float = 2.50,
               dry_run: bool = False, connect: Callable = connect_client):
    """Experimento 006 (câmara de eco: maioria vs recência): plano em exp006."""
    from . import exp006
    win = _load_skeleton(debug_jsonl, index)
    return _run_experiment(
        exp_id="006", probe=exp006.build_probe(win.sections), query=exp006.QUERY,
        plano=exp006.CONDICOES, n=(n or exp006.N_POR_CONDICAO), modelo=modelo,
        budget_max_usd=budget_max_usd, dry_run=dry_run, connect=connect)


def run_exp007(*, debug_jsonl: str, index: int = -1, n: Optional[int] = None,
               modelo: str = "claude-haiku-4-5", budget_max_usd: float = 2.50,
               dry_run: bool = False, connect: Callable = connect_client):
    """Experimento 007 (resistência a injeção via memória): plano em exp007."""
    from . import exp007
    win = _load_skeleton(debug_jsonl, index)
    return _run_experiment(
        exp_id="007", probe=exp007.build_probe(win.sections), query=exp007.QUERY,
        plano=exp007.CONDICOES, n=(n or exp007.N_POR_CONDICAO), modelo=modelo,
        budget_max_usd=budget_max_usd, dry_run=dry_run, connect=connect)


def run_exp006b(*, debug_jsonl: str, index: int = -1, n: Optional[int] = None,
                modelo: str = "claude-haiku-4-5", budget_max_usd: float = 2.50,
                dry_run: bool = False, connect: Callable = connect_client):
    """Experimento 006b (data vs posição — desacoplamento causal): plano em exp006b."""
    from . import exp006b
    win = _load_skeleton(debug_jsonl, index)
    return _run_experiment(
        exp_id="006b", probe=exp006b.build_probe(win.sections), query=exp006b.QUERY,
        plano=exp006b.CONDICOES, n=(n or exp006b.N_POR_CONDICAO), modelo=modelo,
        budget_max_usd=budget_max_usd, dry_run=dry_run, connect=connect)


def _report_rodizio(res) -> None:
    print("\n" + "=" * 64)
    print("RESULTADO DO RODIZIO")
    print("=" * 64)
    d = res.as_dict()
    print(f"  condicoes   : {d['condicoes_rodadas']}/{d['condicoes_plano']} rodadas")
    print(f"  chamadas    : {d['chamadas_totais']}")
    print(f"  custo total : ${d['custo_total_usd']:.4f}  (freio em ${d['budget_max_usd']:.2f})")
    print(f"  parou por   : {d['parou_por']}")
    print(f"  isolamento  : leak_global_ok={d['leak_global_ok']}")
    print(f"\n  {'condicao':<20}{'papel':<26}{'n':<5}{'distintas':<11}{'custo':<10}leak")
    for c in res.condicoes:
        print(f"  {c.rotulo:<20}{c.papel:<26}{c.n_run:<5}{c.distinct:<11}${c.cost_usd:<9.4f}{c.leak_ok}")
    print("=" * 64)
    print("  (fidelidade NAO e computada aqui — e pos-coleta, pelo scorer, lendo o prontuario.)")


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Bancada de Contexto — roda UM experimento.")
    p.add_argument("--formato", default="neutra", help="formato do catalogo (neutra, ablacao, lost_in_middle, ruido_dominante, historico_cortado, ancora_envenenada)")
    p.add_argument("--formato-params", default="{}", help="JSON com params do formato (ex: '{\"n\":5}')")
    p.add_argument("--modelo", default="claude-haiku-4-5")
    p.add_argument("--n", type=int, default=3, help="numero de amostras")
    p.add_argument("--cost-max", type=float, default=0.50, help="teto de custo USD")
    p.add_argument("--no-ceticismo", action="store_true", help="ablaciona o bloco de ceticismo")
    p.add_argument("--dry-run", action="store_true", help="encanamento sem armar/gastar/rede")
    p.add_argument("--debug-jsonl", default=None, help="caminho do context_debug.jsonl (janela real)")
    p.add_argument("--index", type=int, default=-1, help="qual janela do jsonl (-1 = ultima)")
    p.add_argument("--exp001", action="store_true", help="roda o Experimento 001 (ruido inerte, 6 condicoes)")
    p.add_argument("--exp003", action="store_true", help="roda o Experimento 003 (ruido competidor, 6 condicoes)")
    p.add_argument("--exp004", action="store_true", help="roda o Experimento 004 (autoridade entre camadas, 7 condicoes)")
    p.add_argument("--exp006", action="store_true", help="roda o Experimento 006 (camara de eco maioria vs recencia, 9 condicoes)")
    p.add_argument("--exp007", action="store_true", help="roda o Experimento 007 (resistencia a injecao via memoria, 6 condicoes)")
    p.add_argument("--exp006b", action="store_true", help="roda o Experimento 006b (data vs posicao, 4 condicoes)")
    p.add_argument("--budget-max", type=float, default=2.50, help="freio de orcamento do Rodizio (USD)")
    p.add_argument("--score", action="store_true", help="pontua o prontuario (fidelidade), sem gastar")
    p.add_argument("--score-exp", type=str, default=None,
                   help="forca qual experimento pontuar (ex. 003, 004, 006, 007). Sem isto, auto-detecta.")
    p.add_argument("--audit-exp", type=str, default=None,
                   help="le e mostra uma amostra das respostas (ex. 004/006/007).")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # ── Modo AUDITORIA (le respostas do prontuario, sem API) ─────────────────
    if args.audit_exp == "004":
        from .scorer import audit_004
        audit_004(); return 0
    if args.audit_exp == "006":
        from .scorer import audit_006
        audit_006(); return 0
    if args.audit_exp == "007":
        from .scorer import audit_007
        audit_007(); return 0
    if args.audit_exp == "006b":
        from .scorer import audit_006b
        audit_006b(); return 0

    # ── Modo SCORER (analise pos-coleta, sem API) ────────────────────────────
    if args.score:
        if args.score_exp == "004":
            from .scorer import score_autoridade_004, report_004
            report_004(score_autoridade_004(only_real=True)); return 0
        if args.score_exp == "006":
            from .scorer import score_eco_006, report_006
            report_006(score_eco_006(only_real=True)); return 0
        if args.score_exp == "007":
            from .scorer import score_seguranca_007, report_007
            report_007(score_seguranca_007(only_real=True)); return 0
        if args.score_exp == "006b":
            from .scorer import score_data_posicao_006b, report_006b
            report_006b(score_data_posicao_006b(only_real=True)); return 0
        from .scorer import score_prontuario, report
        res = score_prontuario(only_real=True, experimento=args.score_exp)
        report(res)
        return 0

    # ── Modo Experimento (Rodizio do plano congelado) ────────────────────────
    if args.exp001 or args.exp003 or args.exp004 or args.exp006 or args.exp007 or args.exp006b:
        if not args.debug_jsonl:
            print("[ERRO] --exp00N exige --debug-jsonl <caminho> (esqueleto real).")
            return 2
        runner = (run_exp006b if args.exp006b else run_exp007 if args.exp007 else run_exp006 if args.exp006 else run_exp004 if args.exp004
                  else run_exp003 if args.exp003 else run_exp001)
        try:
            res = runner(
                debug_jsonl=args.debug_jsonl, index=args.index,
                n=(args.n if args.n != 3 else None),
                modelo=args.modelo, budget_max_usd=args.budget_max, dry_run=args.dry_run,
            )
        except RuntimeError as e:
            print(f"\n[RECUSADO/ERRO] {e}")
            return 1
        _report_rodizio(res)
        return 0

    # janela-base: do context_debug.jsonl (real) ou erro pedindo a fonte.
    if args.debug_jsonl:
        from .repeater import load_window_from_debug
        win = load_window_from_debug(args.debug_jsonl, index=args.index)
        sections, query = win.sections, win.query
        if not sections:
            print("[ERRO] a janela carregada nao tem secoes. O context_debug tem o campo 'sections'?")
            return 2
    else:
        print("[ERRO] forneca --debug-jsonl <caminho> (janela real capturada pelo Interceptor).")
        return 2

    try:
        params = json.loads(args.formato_params or "{}")
    except Exception as e:
        print(f"[ERRO] --formato-params nao e JSON valido: {e}")
        return 2

    try:
        rec = run_once(
            sections=sections, query=query, formato=args.formato, formato_params=params,
            modelo=args.modelo, n=args.n, cost_max_usd=args.cost_max,
            ceticismo=(not args.no_ceticismo), dry_run=args.dry_run,
        )
    except RuntimeError as e:
        print(f"\n[RECUSADO/ERRO] {e}")
        return 1
    _report(rec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
