#!/usr/bin/env python3
"""
scripts/avaliador_honeypot_14q.py — teste direto e terminal do Degrau 1.

Contrato: docs/preregistro_degrau1_honeypot.md §9 (emenda pós-dado-zero).

PERGUNTA: com as 14 queries congeladas do EXP017, aplicando o gate
corrigido (similaridade BRUTA > 0.7 E epistemic_status == "verified"),
quantas respostas o cache entregaria?

CRITÉRIO: acertos >= 5 -> H1 ; senão H0.

── Por que NÃO usa memory.retrieve() ─────────────────────────────────────────
Duas razões, ambas verificadas no código:

1. `retrieve()` ordena por rank_score — produto de 9 fatores
   (session_boost 1.60, prioridade 1.5, anchor 1.20, decay, ...). O top-1
   dele NÃO é necessariamente o top-1 por similaridade bruta, que é o que
   o gate especifica. Ranquear por rank_score e depois ler "similaridade"
   seria medir outra coisa.
2. `retrieve()` muta `acessos`/`ultimo_acesso` das entries. Este script é
   read-only: lê os JSON direto, não instancia EpisodicMemory (cujo
   __init__ faz scope_dir.mkdir, store.py:310).

── Duas similaridades reportadas ─────────────────────────────────────────────
- `sim_q`   : query vs a parte "Q:" da entry, re-embeddada. É o GATE
              (desenho corrigido: compara pergunta com pergunta).
- `sim_blob`: query vs o embedding persistido da entry, que cobre o blob
              inteiro "Q: ...\\nA: ..." (websocket.py:1200). É o que o
              sistema faz hoje. Reportado só como diagnóstico — mostra
              quanto o blob distorce a medida.

── Por que reporta a CAUSA de cada miss ──────────────────────────────────────
Um "0/14" seco é ambíguo: não distingue "não existe memória parecida" de
"existe, mas é hypothesis". Como nenhuma escrita automática de "verified"
existe no código (websocket.py:1218 grava tudo como "hypothesis"), a
segunda causa é a esperada — e precisa aparecer separada, senão o
resultado seria lido como "cache não funciona" quando na verdade diz
"o gate exclui tudo por construção".

USO (PowerShell, servidor parado, sobre CÓPIA do store):
  $env:EDP_BASE_DIR = "C:\\edp_data_fase0"
  python scripts/avaliador_honeypot_14q.py --store "C:\\edp_data_fase0"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Congelados pelo pré-registro ──────────────────────────────────────────────
GATE_SIM     = 0.70      # similaridade BRUTA mínima
GATE_STATUS  = "verified"
CRITERIO_H1  = 5         # acertos >= 5 -> H1

_RE_QUERY_MD = re.compile(r"^\s*\d+\.\s*\[(R3|R2|N)\]\s+(.+?)\s*$", re.M)
_RE_QA       = re.compile(r"^Q:\s*(.*?)\nA:\s*(.*)$", re.S)


# ── Carga das 14 queries ──────────────────────────────────────────────────────

def carregar_queries(repo: Path) -> list[tuple[str, str]]:
    """
    Lê a lista congelada de EXP017_FASE0.md (rastreado em git, estável).
    Fallback: export_fase0.jsonl (gitignored, pode não existir).

    Retorna [(pool, query), ...] na ordem congelada.
    """
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


# ── Carga do store (read-only) ────────────────────────────────────────────────

def carregar_entries(base: Path) -> list[dict]:
    sessions = base / "sessions"
    if not sessions.is_dir():
        raise SystemExit(f"ERRO: {sessions} não existe. --store aponta para a raiz "
                         f"do store (a pasta que CONTÉM sessions/).")

    arquivos = sorted(sessions.glob("**/episodic.json")) + \
               sorted(sessions.glob("**/semantic.json"))
    if not arquivos:
        raise SystemExit(f"ERRO: nenhum episodic.json/semantic.json sob {sessions}.")

    entries: list[dict] = []
    for arq in arquivos:
        try:
            doc = json.loads(arq.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  aviso: pulando {arq.parent.name}/{arq.name} "
                  f"({type(e).__name__}: {e})")
            continue
        lista = doc.get("entries", doc) if isinstance(doc, dict) else doc
        if not isinstance(lista, list):
            continue
        for e in lista:
            if isinstance(e, dict) and e.get("text"):
                e["_arquivo"] = f"{arq.parent.name}/{arq.name}"
                entries.append(e)
    return entries


def partir_qa(texto: str) -> tuple[str, str | None]:
    """
    Separa o blob "Q: ...\\nA: ..." de websocket.py:1200.
    Retorna (parte_para_comparar, resposta_ou_None).
    Entries fora desse formato comparam pelo texto inteiro e não têm
    resposta extraível — o honeypot não teria o que servir.
    """
    m = _RE_QA.match(texto)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return texto.strip(), None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", type=Path, required=True,
                    help="raiz do store (pasta que contém sessions/) — use uma CÓPIA")
    ap.add_argument("--out", type=Path, default=Path("resultado_honeypot_14q.json"))
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    queries = carregar_queries(repo)
    print(f"queries congeladas: {len(queries)}")

    entries = carregar_entries(args.store)
    print(f"entries carregadas: {len(entries)}")

    # ── Censo de status: responde P3 em uma linha ─────────────────────────────
    censo: dict[str, int] = {}
    for e in entries:
        s = e.get("epistemic_status", "hypothesis")
        censo[s] = censo.get(s, 0) + 1
    print(f"censo epistemic_status: {censo}")
    n_verified = censo.get(GATE_STATUS, 0)
    if n_verified == 0:
        print(f"\n  *** ZERO entries com status '{GATE_STATUS}'. O gate exclui o store")
        print( "  *** INTEIRO por construção (P3 do pré-registro). O resultado abaixo")
        print( "  *** será 0/14 independentemente da similaridade. Isso NÃO mede a")
        print( "  *** ideia do cache — mede a ausência de promoção automática.\n")

    # ── Embeddings ───────────────────────────────────────────────────────────
    from edp.embeddings import embed

    partes = [partir_qa(e["text"]) for e in entries]
    perguntas_mem = [p[0] for p in partes]
    respostas_mem = [p[1] for p in partes]

    print(f"  embeddando {len(queries)} queries + {len(perguntas_mem)} perguntas...")
    Q = embed([q for _, q in queries])          # (14, D) normalizada
    P = embed(perguntas_mem)                    # (N, D)  normalizada

    # embeddings persistidos (blob inteiro) — reutilizados, não recalculados
    B = np.zeros_like(P)
    tem_blob = np.zeros(len(entries), dtype=bool)
    for i, e in enumerate(entries):
        emb = e.get("embedding")
        if isinstance(emb, list) and len(emb) == P.shape[1]:
            v = np.asarray(emb, dtype=np.float32)
            n = np.linalg.norm(v)
            if n > 0:
                B[i] = v / n
                tem_blob[i] = True

    S_q = Q @ P.T                                # (14, N) gate
    # Diagnóstico: restrito às entries que REALMENTE têm embedding persistido.
    # Sem o filtro, as linhas zeradas de B entrariam no max() como sim 0 e
    # diluiriam silenciosamente a coluna.
    S_blob = Q @ B[tem_blob].T if tem_blob.any() else None
    if tem_blob.sum() < len(entries):
        print(f"  nota: {int(tem_blob.sum())}/{len(entries)} entries têm embedding "
              f"persistido — sim_blob é calculada só sobre essas.")

    # ── Avaliação query a query ──────────────────────────────────────────────
    idx_verified = [i for i, e in enumerate(entries)
                    if e.get("epistemic_status", "hypothesis") == GATE_STATUS]

    linhas, hits, hits_sem_gate_status = [], 0, 0
    for k, (pool, q) in enumerate(queries):
        sims = S_q[k]
        j_all = int(np.argmax(sims))
        sim_all = float(sims[j_all])

        if idx_verified:
            j_ver = max(idx_verified, key=lambda i: sims[i])
            sim_ver = float(sims[j_ver])
        else:
            j_ver, sim_ver = -1, -1.0

        hit = sim_ver >= GATE_SIM
        tem_resposta = hit and respostas_mem[j_ver] is not None
        if hit and tem_resposta:
            hits += 1
        if sim_all >= GATE_SIM:
            hits_sem_gate_status += 1

        # causa do miss — o que torna um 0/14 interpretável
        if hit and tem_resposta:
            causa = "HIT"
        elif hit and not tem_resposta:
            causa = "SEM_RESPOSTA_EXTRAIVEL"
        elif sim_all < GATE_SIM:
            causa = "SEM_MEMORIA_SIMILAR"
        else:
            causa = "STATUS_NAO_VERIFIED"

        linhas.append({
            "pool": pool, "query": q,
            "sim_q_melhor":          round(sim_all, 4),
            "status_melhor":         entries[j_all].get("epistemic_status", "hypothesis"),
            "sim_q_melhor_verified": round(sim_ver, 4) if j_ver >= 0 else None,
            "sim_blob_melhor":       round(float(S_blob[k].max()), 4) if S_blob is not None else None,
            "hit": bool(hit and tem_resposta),
            "causa": causa,
            "resposta": (respostas_mem[j_ver][:300] if tem_resposta else None),
        })

    # ── Relatório ────────────────────────────────────────────────────────────
    print("\n" + "=" * 96)
    print(f"AVALIADOR HONEYPOT — 14 queries | gate: sim_bruta >= {GATE_SIM} "
          f"E status == '{GATE_STATUS}'")
    print("=" * 96)
    print(f"{'#':>2} {'pool':<5} {'sim_q':>7} {'sim_ver':>8} {'sim_blob':>9} "
          f"{'hit':>4}  {'causa':<22} query")
    print("-" * 96)
    for i, L in enumerate(linhas, 1):
        sv = f"{L['sim_q_melhor_verified']:.4f}" if L["sim_q_melhor_verified"] is not None else "  n/a"
        sb = f"{L['sim_blob_melhor']:.4f}" if L["sim_blob_melhor"] is not None else "  n/a"
        print(f"{i:>2} {L['pool']:<5} {L['sim_q_melhor']:>7.4f} {sv:>8} {sb:>9} "
              f"{'SIM' if L['hit'] else 'não':>4}  {L['causa']:<22} {L['query'][:38]}")

    causas: dict[str, int] = {}
    for L in linhas:
        causas[L["causa"]] = causas.get(L["causa"], 0) + 1

    veredito = "H1 CONFIRMADA" if hits >= CRITERIO_H1 else "H0 VENCE"
    print("-" * 96)
    print(f"acertos: {hits}/{len(queries)}   critério: >= {CRITERIO_H1}   "
          f"VEREDITO: {veredito}")
    print(f"causas: {causas}")
    print(f"\ncontrafactual (gate SÓ de similaridade, sem exigir '{GATE_STATUS}'): "
          f"{hits_sem_gate_status}/{len(queries)}")
    print("  → a diferença entre este número e os acertos acima é o custo isolado")
    print(f"     do gate de status ({n_verified} entries verified no store).")

    if hits < CRITERIO_H1:
        print("\nLEITURA DO H0 — o que ele autoriza e o que NÃO autoriza:")
        if n_verified == 0:
            print("  NÃO autoriza concluir 'cache não funciona'. Com 0 entries")
            print("  verified, o gate zera o resultado antes de qualquer medida de")
            print("  similaridade. O que ficou provado é P3, não a inviabilidade")
            print("  do honeypot.")
        if causas.get("SEM_MEMORIA_SIMILAR", 0) >= len(queries) // 2:
            print("  A maioria dos misses é por AUSÊNCIA de memória similar — o que")
            print("  é consistente com o pool ser anafórico por desenho (P1).")

    args.out.write_text(json.dumps({
        "contrato": "docs/preregistro_degrau1_honeypot.md",
        "store": str(args.store),
        "n_entries": len(entries),
        "censo_status": censo,
        "gate": {"sim": GATE_SIM, "status": GATE_STATUS},
        "criterio_h1": CRITERIO_H1,
        "acertos": hits,
        "contrafactual_sem_gate_status": hits_sem_gate_status,
        "causas": causas,
        "veredito": veredito,
        "linhas": linhas,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
