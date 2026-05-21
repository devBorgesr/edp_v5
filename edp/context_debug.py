"""
edp.context_debug — Logger do contexto exato enviado ao LLM.

Para investigar bugs onde o LLM age como se não tivesse contexto.
Captura exatamente quais memórias entraram no prompt, em que ordem,
e quanto texto cada uma traz.

Arquivo: $EDP_BASE_DIR/debug_context.log
Rotação: ao passar 5MB, renomeia pra .old e começa novo.

Para desligar: apague ou renomeie o arquivo, ou comente a chamada
em llm_adapter._retrieve_context.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional

from .config import BASE_DIR

logger = logging.getLogger("edp.context_debug")

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
DEBUG_LOG_PATH = BASE_DIR / "debug_context.log"

_lock = threading.Lock()
_turn_counter = 0


def _rotate_if_needed() -> None:
    """Renomeia para .old se passar do limite."""
    try:
        if DEBUG_LOG_PATH.exists() and DEBUG_LOG_PATH.stat().st_size > MAX_FILE_SIZE:
            old_path = DEBUG_LOG_PATH.with_suffix(".log.old")
            if old_path.exists():
                old_path.unlink()
            DEBUG_LOG_PATH.rename(old_path)
    except OSError as e:
        logger.debug("[context_debug] rotação falhou: %s", e)


def log_context(
    session_id: str,
    user_message: str,
    immediate_window: list[dict],
    similarity_results: list[dict],
    final_blocks: list[str],
) -> None:
    """
    Loga o contexto completo de UM turno.

    Args:
        session_id: ID da sessão
        user_message: pergunta original do usuário
        immediate_window: lista de dicts com {id, text, label} dos turnos da
                          janela imediata (até 2, mais novo por último)
        similarity_results: lista de dicts com {id, score, text, source_type}
                            do retrieval por similaridade
        final_blocks: lista de strings finais (com prefixos tipo "[turno anterior] ...")
                      que de fato vão pro LLM
    """
    global _turn_counter

    with _lock:
        _turn_counter += 1
        turn = _turn_counter

        try:
            _rotate_if_needed()

            with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write("\n" + "=" * 78 + "\n")
                f.write(f"[turno #{turn}] {ts} | session={session_id}\n")
                f.write("=" * 78 + "\n")

                # User message
                f.write(f"\nUSER MESSAGE ({len(user_message)} chars):\n")
                f.write(f"  {user_message!r}\n")

                # Janela imediata
                f.write(f"\nJANELA IMEDIATA ({len(immediate_window)} turnos):\n")
                if not immediate_window:
                    f.write("  (vazia)\n")
                else:
                    for item in immediate_window:
                        eid = item.get("id", "?")[:8]
                        label = item.get("label", "?")
                        text = item.get("text") or ""
                        source_type = item.get("source_type", "?")
                        f.write(f"  [{label}] id={eid}... source_type={source_type}\n")
                        f.write(f"    text ({len(text)} chars): {text[:600]!r}\n")

                # Retrieval por similaridade
                f.write(f"\nRETRIEVAL POR SIMILARIDADE ({len(similarity_results)} memórias):\n")
                if not similarity_results:
                    f.write("  (nenhuma)\n")
                else:
                    for r in similarity_results:
                        eid = (r.get("id") or "?")[:8]
                        score = r.get("ranking_score", r.get("score", 0))
                        text = (r.get("text") or "")
                        source_type = r.get("source_type", "?")
                        status = r.get("epistemic_status", "?")
                        f.write(f"  [score={score:.3f}] id={eid}... source_type={source_type} status={status}\n")
                        f.write(f"    text ({len(text)} chars): {text[:200]!r}\n")

                # Blocos finais (o que de fato vai pro LLM)
                total_chars = sum(len(b) for b in final_blocks)
                f.write(f"\nBLOCOS FINAIS ENVIADOS AO LLM ({len(final_blocks)} blocos, "
                        f"{total_chars} chars total):\n")
                if not final_blocks:
                    f.write("  (nenhum bloco)\n")
                else:
                    for i, block in enumerate(final_blocks, 1):
                        f.write(f"  [{i}] ({len(block)} chars): {block[:600]!r}\n")

                f.write("\n")  # linha em branco no final

        except OSError as e:
            # NUNCA quebra retrieval por falha de debug
            logger.debug("[context_debug] falha ao gravar: %s", e)
