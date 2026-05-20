"""
semantic_memory.py — Memória semântica consolidada do EDP v3 (PATCHED)

PATCHES APLICADOS:
  [P-C3-1] bare except: → except Exception as e: (não captura mais KeyboardInterrupt/SystemExit)
  [P-C3-2] threading.RLock para consolidate_from_episodes() e save()
            evita race condition com scheduler._job_consolidate()
  [P-C3-3] retrieve() aceita min_score como alias de min_conf
            (context_builder.py passa min_score= → antes era TypeError silencioso)
  [P-C3-4] _load() loga erro de parsing em vez de silenciar completamente
  [P-C3-5] consolidate_from_episodes() guarda contra lista vazia de embeddings
            (np.vstack([]) lançaria exceção antes do guard MIN_EPISODES_MERGE)

NOTA ARQUITETURAL (split-brain semântico):
  Esta classe (SemanticMemory de semantic_memory.py) armazena objetos Concept
  em _concepts.json. É DIFERENTE da class SemanticMemory em memory.py que
  armazena dicts em _semantic.json. Os dois sistemas coexistem sem sincronização.
  Fix completo requer MemoryBridgeV32 (já criado em pipeline_patched.py) ser
  estendido para também sincronizar _concepts.json ↔ _semantic.json.
  Por ora, este patch protege cada sistema individualmente de race conditions.
"""
import json
import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from .config import MEMORY_DIR, CONSOLIDATION_SIM_THRESH, DECAY_LAMBDA
from .embeddings import embed_one

logger = logging.getLogger("edp.semantic_memory")

MAX_CONCEPTS        = 200
MIN_EPISODES_MERGE  = 2
REINFORCEMENT_BOOST = 0.15


