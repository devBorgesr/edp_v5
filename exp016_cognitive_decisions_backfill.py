#!/usr/bin/env python3
"""
exp016_cognitive_decisions_backfill.py — exp016 P6: passada offline para
popular `cognitive_decisions.key_assertion` nas candidatas DISQ que nunca
passaram pelo extractor de background (edp/runtime/cognitive_decisions.py).

*** NÃO AUTORIZADO A RODAR NESTA ETAPA. ***
Desenhado (P6, Etapa 0) para uso FUTURO, condicionado a autorização explícita
separada do pesquisador sobre o resultado do dry-run (exp016_dryrun.py) —
mesmo protocolo do exp012 fase4 (dry-run → revisão → apply autorizado).
Ao contrário dos scripts de dry-run do exp012/exp016, este ESCREVE e faz
chamadas de API pagas — não é leitura pura.

MOTIVAÇÃO (ver RELATORIO_ETAPA0_EXP016.md, P6):
  CognitiveDecisionsExtractor._select_pending_entries()
  (edp/runtime/cognitive_decisions.py:228-293) só varre:
    - layer == "episodic"  (semantic NUNCA é varrido)
    - scope cognitive (mem._cognitive_view — sprint NUNCA é varrido; sprint
      tem extração própria via comentário HTML embutido na resposta)
    - source_type == "llm_response"
    - timestamp entre [now-24h, now-60s] (janela fixa)
  Além disso o job suspende inteiro em pressure=WARNING+
  (suspend_on_pressure=True, linha 533). Hipótese de trabalho: as 2
  desqualificações de 13/07 00h52-56 caíram numa madrugada de
  pressure=CRITICAL e HOJE já estão fora da janela de 24h — key_assertion
  provavelmente nunca foi (e nunca mais vai ser, pelo caminho normal)
  extraído para elas. Este script é o caminho alternativo: offline,
  servidor parado, sem restrição de janela de 24h.

ESCOPO: só candidatas com scope=="cognitive" e arquivo=="episodic.json" (as
únicas onde o extractor normal algum dia teria atuado — popular
key_assertion em semantic/sprint criaria um campo que o pipeline normal
nunca produziria nessas camadas, fora do escopo desta análise).

SEGURANÇA (mesmo padrão de exp012_fase4_backfill_apply.py):
  1. Backup de sessions/ ANTES de qualquer escrita (aborta se falhar).
  2. IDEMPOTENTE: entry cujo cognitive_decisions já está setado é pulada
     (checado no dado recém-lido).
  3. Write atômico local (tmp no mesmo diretório + os.replace).
  4. Auditoria própria (cog_decisions_backfill_audit.jsonl).
  5. SÓ CÓPIAS — guard anti-produção.
  6. Reusa EXATAMENTE a seleção do exp016_dryrun.find_candidatas() — nenhuma
     regra nova, nenhum id hardcoded.

CUSTO: 1 chamada Haiku por candidata (~$0.001/extração, mesmo prompt/modelo
de CognitiveDecisionsExtractor). N candidatas cognitive/episodic sem
key_assertion → custo ≈ N × $0.001 (ver relatório para estimativa).

USO (servidor parado; aponte para CÓPIA — nunca produção; requer
ANTHROPIC_API_KEY e autorização do pesquisador):
  $env:EDP_BASE_DIR="C:\\edp_data_exp016"
  $env:ANTHROPIC_API_KEY="..."
  python exp016_cognitive_decisions_backfill.py [session_id]
"""
import json, os, re, shutil, sys, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp016_dryrun import find_candidatas
# Nota: NÃO importa edp.memory nem edp.runtime.cognitive_decisions — evita
# puxar a stack pesada do runtime (sklearn/torch) e qualquer dependência de
# instância viva de MemoryStore/BackgroundJob, inadequado para script offline
# standalone (mesmo princípio de exp012_fase4_backfill_apply.py).
# LLMClient real fica em edp.llm_adapter, que só usa stdlib (urllib) em
# import — seguro para reuso aqui.
from edp.llm_adapter import LLMClient, LLMConfig, LLMProvider

# Prompt IDÊNTICO a edp/runtime/cognitive_decisions.py:77-93
# (EXTRACT_PROMPT_SYSTEM) — copiado, não importado, pelo motivo acima.
# Qualquer mudança no original deve ser replicada aqui manualmente.
EXTRACT_PROMPT_SYSTEM = """Você é um extrator de decisões estruturadas.

Receba uma resposta técnica (formato "Q: ...\\nA: ...") e retorne JSON COM
EXATAMENTE estes 3 campos:
  - "key_assertion": afirmação central da resposta em <= 80 chars
  - "concepts": lista de 1 a 5 conceitos técnicos mencionados (strings curtas)
  - "domain": área técnica primária em 1-3 palavras (ex: "redis cache",
              "java concurrency", "react hooks")

REGRAS RÍGIDAS:
  - Responda APENAS o JSON, sem texto antes/depois, sem markdown fence
  - Campos OBRIGATÓRIOS, na ordem indicada
  - JSON deve ser parseável pelo Python json.loads()
  - Se a resposta for muito vaga, use valores genéricos mas válidos

EXEMPLO de resposta esperada:
{"key_assertion":"Redis é melhor que Memcached para sessões web","concepts":["redis","memcached","cache","ttl","persistência"],"domain":"web caching"}"""

