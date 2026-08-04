"""
edp.memory.semantic — SemanticMemory: conhecimento consolidado, alta durabilidade.

Fase 4 T3 (extração 2/3): extraído verbatim de memory.py:1144-1275 (posição
original antes do split; MOVE-ONLY, corpo de classe byte-idêntico ao
original — só esta docstring e os imports são novos).

Nota (Dívida documentada, não mexida nesta extração): SemanticMemory.retrieve()
NÃO lê answer_class — o piso NOT_FOUND_FLOOR (EDP_WRITE_PROVENANCE) só cobre
EpisodicMemory hoje. Ver edp/memory/store.py, comentário "CHOKE-POINT" na
EpisodicMemory.retrieve() — quando o piso for estendido para cá, o one-liner
equivalente entra em SemanticMemory.retrieve() logo abaixo do bloco de
epistemic governance.
"""
import logging
import threading

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from ..config import MEMORY_DIR
from .atomic_io import _atomic_write_json, _safe_load_json, _load_json_or_quarantine, _serialize, _deserialize

logger = logging.getLogger("edp.memory")

class SemanticMemory:
    """
    Memória semântica: conhecimento consolidado, alta durabilidade.
    [P7] _emb_matrix cacheada — reconstruída apenas quando entries muda.
    """

    def __init__(self, session_id: str, scope: str = "cognitive"):
        """
        Args:
            session_id: ID da sessão
            scope: 'cognitive' (default) ou 'sprint'. Commit 1 dos Dois Exocórtices.
        """
        self.session_id  = session_id
        self.scope       = scope
        self._lock       = threading.Lock()
        self._emb_cache: np.ndarray | None = None  # [P7] cache de matrix
        self._cache_dirty = True
        # Commit 1: caminhos isolados por scope
        scope_dir = MEMORY_DIR / f"{session_id}_{scope}"
        scope_dir.mkdir(parents=True, exist_ok=True)
        self.path    = scope_dir / "semantic.json"
        self.entries: list[dict] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            # Peça 0.3.1 / Dívida #53 (docs/preregistro_fix_corrupcao_json.md):
            # _load_json_or_quarantine nunca crasha nem perde dado em
            # silêncio — ver EpisodicMemory._load (store.py) para o
            # desenho completo, migrado primeiro.
            data = _load_json_or_quarantine(self.path, store_label="semantic")
            if data is not None:
                self.entries = _deserialize(data)
        self._cache_dirty = True

    def save(self) -> None:
        # Peça 0.3.1: write atômico
        with self._lock:
            _atomic_write_json(self.path, _serialize(self.entries))

    def promote(self, entry: dict) -> None:
        """Promove uma entrada episódica para memória semântica."""
        # Dívida #49 (13/06/2026): recusas (frase-gatilho da câmara) NÃO viram
        # conhecimento consolidado. "Não sei" não é fato semântico, e promovê-lo
        # daria peso alto (0.85) a uma recusa no retrieval — alimentando o loop.
        # Mesma confiança "alta" do filtro de retrieval, por consistência.
        try:
            from ..echo_chamber import detectar_auto_sinal_de_limite
            if detectar_auto_sinal_de_limite(
                entry.get("text", "") or ""
            ).get("confianca") == "alta":
                logger.info(
                    "[promote] recusa NÃO promovida à semântica (Dívida #49) "
                    "| id=%s", (entry.get("id", "") or "")[:8],
                )
                return
        except Exception as e:
            logger.debug("[promote] checagem de recusa falhou: %s", e)
        entry = dict(entry)
        entry["layer"]     = "semantic"
        entry["prioridade"] = "alta"
        self.entries.append(entry)
        self._cache_dirty = True  # [P7] invalida cache
        self.save()

    def _get_emb_matrix(self) -> np.ndarray | None:
        """[P7] Retorna matrix cacheada; reconstrói só se dirty."""
        if not self.entries:
            return None
        if self._cache_dirty or self._emb_cache is None:
            self._emb_cache = np.vstack([
                np.array(e["embedding"], dtype=np.float32) for e in self.entries
            ])
            self._cache_dirty = False
        return self._emb_cache

    def retrieve(
        self,
        query_emb: np.ndarray,
        top_k: int = 5,
        min_score: float = 0.30,
        respect_epistemic: bool = True,
    ) -> list[dict]:
        """
        [P7] Usa matrix cacheada — não reconstrói a cada chamada.
        [P1-v3.5] respect_epistemic: aplica filtro/penalty igual à episódica.
        """
        if not self.entries:
            return []
        matrix = self._get_emb_matrix()
        if matrix is None:
            return []
        sims = cosine_similarity([query_emb], matrix)[0]

        scored = []
        skipped_blocked = 0
        for i in range(len(self.entries)):
            e = self.entries[i]
            sim = float(sims[i])

            # Epistemic governance
            epi_multiplier = 1.0
            if respect_epistemic:
                status = e.get("epistemic_status", "hypothesis")
                if status in ("contradicted", "quarantined"):
                    skipped_blocked += 1
                    continue
                elif status == "stale":
                    epi_multiplier = 0.5
                elif status == "hypothesis":
                    epi_multiplier = 0.85

            adjusted = sim * epi_multiplier
            if adjusted >= min_score:
                scored.append((adjusted, e))

        scored.sort(key=lambda x: x[0], reverse=True)
        if skipped_blocked > 0:
            try:
                import logging as _lg
                _lg.getLogger("edp.memory").debug(
                    "[semantic.retrieve] epistemic | bloqueadas=%d",
                    skipped_blocked,
                )
            except Exception:
                pass

        return [{**e, "ranking_score": round(s, 4)} for s, e in scored[:top_k]]

    def all_embeddings(self) -> np.ndarray:
        matrix = self._get_emb_matrix()
        return matrix if matrix is not None else np.array([])

    def __len__(self) -> int:
        return len(self.entries)
