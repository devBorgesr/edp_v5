"""
edp.write_provenance — exp012: carimbo + política na gravação (Fase 1 §2).

Regra B CONGELADA na Fase 3 (matriz fase 2, N=97 pós-dedup, avaliador_matriz.py):
R4 (negacao_textual OR kw_continuidade) venceu R1/R2/R3 em F1 (0.78, ZERO FP em
LEGITIMO_META). PR-5 confirmada: no quadrante neg=F∧kw=T, confabulação e
continuação legítima são inseparáveis por texto isolado — a proveniência
(n_mem_prompt) resolve. Duas regras por estrato (RELATORIO_ETAPA0_EXP012V2.md):
  (A) backlog / carimbo sem n_mem_prompt exato → R4 puro.
  (B) carimbo com n_mem_prompt exato            → R4 AND n_mem_prompt==0.
KEYWORDS E REGEX V1 CONGELADAS — copiadas verbatim de extract_ground_truth.py
(fonte de verdade; sincronizar manualmente se a extração mudar).
`eh_recusa_alta`/`classify_v1_refutada` mantidas só para exp012_calibracao.py
(histórico da regra v1, REFUTADA — ver ESTADO_EXP012.md).
"""
from __future__ import annotations
import json
import logging
import os
import re
import time
from pathlib import Path
logger = logging.getLogger("edp.write_provenance")
FIELD_PROV = "ctx_provenance"
FIELD_CLASS = "answer_class"

# ── R1/R2 congeladas (extract_ground_truth.py) ─────────────────────────────
KW = ["continuar", "continuando", "o que discutimos", "o que conversamos",
      "me lembra", "lembra", "voltando ao", "retomar", "retomando",
      "onde estávamos", "o que você me explicou", "última conversa", "falamos"]
NEG = re.compile(r"n[ãa]o (encontro|tenho registro|h[áa] registro|localizo)", re.I)


def negacao_textual(resposta: str) -> bool:
    return bool(NEG.search(resposta or ""))


def kw_continuidade(query: str) -> bool:
    q = (query or "").lower()
    return any(k in q for k in KW)


def eh_recusa_alta(texto: str) -> bool:
    try:
        from .echo_chamber import detectar_auto_sinal_de_limite
        return detectar_auto_sinal_de_limite(texto or "").get("confianca") == "alta"
    except Exception:
        return False


def classify_v1_refutada(prov: dict | None, resposta: str) -> str | None:
    """Regra v1 (n_mem_prompt==0 AND recusa-alta) — REFUTADA na matriz fase 2
    (recall baixo, ESTADO_EXP012.md). Mantida intocada só p/ exp012_calibracao.py
    (registro histórico); NÃO usada no write-path."""
    if not isinstance(prov, dict) or prov.get("n_mem_prompt") is None:
        return None
    if prov["n_mem_prompt"] == 0 and eh_recusa_alta(resposta):
        return "not_found"
    return None


_warned_ctx_slots_off = False  # anti-spam: 1 warning/processo, não por chamada


def classify(prov: dict | None, query: str, resposta: str) -> str | None:
    """Camada B — regra composta CONGELADA (Fase 3). None = não quarentena
    (default defensivo).
    Guarda de defesa (achado pós-Fase-3): com EDP_CTX_SLOTS efetivamente OFF,
    n_mem_prompt existe mas MENTE (Defeito 1 — CP3 morre, tende a 0 sempre).
    Nesse caso o valor é DESCARTADO (vira None) — NUNCA cai no estrato B com
    sinal falso; cai no estrato A (R4 puro), o mesmo regime validado no
    backlog pela matriz."""
    if not isinstance(prov, dict):
        return None
    sinal = negacao_textual(resposta) or kw_continuidade(query)
    if not sinal:
        return None
    n_mem = prov.get("n_mem_prompt")
    from .config import EDP_CTX_SLOTS
    if n_mem is not None and not EDP_CTX_SLOTS:
        global _warned_ctx_slots_off
        if not _warned_ctx_slots_off:
            logger.warning(
                "[exp012] EDP_WRITE_PROVENANCE ativo sem EDP_CTX_SLOTS — "
                "n_mem_prompt não confiável; usando estrato A (R4 puro)"
            )
            _warned_ctx_slots_off = True
        n_mem = None
    if n_mem is None:
        return "not_found"                       # (A) backlog — R4 puro
    return "not_found" if n_mem == 0 else None    # (B) proveniência exata


def _explain(prov: dict, query: str, resposta: str) -> dict:
    """Features + qual estrato/regra disparou — só para o log de auditoria
    (recomputo barato: regex + scan de lista, não vale cachear)."""
    neg = negacao_textual(resposta)
    kw = kw_continuidade(query)
    n_mem = (prov or {}).get("n_mem_prompt")
    regra = "R4-A(backlog, sem n_mem_prompt)" if n_mem is None else "R4-B(n_mem_prompt==0)"
    return {"negacao_textual": neg, "kw_continuidade": kw, "n_mem_prompt": n_mem, "regra": regra}


def _audit_path(session_id: str) -> Path:
    """Mesmo padrão de runtime/lineage.py: $EDP_BASE_DIR/sessions/<sid>_cognitive/."""
    base = Path(os.environ.get("EDP_BASE_DIR", "C:/edp_data"))
    return base / "sessions" / f"{session_id}_cognitive" / "quarantine_audit.jsonl"


def _log_quarantine(session_id: str, entry_id, prov: dict, query: str, resposta: str) -> None:
    """Auditoria append-only da quarentena — reversível (não deleta nada),
    nunca levanta exceção (observabilidade não pode derrubar o turno, mesmo
    padrão de lineage.persist)."""
    try:
        record = {
            "id": entry_id,
            "answer_class": "not_found",
            "timestamp": time.time(),
            **_explain(prov, query, resposta),
        }
        path = _audit_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("[exp012] audit log de quarentena falhou: %s", e)


def stamp_and_classify(memory, entry: dict, prov: dict, query: str, resposta: str) -> None:
    """Camada A sempre (sob a flag); B só quando a regra dispara. Persiste no
    entry da lista episódica (padrão session_summary.py:251-261)."""
    cls = classify(prov, query, resposta)
    eid = entry.get("id")
    def _apply(e):
        e[FIELD_PROV] = dict(prov)
        if cls:
            e[FIELD_CLASS] = cls
    _apply(entry)
    try:
        for e in memory.episodic.entries:
            if e.get("id") == eid:
                _apply(e)
                break
        memory.episodic._dirty = True
        memory.episodic.save()
    except Exception as e:
        logger.debug("[exp012] persistencia do carimbo falhou: %s", e)
    if cls:
        logger.info("[exp012] answer_class=%s id=%s prov=%s", cls, str(eid)[:8], prov)
        _log_quarantine(getattr(memory, "session_id", "default"), eid, prov, query, resposta)
