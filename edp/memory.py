"""
memory.py — Hierarquia de memória cognitiva do EDP v3.
WorkingMemory: recência, volátil, tamanho fixo pequeno.
EpisodicMemory: eventos com timestamp, decay temporal.
SemanticMemory: conhecimento consolidado, alta prioridade.
MemoryStore: interface unificada compatível com v2.

PATCHES APLICADOS:
  [P5]  reinforce_memory, decay_memory, update_usage_stats movidas para
        dentro de MemoryStore (eram funções soltas com self — dead code)
  [P6]  EpisodicMemory.retrieve(): batch cosine O(n) via vstack
        (era loop com cosine_similarity individual por entry)
  [P7]  SemanticMemory: _emb_matrix cacheada, invalidada só em promote()
  [P8]  EpisodicMemory.retrieve(): lock de arquivo para evitar race condition
        de escrita concorrente no JSON (write-after-write)
  [P9]  GLOBAL_MEMORY removido de pipeline.py; MemoryStore agora é instanciado
        por chamada ou passado como argumento (sem singleton de módulo sem lock)
"""
import json
import math
import os
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from .config import (
    MEMORY_DIR, DECAY_LAMBDA, MAX_MEMORY,
    PRIORIDADE_PESO, WORKING_MEM_SIZE, EPISODIC_MEM_SIZE,
)
from .embeddings import embed_one
from .temporal import decay, access_boost, recency_rank
from .clock import now as _now, is_verified as _clock_verified  # Peça 0.2a — relógio interno robusto
from . import schema_v1 as _schema  # Peça 0.3 — schema novo
from . import metrics as M

# ── Utilitário de serialização ────────────────────────────────────────────────

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


# ── Peça 0.4 (reduzida): Lifetime state do EDP ────────────────────────────────

def _edp_lifetime_path() -> Path:
    """Caminho do arquivo que guarda o estado vitalício do EDP."""
    return MEMORY_DIR / "edp_lifetime.json"


def _get_edp_lifetime() -> dict:
    """
    Retorna {edp_session_id, edp_session_start} do EDP.

    Se não existir (primeiro entry da vida do EDP): cria, salva, retorna.
    Esta é a "ignição": acontece UMA vez na vida do EDP, e os valores
    permanecem constantes até a morte do usuário/EDP.

    Retorno:
        dict com chaves edp_session_id (str uuid) e edp_session_start (float t_absolute)
    """
    path = _edp_lifetime_path()

    # Tenta carregar existente
    if path.exists():
        try:
            data = _safe_load_json(path)
            if data and "edp_session_id" in data and "edp_session_start" in data:
                return data
        except Exception:
            pass  # Se corrompido, recria abaixo

    # Não existe ou está corrompido: cria agora (ignição)
    lifetime = {
        "edp_session_id":    str(uuid.uuid4()),
        "edp_session_start": _now(),
    }
    try:
        _atomic_write_json(path, lifetime, indent=2)
    except Exception:
        # Mesmo se não conseguir persistir, retorna o valor gerado
        # (próxima chamada vai tentar de novo)
        pass

    return lifetime


