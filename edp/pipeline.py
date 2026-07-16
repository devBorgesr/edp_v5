"""
pipeline.py — Pipeline completo do EDP v3 (PATCHED)

PATCHES APLICADOS:
  [P13] GLOBAL_MEMORY singleton removido do nível de módulo.
        Substituído por factory get_pipeline_memory(session_id) com cache
        protegido por threading.Lock — sem race condition em multi-thread.
  [P14] ADAPTIVE_CONTROLLER mantido como singleton (read-only após init — seguro).
  [P15] MemoryBridgeV32: envolve SemanticMemory e é detectada via isinstance
        em pipeline.py:646 para chamar bridge.consolidate() em vez de
        semantic_memory.consolidate_from_episodes(). Ramo v3.2 interno removido.
  [P16] Import de SemanticMemory lazy (evita falha no import-time se módulo ausente).
  [P17] classify_score não é mais chamado separadamente quando ScoreVector.decision
        já contém a decisão — elimina duplicação de lógica.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Generator, Iterator

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from .adaptive_controller import AdaptiveController, AdaptiveState
from .attention import dynamic_focus
from .compression import extractive_summarize, fuse_chunks
from .config import CHUNK_SIZE, DEDUP_THRESH, HIGH_SCORE, MID_SCORE, MIN_WORDS
from .embeddings import deduplicate, embed, embed_one
from .clock import now as _now  # Peça 0.2a — relógio interno robusto
from .filters import preprocessar
from .learning_gate import gate_info, memory_priority, should_store
from .meta_reasoner import MetaReasoner
from .cognitive_scheduler import CognitiveScheduler
from .scoring import classify_score, compute_score
from . import metrics as M

# ── [P14] AdaptiveController: singleton read-only — seguro em multi-thread ────
ADAPTIVE_CONTROLLER = AdaptiveController(debug=False)

# ── [P13] MemoryStore com cache thread-safe (substitui GLOBAL_MEMORY) ─────────

_memory_lock:  threading.Lock             = threading.Lock()
_memory_cache: dict[str, object]          = {}

def get_pipeline_memory(session_id: str = "default") -> object:
    """
    [P13] Factory thread-safe para instâncias de SemanticMemory.
    Uma instância por session_id. Lock evita criação duplicada simultânea.
    Antes: GLOBAL_MEMORY = SemanticMemory("default") — singleton sem lock.
    """
    if session_id in _memory_cache:
        return _memory_cache[session_id]
    with _memory_lock:
        # Double-check após lock
        if session_id not in _memory_cache:
            from .semantic_memory import SemanticMemory
            _memory_cache[session_id] = SemanticMemory(session_id)
        return _memory_cache[session_id]


# ── [P15] Bridge v3.1 → v3.2 ─────────────────────────────────────────────────

class MemoryBridgeV32:
    """
    [P15] Envolve SemanticMemory expondo consolidate() e retrieve().
    Detectada via isinstance em pipeline.py:646 para rotear consolidação.
    """

    def __init__(self, session_id: str = "default"):
        self.session_id       = session_id
        self._semantic_memory = get_pipeline_memory(session_id)

    def consolidate(self, episodes: list[dict]) -> None:
        self._semantic_memory.consolidate_from_episodes(episodes)

    def retrieve(self, question: str, top_k: int = 3) -> list[dict]:
        return self._semantic_memory.retrieve(question, top_k=top_k)


# ── Tipos ─────────────────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    context:         list[str]
    logs:            list[dict]
    tokens_original: int  = 0
    tokens_final:    int  = 0
    trace:           list[dict] = field(default_factory=list)
    metrics:         dict       = field(default_factory=dict)
    # Commit A (Sessão 2, 08/06/2026): score agregado da resposta.
    # None quando agregação não foi chamada ou chunks insuficientes.
    aggregate_score: object     = None  # Optional[ScoreVector]

    @property
    def reduction_pct(self) -> float:
        if self.tokens_original == 0:
            return 0.0
        return (self.tokens_original - self.tokens_final) / self.tokens_original * 100

    @property
    def context_str(self) -> str:
        return "\n".join(self.context)

    def compute_aggregate(
        self,
        chunk_scores: list,
        chunk_sizes:  list,
    ) -> None:
        """
        Commit A (Sessão 2, 08/06/2026): agrega chunk_score_vectors em
        um único ScoreVector via média ponderada por tamanho do chunk.

        Filtra Nones (chunks que falharam scoring) ANTES de agregar —
        bug evitável: chunk_score_vectors pode conter None (pipeline.py:354).

        Args:
            chunk_scores: list[Optional[ScoreVector]]
            chunk_sizes:  list[int] — comprimento de cada chunk
                          (mesmo índice de chunk_scores)

        Side effect: self.aggregate_score recebe ScoreVector agregado
                     ou permanece None se nenhum chunk válido.
        """
        # Filtra pares válidos (não-None + size positivo)
        pairs = [
            (sv, sz)
            for sv, sz in zip(chunk_scores, chunk_sizes)
            if sv is not None and sz > 0
        ]
        if not pairs:
            return  # sem dados, mantém None

        total = sum(sz for _, sz in pairs)
        if total <= 0:
            return  # paranoia

        def wavg(attr: str) -> float:
            return sum(
                getattr(sv, attr, 0.0) * sz for sv, sz in pairs
            ) / total

        # Import lazy para evitar circular (scoring → pipeline)
        from .scoring import ScoreVector, compute_confidence, classify_score

        relevance_avg  = wavg("relevance")
        redundancy_avg = wavg("redundancy")
        final_avg      = wavg("final_score")

        try:
            self.aggregate_score = ScoreVector(
                final_score       = final_avg,
                relevance         = relevance_avg,
                novelty           = wavg("novelty"),
                entropy           = wavg("entropy"),
                diversity         = wavg("diversity"),
                redundancy        = redundancy_avg,
                confidence_weight = compute_confidence(relevance_avg, redundancy_avg),
                decision          = classify_score(final_avg),
            )
        except Exception:
            # Não derruba pipeline se agregação falhar
            self.aggregate_score = None


# ── Utilitários internos ──────────────────────────────────────────────────────

def tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text)


def _split_chunks(text: str, size: int) -> list[str]:
    tokens = tokenize(text)
    chunks = []
    for i in range(0, len(tokens), size):
        chunk = " ".join(tokens[i : i + size])
        if len(tokenize(chunk)) >= MIN_WORDS:
            chunks.append(chunk)
    return chunks


def _entropy(text: str) -> float:
    tokens = tokenize(text)
    if not tokens:
        return 0.001
    freq  = Counter(tokens)
    total = len(tokens)
    return max(
        -sum((c / total) * math.log2(c / total) for c in freq.values()),
        0.001,
    )


def _diversity(text: str) -> float:
    tokens = re.findall(r"\w+", text)
    return len(set(tokens)) / len(tokens) if tokens else 0.0


def _safe_attr(obj: object, attr: str, default: float = 0.0) -> float:
    val = getattr(obj, attr, default)
    if not isinstance(val, (int, float)):
        return default
    if math.isnan(val) or math.isinf(val):
        return default
    return float(val)


def _validate_alignment(
    chunks: list,
    chunk_embs: np.ndarray,
    chunk_scores_raw: list,
    chunk_score_vectors: list,
    stage: str = "unknown",
) -> None:
    n = len(chunks)
    issues = []
    if len(chunk_embs) != n:
        issues.append(f"chunk_embs={len(chunk_embs)} vs chunks={n}")
    if len(chunk_scores_raw) != n:
        issues.append(f"chunk_scores_raw={len(chunk_scores_raw)} vs chunks={n}")
    if len(chunk_score_vectors) != n:
        issues.append(f"chunk_score_vectors={len(chunk_score_vectors)} vs chunks={n}")
    if issues:
        raise ValueError(f"[{stage}] Desalinhamento: {'; '.join(issues)}")


# ── Pipeline principal ────────────────────────────────────────────────────────

def run_pipeline(
    text: str,
    question: str,
    debug: bool = False,
    memory_embeddings: np.ndarray | None = None,
    session_id: str = "default",
    memory_bridge: MemoryBridgeV32 | None = None,
) -> PipelineResult:
    """
    Pipeline EDP v3 completo.

    [P13] session_id agora é parâmetro — sem GLOBAL_MEMORY singleton.
    [P15] memory_bridge opcional: se fornecido, consolida em v3.1 E v3.2.
    [P17] ScoreVector.decision usado diretamente — não há chamada dupla de classify_score.
    [G-MIN-INPUT] Guard: inputs com < MIN_INPUT_TOKENS pulam compressão para
                  evitar zerar a mensagem (bug crítico de reduction=100%).
                  O pipeline preserva o texto original como contexto cognitivo
                  mesmo quando muito curto para análise estrutural.
    """
    t_start         = time.perf_counter()
    tokens_original = len(tokenize(text))
    logs:  list[dict] = []
    trace: list[dict] = []

    # ── [G-MIN-INPUT] Guard: inputs muito curtos não passam pelo pipeline ────
    # Razão: chunking + dedup + scoring agressivos zeram inputs < 20 tokens.
    # Esses inputs (saudações, perguntas curtas) devem ir direto sem perda.
    MIN_INPUT_TOKENS = 20
    if tokens_original < MIN_INPUT_TOKENS:
        logs.append({
            "stage":   "guard_min_input",
            "skipped": True,
            "reason":  f"input curto ({tokens_original} < {MIN_INPUT_TOKENS} tokens) - pulando compressao",
        })
        # Preserva texto original como contexto único
        return PipelineResult(
            context=[text.strip()] if text.strip() else [],
            logs=logs,
            tokens_original=tokens_original,
            tokens_final=tokens_original,  # ← sem compressão: original == final
            trace=trace,
            metrics={"skipped_compression": True, "reason": "min_input"},
        )

    # [P13] sem singleton global — instância por sessão
    semantic_memory = memory_bridge or get_pipeline_memory(session_id)
    meta            = MetaReasoner()
    scheduler       = CognitiveScheduler("default")

    def _trace(stage: str, data: dict) -> None:
        if debug:
            trace.append({"stage": stage, "ts": round(time.perf_counter() - t_start, 4), **data})

    # ── Estágio 1: Pré-processamento ──────────────────────────────────────────
    filter_logs: list[dict] = []
    with M.timer("preprocess"):
        try:
            linhas_limpas, text_limpo, filter_logs = preprocessar(text)
            logs.extend(filter_logs)
        except Exception as e:
            logs.append({"stage": "preprocess", "error": str(e)})
            linhas_limpas, text_limpo = [], ""

    _trace("preprocess", {"lines_kept": len(linhas_limpas), "filtered": len(filter_logs)})

    if not text_limpo.strip():
        return PipelineResult(
            context=[], logs=logs,
            tokens_original=tokens_original, tokens_final=0, trace=trace,
        )

    # ── Estágio 2: Chunking ───────────────────────────────────────────────────
    with M.timer("chunking"):
        chunks = _split_chunks(text_limpo, CHUNK_SIZE)
        if not chunks:
            return PipelineResult(
                context=[], logs=logs,
                tokens_original=tokens_original, tokens_final=0, trace=trace,
            )

    _trace("chunking", {"n_chunks": len(chunks)})

    # ── Estágio 3: Embeddings ─────────────────────────────────────────────────
    query_emb:  np.ndarray = np.zeros(1, dtype=np.float32)
    chunk_embs: np.ndarray = np.zeros((len(chunks), 1), dtype=np.float32)
    line_embs:  np.ndarray = np.array([])
    # Fase 0.5 (M1, FASE0_5_MEDICAO_SPLITBRAIN.md): mem_results consultava o
    # split-brain semântico (semantic_memory.py/_concepts.json) mas nunca
    # alcançava .context/.context_str nem qualquer campo lido pelos
    # chamadores vivos — morria em retrieval_quality (trace só sob debug=True,
    # nunca ativo em produção) e em `reflection` (dead store). Removido; os
    # dois consumidores abaixo (AdaptiveController, MetaReasoner) mantidos
    # com lista vazia — ESCOPO DURO desta fase não remove esses subsistemas.
    mem_results: list[dict] = []

    with M.timer("embed_total"):
        try:
            query_emb  = embed_one(question)
            chunk_embs = embed(chunks)
            line_embs  = embed(linhas_limpas) if linhas_limpas else np.array([])
        except Exception as e:
            logs.append({"stage": "embeddings", "error": str(e)})
            return PipelineResult(
                context=[], logs=logs,
                tokens_original=tokens_original, tokens_final=0, trace=trace,
            )

    # ── Estágio 4: Chunk fusion ───────────────────────────────────────────────
    with M.timer("fusion"):
        try:
            chunks, chunk_embs = fuse_chunks(chunks, chunk_embs, threshold=0.82)
        except Exception as e:
            logs.append({"stage": "fusion", "error": str(e)})

    _trace("fusion", {"n_chunks_after": len(chunks)})

    # ── Estágio 5: Deduplicação ───────────────────────────────────────────────
    with M.timer("dedup_chunks"):
        try:
            chunks_deduped, kept_idx = deduplicate(
                chunks, DEDUP_THRESH, chunk_embs, return_indices=True
            )
            if len(chunks_deduped) < len(chunks):
                chunk_embs = chunk_embs[kept_idx]   # fatia embeddings existentes — sem re-embed
                chunks     = chunks_deduped
        except Exception as e:
            logs.append({"stage": "dedup_chunks", "error": str(e)})

    _trace("dedup", {"n_chunks_deduped": len(chunks)})

    # ── Estágio 6: Scoring ────────────────────────────────────────────────────
    with M.timer("scoring"):
        chunk_scores_raw:    list[float] = []
        chunk_score_vectors: list        = []

        for i, (chunk, emb) in enumerate(zip(chunks, chunk_embs)):
            try:
                sd = compute_score(
                    text=chunk,
                    text_embedding=emb,
                    context_embedding=query_emb,
                    memory_embeddings=memory_embeddings,
                    recent_context_embeddings=[],
                )
                final_score = _safe_attr(sd, "final_score", 0.0)
                chunk_scores_raw.append(final_score)
                chunk_score_vectors.append(sd)
            except Exception as e:
                logs.append({"stage": "scoring", "chunk_index": i, "error": str(e)})
                chunk_scores_raw.append(0.0)
                chunk_score_vectors.append(None)

    _trace("scoring", {"scores": [round(s, 3) for s in chunk_scores_raw]})

    try:
        _validate_alignment(chunks, chunk_embs, chunk_scores_raw, chunk_score_vectors, "post-scoring")
    except ValueError as e:
        logs.append({"stage": "alignment_check", "error": str(e)})
        n_safe              = min(len(chunks), len(chunk_embs), len(chunk_scores_raw), len(chunk_score_vectors))
        chunks              = chunks[:n_safe]
        chunk_embs          = chunk_embs[:n_safe]
        chunk_scores_raw    = chunk_scores_raw[:n_safe]
        chunk_score_vectors = chunk_score_vectors[:n_safe]

    # ── AdaptiveController ────────────────────────────────────────────────────
    try:
        n_chunks    = max(len(chunks), 1)
        avg_score   = sum(chunk_scores_raw) / n_chunks if chunk_scores_raw else 0.5
        avg_novelty = sum(
            _safe_attr(sd, "novelty", 0.5) for sd in chunk_score_vectors if sd is not None
        ) / n_chunks

        adaptive_state = AdaptiveState(
            confidence=avg_score,
            hallucination_risk=max(0.0, 1.0 - avg_score),
            entropy=_entropy(text_limpo),
            novelty=avg_novelty,
            redundancy=max(0.0, 1.0 - avg_novelty),
            memory_pressure=min(1.0, len(chunk_score_vectors) / 500.0),
            retrieval_quality=float(len(mem_results)) / max(len(mem_results) + 1, 1),
        )
        adaptive_decision = ADAPTIVE_CONTROLLER.decide(adaptive_state)
        _trace("adaptive_controller", adaptive_decision.to_dict())
    except Exception as e:
        logs.append({"stage": "adaptive_controller", "error": str(e)})
        adaptive_decision = None

    # ── Meta-reasoning ────────────────────────────────────────────────────────
    context_items = [
        {"text": chunks[i], "embedding": chunk_embs[i]}
        for i in range(len(chunks))
    ]
    try:
        reflection = meta.reflect(context_items, mem_results)
    except Exception as e:
        logs.append({"stage": "meta_reasoner", "error": str(e)})
        from dataclasses import dataclass as _dc, field as _f
        @_dc
        class _FallbackReflection:
            confidence: float = 0.5
            hallucination_risk: float = 0.5
            conflicts: list = _f(default_factory=list)
            redundancies: list = _f(default_factory=list)
            critique: str = "fallback"
            reweights: dict = _f(default_factory=dict)
        reflection = _FallbackReflection()

    # ── Estágio 7: Atenção seletiva ───────────────────────────────────────────
    with M.timer("attention"):
        try:
            scores_arr  = np.array(chunk_scores_raw, dtype=np.float32)
            focused_idx = dynamic_focus(
                embeddings=chunk_embs,
                query_emb=query_emb,
                scores=scores_arr,
                top_k=len(chunks),
                suppress_threshold=0.88,
            )
        except Exception as e:
            logs.append({"stage": "attention", "error": str(e)})
            focused_idx = list(range(len(chunks)))

    # ── Estágio 8: Keep / Summarize / Drop ────────────────────────────────────
    final:            list[str]  = []
    learnable_blocks: list[dict] = []

    for i in focused_idx:
        if i >= len(chunks):
            continue

        chunk = chunks[i]
        emb   = chunk_embs[i]
        score = chunk_scores_raw[i]
        sd    = chunk_score_vectors[i]

        if sd is None:
            logs.append({"chunk": i, "action": "drop-no-scoredata"})
            continue

        # [P17] Usa ScoreVector.decision diretamente — sem classify_score() redundante
        decision = sd.decision
        action   = decision.value.lower()

        should_learn = should_store(
            score=_safe_attr(sd, "final_score"),
            confidence=_safe_attr(sd, "confidence_weight"),
            novelty=_safe_attr(sd, "novelty", 1.0),
        )

        gate_debug = gate_info(
            score=_safe_attr(sd, "final_score"),
            confidence=_safe_attr(sd, "confidence_weight"),
            novelty=_safe_attr(sd, "novelty", 1.0),
        )

        priority = memory_priority(
            score=_safe_attr(sd, "final_score"),
            confidence=_safe_attr(sd, "confidence_weight"),
        )

        if action == "drop":
            logs.append({
                "chunk":         i,
                "action":        "drop-score",
                "score":         round(score, 3),
                "entropy":       _safe_attr(sd, "entropy"),
                "relevance":     _safe_attr(sd, "relevance"),
                "learning_gate": gate_debug,
            })
            continue

        tok_chunk = set(tokenize(chunk))
        idx_lines = [
            j for j, l in enumerate(linhas_limpas)
            if len(tok_chunk & set(tokenize(l))) > 2
        ]
        frases = [linhas_limpas[j] for j in idx_lines]
        semb   = (
            line_embs[idx_lines]
            if idx_lines and line_embs.size > 0
            else np.array([])
        )

        min_sim = 0.28 if action == "summarize" else 0.30
        if semb.size > 0:
            sims_f = cosine_similarity(semb, [query_emb]).flatten()
            ok_idx = [k for k, s in enumerate(sims_f) if s >= min_sim]
            frases = [frases[k] for k in ok_idx]
            semb   = semb[ok_idx] if ok_idx else np.array([])

        if action == "keep":
            result = " ".join(frases) if frases else chunk
        else:
            try:
                result = (
                    extractive_summarize(frases, semb, query_emb, max_sentences=2)
                    if frases and semb.size > 0
                    else chunk
                )
            except Exception as e:
                logs.append({"stage": "summarize", "chunk_index": i, "error": str(e)})
                result = chunk

        if result.strip():
            final.append(result)

        if should_learn and result.strip():
            learnable_blocks.append({
                "id":        str(time.time_ns()),
                "text":      result,
                "embedding": emb,
                "score":     _safe_attr(sd, "final_score"),
            })

        logs.append({
            "chunk":      i,
            "action":     action,
            "priority":   priority,
            "score":      round(score, 3),
            "entropy":    _safe_attr(sd, "entropy"),
            "diversity":  _safe_attr(sd, "diversity"),
            "relevance":  _safe_attr(sd, "relevance"),
            "novelty":    _safe_attr(sd, "novelty", 1.0),
            "confidence": _safe_attr(sd, "confidence_weight"),
        })

    # ── Scheduler ─────────────────────────────────────────────────────────────
    try:
        fake_episodes = [
            {
                "id":            str(i),
                "text":          chunks[i],
                "embedding":     chunk_embs[i],
                "score_inicial": chunk_scores_raw[i],
                "acessos":       1,
                "ultimo_acesso": _now(),
                "prioridade":    "media",
            }
            for i in range(len(chunks))
        ]
        report = scheduler.evaluate(fake_episodes)
        _trace("scheduler", report.summary)
    except Exception as e:
        logs.append({"stage": "scheduler", "error": str(e)})

    # ── Estágio 9: Dedup final ────────────────────────────────────────────────
    with M.timer("dedup_final"):
        all_sentences: list[str] = []
        for bloco in final:
            for f in re.split(r"(?<=[.!?])\s+", bloco):
                f = f.strip()
                if f:
                    all_sentences.append(f)
        try:
            all_sentences = deduplicate(all_sentences, threshold=DEDUP_THRESH)
        except Exception as e:
            logs.append({"stage": "dedup_final", "error": str(e)})

    # ── Estágio 10: Reagrupar ─────────────────────────────────────────────────
    blocos_final: list[str] = []
    for i in range(0, len(all_sentences), 2):
        blocos_final.append(" ".join(all_sentences[i : i + 2]))

    tokens_final = len(tokenize("\n".join(blocos_final)))
    ratio        = M.compression_ratio(tokens_original, tokens_final)

    # ── Persistência — [P15] via bridge (suporta v3.1 e v3.2) ────────────────
    if learnable_blocks:
        try:
            episodes = [
                {
                    "id":        item["id"],
                    "text":      item["text"],
                    "embedding": item["embedding"],
                }
                for item in learnable_blocks
            ]
            if isinstance(semantic_memory, MemoryBridgeV32):
                semantic_memory.consolidate(episodes)   # [P15] bridge v3.1+v3.2
            else:
                semantic_memory.consolidate_from_episodes(episodes)
            _trace("semantic_store", {"stored": len(learnable_blocks)})
        except Exception as e:
            logs.append({"stage": "semantic_store", "error": str(e)})
            # Sprint #43 (11/06/2026): este erro era engolido em logs
            # internos que ninguém lê (anti-padrão δ). Caminho standalone
            # (_concepts.json) DEPRECADO em favor do job auto_consolidation;
            # warning mantém qualquer falha visível até a remoção total.
            import logging as _logging
            _logging.getLogger("edp.pipeline").warning(
                "[pipeline] semantic_store (standalone, deprecado) falhou: %s",
                str(e)[:120],
            )

    # Commit A (Sessão 2, 08/06/2026): construir result e agregar scores
    # antes de retornar. chunk_score_vectors pode conter None — filter
    # acontece dentro de compute_aggregate.
    _result = PipelineResult(
        context=blocos_final,
        logs=logs,
        tokens_original=tokens_original,
        tokens_final=tokens_final,
        trace=trace,
        metrics={"compression_ratio": ratio},
    )
    try:
        # chunk_score_vectors e chunks ainda em escopo (locais de run_pipeline)
        _chunk_sizes = [len(c) for c in chunks]
        _result.compute_aggregate(
            chunk_scores=chunk_score_vectors,
            chunk_sizes=_chunk_sizes,
        )
    except Exception as e:
        # Agregação NÃO pode derrubar pipeline conversacional
        logs.append({"stage": "aggregate_score", "error": str(e)})
    return _result


# ── Streaming pipeline ────────────────────────────────────────────────────────

def process_stream(
    text_stream: Iterator,
    question: str,
    memory_store=None,
    chunk_separator: str = "\n",
    buffer_lines: int = 5,
    prioridade: str = "media",
    session_id: str = "default",
) -> Generator[PipelineResult, None, None]:
    """
    [P13] session_id adicionado — usa get_pipeline_memory() sem singleton global.
    """
    buffer: list[str] = []

    def _flush(lines: list[str]) -> PipelineResult:
        batch = chunk_separator.join(lines)
        try:
            result = run_pipeline(batch, question, session_id=session_id)
        except Exception as e:
            return PipelineResult(
                context=[],
                logs=[{"stage": "stream", "error": str(e)}],
                tokens_original=0,
                tokens_final=0,
            )
        if memory_store and result.context:
            for b in result.context:
                if b.strip():
                    memory_store.add(b, score=0.5, prioridade=prioridade)
        return result

    for chunk in text_stream:
        if not chunk.strip():
            continue
        buffer.extend(chunk.split(chunk_separator))
        if len(buffer) >= buffer_lines:
            yield _flush(buffer)
            buffer = []

    if buffer:
        yield _flush(buffer)
