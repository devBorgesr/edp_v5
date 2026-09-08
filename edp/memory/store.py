"""
edp.memory.store — WorkingMemory, EpisodicMemory, _ScopedView, MemoryStore.

Fase 4 T3 (extração 3/3, final): extraído verbatim de memory.py
(WorkingMemory, EpisodicMemory, migração legacy, _ScopedView, MemoryStore —
posições originais antes do split; MOVE-ONLY, corpos de função byte-
idênticos ao original — só esta docstring e os imports são novos).

CHOKE-POINT (item G do adendo do pesquisador — desenho intencional, não
acidente): o piso NOT_FOUND_FLOOR (EDP_TOXIC_GUARDS desde fix/toxic-guards;
EDP_WRITE_PROVENANCE governa só a escrita do carimbo — ver config.py e
ACHADO_FLAG_UNICA_TOXICIDADE.md do lab_edp), ver
EpisodicMemory.retrieve() abaixo, import local de NOT_FOUND_FLOOR/
TOXIC_ANSWER_CLASSES) e a exclusão do índice híbrido (ver
MemoryStore._hybrid_index(), mesmo import local) SÃO OS DOIS PONTOS ONDE
answer_class tóxico ("not_found" | "disqualification") é aplicado como
defesa — e ficam de propósito no MESMO módulo. Separá-los em arquivos
diferentes é PROIBIDO (por isso EpisodicMemory e MemoryStore NÃO foram
splitados em episodic.py + store.py separados, ver relato da Fase 4 T3 —
desvio do corte proposto originalmente). Quando o piso for estendido para
SemanticMemory (Dívida documentada, ver edp/memory/semantic.py), o
one-liner equivalente entra em SemanticMemory.retrieve() — módulo
diferente deste, mas o par piso/exclusão-híbrida que JÁ existe continua
adjacente aqui.
"""
import logging
import threading
import uuid
from pathlib import Path
from typing import List, Optional

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from ..config import (
    MEMORY_DIR, DECAY_LAMBDA, MAX_MEMORY,
    PRIORIDADE_PESO, WORKING_MEM_SIZE, EPISODIC_MEM_SIZE,
)
from ..embeddings import embed_one
from ..temporal import decay, access_boost, recency_rank
from ..clock import now as _now, is_verified as _clock_verified  # Peça 0.2a — relógio interno robusto
from .. import schema_v1 as _schema  # Peça 0.3 — schema novo
from .. import metrics as M

from .atomic_io import _atomic_write_json, _safe_load_json, _load_json_or_quarantine, _serialize, _deserialize
from .semantic import SemanticMemory

logger = logging.getLogger("edp.memory")

# ── Commit 3c.β (Renato, 04/06/2026) ──────────────────────────────────────────
# Constantes do session_marker persistente.
#
# SESSION_GAP_THRESHOLD_SEC: fronteira de sessão diária. Gap > 4h entre dois
# entries consecutivos define que estão em sessões diferentes. Mesmo valor
# usado no llm_adapter.py para detecção dinâmica de sessão atual (3c.α) —
# consistência arquitetural.
#
# SESSION_BOOST_FACTOR: multiplicador aplicado ao ranking_score no retrieval
# quando entry.session_marker == current_session_marker.
# OUT_OF_SESSION_PENALTY: multiplicador aplicado quando entry TEM marker mas
# é DIFERENTE do atual (sessão antiga conhecida).
#
# CALIBRAÇÃO EMPÍRICA (Commit 3c.β-cal, Renato 04/06/2026):
#   Valor inicial 1.30 mostrou-se insuficiente em produção:
#     - Pergunta: "qual escolher pra cache de sessões web?"
#     - Sessão atual: discussão de Redis/Memcached
#     - Memória legacy: Docker/Podman (sim semântica alta)
#     - Resultado: modelo alucinou Docker em vez de Redis (INADMISSÍVEL)
#   Decisão: boost 1.60 + penalty 0.85 (Opção P1, aprovada Renato 04/06):
#     - Memória atual: ×1.60 (era ×1.30 — +23%)
#     - Memória sessão antiga conhecida: ×0.85 (era ×1.0 — -15%)
#     - Memória legacy sem marker: ×1.00 (preservada para backward-compat)
#     - Diferenciação atual/antiga: ×1.88 (era ×1.30 — +45%)
#   Por que essa combinação > boost 2.00 puro:
#     - Preserva acesso a memórias legacy valiosas (sem marker = neutro)
#     - Cria assimetria nuançada (boost forte + penalty sutil)
#     - Não ofusca memórias antigas semanticamente muito superiores
#   Princípio aplicado: Solidificação Iterativa — dados empíricos sobrescrevem
#   palpite inicial. Commit 4 (Gauss) calibrará empiricamente no futuro
#   baseado em distribuição real de similaridades.
SESSION_GAP_THRESHOLD_SEC = 4 * 3600   # 4h
SESSION_BOOST_FACTOR      = 1.60       # calibrado 04/06/2026 (era 1.30)
OUT_OF_SESSION_PENALTY    = 0.85       # NOVO 04/06/2026 — sessão antiga conhecida