def _new_entry(
    text: str,
    score: float,
    prioridade: str,
    source: str = "user",
    confidence: Optional[float] = None,
    epistemic_status: str = "hypothesis",
    derived_from: Optional[List[str]] = None,
) -> dict:
    """
    Cria entrada de memória com provenance + embedding versioning.

    Campos novos (governança epistêmica, INVARIANTE 3 e 4):
      source:           "user" | "llm:<model>" | "tool:<name>" | "system"
      confidence:       0.0-1.0 — None = inferido do score
      epistemic_status: "hypothesis" | "verified" | "contested" | "quarantine"
      derived_from:     lista de IDs de memórias pai (genealogia)

    Campos de versioning (PROBLEMA OCULTO 1 — embedding drift):
      embedding_model:  nome do modelo usado
      embedding_version: versão do modelo
    """
    # Peça 0.4 (reduzida): carrega lifetime do EDP (ignição na primeira chamada)
    # Carregado ANTES de 'agora' para garantir session_start <= t_absolute do entry
    _edp_lifetime = _get_edp_lifetime()

    agora = _now()
    # Confidence: se não fornecido, deriva do score (clamp 0..1)
    if confidence is None:
        confidence = max(0.0, min(1.0, float(score)))

    # Identifica modelo de embedding (lazy import para evitar ciclo)
    emb_model   = "all-MiniLM-L6-v2"
    emb_version = "1.0.0"
    try:
        from .config import EMBED_MODEL, EMBED_MODEL_VERSION
        emb_model   = EMBED_MODEL
        emb_version = EMBED_MODEL_VERSION
    except Exception:
        pass

    # Auto-classificação de source_type (governança de retrieval)
    try:
        from .memory_classifier import classify_memory
        source_type = classify_memory(text, source)
    except Exception:
        source_type = "unknown"

    return {
        "id":            str(uuid.uuid4()),
        "text":          text,
        "embedding":     embed_one(text),
        "timestamp":     agora,
        "score_inicial": round(float(score), 4),
        "acessos":       0,
        "ultimo_acesso": agora,
        "prioridade":    prioridade,
        "layer":         "episodic",

        # ── Provenance (governança) ──────────────────────────────────────────
        "source":            source,
        "source_type":       source_type,    # ← NOVO: auto-classificado
        "confidence":        round(confidence, 4),
        "epistemic_status":  epistemic_status,
        "derived_from":      list(derived_from or []),

        # ── Embedding versioning (drift detection) ───────────────────────────
        "embedding_model":   emb_model,
        "embedding_version": emb_version,

        # ── Schema v1 (Peça 0.3) ─────────────────────────────────────────────
        # Camada (i) tempo absoluto: t_absolute redundante com 'timestamp'
        # mas explícito para legibilidade
        _schema.FIELD_T_ABSOLUTE:                agora,
        # Peça 0.4 (reduzida): sessão do EDP — vitalícia
        # Carregada uma vez na ignição, constante até a morte
        _schema.FIELD_EDP_SESSION_ID:            _edp_lifetime["edp_session_id"],
        _schema.FIELD_EDP_SESSION_START:         _edp_lifetime["edp_session_start"],
        # Peça 2.0: bloco — preenchido em MemoryStore.add via link_entry_to_active_block
        _schema.FIELD_BLOCK_ID:                  None,
        # Camada (ii) tempo vivencial: campos esqueleto, preenchidos em 0.4
        _schema.FIELD_T_USER_SESSION_START:      None,
        _schema.FIELD_T_USER_TURN_N:             None,
        _schema.FIELD_T_MODEL_CONTEXT_START:     None,
        _schema.FIELD_T_MODEL_TURN_N:            None,
        _schema.FIELD_GAP_BEFORE:                None,   # calculado em add()
        _schema.FIELD_GAP_CAUSE:                 None,   # peça 2 preenche
        _schema.FIELD_GAP_RESOLUTION:            None,   # peça 2 preenche
        # Camada (iii) origem do conhecimento
        _schema.FIELD_ORIGIN:                    _schema.ORIGIN_MEASURED,
        _schema.FIELD_T_LOADED:                  None,   # só p/ ORIGIN_REFERENCE
        _schema.FIELD_REFERENCE_SOURCE:          None,   # só p/ ORIGIN_REFERENCE
        _schema.FIELD_INTERNAL_TEMPORAL_CLAIMS:  None,   # só p/ ORIGIN_REFERENCE
        # Confiabilidade temporal: True se clock estava em fallback
        _schema.FIELD_TEMPORAL_UNRELIABLE:       (not _clock_verified()),
        # ── Peça 2.5c.2 (Buraco 3, parte 2): âncora epistêmica ─────────────
        # True quando o texto contém admissão de limite em prosa natural
        # ("Não tenho base sólida", "Não encontro referência clara", etc).
        # Preenchido em MemoryStore.add via epistemic_classifier.
        # Retrieval aplica anchor_boost=1.20 (empata com source_type "external"
        # — admissão de ignorância vale como autoridade externa não-inflada).
        # Backward-compatible: entries antigos sem o campo → False → mult=1.0.
        "is_epistemic_anchor":                   False,
        # Versão do schema (sempre presente em novos entries)
        _schema.FIELD_SCHEMA_VERSION:            _schema.SCHEMA_VERSION,
    }

# ── Working Memory ─────────────────────────────────────────────────────────────

class WorkingMemory:
    """
    Memória de trabalho: volátil, baseada em recência.
    Tamanho fixo pequeno. Não persiste em disco.
    """

    def __init__(self, max_size: int = WORKING_MEM_SIZE):
        self.max_size = max_size
        self._buffer: list[dict] = []

    def add(self, entry: dict) -> None:
        entry = dict(entry)
        entry["layer"] = "working"
        self._buffer.append(entry)
        if len(self._buffer) > self.max_size:
            self._buffer.pop(0)

    def retrieve(self, top_k: int = 5) -> list[dict]:
        return recency_rank(self._buffer, top_k)

    def flush(self) -> list[dict]:
        out = list(self._buffer)
        self._buffer.clear()
        return out

    def __len__(self) -> int:
        return len(self._buffer)


