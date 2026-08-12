#!/usr/bin/env python3
"""
scripts/medir_gap_score_haiku.py — executa a EMENDA E-2 do
`docs/preregistro_gap_score.md`.

As três fórmulas aritméticas (bruta, idf, idf⁰) falharam; a regra de
parada E-1.4 admitia uma única saída — "extração de assunto, que custa
LLM". Isto é ela.

TUDO O QUE IMPORTA ESTÁ CONGELADO EM E-2 E É REPRODUZIDO AQUI SEM
ALTERAÇÃO. Se este arquivo divergir do documento, o documento vence.

  · prompt de sistema  — E-2.2, verbatim
  · template do usuário — E-2.2, verbatim
  · modelo `claude-haiku-4-5`, temperatura 0.0 — E-2.3
  · 5 rodadas por pergunta, páginas em ordem alfabética fixa — E-2.3
  · condição (e), unanimidade sem voto de maioria — E-2.4
  · gabarito e conjuntos — importados de `medir_gap_score.py`, para que
    não possam divergir entre os dois runners

O provider é o `AnthropicProvider` do próprio repo, usado sem alteração
— mesma disciplina de `scripts/e2_extracao_alvos.py`, que reusou
`EXTRACT_PROMPT_SYSTEM` intacto. Escrever um cliente novo aqui abriria
espaço para ajustá-lo até o resultado sair.

USO:
    python3 scripts/medir_gap_score_haiku.py --dry-run   # zero chamadas
    ANTHROPIC_API_KEY=sk-... python3 scripts/medir_gap_score_haiku.py

Custo declarado em E-2.7: ~US$ 1,04 na rodada completa.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Windows CI captura stdout com cp1252 ("charmap") — ⁰/─ fora do Latin-1
# crasham com UnicodeEncodeError antes de imprimir o resultado. Achado em
# 11/08 no CI real (`windows-latest`); Linux local já é UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

# Gabarito e conjuntos: fonte única, importada — não recopiada.
from medir_gap_score import CONJ_A, CONJ_B, PAGINAS, PERGUNTAS  # noqa: E402

MODELO = "claude-haiku-4-5"          # E-2.3
TEMPERATURA = 0.0                    # E-2.3
RODADAS = 5                          # E-2.3
SHA_CONGELADO = "27e4fe846fdc37fb"   # E-2.3
MIN_UNANIMES = 12                    # E-2.4

# ── E-2.2, verbatim ──────────────────────────────────────────────────────────

SISTEMA = """Você recebe as páginas de uma wiki pessoal e UMA pergunta.

Decida uma coisa só: a wiki contém material suficiente para responder
a pergunta?

"Material suficiente" significa que alguém lendo estas páginas
conseguiria formular a resposta. NÃO exige que exista uma página
dedicada ao assunto — material espalhado por várias páginas conta.

NÃO conta como material:
- o termo da pergunta aparecer apenas como EXEMPLO de pergunta
- o termo aparecer só em lista, índice ou menção de passagem, sem o
  conteúdo que responde

Responda SOMENTE com uma linha, exatamente neste formato:
VEREDITO: SIM
ou
VEREDITO: NAO"""

USUARIO = """=== PÁGINAS DA WIKI ===
{paginas}