# Commit 3c.β-γ (Renato, 05/06/2026): Filtragem Adaptativa
#
# Problema descoberto empiricamente: boost+penalty corrigem RANKING mas não
# INCLUSÃO. Memórias antigas conhecidas (Docker/Podman) com similaridade alta
# ainda passam do min_score=0.20 e entram no contexto, levando o modelo a
# mencioná-las explicitamente para "dispensar" — alucinação residual.
#
# Filtragem adaptativa: se EXISTE pelo menos uma memória da sessão atual com
# score >= CURRENT_SESSION_TRUST_THRESHOLD, DESCARTA memórias com session_marker
# DIFERENTE (sessão antiga conhecida) do retrieval. Legacy (sem marker) é
# preservada — backward-compat.
#
# Se NÃO existe nada relevante na sessão atual: mantém tudo (busca em todo
# histórico normalmente). Comportamento adaptativo: não é cego, é contextual.
#
# Threshold 0.30 escolhido: mesmo patamar que o EDP considera "memória útil"
# em outros lugares do scoring (semelhante ao min_score=0.20 mas com margem
# para boost ×1.60 já aplicado, então 0.30 é "score líquido após boost").
CURRENT_SESSION_TRUST_THRESHOLD = 0.30

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
        from ..config import EMBED_MODEL, EMBED_MODEL_VERSION
        emb_model   = EMBED_MODEL
        emb_version = EMBED_MODEL_VERSION
    except Exception:
        pass

    # Auto-classificação de source_type (governança de retrieval)
    try:
        from ..memory_classifier import classify_memory
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

    def __init__(self, session_id: str, max_size: int = EPISODIC_MEM_SIZE,
                 scope: str = "cognitive"):
        """
        Args:
            session_id: ID da sessão (ex: 'default')
            max_size: tamanho máximo de entradas
            scope: 'cognitive' (default) ou 'sprint'. Define sub-diretório
                   de persistência. Peça Commit 1 dos Dois Exocórtices.
        """
        self.session_id = session_id
        self.scope      = scope
        self.max_size   = max_size
        self._lock      = threading.Lock()  # [P8] proteção de escrita em disco
        # Commit 1: caminhos isolados por scope
        #   <MEMORY_DIR>/<session>_<scope>/episodic.json
        scope_dir = MEMORY_DIR / f"{session_id}_{scope}"
        scope_dir.mkdir(parents=True, exist_ok=True)
        self.path    = scope_dir / "episodic.json"
        self.entries: list[dict] = []
        self._dirty:          bool = False   # [WAL-FIX] batch persistence
        self._pending_writes: int  = 0
        self._batch_size:     int  = 50      # flush a cada 50 inserções

        # Commit 3c.β (Renato, 04/06/2026): cache lazy do current_session_marker.
        # Computado no primeiro add(), reverificado a cada add subsequente
        # (se gap > 4h desde último entry → nova sessão, novo marker).
        # None inicial: força recomputação no primeiro uso pós-load.
        self._current_session_marker: Optional[str] = None

        self._load()

        # Store pressure monitor — extracted from feb0db9:pressure_monitor.py
        from ..pressure import StorePressureMonitor
        self._pressure = StorePressureMonitor()
        self._pressure.update(len(self.entries) / max(self.max_size, 1))
        if len(self.entries) > self.max_size:
            logger.warning(
                "[memory] store carregado acima do limite: %d/%d entradas",
                len(self.entries), self.max_size,
            )

    def _load(self) -> None:
        if self.path.exists():
            # Peça 0.3.1: usa _safe_load_json que tolera JSON corrompido por
            # write parcial. Dívida #53 (docs/preregistro_fix_corrupcao_json.md):
            # truncamento GENUÍNO no meio do objeto (não recuperável por
            # _safe_load_json) usava a crashar aqui — EpisodicMemory.__init__
            # chama _load() sem try/except, derrubando a construção inteira
            # do MemoryStore. _load_json_or_quarantine nunca propaga
            # JSONDecodeError/UnicodeDecodeError: quarentena o arquivo
            # original (byte-idêntico, movido — nunca apagado), loga
            # critical e emite evento store_degraded, retornando None
            # (mesmo contrato que já tínhamos para FileNotFoundError —
            # entries fica vazio, degradação explícita, não sucesso).
            data = _load_json_or_quarantine(self.path, store_label="episodic")
            if data is not None:
                self.entries = _deserialize(data)

    def save(self) -> None:
        # [P8] Lock garante que writes concorrentes não corrompem o JSON
        # Peça 0.3.1: write atômico via tmp + fsync + rename
        with self._lock:
            _atomic_write_json(self.path, _serialize(self.entries))

    # ── Commit 3c.β (Renato, 04/06/2026) ──────────────────────────────────────
    def _get_or_create_session_marker(self) -> str:
        """
        Resolve o session_marker apropriado para uma entry NOVA que está
        prestes a ser adicionada.

        Lógica:
          1. Se não há entries anteriores → gera novo UUID (primeira sessão)
          2. Pega timestamp do último entry + seu session_marker
          3. Se gap (now - last_ts) > SESSION_GAP_THRESHOLD_SEC → nova sessão
             (gera novo UUID)
          4. Senão → herda session_marker do último entry (mesma sessão)

        Cache lazy em self._current_session_marker:
          - Computado no primeiro uso
          - Reverificado a cada chamada (timestamp do último entry pode
            indicar fronteira de sessão mesmo que cache exista)

        Robustez:
          - Entries sem timestamp ou session_marker (legacy) tratados como
            indefinidos: cria novo marker
          - Exceções: fallback para novo UUID (não trava add)

        Retorna: UUID string (sempre — nunca None).
        """
        try:
            now_ts = _now()
            # Caso 1: sem entries → primeira do scope
            if not self.entries:
                new_marker = str(uuid.uuid4())
                self._current_session_marker = new_marker
                return new_marker

            # Caso 2: pega último entry (entries é cronológica via append)
            last_entry = self.entries[-1]
            last_ts = last_entry.get("timestamp") or last_entry.get(
                _schema.FIELD_T_ABSOLUTE
            )
            last_marker = last_entry.get(_schema.FIELD_SESSION_MARKER)

            # Casos defensivos: last_ts/marker ausentes ou inválidos → nova sessão
            if last_ts is None or float(last_ts) <= 0:
                new_marker = str(uuid.uuid4())
                self._current_session_marker = new_marker
                return new_marker
            if not last_marker:
                # Entry legado sem marker → criar nova sessão a partir daqui
                new_marker = str(uuid.uuid4())
                self._current_session_marker = new_marker
                return new_marker

            # Caso 3: calcula gap
            gap = now_ts - float(last_ts)
            if gap > SESSION_GAP_THRESHOLD_SEC:
                # Fronteira de sessão: nova
                new_marker = str(uuid.uuid4())
                self._current_session_marker = new_marker
                logger.info(
                    "[session] nova sessão detectada (gap=%.0fs > %ds) | scope=%s | marker=%s",
                    gap, SESSION_GAP_THRESHOLD_SEC, self.scope, new_marker[:8],
                )
                return new_marker

            # Caso 4: continua mesma sessão
            self._current_session_marker = str(last_marker)
            return self._current_session_marker
        except Exception as e:
            # Robustez total: nunca trava add por causa de session_marker
            logger.debug("[session] _get_or_create_session_marker falhou: %s", e)
            fallback = str(uuid.uuid4())
            self._current_session_marker = fallback
            return fallback

    def add(self, entry: dict) -> None:
        entry = dict(entry)
        entry["layer"] = "episodic"

        # Commit 3c.β (Renato, 04/06/2026): preenche session_marker
        # ANTES do append. Se entry já tem marker (raro — só em migração ou
        # testes), respeita; senão computa via helper.
        # Helper resolve fronteira de sessão via gap > 4h.
        try:
            if not entry.get(_schema.FIELD_SESSION_MARKER):
                entry[_schema.FIELD_SESSION_MARKER] = self._get_or_create_session_marker()
        except Exception as e:
            logger.debug("[session] preenchimento de session_marker falhou: %s", e)

        self.entries.append(entry)
        if len(self.entries) > self.max_size:
            self._prune()
        _prev_alert = self._pressure.snapshot().eviction_alert
        self._pressure.update(len(self.entries) / max(self.max_size, 1))
        _snap = self._pressure.snapshot()
        if _snap.eviction_alert and not _prev_alert:
            logger.warning("[memory] pressão de eviction em ALERTA: %d/%d (ema=%.2f)",
                           len(self.entries), self.max_size, _snap.eviction)
        # [WAL-FIX] batch mode: não salva em cada add — marca dirty
        # save() explícito chamado por: flush(), retrieve(), _prune(), shutdown
        self._dirty = True
        self._pending_writes += 1
        if self._pending_writes >= self._batch_size:
            self.save()
            self._pending_writes = 0

        # ── Commit 3b (Renato, 04/06/2026) ────────────────────────────────────
        # Pareto event logger: emite memory_added para telemetria de
        # calibradores (Gauss/Bayes/Memory Palace). Hook explícito (D2=α).
        # Falha silenciosa: telemetria não pode quebrar gravação de memória.
        #
        # Commit 3b-fix (Renato, 04/06/2026): scope agora usa self.scope
        # (atributo direto da EpisodicMemory). Antes pegava do entry, mas
        # entries não carregam campo "scope" — resultava sempre em "unknown".
        try:
            from ..runtime.pareto_store import emit_memory_added
            session_id = getattr(self, "session_id", None) or entry.get("session_id", "unknown")
            source_type = entry.get("source_type", "unknown")
            # Scope direto do atributo da EpisodicMemory (set no __init__)
            scope = getattr(self, "scope", None) or "unknown"
            text = entry.get("text") or ""
            topic_tag = entry.get("topic_tag") or entry.get("tag")
            emit_memory_added(
                session_id=str(session_id),
                source_type=str(source_type),
                scope=str(scope),
                len_text=len(text),
                topic_tag=str(topic_tag) if topic_tag else None,
            )
        except Exception as e:
            logger.debug("[memory.add] pareto emit falhou: %s", e)

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
        # Telemetria de ranking (13/08/2026): candidata que não passa do
        # min_score NUNCA entra em `scored` — é descartada antes do append, na
        # linha do gate. Sem este contador, "quantas competiram e perderam" não
        # é recuperável nem em princípio da estrutura.
        n_avaliadas = 0

        # ── Sprint v3.6: Source-type weighting + dominance penalty ──────────
        # Penaliza memórias hiperdominantes (top-3 por acessos).
        # Reduz score de meta_conversation (governança anti-loop).
        from ..memory_classifier import get_source_weight

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

            # exp012/exp016 (EDP_TOXIC_GUARDS): peso-piso p/ answer_class
            # tóxico (not_found | disqualification — TOXIC_ANSWER_CLASSES,
            # config.py). Dívida documentada (não mexida nesta mudança):
            # SemanticMemory.retrieve() não lê answer_class — este piso só
            # cobre episodic (ver exp012_fase4_backfill_apply.py, achado de
            # fonte, e RELATORIO_ETAPA0_EXP016.md P1). fix/toxic-guards:
            # flag desacoplada de EDP_WRITE_PROVENANCE (só escrita do
            # carimbo) — ver ACHADO_FLAG_UNICA_TOXICIDADE.md do lab_edp.
            from ..config import EDP_TOXIC_GUARDS as _WP, NOT_FOUND_FLOOR as _NF, TOXIC_ANSWER_CLASSES as _TAC
            nf_floor = _NF if (_WP and e.get("answer_class") in _TAC) else 1.0

            # ── Commit 3c.β-cal (Renato, 04/06/2026): session_boost calibrado ─
            # Lógica de 3 ramos baseada em session_marker:
            #   1. Marker == atual              → boost ×1.60 (sessão atual)
            #   2. Marker != atual (mas existe) → penalty ×0.85 (sessão antiga conhecida)
            #   3. Marker ausente (legacy)      → neutro ×1.0 (backward-compat)
            # Resolve amnésia retrógrada parcial: turnos do dia que caíram fora da
            # janela imediata (N=6) ficam significativamente mais acessíveis via
            # retrieval. Calibração corrige alucinação observada empiricamente
            # em 04/06/2026 (cache de sessões web → Docker em vez de Redis).
            # Diferenciação atual/antiga: ×1.88 (vs ×1.30 da versão inicial).
            entry_marker = e.get(_schema.FIELD_SESSION_MARKER)
            if entry_marker and self._current_session_marker:
                if entry_marker == self._current_session_marker:
                    session_boost = SESSION_BOOST_FACTOR   # ×1.60 atual
                else:
                    session_boost = OUT_OF_SESSION_PENALTY # ×0.85 antiga conhecida
            else:
                session_boost = 1.0                        # ×1.00 legacy/neutro

            d    = decay(e["ultimo_acesso"])
            prio = PRIORIDADE_PESO.get(e["prioridade"], 1.0)
            ab   = access_boost(e["acessos"])
            rank_score = round(
                float(sim) * d * prio * ab
                * epi_multiplier * src_weight * dom_penalty * anchor_boost * session_boost
                * nf_floor,
                4,
            )
            n_avaliadas += 1
            if rank_score >= min_score:
                scored.append((rank_score, i, {
                    "sim": float(sim), "decay": d, "prio": prio,
                    "access_boost": ab, "epi_mult": epi_multiplier,
                    "src_weight": src_weight, "dom_penalty": dom_penalty,
                    "anchor_boost": anchor_boost,
                    "session_boost": session_boost,
                    # nf_floor entrou no dict em 13/08. Ele SEMPRE esteve no
                    # produto do rank_score (piso tóxico do exp012/exp016,
                    # NOT_FOUND_FLOOR=0.05) e era o único dos dez fatores que
                    # não ficava registrado — justamente o que implementa a
                    # governança epistêmica, e o que sozinho derruba um score
                    # em 20× quando dispara.
                    "nf_floor": nf_floor,
                }))

        scored.sort(key=lambda x: x[0], reverse=True)
        _n_acima_piso = len(scored)   # telemetria: sobreviventes do min_score

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

        # ── Commit 3c.β-γ (Renato, 05/06/2026): Filtragem Adaptativa ───────────
        # Após scoring + sort, ANTES de pegar top-K, aplica filtro contextual:
        #
        # Regra: SE existe pelo menos 1 memória da sessão atual com score >=
        # CURRENT_SESSION_TRUST_THRESHOLD (0.30), ENTÃO descarta memórias com
        # session_marker DIFERENTE do current. Legacy (sem marker) preservada.
        #
        # Justificativa: quando a sessão atual já fornece contexto suficiente,
        # incluir memórias de SESSÕES ANTIGAS conhecidas adiciona ruído (modelo
        # tende a mencioná-las explicitamente para "dispensar"). Quando NÃO
        # tem nada relevante na sessão atual, busca em todo histórico.
        #
        # Comportamento adaptativo: não é filtro cego, é contextual. Resolve
        # alucinação residual descoberta empiricamente em 05/06/2026.
        try:
            if self._current_session_marker and scored:
                # Procura algum entry da sessão atual com score >= threshold
                has_current_with_quality = any(
                    (
                        self.entries[i].get(_schema.FIELD_SESSION_MARKER)
                        == self._current_session_marker
                        and rank_score >= CURRENT_SESSION_TRUST_THRESHOLD
                    )
                    for rank_score, i, _ in scored
                )

                if has_current_with_quality:
                    n_before = len(scored)
                    filtered = []
                    n_dropped_other_session = 0
                    for rank_score, i, breakdown in scored:
                        entry_marker = self.entries[i].get(_schema.FIELD_SESSION_MARKER)
                        # Mantém:
                        #   - memórias SEM marker (legacy)
                        #   - memórias DA sessão atual (marker == current)
                        # Descarta:
                        #   - memórias COM marker DIFERENTE (sessão antiga conhecida)
                        if entry_marker is None:
                            filtered.append((rank_score, i, breakdown))
                        elif entry_marker == self._current_session_marker:
                            filtered.append((rank_score, i, breakdown))
                        else:
                            n_dropped_other_session += 1

                    scored = filtered
                    if n_dropped_other_session > 0:
                        logger.info(
                            "[retrieve] filtragem_adaptativa | descartadas=%d "
                            "(sessao antiga conhecida) | preservadas=%d",
                            n_dropped_other_session, len(scored),
                        )
        except Exception as e:
            logger.debug("[retrieve] filtragem adaptativa falhou: %s", e)

        # ── Commit 3c.β-calibrate (Renato, 04/06/2026) ─────────────────────────
        # Log diagnóstico dos top-3 com session_boost aplicado. Permite ver
        # mecanicamente se memórias da sessão atual estão vencendo memórias
        # antigas semanticamente fortes. Habilitado apenas em modo debug
        # (logger.debug) para não poluir logs em produção.
        try:
            if scored and logger.isEnabledFor(logging.DEBUG):
                for rank, i, bd in scored[:3]:
                    e_obj = self.entries[i]
                    txt = (e_obj.get("text") or "")[:50].replace("\n", " ")
                    marker = (e_obj.get(_schema.FIELD_SESSION_MARKER) or "—")[:8]
                    atual = "★" if bd.get("session_boost", 1.0) > 1.0 else " "
                    logger.debug(
                        "[retrieve] top%s: score=%.3f sim=%.3f sboost=%.2f marker=%s | %s",
                        atual, rank, bd["sim"], bd.get("session_boost", 1.0), marker, txt,
                    )
        except Exception:
            pass

        # ── Dívida #49 (13/06/2026): filtro de recusas no retrieval ────────────
        # Respostas de recusa (frase-gatilho da câmara) que são recuperadas e
        # injetadas no contexto podem PRIMAR o modelo a repetir a recusa →
        # disparo da câmara (loop de auto-reforço). Medição: 3/5 disparos tinham
        # recusa na fonte (probabilístico, não determinante). Filtro reusa o
        # detector do echo_chamber, SÓ confiança "alta" (frases exatas) — isola
        # a variável para o experimento antes/depois da taxa de disparo.
        # As recusas PERMANECEM na episódica (auditoria/lineage intactos); só
        # deixam de ser injetadas e de ter acessos incrementados (quebra o
        # reforço). Filtrar ANTES de montar results é o que zera o incremento.
        _n_apos_sessao = len(scored)  # telemetria: sobreviventes do filtro de sessão
        try:
            from ..echo_chamber import detectar_auto_sinal_de_limite
            _n_antes = len(scored)
            scored = [
                (rs, i, bd) for (rs, i, bd) in scored
                if detectar_auto_sinal_de_limite(
                    self.entries[i].get("text", "") or ""
                ).get("confianca") != "alta"
            ]
            _n_recusa = _n_antes - len(scored)
            if _n_recusa > 0:
                logger.info(
                    "[retrieve] filtro_recusa | descartadas=%d "
                    "(recusa alta-confianca) | preservadas=%d",
                    _n_recusa, len(scored),
                )
        except Exception as e:
            logger.debug("[retrieve] filtro_recusa falhou: %s", e)

        # ── Telemetria de ranking (13/08/2026) ────────────────────────────
        # Quatro cortes decidem o que chega ao prompt, e três eram invisíveis:
        # min_score (antes do append), filtro adaptativo de sessão, e
        # filtro_recusa. Só o top_k final aparecia, como `memory_hits`.
        #
        # Mesma disciplina da cascata do §10 do contrato da Fase 1, aplicada à
        # SELEÇÃO em vez da amostra: toda redução explicável. Gate da flag antes
        # de qualquer trabalho — com ela OFF isto é um `if`.
        try:
            from ..config import EDP_RANKING_TELEMETRY as _rt
            if _rt:
                from ..runtime.pareto_store import emit_ranking_decision
                emit_ranking_decision(
                    n_avaliadas=n_avaliadas,
                    n_acima_do_piso=_n_acima_piso,
                    n_apos_filtro_sessao=_n_apos_sessao,
                    n_apos_filtro_recusa=len(scored),
                    n_entregues=min(top_k, len(scored)),
                    min_score=min_score,
                    top_k=top_k,
                    metodo="cosine",
                    detalhe=[
                        {"rank": r, "score": rs, "fatores": bd}
                        for r, (rs, _i, bd) in enumerate(scored[:20], 1)
                    ],
                )
        except Exception as e:
            logger.debug("[retrieve] telemetria de ranking falhou: %s", e)

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

        # ── Commit 3b (Renato, 04/06/2026) ────────────────────────────────────
        # Pareto event logger: emite memory_accessed para telemetria.
        # n_returned=len(results), top_score=primeiro ranking_score (ou 0).
        # Falha silenciosa: telemetria não pode quebrar retrieval.
        #
        # Commit 3b-fix (Renato, 04/06/2026): scope agora usa self.scope
        # (atributo direto). Heurística substring de session_id era frágil:
        # se session_id="default", nenhum match → scope sempre "unknown".
        try:
            from ..runtime.pareto_store import emit_memory_accessed
            session_id = getattr(self, "session_id", "unknown")
            n_returned = len(results)
            top_score = results[0].get("ranking_score", 0.0) if results else 0.0
            # Scope direto do atributo (cognitive ou sprint, set no __init__)
            scope = getattr(self, "scope", None) or "unknown"
            emit_memory_accessed(
                session_id=str(session_id),
                n_returned=n_returned,
                top_score=float(top_score),
                scope=str(scope),
            )
        except Exception as e:
            logger.debug("[memory.retrieve] pareto emit falhou: %s", e)

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