# ── Episodic Memory ───────────────────────────────────────────────────────────

class EpisodicMemory:
    """
    Memória episódica: eventos com timestamp, decay temporal.
    Persiste em disco. Poda automática por score composto.
    [P8] Threading lock para proteger save() de race conditions.
    """

    def __init__(self, session_id: str, max_size: int = EPISODIC_MEM_SIZE):
        self.session_id = session_id
        self.max_size   = max_size
        self._lock      = threading.Lock()  # [P8] proteção de escrita em disco
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self.path    = MEMORY_DIR / f"{session_id}_episodic.json"
        self.entries: list[dict] = []
        self._dirty:          bool = False   # [WAL-FIX] batch persistence
        self._pending_writes: int  = 0
        self._batch_size:     int  = 50      # flush a cada 50 inserções
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            # Peça 0.3.1: usa _safe_load_json que tolera JSON corrompido por write parcial
            data = _safe_load_json(self.path)
            if data is not None:
                self.entries = _deserialize(data)

    def save(self) -> None:
        # [P8] Lock garante que writes concorrentes não corrompem o JSON
        # Peça 0.3.1: write atômico via tmp + fsync + rename
        with self._lock:
            _atomic_write_json(self.path, _serialize(self.entries))

    def add(self, entry: dict) -> None:
        entry = dict(entry)
        entry["layer"] = "episodic"
        self.entries.append(entry)
        if len(self.entries) > self.max_size:
            self._prune()
        # [WAL-FIX] batch mode: não salva em cada add — marca dirty
        # save() explícito chamado por: flush(), retrieve(), _prune(), shutdown
        self._dirty = True
        self._pending_writes += 1
        if self._pending_writes >= self._batch_size:
            self.save()
            self._pending_writes = 0

    def retrieve(
        self,
        query_emb: np.ndarray,
        top_k: int = 5,
        min_score: float = 0.20,
        respect_epistemic: bool = True,
    ) -> list[dict]:
        """
        [P6] FIX O(n) batch cosine:
        Antes: loop com cosine_similarity([emb_i], [query]) por entry — n chamadas individuais.
        Agora: vstack todos embeddings → 1 chamada cosine_similarity(matrix, [query]).
        Para n=500 entries: 500x menos chamadas sklearn.

        [P1-v3.5] Epistemic governance:
            respect_epistemic=True (default) aplica as regras:
              - REMOVE: contradicted, quarantined
              - PENALIZA score: stale (×0.5), hypothesis (×0.85)
              - PRESERVA: verified (×1.0)
              - Status desconhecido (legacy entries) tratado como hypothesis

            Set respect_epistemic=False para retorno raw (debug, migração).
            Retrocompatível: entries sem campo epistemic_status são tratadas
            como hypothesis (default conservador).
        """
        if not self.entries:
            return []

        # Batch: extrai todos embeddings de uma vez
        emb_matrix = np.vstack([
            np.array(e["embedding"], dtype=np.float32)
            for e in self.entries
        ])
        sims = cosine_similarity(emb_matrix, [query_emb]).flatten()

        agora = _now()
        scored = []
        skipped_blocked = 0     # contagem de filtradas por epistemic_status

        # ── Sprint v3.6: Source-type weighting + dominance penalty ──────────
        # Penaliza memórias hiperdominantes (top-3 por acessos).
        # Reduz score de meta_conversation (governança anti-loop).
        from .memory_classifier import get_source_weight

        # Identifica top-3 mais acessadas (dominance penalty)
        # Só penaliza se total_retrievals > 20 (evita penalizar sistema vazio)
        total_retrievals = sum(e.get("acessos", 0) for e in self.entries)
        dominant_ids: set[str] = set()
        if total_retrievals >= 20:
            top_by_access = sorted(
                self.entries,
                key=lambda x: x.get("acessos", 0),
                reverse=True,
            )[:3]
            # Só conta como dominante se concentra >12% sozinha
            for top_e in top_by_access:
                acc = top_e.get("acessos", 0)
                if acc / max(total_retrievals, 1) >= 0.12:
                    dominant_ids.add(top_e.get("id", ""))

        for i, (e, sim) in enumerate(zip(self.entries, sims)):
            # ── Epistemic governance (P1) ────────────────────────────────────
            epi_multiplier = 1.0
            if respect_epistemic:
                status = e.get("epistemic_status", "hypothesis")
                if status in ("contradicted", "quarantined"):
                    skipped_blocked += 1
                    continue   # NÃO retorna nunca
                elif status == "stale":
                    epi_multiplier = 0.5
                elif status == "hypothesis":
                    epi_multiplier = 0.85
                # "verified" e qualquer outro → 1.0

            # ── Source-type weighting (Sprint v3.6) ──────────────────────────
            # Retrocompatível: entries sem source_type → peso 1.0 (neutro)
            src_type = e.get("source_type")
            src_weight = get_source_weight(src_type) if src_type else 1.0

            # ── Dominance penalty ─────────────────────────────────────────────
            # Memória hiperdominante leva multiplicador 0.7 (não bloqueio)
            dom_penalty = 0.70 if e.get("id") in dominant_ids else 1.0

            # ── Peça 2.5c.2 (Buraco 3, parte 2): anchor_boost ─────────────────
            # Entries marcados como âncora epistêmica (admissão de limite em
            # prosa natural detectada na gravação) ganham boost 1.20 — empata
            # com source_type "external". Conceito: admissão de ignorância do
            # próprio modelo vale como autoridade externa não-inflada.
            # Backward-compat: campo ausente → False → multiplicador 1.0.
            anchor_boost = 1.20 if e.get("is_epistemic_anchor") else 1.0

            d    = decay(e["ultimo_acesso"])
            prio = PRIORIDADE_PESO.get(e["prioridade"], 1.0)
            ab   = access_boost(e["acessos"])
            rank_score = round(
                float(sim) * d * prio * ab
                * epi_multiplier * src_weight * dom_penalty * anchor_boost,
                4,
            )
            if rank_score >= min_score:
                scored.append((rank_score, i, {
                    "sim": float(sim), "decay": d, "prio": prio,
                    "access_boost": ab, "epi_mult": epi_multiplier,
                    "src_weight": src_weight, "dom_penalty": dom_penalty,
                    "anchor_boost": anchor_boost,
                }))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Log apenas se removeu algo (acionável)
        if skipped_blocked > 0:
            try:
                import logging as _lg
                _lg.getLogger("edp.memory").info(
                    "[retrieve] epistemic | bloqueadas=%d (contradicted+quarantined)",
                    skipped_blocked,
                )
            except Exception:
                pass

        results = []
        for rank_score, i, breakdown in scored[:top_k]:
            entry = self.entries[i]
            entry["acessos"]       += 1
            entry["ultimo_acesso"]  = agora
            results.append({
                **entry,
                "ranking_score":      rank_score,
                "ranking_breakdown":  breakdown,    # debug: explica score
            })

        if self._dirty:
            self.save()
        return results

    def _rank(self, e: dict, query_emb: np.ndarray) -> float:
        """Mantido para compatibilidade retroativa — internamente não usado em retrieve()."""
        emb  = np.array(e["embedding"], dtype=np.float32)
        sim  = float(cosine_similarity([emb], [query_emb])[0][0])
        d    = decay(e["ultimo_acesso"])
        prio = PRIORIDADE_PESO.get(e["prioridade"], 1.0)
        ab   = access_boost(e["acessos"])
        return round(sim * d * prio * ab, 4)


    def flush(self) -> None:
        """[WAL-FIX] Força escrita em disco de todas as mudanças pendentes."""
        if self._dirty or self._pending_writes > 0:
            self.save()
            self._pending_writes = 0
            self._dirty = False

    def _prune(self) -> None:
        self.entries.sort(
            key=lambda e: (
                e["score_inicial"]
                * decay(e["ultimo_acesso"])
                * PRIORIDADE_PESO.get(e["prioridade"], 1.0)
            ),
            reverse=True,
        )
        self.entries = self.entries[: self.max_size]

    def all_embeddings(self) -> np.ndarray:
        if not self.entries:
            return np.array([])
        return np.vstack([np.array(e["embedding"], dtype=np.float32) for e in self.entries])

    # ── Governança (Memory Review System) ──────────────────────────────────

    def list_entries(
        self,
        limit: int = 100,
        offset: int = 0,
        filter_status: Optional[str] = None,
        filter_source: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[dict]:
        """
        Lista entries com filtros, sem retornar embeddings (payload grande).

        Args:
            filter_status: epistemic_status para filtrar (verified, hypothesis,
                          stale, contradicted, quarantined). None = todos.
            filter_source: source para filtrar (user, llm:*, tool:*). None = todos.
            search: substring no texto (case-insensitive). None = sem busca.
        """
        # Ordena por timestamp desc (mais recentes primeiro)
        sorted_entries = sorted(
            self.entries,
            key=lambda e: e.get("timestamp", 0),
            reverse=True,
        )

        # Aplica filtros
        if filter_status:
            sorted_entries = [
                e for e in sorted_entries
                if e.get("epistemic_status", "hypothesis") == filter_status
            ]
        if filter_source:
            sorted_entries = [
                e for e in sorted_entries
                if filter_source in (e.get("source") or "")
            ]
        if search:
            search_lower = search.lower()
            sorted_entries = [
                e for e in sorted_entries
                if search_lower in (e.get("text") or "").lower()
            ]

        # Paginação
        sliced = sorted_entries[offset:offset + limit]

        # Remove embedding (não serializa para frontend - lista demais)
        result = []
        for e in sliced:
            entry_copy = {k: v for k, v in e.items() if k != "embedding"}
            result.append(entry_copy)
        return result

    def get_entry(self, entry_id: str) -> Optional[dict]:
        """Retorna entry por ID (sem embedding)."""
        for e in self.entries:
            if e.get("id") == entry_id:
                return {k: v for k, v in e.items() if k != "embedding"}
        return None

    def update_entry(
        self,
        entry_id: str,
        text: Optional[str] = None,
        epistemic_status: Optional[str] = None,
        confidence: Optional[float] = None,
        prioridade: Optional[str] = None,
        source_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        note: Optional[str] = None,
    ) -> bool:
        """
        Atualiza campos de uma entry.

        IMPORTANTE: Se text muda, o embedding fica defasado.
        Não recomputamos automaticamente (custo de API/CPU).
        Caller é responsável por marcar como 'stale' se quiser.

        Para atualizar text + reembed: use o endpoint POST /memory/{id}/reembed.

        Returns: True se entry encontrada e atualizada.
        """
        VALID_STATUS = ("verified", "hypothesis", "stale", "contradicted", "quarantined")
        VALID_SOURCE_TYPES = (
            "user_input", "llm_response", "meta_conversation",
            "session_summary", "external", "unknown",
            "camara_response",  # Peça 2.4a.7: texto refinado pela câmara de eco
        )

        for e in self.entries:
            if e.get("id") != entry_id:
                continue

            # Atualiza apenas campos não-None
            if text is not None:
                e["text"] = text
                # Marca que embedding ficou potencialmente defasado
                e["text_edited_at"] = _now()
            if epistemic_status is not None:
                if epistemic_status not in VALID_STATUS:
                    raise ValueError(
                        f"epistemic_status inválido '{epistemic_status}'. "
                        f"Válidos: {VALID_STATUS}"
                    )
                e["epistemic_status"] = epistemic_status
            if confidence is not None:
                e["confidence"] = max(0.0, min(1.0, float(confidence)))
            if prioridade is not None:
                e["prioridade"] = prioridade
            if source_type is not None:
                if source_type not in VALID_SOURCE_TYPES:
                    raise ValueError(
                        f"source_type inválido '{source_type}'. "
                        f"Válidos: {VALID_SOURCE_TYPES}"
                    )
                e["source_type"] = source_type
            if tags is not None:
                e["tags"] = list(tags)[:20]  # limita
            if note is not None:
                e["human_note"] = str(note)[:500]

            # Sempre atualiza updated_at
            e["updated_at"] = _now()
            self._dirty = True
            self.save()
            return True
        return False

    def delete_entry(self, entry_id: str) -> bool:
        """Remove uma entry por ID. Retorna True se removida."""
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.get("id") != entry_id]
        if len(self.entries) < before:
            self._dirty = True
            self.save()
            return True
        return False

    def delete_by_filter(
        self,
        filter_status: Optional[str] = None,
        filter_source: Optional[str] = None,
        confirm: bool = False,
    ) -> int:
        """
        Apaga em batch por filtro. PERIGOSO — requer confirm=True.

        Returns: número de entries removidas.
        """
        if not confirm:
            raise ValueError(
                "delete_by_filter requer confirm=True para evitar exclusões acidentais"
            )
        if not filter_status and not filter_source:
            raise ValueError(
                "delete_by_filter requer pelo menos 1 filtro (status ou source)"
            )

        before = len(self.entries)
        keep = []
        for e in self.entries:
            should_delete = True
            if filter_status:
                if e.get("epistemic_status", "hypothesis") != filter_status:
                    should_delete = False
            if filter_source and should_delete:
                if filter_source not in (e.get("source") or ""):
                    should_delete = False
            if not should_delete:
                keep.append(e)

        removed = before - len(keep)
        if removed > 0:
            self.entries = keep
            self._dirty = True
            self.save()
        return removed

    def stats_by_status(self) -> dict:
        """Conta entries por epistemic_status (para dashboard)."""
        from collections import Counter
        counter = Counter(
            e.get("epistemic_status", "hypothesis") for e in self.entries
        )
        return dict(counter)

    def __len__(self) -> int:
        return len(self.entries)


