#!/usr/bin/env python3
"""
scripts/e2_extracao_alvos.py — Regra E-2 (emenda E-2.1) da Wiki de conversas.

Contrato: docs/design_wiki_conversas.md, regra E-2 + emenda E-2.1,
congeladas em 404429c/próximo commit ANTES desta medição.

PERGUNTA (condicional): dado que o texto de um turno contém o termo-alvo,
a extração de conceitos o recupera em `concepts[]`/`domain`?

CRITÉRIO CONGELADO:
  PASSA se, para >=3 dos 5 alvos, o termo aparece em concepts[]/domain em
  PELO MENOS 1 dos turnos amostrados daquele alvo.
  FALHA -> camada 3 cai, por defeito do PIPELINE (conclusão forte), não
  por corpus errado.

AMOSTRAGEM ESTRATIFICADA, não aleatória. Motivo na E-2.1: aleatória de
200 em 3.748 (5,3%) daria 0,43 turnos esperados de "Mongólia" — o
critério falharia por amostragem, não por extração. Estratificar responde
a pergunta condicional; aleatório responde outra, já respondida (5/5).

CONTROLE NEGATIVO (NORTE §4.5): 20 turnos sem alvo nenhum, para medir
falso positivo do extrator.

REUSO SEM ALTERAÇÃO: usa EXTRACT_PROMPT_SYSTEM e
CognitiveDecisions.from_json_str de edp/runtime/cognitive_decisions.py.
Prompt novo invalidaria a medição — o que está sendo testado é o
pipeline que já existe, não um pipeline que eu inventei agora.

CUSTO: até 108 chamadas Haiku (~US$0,15). Use --dry-run para ver a
amostra e a estimativa SEM gastar nada.

USO:
  python scripts/e2_extracao_alvos.py --exports "C:\\...\\export.json" --dry-run
  $env:ANTHROPIC_API_KEY = "sk-..."
  python scripts/e2_extracao_alvos.py --exports "C:\\...\\export.json"
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from precondicao_wiki_conversas import (          # noqa: E402
    ALVOS, normalizar, textos_de_exports,
)

SEED           = 20260807
N_POR_ALVO     = 20
N_CONTROLE     = 20
CRITERIO_PASSA = 3          # >=3 dos 5 alvos recuperados
MAX_TEXT       = 3000       # espelha MAX_PROMPT_TEXT_LEN do extrator

PRECO_IN  = 1.00 / 1_000_000    # model_router.py:30, claude-haiku-4-5
PRECO_OUT = 5.00 / 1_000_000


def montar_par_qa(textos: list[tuple[str, str]], idx: int) -> str:
    """
    O prompt do extrator espera 'Q: ...\\nA: ...' (cognitive_decisions.py:78).
    Reconstrói esse formato usando o turno anterior como Q — mandar só o
    texto solto mediria o extrator fora do formato para o qual foi
    calibrado.
    """
    anterior = textos[idx - 1][1] if idx > 0 else ""
    atual    = textos[idx][1]
    return f"Q: {anterior[:MAX_TEXT // 2]}\nA: {atual[:MAX_TEXT // 2]}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exports", type=Path, required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="mostra amostra e custo estimado, não chama LLM")
    ap.add_argument("--n-por-alvo", type=int, default=N_POR_ALVO)
    ap.add_argument("--out", type=Path, default=Path("resultado_e2_extracao.json"))
    args = ap.parse_args()

    textos, n_turnos, n_conv = textos_de_exports(args.exports)
    print(f"corpus: {n_conv} conversa(s), {n_turnos} turnos, "
          f"{len(textos)} textos com conteúdo")
    if not textos:
        raise SystemExit("ERRO: nenhum texto extraído dos exports.")

    normalizados = [normalizar(t) for _, t in textos]

    # ── Amostragem estratificada (seed fixa) ─────────────────────────────────
    rnd = random.Random(SEED)
    amostra: dict[str, list[int]] = {}
    com_alvo: set[int] = set()

    for query, termos in ALVOS:
        termos_n = [normalizar(t) for t in termos]
        candidatos = [i for i, tn in enumerate(normalizados)
                      if any(t in tn for t in termos_n)]
        com_alvo.update(candidatos)
        n = min(args.n_por_alvo, len(candidatos))
        amostra[query] = sorted(rnd.sample(candidatos, n)) if n else []

    sem_alvo = [i for i in range(len(textos)) if i not in com_alvo]
    controle = sorted(rnd.sample(sem_alvo, min(N_CONTROLE, len(sem_alvo))))

    total_calls = sum(len(v) for v in amostra.values()) + len(controle)

    print(f"\namostra estratificada (seed {SEED}):")
    for query, idxs in amostra.items():
        termos = dict(ALVOS)[query]
        cand = sum(1 for tn in normalizados
                   if any(normalizar(t) in tn for t in termos))
        print(f"  {len(idxs):>3} de {cand:>4} candidatos — {query[:52]}")
    print(f"  {len(controle):>3} controle negativo (turnos sem alvo)")
    print(f"  total de chamadas: {total_calls}")

    tok_in  = total_calls * (MAX_TEXT // 4)     # ~4 chars/token, texto truncado
    tok_out = total_calls * 100
    custo   = tok_in * PRECO_IN + tok_out * PRECO_OUT
    print(f"  custo estimado: ~US$ {custo:.2f} "
          f"({tok_in/1000:.0f}k tok in, {tok_out/1000:.0f}k out, Haiku)")

    if args.dry_run:
        print("\n--dry-run: nada foi chamado. Rode sem a flag para medir.")
        return 0

    # ── Cliente LLM ──────────────────────────────────────────────────────────
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ERRO: defina ANTHROPIC_API_KEY antes de rodar sem --dry-run.")

    from edp.llm_adapter import LLMClient, LLMConfig, LLMProvider
    from edp.runtime.cognitive_decisions import (
        EXTRACT_PROMPT_SYSTEM, CognitiveDecisions,
    )

    cfg = LLMConfig(provider=LLMProvider.ANTHROPIC, model="claude-haiku-4-5",
                    api_key=os.environ["ANTHROPIC_API_KEY"],
                    temperature=0.0, max_tokens=512)
    client = LLMClient(cfg)

    def extrair(idx: int):
        try:
            resp = client.complete(montar_par_qa(textos, idx), EXTRACT_PROMPT_SYSTEM)
        except Exception as e:
            print(f"    ERRO idx={idx}: {type(e).__name__}: {str(e)[:110]}")
            return None
        return CognitiveDecisions.from_json_str(resp, model_used=cfg.model)

    # ── Medição ──────────────────────────────────────────────────────────────
    t0 = time.time()
    resultados: dict[str, dict] = {}
    falhas_parse = 0

    for query, idxs in amostra.items():
        termos_n = [normalizar(t) for t in dict(ALVOS)[query]]
        acertos, extraidos = 0, 0
        exemplos: list[str] = []
        print(f"\n[{query[:56]}] {len(idxs)} turnos")
        for idx in idxs:
            cd = extrair(idx)
            if cd is None:
                falhas_parse += 1
                continue
            extraidos += 1
            campos = normalizar(" ".join(list(cd.concepts) + [cd.domain]))
            if any(t in campos for t in termos_n):
                acertos += 1
                if len(exemplos) < 3:
                    exemplos.append(f"{textos[idx][0]}: {cd.concepts} / {cd.domain}")
        recall = acertos / extraidos if extraidos else 0.0
        resultados[query] = {"amostrados": len(idxs), "extraidos": extraidos,
                             "acertos": acertos, "recall": round(recall, 3),
                             "exemplos": exemplos}
        print(f"  recuperado em {acertos}/{extraidos} "
              f"(recall {recall:.0%}){'  <- ALVO RECUPERADO' if acertos else ''}")
        for ex in exemplos:
            print(f"    {ex[:100]}")

    # ── Controle negativo ────────────────────────────────────────────────────
    print(f"\n[controle negativo] {len(controle)} turnos sem alvo")
    todos_termos = [normalizar(t) for _, ts in ALVOS for t in ts]
    fp = 0
    for idx in controle:
        cd = extrair(idx)
        if cd is None:
            falhas_parse += 1
            continue
        campos = normalizar(" ".join(list(cd.concepts) + [cd.domain]))
        if any(t in campos for t in todos_termos):
            fp += 1
    print(f"  falsos positivos: {fp}/{len(controle)}")

    # ── Veredito ─────────────────────────────────────────────────────────────
    recuperados = [q for q, r in resultados.items() if r["acertos"] > 0]
    passa = len(recuperados) >= CRITERIO_PASSA
    dur = time.time() - t0

    print("\n" + "=" * 84)
    print(f"E-2 — alvos recuperados: {len(recuperados)}/5   "
          f"critério: >= {CRITERIO_PASSA}")
    print(f"VEREDITO: {'PASSA' if passa else 'FALHA'}")
    if passa:
        print("  → segue para o pré-registro do §9 (compilação de páginas).")
        print("  NÃO significa que a Wiki funciona: só que a extração alcança")
        print("  os alvos. Se a página compilada vale mais que o turno cru")
        print("  continua sem medição.")
    else:
        print("  → camada 3 CAI, por defeito do pipeline de extração.")
        print("  Conclusão forte: o conteúdo existe (5/5 em texto cru) mas a")
        print("  extração não o alcança. Não é corpus errado desta vez.")
    print(f"\nfalhas de parse: {falhas_parse} | {total_calls} chamadas | "
          f"{dur:.0f}s")

    args.out.write_text(json.dumps({
        "contrato": "docs/design_wiki_conversas.md E-2 + E-2.1",
        "seed": SEED, "n_por_alvo": args.n_por_alvo,
        "criterio": CRITERIO_PASSA,
        "por_alvo": resultados,
        "controle_negativo": {"n": len(controle), "falsos_positivos": fp},
        "recuperados": len(recuperados),
        "veredito": "PASSA" if passa else "FALHA",
        "falhas_parse": falhas_parse,
        "custo_estimado_usd": round(custo, 4),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