# ── MemoryStore — interface unificada (compatível com v2) ─────────────────────
# Commit 1 dos Dois Exocórtices (2026-05-31):
#   MemoryStore agora segura DUAS instâncias internamente — uma para o exocórtex
#   cognitivo (memória longitudinal, conhecimento, contexto) e outra para o
#   exocórtex de trabalho (sprint). O scope ativo determina qual instância
#   responde às chamadas de leitura/escrita.
#
#   API externa preservada: código antigo que faz `mem.episodic.entries` continua
#   funcionando — properties dinâmicos delegam para o scope ativo.
#
#   Default: scope = "cognitive" (compatível com comportamento pré-refator).


_LEGACY_SUFFIX_MAP = {
    "_episodic.json":      ("cognitive", "episodic.json"),
    "_semantic.json":      ("cognitive", "semantic.json"),
    "_co_occurrence.json": ("cognitive", "co_occurrence.json"),
    "_blocks.json":        ("cognitive", "blocks.json"),
}


def _migrate_legacy_session_files(session_id: str) -> int:
    """
    Migra arquivos do formato legacy (pré-Commit 1) para o formato isolado:
        <MEMORY_DIR>/{session_id}_episodic.json
            → <MEMORY_DIR>/{session_id}_cognitive/episodic.json
        <MEMORY_DIR>/{session_id}_semantic.json
            → <MEMORY_DIR>/{session_id}_cognitive/semantic.json
        <MEMORY_DIR>/{session_id}_co_occurrence.json
            → <MEMORY_DIR>/{session_id}_cognitive/co_occurrence.json
        <MEMORY_DIR>/{session_id}_blocks.json
            → <MEMORY_DIR>/{session_id}_cognitive/blocks.json

    Razão (decidida em conversa): todos os dados acumulados antes do Commit 1
    pertencem ao cognitive (única biblioteca que existia). Sprint começa zerado.

    Idempotente: roda 2x sem efeito na segunda. Se destino já existe E source
    também, source é deixado em paz (não sobrescreve). Falha SEM exception.

    Returns:
        Número de arquivos migrados nesta chamada.
    """
    import shutil
    migrated = 0
    cog_dir = MEMORY_DIR / f"{session_id}_cognitive"

    for suffix, (scope, new_name) in _LEGACY_SUFFIX_MAP.items():
        legacy_path = MEMORY_DIR / f"{session_id}{suffix}"
        if not legacy_path.exists():
            continue

        target_dir = MEMORY_DIR / f"{session_id}_{scope}"
        target_path = target_dir / new_name

        if target_path.exists():
            # Já migrou antes. Não sobrescreve. Log pra auditoria.
            logger.info(
                "[migration] skip %s — destino já existe (%s)",
                legacy_path.name, target_path
            )
            continue

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy_path), str(target_path))
            migrated += 1
            logger.info(
                "[migration] %s → %s",
                legacy_path.name, target_path.relative_to(MEMORY_DIR)
            )
        except Exception as e:
            logger.warning(
                "[migration] FALHA mover %s → %s: %s",
                legacy_path.name, target_path, e
            )

    if migrated > 0:
        logger.info(
            "[migration] sessão '%s': %d arquivo(s) migrados para cognitive/",
            session_id, migrated
        )
    return migrated


