"""
edp.write_provenance — exp012: carimbo + política na gravação (Fase 1 §2).
Regra B NÃO CONGELADA (calibração pendente, exp012_calibracao.py):
not_found ⟺ prov.n_mem_prompt==0 E recusa-alta na resposta (auxiliar textual —
sozinho vaza; sozinho o n_mem==0 pisaria conteúdo legítimo tipo supernova).
"""
from __future__ import annotations
import logging
logger = logging.getLogger("edp.write_provenance")
FIELD_PROV = "ctx_provenance"
FIELD_CLASS = "answer_class"


def eh_recusa_alta(texto: str) -> bool:
    try:
        from .echo_chamber import detectar_auto_sinal_de_limite
        return detectar_auto_sinal_de_limite(texto or "").get("confianca") == "alta"
    except Exception:
        return False


def classify(prov: dict | None, resposta: str) -> str | None:
    """Regra B (pré-freeze). None = sem classificação (default defensivo)."""
    if not isinstance(prov, dict) or prov.get("n_mem_prompt") is None:
        return None
    if prov["n_mem_prompt"] == 0 and eh_recusa_alta(resposta):
        return "not_found"
    return None


def stamp_and_classify(memory, entry: dict, prov: dict, resposta: str) -> None:
    """Camada A sempre (sob a flag); B só quando a regra dispara. Persiste no
    entry da lista episódica (padrão session_summary.py:251-261)."""
    cls = classify(prov, resposta)
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
