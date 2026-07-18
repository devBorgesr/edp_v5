"""
edp.memory — Hierarquia de memória cognitiva do EDP v3 (pacote).

Fase 4 T3 (split MOVE-ONLY, completo): memory.py (2300+ linhas) virou
pacote. Corte final (ajustado do proposto original — ver relato da Fase 4):

  atomic_io.py  — _serialize/_deserialize/_atomic_write_json/_safe_load_json
                  (Dívida técnica #8: write atômico com lock+retry)
  semantic.py   — SemanticMemory (conhecimento consolidado)
  store.py      — WorkingMemory, EpisodicMemory, _ScopedView, MemoryStore
                  (episodic.py NÃO foi splitado separado de store.py: o piso
                  NOT_FOUND_FLOOR (EpisodicMemory.retrieve) e a exclusão do
                  índice híbrido (MemoryStore._hybrid_index) são o mesmo par
                  de defesa exp012/exp016 e precisam ficar no MESMO módulo —
                  choke-point, item G do adendo. Ver comentário CHOKE-POINT
                  no topo de store.py.)
  __init__.py   — este arquivo: re-exporta a superfície pública, byte-
                  compatível com o memory.py original. Nenhum importador
                  externo (edp/*, tests/*, scripts/*, benchmark_edp.py,
                  run.py, etc.) precisa mudar.

Corpos de função/classe byte-idênticos ao memory.py original em todo o
pacote — só imports e localização mudaram.
"""

from .atomic_io import (
    _atomic_write_json,
    _safe_load_json,
    _serialize,
    _deserialize,
)
from .semantic import SemanticMemory
from .store import (
    MEMORY_DIR,
    SESSION_GAP_THRESHOLD_SEC,
    SESSION_BOOST_FACTOR,
    OUT_OF_SESSION_PENALTY,
    CURRENT_SESSION_TRUST_THRESHOLD,
    _now,
    _edp_lifetime_path,
    _get_edp_lifetime,
    _new_entry,
    _migrate_legacy_session_files,
    WorkingMemory,
    EpisodicMemory,
    _ScopedView,
    MemoryStore,
)