# ── exp017 Fase 1 (T2) — dedup do retrieve (read-side) ──────────────────────────
# Função pura: opera sobre uma lista JÁ ranqueada (ordem = ranking_score desc)
# e JÁ filtrada por governança — piso NOT_FOUND_FLOOR e exclusão do híbrido
# rodam durante o scoring de cada camada, antes de qualquer candidato chegar
# aqui (RELATORIO_F1T1_EXP017.md, item c). Não lê config, não loga, não muta
# `candidates` — mode="off" é o contrato de compatibilidade byte-idêntica que
# os call sites (cosine e híbrido) dependem.

def _normalize_text_exp017(text: str | None) -> str:
    """strip + casefold + colapso de whitespace — MESMA normalização do censo
    (scripts/censo_exp017.py:39-40)."""
    import re
    return re.sub(r"\s+", " ", (text or "").strip().casefold())


def _dedup_pass_exp017(candidates: list[dict], k: int) -> tuple[list[dict], int]:
    """1ª passada por ID (colapsa fenômeno D — determinística, sem
    normalização), 2ª por hash normalizado (colapsa fenômeno A-no-resultado).
    Lazy: para assim que `kept` alcança `k`, ou a lista se esgota. Duplicata =
    item cujo ID ou hash normalizado já apareceu antes no ranking;
    representante = primeira ocorrência (maior score, já que `candidates`
    chega ordenado). Retorna (kept, n_removido) — n_removido conta só as
    duplicatas puladas até `k` ser preenchido.
    """
    kept: list[dict] = []
    seen_ids: set = set()
    seen_hashes: set = set()
    n_removed = 0
    for c in candidates:
        if len(kept) >= k:
            break
        cid = c.get("id")
        chash = _normalize_text_exp017(c.get("text"))
        if cid and cid in seen_ids:
            n_removed += 1
            continue
        if chash and chash in seen_hashes:
            n_removed += 1
            continue
        if cid:
            seen_ids.add(cid)
        if chash:
            seen_hashes.add(chash)
        kept.append(c)
    return kept, n_removed


