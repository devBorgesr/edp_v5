"""
edp.lab.isolation — Isolamento experimental da Bancada (Bug 3 da varredura).

O PROBLEMA (achado no espelho): o _active_scope do MemoryStore e um campo MUTAVEL
COMPARTILHADO num singleton por-sessao, lido ASSINCRONAMENTE pelos jobs de
background (auto_consolidation, cognitive_decisions, health). Trocar o scope da
sessao de PRODUCAO ("default") para rodar experimento e uma CORRIDA: um job
disparando no meio, ou o flip-back com uma amostra ainda voando, pode jogar
conteudo experimental na memoria cognitiva. Isso contamina o sujeito e invalida
o prontuario — exatamente o aceite critico da Fase 2.

A SOLUCAO (por construcao, nao por corrida): rodar todo experimento numa SESSAO
DEDICADA, "__lab__<uuid>", via o registry. Confirmado na varredura:
  - os jobs de background sao presos a session_id="default" (make_*_job recebe
    session_id no registro) -> a sessao de lab nasce LIVRE deles.
  - cada sessao+scope persiste em $EDP_BASE_DIR/sessions/<session>_<scope>/
    (config.MEMORY_DIR) -> a sessao de lab e FISICAMENTE separada da producao;
    nada que ela escreve cai em sessions/default_cognitive/.
  - a sessao de lab e DESCARTAVEL: ao sair, e purgada (registry + disco), o que
    tambem evita acumulo de orfaos em tempo indeterminado.

INV-1 (isolamento) e INV-5 (producao intocada) moram aqui.

SEGURANCA: o purge so opera em sessoes com prefixo "__lab__". Tentar purgar
producao LEVANTA excecao. A delecao em disco confere, por caminho, que cada
diretorio contem o id de lab antes de remover — apagar producao por engano e
impossivel por construcao.

NAO-GATE: criar uma sessao descartavel nao toca producao, entao isolation nao e
travado por EDP_LAB_ARMED. O gate de "armado" vive no runner/interceptor (onde o
experimento de fato manda ao modelo / modifica a janela).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger("edp.lab.isolation")

# Prefixo que marca uma sessao como de laboratorio. Nenhuma sessao de producao
# ("default" e afins) usa este prefixo — e o que torna o purge seguro.
LAB_PREFIX = "__lab__"


def new_lab_session_id() -> str:
    """Gera um session_id de lab unico: '__lab__<12hex>'."""
    return f"{LAB_PREFIX}{uuid.uuid4().hex[:12]}"


def is_lab_session(session_id: str) -> bool:
    """True se o session_id e de laboratorio (prefixo __lab__)."""
    return isinstance(session_id, str) and session_id.startswith(LAB_PREFIX)


def _sessions_root() -> Path:
    """Raiz das sessoes em disco. Espelha config.MEMORY_DIR = EDP_BASE_DIR /
    'sessions' SEM importar config (mantem isolation leve e desacoplado)."""
    base = os.environ.get("EDP_BASE_DIR", "data")
    return Path(base) / "sessions"


def _lab_scope_dirs(session_id: str) -> list:
    """Diretorios em disco de uma sessao de lab: sessions/<lab>_<scope>/.
    Glob por '<lab>_*'. Como o id de lab carrega um uuid, este glob jamais
    casa com um diretorio de producao."""
    if not is_lab_session(session_id):
        return []
    root = _sessions_root()
    if not root.exists():
        return []
    return [p for p in root.glob(f"{session_id}_*") if p.is_dir()]


@dataclass
class LabSession:
    """Handle de uma sessao de lab isolada. O runner usa session_id para pegar
    seu proprio runtime (get_runtime) e rodar turnos; memory para inspecionar."""
    session_id: str
    scope: str = "sprint"
    memory: object = None


def purge_lab_session(session_id: str) -> dict:
    """Apaga a sessao de lab: registry (memoria) + disco. DESCARTAVEL.

    GUARDA DURA: so opera em sessoes __lab__. Purgar producao LEVANTA excecao —
    nunca se apaga producao por engano. A delecao em disco RE-confere, por
    caminho, que o nome do diretorio contem o id de lab antes de remover.
    """
    if not is_lab_session(session_id):
        raise ValueError(
            f"purge_lab_session RECUSADO: '{session_id}' nao tem prefixo "
            f"'{LAB_PREFIX}'. Producao NUNCA e purgada por esta funcao."
        )
    removed_dirs = []
    # 1) registry (em memoria) — best-effort; remove _runtimes e _memories.
    try:
        from ..runtime.registry import reset_session
        reset_session(session_id)
    except Exception as e:
        logger.debug("[isolation] reset_session(%s) falhou: %s", session_id, e)
    # 2) disco — cirurgico, com dupla guarda por caminho.
    for d in _lab_scope_dirs(session_id):
        try:
            if LAB_PREFIX not in d.name or session_id not in d.name:
                logger.warning("[isolation] dir suspeito IGNORADO no purge: %s", d)
                continue
            shutil.rmtree(d)
            removed_dirs.append(str(d))
        except Exception as e:
            logger.warning("[isolation] rmtree(%s) falhou: %s", d, e)
    logger.info(
        "[isolation] purga | sessao=%s | dirs_removidos=%d",
        session_id, len(removed_dirs),
    )
    return {"session_id": session_id, "removed_dirs": removed_dirs}


@contextmanager
def experimental_session(
    scope: str = "sprint",
    purge: bool = True,
) -> Iterator[LabSession]:
    """Context manager: entrega uma sessao de lab DEDICADA e ISOLADA.

    Isolamento por construcao (Bug 3): a sessao __lab__<uuid> e separada da
    producao no registry E no disco, e LIVRE dos jobs de background (presos a
    'default'). Escreve no scope 'sprint' da sessao de lab por padrao (defesa em
    profundidade: view descartavel de uma sessao descartavel).

    Ao sair, a sessao e purgada (descartavel) — a menos que purge=False, para
    inspecao pos-morte de um experimento. A purga na saida tambem impede acumulo
    de orfaos em disco ao longo de tempo indeterminado.

    Uso (futuro Repetidor):
        with experimental_session() as lab:
            rt = get_runtime(lab.session_id)   # runtime isolado
            ... roda N turnos, grava no prontuario ...
        # aqui a sessao ja foi purgada; producao intocada.
    """
    sid = new_lab_session_id()
    mem = None
    try:
        from ..runtime.registry import get_memory
        mem = get_memory(sid)
        try:
            mem.set_scope(scope)
        except Exception as e:
            logger.debug("[isolation] set_scope(%s) falhou: %s", scope, e)
        logger.info("[isolation] sessao de lab ABERTA: %s | scope=%s", sid, scope)
        yield LabSession(session_id=sid, scope=scope, memory=mem)
    finally:
        if purge:
            try:
                purge_lab_session(sid)
            except Exception as e:
                logger.warning("[isolation] purga de %s falhou: %s", sid, e)
        else:
            logger.info("[isolation] sessao de lab MANTIDA (purge=False): %s", sid)


# ── Verificacao do isolamento (para o aceite PARANOICO da Fase 2) ─────────────
# Baseada em DISCO (a verdade do EDP), nao na API em memoria: le os arquivos do
# scope cognitive da producao e tira um fingerprint. Antes == depois prova que o
# experimento NAO tocou a memoria cognitiva. O hash e a garantia forte (qualquer
# alteracao do arquivo muda o hash); as contagens sao informativas.

def cognitive_fingerprint(prod_session: str = "default") -> dict:
    """Fingerprint do exocortex COGNITIVE da producao (em disco)."""
    root = _sessions_root() / f"{prod_session}_cognitive"
    out = {"session": prod_session, "episodic_n": 0, "semantic_n": 0, "hash": "0" * 16}
    try:
        blobs = []
        for name, key in (("episodic.json", "episodic_n"), ("semantic.json", "semantic_n")):
            p = root / name
            if p.exists():
                raw = p.read_text(encoding="utf-8")
                blobs.append(raw)
                try:
                    data = json.loads(raw)
                    if isinstance(data, list):
                        out[key] = len(data)
                    elif isinstance(data, dict):
                        out[key] = len(data.get("entries", data))
                except Exception:
                    pass
        out["hash"] = hashlib.sha256("".join(blobs).encode("utf-8")).hexdigest()[:16]
    except Exception as e:
        logger.debug("[isolation] fingerprint falhou: %s", e)
    return out


def verify_no_leak(before: dict, after: dict) -> bool:
    """True se a memoria cognitiva da producao esta INALTERADA (sem vazamento).
    Comparacao por hash (forte) + contagens (informativas)."""
    return (
        before.get("hash") == after.get("hash")
        and before.get("episodic_n") == after.get("episodic_n")
        and before.get("semantic_n") == after.get("semantic_n")
    )


def production_contains(sentinel: str, prod_session: str = "default") -> bool:
    """True se o texto sentinela aparece nos arquivos do scope cognitive da
    producao. Usado para provar que conteudo experimental NAO vazou para la."""
    root = _sessions_root() / f"{prod_session}_cognitive"
    try:
        for name in ("episodic.json", "semantic.json"):
            p = root / name
            if p.exists() and sentinel in p.read_text(encoding="utf-8"):
                return True
    except Exception as e:
        logger.debug("[isolation] production_contains falhou: %s", e)
    return False
