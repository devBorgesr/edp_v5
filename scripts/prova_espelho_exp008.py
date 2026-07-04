"""
scripts/prova_espelho_exp008.py — Prova-no-espelho do Experimento 008.

Entry point explícito e descartável para REVISAR o setup do exp008 ANTES do
disparo real (que custa tempo de embedding e grava registros reais). Roda o
encanamento INTEIRO em dry-run:

  - clona (read-only) as memórias REAIS do scope cognitive de produção;
  - constrói os pares (query -> alvo) pela regra CONGELADA do pré-registro;
  - imprime cada par para inspeção humana;
  - chama o retrieve REAL (MemoryStore.retrieve) sobre o clone ISOLADO,
    confirmando que o caminho de produção é exercitado (não um mock);
  - confere o fingerprint anti-vazamento (produção cognitive intacta — INV-5).

NÃO arma a Bancada, NÃO toca produção, NÃO chama o LLM. Os registros gravados
ficam marcados como dry_run=True (o scorer os ignora).

Uso:
  set EDP_BASE_DIR=C:\\edp_data        (ou export, conforme a casa)
  python scripts/prova_espelho_exp008.py
  python scripts/prova_espelho_exp008.py --no-record   (só imprime, não grava)

Depois de revisar e concordar com o setup, o disparo REAL é:
  set EDP_LAB_ARMED=1
  python -m edp.lab.exp008
  python -m edp.lab.exp008 --score     (Wilson + veredito, pós-coleta)
  python -m edp.lab.exp008 --audit      (os exemplos: o que veio, certo/errado)
"""
from __future__ import annotations

import argparse
import logging
import sys


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Prova-no-espelho (dry-run) do Experimento 008.")
    p.add_argument("--prod-session", default="default",
                   help="sessão de produção a clonar (read-only). Default: default")
    p.add_argument("--no-record", action="store_true",
                   help="não grava no prontuário (mesmo como dry_run); só imprime.")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Reusa o main do experimento congelado, sempre em --dry-run (nunca arma daqui).
    from edp.lab.exp008 import main as exp008_main

    forwarded = ["--dry-run", "--prod-session", args.prod_session]
    if args.no_record:
        forwarded.append("--no-record")
    return exp008_main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
