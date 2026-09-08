"""
edp.session_summary — Resumos de sessão com tag temática emergente.

Quando uma sessão termina (inatividade >5min ou disconnect), gera:
  1) Sumário objetivo (3-5 substantivos concretos) via LLM
  2) Label semântico curto (snake_case, 2 palavras) via LLM
  3) Verifica similaridade com summaries existentes:
       - Se cos_sim > 0.75 com algum → reusa o tema_tag dele
       - Senão → cria novo tema_tag

Resultado vira memória com:
  source_type = "session_summary"
  topic_tag   = "bitcoin_mining" (label semântico)

Princípios:
  - NÃO conduz a conversa (sem welcome contextual auto)
  - Resumo CURTO e CONCRETO (anti-filosofização)
  - Custo: 1 chamada Haiku por SESSÃO (não por mensagem)
"""
from __future__ import annotations
from typing import Optional, List
import logging
import re
import time

logger = logging.getLogger("edp.session_summary")

# Threshold de similaridade para reuso de tag
TAG_SIMILARITY_THRESHOLD = 0.75
# Mínimo de mensagens para gerar resumo (sessão muito curta = ruído)
MIN_MESSAGES_FOR_SUMMARY = 4


SUMMARY_PROMPT = """Resuma o tema central desta conversa em 1 frase de 10-20 palavras.

Regras estritas:
- Use SUBSTANTIVOS CONCRETOS (ex: "Bitcoin, SHA-256, pools")
- NAO filosofize, NAO use palavras abstratas
- NAO mencione "memoria", "EDP", "validacao" (meta-conversa)
- Apenas o CONTEUDO discutido

Conversa:
{conversation}

Resumo curto (apenas substantivos concretos):"""


LABEL_PROMPT = """Gere um label curto em snake_case (2 palavras) para esta conversa.

Exemplos validos:
  bitcoin_mining
  quantum_algorithms
  entropy_time
  llm_finetuning

Regras:
- APENAS 2 palavras, minusculas, separadas por underscore
- Substantivos especificos
- NAO use: memoria, edp, validacao, sistema
- Responda APENAS o label, sem explicacao

Resumo: {summary}

Label:"""


def _clean_label(raw: str) -> str:
    """Sanitiza output do LLM → snake_case válido."""
    if not raw:
        return "tema_geral"
    # Pega primeira linha, remove pontuação
    line = raw.strip().split("\n")[0].strip()
    # Remove tudo que não é alfanumérico ou underscore
    line = re.sub(r"[^a-zA-Z0-9_\s]", "", line).strip()
    # Espaços → underscore, minúsculas
    line = re.sub(r"\s+", "_", line.lower())
    # Limita a 2 palavras (4 underscores no máx)
    parts = line.split("_")[:3]
    label = "_".join(p for p in parts if p)
    # Fallback se vazio
    if not label or len(label) < 3:
        return "tema_geral"
    return label[:40]


def _build_conversation_text(entries: List[dict], max_chars: int = 3000) -> str:
    """Concatena últimas entradas da sessão para enviar ao LLM."""
    lines = []
    total = 0
    for e in entries:
        txt = (e.get("text", "") or "").strip()
        if not txt:
            continue
        if total + len(txt) > max_chars:
            break
        lines.append(txt[:400])
        total += len(txt)
    return "\n---\n".join(lines)


def _clean_summary(raw: str, max_chars: int = 500) -> str:
    """Limpa o resumo do LLM preservando o CORPO coeso.

    Dívida #50 (13/06/2026): antes, o código pegava só a 1ª linha não-header
    e descartava o resto — gravando fragmentos inúteis ('**Redis:**',
    '| Tópico | Resumo |') como resumo de sessão de prioridade alta, que
    poluíam o retrieval (o item mais recuperado da base era um header puro).
    Agora descarta apenas markdown ESTRUTURAL (tabelas, separadores, headers)
    e mantém o conteúdo real, unido num texto contínuo até max_chars.
    """
    import re as _re2
    uteis = []
    for ln in (raw or "").split("\n"):
        s = ln.strip()
        if not s:
            continue
        # linha de tabela markdown: |...|
        if s.startswith("|") and s.endswith("|"):
            continue
        # separadores / linhas só de pontuação estrutural (---, ===, |, :)
        if _re2.fullmatch(r"[\-\=\*\|\s:]+", s):
            continue
        # header markdown (# ...)
        if s.startswith("#"):
            continue
        uteis.append(s)
    texto = " ".join(uteis)
    texto = _re2.sub(r"[*_`]+", "", texto)       # remove ênfase inline
    texto = _re2.sub(r"\s+", " ", texto).strip()  # normaliza espaços
    return texto[:max_chars]


