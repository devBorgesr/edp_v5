"""
edp.runtime.cognitive_decisions — Extrator de decisões estruturadas para
memórias cognitive em background.

Princípio (Commit 3d, Renato 06/06/2026):
  Sprint mode já extrai decisions estruturadas via comentário HTML que o
  modelo gera embutido na resposta. Cognitive mode NÃO tem essa instrução
  — respostas livres ficam sem indexação estruturada de conceitos/asserções.
  Commit 3d resolve via polling em background:
    1. Loop varre entries cognitive sem decisions há > 60s (frescos mas estáveis)
    2. Chama Haiku-mini com prompt estruturado
    3. Grava JSON resultante em entry["cognitive_decisions"]
  Custo: ~$0.001 por extração × 50 entries/dia = ~$0.05/dia
  Latência: ZERO no hot path conversacional (background isolado)

Escopo do Commit 3d (mínimo, validado):
  - Polling periódico via background loop (não event-driven)
  - Max 3 extrações por tick (proteção de custo)
  - Filtros: layer=episodic, scope=cognitive, source_type=llm_response,
    sem cognitive_decisions, idade < 24h
  - JSON estruturado: key_assertion, concepts, domain
  - Backward-compat: entries sem campo continuam funcionando
  - Env var EDP_COGNITIVE_DECISIONS_ENABLED para desligar
  - Reusa LLM provider já configurado no runtime

Escopo NÃO incluído (commits futuros):
  - Uso das decisions extraídas em retrieve (Commit 3d-β)
  - Re-extração quando entry é modificado
  - Versionamento de schema das decisions
  - Decisions para sprint (já existe via HTML comment)

Decisões arquiteturais (D1-D6, Renato 06/06/2026):
  D1 = β: polling periódico (não event-driven em hot path)
  D2 = prompt fixo estruturado (key_assertion + concepts + domain)
  D3 = α: campo cognitive_decisions no entry (não arquivo separado)
  D4 = 4 filtros (layer, scope, source_type, age + presence)
  D5 = α: um-por-um, max 3/tick (robustez + custo controlado)
  D6 = env var EDP_COGNITIVE_DECISIONS_ENABLED (default True)

Princípios EDP aplicados:
  1. Base Sólida          → falha não afeta resposta ao usuário (background)
  2. Soberania Progressiva → usa cloud agora, swap fácil para local depois
  3. Arquitetura Forward  → cognitive_decisions schema permite consumidores
  4. Solidificação        → reusa bgloop + provider + memory validados
  5. Reuso Infraestrutura → BackgroundJob + LLMProvider + memory.entries
  6. Retrieval Adaptativo → (futuro) concepts/domain podem refinar retrieval
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, asdict, field
from typing import Any, List, Optional

from ..clock import now as _now
from .background_loop import BackgroundJob

logger = logging.getLogger("edp.runtime.cognitive_decisions")


# ── Constantes ───────────────────────────────────────────────────────────────

# Campo adicionado aos entries (chave reservada)
FIELD_COGNITIVE_DECISIONS = "cognitive_decisions"

# Limites
MAX_EXTRACTIONS_PER_TICK = 3        # max entries processadas por tick
MAX_ENTRY_AGE_SEC        = 86400    # 24h — não reprocessa histórico velho
MIN_ENTRY_AGE_SEC        = 60       # 60s — espera entry estabilizar antes
MAX_PROMPT_TEXT_LEN      = 3000     # corta Q+A para limitar tokens entrada
EXTRACTION_TIMEOUT_SEC   = 15.0     # timeout LLM call

# Prompt fixo. Estruturado para JSON estrito.
EXTRACT_PROMPT_SYSTEM = """Você é um extrator de decisões estruturadas.

Receba uma resposta técnica (formato "Q: ...\\nA: ...") e retorne JSON COM 
EXATAMENTE estes 3 campos:
  - "key_assertion": afirmação central da resposta em <= 80 chars
  - "concepts": lista de 1 a 5 conceitos técnicos mencionados (strings curtas)
  - "domain": área técnica primária em 1-3 palavras (ex: "redis cache", 
              "java concurrency", "react hooks")

