"""
tests/conftest.py — esqueleto pytest do hardening Fase 1.

Regras do escopo (adendo do pesquisador):
  - NUNCA gt_*.csv nem dados reais nas fixtures — tudo aqui é SINTÉTICO
    (texto/embeddings fabricados, sem PII, sem stores de produção).
  - markers `windows_only` e `live_store` ficam FORA do default run
    (ver pytest.ini `addopts`); precisam de `-m windows_only` explícito.
  - `frozen_clock` é só o ESQUELETO (item D do adendo): patcheia
    `edp.clock.now` diretamente. NÃO re-patcheia os `_now` já vinculados
    por import (`from .clock import now as _now`) em memory.py/pipeline.py/
    semantic_memory.py — essa injeção completa fica para fase posterior.
"""
from __future__ import annotations

import hashlib
import sys
import uuid
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import edp.config as edp_config

EMBED_DIM = edp_config.EMBED_DIM


# ── Embeddings determinísticos (sem sentence-transformers, sem rede) ──────────

def _fake_embed_one(text: str) -> np.ndarray:
    """Vetor determinístico por texto — mesmo texto => mesmo vetor,
    textos diferentes => vetores ~ortogonais (hash-seeded RNG)."""
    h = hashlib.sha256((text or "").encode("utf-8")).digest()
    seed = int.from_bytes(h[:4], "little")
    rng = np.random.RandomState(seed)
    v = rng.rand(EMBED_DIM).astype(np.float32)
    norm = np.linalg.norm(v)
    return v / norm if norm > 0 else v


def _fake_embed(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.empty((0, EMBED_DIM), dtype=np.float32)
    return np.vstack([_fake_embed_one(t) for t in texts])


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    """
    Autouse: substitui embed()/embed_one() em TODOS os módulos que os
    importaram por nome (`from .embeddings import embed_one`) — patchear só
    `edp.embeddings.embed_one` não bastaria, os consumidores já têm o nome
    vinculado no próprio namespace.

    Mantido leve de propósito (sem sklearn/torch/rede) para a suite rodar
    igual em qualquer máquina, inclusive sem sentence-transformers instalado.
    """
    monkeypatch.setattr(edp_config, "EMBED_DIM", EMBED_DIM, raising=False)

    import edp.embeddings as embeddings_mod
    monkeypatch.setattr(embeddings_mod, "embed_one", _fake_embed_one)
    monkeypatch.setattr(embeddings_mod, "embed", _fake_embed)

    for modname in ("edp.memory", "edp.pipeline", "edp.semantic_memory"):
        try:
            mod = sys.modules.get(modname) or __import__(modname, fromlist=["_"])
        except Exception:
            continue
        if hasattr(mod, "embed_one"):
            monkeypatch.setattr(mod, "embed_one", _fake_embed_one, raising=False)
        if hasattr(mod, "embed"):
            monkeypatch.setattr(mod, "embed", _fake_embed, raising=False)

    yield


# ── Store isolado em disco (tmp_path) — nunca toca C:\edp_data / produção ─────

@pytest.fixture
def isolated_base_dir(tmp_path, monkeypatch):
    """
    Redireciona MEMORY_DIR/BASE_DIR (config + módulos que já importaram os
    nomes) para tmp_path, e seta EDP_BASE_DIR no ambiente (alguns scripts
    standalone, ex. write_provenance._audit_path, leem o env var direto).
    """
    base = tmp_path / "edp_data_test"
    sessions = base / "sessions"
    sessions.mkdir(parents=True)

    monkeypatch.setenv("EDP_BASE_DIR", str(base))
    monkeypatch.setattr(edp_config, "BASE_DIR", base, raising=False)
    monkeypatch.setattr(edp_config, "MEMORY_DIR", sessions, raising=False)
    monkeypatch.setattr(edp_config, "METRICS_LOG", base / "metrics.jsonl", raising=False)

    import edp.memory as memory_mod
    monkeypatch.setattr(memory_mod, "MEMORY_DIR", sessions, raising=False)

    # edp.metrics faz `from .config import METRICS_LOG` — nome vinculado no
    # import, não relido a cada chamada. Repatchear o módulo de config sozinho
    # não basta; qualquer M.timer()/M.record() (ex: MemoryStore.add()) grava
    # em METRICS_LOG do lugar errado (produção) sem este patch explícito.
    import edp.metrics as metrics_mod
    monkeypatch.setattr(metrics_mod, "METRICS_LOG", base / "metrics.jsonl", raising=False)

    try:
        import edp.semantic_memory as semantic_memory_mod
    except ModuleNotFoundError:
        semantic_memory_mod = None  # pós-T4d: módulo aposentado
    if semantic_memory_mod is not None:
        monkeypatch.setattr(semantic_memory_mod, "MEMORY_DIR", sessions, raising=False)

    return base


# ── Store sintético (MemoryStore real, sessão/disco isolados) ─────────────────

@pytest.fixture
def synthetic_store(isolated_base_dir):
    """
    MemoryStore real (não um double) apontando para tmp_path — usado pelos
    testes que precisam do mecanismo de gate real (peso-piso, exclusão
    híbrida, consolidação), sem tocar em store de produção nem em CSV real.
    """
    import edp.memory as memory_mod
    session_id = f"test-{uuid.uuid4().hex[:8]}"
    return memory_mod.MemoryStore(session_id)


def make_entry(**overrides) -> dict:
    """
    Fábrica de entry episódico sintético — só os campos que o código de
    scoring/retrieve/consolidação de fato lê (ver edp/memory.py:627-761,
    edp/consolidation.py). Sem PII, sem dado de produção.
    """
    now = 1_800_000_000.0  # timestamp fixo arbitrário — não usa relógio real
    entry = {
        "id":             str(uuid.uuid4()),
        "text":           "Q: pergunta sintética\nA: resposta sintética",
        "embedding":      _fake_embed_one(f"synthetic-{uuid.uuid4().hex}"),
        "timestamp":      now,
        "score_inicial":  0.5,
        "acessos":        0,
        "ultimo_acesso":  now,
        "prioridade":     "media",
        "layer":          "episodic",
        "source":         "user",
        "source_type":    "user_input",
        "confidence":     0.5,
        "epistemic_status": "hypothesis",
        "derived_from":   [],
        "is_epistemic_anchor": False,
        "answer_class":   None,
    }
    entry.update(overrides)
    return entry


@pytest.fixture
def entry_factory():
    return make_entry


# ── frozen_clock — ESQUELETO (item D do adendo, injeção completa depois) ──────

@pytest.fixture
def frozen_clock(monkeypatch):
    """
    Esqueleto: fixa edp.clock.now() num valor constante. NÃO propaga (ainda)
    para os `_now` já vinculados por `from .clock import now as _now` em
    memory.py/pipeline.py/semantic_memory.py — cada um desses precisaria de
    monkeypatch próprio, deixado para a fase de injeção completa.
    """
    fixed = 1_800_000_000.0
    import edp.clock as clock_mod
    monkeypatch.setattr(clock_mod, "now", lambda: fixed, raising=False)
    return fixed
