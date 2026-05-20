"""
cleanup_orphans.py — Apaga memórias órfãs de chamadas internas do EDP.

Contexto:
  Antes do hotfix v3.13.3, o session_summary chamava llm_runtime.chat()
  sem passar store_to_memory=False. Resultado: as chamadas internas para
  gerar resumo e label viraram entries episódicas como se fossem turnos
  reais do usuário.

Este script identifica e apaga essas memórias órfãs.

Critérios de identificação:
  1. text contém "Gere um label curto em snake_case" ou
     "Resumo conciso desta conversa" (prompts internos)
  2. text começa com label snake_case isolado (resposta do LLM ao prompt)
  3. text começa com "[session_summary]" mas source_type != "session_summary"
     (entry mal-marcada)

Uso:
  cd C:\\Users\\central\\Downloads\\edp_v3_backup\\edp_v3_backup
  $env:EDP_BASE_DIR = "C:\\edp_data"
  python cleanup_orphans.py
  
  Por padrão, faz DRY-RUN (só lista, não apaga).
  Para apagar de verdade:
  python cleanup_orphans.py --apply
"""
import sys
import os
import re
import argparse

# Garante que edp/ está no path
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

from edp.memory import MemoryStore

# Padrões que identificam chamadas internas órfãs
INTERNAL_PROMPTS = [
    r"Gere um label curto em snake_case",
    r"Resumo conciso desta conversa",
    r"Voce e um resumidor objetivo",
    r"Voce gera labels curtos em snake_case",
]

# Padrão de resposta-label (texto curto snake_case isolado como Q ou A)
LABEL_RESPONSE = re.compile(r"^\s*(?:Q:|A:)?\s*[a-z_]{3,40}\s*$", re.MULTILINE)


