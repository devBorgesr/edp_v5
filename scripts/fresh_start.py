"""
scripts/fresh_start.py — Peça 0.5 do EDP

Operação ÚNICA e EXCEPCIONAL: archiva episódicos atuais, zera memória episódica
ativa, zera co-ocorrência, preserva sessão vitalícia (edp_lifetime) e conceitos
semânticos (mas desacopla derived_from).

Filosofia (decidida pelo usuário):
  Os 200 entries atuais foram gerados num EDP com fundação fraca (relógio
  errado, sem schema temporal, sem tracking de sessão vitalícia). O novo EDP
  tem fundação robusta (clock NTP, schema v1, edp_session_id vitalício).
  Em vez de migrar com "marca de não-confiável", o usuário decidiu:

    1. Backup dos episódicos atuais → backups/
    2. Zerar episódicos ativos (fresh start)
    3. Manter edp_lifetime (continuidade do EDP)
    4. Manter semânticos (conhecimento abstraído tem valor)
    5. Zerar co-ocorrência (pares ficariam órfãos)

  Depois, o usuário reimporta manualmente, fragmentado, no ritmo dele,
  escolhendo o que vale a pena reentrar no EDP.

Uso:
    # Dry-run (mostra o que vai mudar sem aplicar):
    python scripts/fresh_start.py

    # Aplica de verdade (cria backup automático e modifica):
    python scripts/fresh_start.py --apply

Detalhes:
  - Idempotente: se já foi executado, detecta e pula (via migrations.json)
  - Backup automático antes de modificar
  - Atomicidade: usa write atômico do edp.memory
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Garante import de edp/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


MIGRATION_ID = "fresh_start_pre_relogio_interno_v1"


def main():
    parser = argparse.ArgumentParser(description="Fresh start do EDP (peça 0.5).")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica de verdade. Sem isso, roda em dry-run.",
    )
    parser.add_argument(
        "--session-id",
        default="default",
        help="ID da sessão a resetar (default: 'default').",
    )
    parser.add_argument(
        "--clean-derived-from",
        choices=["yes", "no"],
        default=None,
        help="Limpar 'derived_from' dos semânticos para não apontar para "
             "IDs órfãos? yes/no. Default: pergunta interativamente.",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("EDP — Fresh Start (peça 0.5)")
    print("=" * 70)
    print()

    # Importa edp para usar MEMORY_DIR e funções
    from edp.config import MEMORY_DIR
    from edp.memory import _atomic_write_json, _safe_load_json
    from edp.clock import now as clock_now

    session_id = args.session_id
    edp_data = Path(MEMORY_DIR).parent  # MEMORY_DIR = .../sessions; pai = edp_data
    backups_dir = edp_data / "backups"
    migrations_path = MEMORY_DIR / "_migrations.json"

    print(f"Diretório EDP: {edp_data}")
    print(f"Diretório de sessões: {MEMORY_DIR}")
    print(f"Diretório de backups: {backups_dir}")
    print(f"Sessão alvo: {session_id}")
    print()

    # ── Verifica se já foi aplicado ──────────────────────────────────────────
    migrations = {}
    if migrations_path.exists():
        try:
            migrations = _safe_load_json(migrations_path) or {}
        except Exception:
            migrations = {}

    if MIGRATION_ID in migrations:
        applied_at = migrations[MIGRATION_ID].get("applied_at")
        applied_dt = (
            datetime.fromtimestamp(applied_at).strftime("%Y-%m-%d %H:%M:%S")
            if applied_at else "?"
        )
        print(f"⚠ Fresh start JÁ aplicado em {applied_dt}.")
        print(f"  Migration ID: {MIGRATION_ID}")
        print(f"  Para forçar nova execução, remova a entrada de {migrations_path}.")
        print()
        print("Saindo.")
        return 0

    # ── Identifica arquivos ──────────────────────────────────────────────────
    episodic_path    = MEMORY_DIR / f"{session_id}_episodic.json"
    semantic_path    = MEMORY_DIR / f"{session_id}_semantic.json"
    co_occurrence_path = MEMORY_DIR / f"{session_id}_co_occurrence.json"

    print("Arquivos detectados:")
    files_status = []
    for label, p in [("Episódicos", episodic_path),
                     ("Semânticos", semantic_path),
                     ("Co-ocorrência", co_occurrence_path)]:
        if p.exists():
            size = p.stat().st_size
            data = _safe_load_json(p)
            count = (
                len(data) if isinstance(data, list)
                else len(data) if isinstance(data, dict)
                else "?"
            )
            print(f"  ✓ {label}: {p.name} ({size:,} bytes, {count} entries)")
            files_status.append((label, p, data, count))
        else:
            print(f"  - {label}: {p.name} (não existe, ignorado)")
            files_status.append((label, p, None, 0))
    print()

    # ── Plano de ação ────────────────────────────────────────────────────────
    print("Plano de ação:")
    print("  1. Backup de episódicos → backups/<session>_episodic.<timestamp>.bak.json")
    print("  2. Backup de co-ocorrência → backups/<session>_co_occurrence.<timestamp>.bak.json")
    print("  3. Episódicos ativos → [] (vazio)")
    print("  4. Co-ocorrência ativa → {} (vazio)")
    print("  5. Semânticos → MANTIDOS (decisão D2=b do usuário)")
    print("  6. edp_lifetime → MANTIDO (decisão D1=a do usuário)")
    print()

    # ── Decisão sobre derived_from ──────────────────────────────────────────
    sem_label, sem_path, sem_data, sem_count = files_status[1]
    has_semantic = sem_data is not None and isinstance(sem_data, list) and len(sem_data) > 0

    clean_derived = None
    if has_semantic:
        # Verifica se algum semântico tem derived_from não-vazio
        has_orphans_risk = any(
            isinstance(s, dict) and s.get("derived_from")
            for s in sem_data
        )
        if has_orphans_risk:
            print("⚠ Semânticos têm 'derived_from' apontando para episódicos.")
            print("  Após o reset, esses IDs serão órfãos.")
            print()
            if args.clean_derived_from is None:
                if not args.apply:
                    print("  Em --apply real, será perguntado se limpar derived_from.")
                    clean_derived = None
                else:
                    resp = input("  Limpar derived_from dos semânticos para "
                                 "honestidade? [s/N]: ").strip().lower()
                    clean_derived = resp in ("s", "sim", "y", "yes")
            else:
                clean_derived = args.clean_derived_from == "yes"

            if clean_derived is not None:
                print(f"  → Decisão: {'limpar' if clean_derived else 'manter'} derived_from")
            print()

    # ── Aplicar ou só mostrar ────────────────────────────────────────────────
    if not args.apply:
        print("=" * 70)
        print("MODO DRY-RUN — nada foi modificado.")
        print("Para aplicar de verdade: python scripts/fresh_start.py --apply")
        print("=" * 70)
        return 0

    print("=" * 70)
    print("APLICANDO. Backup automático será criado primeiro.")
    print("=" * 70)
    print()

    # Cria diretório de backups
    backups_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── Backup ────────────────────────────────────────────────────────────────
    backup_paths = {}
    for label, src, data, count in files_status:
        if data is None:
            continue
        if label == "Semânticos":
            continue  # não vai mudar, não precisa backup
        ext = ".json"
        backup_name = src.name.replace(ext, f".{ts}.bak{ext}")
        backup_path = backups_dir / backup_name
        _atomic_write_json(backup_path, data, indent=2)
        backup_paths[label] = backup_path
        print(f"  ✓ Backup de {label}: {backup_path}")

    print()

    # ── Limpa derived_from se decidido ───────────────────────────────────────
    if has_semantic and clean_derived:
        # Backup dos semânticos antes da modificação
        backup_name = sem_path.name.replace(".json", f".{ts}.bak.json")
        backup_path = backups_dir / backup_name
        _atomic_write_json(backup_path, sem_data, indent=2)
        print(f"  ✓ Backup de Semânticos (antes de limpar derived_from): {backup_path}")

        # Limpa derived_from
        cleaned_count = 0
        for s in sem_data:
            if isinstance(s, dict) and s.get("derived_from"):
                s["_derived_from_pre_reset"] = s["derived_from"]  # arquiva
                s["derived_from"] = []
                cleaned_count += 1

        _atomic_write_json(sem_path, sem_data, indent=2)
        print(f"  ✓ derived_from limpo em {cleaned_count} semânticos "
              f"(originais arquivados em _derived_from_pre_reset)")

    # ── Zera episódicos ──────────────────────────────────────────────────────
    if episodic_path.exists():
        _atomic_write_json(episodic_path, [], indent=2)
        print(f"  ✓ Episódicos zerados: {episodic_path}")

    # ── Zera co-ocorrência ───────────────────────────────────────────────────
    if co_occurrence_path.exists():
        # Co-ocorrência geralmente é dict, não list
        co_data = _safe_load_json(co_occurrence_path)
        empty_co = {} if isinstance(co_data, dict) else []
        _atomic_write_json(co_occurrence_path, empty_co, indent=2)
        print(f"  ✓ Co-ocorrência zerada: {co_occurrence_path}")

    # ── Marca migração como aplicada ─────────────────────────────────────────
    migrations[MIGRATION_ID] = {
        "applied_at": clock_now(),
        "applied_at_iso": datetime.now().isoformat(),
        "session_id": session_id,
        "backups": {k: str(v) for k, v in backup_paths.items()},
        "cleaned_derived_from": bool(clean_derived) if has_semantic else None,
    }
    _atomic_write_json(migrations_path, migrations, indent=2)
    print(f"  ✓ Migração registrada: {migrations_path}")

    print()
    print("=" * 70)
    print("Fresh start APLICADO.")
    print()
    print("Próximos passos:")
    print(f"  1. Sobe servidor: python run.py serve")
    print(f"  2. EDP começa vazio (episódicos=0, co-ocorrência=0)")
    print(f"  3. Backups disponíveis em: {backups_dir}")
    print(f"  4. Reimporte os 200 entries no seu ritmo, via chat,")
    print(f"     escolhendo o que vale a pena retomar.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
