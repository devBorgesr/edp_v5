"""
consolidation.py (PATCHED)
[P-F5] dynamic_threshold: cosine_similarity pré-computada 1× antes do loop binário
       Antes: O(n²)×20 | Depois: O(n²)×1 + O(n²) loop — 20× menos ops float
"""
"""
consolidation.py — Consolidação semântica do EDP v3.
Clustering por cosseno, merge de metadata, threshold dinâmico,
redundancy collapse, promoção para memória semântica.
"""
import logging

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from .config import CONSOLIDATION_SIM_THRESH, CONSOLIDATION_CLUSTER_MIN

logger = logging.getLogger("edp.consolidation")

# ── Clustering ────────────────────────────────────────────────────────────────

def cluster_entries(
    entries: list[dict],
    embeddings: np.ndarray,
    threshold: float | None = None,
) -> list[list[int]]:
    """
    Agrupa entradas por similaridade semântica (single-linkage).
    threshold=None usa CONSOLIDATION_SIM_THRESH do config.
    Retorna lista de clusters (cada cluster = lista de índices).
    """
    if threshold is None:
        threshold = CONSOLIDATION_SIM_THRESH

    if not entries or embeddings.size == 0:
        return [[i] for i in range(len(entries))]

    n          = len(entries)
    sim_matrix = cosine_similarity(embeddings)
    visited    = [False] * n
    clusters:   list[list[int]] = []

    for i in range(n):
        if visited[i]:
            continue
        cluster = [i]
        visited[i] = True
        for j in range(i + 1, n):
            if not visited[j] and sim_matrix[i, j] >= threshold:
                cluster.append(j)
                visited[j] = True
        clusters.append(cluster)

    return clusters

def dynamic_threshold(embeddings: np.ndarray, target_clusters: int) -> float:
    """
    Calcula threshold dinamicamente para atingir ~target_clusters.
    Faz busca binária sobre o threshold.

    [P-F5] FIX O(n²)×20:
    Antes: cosine_similarity(embeddings) recalculada 20× dentro do loop binário.
           Para n=200: 20 × 40k = 800k ops float.
    Depois: pré-computa sim_matrix 1× antes do loop.
            Loop binário usa a matrix já computada → O(n²) apenas 1×.
    """
    if embeddings.size == 0 or len(embeddings) <= 1:
        return CONSOLIDATION_SIM_THRESH

    # [P-F5] Pré-computa fora do loop
    sim = cosine_similarity(embeddings)
    n   = len(embeddings)

    lo, hi = 0.50, 0.99
    for _ in range(20):
        mid        = (lo + hi) / 2.0
        visited    = [False] * n
        n_clusters = 0
        for i in range(n):
            if not visited[i]:
                visited[i]  = True
                n_clusters += 1
                for j in range(i + 1, n):
                    if not visited[j] and sim[i, j] >= mid:
                        visited[j] = True
        if n_clusters > target_clusters:
            hi = mid
        else:
            lo = mid

    return round((lo + hi) / 2.0, 4)

# ── Merge ─────────────────────────────────────────────────────────────────────

def merge_cluster(entries: list[dict], cluster: list[int]) -> dict | None:
    """
    Funde cluster de entradas em uma única entrada consolidada.
    - texto: concatenação das entradas do cluster
    - acessos: soma de todos
    - prioridade: melhor prioridade do cluster
    - score_inicial: média ponderada por acessos
    - embedding: média normalizada dos embeddings
    - timestamp: mais recente
    - ultimo_acesso: mais recente
    """
    if not cluster:
        return None
    if len(cluster) == 1:
        return entries[cluster[0]]

    import time, uuid
    import numpy as np

    grupo = [entries[i] for i in cluster]

    PRIO_ORDER = {"alta": 3, "media": 2, "baixa": 1}
    melhor_prio = max(grupo, key=lambda e: PRIO_ORDER.get(e.get("prioridade","media"), 2))

    total_acessos  = sum(e.get("acessos", 0) for e in grupo)
    total_peso     = max(total_acessos, 1)
    score_medio    = sum(
        e.get("score_inicial", 0.5) * (e.get("acessos", 0) + 1)
        for e in grupo
    ) / (total_peso + len(grupo))

    # texto consolidado — join com separador
    textos = [e["text"] for e in grupo]
    texto_merged = " | ".join(textos)

    # embedding médio normalizado
    embs = np.vstack([
        np.array(e["embedding"], dtype=np.float32) for e in grupo
    ])
    emb_medio = embs.mean(axis=0)
    norm = np.linalg.norm(emb_medio)
    if norm > 0:
        emb_medio = emb_medio / norm

    ts_recente = max(e.get("timestamp", 0)    for e in grupo)
    ac_recente = max(e.get("ultimo_acesso", 0) for e in grupo)

    return {
        "id":            str(uuid.uuid4()),
        "text":          texto_merged,
        "embedding":     emb_medio,
        "timestamp":     ts_recente,
        "score_inicial": round(score_medio, 4),
        "acessos":       total_acessos,
        "ultimo_acesso": ac_recente,
        "prioridade":    melhor_prio.get("prioridade", "media"),
        "layer":         "episodic",
        "merged_from":   len(cluster),
    }

