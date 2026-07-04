"""
Força extração de cognitive_decisions para as memórias antigas (>24h)
que o extractor normal ignora por filtro de idade.

Custo estimado: ~$0.001 por entry × 90 entries = ~$0.09
Roda com o servidor EDP PARADO.

Uso:
    $env:EDP_BASE_DIR = "C:\edp_data"
    python force_extract_old.py
"""
import asyncio
import json
import logging
import os
import pathlib
import sys
import copy

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("force_extract")

BASE_DIR = pathlib.Path(os.environ.get("EDP_BASE_DIR", r"C:\edp_data"))
EPISODIC_PATH = BASE_DIR / "sessions" / "default_cognitive" / "episodic.json"

# Mesmas constantes do extractor
MAX_PROMPT_TEXT_LEN = 3000
EXTRACTION_TIMEOUT_SEC = 15.0

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


def load_entries():
    data = json.loads(EPISODIC_PATH.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data, data.get("entries", [])
    return None, data


def save_entries(data_root, entries):
    if data_root is not None:
        data_root["entries"] = entries
        out = data_root
    else:
        out = entries
    EPISODIC_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def parse_cd(raw: str, model_used: str) -> dict | None:
    import re
    if not raw:
        return None
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()
    try:
        data = json.loads(cleaned)
    except Exception as e:
        logger.warning(f"  JSON inválido: {e} | raw={cleaned[:100]!r}")
        return None
    ka = data.get("key_assertion", "")
    cs = data.get("concepts", [])
    dm = data.get("domain", "")
    if not isinstance(ka, str) or not ka.strip():
        return None
    if not isinstance(cs, list) or not cs:
        return None
    if not isinstance(dm, str) or not dm.strip():
        return None
    from edp.clock import now as _now
    return {
        "key_assertion": ka.strip()[:80],
        "concepts": [c.strip()[:50] for c in cs if isinstance(c, str) and c.strip()][:5],
        "domain": dm.strip()[:50],
        "extracted_at": _now(),
        "model_used": model_used,
    }


async def extract_one(client, cfg, entry: dict) -> dict | None:
    text = (entry.get("text") or "")[:MAX_PROMPT_TEXT_LEN]
    if not text:
        return None
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(client.complete, text, EXTRACT_PROMPT_SYSTEM),
            timeout=EXTRACTION_TIMEOUT_SEC,
        )
        model_used = getattr(cfg, "model", "unknown")
        return parse_cd(response, model_used)
    except asyncio.TimeoutError:
        logger.warning(f"  TIMEOUT entry id={entry.get('id')}")
        return None
    except Exception as e:
        logger.warning(f"  ERRO entry id={entry.get('id')}: {e}")
        return None


async def main():
    # Verifica que o arquivo existe
    if not EPISODIC_PATH.exists():
        logger.error(f"ERRO: {EPISODIC_PATH} não encontrado.")
        logger.error(f"Verifique EDP_BASE_DIR={BASE_DIR}")
        sys.exit(1)

    data_root, entries = load_entries()
    total = len(entries)

    # Seleciona pendentes (sem cognitive_decisions, layer episodic, llm_response)
    pending = [
        e for e in entries
        if isinstance(e, dict)
        and e.get("layer") == "episodic"
        and e.get("source_type") == "llm_response"
        and not isinstance(e.get("cognitive_decisions"), dict)
        and (e.get("text") or "").strip()
    ]

    logger.info(f"Total entries: {total}")
    logger.info(f"Pendentes para extração: {len(pending)}")
    logger.info(f"Já com cognitive_decisions: {total - len(pending)}")
    logger.info(f"Caminho: {EPISODIC_PATH}")
    logger.info("")

    if not pending:
        logger.info("Nada a fazer — todas as entries elegíveis já têm cognitive_decisions.")
        return

    # Inicializa o runtime EDP para pegar o client LLM
    logger.info("Inicializando runtime EDP (para pegar o LLM client)...")
    try:
        # Adiciona o diretório do projeto ao path
        project_root = pathlib.Path(__file__).parent
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))

        from edp.llm_adapter import EDPRuntime

        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            logger.error("ERRO: ANTHROPIC_API_KEY não definida.")
            logger.error("  $env:ANTHROPIC_API_KEY = 'sk-ant-...'")
            sys.exit(1)

        runtime = EDPRuntime(session_id="default")
        ok = runtime.connect_anthropic(
            api_key=api_key,
            model="claude-haiku-4-5",
        )
        if not ok:
            logger.error("ERRO: connect_anthropic retornou False.")
            sys.exit(1)

        client = getattr(runtime, "_client", None)
        cfg = getattr(runtime, "_llm_config", None)

        if client is None:
            logger.error("ERRO: LLM client não disponível após connect_anthropic().")
            sys.exit(1)

        logger.info(f"LLM client OK | model={getattr(cfg, 'model', '?')}")
        logger.info("")
    except Exception as e:
        logger.error(f"ERRO inicializando runtime: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Extrai em lote com save incremental a cada 5
    extracted = 0
    failed = 0
    skipped = 0

    # Mapeia id -> índice para atualizar as entries
    id_to_idx = {str(e.get("id")): i for i, e in enumerate(entries)}

    for i, entry in enumerate(pending):
        eid = entry.get("id", "?")
        text_preview = (entry.get("text") or "")[:60].replace("\n", " ")
        logger.info(f"[{i+1}/{len(pending)}] id={eid} | {text_preview}...")

        cd = await extract_one(client, cfg, entry)

        if cd is None:
            failed += 1
            logger.info(f"  → FALHOU")
            continue

        # Atualiza a entry no array principal
        idx = id_to_idx.get(str(eid))
        if idx is not None:
            entries[idx]["cognitive_decisions"] = cd
            extracted += 1
            logger.info(f"  → OK | domain={cd['domain']} | concepts={cd['concepts'][:3]}")
        else:
            skipped += 1
            logger.warning(f"  → id não encontrado no array principal")

        # Salva a cada 5 extrações (checkpoint incremental)
        if extracted % 5 == 0:
            save_entries(data_root, entries)
            logger.info(f"  [checkpoint] {extracted} extraídas salvas em disco")

    # Salva final
    save_entries(data_root, entries)

    com_cd = sum(1 for e in entries if isinstance(e.get("cognitive_decisions"), dict))
    logger.info("")
    logger.info("=" * 60)
    logger.info(f"CONCLUÍDO")
    logger.info(f"  extraídas   : {extracted}")
    logger.info(f"  falhas      : {failed}")
    logger.info(f"  puladas     : {skipped}")
    logger.info(f"  total com cognitive_decisions agora: {com_cd}/{total}")
    logger.info(f"  arquivo salvo: {EPISODIC_PATH}")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
