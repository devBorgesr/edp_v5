#!/usr/bin/env python3
"""
scripts/medir_gate_especificidade.py — testa o gate de especificidade.

Contrato: docs/preregistro_gate_especificidade.md (escrito ANTES desta
medição, commit do pré-registro precede o deste resultado).

PERGUNTA: existe uma regra de roteamento que NÃO seleciona vagueza?

O H0 do Degrau 1 mostrou que um gate de similaridade bruta dispara em
perguntas anafóricas e silencia em factuais (R1, seletividade invertida).
A causa é a regra, não a fonte. Este script testa a candidata mais barata:
especificidade da query, computada SEM olhar o que foi recuperado.

HIPÓTESE (livre de limiar, para não haver overfitting em 14 pontos):
  H1: min(especificidade dos 5 [N]) > max(especificidade dos 6 [R3])
  H0: qualquer sobreposição.

[R2] é REPORTADO mas NUNCA PONTUADO — os 3 são ambíguos por natureza
(anáfora com tópico real) e pontuá-los, em qualquer direção, seria eu
escolhendo o resultado. Congelado no §2 do pré-registro.

MÉTRICA (congelada, §3): média dos 3 maiores IDF entre os tokens da query.
  IDF(t) = ln((N+1)/(df(t)+1)), documento = uma entry do store.
  Sem lista de stopwords: o IDF já rebaixa palavra comum, e uma lista
  manual seria mais um parâmetro ajustável.
  Token OOV: df=0 -> IDF máximo. Contagem de OOV é reportada porque pode
  inflar [N] artificialmente.

READ-ONLY: lê os JSON do store direto, não instancia EpisodicMemory
(cujo __init__ faz mkdir, store.py:310), não chama retrieve().
Não precisa de embeddings — é contagem de termos, roda em segundos.

USO (PowerShell):
  python scripts/medir_gate_especificidade.py --store "C:\\edp_data_fase0"
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TOP_N        = 3          # média dos 3 maiores IDF (congelado §3)
POOL_POS     = "N"        # positivos
POOL_NEG     = "R3"       # negativos inequívocos
POOL_AMBIG   = "R2"       # reportado, nunca pontuado

_RE_TOKEN    = re.compile(r"\w+", re.UNICODE)
_RE_QUERY_MD = re.compile(r"^\s*\d+\.\s*\[(R3|R2|N)\]\s+(.+?)\s*$", re.M)
_RE_QA       = re.compile(r"^Q:\s*(.*?)\nA:\s*(.*)$", re.S)


def tokenizar(texto: str) -> list[str]:
    return _RE_TOKEN.findall(texto.lower())


def carregar_queries(repo: Path) -> list[tuple[str, str]]:
    md = repo / "EXP017_FASE0.md"
    if md.exists():
        achados = _RE_QUERY_MD.findall(md.read_text(encoding="utf-8"))
        if achados:
            return [(pool, q) for pool, q in achados]
    jl = repo / "export_fase0.jsonl"
    if jl.exists():
        with jl.open(encoding="utf-8") as f:
            return [("?", json.loads(l)["query"]) for l in f if l.strip()]
    raise SystemExit("ERRO: nem EXP017_FASE0.md nem export_fase0.jsonl encontrados.")


def carregar_entries(base: Path) -> list[str]:
    """Textos das entries. Para blobs 'Q: ...\\nA: ...', usa o texto inteiro:
    o IDF mede o vocabulário que o sistema conhece, não só as perguntas."""
    sessions = base / "sessions"
    if not sessions.is_dir():
        raise SystemExit(f"ERRO: {sessions} não existe. --store aponta para a raiz "
                         f"do store (a pasta que CONTÉM sessions/).")
    arquivos = sorted(sessions.glob("**/episodic.json")) + \
               sorted(sessions.glob("**/semantic.json"))
    if not arquivos:
        raise SystemExit(f"ERRO: nenhum episodic.json/semantic.json sob {sessions}.")

    textos: list[str] = []
    for arq in arquivos:
        try:
            doc = json.loads(arq.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  aviso: pulando {arq.parent.name}/{arq.name} "
                  f"({type(e).__name__}: {e})")
            continue
        lista = doc.get("entries", doc) if isinstance(doc, dict) else doc
        if isinstance(lista, list):
            textos.extend(e["text"] for e in lista
                          if isinstance(e, dict) and e.get("text"))
    return textos


def construir_idf(textos: list[str]) -> tuple[dict[str, float], float, int]:
    """Retorna (idf, idf_oov, N)."""
    N = len(textos)
    df: Counter = Counter()
    for t in textos:
        df.update(set(tokenizar(t)))
    idf = {t: math.log((N + 1) / (d + 1)) for t, d in df.items()}
    idf_oov = math.log((N + 1) / 1)          # df=0
    return idf, idf_oov, N


def especificidade(query: str, idf: dict[str, float], idf_oov: float
                   ) -> tuple[float, float, float, float, int, list[tuple[str, float]]]:
    """
    Retorna (top3_OOV0, top3_OOVmax, media_tudo, maximo, n_oov, termos).

    top3_OOV0 é a PRIMÁRIA (emenda E1). top3_OOVmax é a métrica original
    do §3, reportada em paralelo para que a troca seja auditável.
    """
    toks = tokenizar(query)
    if not toks:
        return 0.0, 0.0, 0.0, 0.0, 0, []

    # E1: token OOV vale 0 — o store NÃO conhece o termo, logo não pode
    # responder sobre ele. Ver §3-bis do pré-registro.
    pares_0   = [(t, idf.get(t, 0.0))      for t in toks]
    vals_max  = sorted((idf.get(t, idf_oov) for t in toks), reverse=True)
    vals_0    = sorted((v for _, v in pares_0), reverse=True)
    oov       = sum(1 for t in toks if t not in idf)

    top_0   = vals_0[:TOP_N]
    top_max = vals_max[:TOP_N]
    return (sum(top_0)   / len(top_0),      # PRIMÁRIA (E1)
            sum(top_max) / len(top_max),    # original §3
            sum(vals_0)  / len(vals_0),     # secundária
            vals_0[0],                      # secundária
            oov,
            sorted(pares_0, key=lambda p: -p[1])[:TOP_N])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", type=Path, required=True,
                    help="raiz do store (pasta que contém sessions/) — use uma CÓPIA")
    ap.add_argument("--out", type=Path, default=Path("resultado_gate_especificidade.json"))
    args = ap.parse_args()

    repo    = Path(__file__).resolve().parent.parent
    queries = carregar_queries(repo)
    textos  = carregar_entries(args.store)
    idf, idf_oov, N = construir_idf(textos)
    print(f"queries: {len(queries)} | entries (documentos IDF): {N} | "
          f"vocabulário: {len(idf)} | IDF_oov = {idf_oov:.4f}")

    linhas = []
    for pool, q in queries:
        esp0, espmax, media, mx, oov, topterms = especificidade(q, idf, idf_oov)
        linhas.append({"pool": pool, "query": q,
                       "espec_top3": round(esp0, 4),          # PRIMÁRIA (E1)
                       "espec_top3_oovmax": round(espmax, 4),  # original §3
                       "media_tudo": round(media, 4), "maximo": round(mx, 4),
                       "oov": oov,
                       "termos": [(t, round(v, 3)) for t, v in topterms]})

    # ── tabela ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 108)
    print("GATE DE ESPECIFICIDADE — média dos 3 maiores IDF por query")
    print("PRIMÁRIA = top3 com OOV valendo 0 (emenda E1). top3_max = métrica "
          "original do §3, para auditoria.")
    print("=" * 108)
    print(f"{'#':>2} {'pool':<5} {'top3':>7} {'top3_max':>9} {'oov':>4}  "
          f"{'termos mais raros':<34} query")
    print("-" * 108)
    for i, L in enumerate(linhas, 1):
        tt = ", ".join(f"{t}:{v}" for t, v in L["termos"])
        print(f"{i:>2} {L['pool']:<5} {L['espec_top3']:>7.4f} "
              f"{L['espec_top3_oovmax']:>9.4f} {L['oov']:>4}  {tt[:34]:<34} "
              f"{L['query'][:30]}")

    pos = [L["espec_top3"] for L in linhas if L["pool"] == POOL_POS]
    neg = [L["espec_top3"] for L in linhas if L["pool"] == POOL_NEG]
    amb = [L["espec_top3"] for L in linhas if L["pool"] == POOL_AMBIG]

    if not pos or not neg:
        raise SystemExit(f"ERRO: pools vazios (pos={len(pos)}, neg={len(neg)}).")

    # ── Checagem de sanidade do instrumento (§3-bis.1, congelada) ────────────
    # O gate só discrimina se o corpus do IDF for do MESMO GÊNERO das queries.
    # Num corpus técnico, fala conversacional vira "rara" e o gate inverte.
    tok_r3 = [t for L in linhas if L["pool"] == POOL_NEG for t in tokenizar(L["query"])]
    oov_r3 = sum(1 for t in tok_r3 if t not in idf)
    frac_r3 = oov_r3 / len(tok_r3) if tok_r3 else 0.0
    print("-" * 108)
    print(f"[sanidade §3-bis.1] tokens OOV nas 6 [{POOL_NEG}]: "
          f"{oov_r3}/{len(tok_r3)} = {frac_r3:.1%} (limite: 20%)")
    if frac_r3 > 0.20:
        print("\n  *** INSTRUMENTO INVÁLIDO ***")
        print("  O corpus do IDF não é do mesmo gênero das queries: palavras de")
        print("  conversa aparecem como raras. O gate mede gênero, não")
        print("  especificidade. O veredito abaixo NÃO deve ser interpretado —")
        print("  refazer com corpus conversacional (§3-bis.1).\n")
        instrumento_valido = False
    else:
        instrumento_valido = True
        print("  → corpus é do gênero certo; veredito interpretável.")

    min_pos, max_neg = min(pos), max(neg)
    separacao = min_pos - max_neg
    h1 = separacao > 0

    print("-" * 104)
    print(f"[{POOL_POS}]  positivos      n={len(pos)}  min={min_pos:.4f}  max={max(pos):.4f}")
    print(f"[{POOL_NEG}] negativos      n={len(neg)}  min={min(neg):.4f}  max={max_neg:.4f}")
    print(f"[{POOL_AMBIG}] ambíguos (não pontuado) n={len(amb)}  "
          f"valores={[round(a, 4) for a in sorted(amb)]}")
    print(f"\nseparação = min([{POOL_POS}]) - max([{POOL_NEG}]) = "
          f"{min_pos:.4f} - {max_neg:.4f} = {separacao:+.4f}")
    if not instrumento_valido:
        print("VEREDITO: *** NÃO INTERPRETÁVEL *** (instrumento inválido, §3-bis.1)")
        print(f"  (o cálculo bruto daria "
              f"{'H1' if h1 else 'H0'}, separação {separacao:+.4f} — registrado só "
              f"para diagnóstico, NÃO é resultado)")
    else:
        print(f"VEREDITO: {'H1 — SEPARAÇÃO PERFEITA' if h1 else 'H0 — HÁ SOBREPOSIÇÃO'}")

    limiar = None
    if h1 and instrumento_valido:
        limiar = (min_pos + max_neg) / 2
        print(f"  limiar DERIVADO (ponto médio, não escolhido): {limiar:.4f}")
        print(f"  → gate de especificidade vira pré-requisito de roteamento.")
        print(f"  → NÃO ressuscita o honeypot (§5): cobertura segue 0 neste store,")
        print(f"     F1 segue não medido, 0,95% verified, blob Q+A intocado.")
    elif instrumento_valido:
        sobrep = sorted([(L["espec_top3"], L["pool"], L["query"])
                         for L in linhas if L["pool"] in (POOL_POS, POOL_NEG)
                         and min_pos <= L["espec_top3"] <= max_neg])
        print("  itens na zona de sobreposição:")
        for v, p, q in sobrep:
            print(f"    {v:.4f} [{p}] {q[:60]}")
        print("  → gate de especificidade DESCARTADO. Não procurar outro corte:")
        print("     a hipótese era livre de limiar justamente para impedir isso.")

    # Veredito sob a métrica ORIGINAL (§3), para a troca ser auditável
    pos_m = [L["espec_top3_oovmax"] for L in linhas if L["pool"] == POOL_POS]
    neg_m = [L["espec_top3_oovmax"] for L in linhas if L["pool"] == POOL_NEG]
    sep_m = min(pos_m) - max(neg_m)
    print(f"\n[auditoria] sob a métrica ORIGINAL do §3 (OOV=máximo): "
          f"separação = {sep_m:+.4f} → {'H1' if sep_m > 0 else 'H0'}")
    if (sep_m > 0) != h1:
        print("  ATENÇÃO: as duas métricas DIVERGEM. A emenda E1 mudou o veredito.")
        print("  Isso precisa aparecer no relatório — ver §3-bis do pré-registro.")

    total_oov = sum(L["oov"] for L in linhas)
    print(f"\nOOV total: {total_oov} tokens "
          f"([{POOL_POS}]={sum(L['oov'] for L in linhas if L['pool']==POOL_POS)}, "
          f"[{POOL_NEG}]={sum(L['oov'] for L in linhas if L['pool']==POOL_NEG)})")
    if sum(L["oov"] for L in linhas if L["pool"] == POOL_POS) > len(pos):
        print("  nota: OOV concentrado em [N] pode inflar a separação — termo nunca")
        print("  visto é 'específico' mas também é indício de que a resposta NÃO está")
        print("  no store. Ver §3 do pré-registro.")

    args.out.write_text(json.dumps({
        "contrato": "docs/preregistro_gate_especificidade.md",
        "store": str(args.store), "n_documentos_idf": N,
        "metrica": f"media dos {TOP_N} maiores IDF",
        "min_positivos": round(min_pos, 4), "max_negativos": round(max_neg, 4),
        "separacao": round(separacao, 4),
        "veredito": "H1" if h1 else "H0",
        "limiar_derivado": round(limiar, 4) if limiar is not None else None,
        "ambiguos_r2_nao_pontuados": [round(a, 4) for a in sorted(amb)],
        "linhas": linhas,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
