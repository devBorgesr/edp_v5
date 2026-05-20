"""
repair_episodic.py — Diagnostica e repara episodic.json corrompido.

Causa típica: write interrompido (Ctrl+C durante save), gerando
"Extra data" no fim do arquivo.

Uso:
  # 1. Diagnostica (não modifica nada)
  python repair_episodic.py
  
  # 2. Repara (preserva backup automático antes)
  python repair_episodic.py --repair

Por segurança, faz backup em episodic.json.broken antes de qualquer escrita.
"""
import sys
import os
import json
import argparse
import shutil
from pathlib import Path


def find_episodic_path(session_id: str = "default") -> Path:
    """Localiza o episodic.json da sessão.
    
    Estrutura real do EDP: C:\\edp_data\\sessions\\{session}_episodic.json
    """
    base = os.environ.get("EDP_BASE_DIR", "")
    if not base:
        print("✗ EDP_BASE_DIR não está setado. Rode:")
        print('  $env:EDP_BASE_DIR = "C:\\edp_data"')
        sys.exit(1)
    
    # Estrutura real: sessions/{session}_episodic.json
    candidates = [
        Path(base) / "sessions" / f"{session_id}_episodic.json",
        Path(base) / "episodic" / session_id / "episodic.json",  # alt
        Path(base) / f"{session_id}_episodic.json",               # alt
    ]
    
    for p in candidates:
        if p.exists():
            return p
    
    print(f"✗ Arquivo não encontrado. Tentei:")
    for p in candidates:
        print(f"    {p}")
    sys.exit(1)


def try_parse_strict(content: str):
    """Tenta parse padrão. Retorna (sucesso, dados, erro)."""
    try:
        return True, json.loads(content), None
    except json.JSONDecodeError as e:
        return False, None, e


def try_parse_truncated(content: str, error_pos: int):
    """
    Tenta parse cortando no ponto do erro.
    
    Se o JSON está corrompido por "Extra data" depois de N chars,
    geralmente os primeiros N chars são um JSON válido.
    """
    # Tenta cortar progressivamente para trás até achar fim de array/objeto
    candidates = []
    
    # Estratégia 1: corta no error_pos exato
    candidates.append(content[:error_pos])
    
    # Estratégia 2: procura último "]" antes do erro
    last_bracket = content.rfind("]", 0, error_pos)
    if last_bracket > 0:
        candidates.append(content[:last_bracket + 1])
    
    # Estratégia 3: procura último "}" antes do erro (se for objeto)
    last_brace = content.rfind("}", 0, error_pos)
    if last_brace > 0:
        # Pode ser meio de um dict, precisa fechar bem
        # Conta abertura/fechamento
        truncated = content[:last_brace + 1]
        # Se for array de objetos, fecha com ]
        open_arrays = truncated.count("[")
        close_arrays = truncated.count("]")
        if open_arrays > close_arrays:
            truncated += "\n]" * (open_arrays - close_arrays)
            candidates.append(truncated)
    
    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, list) and len(data) > 0:
                return True, data, len(cand)
        except json.JSONDecodeError:
            continue
    
    return False, None, 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repair", action="store_true",
                        help="Aplica reparo (sem isso é só diagnóstico)")
    parser.add_argument("--session", default="default")
    args = parser.parse_args()
    
    print("=" * 60)
    print("repair_episodic.py — diagnóstico e reparo de episodic.json")
    print("=" * 60)
    
    path = find_episodic_path(args.session)
    print(f"Arquivo: {path}")
    print(f"Tamanho: {path.stat().st_size:,} bytes")
    print()
    
    content = path.read_text(encoding="utf-8")
    print(f"Caracteres: {len(content):,}")
    print()
    
    # Diagnóstico
    print("─" * 60)
    print("DIAGNÓSTICO")
    print("─" * 60)
    
    ok, data, err = try_parse_strict(content)
    if ok:
        print(f"✓ JSON válido. {len(data)} entries.")
        print(f"  Nenhum reparo necessário.")
        return
    
    print(f"✗ JSON corrompido: {err}")
    print(f"  Posição do erro: char {err.pos:,} (linha {err.lineno}, col {err.colno})")
    print(f"  Bytes válidos: {err.pos:,} de {len(content):,}")
    print(f"  Bytes extras: {len(content) - err.pos:,}")
    print()
    
    # Tenta reparar
    print("─" * 60)
    print("TENTATIVA DE RECUPERAÇÃO")
    print("─" * 60)
    
    ok, data, used_len = try_parse_truncated(content, err.pos)
    if not ok:
        print(f"✗ Não foi possível recuperar automaticamente.")
        print(f"  Recomendo restaurar do backup:")
        print(f"  Copy-Item C:\\edp_data_backup_19_05 C:\\edp_data -Recurse -Force")
        return
    
    print(f"✓ Conseguiu recuperar {len(data)} entries usando os primeiros {used_len:,} bytes")
    print(f"  Perda: {len(content) - used_len:,} bytes de dados extras")
    print()
    
    # Mostra os últimos 3 entries recuperados
    print("Últimos 3 entries recuperados:")
    for i, entry in enumerate(data[-3:], start=len(data)-2):
        text = (entry.get("text") or "")[:60].replace("\n", " ")
        print(f"  [{i}] {text}...")
    print()
    
    if not args.repair:
        print("─" * 60)
        print(f"DRY RUN. Para aplicar o reparo:")
        print(f"  python repair_episodic.py --repair")
        return
    
    # Aplicar
    backup_path = path.with_suffix(".json.broken")
    print(f"Backup do arquivo corrompido: {backup_path}")
    shutil.copy2(path, backup_path)
    
    print(f"Escrevendo arquivo reparado...")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print()
    print(f"✓ Reparado.")
    print(f"  Original (corrompido): {backup_path}")
    print(f"  Atual (válido):        {path}")
    print(f"  Entries: {len(data)}")
    print()
    print(f"Próximos passos:")
    print(f"  1. python cleanup_orphans.py        (DRY RUN)")
    print(f"  2. python cleanup_orphans.py --apply")
    print(f"  3. python run.py serve")


if __name__ == "__main__":
    main()
