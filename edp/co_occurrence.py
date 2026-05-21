"""
edp.co_occurrence — Rastreamento de co-ocorrência entre memórias.

Conta quantas vezes pares de memórias são retrieved juntas no mesmo turno.
Dados emergem naturalmente do uso: ideias que interagem ganham peso juntas.

Princípios:
  - Observação passiva: NÃO modifica retrieval (apenas conta)
  - Simétrico: A↔B é o mesmo par (registrado nas duas direções para consulta rápida)
  - Persistência atômica: write-temp + rename (lição do JSON corrompido de 19/05)
  - Limite proteção: 50.000 pares máximo; descarta count=1 mais antigos quando estoura

PR1 (este arquivo): estrutura + persistência + API base
PR2 (futuro): hook no retrieval para chamar record_co_occurrence
PR3 (futuro): endpoint GET + UI no /memory/review
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

from .config import MEMORY_DIR
from .clock import now as _now  # Peça 0.2a — relógio interno robusto

logger = logging.getLogger("edp.co_occurrence")

MAX_PAIRS = 50_000
CLEANUP_KEEP_FRACTION = 0.9  # após cleanup, manter 90% do limite (descarta 10%)


class CoOccurrenceTracker:
    """
    Rastreia pares de memórias que foram retrieved juntas.

    Estrutura interna:
        self.pairs[memory_id_a][memory_id_b] = {"count": int, "last_seen": float}

    Estrutura simétrica: se A↔B foi registrado, está em pairs[A][B] E pairs[B][A].
    Isso permite get_top_co_occurred(X) sem precisar buscar duas vezes.
    """

    def __init__(self, session_id: str = "default") -> None:
        self.session_id = session_id
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self.path: Path = MEMORY_DIR / f"{session_id}_co_occurrence.json"
        self.pairs: dict[str, dict[str, dict]] = {}
        self._lock = threading.Lock()
        self._dirty = False
        self._load()

    # ───────────────────────────────────────────────────────────────────
    # Persistência
    # ───────────────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Carrega de disco. Em caso de corrupção, inicia vazio (não fatal)."""
        if not self.path.exists():
            logger.debug("[co_occurrence] arquivo não existe, iniciando vazio: %s", self.path)
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning("[co_occurrence] formato inválido em %s, ignorando", self.path)
                return
            self.pairs = data
            logger.info(
                "[co_occurrence] carregado session=%s | pairs=%d",
                self.session_id, self._count_unique_pairs(),
            )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                "[co_occurrence] falha ao carregar %s: %s | iniciando vazio",
                self.path, e,
            )
            self.pairs = {}

    def save(self) -> None:
        """
        Salva atomicamente: escreve em .tmp e depois renomeia.
        Garante que nunca haverá arquivo parcialmente escrito.
        """
        with self._lock:
            if not self._dirty:
                return
            tmp_path = self.path.with_suffix(".json.tmp")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(self.pairs, f, ensure_ascii=False, separators=(",", ":"))
                # os.replace é atômico em POSIX e Windows
                os.replace(tmp_path, self.path)
                self._dirty = False
            except (OSError, TypeError) as e:
                logger.error("[co_occurrence] falha ao salvar: %s", e)
                # Limpa .tmp se ficou pendurado
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass

    # ───────────────────────────────────────────────────────────────────
    # Registro de co-ocorrência
    # ───────────────────────────────────────────────────────────────────

    def record_co_occurrence(self, memory_ids: list[str]) -> int:
        """
        Registra que essas memórias foram retrieved juntas no mesmo turno.
        Incrementa contador para todos os pares possíveis.

        Args:
            memory_ids: IDs das memórias que apareceram juntas

        Returns:
            Número de pares atualizados.
        """
        if not memory_ids or len(memory_ids) < 2:
            return 0

        # Remove duplicatas e None preservando ordem
        unique_ids = []
        seen = set()
        for mid in memory_ids:
            if mid and mid not in seen:
                seen.add(mid)
                unique_ids.append(mid)

        if len(unique_ids) < 2:
            return 0

        now = _now()
        pairs_updated = 0

        with self._lock:
            # Para cada par (i, j) com i != j, incrementa nas duas direções
            for i, id_a in enumerate(unique_ids):
                for id_b in unique_ids[i + 1:]:
                    self._inc_pair(id_a, id_b, now)
                    self._inc_pair(id_b, id_a, now)
                    pairs_updated += 1

            self._dirty = True

            # Checa limite e aplica cleanup se necessário
            unique_pairs = self._count_unique_pairs()
            if unique_pairs > MAX_PAIRS:
                self._cleanup_old_pairs()

        return pairs_updated

    def _inc_pair(self, id_a: str, id_b: str, now: float) -> None:
        """Incrementa contador do par (a→b). Caller deve segurar lock."""
        if id_a not in self.pairs:
            self.pairs[id_a] = {}
        if id_b not in self.pairs[id_a]:
            self.pairs[id_a][id_b] = {"count": 0, "last_seen": 0.0}
        self.pairs[id_a][id_b]["count"] += 1
        self.pairs[id_a][id_b]["last_seen"] = now

    # ───────────────────────────────────────────────────────────────────
    # Consulta
    # ───────────────────────────────────────────────────────────────────

    def get_top_co_occurred(
        self,
        memory_id: str,
        top_k: int = 10,
    ) -> list[dict]:
        """
        Retorna as memórias mais co-occorridas com `memory_id`.

        Returns:
            Lista de dicts {id, count, last_seen} ordenada por count DESC,
            depois last_seen DESC.
        """
        if memory_id not in self.pairs:
            return []

        items = self.pairs[memory_id]
        sorted_items = sorted(
            items.items(),
            key=lambda kv: (kv[1]["count"], kv[1]["last_seen"]),
            reverse=True,
        )
        return [
            {"id": other_id, "count": data["count"], "last_seen": data["last_seen"]}
            for other_id, data in sorted_items[:top_k]
        ]

    def stats(self) -> dict:
        """Retorna métricas globais."""
        unique_pairs = self._count_unique_pairs()
        total_count = sum(
            d["count"]
            for others in self.pairs.values()
            for d in others.values()
        )
        # total_count conta cada par duas vezes (simétrico), dividir por 2
        total_count = total_count // 2
        return {
            "total_unique_pairs": unique_pairs,
            "total_co_occurrences": total_count,
            "tracked_memories": len(self.pairs),
            "session_id": self.session_id,
        }

    # ───────────────────────────────────────────────────────────────────
    # Limpeza
    # ───────────────────────────────────────────────────────────────────

    def _count_unique_pairs(self) -> int:
        """Conta pares únicos (dividindo por 2 pela simetria)."""
        total_entries = sum(len(v) for v in self.pairs.values())
        return total_entries // 2

    def _cleanup_old_pairs(self) -> None:
        """
        Quando excede MAX_PAIRS, descarta pares com count=1 mais antigos.
        Caller deve segurar lock.
        """
        target = int(MAX_PAIRS * CLEANUP_KEEP_FRACTION)
        before = self._count_unique_pairs()

        # Lista pares (id_a, id_b, last_seen) onde count=1
        candidates = []
        for id_a, others in self.pairs.items():
            for id_b, data in others.items():
                if data["count"] == 1 and id_a < id_b:  # evita duplicar simétrico
                    candidates.append((data["last_seen"], id_a, id_b))

        # Ordena por last_seen ASC (mais antigos primeiro)
        candidates.sort()

        to_remove = before - target
        removed = 0
        for _, id_a, id_b in candidates:
            if removed >= to_remove:
                break
            self._remove_pair(id_a, id_b)
            removed += 1

        after = self._count_unique_pairs()
        logger.warning(
            "[co_occurrence] cleanup: removidos %d pares antigos com count=1 (%d → %d)",
            removed, before, after,
        )

    def _remove_pair(self, id_a: str, id_b: str) -> None:
        """Remove par dos dois lados. Caller deve segurar lock."""
        if id_a in self.pairs and id_b in self.pairs[id_a]:
            del self.pairs[id_a][id_b]
            if not self.pairs[id_a]:
                del self.pairs[id_a]
        if id_b in self.pairs and id_a in self.pairs[id_b]:
            del self.pairs[id_b][id_a]
            if not self.pairs[id_b]:
                del self.pairs[id_b]

    # ───────────────────────────────────────────────────────────────────
    # Manutenção de memórias deletadas
    # ───────────────────────────────────────────────────────────────────

    def forget_memory(self, memory_id: str) -> int:
        """
        Remove todas referências a uma memória (chamado quando entry é deletada).

        Returns:
            Número de pares removidos.
        """
        removed = 0
        with self._lock:
            # Remove entradas onde memory_id é chave principal
            if memory_id in self.pairs:
                removed += len(self.pairs[memory_id])
                del self.pairs[memory_id]

            # Remove entradas onde memory_id aparece como par
            for id_a in list(self.pairs.keys()):
                if memory_id in self.pairs[id_a]:
                    del self.pairs[id_a][memory_id]
                    removed += 1
                    # Se essa memória ficou sem pares, remove do dict raiz
                    if not self.pairs[id_a]:
                        del self.pairs[id_a]

            if removed > 0:
                self._dirty = True

        return removed