# ── Consolidação completa ─────────────────────────────────────────────────────

def consolidate(
    memory,  # MemoryStore
    threshold: float | None = None,
    promote_threshold: float = 3,  # mín. de acessos para promover a semantic
) -> dict:
    """
    Roda clustering + merge na memória episódica.
    Entradas com acessos >= promote_threshold são promovidas
    para SemanticMemory após o merge.

    Retorna métricas da operação.
    """
    entries    = memory.episodic.entries
    embeddings = memory.episodic.all_embeddings()

    if not entries:
        return {"before": 0, "after": 0, "merged": 0, "promoted": 0}

    clusters   = cluster_entries(entries, embeddings, threshold)
    new_entries: list[dict] = []
    merged_count   = 0
    promoted_count = 0

    for cluster in clusters:
        if len(cluster) > 1:
            merged = merge_cluster(entries, cluster)
            if merged:
                new_entries.append(merged)
                merged_count += 1
                # promoção para semantic
                if merged.get("acessos", 0) >= promote_threshold:
                    memory.semantic.promote(merged)
                    promoted_count += 1
        else:
            entry = entries[cluster[0]]
            new_entries.append(entry)
            # promoção de entradas individuais muito acessadas
            if entry.get("acessos", 0) >= promote_threshold:
                memory.semantic.promote(entry)
                promoted_count += 1

    before = len(entries)
    memory.episodic.entries = new_entries
    memory.episodic.save()
    memory.semantic.save()

    from . import metrics as M
    M.consolidation_ratio(before, len(new_entries))

    return {
        "before":    before,
        "after":     len(new_entries),
        "merged":    merged_count,
        "promoted":  promoted_count,
        "threshold": threshold or CONSOLIDATION_SIM_THRESH,
    }

# ── Explainability ────────────────────────────────────────────────────────────

def explain_cluster(entries: list[dict], cluster: list[int]) -> dict:
    """
    Retorna informação legível sobre um cluster para debug.
    """
    grupo = [entries[i] for i in cluster]
    return {
        "size":    len(cluster),
        "indices": cluster,
        "texts":   [e["text"][:80] for e in grupo],
        "priorities": [e.get("prioridade", "?") for e in grupo],
        "acessos":    [e.get("acessos", 0) for e in grupo],
    }