def _dedup_ranked(candidates: list[dict], k: int, mode: str, rng=None) -> list[dict]:
    """exp017 Fase 1 (T2) — colapsa duplicatas do ranking ANTES do
    truncamento em top_k, com refill dos próximos candidatos do ranking.

    mode="off"            -> candidates[:k], byte-idêntico ao truncamento
                              atual.
    mode="dedup"           -> 1ª passada por ID, 2ª por hash normalizado
                              (_dedup_pass_exp017), refill lazy até `k`.
    mode="random_pareado"   -> controle-reserva (EXP017_FASE0.md §3): d =
                              quantas duplicatas o modo "dedup" removeria até
                              `k`; remove d itens ALEATÓRIOS do top-k bruto
                              (candidates[:k], sem dedup) e faz refill com os
                              próximos do ranking, na ordem — mesmo par
                              mecânico do dedup, critério aleatório em vez de
                              duplicata. `rng` obrigatório (random.Random do
                              chamador — disciplina de seed do
                              EDP_SHUFFLE_SEED). Mesma restrição do "dedup":
                              se a densidade de duplicatas for tão alta que
                              nem "dedup" alcançaria `k` (pool sem conteúdo
                              único suficiente), `d` pode exceder o que sobra
                              além do top-k bruto — o resultado, como no
                              "dedup", vem com MENOS que `k` itens (mesma
                              honestidade de degradação, não um bug).

    Não muta `candidates`. Não lê flags/config — a escolha de modo é do
    chamador.
    """
    if not candidates:
        return []

    if mode == "off":
        return candidates[:k]

    if mode == "dedup":
        kept, _ = _dedup_pass_exp017(candidates, k)
        return kept

    if mode == "random_pareado":
        if rng is None:
            raise ValueError("_dedup_ranked: mode='random_pareado' exige rng")
        base_k = min(k, len(candidates))
        _, d = _dedup_pass_exp017(candidates, base_k)
        top_k_raw = list(candidates[:base_k])
        d = min(d, len(top_k_raw))
        remove_positions = set(rng.sample(range(len(top_k_raw)), d)) if d > 0 else set()
        survivors = [c for i, c in enumerate(top_k_raw) if i not in remove_positions]
        for c in candidates[base_k:]:
            if len(survivors) >= k:
                break
            survivors.append(c)
        return survivors

    raise ValueError(f"_dedup_ranked: mode desconhecido: {mode!r}")


class _ScopedView:
    """
    Agrupa episodic + semantic + blocks de um único scope.
    Usado internamente pelo MemoryStore para segurar as duas bibliotecas.
    """
    def __init__(self, session_id: str, scope: str):
        self.session_id = session_id
        self.scope      = scope
        self.episodic   = EpisodicMemory(session_id, scope=scope)
        self.semantic   = SemanticMemory(session_id,  scope=scope)
        from ..blocks import BlockManager
        self.blocks     = BlockManager(session_id, MEMORY_DIR, scope=scope)