=== PERGUNTA ===
{pergunta}"""

_RE_VEREDITO = re.compile(r"^VEREDITO:\s*(SIM|NAO)\s*$", re.M | re.I)


def bloco_das_paginas() -> tuple[str, str]:
    """As 16 páginas em ordem alfabética por slug (E-2.3). Devolve (texto, sha)."""
    arquivos = sorted(PAGINAS.glob("*.md"))
    h = hashlib.sha256()
    partes = []
    for p in arquivos:
        h.update(p.read_bytes())
        partes.append(f"--- {p.stem} ---\n"
                      f"{p.read_text(encoding='utf-8', errors='replace')}")
    return "\n\n".join(partes), h.hexdigest()[:16]


def parse(texto: str) -> str | None:
    """`VEREDITO: SIM|NAO`. Qualquer outra coisa é inválida (E-2.3)."""
    m = _RE_VEREDITO.search(texto or "")
    return m.group(1).upper() if m else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="materializa o prompt e sai; nenhuma chamada, custo zero")
    ap.add_argument("--smoke", action="store_true",
                    help="1 chamada em N2 — fora do critério (§5) — só para "
                         "verificar encanamento e parser")
    ap.add_argument("--saida", type=Path,
                    default=_ROOT / "resultado_gap_score_haiku.json")
    args = ap.parse_args()

    if not PAGINAS.is_dir():
        raise SystemExit(f"ERRO: {PAGINAS} não existe.")

    paginas_txt, sha = bloco_das_paginas()
    print(f"corpus: 16 páginas, sha256 {sha}", end="")
    if sha != SHA_CONGELADO:
        print(f"  != {SHA_CONGELADO} congelado em E-2.3")
        raise SystemExit("ERRO: a wiki mudou desde o congelamento. "
                         "O gabarito não vale — rodada anulada (E-2.3).")
    print("  == congelado em E-2.3  OK")
    print(f"modelo={MODELO}  temperatura={TEMPERATURA}  rodadas={RODADAS}")

    if args.dry_run:
        exemplo = USUARIO.format(paginas=paginas_txt, pergunta=PERGUNTAS[2][2])
        print(f"\n--- SISTEMA ({len(SISTEMA)} chars) ---\n{SISTEMA}")
        print(f"\n--- USUÁRIO, Q3 ({len(exemplo)} chars ~{len(exemplo)//4} tok) ---")
        print(exemplo[:600] + "\n   [...corpo das 16 páginas...]\n" + exemplo[-320:])
        n = len(PERGUNTAS) * RODADAS
        tok = (len(SISTEMA) + len(exemplo)) // 4
        print(f"\n{len(PERGUNTAS)} perguntas x {RODADAS} rodadas = {n} chamadas")
        print(f"~{tok} tok/chamada -> ~{tok*n/1e6:.3f}M entrada "
              f"-> US$ {tok*n/1e6:.2f} (US$1,00/M, model_router.py:30)")
        print("\ndry-run: nenhuma chamada feita, custo US$ 0,00.")
        return 0

    # A chave vai num header HTTP. Espaço, \n ou \r dentro dela fazem o
    # http.client levantar `Invalid header value` com 30 linhas de
    # traceback e o prefixo da chave impresso no erro — foi o que
    # aconteceu em 11/08, com a chave cortada em 18 chars por uma quebra
    # de linha no paste. Falhar aqui, cedo e sem vazar a chave.
    bruta = os.environ.get("ANTHROPIC_API_KEY", "")
    chave = bruta.strip()
    if not chave:
        raise SystemExit("ERRO: ANTHROPIC_API_KEY ausente no ambiente.")
    if any(c in chave for c in " \t\r\n"):
        raise SystemExit(
            f"ERRO: ANTHROPIC_API_KEY tem espaço ou quebra de linha no MEIO "
            f"({len(chave)} chars). O paste provavelmente quebrou.\n"
            f"       Use:  read -rs ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY")
    if len(chave) < 80:
        raise SystemExit(
            f"ERRO: ANTHROPIC_API_KEY tem {len(chave)} chars — curta demais. "
            f"A chave da Anthropic tem ~108.\n"
            f"       Foi truncada no paste. Use:  "
            f"read -rs ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY")

    from edp.llm.providers.anthropic import AnthropicProvider
    from edp.llm.providers.base import CompletionRequest, Message, ProviderConfig, Role

    prov = AnthropicProvider(ProviderConfig(api_key=chave, model=MODELO,
                                            timeout_s=300.0))

    def perguntar(texto_pergunta: str) -> tuple[str | None, float, str]:
        r = prov.complete(CompletionRequest(
            messages=[Message(Role.USER, USUARIO.format(
                paginas=paginas_txt, pergunta=texto_pergunta))],
            system=SISTEMA, temperature=TEMPERATURA, max_tokens=32))
        return parse(r.text), (r.metrics.cost_usd or 0.0), (r.text or "").strip()

    if args.smoke:
        # N2 está FORA do critério (§5) — olhar a resposta dela não gasta
        # grau de liberdade nenhum. Só prova que a API responde e o
        # parser extrai.
        n2 = next(t for q, _, t in PERGUNTAS if q == "N2")
        v, custo, cru = perguntar(n2)
        print(f"\nsmoke em N2 (fora do critério): parse={v!r}  "
              f"cru={cru!r}  custo=US$ {custo:.4f}")
        print("OK — encanamento e parser funcionam." if v else
              "FALHA — o parser não extraiu VEREDITO. Não rodar o completo.")
        return 0 if v else 1

    # ── Rodada completa ──────────────────────────────────────────────────────
    resultados: dict[str, dict] = {}
    custo_total = 0.0
    print()
    for qid, classe, texto in PERGUNTAS:
        vereditos, brutos = [], []
        for _ in range(RODADAS):
            v, c, cru = perguntar(texto)
            vereditos.append(v)
            brutos.append(cru)
            custo_total += c
        cont = Counter(vereditos)
        unanime = len(cont) == 1 and None not in cont
        veredito = vereditos[0] if unanime else None
        resultados[qid] = {"classe": classe, "vereditos": vereditos,
                           "unanime": unanime, "veredito": veredito,
                           "brutos": brutos}
        marca = veredito if unanime else f"INSTÁVEL {dict(cont)}"
        print(f"  {qid:<4} {classe:<5} {marca}")

    # ── Condições ────────────────────────────────────────────────────────────
    def ok(qid: str, esperado: str) -> bool:
        """Instável conta como não-acerto (E-2.4)."""
        r = resultados[qid]
        return bool(r["unanime"]) and r["veredito"] == esperado

    a = sum(1 for q in CONJ_A if ok(q, "SIM"))
    b = sum(1 for q in CONJ_B if ok(q, "NAO"))
    unanimes = sum(1 for r in resultados.values() if r["unanime"])

    cond = [
        ("a", f"A com SIM: {a}/4 (exige >=3)", a >= 3),
        ("b", f"B com NAO: {b}/8 (exige >=7)", b >= 7),
        ("c", f"Q3 -> {resultados['Q3']['veredito']} (exige NAO)", ok("Q3", "NAO")),
        ("d", f"N3 -> {resultados['N3']['veredito']} (exige NAO)", ok("N3", "NAO")),
        ("e", f"unânimes: {unanimes}/15 (exige >={MIN_UNANIMES})",
         unanimes >= MIN_UNANIMES),
    ]
    print("\n" + "=" * 72)
    for k, desc, v in cond:
        print(f"  ({k}) {'OK ' if v else 'NAO'}  {desc}")

    if not cond[-1][2]:
        veredito = "ANULADA — instrumento não-determinístico (E-2.4)"
    else:
        veredito = "PASSA" if all(v for _, _, v in cond) else "FALHA"
    print("=" * 72)
    print(f"VEREDITO: {veredito}")
    print(f"custo real: US$ {custo_total:.4f}  "
          f"(estimado em E-2.7: US$ 1,04)")

    args.saida.write_text(json.dumps({
        "rodado_em": datetime.now(timezone.utc).isoformat(),
        "modelo": MODELO, "temperatura": TEMPERATURA, "rodadas": RODADAS,
        "sha_corpus": sha, "condicoes": {k: v for k, _, v in cond},
        "veredito": veredito, "custo_usd": round(custo_total, 4),
        "resultados": resultados,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"bruto salvo em: {args.saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