# ── Auto-consolidation & merge (upgrade) ─────────────────────────────────────
def consolidate_promote_only(memory, promote_threshold: int = 3) -> dict:
    """
    Modo NÃO-DESTRUTIVO: apenas promove memórias episódicas muito acessadas
    para SemanticMemory, sem mesclar nada.

    Memórias episódicas permanecem intactas. Útil para usuários que querem
    construir camada semântica sem perder granularidade da episódica.

    Critério: entry.get("acessos", 0) >= promote_threshold
    Skip: já promovida (entry["layer"] == "semantic" — não acontece na episódica,
          mas check defensivo); duplicação por id na semantic.

    Fase 5 (fix/consolidation-toxicity-guard): entries com answer_class em
    TOXIC_ANSWER_CLASSES (edp/config.py — not_found | disqualification) NÃO
    são promovidas, gated por EDP_TOXIC_GUARDS desde fix/toxic-guards (era
    EDP_WRITE_PROVENANCE — desacoplado porque o rollback de escrita não pode
    desarmar a defesa sobre carimbos já persistidos, ver
    ACHADO_FLAG_UNICA_TOXICIDADE.md do lab_edp). Racional: conteúdo quarentenado não
    ganha upgrade de durabilidade — promover elevaria prioridade a "alta" e
    tiraria a entry do caminho episódico onde o peso-piso a penaliza; a
    SemanticMemory não tem peso-piso próprio (dívida documentada desde o
    exp012), então uma entry tóxica promovida só continuaria coberta pela
    exclusão do índice híbrido, nunca pelo caminho cosine puro. Furo medido
    em 14/07/2026 (Fase 5): auto_consolidation promoveu entries no meio de
    uma rodada sem consultar toxicidade nenhuma.

    Retorna métricas da operação.
    """
    entries = memory.episodic.entries
    if not entries:
        return {
            "mode":          "promote_only",
            "scanned":       0,
            "promoted":      0,
            "skipped":       0,
            "already_promoted": 0,
            "blocked_toxic": 0,
            "threshold":     promote_threshold,
        }

    # IDs já presentes na semantic (evita duplicação)
    semantic_ids = {e.get("id") for e in memory.semantic.entries if e.get("id")}

    from .config import EDP_TOXIC_GUARDS, TOXIC_ANSWER_CLASSES

    promoted_count = 0
    already_count  = 0
    skipped_count  = 0
    blocked_toxic_count = 0
    promoted_ids:  list[str] = []

    for e in entries:
        acessos = int(e.get("acessos", 0))
        if acessos < promote_threshold:
            skipped_count += 1
            continue

        eid = e.get("id")
        if eid and eid in semantic_ids:
            already_count += 1
            continue

        if EDP_TOXIC_GUARDS and e.get("answer_class") in TOXIC_ANSWER_CLASSES:
            blocked_toxic_count += 1
            logger.info(
                "[consolidation] promoção bloqueada (conteúdo quarentenado) "
                "| id=%s classe=%s",
                (eid or "")[:8], e.get("answer_class"),
            )
            continue

        # Promove (cópia, mantém episódica intacta)
        memory.semantic.promote(e)
        promoted_count += 1
        if eid:
            promoted_ids.append(eid)
            semantic_ids.add(eid)

    memory.semantic.save()

    return {
        "mode":             "promote_only",
        "scanned":          len(entries),
        "promoted":         promoted_count,
        "promoted_ids":     promoted_ids,
        "skipped":          skipped_count,
        "already_promoted": already_count,
        "blocked_toxic":    blocked_toxic_count,
        "threshold":        promote_threshold,
        "semantic_total":   len(memory.semantic.entries),
    }


# ── Auto-consolidation & merge (upgrade) ─────────────────────────────────────
def auto_consolidate(memory, trigger_size: int = 50, promote_threshold: int = 3) -> dict:
    n = len(memory.episodic.entries)
    if n < trigger_size:
        return {"status": "skipped", "reason": f"episodic={n} < trigger={trigger_size}", "size": n}
    result = consolidate(memory, promote_threshold=promote_threshold)
    result["status"] = "consolidated"
    return result

def merge_similar_memories(memory, threshold: float = 0.90, dry_run: bool = False) -> dict:
    entries    = memory.episodic.entries
    embeddings = memory.episodic.all_embeddings()
    if len(entries) < 2 or embeddings.size == 0:
        return {"merged": 0, "before": len(entries), "after": len(entries)}
    clusters = cluster_entries(entries, embeddings, threshold=threshold)
    merged_count = 0; new_entries = []
    for cluster in clusters:
        if len(cluster) == 1:
            new_entries.append(entries[cluster[0]]); continue
        grupo = [entries[i] for i in cluster]
        best  = dict(max(grupo, key=lambda e: e.get("score_inicial", 0.5)))
        best["acessos"]       = sum(e.get("acessos", 0) for e in grupo)
        best["ultimo_acesso"] = max(e.get("ultimo_acesso", 0) for e in grupo)
        new_entries.append(best); merged_count += len(cluster) - 1
    before = len(entries); after = len(new_entries)
    if not dry_run:
        memory.episodic.entries = new_entries; memory.episodic.save()
    return {"merged": merged_count, "before": before, "after": after,
            "dry_run": dry_run, "threshold": threshold}