@dataclass
class Concept:
    id:          str
    text:        str
    embedding:   np.ndarray
    confidence:  float
    source_ids:  list
    reinforced:  int   = 0
    created_at:  float = field(default_factory=time.time)
    last_seen:   float = field(default_factory=time.time)
    decay_rate:  float = 0.01

    @property
    def decay(self) -> float:
        return math.exp(-self.decay_rate * (time.time() - self.last_seen) / 86400)

    @property
    def effective_confidence(self) -> float:
        return round(self.confidence * self.decay, 4)

    def reinforce(self, boost: float = REINFORCEMENT_BOOST) -> None:
        self.confidence = min(round(self.confidence + boost, 4), 1.0)
        self.last_seen  = time.time()
        self.reinforced += 1

    def to_dict(self) -> dict:
        return {
            "id":          self.id,
            "text":        self.text,
            "embedding":   self.embedding.tolist(),
            "confidence":  self.confidence,
            "source_ids":  self.source_ids,
            "reinforced":  self.reinforced,
            "created_at":  self.created_at,
            "last_seen":   self.last_seen,
            "decay_rate":  self.decay_rate,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Concept":
        d2 = dict(d)
        d2["embedding"] = np.array(d2["embedding"], dtype=np.float32)
        return cls(**d2)


class SemanticMemory:
    """
    Memória semântica baseada em Concept objects.
    Persiste em _concepts.json (DIFERENTE de memory.py._semantic.json).

    [P-C3-2] RLock protege todas as mutações e escritas em disco.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id  = session_id
        self._lock       = threading.RLock()  # [P-C3-2] reentrant — safe para consolidate→save
        self._concepts:  List[Concept] = []
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self._path = MEMORY_DIR / f"{session_id}_concepts.json"
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            self._concepts = [Concept.from_dict(d) for d in raw]
        except Exception as e:  # [P-C3-1] não mais bare except
            logger.warning("[SemanticMemory] Falha ao carregar %s: %s — iniciando vazio", self._path, e)
            self._concepts = []

    def save(self) -> None:
        with self._lock:  # [P-C3-2]
            self._path.write_text(
                json.dumps([c.to_dict() for c in self._concepts], ensure_ascii=False, indent=2),
                "utf-8",
            )

    def consolidate_from_episodes(
        self,
        episodes:  list,
        threshold: Optional[float] = None,
    ) -> list:
        """
        [P-C3-2] Lock completo durante consolidação — evita race com scheduler.
        [P-C3-5] Guard contra episodes vazios antes de np.vstack.
        """
        threshold = threshold or CONSOLIDATION_SIM_THRESH

        # [P-C3-5] Guard: filtra episodes sem embedding válido ANTES de vstack
        valid_eps = [e for e in episodes if e.get("embedding") is not None]
        if len(valid_eps) < MIN_EPISODES_MERGE:
            return []

        with self._lock:  # [P-C3-2]
            embs = np.vstack([np.array(e["embedding"], dtype=np.float32) for e in valid_eps])
            sim  = cosine_similarity(embs)
            n    = len(valid_eps)
            visited  = [False] * n
            affected = []

            for i in range(n):
                if visited[i]:
                    continue
                cluster    = [i]
                visited[i] = True
                for j in range(i + 1, n):
                    if not visited[j] and sim[i, j] >= threshold:
                        cluster.append(j)
                        visited[j] = True

                if len(cluster) < MIN_EPISODES_MERGE:
                    continue

                c_eps    = [valid_eps[k] for k in cluster]
                c_embs   = embs[cluster]
                emb_mean = c_embs.mean(axis=0)
                norm     = np.linalg.norm(emb_mean)
                if norm > 0:
                    emb_mean /= norm

                existing = self._find_similar(emb_mean, 0.85)
                if existing:
                    existing.reinforce()
                    existing.source_ids = list(
                        set(existing.source_ids + [e["id"] for e in c_eps])
                    )
                    affected.append(existing)
                else:
                    sims_c = cosine_similarity([emb_mean], c_embs)[0]
                    best   = c_eps[int(sims_c.argmax())]["text"]
                    concept = Concept(
                        id=str(uuid.uuid4()),
                        text=best,
                        embedding=emb_mean.astype(np.float32),
                        confidence=round(float(sims_c.mean()), 4),
                        source_ids=[e["id"] for e in c_eps],
                    )
                    self._concepts.append(concept)
                    affected.append(concept)

            if len(self._concepts) > MAX_CONCEPTS:
                self._concepts.sort(key=lambda c: c.effective_confidence, reverse=True)
                self._concepts = self._concepts[:MAX_CONCEPTS]

            self.save()
            return affected

    def _find_similar(self, emb: np.ndarray, thresh: float) -> Optional[Concept]:
        """Deve ser chamado dentro de lock (não adquire lock próprio)."""
        if not self._concepts:
            return None
        sims = cosine_similarity(
            [emb],
            np.vstack([c.embedding for c in self._concepts]),
        )[0]
        idx = int(sims.argmax())
        return self._concepts[idx] if sims[idx] >= thresh else None

    def retrieve(
        self,
        query:    str,
        top_k:    int   = 5,
        min_conf: float = 0.20,
        min_score: Optional[float] = None,  # [P-C3-3] alias para compatibilidade com context_builder
    ) -> list:
        """
        [P-C3-3] min_score aceito como alias de min_conf.
        context_builder.py passa min_score= — antes causava TypeError silencioso
        porque min_score não era parâmetro reconhecido.
        """
        # Se min_score passado explicitamente, usa ele como min_conf
        effective_min = min_score if min_score is not None else min_conf

        with self._lock:
            if not self._concepts:
                return []
            qemb  = embed_one(query)
            cembs = np.vstack([c.embedding for c in self._concepts])
            sims  = cosine_similarity([qemb], cembs)[0]

            results = []
            for i, c in enumerate(self._concepts):
                if c.effective_confidence < effective_min:
                    continue
                score = float(sims[i]) * c.effective_confidence
                results.append({
                    "id":             c.id,
                    "text":           c.text,
                    "confidence":     c.confidence,
                    "effective_conf": c.effective_confidence,
                    "similarity":     round(float(sims[i]), 4),
                    "score":          round(score, 4),
                    # [P-C3-3] ranking_score alias — compatibilidade com memory.py.MemoryStore.retrieve()
                    "ranking_score":  round(score, 4),
                    "reinforced":     c.reinforced,
                    "source_count":   len(c.source_ids),
                })

            return sorted(results, key=lambda x: x["score"], reverse=True)[:top_k]

    def reinforce_by_query(
        self,
        query:     str,
        threshold: float = 0.70,
        boost:     float = REINFORCEMENT_BOOST,
    ) -> int:
        with self._lock:
            if not self._concepts:
                return 0
            qemb = embed_one(query)
            sims = cosine_similarity(
                [qemb],
                np.vstack([c.embedding for c in self._concepts]),
            )[0]
            n = sum(
                1 for i, s in enumerate(sims)
                if float(s) >= threshold and (self._concepts[i].reinforce(boost) or True)
            )
            if n:
                self.save()
            return n

    def apply_decay(self, min_confidence: float = 0.05) -> int:
        with self._lock:
            before = len(self._concepts)
            self._concepts = [
                c for c in self._concepts
                if c.confidence > 0.80 or c.effective_confidence >= min_confidence
            ]
            removed = before - len(self._concepts)
            if removed > 0:
                self.save()
            return removed

    def stats(self) -> dict:
        with self._lock:
            if not self._concepts:
                return {"total": 0}
            confs = [c.effective_confidence for c in self._concepts]
            return {
                "total":           len(self._concepts),
                "avg_confidence":  round(sum(confs) / len(confs), 4),
                "high_conf":       sum(1 for c in self._concepts if c.confidence > 0.70),
                "avg_reinforced":  round(
                    sum(c.reinforced for c in self._concepts) / len(self._concepts), 2
                ),
            }

    def __len__(self) -> int:
        return len(self._concepts)
