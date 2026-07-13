#!/usr/bin/env python3
"""
exp012_fase4_backfill_apply.py — exp012 Fase 4 (backfill): PASSADA REAL.

Autorizada pelo pesquisador em 12/07/2026 sobre o dry-run revisado (16 ids
únicos = 12 TP reais + 2 FP aceitos [728c1579, eceb81dc] + 2 negações de hoje
[d2fc578f, c7f58a47] com override explícito do estrato B). Reusa EXATAMENTE
a seleção do dry-run — exp012_fase4_backfill_dryrun.find_candidatas(), mesmo
estrato A / R4 puro. Nenhuma regra nova, nenhum id hardcoded aqui.

SEGURANÇA:
  1. Backup de sessions/ ANTES de qualquer escrita (primeiro passo, sempre —
     mesmo se 0 candidatas). Aborta (die) se o backup falhar; nada é escrito.
  2. IDEMPOTENTE: entry cujo answer_class já está setado é pulada (checado no
     dado recém-lido, não no snapshot do dry-run — re-rodar não duplica nada).
  3. Write atômico local (tmp no mesmo diretório + os.replace) — mesmo
     princípio de edp.memory._atomic_write_json, sem importar o módulo (que
     puxa sklearn/torch — stack pesada, inadequada p/ script offline).
  4. Auditoria própria (backfill_audit.jsonl, mesmo diretório do
     quarantine_audit.jsonl do write-path), 1 linha por gravação.
  5. SÓ CÓPIAS — mesmo guard de produção dos demais scripts exp012.

ACHADO DE FONTE (pré-escrita; NÃO muda o que este script grava, muda a
EXPECTATIVA de qualquer teste pós-backfill):
  - Peso-piso (edp/memory.py:717-719) vive dentro de EpisodicMemory.retrieve()
    — só cobre entries EPISÓDICAS.
  - SemanticMemory.retrieve() (edp/memory.py:1212-1263) NÃO tem nenhuma
    lógica de answer_class/NOT_FOUND_FLOOR — uma entry semântica marcada
    not_found NÃO ganha desconto no caminho cosine puro.
  - A exclusão do índice híbrido (edp/memory.py:1687-1745, _hybrid_index)
    cobre AMBAS as camadas igualmente (`for layer,pool in (("episodic",epi),
    ("semantic",sem))`, mesma condição `answer_class=="not_found"`).
  - EDP_HYBRID_RETRIEVAL é default=1 desde a Fase 3 (rebase) — o caminho
    ATIVO por padrão já cobre semantic corretamente. SE alguém desligar o
    híbrido (EDP_HYBRID_RETRIEVAL=0, fallback pro cosine puro), cópias
    semânticas de entries not_found voltam a competir SEM piso.

AVISO OBRIGATÓRIO (repetido no fim da execução): o índice híbrido em memória
NÃO é invalidado por esta escrita offline (edp/memory.py:1690-1693 — chave do
cache = len(epi)+len(sem)+último id de cada; edição in-place sem mudar
contagem não muda nenhum dos dois). REINICIE O SERVIDOR antes de qualquer
teste pós-backfill.

USO (servidor PARADO; aponte para CÓPIA — nunca produção):
  $env:EDP_BASE_DIR="C:\\edp_data_hybrid_test"
  python exp012_fase4_backfill_apply.py [session_id]
"""
import json, os, shutil, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp012_fase4_backfill_dryrun import LAYERS, find_candidatas


def die(msg):
    print(f"[ERRO] {msg}")
    sys.exit(2)


def _atomic_write_json(path, data):
    """tmp no mesmo diretório do destino + os.replace (atômico em POSIX e
    Windows). Se falhar no meio, o arquivo original fica intacto."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_backfill_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def backup_sessions(base):
    src = os.path.join(base, "sessions")
    if not os.path.isdir(src):
        die(f"sessions/ não existe em {base}")
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(base, f"sessions_backup_pre_backfill_{ts}")
    try:
        shutil.copytree(src, dst)
    except Exception as e:
        die(f"BACKUP FALHOU ({e}) — abortando, NADA foi escrito")
    return dst


def audit_path_for(base, sid):
    d = os.path.join(base, "sessions", f"{sid}_cognitive")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "backfill_audit.jsonl")


def log_audit(path, record):
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[AVISO] auditoria falhou p/ {record.get('id')}: {e}")


def main():
    base = os.environ.get("EDP_BASE_DIR") or die("EDP_BASE_DIR não setado")
    if os.path.basename(base.rstrip("/\\")).lower() == "edp_data":
        die("aponte para CÓPIA, não produção")
    sid = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EDP_SESSION_ID", "default")

    candidatas, vistas = find_candidatas(base, sid)
    cand_by_id = {c["id"]: c for c in candidatas}
    print(f"store={base} sid={sid} | entries vistas={vistas} | candidatas={len(candidatas)} "
          f"ids únicos={len(cand_by_id)}")

    # Backup é o PRIMEIRO passo sempre, mesmo com 0 candidatas — protege
    # contra qualquer escrita, não só as que sabemos de antemão que vão sair.
    backup = backup_sessions(base)
    print(f"[BACKUP] sessions/ copiado para: {backup}")

    audit_path = audit_path_for(base, sid)
    n_gravadas = n_puladas = n_erros = 0

    for scope, fname in LAYERS:
        p = os.path.join(base, "sessions", f"{sid}_{scope}", fname)
        if not os.path.exists(p):
            continue
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            print(f"[ERRO] não consegui ler {p}: {e} — arquivo pulado")
            n_erros += 1
            continue
        if not isinstance(data, list):
            print(f"[ERRO] {p}: formato inesperado (esperava lista de entries) — "
                  f"arquivo pulado, NADA escrito nele")
            n_erros += 1
            continue

        mudou = False
        for e in data:
            if not isinstance(e, dict):
                continue
            eid = e.get("id")
            if eid not in cand_by_id:
                continue
            if e.get("answer_class"):  # IDEMPOTENTE: já classificado, pula
                n_puladas += 1
                continue
            c = cand_by_id[eid]
            e["answer_class"] = "not_found"
            mudou = True
            n_gravadas += 1
            log_audit(audit_path, {
                "id": eid, "camada": scope, "arquivo": fname,
                "negacao_textual": c["negacao_textual"],
                "kw_continuidade": c["kw_continuidade"],
                "regra": "R4-A-backfill",
                "override_estrato_b": c["override_estrato_b"],
                "timestamp": time.time(),
            })

        if mudou:
            try:
                _atomic_write_json(p, data)
            except Exception as e:
                print(f"[ERRO] escrita falhou em {p}: {e}")
                n_erros += 1

    print(f"\n[RESULTADO] gravadas={n_gravadas} puladas(já classificadas)={n_puladas} erros={n_erros}")
    print(f"[BACKUP] {backup}")
    print("\n[AVISO OBRIGATORIO] índice híbrido em memória NÃO foi invalidado por esta "
          "escrita offline — REINICIE O SERVIDOR antes de qualquer teste pós-backfill.")


if __name__ == "__main__":
    main()