def generate_session_summary(
    memory_store,
    llm_runtime,
    session_id: str = "default",
    last_n: int = 10,
) -> Optional[dict]:
    """
    Gera resumo da sessão atual.

    Args:
      memory_store: instância de MemoryStore
      llm_runtime: instância de EDPRuntime (precisa ter cliente conectado)
      session_id: id da sessão
      last_n: quantas entradas recentes considerar

    Returns:
      dict com {summary, label, reused, entry_id} ou None se não gerou.
    """
    if memory_store is None or llm_runtime is None:
        logger.debug("[summary] memory ou runtime ausente")
        return None
    if not llm_runtime.is_connected():
        logger.debug("[summary] LLM nao conectado")
        return None

    # 1) Coleta últimas N entradas episódicas que NAO sao summaries
    try:
        entries = [
            e for e in memory_store.episodic.entries
            if e.get("source_type") != "session_summary"
        ]
        entries = entries[-last_n:]
    except Exception as e:
        logger.warning("[summary] falha coletando entries: %s", e)
        return None

    if len(entries) < MIN_MESSAGES_FOR_SUMMARY:
        logger.debug("[summary] sessao curta (%d msgs), pulando", len(entries))
        return None

    # 2) Gera sumário via LLM
    conv_text = _build_conversation_text(entries)
    prompt = SUMMARY_PROMPT.format(conversation=conv_text)
    try:
        # store_to_memory=False: esta é chamada interna do sistema,
        # não deve virar turno episódico do usuário.
        response = llm_runtime.chat(
            prompt,
            system="Voce e um resumidor objetivo.",
            store_to_memory=False,
        )
        summary_text = (response.text or "").strip()
        # Dívida #50 (13/06/2026): preserva o corpo do resumo, limpando só
        # markdown estrutural — em vez de capturar a 1ª linha (frequentemente
        # um header/tabela) e descartar o resto.
        summary_text = _clean_summary(summary_text, max_chars=500)
        if not summary_text or len(summary_text) < 10:
            logger.debug("[summary] resumo vazio/curto, pulando")
            return None
    except Exception as e:
        logger.warning("[summary] LLM falhou no resumo: %s", e)
        return None

    # 3) Gera label via LLM
    try:
        label_response = llm_runtime.chat(
            LABEL_PROMPT.format(summary=summary_text),
            system="Voce gera labels curtos em snake_case.",
            store_to_memory=False,
        )
        raw_label = (label_response.text or "").strip()
        label = _clean_label(raw_label)
    except Exception as e:
        logger.warning("[summary] LLM falhou no label, usando heuristica: %s", e)
        label = _heuristic_label(summary_text)

    # 4) Procura tags similares existentes (reuso de tema)
    reused = False
    final_label = label
    # Ligados ANTES do try: a guarda do 4-bis os lê, e sem isto um erro aqui
    # deixaria lá um NameError engolido pelo except — a guarda desligaria em
    # silêncio, que é o pior modo de falha para uma flag.
    summary_emb = None
    existing_summaries: list = []
    try:
        from .embeddings import embed_one
        summary_emb = embed_one(summary_text)
        existing_summaries = [
            e for e in memory_store.episodic.entries + memory_store.semantic.entries
            if e.get("source_type") == "session_summary" and e.get("topic_tag")
        ]
        if existing_summaries:
            import numpy as np
            from sklearn.metrics.pairwise import cosine_similarity
            for prev in existing_summaries:
                prev_emb = np.array(prev.get("embedding", []), dtype=np.float32)
                if prev_emb.size == 0:
                    continue
                sim = float(cosine_similarity([prev_emb], [summary_emb])[0][0])
                if sim >= TAG_SIMILARITY_THRESHOLD:
                    final_label = prev.get("topic_tag", label)
                    reused = True
                    logger.info(
                        "[summary] tag reusada: %s (sim=%.3f com summary anterior)",
                        final_label, sim,
                    )
                    break
    except Exception as e:
        logger.debug("[summary] tag matching falhou: %s", e)

    # ── 4-bis) Guarda de duplicata (18/08/2026) ──────────────────────────────
    # O laço acima NÃO serve para isto e não foi tocado: ele para no PRIMEIRO
    # resumo acima de 0.75 (`break`), então o `sim` dele é o do primeiro
    # encontrado, não o máximo. Para decidir "isto já está gravado" é preciso o
    # MÁXIMO sobre todos — varredura completa, que só roda com flag ligada.
    #
    # Medido no store vivo: 4 dos 5 grupos de texto exatamente repetido são
    # session_summary. O passo 5 grava mesmo quando o passo 4 já detectou a
    # duplicata. Ver ACHADO_DUPLICATA_EXPLICA_DOMINANCIA.md (lab).
    _dup_sim: float | None = None
    _dup_de = ""
    try:
        from .config import (
            EDP_SUMMARY_TELEMETRY as _st,
            EDP_SUMMARY_DEDUP as _sd,
            SUMMARY_DEDUP_THRESHOLD as _lim,
        )
        if (_st or _sd) and existing_summaries and summary_emb is not None:
            import numpy as np
            from sklearn.metrics.pairwise import cosine_similarity
            # DEFEITO ACHADO POR TESTE (18/08/2026): `summary_emb` acima é o
            # embedding do texto NU, mas o passo 5 grava
            # `f"[session_summary] {summary_text}"` — com prefixo. O laço do
            # passo 4 compara portanto nu-contra-prefixado, e duplicata EXATA
            # dá 0.769, não 1.0. Ele passa raspando o TAG_SIMILARITY_THRESHOLD
            # de 0.75 por sorte, e um limiar de guarda calibrado para texto
            # igual (~1.0) seria INALCANÇÁVEL contra esse número.
            #
            # Aqui compara-se igual com igual: o texto COMO SERÁ GRAVADO.
            # A correção fica confinada a este bloco de propósito — mexer no
            # laço acima mudaria o reuso de rótulo com as flags desligadas, e
            # o §4.7 exige que desligada seja byte-idêntica.
            from .embeddings import embed_one as _e1
            _emb_como_gravado = _e1(f"[session_summary] {summary_text}")
            for prev in existing_summaries:
                prev_emb = np.array(prev.get("embedding", []), dtype=np.float32)
                if prev_emb.size == 0:
                    continue
                s = float(cosine_similarity([prev_emb], [_emb_como_gravado])[0][0])
                if _dup_sim is None or s > _dup_sim:
                    _dup_sim, _dup_de = s, str(prev.get("id") or "")
    except Exception as e:
        logger.debug("[summary] varredura de duplicata falhou: %s", e)

    try:
        from .config import (
            EDP_SUMMARY_DEDUP as _sd2,
            SUMMARY_DEDUP_THRESHOLD as _lim2,
        )
        _pula = bool(_sd2 and _dup_sim is not None and _dup_sim >= _lim2)
        from .runtime.pareto_store import emit_summary_write
        emit_summary_write(
            max_sim=_dup_sim,
            n_anteriores=len(existing_summaries),
            limiar=_lim2,
            gravou=not _pula,
            guarda_ativa=bool(_sd2),
            topic_tag=final_label,
            n_chars=len(summary_text),
        )
        if _pula:
            # NÃO grava. Devolve o mesmo formato dos callers (`label`, `reused`,
            # `entry_id`) para que websocket.py:1388, memory.py:337 e
            # consolidator.py:52 sigam funcionando sem saber desta guarda.
            logger.info(
                "[summary] duplicata PULADA sim=%.4f >= %.4f (de %s) label=%s",
                _dup_sim, _lim2, _dup_de[:8], final_label,
            )
            return {
                "summary":  summary_text,
                "label":    final_label,
                "reused":   True,
                "entry_id": _dup_de or None,
                "skipped_duplicate": True,
            }
    except Exception as e:
        logger.debug("[summary] guarda de duplicata falhou (ignorado): %s", e)

    # 5) Persiste como memória especial
    try:
        text_to_store = f"[session_summary] {summary_text}"
        # exp009 (H1 confirmada, limiares §6): sem os privilégios de nascença
        # (prioridade="alta" + epistemic_status="verified"), a dominância indevida
        # dos summaries no retrieve cai (86.7%→33% nas queries vagas) e a memória
        # de conteúdo volta ao topo, sem quebrar o caso legítimo. Defaults do add
        # valem: prioridade="media", epistemic_status="hypothesis".
        #
        # ERRATA 18/08/2026 — o texto acima fica, o escopo dele muda.
        # O 86.7%→33% foi medido no caminho COSSENO (o store `edp_data_exp009`
        # tem 813 eventos terminando em 02/07). Desde 08/07 o default é o
        # híbrido (config.py:53), onde `prioridade` só aparece numa f-string de
        # exibição (store.py:1898) e `epistemic_status` só na exclusão binária
        # de contradicted/quarantined (store.py:1654). Nenhum dos dois ordena.
        #
        # A ESCRITA ABAIXO NÃO MUDA e continua certa — não conceder privilégio
        # é correto em qualquer caminho. O que caducou é a CAUSA declarada: hoje
        # não há privilégio a conceder, então também não há efeito a atribuir.
        # A dominância medida em 18/08 acontece COM os defaults já aplicados, e
        # a causa é outra — os resumos estão DUPLICADOS no store (4 dos 5 grupos
        # de texto repetido). Ver ACHADO_DUPLICATA_EXPLICA_DOMINANCIA.md (lab) e
        # a guarda `EDP_SUMMARY_DEDUP` acima.
        #
        # Nota de método: este número não está registrado no próprio
        # pré-registro do exp009 — ele existe só aqui, num comentário de
        # produção. Ver §8-ter de `edp/lab/preregistro_experimento_009.md`.
        entry = memory_store.add(
            text=text_to_store,
            score=0.85,
            source=f"system:summary",
            confidence=0.85,
        )
        # Sobrescreve no objeto retornado E na entry persistida.
        # mem.add pode fazer cópia interna, então localiza por id.
        entry_id = entry.get("id")
        entry["source_type"] = "session_summary"
        entry["topic_tag"] = final_label
        entry["session_id"] = session_id
        # Atualiza também na lista persistida
        for e in memory_store.episodic.entries:
            if e.get("id") == entry_id:
                e["source_type"] = "session_summary"
                e["topic_tag"] = final_label
                e["session_id"] = session_id
                break
        # Força save
        memory_store.episodic._dirty = True
        memory_store.episodic.save()

        logger.info(
            "[summary] OK session=%s label=%s reused=%s | %s",
            session_id, final_label, reused, summary_text[:80],
        )
        return {
            "summary":  summary_text,
            "label":    final_label,
            "reused":   reused,
            "entry_id": entry.get("id"),
        }
    except Exception as e:
        logger.warning("[summary] falha ao salvar: %s", e)
        return None


def _heuristic_label(summary: str) -> str:
    """Fallback de label sem LLM: pega 2 substantivos mais frequentes."""
    words = re.findall(r"\b[a-záéíóúâêôãõç]{4,}\b", summary.lower())
    # Stopwords comuns pt-BR
    stop = {
        "sobre", "como", "para", "esse", "este", "essa", "esta",
        "porque", "porem", "mesmo", "ainda", "depois", "antes",
        "tambem", "quase", "muito", "pouco", "entao", "assim",
        "todos", "todas", "alguns", "algumas", "outros", "outras",
        "discutiu", "discussao", "conversa", "tema", "ponto",
    }
    freq: dict = {}
    for w in words:
        if w in stop:
            continue
        freq[w] = freq.get(w, 0) + 1
    top = sorted(freq.items(), key=lambda x: -x[1])[:2]
    if len(top) < 2:
        return "tema_geral"
    return f"{top[0][0]}_{top[1][0]}"[:40]
