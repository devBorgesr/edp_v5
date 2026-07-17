"""
failsafe.py — Proteção e recovery de memória do EDP v3.
"""
import json, shutil, time
from pathlib import Path
from typing import List, Optional, Tuple

_REQUIRED = {"id", "text", "embedding", "timestamp", "score_inicial"}
_BACKUP_SUFFIX = "_backups"

def validate_memory_json(path: str) -> Tuple[bool, List[str]]:
    errors: List[str] = []; p = Path(path)
    if not p.exists(): return False, [f"Não encontrado: {path}"]
    try:
        with open(path, "r", encoding="utf-8") as f: data = json.load(f)
    except json.JSONDecodeError as e: return False, [f"JSON inválido: {e}"]
    if not isinstance(data, list): return False, ["Esperava lista"]
    for i, e in enumerate(data):
        if not isinstance(e, dict): errors.append(f"Entry {i}: não é dict"); continue
        missing = _REQUIRED - set(e.keys())
        if missing: errors.append(f"Entry {i}: campos faltando: {missing}")
        emb = e.get("embedding")
        if emb is not None and (not isinstance(emb, list) or len(emb) == 0):
            errors.append(f"Entry {i}: embedding inválido")
        ts = e.get("timestamp")
        if ts is not None and not isinstance(ts, (int, float)):
            errors.append(f"Entry {i}: timestamp inválido")
    return len(errors) == 0, errors

def detect_corruption(memory_store) -> dict:
    issues: List[dict] = []; now = time.time(); future = now + 86400
    def _check(entries, layer):
        for i, e in enumerate(entries):
            eid = e.get("id", f"idx_{i}")
            if not e.get("id"):              issues.append({"id": eid, "layer": layer, "issue": "sem_id"})
            emb = e.get("embedding")
            if emb is None or (isinstance(emb, list) and len(emb) == 0):
                issues.append({"id": eid, "layer": layer, "issue": "embedding_vazio"})
            ts = e.get("timestamp", 0)
            if isinstance(ts, (int, float)) and ts > future:
                issues.append({"id": eid, "layer": layer, "issue": "timestamp_futuro"})
            if not e.get("text", "").strip():
                issues.append({"id": eid, "layer": layer, "issue": "texto_vazio"})
    _check(memory_store.episodic.entries, "episodic")
    _check(memory_store.semantic.entries, "semantic")
    return {"issues": issues, "n_issues": len(issues), "is_healthy": len(issues) == 0,
            "episodic_n": len(memory_store.episodic.entries), "semantic_n": len(memory_store.semantic.entries)}

def incremental_backup(memory_store) -> dict:
    from .config import MEMORY_DIR
    session    = memory_store.session_id
    backup_dir = MEMORY_DIR / f"{session}{_BACKUP_SUFFIX}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time()); backed = []; errors = []
    for src in [memory_store.episodic.path, memory_store.semantic.path]:
        src = Path(src)
        if not src.exists(): continue
        # Precisa do prefixo de sessão: restore_backup() busca
        # "{session}_{layer}_{ts}.json" / glob "*_episodic_*.json" — sem o
        # prefixo, o backup fica órfão e restore_backup() nunca o acha
        # (retorna "Backup vazio" mesmo com o arquivo presente em disco;
        # achado escrevendo o teste de roundtrip, T1 Fase 3).
        dst = backup_dir / f"{session}_{src.stem}_{ts}{src.suffix}"
        try: shutil.copy2(src, dst); backed.append(str(dst))
        except Exception as e: errors.append({"file": str(src), "error": str(e)})
    _prune_backups(backup_dir)
    return {"session_id": session, "timestamp": ts, "backed_files": backed,
            "errors": errors, "backup_dir": str(backup_dir)}

def _prune_backups(backup_dir: Path, keep: int = 5) -> None:
    files = sorted(backup_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for old in files[:-keep] if len(files) > keep else []:
        old.unlink(missing_ok=True)

def restore_backup(memory_store, backup_timestamp: Optional[int] = None) -> dict:
    from .config import MEMORY_DIR
    session    = memory_store.session_id
    backup_dir = MEMORY_DIR / f"{session}{_BACKUP_SUFFIX}"
    if not backup_dir.exists(): return {"ok": False, "reason": "Nenhum backup encontrado"}
    all_b = sorted(backup_dir.glob("*_episodic_*.json"), key=lambda p: p.stat().st_mtime)
    if not all_b: return {"ok": False, "reason": "Backup vazio"}
    chosen_ts = backup_timestamp or int(all_b[-1].stem.split("_")[-1])
    restored = []; errors = []
    for layer in ("episodic", "semantic"):
        bf = backup_dir / f"{session}_{layer}_{chosen_ts}.json"
        if not bf.exists(): continue
        ok, errs = validate_memory_json(str(bf))
        if not ok: errors.append({"file": str(bf), "errors": errs}); continue
        dst = getattr(memory_store, layer).path
        shutil.copy2(bf, dst); restored.append(str(dst))
    if restored:
        memory_store.episodic._load(); memory_store.semantic._load()
    return {"ok": len(errors) == 0, "restored_files": restored,
            "errors": errors, "backup_timestamp": chosen_ts}