def is_orphan(entry: dict) -> tuple[bool, str]:
    """Retorna (é_órfã, motivo)."""
    text = entry.get("text", "") or ""
    source_type = entry.get("source_type", "")
    
    # Regra 1: entry com texto começando "[session_summary]" mas source_type
    # diferente — deveria ser marcada mas não foi
    if text.startswith("[session_summary]") and source_type != "session_summary":
        return True, "marcada como [session_summary] mas source_type não bate"
    
    # Regra 2: prompts internos vazaram para memória
    for pattern in INTERNAL_PROMPTS:
        if re.search(pattern, text, re.IGNORECASE):
            return True, f"contém prompt interno: '{pattern[:40]}'"
    
    # Regra 3: resposta isolada que parece label snake_case puro
    # (texto Q/A muito curto onde A é só uma palavra snake_case)
    if text.startswith("Q:") and "A:" in text:
        # Pega só a parte da resposta
        a_part = text.split("A:", 1)[1].strip()
        if (
            len(a_part) < 50
            and re.match(r"^[a-z][a-z_0-9]{2,30}$", a_part)
            and "_" in a_part
        ):
            return True, f"resposta é só label snake_case: '{a_part[:30]}'"
    
    return False, ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica de verdade (default: DRY RUN)",
    )
    parser.add_argument(
        "--mode",
        choices=["delete", "fix"],
        default="fix",
        help=(
            "Ação para entries identificadas: "
            "'fix' (default) corrige source_type para 'session_summary' "
            "preservando o conteúdo. "
            "'delete' apaga as entries (modo antigo)."
        ),
    )
    parser.add_argument(
        "--session",
        default="default",
        help="Session ID (default: 'default')",
    )
    args = parser.parse_args()

    print(f"=" * 60)
    print(f"EDP cleanup_orphans.py — v3.13.4")
    print(f"=" * 60)
    print(f"Session: {args.session}")
    print(f"Modo ação: {args.mode.upper()}")
    print(f"Modo execução: {'APLICAR' if args.apply else 'DRY RUN (só lista)'}")
    print(f"Base dir: {os.environ.get('EDP_BASE_DIR', '(não setada)')}")
    print()

    mem = MemoryStore(args.session)
    total_before = len(mem.episodic.entries)
    print(f"Total de memórias episódicas: {total_before}")
    print()

    # Identifica candidatas — separa em duas categorias:
    # - mismarked_summary: tem texto [session_summary] mas source_type errado
    #                       → pode ser CORRIGIDO (fix) ou apagado (delete)
    # - internal_orphan:    chamada interna vazada (prompt + label)
    #                       → SEMPRE apagar (não tem valor preservar)
    mismarked_summaries = []
    internal_orphans = []

    for entry in mem.episodic.entries:
        text = entry.get("text", "") or ""
        source_type = entry.get("source_type", "")

        # Categoria 1: session_summary mal-marcado
        if text.startswith("[session_summary]") and source_type != "session_summary":
            mismarked_summaries.append(entry)
            continue

        # Categoria 2: prompts internos vazados (sempre apaga)
        is_internal = False
        internal_reason = ""
        for pattern in INTERNAL_PROMPTS:
            if re.search(pattern, text, re.IGNORECASE):
                is_internal = True
                internal_reason = f"contém prompt interno: '{pattern[:40]}'"
                break

        if not is_internal and text.startswith("Q:") and "A:" in text:
            a_part = text.split("A:", 1)[1].strip()
            if (
                len(a_part) < 50
                and re.match(r"^[a-z][a-z_0-9]{2,30}$", a_part)
                and "_" in a_part
            ):
                is_internal = True
                internal_reason = f"resposta é só label snake_case: '{a_part[:30]}'"

        if is_internal:
            internal_orphans.append((entry, internal_reason))

    total_actions = len(mismarked_summaries) + len(internal_orphans)
    print(f"Session_summaries mal-marcados (source_type errado): {len(mismarked_summaries)}")
    print(f"Órfãs de chamada interna (lixo a apagar):            {len(internal_orphans)}")
    print(f"Total de ações: {total_actions}")
    print()

    if total_actions == 0:
        print("✓ Nenhuma ação necessária.")
        return

    if mismarked_summaries:
        print("─" * 60)
        print(f"SESSION_SUMMARIES MAL-MARCADOS (ação: {args.mode.upper()})")
        print("─" * 60)
        for i, entry in enumerate(mismarked_summaries, 1):
            eid = entry.get("id", "?")
            text = (entry.get("text") or "")[:80].replace("\n", " ")
            current_st = entry.get("source_type", "(vazio)")
            print(f"\n[{i}] id={eid[:8]}...")
            print(f"    source_type atual: '{current_st}'")
            if args.mode == "fix":
                print(f"    ação: corrigir source_type → 'session_summary'")
            else:
                print(f"    ação: APAGAR (você escolheu --mode=delete)")
            print(f"    texto: {text}...")

    if internal_orphans:
        print()
        print("─" * 60)
        print(f"ÓRFÃS DE CHAMADA INTERNA (sempre apaga, sem valor)")
        print("─" * 60)
        for i, (entry, reason) in enumerate(internal_orphans, 1):
            eid = entry.get("id", "?")
            text = (entry.get("text") or "")[:80].replace("\n", " ")
            print(f"\n[{i}] id={eid[:8]}...")
            print(f"    motivo: {reason}")
            print(f"    texto: {text}...")

    print()
    print("─" * 60)

    if not args.apply:
        action_desc = "corrigir source_type" if args.mode == "fix" else "apagar"
        print(f"\nDRY RUN. Para {action_desc} (preservando ou apagando):")
        print(f"  python cleanup_orphans.py --apply")
        print(f"  python cleanup_orphans.py --apply --mode=delete  # se preferir apagar tudo")
        return

    # APLICAR
    fixed_count = 0
    deleted_count = 0

    if args.mode == "fix":
        # Corrige source_type dos session_summaries mal-marcados
        ids_to_fix = {e.get("id") for e in mismarked_summaries}
        for entry in mem.episodic.entries:
            if entry.get("id") in ids_to_fix:
                entry["source_type"] = "session_summary"
                fixed_count += 1
    else:
        # Apaga tudo (modo delete)
        ids_to_delete = {e.get("id") for e in mismarked_summaries}
        mem.episodic.entries = [
            e for e in mem.episodic.entries if e.get("id") not in ids_to_delete
        ]
        deleted_count = len(ids_to_delete)

    # Sempre apaga internal_orphans (não há valor em preservar)
    ids_internal = {e.get("id") for e, _ in internal_orphans}
    if ids_internal:
        mem.episodic.entries = [
            e for e in mem.episodic.entries if e.get("id") not in ids_internal
        ]
        deleted_count += len(ids_internal)

    # Força save
    mem.episodic._dirty = True
    mem.episodic.save()

    total_after = len(mem.episodic.entries)
    print(f"\n✓ Concluído.")
    print(f"  Total antes: {total_before}")
    print(f"  Total depois: {total_after}")
    if fixed_count:
        print(f"  Source_type corrigido: {fixed_count}")
    if deleted_count:
        print(f"  Apagadas: {deleted_count}")
    print(f"\nReinicie o EDP para garantir que o cache em RAM seja atualizado.")


if __name__ == "__main__":
    main()
