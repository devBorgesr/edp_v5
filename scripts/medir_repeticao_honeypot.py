#!/usr/bin/env python3
"""
scripts/medir_repeticao_honeypot.py — Fase A do Degrau 1 (Honeypot).

Contrato: docs/preregistro_degrau1_honeypot.md (escrito ANTES desta
medição). Mede UM número: a taxa de repetição semântica de perguntas do
usuário (fenômeno F1 do §3 do pré-registro).

F1 é o TETO ABSOLUTO de economia de qualquer cache de respostas. Se o
usuário nunca repete pergunta, nenhum honeypot — por melhor que seja o
gate — economiza nada. Por isso F1 se mede ANTES de escrever uma linha de
honeypot.

NÃO CONFUNDIR com scripts/medir_repeat_exp017.py: aquele mede F2
(repetição de IDs entre retrieves consecutivos), fenômeno diferente. F2
alto não implica F1 alto — retrieves colapsam justamente em perguntas
VAGAS e DIFERENTES entre si.

READ-ONLY sobre a memória: não importa websocket.py, não chama
memory.add(), não chama retrieve() (que muta acessos/ultimo_acesso).
A única escrita é o cache de embeddings (BASE_DIR/embed_cache.sqlite),
efeito colateral de edp.embeddings.embed(). Por isso: aponte EDP_BASE_DIR
para uma CÓPIA, nunca para produção.

USO (PowerShell, servidor parado):
  $env:EDP_BASE_DIR = "C:\\edp_data_honeypot"     # cópia, não produção
  python scripts/medir_repeticao_honeypot.py --source export --path "C:\\exports"
  python scripts/medir_repeticao_honeypot.py --source store

Saída: tabela no stdout + JSON em --out (default:
resultado_repeticao_honeypot.json), para colar na seção "## Resultado"
do pré-registro.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Constantes congeladas pelo pré-registro (§5.3, §5.4) ──────────────────────
# NÃO ajustar depois de ver o resultado. MIN_WORDS espelha edp/config.py:19.
MIN_WORDS   = 5
MAX_CHARS   = 2000
LIMIAR_DEC  = 0.85            # corte de decisão (H1a)
LIMIARES    = (0.80, 0.85, 0.90)   # 0.80/0.90 são diagnóstico de robustez
PISO_H1A    = 0.10            # H1a: >= 10% dos turnos elegíveis
N_MIN       = 100             # amostra mínima p/ emitir veredito (§5.5)


# ── Extração de turnos ────────────────────────────────────────────────────────

def turnos_de_export(path: Path) -> list[str]:
    """
    Extrai turnos de usuário, em ordem, de um export do sensor (v4.x).

    Formato: {"turns": [{"role": "human"|"user"|..., "raw_text": str,
                         "created_at": str, ...}, ...]}
    """
    with path.open(encoding="utf-8") as f:
        doc = json.load(f)

    turns = doc.get("turns") or []
    if not isinstance(turns, list):
        return []

    out = []
    for t in turns:
        if not isinstance(t, dict):
            continue
        if str(t.get("role", "")).lower() not in ("human", "user"):
            continue
        texto = t.get("raw_text") or ""
        if not texto:
            # fallback: concatena blocos de texto quando raw_text vem vazio
            blocos = t.get("blocks") or []
            texto = " ".join(
                b.get("text", "") for b in blocos
                if isinstance(b, dict) and b.get("type") == "text"
            )
        if texto.strip():
            out.append(texto.strip())
    return out


_RE_Q = re.compile(r"^Q:\s*(.*?)(?:\nA:|$)", re.S)


def turnos_de_store(base_dir: Path) -> list[str]:
    """
    Extrai a parte "Q:" dos blobs gravados por websocket.py:1200
    (`combined = f"Q: {msg}\\nA: {resp}"`), em ordem de timestamp.

    Ignora entries que não seguem esse formato (memórias criadas à mão,
    session_summary, etc.) — não são turnos de usuário.
    """
    sessions = base_dir / "sessions"
    if not sessions.is_dir():
        raise SystemExit(
            f"ERRO: {sessions} não existe. Aponte EDP_BASE_DIR para a cópia "
            f"do store (ver docstring)."
        )

    coletados: list[tuple[float, str]] = []
    arquivos = sorted(sessions.glob("**/episodic.json"))
    if not arquivos:
        raise SystemExit(f"ERRO: nenhum episodic.json sob {sessions}.")

    for arq in arquivos:
        try:
            doc = json.loads(arq.read_text(encoding="utf-8"))
        except Exception as e:                      # JSON truncado/corrompido
            print(f"  aviso: pulando {arq.name} ({type(e).__name__}: {e})")
            continue
        entries = doc.get("entries", doc) if isinstance(doc, dict) else doc
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            texto = e.get("text") or ""
            m = _RE_Q.match(texto)
            if not m:
                continue
            pergunta = m.group(1).strip()
            if pergunta:
                coletados.append((float(e.get("timestamp") or 0.0), pergunta))

    coletados.sort(key=lambda p: p[0])
    return [t for _, t in coletados]


# ── Filtro congelado (§5.3 do pré-registro) ───────────────────────────────────

def filtrar(turnos: list[str]) -> tuple[list[str], dict]:
    mantidos, n_curto, n_longo = [], 0, 0
    for t in turnos:
        if len(t) > MAX_CHARS:
            n_longo += 1
            continue
        if len(t.split()) < MIN_WORDS:
            n_curto += 1
            continue
        mantidos.append(t)
    total = len(turnos)
    stats = {
        "turnos_brutos":     total,
        "descartados_curto": n_curto,
        "descartados_longo": n_longo,
        "elegiveis":         len(mantidos),
        "fracao_descartada": round((total - len(mantidos)) / total, 4) if total else 0.0,
    }
    return mantidos, stats


# ── Medição ───────────────────────────────────────────────────────────────────

def medir(turnos: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """
    Para cada turno i, a maior similaridade contra qualquer turno j < i.

    Retorna (max_sim, idx_vizinho). O turno 0 não tem anterior — recebe
    -1.0 / -1 e é excluído das frações (denominador = N-1).
    """
    from edp.embeddings import embed

    print(f"  embeddando {len(turnos)} turnos...")
    M = embed(turnos)                    # (N, D), já L2-normalizada
    S = M @ M.T                          # cosseno, pois normalizada

    n = len(turnos)
    max_sim = np.full(n, -1.0, dtype=np.float32)
    vizinho = np.full(n, -1, dtype=np.int32)
    for i in range(1, n):
        anteriores = S[i, :i]
        j = int(np.argmax(anteriores))
        max_sim[i] = float(anteriores[j])
        vizinho[i] = j
    return max_sim, vizinho


def sha256_corpus(turnos: list[str]) -> str:
    h = hashlib.sha256()
    for t in turnos:
        h.update(t.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=("export", "store"), required=True,
                    help="export: JSON(s) do sensor | store: episodic.json do EDP")
    ap.add_argument("--path", type=Path, default=None,
                    help="arquivo ou diretório de exports (--source export)")
    ap.add_argument("--out", type=Path,
                    default=Path("resultado_repeticao_honeypot.json"))
    args = ap.parse_args()

    # ── coleta ────────────────────────────────────────────────────────────────
    if args.source == "export":
        if not args.path:
            ap.error("--source export exige --path")
        arquivos = ([args.path] if args.path.is_file()
                    else sorted(args.path.glob("*.json")))
        if not arquivos:
            raise SystemExit(f"ERRO: nenhum .json em {args.path}")
        turnos: list[str] = []
        for arq in arquivos:
            t = turnos_de_export(arq)
            print(f"  {arq.name}: {len(t)} turnos de usuário")
            turnos.extend(t)
    else:
        from edp.config import BASE_DIR
        print(f"  BASE_DIR = {BASE_DIR}")
        turnos = turnos_de_store(Path(BASE_DIR))

    if len(turnos) < 2:
        raise SystemExit(f"ERRO: {len(turnos)} turno(s) — insuficiente.")

    elegiveis, stats = filtrar(turnos)
    if len(elegiveis) < 2:
        raise SystemExit(f"ERRO: {len(elegiveis)} turno(s) após filtro.")

    corpus_hash = sha256_corpus(elegiveis)
    max_sim, vizinho = medir(elegiveis)

    # ── frações (denominador exclui o turno 0, que não tem anterior) ──────────
    comparaveis = max_sim[1:]
    fracoes = {
        f"{lim:.2f}": round(float((comparaveis >= lim).mean()), 4)
        for lim in LIMIARES
    }
    pcts = {
        f"p{p}": round(float(np.percentile(comparaveis, p)), 4)
        for p in (50, 75, 90, 95)
    }
    pcts["max"] = round(float(comparaveis.max()), 4)

    decisao = fracoes[f"{LIMIAR_DEC:.2f}"]
    if len(comparaveis) < N_MIN:
        # Sem amostra, "0%" não distingue "não repete" de "não medimos".
        veredito = "AMOSTRA INSUFICIENTE — SEM VEREDITO"
    else:
        veredito = "H1a SOBREVIVE" if decisao >= PISO_H1A else "H0a VENCE"

    # ── relatório ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 66)
    print("FASE A — taxa de repetição semântica de perguntas (F1)")
    print("=" * 66)
    print(f"corpus SHA-256 : {corpus_hash}")
    print(f"turnos brutos  : {stats['turnos_brutos']}")
    print(f"descartados    : {stats['descartados_curto']} curtos (<{MIN_WORDS} palavras), "
          f"{stats['descartados_longo']} longos (>{MAX_CHARS} chars) "
          f"= {stats['fracao_descartada']:.1%}")
    print(f"elegíveis      : {stats['elegiveis']} (comparáveis: {len(comparaveis)})")

    if stats["fracao_descartada"] > 0.50:
        print("\n  ATENÇÃO: >50% descartado — resultado marcado FRÁGIL "
              "(§5.3 do pré-registro). Não reajustar o filtro nesta medição.")

    print("\nfração de turnos com repetição anterior:")
    for lim in LIMIARES:
        marca = "  <== DECISÃO" if lim == LIMIAR_DEC else ""
        print(f"  >= {lim:.2f} : {fracoes[f'{lim:.2f}']:>7.2%}{marca}")

    print("\ndistribuição da similaridade máxima:")
    print("  " + "  ".join(f"{k}={v}" for k, v in pcts.items()))

    print(f"\npiso H1a: {PISO_H1A:.0%}  |  medido @ {LIMIAR_DEC}: {decisao:.2%}")
    print(f"VEREDITO: {veredito}")
    if veredito.startswith("AMOSTRA"):
        print(f"  → {len(comparaveis)} pares comparáveis < N_MIN={N_MIN} (§5.5).")
        print("  → NÃO é resultado. Amplie o corpus e rode de novo.")
    elif veredito == "H0a VENCE":
        print("  → honeypot ABANDONADO (§6). Registrar em FILA_FUTURO.md.")
    else:
        print("  → executa Fase B (acurácia, julgada pelo pesquisador).")

    # top pares, para o julgamento humano da Fase B
    ordem = np.argsort(-max_sim)[:10]
    print("\ntop-10 pares mais similares (insumo da Fase B):")
    for i in ordem:
        if max_sim[i] < 0:
            continue
        print(f"  {max_sim[i]:.4f}  [{i}] {elegiveis[i][:64]!r}")
        print(f"           ~[{vizinho[i]}] {elegiveis[vizinho[i]][:64]!r}")

    # ── JSON ──────────────────────────────────────────────────────────────────
    args.out.write_text(json.dumps({
        "contrato":     "docs/preregistro_degrau1_honeypot.md",
        "fase":         "A",
        "source":       args.source,
        "corpus_sha256": corpus_hash,
        "filtro":       {"min_words": MIN_WORDS, "max_chars": MAX_CHARS, **stats},
        "limiar_decisao": LIMIAR_DEC,
        "piso_h1a":     PISO_H1A,
        "fracoes":      fracoes,
        "percentis":    pcts,
        "veredito":     veredito,
        "pares_top": [
            {"sim": round(float(max_sim[i]), 4),
             "atual": elegiveis[i], "anterior": elegiveis[vizinho[i]]}
            for i in ordem if max_sim[i] >= 0
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