# ── Semantic Memory ───────────────────────────────────────────────────────────

class SemanticMemory:
    """
    Memória semântica: conhecimento consolidado, alta durabilidade.
    [P7] _emb_matrix cacheada — reconstruída apenas quando entries muda.
    """

    def __init__(self, session_id: str):
        self.session_id  = session_id
        self._lock       = threading.Lock()
        self._emb_cache: np.ndarray | None = None  # [P7] cache de matrix
        self._cache_dirty = True
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self.path    = MEMORY_DIR / f"{session_id}_semantic.json"
        self.entries: list[dict] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            # Peça 0.3.1: usa _safe_load_json
            data = _safe_load_json(self.path)
            if data is not None:
                self.entries = _deserialize(data)
        self._cache_dirty = True

    def save(self) -> None:
        # Peça 0.3.1: write atômico
        with self._lock:
            _atomic_write_json(self.path, _serialize(self.entries))

    def promote(self, entry: dict) -> None:
        """Promove uma entrada episódica para memória semântica."""
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


# ── MemoryStore — interface unificada (compatível com v2) ─────────────────────

class MemoryStore:
    """
    Interface unificada de memória.
    Compatível com v2 e expõe as três camadas.

    [P5] reinforce_memory, decay_memory, update_usage_stats
         movidas para cá como métodos de instância.
    """

    def __init__(self, session_id: str):
        self.session_id  = session_id
        self.working     = WorkingMemory()
        self.episodic    = EpisodicMemory(session_id)
        self.semantic    = SemanticMemory(session_id)

        # Peça 2.0: BlockManager — gerencia blocos da sessão
        from .blocks import BlockManager
        self.blocks      = BlockManager(session_id, MEMORY_DIR)

        print(
            f"[Memory] Sessão '{session_id}' carregada "
            f"(episódica={len(self.episodic)} | semântica={len(self.semantic)} "
            f"| blocos={len(self.blocks.blocks)})"
        )

    # ── v2-compat ──────────────────────────────────────────────────────────────

    @property
    def entries(self) -> list[dict]:
        return self.episodic.entries

    @entries.setter
    def entries(self, value: list[dict]) -> None:
        self.episodic.entries = value

    def save(self) -> None:
        self.episodic.save()
        self.semantic.save()

    def all_embeddings(self) -> np.ndarray:
        return self.episodic.all_embeddings()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def add(
        self,
        text: str,
        score: float,
        prioridade: str = "media",
        to_working: bool = True,
        source: str = "user",
        confidence: Optional[float] = None,
        epistemic_status: str = "hypothesis",
        derived_from: Optional[List[str]] = None,
    ) -> dict:
        """
        Adiciona memória. Provenance opcional para retrocompatibilidade total.

        Parâmetros novos (governança epistêmica):
            source: "user" (default), "llm:<model>", "tool:<name>", "system"
            confidence: 0.0-1.0 — None = deriva de score
            epistemic_status: "hypothesis" (default), "verified", "contested"
            derived_from: lista de IDs pai (rastreabilidade)
        """
        if prioridade not in PRIORIDADE_PESO:
            prioridade = "media"
        with M.timer("memory_add"):
            entry = _new_entry(
                text, score, prioridade,
                source=source,
                confidence=confidence,
                epistemic_status=epistemic_status,
                derived_from=derived_from,
            )

        # ── Peça 0.3: calcula gap_before ────────────────────────────────────
        # gap_before = t_absolute deste entry - t_absolute mais recente entre
        # entries 'measured' já presentes em episodic.
        # Tolerância: falha de cálculo não bloqueia gravação.
        try:
            if entry.get(_schema.FIELD_ORIGIN, _schema.ORIGIN_MEASURED) == _schema.ORIGIN_MEASURED:
                cur_ts = entry.get(_schema.FIELD_T_ABSOLUTE, entry.get("timestamp"))
                if cur_ts is not None and self.episodic.entries:
                    prev_ts = max(
                        (e.get(_schema.FIELD_T_ABSOLUTE, e.get("timestamp", 0)) or 0)
                        for e in self.episodic.entries
                        if e.get(_schema.FIELD_ORIGIN, _schema.ORIGIN_MEASURED) == _schema.ORIGIN_MEASURED
                    )
                    if prev_ts and prev_ts < cur_ts:
                        entry[_schema.FIELD_GAP_BEFORE] = float(cur_ts - prev_ts)
        except Exception:
            pass

        # ── Peça 2.0: vincula entry ao bloco aberto atual ──────────────────
        # Apenas para entries 'measured' — entries 'reference' (peça 4) ficam
        # sem block_id (são histórico importado, não conversa viva).
        try:
            if entry.get(_schema.FIELD_ORIGIN, _schema.ORIGIN_MEASURED) == _schema.ORIGIN_MEASURED:
                edp_sid = entry.get(_schema.FIELD_EDP_SESSION_ID)
                if edp_sid:
                    block_id = self.blocks.link_entry_to_active_block(
                        entry_id=entry["id"],
                        edp_session_id=edp_sid,
                    )
                    entry[_schema.FIELD_BLOCK_ID] = block_id
        except Exception:
            # Tolerância: falha de bloco não bloqueia gravação
            pass

        # ── Peça 2.5c.2 (Buraco 3, parte 2): detecta âncora epistêmica ─────
        # Analisa o texto para detectar admissão de limite em prosa natural
        # ("Não tenho base sólida", "Não encontro referência clara", etc).
        # Caso real motivador: Bayes/Turing (modelo disse "não tenho base
        # sólida" em prosa natural, sem disparar auto-sinal da câmara, e dois
        # turnos depois cedeu à pressão porque a admissão não viajou no
        # retrieval). Marcar agora permite que esses turnos ganhem boost no
        # retrieval futuro (anchor_boost=1.20 em EpisodicMemory.retrieve).
        # Política: NÃO bloqueia, NÃO altera texto. Só marca metadado.
        # Tolerância: falha do classificador não bloqueia gravação.
        try:
            from .epistemic_classifier import detectar_admissao_em_prosa
            if detectar_admissao_em_prosa(text):
                entry["is_epistemic_anchor"] = True
        except Exception:
            pass

        if to_working:
            self.working.add(entry)
        self.episodic.add(entry)
        if len(self.episodic) > MAX_MEMORY:
            self.episodic._prune()
        M.memory_size(len(self.episodic))
        return entry

    def get(self, entry_id: str) -> dict | None:
        for e in self.episodic.entries + self.semantic.entries:
            if e["id"] == entry_id:
                return e
        return None

    def delete(self, entry_id: str) -> bool:
        before = len(self.episodic.entries)
        self.episodic.entries = [
            e for e in self.episodic.entries if e["id"] != entry_id
        ]
        if len(self.episodic.entries) < before:
            self.episodic.save()
            return True
        return False

    def set_prioridade(self, entry_id: str, prioridade: str) -> None:
        if prioridade not in PRIORIDADE_PESO:
            raise ValueError(f"Prioridade inválida: {prioridade}")
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(f"Entrada '{entry_id}' não encontrada")
        entry["prioridade"] = prioridade
        self.save()

    # ── Retrieval unificado ───────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.20,
        layers: list[str] | None = None,
    ) -> list[dict]:
        from .embeddings import embed_one as _e1
        query_emb = _e1(query)
        layers    = layers or ["working", "episodic", "semantic"]
        results: list[dict] = []

        if "working" in layers:
            for e in self.working.retrieve(top_k=top_k):
                emb = np.array(e["embedding"], dtype=np.float32)
                sim = float(cosine_similarity([emb], [query_emb])[0][0])
                if sim >= min_score:
                    results.append({**e, "ranking_score": round(sim, 4)})

        if "episodic" in layers:
            results.extend(self.episodic.retrieve(query_emb, top_k, min_score))

        if "semantic" in layers:
            results.extend(self.semantic.retrieve(query_emb, top_k, min_score))

        seen: dict[str, dict] = {}
        for r in results:
            eid = r["id"]
            if eid not in seen or r["ranking_score"] > seen[eid]["ranking_score"]:
                seen[eid] = r

        final = sorted(seen.values(), key=lambda x: x["ranking_score"], reverse=True)
        for r in final:
            M.retrieval_score(r["ranking_score"])

        final_top = final[:top_k]

        # ── P2: Retrieval quality monitor (não-bloqueante) ───────────────────
        try:
            from .runtime.retrieval_monitor import get_monitor
            top_scores = [r["ranking_score"] for r in final_top]
            top_ids    = [r.get("id", "") for r in final_top]
            get_monitor().record_turn(top_scores=top_scores, result_ids=top_ids)
        except Exception:
            pass  # nunca quebra retrieve por causa de monitor

        # ── P3: Contradiction flagging (não-bloqueante) ──────────────────────
        # Só executa se top_k >= 2 (precisa pares para comparar)
        if len(final_top) >= 2:
            try:
                from .runtime.contradiction_flagger import get_flagger
                get_flagger().scan_results(final_top)
            except Exception:
                pass  # nunca quebra retrieve

        return final_top

    # ── Poda manual ───────────────────────────────────────────────────────────

    def prune(self, keep: int | None = None) -> dict:
        keep   = keep or MAX_MEMORY
        before = len(self.episodic.entries)
        self.episodic._prune()
        after  = len(self.episodic.entries)
        self.episodic.save()
        return {"before": before, "after": after, "removed": before - after}

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "session_id":    self.session_id,
            "working":       len(self.working),
            "episodic":      len(self.episodic),
            "semantic":      len(self.semantic),
            "total":         len(self.episodic) + len(self.semantic),
            "max_episodic":  EPISODIC_MEM_SIZE,
            "max_total":     MAX_MEMORY,
        }

    # ── Relatório (v2-compat) ─────────────────────────────────────────────────

    def report(self) -> str:
        lines = [
            f"{'='*60}",
            f"MEMÓRIA — {self.session_id} "
            f"({len(self.episodic)}/{MAX_MEMORY})",
            f"{'='*60}",
        ]
        for e in sorted(
            self.episodic.entries,
            key=lambda x: decay(x["ultimo_acesso"]),
            reverse=True,
        ):
            d    = decay(e["ultimo_acesso"])
            dias = (_now() - e["ultimo_acesso"]) / 86_400
            lines.append(
                f"  [{e['prioridade']:5s}] "
                f"decay={d:.3f} | "
                f"acessos={e['acessos']:2d} | "
                f"há {dias:.1f}d | "
                f"{e['text'][:55]}"
            )
        lines.append(f"{'='*60}")
        return "\n".join(lines)

    # ── Sessions ──────────────────────────────────────────────────────────────

    @staticmethod
    def list_sessions() -> list[str]:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        stems = set()
        for p in MEMORY_DIR.glob("*.json"):
            name = p.stem
            for suf in ("_episodic", "_semantic"):
                if name.endswith(suf):
                    name = name[: -len(suf)]
            stems.add(name)
        return sorted(stems)

    @staticmethod
    def delete_session(session_id: str) -> bool:
        deleted = False
        for suf in ("_episodic.json", "_semantic.json", ".json"):
            p = MEMORY_DIR / f"{session_id}{suf}"
            if p.exists():
                p.unlink()
                deleted = True
        return deleted

    # ── [P5] Métodos de reforço/decay — movidos para dentro da classe ─────────

    def reinforce_memory(self, entry_id: str, boost: float = 0.10) -> bool:
        """
        [P5] Era função solta (reinforce_memory(self, ...)) — dead code garantido.
        Agora é método de instância de MemoryStore.
        Reforça score e atualiza timestamp de acesso.
        """
        entry = self.get(entry_id)
        if entry is None:
            return False
        entry["score_inicial"] = round(min(entry.get("score_inicial", 0.5) + boost, 1.0), 4)
        entry["acessos"]       = entry.get("acessos", 0) + 1
        entry["ultimo_acesso"] = _now()
        self.save()
        return True

    def decay_memory(self, entry_id: str, factor: float = 0.50) -> bool:
        """
        [P5] Era função solta — dead code garantido.
        Aplica fator de decay ao score_inicial de uma entrada.
        """
        entry = self.get(entry_id)
        if entry is None:
            return False
        entry["score_inicial"] = round(max(entry.get("score_inicial", 0.5) * factor, 0.01), 4)
        self.save()
        return True

    def update_usage_stats(self) -> dict:
        """
        [P5] Era função solta — dead code garantido.
        Promove entradas frequentes, demove inativas, remove scores zerados.
        Retorna relatório de mudanças.
        """
        promoted = 0
        demoted  = 0
        removed  = 0
        surviving: list[dict] = []

        for e in self.episodic.entries:
            if e.get("score_inicial", 0.5) < 0.01:
                removed += 1
                continue
            a    = e.get("acessos", 0)
            prio = e.get("prioridade", "media")
            if a >= 10 and prio != "alta":
                e["prioridade"] = "alta"
                promoted += 1
            elif a == 0 and prio == "alta":
                e["prioridade"] = "media"
                demoted += 1
            surviving.append(e)

        self.episodic.entries = surviving
        self.episodic.save()
        return {
            "total":    len(surviving),
            "removed":  removed,
            "promoted": promoted,
            "demoted":  demoted,
        }
