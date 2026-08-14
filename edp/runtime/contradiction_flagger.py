"""
edp.runtime.contradiction_flagger — Detector + persistência de possíveis contradições.

PRINCÍPIO INVIOLÁVEL:
  Este módulo NÃO resolve conflitos.
  Este módulo NÃO altera epistemic_status automaticamente.
  Este módulo NÃO deleta memórias.

  Apenas:
    1. FLAGA `possible_conflict=True`
    2. LOGA o par envolvido
    3. PERSISTE em disco para review humano
    4. CAPTURA feedback (útil / falso positivo)

Os flags são INFRA DE OBSERVAÇÃO, não governança.
Sobreviverão mesmo se a arquitetura mudar — coletam evidência pura.

Heurística (PT-BR + inglês básico):
  1. Embeddings similares (>0.85)
  2. AND presença de marcador de negação em apenas UM dos textos
  3. → flagar como possível conflito

LIMITAÇÕES CONHECIDAS (sem tentar resolver):
  - Falsos positivos em embeddings (paráfrase != contradição)
  - Falsos negativos quando contradição é semântica sem negação explícita
  - Não pega contradições temporais ("era X" vs "agora é Y")

Por que NÃO usar LLM:
  - 1 chamada extra por par = 30-90s em CPU
  - Inviável em 8GB sem GPU
  - Heurística cobre ~30-50% dos casos comuns
  - Resto fica para revisão manual via dashboard
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import deque, Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Deque, List, Optional, Dict, Any

from ..clock import now as _now  # Peça 0.2b — relógio interno robusto

logger = logging.getLogger("edp.runtime.contradiction")


# ── Marcadores de negação (PT-BR + EN básico) ────────────────────────────────
_NEGATION_TOKENS = [
    # PT-BR
    "não", "nao", "nunca", "jamais", "tampouco", "nenhum", "nenhuma",
    "sem", "nada", "ninguém", "ninguem", "exceto",
    # EN básico
    "not", "no", "never", "none", "without", "neither", "nor",
]
_NEGATION_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in _NEGATION_TOKENS) + r")\b",
    re.IGNORECASE,
)

SIMILARITY_THRESHOLD = 0.85
MAX_FLAGS_KEPT = 200   # rolling em memória (aumentado de 50 para survey decente)


def has_negation(text: str) -> bool:
    """Retorna True se texto contém marcador de negação."""
    if not text:
        return False
    return bool(_NEGATION_PATTERN.search(text))


def negation_asymmetry(text_a: str, text_b: str) -> bool:
    """True se UM dos textos tem negação e o OUTRO não."""
    neg_a = has_negation(text_a)
    neg_b = has_negation(text_b)
    return neg_a != neg_b


# ── Feedback humano ──────────────────────────────────────────────────────────

class FlagFeedback:
    """Valores possíveis do feedback do operador sobre um flag."""
    UNREVIEWED    = "unreviewed"      # default — ninguém revisou ainda
    USEFUL        = "useful"          # operador confirmou: era contradição real
    FALSE_POSITIVE = "false_positive" # operador rejeitou: heurística errou
    AMBIGUOUS     = "ambiguous"       # operador viu mas não decidiu

    ALL = (UNREVIEWED, USEFUL, FALSE_POSITIVE, AMBIGUOUS)


@dataclass
class ConflictFlag:
    flag_id:     str                 # único, derivado de ids_canonical + ts
    id_a:        str
    id_b:        str
    similarity:  float
    text_a:      str
    text_b:      str
    detected_at: float = field(default_factory=_now)
    reason:      str   = "negation_asymmetry"

    # Feedback humano (preenchido após review)
    feedback:        str            = FlagFeedback.UNREVIEWED
    feedback_at:     Optional[float] = None
    feedback_note:   str            = ""

    def to_dict(self) -> dict:
        return {
            "flag_id":       self.flag_id,
            "id_a":          self.id_a,
            "id_b":          self.id_b,
            "similarity":    round(self.similarity, 4),
            "text_a":        self.text_a[:300],
            "text_b":        self.text_b[:300],
            "detected_at":   self.detected_at,
            "reason":        self.reason,
            "feedback":      self.feedback,
            "feedback_at":   self.feedback_at,
            "feedback_note": self.feedback_note,
        }


class ContradictionFlagger:
    """
    Singleton thread-safe.

    Persistência:
      - Diretório: $EDP_BASE_DIR/flags/  (ou ./data/flags/ default)
      - Arquivo: flags.jsonl  (1 linha = 1 flag)
      - Append-only, sem dependência de DB

    Uso típico:
        flagger = get_flagger()
        if flagger.check_pair(entry_a, entry_b, similarity=0.91):
            ...
        recent = flagger.list_flags(limit=50, filter_feedback="unreviewed")
        flagger.set_feedback("flag_xxx", FlagFeedback.USEFUL, note="real")
    """

    def __init__(self, persist_dir: Optional[Path] = None) -> None:
        self.flags: Deque[ConflictFlag] = deque(maxlen=MAX_FLAGS_KEPT)
        self._lock = threading.Lock()
        self._seen_pairs: set = set()
        self._max_seen = 1000

        # Diretório de persistência
        if persist_dir is None:
            base = os.environ.get("EDP_BASE_DIR", "data")
            persist_dir = Path(base) / "flags"
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.persist_file = self.persist_dir / "flags.jsonl"

        # Carrega flags persistidas
        self._load_from_disk()

        logger.info(
            "[contradiction] inicializado | sim_thresh=%.2f max_flags=%d persist=%s loaded=%d",
            SIMILARITY_THRESHOLD, MAX_FLAGS_KEPT, self.persist_file, len(self.flags),
        )

    # ── Persistência ─────────────────────────────────────────────────────────

    def _load_from_disk(self) -> None:
        """Carrega flags persistidas, mantém apenas as mais recentes em memória."""
        if not self.persist_file.exists():
            return
        loaded: List[ConflictFlag] = []
        latest_per_id: Dict[str, ConflictFlag] = {}
        try:
            with open(self.persist_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        # Sobrescreve se ID já visto (versão mais recente vence)
                        fid = d.get("flag_id", "")
                        if fid:
                            latest_per_id[fid] = ConflictFlag(
                                flag_id=fid,
                                id_a=d.get("id_a", ""),
                                id_b=d.get("id_b", ""),
                                similarity=d.get("similarity", 0.0),
                                text_a=d.get("text_a", ""),
                                text_b=d.get("text_b", ""),
                                detected_at=d.get("detected_at", 0.0),
                                reason=d.get("reason", ""),
                                feedback=d.get("feedback", FlagFeedback.UNREVIEWED),
                                feedback_at=d.get("feedback_at"),
                                feedback_note=d.get("feedback_note", ""),
                            )
                    except (json.JSONDecodeError, TypeError):
                        continue
        except OSError as e:
            logger.warning("[contradiction] load falhou: %s", e)
            return

        # Ordena por detected_at e mantém últimos N em memória
        sorted_flags = sorted(latest_per_id.values(), key=lambda f: f.detected_at)
        for f in sorted_flags[-MAX_FLAGS_KEPT:]:
            self.flags.append(f)
            pair_key = tuple(sorted([f.id_a, f.id_b]))
            self._seen_pairs.add(pair_key)

    def _append_to_disk(self, flag: ConflictFlag) -> None:
        """Append-only de 1 flag."""
        try:
            with open(self.persist_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(flag.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("[contradiction] persist falhou: %s", e)

    # ── Detecção ─────────────────────────────────────────────────────────────

    def check_pair(
        self,
        entry_a: dict,
        entry_b: dict,
        similarity: float,
    ) -> bool:
        """
        Avalia se par é possível contradição.
        Returns: True se flagado.
        """
        if similarity < SIMILARITY_THRESHOLD:
            return False

        id_a = entry_a.get("id", "")
        id_b = entry_b.get("id", "")
        if not id_a or not id_b or id_a == id_b:
            return False

        pair_key = tuple(sorted([id_a, id_b]))
        if pair_key in self._seen_pairs:
            return False

        text_a = entry_a.get("text", "")
        text_b = entry_b.get("text", "")
        if not negation_asymmetry(text_a, text_b):
            return False

        # Gera flag_id determinístico (mesma par + ts = mesmo id)
        ts = _now()
        flag_id = f"flag_{int(ts)}_{id_a[:6]}_{id_b[:6]}"

        flag = ConflictFlag(
            flag_id=flag_id,
            id_a=id_a, id_b=id_b,
            similarity=similarity,
            text_a=text_a, text_b=text_b,
            detected_at=ts,
        )

        with self._lock:
            self.flags.append(flag)
            self._seen_pairs.add(pair_key)
            if len(self._seen_pairs) > self._max_seen:
                self._seen_pairs = set(list(self._seen_pairs)[-self._max_seen + 100:])

        # Persiste em disco (fora do lock)
        self._append_to_disk(flag)

        logger.warning(
            "[contradiction] POSSIVEL CONFLITO id=%s sim=%.3f "
            "| A='%s...' B='%s...'",
            flag_id, similarity,
            text_a[:60].replace("\n", " "),
            text_b[:60].replace("\n", " "),
        )
        return True

    def scan_results(
        self,
        results: List[dict],
        similarities: Optional[List[List[float]]] = None,
    ) -> int:
        """
        Escaneia pares dentro de uma lista de resultados.

        A telemetria (13/08/2026) grava por que NÃO flagou, não só quanto
        flagou: `data/flags/` vazio é ambíguo entre "não rodou", "abortou" e
        "rodou e nada cruzou o limiar". Ver `emit_contradiction_scan`.
        """
        if len(results) < 2:
            self._telemetria(len(results), 0, None, 0, 0, "menos_de_2")
            return 0

        flagged = 0

        if similarities is None:
            try:
                import numpy as np
                from sklearn.metrics.pairwise import cosine_similarity
                embs = []
                for r in results:
                    emb = r.get("embedding")
                    if emb is None:
                        # UM resultado sem embedding cancela o par-a-par INTEIRO.
                        self._telemetria(len(results), 0, None, 0, 0, "sem_embedding")
                        return 0
                    embs.append(np.array(emb, dtype=np.float32))
                M = np.vstack(embs)
                similarities = cosine_similarity(M).tolist()
            except Exception as e:
                logger.debug("[contradiction] sim calc falhou: %s", e)
                self._telemetria(len(results), 0, None, 0, 0, "sim_calc_falhou")
                return 0

        n_pares = 0
        n_acima = 0
        max_sim = None
        for i in range(len(results)):
            for j in range(i + 1, len(results)):
                sim = float(similarities[i][j])
                # acumulado no laço que já existe — sem segunda passada
                n_pares += 1
                if max_sim is None or sim > max_sim:
                    max_sim = sim
                if sim >= SIMILARITY_THRESHOLD:
                    n_acima += 1
                if self.check_pair(results[i], results[j], sim):
                    flagged += 1

        self._telemetria(len(results), n_pares, max_sim, n_acima, flagged, "")
        return flagged

    @staticmethod
    def _telemetria(n_resultados, n_pares, max_sim, n_acima, n_flagados, abortou):
        """Nunca levanta: telemetria não derruba retrieve."""
        try:
            from ..config import EDP_CONTRADICTION_TELEMETRY as _ct
            if _ct:
                from .pareto_store import emit_contradiction_scan
                emit_contradiction_scan(
                    n_resultados=n_resultados, n_pares=n_pares, max_sim=max_sim,
                    n_acima_do_limiar=n_acima, n_flagados=n_flagados,
                    limiar=SIMILARITY_THRESHOLD, abortou=abortou,
                )
        except Exception:
            pass

    # ── Review API ───────────────────────────────────────────────────────────

    def list_flags(
        self,
        limit:           int = 50,
        offset:          int = 0,
        filter_feedback: Optional[str] = None,
    ) -> List[dict]:
        """
        Lista flags ordenadas por detected_at descendente (mais recentes primeiro).

        Args:
            limit: máximo de flags retornadas
            offset: pula primeiros N
            filter_feedback: "unreviewed" | "useful" | "false_positive" | "ambiguous" | None (todos)
        """
        with self._lock:
            all_flags = list(self.flags)

        # Ordena por detected_at desc
        all_flags.sort(key=lambda f: f.detected_at, reverse=True)

        # Filtra
        if filter_feedback in FlagFeedback.ALL:
            all_flags = [f for f in all_flags if f.feedback == filter_feedback]

        # Paginação
        sliced = all_flags[offset:offset + limit]
        return [f.to_dict() for f in sliced]

    def get_flag(self, flag_id: str) -> Optional[dict]:
        with self._lock:
            for f in self.flags:
                if f.flag_id == flag_id:
                    return f.to_dict()
        return None

    def set_feedback(
        self,
        flag_id:  str,
        feedback: str,
        note:     str = "",
    ) -> bool:
        """
        Marca feedback humano sobre um flag.
        Persiste em disco (append) — histórico preservado.
        Returns: True se flag encontrado e atualizado.
        """
        if feedback not in FlagFeedback.ALL:
            raise ValueError(
                f"feedback inválido '{feedback}'. Valores: {FlagFeedback.ALL}"
            )

        with self._lock:
            target = None
            for f in self.flags:
                if f.flag_id == flag_id:
                    target = f
                    break
            if target is None:
                return False

            target.feedback      = feedback
            target.feedback_at   = _now()
            target.feedback_note = note[:500]   # limita tamanho da nota

        # Persiste versão atualizada (append, vence pelo flag_id mais recente)
        self._append_to_disk(target)

        logger.info(
            "[contradiction] feedback registrado | flag=%s feedback=%s note='%s'",
            flag_id, feedback, note[:80],
        )
        return True

    # ── Agregados ────────────────────────────────────────────────────────────

    def aggregates(self) -> dict:
        """
        Calcula agregados úteis para meta-análise.

        Retorna:
            - total: contagem geral
            - by_feedback: distribuição por status
            - useful_rate: % de revisados que foram úteis
            - review_rate: % de flags que foram revisados
            - avg_similarity: similaridade média
            - useful_avg_sim: similaridade média dos úteis
            - fp_avg_sim: similaridade média dos falsos positivos
            - oldest_unreviewed_age_days: idade do flag mais antigo sem review
        """
        with self._lock:
            flags = list(self.flags)

        total = len(flags)
        if total == 0:
            return {
                "total":            0,
                "by_feedback":      {},
                "useful_rate":      None,
                "review_rate":      None,
                "avg_similarity":   None,
                "useful_avg_sim":   None,
                "fp_avg_sim":       None,
                "oldest_unreviewed_age_days": None,
            }

        # Distribuição
        by_fb = Counter(f.feedback for f in flags)
        for status in FlagFeedback.ALL:
            by_fb.setdefault(status, 0)

        reviewed = total - by_fb[FlagFeedback.UNREVIEWED]
        useful   = by_fb[FlagFeedback.USEFUL]
        fp       = by_fb[FlagFeedback.FALSE_POSITIVE]

        useful_rate = (useful / reviewed) if reviewed > 0 else None
        review_rate = (reviewed / total) if total > 0 else 0.0

        # Similaridades médias
        all_sims    = [f.similarity for f in flags]
        useful_sims = [f.similarity for f in flags if f.feedback == FlagFeedback.USEFUL]
        fp_sims     = [f.similarity for f in flags if f.feedback == FlagFeedback.FALSE_POSITIVE]

        avg_sim    = sum(all_sims) / len(all_sims) if all_sims else None
        useful_sim = sum(useful_sims) / len(useful_sims) if useful_sims else None
        fp_sim     = sum(fp_sims) / len(fp_sims) if fp_sims else None

        # Idade do mais antigo unreviewed
        now = _now()
        unreviewed_ages = [
            (now - f.detected_at) / 86400.0
            for f in flags if f.feedback == FlagFeedback.UNREVIEWED
        ]
        oldest_age = max(unreviewed_ages) if unreviewed_ages else None

        return {
            "total":          total,
            "by_feedback":    dict(by_fb),
            "useful_rate":    round(useful_rate, 3) if useful_rate is not None else None,
            "review_rate":    round(review_rate, 3),
            "avg_similarity": round(avg_sim, 4) if avg_sim else None,
            "useful_avg_sim": round(useful_sim, 4) if useful_sim else None,
            "fp_avg_sim":     round(fp_sim, 4) if fp_sim else None,
            "oldest_unreviewed_age_days": (
                round(oldest_age, 2) if oldest_age is not None else None
            ),
        }

    def recent_flags(self, n: int = 10) -> List[dict]:
        """Compat com API antiga."""
        return self.list_flags(limit=n)

    def stats(self) -> dict:
        """Compat com API antiga + agregados novos."""
        with self._lock:
            base = {
                "total_flags":          len(self.flags),
                "tracked_pairs":        len(self._seen_pairs),
                "similarity_threshold": SIMILARITY_THRESHOLD,
                "max_flags_kept":       MAX_FLAGS_KEPT,
                "persist_file":         str(self.persist_file),
            }
        base.update({"aggregates": self.aggregates()})
        return base


# ── Singleton ────────────────────────────────────────────────────────────────

_flagger: Optional[ContradictionFlagger] = None
_flagger_lock = threading.Lock()


def get_flagger() -> ContradictionFlagger:
    global _flagger
    if _flagger is None:
        with _flagger_lock:
            if _flagger is None:
                _flagger = ContradictionFlagger()
    return _flagger
