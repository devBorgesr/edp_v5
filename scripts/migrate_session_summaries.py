#!/usr/bin/env python3
"""
scripts/migrate_session_summaries.py — Migração one-shot dos session_summary ANTIGOS.

Complemento do fix validado pelo exp009 (fix/session-summary-gravador): o fix muda
como NOVOS summaries nascem; este script corrige os JÁ EXISTENTES no store, que
continuam com os privilégios de nascença.

O QUE FAZ (exatamente o estado que o exp009 validou — trat_gravador):
  Para toda entry com source_type == "session_summary":
    prioridade       "alta"     -> "media"
    epistemic_status "verified" -> "hypothesis"
  NADA MAIS. Em particular, "acessos" NÃO é tocado (o exp009 não validou
  reset de acessos; a promoção por mérito — acessos>=10 -> alta, memory.py:1788 —
  continua podendo recolocar um summary em alta se ele ganhar uso real).

SEGURANÇA:
  - DEFAULT é DRY-RUN: lista o que mudaria (id, acessos, campos atuais, preview)
    e NÃO escreve nada. Só escreve com --apply.
  - --apply faz BACKUP timestampado de cada arquivo antes (episodic.json.bak.<ts>)
    e escreve atômico (tmp + rename, padrão da casa).
  - Pós-apply: relê os arquivos e VERIFICA que nenhum summary alta/verified restou.
  - RODE COM O SERVIDOR EDP PARADO. O MemoryStore mantém as entries em RAM e salva
    por cima — migrar com o servidor vivo seria sobrescrito no próximo save.

USO (Windows PowerShell; análogo em Linux/macOS):
    # servidor EDP PARADO
    $env:EDP_BASE_DIR = "C:\\edp_data"            # aqui é o store REAL de propósito
    python scripts\\migrate_session_summaries.py             # 1) dry-run: revisar
    python scripts\\migrate_session_summaries.py --apply      # 2) migrar (com backup)

    # opcionais:
    #   $env:EDP_SESSION_ID = "default"    (default)
    #   $env:EDP_SCOPE      = "cognitive"  (default)
    #   --all-sessions      varre TODOS os diretórios sessions/*_*/

REVERSÃO: restaurar os .bak.<ts> por cima dos .json (o script imprime os caminhos).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

SS = "session_summary"
FILES = ("episodic.json", "semantic.json")


def _die(msg: str, code: int = 2):
    print(f"\n[ERRO] {msg}\n", file=sys.stderr)
    sys.exit(code)


def _load(path: Path):
    """(estrutura_original, lista_de_entries) preservando shape dict/list."""
    if not path.exists():
        return None, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        _die(f"{path} não é JSON válido ({e}). Nada foi alterado.")
    if isinstance(data, dict):
        return data, data.get("entries", [])
    if isinstance(data, list):
        return data, data
    return None, []


def _save_atomic(path: Path, original, entries: list):
    if isinstance(original, dict):
        payload = dict(original)
        payload["entries"] = entries
    else:
        payload = entries
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def find_candidates(entries: list) -> list:
    """Entries session_summary que ainda carregam privilégio de nascença."""
    out = []
    for e in entries:
        if not isinstance(e, dict) or e.get("source_type") != SS:
            continue
        if e.get("prioridade") == "alta" or e.get("epistemic_status") == "verified":
            out.append(e)
    return out


def migrate_entry(e: dict) -> list:
    """Aplica a migração numa entry. Retorna a lista de mudanças feitas."""
    changes = []
    if e.get("prioridade") == "alta":
        e["prioridade"] = "media"
        changes.append('prioridade: alta->media')
    if e.get("epistemic_status") == "verified":
        e["epistemic_status"] = "hypothesis"
        changes.append('epistemic_status: verified->hypothesis')
    return changes


def session_dirs(base: Path, all_sessions: bool) -> list:
    root = base / "sessions"
    if not root.is_dir():
        _die(f"{root} não existe. EDP_BASE_DIR está certo?")
    if all_sessions:
        return sorted(p for p in root.iterdir() if p.is_dir() and "_" in p.name)
    session = os.environ.get("EDP_SESSION_ID", "default")
    scope   = os.environ.get("EDP_SCOPE", "cognitive")
    d = root / f"{session}_{scope}"
    if not d.is_dir():
        _die(f"{d} não existe. Ajuste EDP_SESSION_ID/EDP_SCOPE ou use --all-sessions.")
    return [d]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Migra session_summary antigos: alta->media, verified->hypothesis. "
                    "DEFAULT: dry-run (não escreve).")
    p.add_argument("--apply", action="store_true",
                   help="escreve as mudanças (com backup). Sem esta flag, só lista.")
    p.add_argument("--all-sessions", action="store_true",
                   help="varre todos os diretórios sessions/*_*/ (default: só a sessão/scope das envs).")
    args = p.parse_args(argv)

    base = os.environ.get("EDP_BASE_DIR")
    if not base:
        _die("EDP_BASE_DIR não está setado. Para migrar o store real: "
             "$env:EDP_BASE_DIR = \"C:\\edp_data\" (com o servidor EDP PARADO).")
    base = Path(base).resolve()
    if not base.is_dir():
        _die(f"EDP_BASE_DIR não é um diretório: {base}")

    mode = "APPLY (escreve, com backup)" if args.apply else "DRY-RUN (não escreve nada)"
    print("=" * 92)
    print("MIGRAÇÃO session_summary — remove privilégios de nascença dos ANTIGOS (exp009)")
    print("=" * 92)
    print(f"  base    : {base}")
    print(f"  modo    : {mode}")
    print(f"  regra   : prioridade alta->media | epistemic_status verified->hypothesis")
    print(f"  intocado: acessos, source_type, topic_tag, texto, embedding — tudo o mais")
    if args.apply:
        print(f"  [ATENÇÃO] confirme que o servidor EDP está PARADO — senão o estado em RAM")
        print(f"            sobrescreve a migração no próximo save.")

    ts = int(time.time())
    total_cand = total_migrated = 0
    backups = []

    for d in session_dirs(base, args.all_sessions):
        for fname in FILES:
            path = d / fname
            original, entries = _load(path)
            if not entries:
                continue
            cands = find_candidates(entries)
            if not cands:
                continue
            total_cand += len(cands)
            print(f"\n── {path.relative_to(base)} : {len(cands)} summary(ies) com privilégio")
            for e in cands:
                prio = e.get("prioridade")
                st = e.get("epistemic_status")
                txt = (e.get("text") or "").replace("\n", " ")
                txt = txt.removeprefix("[session_summary]").strip()[:70]
                print(f"   id={str(e.get('id'))[:14]:14} acc={e.get('acessos', 0):>3} "
                      f"prio={prio!s:6} status={st!s:10} | {txt}")

            if args.apply:
                bak = path.with_suffix(path.suffix + f".bak.{ts}")
                bak.write_bytes(path.read_bytes())
                backups.append(bak)
                n = 0
                for e in cands:
                    if migrate_entry(e):
                        n += 1
                _save_atomic(path, original, entries)
                total_migrated += n
                print(f"   -> migradas {n} | backup: {bak.name}")

    print("\n" + "=" * 92)
    if total_cand == 0:
        print("RESULTADO: nenhum session_summary com privilégio de nascença encontrado. Nada a fazer.")
        print("=" * 92)
        return 0

    if not args.apply:
        print(f"DRY-RUN: {total_cand} summary(ies) seriam migradas. Nada foi escrito.")
        print("Revise a lista acima; se ok:  python scripts/migrate_session_summaries.py --apply")
        print("=" * 92)
        return 0

    # ── Verificação pós-apply: relê e confere que não restou privilégio ──────
    residual = 0
    for d in session_dirs(base, args.all_sessions):
        for fname in FILES:
            _o, entries = _load(d / fname)
            residual += len(find_candidates(entries))
    ok = residual == 0
    print(f"APPLY: {total_migrated} summary(ies) migradas | backups: {len(backups)} arquivo(s)")
    for b in backups:
        print(f"   {b}")
    print(f"VERIFICAÇÃO pós-apply (releitura): privilégios restantes = {residual} "
          f"-> {'OK' if ok else 'FALHOU — investigue antes de usar o store!'}")
    print("Reversão: copie os .bak por cima dos .json correspondentes.")
    print("=" * 92)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