REGRAS RÍGIDAS:
  - Responda APENAS o JSON, sem texto antes/depois, sem markdown fence
  - Campos OBRIGATÓRIOS, na ordem indicada
  - JSON deve ser parseável pelo Python json.loads()
  - Se a resposta for muito vaga, use valores genéricos mas válidos

EXEMPLO de resposta esperada:
{"key_assertion":"Redis é melhor que Memcached para sessões web","concepts":["redis","memcached","cache","ttl","persistência"],"domain":"web caching"}"""


# ── Dataclass ────────────────────────────────────────────────────────────────

@dataclass
class CognitiveDecisions:
    """
    Resultado da extração estruturada de uma entry cognitive.

    Atributos:
      key_assertion  Afirmação central em <= 80 chars
      concepts       Lista de 1-5 conceitos técnicos
      domain         Área técnica em 1-3 palavras
      extracted_at   Timestamp epoch float da extração
      model_used     Identificador do modelo que extraiu (para audit)
    """
    key_assertion: str
    concepts:      List[str]
    domain:        str
    extracted_at:  float
    model_used:    str = "unknown"

    def to_dict(self) -> dict:
        """Serialização para gravação em entry."""
        return asdict(self)

    @classmethod
    def from_json_str(
        cls, raw: str, model_used: str = "unknown",
    ) -> Optional["CognitiveDecisions"]:
        """
        Parse seguro de string JSON retornada pelo LLM.

        Robustez:
          - Tenta remover fences markdown (```json ... ```)
          - Valida que campos obrigatórios existem
          - Trunca key_assertion a 80 chars (modelo pode estourar)
          - Limita concepts a 5 elementos
          - Retorna None em qualquer erro de parsing/validação
        """
        if not raw or not isinstance(raw, str):
            return None
        cleaned = raw.strip()
        # Remove fence markdown se LLM teimou em colocar
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            # Commit δ (07/06/2026): DEBUG→WARNING. LLM retornou lixo,
            # quero saber empiricamente (Gauss/Bayes do parser).
            logger.warning(
                "[cog_dec] JSON inválido: %s | raw=%r", e, cleaned[:200],
            )
            return None
        if not isinstance(data, dict):
            logger.warning(
                "[cog_dec] JSON não é dict: %s", type(data).__name__,
            )
            return None
        # Valida campos obrigatórios
        ka = data.get("key_assertion")
        cs = data.get("concepts")
        dm = data.get("domain")
        if not isinstance(ka, str) or not ka.strip():
            return None
        if not isinstance(cs, list):
            return None
        if not isinstance(dm, str) or not dm.strip():
            return None
        # Sanitiza: trunca, limita lista
        ka = ka.strip()[:80]
        dm = dm.strip()[:50]
        # Mantém só strings válidas, max 5
        concepts_clean = [
            c.strip()[:50]
            for c in cs
            if isinstance(c, str) and c.strip()
        ][:5]
        if not concepts_clean:
            return None
        return cls(
            key_assertion = ka,
            concepts      = concepts_clean,
            domain        = dm,
            extracted_at  = _now(),
            model_used    = model_used,
        )


# ── Extrator principal ───────────────────────────────────────────────────────

class CognitiveDecisionsExtractor:
    """
    Extrai decisions estruturadas para entries cognitive em background.

    Uso típico: criar via factory `make_extraction_job(runtime)` e registrar
    no background_loop. Job dispara periodicamente, varre entries pendentes,
    chama LLM para cada uma.

    Estado interno: contadores cumulativos para observabilidade.
    """

    def __init__(
        self,
        runtime,
        max_per_tick:        int = MAX_EXTRACTIONS_PER_TICK,
        max_entry_age_sec:   int = MAX_ENTRY_AGE_SEC,
        min_entry_age_sec:   int = MIN_ENTRY_AGE_SEC,
        timeout_sec:         float = EXTRACTION_TIMEOUT_SEC,
    ) -> None:
        self.runtime              = runtime
        self.max_per_tick         = max_per_tick
        self.max_entry_age_sec    = max_entry_age_sec
        self.min_entry_age_sec    = min_entry_age_sec
        self.timeout_sec          = timeout_sec
        self._stats = {
            "ticks":      0,
            "extracted":  0,
            "failed":     0,
            "skipped":    0,
            "started_at": _now(),
        }

    def stats(self) -> dict:
        """Estatísticas operacionais (para dashboard / debug)."""
        return dict(self._stats)

    def _is_extraction_enabled(self) -> bool:
        """Consulta env var (re-lida a cada chamada para permitir toggle live)."""
        val = os.environ.get("EDP_COGNITIVE_DECISIONS_ENABLED", "true").strip().lower()
        return val in ("true", "1", "yes", "on")

    def _select_pending_entries(self) -> List[dict]:
        """
        Seleciona até max_per_tick entries que precisam de extração.

        Filtros (todos AND):
          - layer == "episodic"
          - scope cognitive (não sprint — sprint já tem via HTML)
          - source_type == "llm_response" (formato Q+A)
          - cognitive_decisions ausente OU None
          - timestamp dentro [now - max_age, now - min_age]

        Robustez:
          - Erro acessando memory → lista vazia
          - Entries malformados → ignorados silenciosamente

        Commit 3d-fix4 (Renato, 07/06/2026): usa registry.get_memory()
        (instância ÚNICA compartilhada com endpoints/WebSocket) em vez de
        runtime._memory (instância duplicada). Sem isso, modificações em
        memória ficam em cópia diferente do que é persistido pelos hooks
        de WebSocket. Bug 4 descoberto empiricamente: extrações apareciam
        no log mas disco mostrava 0/N. Causa: 2 instâncias MemoryStore
        separadas no EDP (registry + runtime).
        """
        mem = self._get_memory_store()
        if mem is None:
            return []
        try:
            # Acessa scope cognitive diretamente (não delegando via property,
            # porque _active_scope pode estar em sprint)
            cog_view = getattr(mem, "_cognitive_view", None)
            if cog_view is None:
                # Commit δ (07/06/2026): DEBUG→WARNING. Falha estrutural grave.
                logger.warning("[cog_dec] _cognitive_view não disponível em mem")
                return []
            entries = list(cog_view.episodic.entries)
        except Exception as e:
            logger.warning("[cog_dec] erro lendo entries: %s: %s", type(e).__name__, e)
            return []
        now_ts = _now()
        max_oldest = now_ts - self.max_entry_age_sec
        min_newest = now_ts - self.min_entry_age_sec
        pending: List[dict] = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            if e.get("layer") != "episodic":
                continue
            if e.get("source_type") != "llm_response":
                continue
            if e.get(FIELD_COGNITIVE_DECISIONS) is not None:
                continue
            ts = e.get("timestamp") or e.get("ultimo_acesso")
            if ts is None:
                continue
            try:
                tsf = float(ts)
            except (ValueError, TypeError):
                continue
            if tsf < max_oldest:
                continue
            if tsf > min_newest:
                continue
            pending.append(e)
            if len(pending) >= self.max_per_tick:
                break
        return pending

    def _get_memory_store(self):
        """
        Retorna a instância ÚNICA de MemoryStore via registry.

        Commit 3d-fix4: descoberta mecânica de que EDP tem 2 instâncias
        de MemoryStore separadas — uma em registry._memories (usada por
        endpoints e WebSocket), outra em runtime._memory (criada em
        llm_adapter). Modificações no runtime NÃO são visíveis no disco
        porque endpoints sobrescrevem com a outra instância.

        A correta para extração é a do REGISTRY (compartilhada).
        """
        try:
            from . import registry
            session_id = getattr(self.runtime, "session_id", "default")
            mem = registry.get_memory(session_id)
            if not registry.is_valid(mem):
                logger.warning(
                    "[cog_dec] registry.get_memory retornou marcador de erro: %s",
                    registry.get_error(mem),
                )
                return None
            return mem
        except Exception as e:
            logger.warning(
                "[cog_dec] falha obtendo memory via registry: %s: %s",
                type(e).__name__, e,
            )
            return None

    async def _extract_for_entry(self, entry: dict) -> Optional[CognitiveDecisions]:
        """
        Chama LLM com prompt estruturado para 1 entry. Retorna
        CognitiveDecisions ou None se falhou.

        Commit 3d-fix2 (06/06/2026): usa runtime._client.complete (LLMClient)
        em vez de tentar acessar provider diretamente. Atributo correto é
        _client, não _llm_provider. Confirmado mecanicamente em llm_adapter.py.

        LLMClient.complete(prompt, system="") -> str é a interface canônica
        usada em todo o EDP para completions one-shot (não-streaming).
        """
        text = entry.get("text") or ""
        if not text:
            return None
        prompt_user = text[:MAX_PROMPT_TEXT_LEN]
        try:
            client = getattr(self.runtime, "_client", None)
            cfg    = getattr(self.runtime, "_llm_config", None)
            if client is None or cfg is None:
                logger.warning(
                    "[cog_dec] LLM client/config não configurado, pulando "
                    "(client=%s, cfg=%s)", client is not None, cfg is not None,
                )
                return None
            # LLMClient.complete é SÍNCRONO — roda em thread pool para não
            # bloquear o event loop do asyncio
            response_text = await asyncio.wait_for(
                asyncio.to_thread(
                    client.complete,
                    prompt_user,
                    EXTRACT_PROMPT_SYSTEM,
                ),
                timeout=self.timeout_sec,
            )
            model_used = getattr(cfg, "model", "unknown")
            cd = CognitiveDecisions.from_json_str(response_text, model_used=model_used)
            if cd is None:
                logger.warning(
                    "[cog_dec] parsing falhou para entry id=%s | response_preview=%r",
                    entry.get("id"), (response_text or "")[:200],
                )
            return cd
        except asyncio.TimeoutError:
            logger.warning(
                "[cog_dec] timeout (%ds) extraindo entry id=%s",
                self.timeout_sec, entry.get("id"),
            )
            return None
        except Exception as e:
            logger.warning(
                "[cog_dec] erro extraindo entry id=%s: %s: %s",
                entry.get("id"), type(e).__name__, str(e)[:150],
            )
            return None

    async def _call_provider(self, *args, **kwargs):
        """
        DEPRECATED — mantido por compatibilidade com testes que chamavam
        diretamente. Implementação real agora está em _extract_for_entry.

        Não usado pelo job em produção.
        """
        raise NotImplementedError(
            "_call_provider foi consolidado em _extract_for_entry (Commit 3d-fix2)"
        )

    def _persist_decisions(self, entry: dict, cd: CognitiveDecisions) -> bool:
        """
        Grava cognitive_decisions no entry. Marca dirty no episodic para
        flush no próximo ciclo. Retorna True se sucesso.

        Commit 3d-fix4: usa registry.get_memory (instância única) em vez
        de runtime._memory. Crítico para garantir que mudanças aparecem
        no disco que endpoints leem.
        """
        try:
            entry[FIELD_COGNITIVE_DECISIONS] = cd.to_dict()
            mem = self._get_memory_store()
            if mem is None:
                return False
            # Sinaliza dirty no scope cognitive específico
            try:
                cog_view = getattr(mem, "_cognitive_view", None)
                ep = cog_view.episodic if cog_view else None
                if ep is not None and hasattr(ep, "_dirty"):
                    ep._dirty = True
                    ep._pending_writes = getattr(ep, "_pending_writes", 0) + 1
            except Exception:
                pass
            return True
        except Exception as e:
            logger.warning("[cog_dec] erro persistindo decisions: %s: %s", type(e).__name__, e)
            return False

    async def extract_pending(self) -> None:
        """
        Função principal chamada pelo background job. Varre entries
        pendentes, extrai decisions para cada uma, persiste.

        Robustez:
          - Falha em 1 entry NÃO interrompe processamento dos outros
          - Timeout por entry isolado
          - Sem persistência distribuída — entries em memória recebem update

        Commit 3d-fix3 (Renato, 06/06/2026): força flush em disco após
        processar batch. Sem isso, mudanças ficam só em memória — batch
        flush padrão exige 50 writes acumulados, e extrações ocorrem em
        ritmo bem mais lento (1-3 por tick a cada 60s). Bug observado
        empiricamente: log "[cog_dec] extraído" aparecia mas disco continuava
        com 0 entries persistidas.
        """
        self._stats["ticks"] += 1
        if not self._is_extraction_enabled():
            return
        pending = self._select_pending_entries()
        if not pending:
            return
        logger.info(
            "[cog_dec] processando %d entries pendentes (tick #%d)",
            len(pending), self._stats["ticks"],
        )
        n_extracted_this_tick = 0
        for entry in pending:
            try:
                cd = await self._extract_for_entry(entry)
                if cd is None:
                    self._stats["failed"] += 1
                    continue
                if self._persist_decisions(entry, cd):
                    self._stats["extracted"] += 1
                    n_extracted_this_tick += 1
                    logger.info(
                        "[cog_dec] extraído entry id=%s | domain=%s | concepts=%d",
                        entry.get("id"), cd.domain, len(cd.concepts),
                    )
                else:
                    self._stats["failed"] += 1
            except Exception as e:
                self._stats["failed"] += 1
                logger.warning(
                    "[cog_dec] erro processando entry: %s: %s",
                    type(e).__name__, str(e)[:150],
                )

        # ── Commit 3d-fix3: flush explícito após batch ────────────────────
        # Memory padrão usa batch flush (50 writes acumuladas → save).
        # Extrações são bem mais lentas (1-3 por tick / 60s), nunca atingem
        # 50 sem retrieve no meio. Solução: flush explícito ao final do tick
        # se houve ao menos 1 extração persistida.
        #
        # Commit 3d-fix4: usa registry.get_memory (instância única) e
        # eleva log de DEBUG para INFO para diagnóstico visível.
        if n_extracted_this_tick > 0:
            try:
                mem = self._get_memory_store()
                if mem is None:
                    logger.warning("[cog_dec] flush impossível — memory store indisponível")
                else:
                    cog_view = getattr(mem, "_cognitive_view", None)
                    ep = cog_view.episodic if cog_view else None
                    if ep is not None and hasattr(ep, "flush"):
                        ep.flush()
                        logger.info(
                            "[cog_dec] flush em disco | extraídas neste tick=%d | path=%s",
                            n_extracted_this_tick, getattr(ep, "path", "?"),
                        )
                    else:
                        logger.warning(
                            "[cog_dec] episodic.flush() não disponível (ep=%s)", ep,
                        )
            except Exception as e:
                logger.warning(
                    "[cog_dec] flush em disco falhou: %s: %s",
                    type(e).__name__, str(e)[:150],
                )


# ── Factory para BackgroundJob ───────────────────────────────────────────────

def make_extraction_job(
    runtime,
    interval_sec: float = 60.0,
    priority:     int = 3,
) -> BackgroundJob:
    """
    Cria BackgroundJob configurado para o extractor.

    Args:
      runtime:      EDPRuntime já inicializado (com LLM provider)
      interval_sec: frequência do polling (default 60s — equilibra
                    custo e frescor)
      priority:     3 (baixa — não interfere com jobs críticos)

    Retorna BackgroundJob pronto para `loop.register_job()`.

    Princípio Solidificação Iterativa: extractor é stateful (contadores),
    closure captura instância única — todas as execuções do job operam
    no mesmo extractor.
    """
    extractor = CognitiveDecisionsExtractor(runtime)
    async def _job_func():
        await extractor.extract_pending()
    return BackgroundJob(
        name                 = "cognitive_decisions_extractor",
        func                 = _job_func,
        interval_sec         = interval_sec,
        priority             = priority,
        suspend_on_pressure  = True,   # extração é luxo, suspende em WARNING+
        timeout_sec          = EXTRACTION_TIMEOUT_SEC * MAX_EXTRACTIONS_PER_TICK + 10,
        enabled              = True,
    )
