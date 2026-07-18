"""
edp.memory.atomic_io — serialização + write atômico + load tolerante.

Fase 4 T3: extraído verbatim de memory.py:101-249 (MOVE-ONLY, corpos de
função byte-idênticos ao original — só esta docstring e os imports são
novos). Carrega a Dívida técnica #8 (write atômico com lock+retry, ver
docstring de _atomic_write_json abaixo) — não mexido nesta extração.
"""
import json
import os
import threading
import time
from pathlib import Path

import numpy as np

def _serialize(entries: list[dict]) -> list[dict]:
    out = []
    for e in entries:
        c = dict(e)
        if isinstance(c.get("embedding"), np.ndarray):
            c["embedding"] = c["embedding"].tolist()
        out.append(c)
    return out

def _deserialize(entries: list[dict]) -> list[dict]:
    for e in entries:
        if "embedding" in e and isinstance(e["embedding"], list):
            e["embedding"] = np.array(e["embedding"], dtype=np.float32)
    return entries


# ── Peça 0.3.1: Write atômico e load tolerante ────────────────────────────────

# Dívida técnica #8: PermissionError [WinError 32] em writes concorrentes no Windows.
# `os.replace` falha se destino estiver aberto por outro thread (ex: dashboard lendo).
# Defesas em camadas:
#   1. Lock global por path (serializa saves do mesmo arquivo)
#   2. Retry com backoff exponencial em PermissionError/OSError
#   3. Limpeza de .tmp órfão pré-existente

_WRITE_LOCKS: dict = {}
_WRITE_LOCKS_GUARD = threading.Lock()

def _get_write_lock(path):
    """Lock por path (chave = str do path absoluto). Cria lazy."""
    key = str(Path(path).resolve())
    with _WRITE_LOCKS_GUARD:
        lk = _WRITE_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _WRITE_LOCKS[key] = lk
        return lk


def _atomic_write_json(path, data, *, indent: int = 2) -> None:
    """
    Grava JSON de forma atômica: tmp → fsync → rename.

    Se processo for interrompido no meio da gravação, o arquivo original
    fica intacto (apenas o .tmp pode estar parcial). Próximo boot lê o
    original sem corrupção.

    Robusto em POSIX e Windows. Em Windows, `os.replace` pode falhar com
    PermissionError [WinError 32] se o destino estiver aberto por outro
    thread (ex: dashboard lendo) ou processo (ex: antivírus escaneando).
    Defesas: lock por path + retry com backoff.

    Args:
        path: caminho do arquivo final
        data: dado serializável em JSON
        indent: indentação do JSON
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")

    # Serializa saves do mesmo arquivo (evita .tmp órfão por concorrência interna)
    with _get_write_lock(path):
        # Limpa .tmp órfão de save anterior que morreu (se houver)
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

        # 1. Escreve no .tmp
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            try:
                # 2. Força sincronização com disco (não só buffer do OS)
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                # Alguns FS não suportam fsync; segue mesmo assim
                pass

        # 3. Replace atômico com retry — Windows pode falhar transientemente
        # quando destino está aberto por outro processo (antivírus, dashboard, etc).
        # Backoff: 50ms, 100ms, 200ms, 400ms, 800ms (total ~1.5s antes de desistir)
        backoffs = (0.05, 0.10, 0.20, 0.40, 0.80)
        last_err = None
        for delay in (0.0,) + backoffs:
            if delay > 0:
                time.sleep(delay)
            try:
                os.replace(tmp, path)
                return
            except (PermissionError, OSError) as e:
                last_err = e
                continue
        # Após todas as tentativas, levanta para o caller decidir
        # (em threads de save automático, é capturado e logado pelo runtime)
        raise last_err


def _safe_load_json(path):
    """
    Carrega JSON tolerando corrupção por write parcial.

    Estratégia:
      1. Tenta json.load() normal
      2. Se falhar com 'Extra data' (dados depois do JSON válido):
         tenta recuperar a parte válida procurando último ']' fechado
      3. Se ainda falhar, retorna None (caller decide se inicia vazio)

    NÃO modifica o arquivo automaticamente; apenas retorna o conteúdo
    recuperado para o caller decidir o que fazer.

    Returns:
        dados (list/dict) ou None se irrecuperável
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        # Tenta recuperar JSON válido cortando lixo depois do último ']' ou '}'
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            # Busca último fecha-array/fecha-objeto que parsing bem
            for cut in range(len(content), 0, -1):
                if content[cut-1] in "]}":
                    candidate = content[:cut]
                    try:
                        data = json.loads(candidate)
                        # Conseguiu recuperar
                        import logging
                        logger = logging.getLogger("edp.memory")
                        logger.warning(
                            "[memory] JSON corrompido em %s recuperado por truncamento "
                            "(perdeu %d bytes de lixo: %r)",
                            path, len(content) - cut, content[cut:cut+50]
                        )
                        return data
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass

        # Não recuperou — re-raise o erro original
        raise
    except FileNotFoundError:
        return None