class MemoryStore:
    """
    Interface unificada de memória — agora com dois scopes (Commit 1).

    Mantém duas instâncias internas:
      - self._cognitive_view: memória longitudinal (hipocampo)
      - self._sprint_view:    memória de trabalho (gânglios da base)

    A property `episodic`, `semantic`, `blocks` delegam para o scope ativo
    (`self._active_scope`, default 'cognitive').

    [P5] reinforce_memory, decay_memory, update_usage_stats
         movidas para cá como métodos de instância.
    """

    def __init__(self, session_id: str):
        self.session_id  = session_id
        self.working     = WorkingMemory()

        # Commit 1: migração automática de arquivos legacy ANTES de instanciar
        _migrate_legacy_session_files(session_id)

        # Duas bibliotecas — ambas em RAM (decisão D2-B).
        self._cognitive_view = _ScopedView(session_id, scope="cognitive")
        self._sprint_view    = _ScopedView(session_id, scope="sprint")

        # Scope ativo — default cognitive (compatível com comportamento legacy).
        # EDPRuntime chama set_scope() quando modo muda.
        self._active_scope: str = "cognitive"

        # Log de inicialização com ambos os scopes
        cog_e = len(self._cognitive_view.episodic)
        cog_s = len(self._cognitive_view.semantic)
        cog_b = len(self._cognitive_view.blocks.blocks)
        spr_e = len(self._sprint_view.episodic)
        spr_s = len(self._sprint_view.semantic)
        spr_b = len(self._sprint_view.blocks.blocks)
        print(
            f"[Memory] Sessão '{session_id}' carregada — DOIS EXOCÓRTICES\n"
            f"         cognitive: episódica={cog_e} | semântica={cog_s} | blocos={cog_b}\n"
            f"         sprint:    episódica={spr_e} | semântica={spr_s} | blocos={spr_b}\n"
            f"         scope ativo: {self._active_scope}"
        )

    # ── Scope management (Commit 1) ───────────────────────────────────────────

    def set_scope(self, scope: str) -> None:
        """
        Troca o scope ativo. Chamado pelo EDPRuntime ao mudar de modo.

        Args:
            scope: 'cognitive' ou 'sprint'

        Raises:
            ValueError: se scope inválido
        """
        if scope not in ("cognitive", "sprint"):
            raise ValueError(f"scope deve ser 'cognitive' ou 'sprint', recebido: {scope!r}")
        if scope == self._active_scope:
            logger.debug("[memory] set_scope: já estava em %s, sem mudança", scope)
            return
        old_scope = self._active_scope
        self._active_scope = scope
        logger.info(
            "[memory] scope ativo: %s → %s (cognitive=%d episódicas, sprint=%d episódicas)",
            old_scope, scope,
            len(self._cognitive_view.episodic),
            len(self._sprint_view.episodic),
        )

    @property
    def active_scope(self) -> str:
        """Scope ativo no momento ('cognitive' ou 'sprint')."""
        return self._active_scope

    def _active_view(self) -> "_ScopedView":
        """Retorna a _ScopedView correspondente ao scope ativo."""
        return self._cognitive_view if self._active_scope == "cognitive" else self._sprint_view

    # Acesso direto às views (para casos que precisam ler/escrever scope específico
    # independente do scope ativo — ex: cross-scope retrieval futuro).
    @property
    def cognitive(self) -> "_ScopedView":
        return self._cognitive_view

    @property
    def sprint(self) -> "_ScopedView":
        return self._sprint_view

    # ── Properties delegadas ao scope ativo (compat com código pré-refator) ──

    @property
    def episodic(self) -> "EpisodicMemory":
        return self._active_view().episodic

    @property
    def semantic(self) -> "SemanticMemory":
        return self._active_view().semantic

    @property
    def blocks(self):  # BlockManager (import tardio)
        return self._active_view().blocks

    # ── v2-compat ──────────────────────────────────────────────────────────────

    @property
    def entries(self) -> list[dict]:
        return self.episodic.entries

    @entries.setter
    def entries(self, value: list[dict]) -> None:
        self.episodic.entries = value

    def save(self) -> None:
        """
        Persiste AMBOS os scopes (cognitive + sprint).
        Commit 1: garante que mudanças no scope inativo não sejam perdidas
        se save() for chamado durante o outro scope ativo.
        """
        self._cognitive_view.episodic.save()
        self._cognitive_view.semantic.save()
        self._sprint_view.episodic.save()
        self._sprint_view.semantic.save()

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
            from ..epistemic_classifier import detectar_admissao_em_prosa
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
        from ..embeddings import embed_one as _e1
        query_emb = _e1(query)

        # ── EDP_HYBRID_RETRIEVAL (exp010, 07/2026) — DEFAULT **LIGADA** ────────
        # ERRATA 18/08/2026: este comentário dizia "flag DESLIGADA por padrão".
        # É FALSO desde a promoção de 08/07 (config.py:53 -> default "1"), e
        # ele enganou três raciocínios meus num único dia: a telemetria de
        # ranking foi instalada no caminho cosseno (código morto até 18/08), o
        # laço "acessos -> access_boost" foi descrito como vivo (não é), e eu
        # quase recomendei o exp009, que manipula campos sem efeito aqui.
        # ONZE de dezoito mecanismos do cosseno não decidem nada em produção —
        # os dez fatores multiplicativos inteiros e o filtro adaptativo de
        # sessão. Ver lab/docs/sujeito_edp/AUDITORIA_MECANISMO_APOSENTADO.md.
        # Ligada, a SELEÇÃO/RANKING passa a ser BM25+vetorial+RRF (sem MMR);
        # todo o pós-processamento (monitor, contradiction, formato final_top)
        # é o mesmo. min_score do chamador (escala cosine) NÃO se aplica ao RRF
        # (escala ~0.016) — o caminho híbrido usa HYBRID_MIN_SCORE (config).
        # Desligada (EDP_HYBRID_RETRIEVAL=0, a rede de segurança — NÃO o
        # default), o fluxo abaixo é EXATAMENTE o de antes. O "(default)" que
        # estava aqui sobreviveu à errata acima, na mesma linha que ela corrige.
        # test_mecanismo_aposentado_e_no_op.py trava as duas metades por
        # comportamento, que é o que não apodrece.
        from ..config import EDP_HYBRID_RETRIEVAL
        if EDP_HYBRID_RETRIEVAL:
            return self._retrieve_hybrid(query, query_emb, top_k)

        # ── exp017 Fase 1 (T3) — resolve o modo ANTES de consultar as camadas.
        # Achado corrigido de RELATORIO_F1T1_EXP017.md (item b): Episodic
        # Memory.retrieve()/SemanticMemory.retrieve() truncam em `top_k`
        # INTERNAMENTE (scored[:top_k]) — sem overfetch NA CHAMADA a cada
        # camada, o refill nunca veria candidatos além do top_k por camada,
        # mesmo com o merge/dedup correto logo abaixo. OFF pede exatamente
        # `top_k` por camada — byte-idêntico ao comportamento atual.
        _mode = "off"
        try:
            from ..config import (
                EDP_RETRIEVE_DEDUP as _dd, EDP_RETRIEVE_SHUFFLE as _sh,
                EDP_RETRIEVE_RANDOM_DROP as _rd,
                resolve_retrieve_instrumentation_exp017 as _resolve,
            )
            _mode = _resolve(_dd, _sh, _rd)
        except Exception as _e_dedup0:
            logger.debug("[exp017] resolucao de modo (cosine) falhou (ignorado): %s", _e_dedup0)
            _mode = "off"

        _overfetch       = _mode in ("dedup", "random_pareado")
        _working_top_k   = len(self.working)  if _overfetch else top_k
        _episodic_top_k  = len(self.episodic) if _overfetch else top_k
        _semantic_top_k  = len(self.semantic) if _overfetch else top_k

        layers    = layers or ["working", "episodic", "semantic"]
        results: list[dict] = []

        if "working" in layers:
            for e in self.working.retrieve(top_k=_working_top_k):
                emb = np.array(e["embedding"], dtype=np.float32)
                sim = float(cosine_similarity([emb], [query_emb])[0][0])
                if sim >= min_score:
                    results.append({**e, "ranking_score": round(sim, 4)})

        if "episodic" in layers:
            results.extend(self.episodic.retrieve(query_emb, _episodic_top_k, min_score))

        if "semantic" in layers:
            results.extend(self.semantic.retrieve(query_emb, _semantic_top_k, min_score))

        seen: dict[str, dict] = {}
        for r in results:
            eid = r["id"]
            if eid not in seen or r["ranking_score"] > seen[eid]["ranking_score"]:
                seen[eid] = r

        final = sorted(seen.values(), key=lambda x: x["ranking_score"], reverse=True)
        for r in final:
            M.retrieval_score(r["ranking_score"])

        # ── exp017 Fase 1 (T3) — aplica o modo já resolvido acima. Ponto
        # candidato (RELATORIO_F1T1_EXP017.md, T1b): `final` já passou piso/
        # exclusão (scoring de cada camada) e ainda não foi truncado — refill
        # seguro. mode="off" preserva o truncamento atual byte-idêntico.
        _rng = None
        if _mode == "random_pareado":
            from ..config import EDP_SHUFFLE_SEED as _seed
            import hashlib as _hashlib, random as _random
            _qh = _hashlib.sha256(query.encode("utf-8")).hexdigest()
            _rng = _random.Random(f"{_seed}:{_qh}")
        try:
            final_top = _dedup_ranked(final, top_k, _mode, rng=_rng)
        except Exception as _e_dedup:
            logger.debug("[exp017] dedup falhou (ignorado): %s", _e_dedup)
            final_top = final[:top_k]

        # ── P2: Retrieval quality monitor (não-bloqueante) ───────────────────
        try:
            from ..runtime.retrieval_monitor import get_monitor
            top_scores = [r["ranking_score"] for r in final_top]
            top_ids    = [r.get("id", "") for r in final_top]
            get_monitor().record_turn(top_scores=top_scores, result_ids=top_ids)
        except Exception:
            pass  # nunca quebra retrieve por causa de monitor

        # ── P3: Contradiction flagging (não-bloqueante) ──────────────────────
        # Só executa se top_k >= 2 (precisa pares para comparar)
        if len(final_top) >= 2:
            try:
                from ..runtime.contradiction_flagger import get_flagger
                get_flagger().scan_results(final_top)
            except Exception:
                pass  # nunca quebra retrieve

        return final_top

    # ── Retrieval híbrido (exp010) — só roda com EDP_HYBRID_RETRIEVAL=1 ──────

    def _hybrid_index(self):
        """Índice do HybridRetriever sobre episodic+semantic do scope ativo.

        Cache com invalidação barata: chave = (scope, len(epi), len(sem),
        último id de cada). Cobre o caso comum (append de entries); edição
        in-place sem mudar contagem não invalida — dívida documentada, o
        custo de rebuild é medível e o miss é raro.

        Governança preservada na SELEÇÃO (o híbrido substitui o ranking, não
        a governança dura): entries contradicted/quarantined ficam FORA do
        índice ("não retorna nunca", mesma regra do cosine), e recusas de
        alta confiança também (Dívida #49 — filtro_recusa do cosine).
        """
        view = self._active_view()
        epi = view.episodic.entries
        sem = view.semantic.entries
        key = (
            self._active_scope, len(epi), len(sem),
            (epi[-1].get("id") if epi else None),
            (sem[-1].get("id") if sem else None),
        )
        cached = getattr(self, "_hybrid_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]

        from ..retrieval_hybrid import HybridRetriever
        try:
            from ..echo_chamber import detectar_auto_sinal_de_limite as _recusa
        except Exception:
            _recusa = None

        entries_kept: list[dict] = []
        layer_of:     list[str]  = []
        for layer, pool in (("episodic", epi), ("semantic", sem)):
            for e in pool:
                if not isinstance(e, dict):
                    continue
                if not e.get("id") or not (e.get("text") or "").strip():
                    continue
                if e.get("embedding") is None:
                    continue
                # governança dura: nunca retornáveis ficam fora do índice
                if e.get("epistemic_status") in ("contradicted", "quarantined"):
                    continue
                # exp012/exp016: answer_class tóxico fora do índice híbrido
                # (piso operacional; a entry NÃO é deletada — segue no store
                # e no cosine com piso). TOXIC_ANSWER_CLASSES = {"not_found",
                # "disqualification"} (config.py) — mesma lacuna documentada
                # acima (só cobre episodic+semantic AQUI porque este laço
                # varre as duas; o peso-piso isolado em EpisodicMemory NÃO).
                # fix/toxic-guards: flag desacoplada de EDP_WRITE_PROVENANCE
                # (só escrita do carimbo) — ver ACHADO_FLAG_UNICA_TOXICIDADE.md.
                from ..config import EDP_TOXIC_GUARDS as _WP12, TOXIC_ANSWER_CLASSES as _TAC12
                if _WP12 and e.get("answer_class") in _TAC12:
                    continue
                # filtro_recusa (Dívida #49): recusa alta-confiança não é injetada
                if _recusa is not None:
                    try:
                        if _recusa(e.get("text", "") or "").get("confianca") == "alta":
                            continue
                    except Exception:
                        pass
                entries_kept.append(e)
                layer_of.append(layer)

        if not entries_kept:
            index = None
        else:
            hr = HybridRetriever()  # alpha=0.5, rrf_k=60 (defaults = exp010)
            hr.add(
                [e["text"] for e in entries_kept],
                np.array([e["embedding"] for e in entries_kept], dtype=np.float32),
            )
            index = {"hr": hr, "entries": entries_kept, "layer_of": layer_of}
        self._hybrid_cache = (key, index)
        return index

    def _retrieve_hybrid(self, query: str, query_emb: np.ndarray, top_k: int) -> list[dict]:
        """Caminho híbrido do retrieve (exp010): BM25+vetorial+RRF, SEM MMR.

        Mesmo contrato do retrieve cosine: retorna final_top (dicts de entry +
        ranking_score/ranking_breakdown) e preserva os efeitos do caminho atual:
        acessos++/ultimo_acesso nas entries episódicas retornadas (com save
        oportunista, como episodic.retrieve), telemetria pareto, M.retrieval_score,
        retrieval_monitor e contradiction flagging.

        NOTA de escala: ranking_score aqui é RRF (~0.016 máx), não cosine —
        consumidores de exibição/telemetria veem a escala nova enquanto a flag
        estiver ligada (documentado no commit).
        """
        from ..config import HYBRID_MIN_SCORE

        index = self._hybrid_index()
        if not index:
            return []

        # ── exp017 Fase 1 (T3) — EDP_RETRIEVE_DEDUP / EDP_RETRIEVE_RANDOM_DROP ─
        # (default OFF). RELATORIO_F1T1_EXP017.md (T1b): o ranking completo do
        # híbrido nasce pré-truncado DENTRO de HybridRetriever.search()
        # (retrieval_hybrid.py:199-202) — pedir top_k=len(corpus) quando a
        # flag liga expõe o ranking inteiro para o refill (superset monotônico:
        # ampliar top_k só amplia o pool de candidatos, não reordena os que já
        # estariam no top-k menor). OFF pede exatamente `top_k`, byte-idêntico.
        _mode = "off"
        try:
            from ..config import (
                EDP_RETRIEVE_DEDUP as _dd, EDP_RETRIEVE_SHUFFLE as _sh,
                EDP_RETRIEVE_RANDOM_DROP as _rd,
                resolve_retrieve_instrumentation_exp017 as _resolve,
            )
            _mode = _resolve(_dd, _sh, _rd)
        except Exception as _e_dedup:
            logger.debug("[exp017] resolucao de modo (hibrido) falhou (ignorado): %s", _e_dedup)
            _mode = "off"

        _search_top_k = len(index["entries"]) if _mode in ("dedup", "random_pareado") else top_k

        res = index["hr"].search(
            query, query_emb,
            top_k=_search_top_k, min_score=HYBRID_MIN_SCORE,  # escala RRF, NÃO 0.20
            method="rrf", mmr=False,                            # exp010: MMR piora aqui
        )

        # Candidatos crus (metadado só) — mutação de acessos/ultimo_acesso é
        # ADIADA para depois do dedup: só a entry que sobrevive ao refill foi
        # de fato entregue (exp017 é read-side; overfetch não deve inflar
        # contagem de acesso de candidatos descartados pelo colapso).
        candidates: list[dict] = []
        for pos, i in enumerate(res.indices):
            if i >= len(index["entries"]):
                continue
            entry = index["entries"][i]
            candidates.append({
                **entry,
                "ranking_score": res.scores[pos] if pos < len(res.scores) else 0.0,
                "ranking_breakdown": {
                    "method": "rrf",
                    "bm25":   res.bm25_scores[pos] if pos < len(res.bm25_scores) else 0.0,
                    "vec":    res.vector_scores[pos] if pos < len(res.vector_scores) else 0.0,
                },
                "_exp017_layer":     index["layer_of"][i],
                "_exp017_entry_ref": entry,
            })

        _rng = None
        if _mode == "random_pareado":
            from ..config import EDP_SHUFFLE_SEED as _seed
            import hashlib as _hashlib, random as _random
            _qh = _hashlib.sha256(query.encode("utf-8")).hexdigest()
            _rng = _random.Random(f"{_seed}:{_qh}")
        try:
            final_top = _dedup_ranked(candidates, top_k, _mode, rng=_rng)
        except Exception as _e_dedup2:
            logger.debug("[exp017] dedup (hibrido) falhou (ignorado): %s", _e_dedup2)
            final_top = candidates[:top_k]

        agora = _now()
        touched_episodic = False
        for r in final_top:
            layer = r.pop("_exp017_layer", None)
            orig  = r.pop("_exp017_entry_ref", None)
            if layer == "episodic" and orig is not None:
                orig["acessos"]      = orig.get("acessos", 0) + 1
                orig["ultimo_acesso"] = agora
                r["acessos"]          = orig["acessos"]
                r["ultimo_acesso"]    = orig["ultimo_acesso"]
                touched_episodic = True

        # save oportunista — mesma semântica do episodic.retrieve (memory.py:879)
        if touched_episodic:
            ep = self._active_view().episodic
            if getattr(ep, "_dirty", False):
                try:
                    ep.save()
                except Exception:
                    pass

        for r in final_top:
            M.retrieval_score(r["ranking_score"])

        # telemetria pareto (paridade com episodic.retrieve)
        try:
            from ..runtime.pareto_store import emit_memory_accessed
            emit_memory_accessed(
                session_id=str(getattr(self, "session_id", "unknown")),
                n_returned=len(final_top),
                top_score=final_top[0].get("ranking_score", 0.0) if final_top else 0.0,
                scope=self._active_scope,
            )
        except Exception:
            pass

        # P2: retrieval monitor (paridade)
        try:
            from ..runtime.retrieval_monitor import get_monitor
            get_monitor().record_turn(
                top_scores=[r["ranking_score"] for r in final_top],
                result_ids=[r.get("id", "") for r in final_top],
            )
        except Exception:
            pass

        # P3: contradiction flagging (paridade)
        if len(final_top) >= 2:
            try:
                from ..runtime.contradiction_flagger import get_flagger
                get_flagger().scan_results(final_top)
            except Exception:
                pass

        # ── Telemetria de ranking (paridade) — 18/08/2026 ─────────────────
        # ESTA FALTAVA. A telemetria de 13/08 foi instalada so no caminho
        # cosseno (EpisodicMemory.retrieve), e `MemoryStore.retrieve:1511`
        # devolve por AQUI quando EDP_HYBRID_RETRIEVAL=1 — que e o default
        # desde 08/07. Resultado medido em 18/08: zero eventos apos quatro
        # turnos reais com a flag ligada. Nao era bug de emissao; era codigo
        # morto no caminho vivo.
        #
        # A cascata daqui NAO e a do cosseno, e por isso o esquema marca
        # `metodo` e usa None onde o estagio nao existe. Repetir os numeros
        # do cosseno descreveria filtros que este caminho nao tem.
        try:
            from ..config import EDP_RANKING_TELEMETRY as _rt
            if _rt:
                from ..runtime.pareto_store import emit_ranking_decision
                emit_ranking_decision(
                    n_avaliadas=len(index["entries"]),
                    n_acima_do_piso=len(res.indices),
                    n_apos_filtro_sessao=None,   # nao existe no hibrido
                    n_apos_filtro_recusa=None,   # idem
                    n_apos_dedup=len(final_top),
                    n_entregues=len(final_top),
                    min_score=HYBRID_MIN_SCORE,
                    top_k=top_k,
                    metodo="rrf",
                    detalhe=[
                        {"rank": i, "score": r.get("ranking_score", 0.0),
                         "fatores": r.get("ranking_breakdown", {})}
                        for i, r in enumerate(final_top[:20], 1)
                    ],
                )
        except Exception as _e_rt:
            logger.debug("[retrieve-hibrido] telemetria de ranking falhou: %s", _e_rt)

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