MAX_PROMPT_TEXT_LEN = 3000  # mesmo limite de cognitive_decisions.py:73


def die(msg):
    print(f"[ERRO] {msg}")
    sys.exit(2)


def _parse_decisions(raw: str) -> dict | None:
    """Parse mínimo, mesma validação de CognitiveDecisions.from_json_str
    (edp/runtime/cognitive_decisions.py:120-182), reimplementada standalone."""
    if not raw or not isinstance(raw, str):
        return None
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    ka, cs, dm = data.get("key_assertion"), data.get("concepts"), data.get("domain")
    if not isinstance(ka, str) or not ka.strip():
        return None
    if not isinstance(cs, list) or not isinstance(dm, str) or not dm.strip():
        return None
    concepts_clean = [c.strip()[:50] for c in cs if isinstance(c, str) and c.strip()][:5]
    if not concepts_clean:
        return None
    return {
        "key_assertion": ka.strip()[:80],
        "concepts": concepts_clean,
        "domain": dm.strip()[:50],
        "extracted_at": time.time(),
        "model_used": "claude-haiku-4-5",
        "source": "exp016_backfill_offline",  # distingue do extractor normal em audit
    }


def _backup_sessions(base: str) -> str:
    src = os.path.join(base, "sessions")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(base, f"sessions_backup_exp016_{stamp}")
    if not os.path.isdir(src):
        die(f"sessions/ não encontrado em {base}")
    shutil.copytree(src, dst)
    return dst


def _atomic_write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main():
    base = os.environ.get("EDP_BASE_DIR") or die("EDP_BASE_DIR não setado")
    if os.path.basename(base.rstrip("/\\")).lower() == "edp_data":
        die("aponte para CÓPIA, não produção")
    api_key = os.environ.get("ANTHROPIC_API_KEY") or die("ANTHROPIC_API_KEY não setado")
    sid = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EDP_SESSION_ID", "default")

    candidatas, vistas = find_candidatas(base, sid)
    alvo = [
        c for c in candidatas
        if c["scope"] == "cognitive" and c["arquivo"] == "episodic.json"
        and not c["ja_tem_cognitive_decisions"]
    ]
    print(f"store={base} sid={sid} | entries vistas={vistas} | candidatas DISQ={len(candidatas)} "
          f"| alvo (cognitive/episodic, sem cognitive_decisions)={len(alvo)}")
    if not alvo:
        print("[NADA A FAZER] nenhuma candidata elegível.")
        return

    backup_path = _backup_sessions(base)
    print(f"[BACKUP] {backup_path}")

    client = LLMClient(LLMConfig(
        provider=LLMProvider.ANTHROPIC, model="claude-haiku-4-5", api_key=api_key,
    ))
    audit_path = os.path.join(base, "cog_decisions_backfill_audit.jsonl")
    n_ok = n_fail = 0
    for c in alvo:
        entries, _ = None, None
        # Releitura por arquivo (não do snapshot de find_candidatas) — mesmo
        # princípio de idempotência do backfill_apply.py: idempotência é
        # checada no dado recém-lido, não no snapshot do dry-run.
        with open(c["path"], encoding="utf-8") as f:
            data = json.load(f)
        pool = data.get("entries", data) if isinstance(data, dict) else data
        entry = next((e for e in pool if e.get("id") == c["id"]), None)
        if entry is None or entry.get("cognitive_decisions"):
            continue  # idempotente: já processado por outra passada
        text = (entry.get("text") or "")[:MAX_PROMPT_TEXT_LEN]
        try:
            raw = client.complete(text, EXTRACT_PROMPT_SYSTEM)
            cd = _parse_decisions(raw)
        except Exception as e:
            cd = None
            print(f"  [FALHA] {c['id']}: {type(e).__name__}: {e}")
        if cd is None:
            n_fail += 1
            with open(audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"id": c["id"], "ok": False, "ts": time.time()}) + "\n")
            continue
        entry["cognitive_decisions"] = cd
        _atomic_write_json(c["path"], data)
        n_ok += 1
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "id": c["id"], "ok": True, "ts": time.time(),
                "key_assertion": cd["key_assertion"],
            }) + "\n")
        print(f"  [OK] {c['id']} key_assertion={cd['key_assertion']!r}")

    print(f"\n[BACKFILL] {n_ok} gravadas, {n_fail} falharam. Backup em {backup_path}. "
          f"Auditoria em {audit_path}.")


if __name__ == "__main__":
    main()
