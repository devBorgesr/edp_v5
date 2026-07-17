# Fase 0.5 — Medição do split-brain semântico (`semantic_memory.py`)

Só leitura. Complementa o achado do Item 3 de `FASE0_DIAGNOSTICO_HARDENING.md`
("run_pipeline() consulta um SemanticMemory paralelo e desatualizado em todo
turno vivo"). Todo trecho abaixo é fonte lida, com file:line — nenhuma
inferência.

---

## M1 — ALCANCE: o que `run_pipeline()` lê de `semantic_memory.py` chega ao prompt do LLM?

**Resposta: NÃO alcança.** Rastreio completo, do `retrieve()` até o ponto de descarte:

1. `mem_results = semantic_memory.retrieve(question, top_k=3)`
   (`edp/pipeline.py:327`) — a leitura em si, roda em todo turno.
2. `mem_results` tem exatamente 2 consumidores dentro de `run_pipeline()`:
   - **`retrieval_quality=float(len(mem_results)) / max(len(mem_results)+1, 1)`**
     (`edp/pipeline.py:408`) → vira campo de `AdaptiveState` →
     `adaptive_decision = ADAPTIVE_CONTROLLER.decide(adaptive_state)`
     (`edp/pipeline.py:410`) → usado **só** em
     `_trace("adaptive_controller", adaptive_decision.to_dict())`
     (`edp/pipeline.py:411`). `_trace()` só grava (`edp/pipeline.py:283-285`,
     `def _trace(stage, data): if debug: trace.append(...)`) **quando
     `debug=True`** — e os dois chamadores reais nunca passam `debug=True`
     (`edp/api/routes/llm.py:74`, `edp/api/routes/websocket.py:624-629`;
     default é `debug: bool = False`, `edp/pipeline.py:236`). Morre aqui,
     sem sequer popular `trace` no caminho vivo.
   - **`reflection = meta.reflect(context_items, mem_results)`**
     (`edp/pipeline.py:422`) — variável **nunca lida depois** (grep
     exaustivo de `reflection` em `pipeline.py`: só aparece nas linhas 422
     e 434/fallback; nenhuma leitura posterior). Morre por escopo, dead
     store.
3. O texto que de fato compõe `PipelineResult.context`/`.context_str`
   (`blocos_final`, `edp/pipeline.py`, construído por chunking + fusão
   do **texto de entrada**) não deriva de `mem_results` em nenhum ponto —
   confirmado por leitura de todo o corpo de `run_pipeline()`.
4. **Nenhum caller lê `.context`/`.context_str`/`.logs`/`.trace` de
   `PipelineResult`.** Grep exaustivo de `pres\.` nos dois únicos
   chamadores vivos:
   - `edp/api/routes/llm.py:76`: só `pres.reduction_pct`.
   - `edp/api/routes/websocket.py:633,645,647`: só `pres.reduction_pct` e
     `pres.aggregate_score` (este último é `ScoreVector` calculado de
     `chunk_score_vectors`/`chunk_sizes` — scoring do texto de entrada via
     `classify_score`/`compute_score`, `edp/pipeline.py:362-379`,
     **anterior** ao bloco de meta-reasoning/adaptive controller que usa
     `mem_results`; não há caminho de `mem_results` até `aggregate_score`).
5. O prompt real enviado ao LLM vem de um caminho **totalmente separado**:
   `EDPRuntime.chat()` → `_retrieve_context()`
   (`edp/llm_adapter.py:1990-2280`, mapeado na sessão exp016 anterior) —
   usa `self._memory` (`MemoryStore` de `edp/memory.py`), sem nenhuma
   referência a `semantic_memory.py`/`get_pipeline_memory()`.

**Ponto de descarte, resumido:** `mem_results` morre dentro de
`run_pipeline()` em duas variáveis não-lidas (`adaptive_decision.to_dict()`
só grava sob `debug=True`, nunca ativo no caminho vivo; `reflection`, dead
store). Nada derivado de `_concepts.json` sai da função.

---

## M2 — ESCRITA: o que popula `_concepts.json` hoje?

Nome real do arquivo: `<session_id>_concepts.json` (não `_concepts.json`
literal — `edp/semantic_memory.py:103`,
`self._path = MEMORY_DIR / f"{session_id}_concepts.json"`, flat sob
`sessions/`, sem subpasta de scope).

**Um único escritor vivo hoje:**

- `edp/pipeline.py:596-611`, dentro de `run_pipeline()`: `if
  learnable_blocks:` (populado em `edp/pipeline.py:536`, decisão de
  scoring sobre chunks do turno) → como o `memory_bridge` chega sempre
  `None` dos dois chamadores vivos (`isinstance(semantic_memory,
  MemoryBridgeV32)` é `False`), cai no `else`:
  `semantic_memory.consolidate_from_episodes(episodes)`
  (`edp/pipeline.py:609`).
- `SemanticMemory.consolidate_from_episodes()`
  (`edp/semantic_memory.py:123-189`): guarda contra episodes sem embedding
  (`MIN_EPISODES_MERGE`), clusteriza por cosine ≥ threshold, cria/reforça
  `Concept`, poda por `MAX_CONCEPTS`, e **grava incondicionalmente** via
  `self.save()` (`edp/semantic_memory.py:188`, dentro do `RLock`) sempre
  que o método roda e processa ao menos 1 cluster.

**Escritor morto (histórico, não roda):** `scheduler.py`'s
`_job_consolidate` também chamava `SemanticMemory.consolidate_from_episodes()`
inline (docstring do próprio `scheduler.py:9-10`) — mas `scheduler.py` tem
zero importadores vivos (confirmado na Fase 0, Item 3).

**Nenhum outro consumidor** de `edp.semantic_memory` existe no repo além
de `pipeline.py` e `scheduler.py` (grep exaustivo já feito na Fase 0, Item
3 — reconferido aqui, mesmo resultado: 2 arquivos, 1 vivo/1 morto).

**Conclusão prática:** o arquivo é escrito de verdade (não é write morto),
mas o conteúdo escrito nunca é lido por nada fora do próprio
`SemanticMemory` de `semantic_memory.py` (nem `retrieve()`, chamado no
mesmo turno, influencia o que sai da função, conforme M1).

---

## M3 — grep de 1 linha para o Daniel (Windows/PowerShell) rodar contra os `default_concepts.json` dos stores de teste

Conta quantos `Concept` cada arquivo tem (1 ocorrência de `"text":` por
concept) e mostra uma amostra do conteúdo — sem precisar parsear o JSON
por completo:

```powershell
Get-ChildItem -Path C:\ -Recurse -Filter "default_concepts.json" -ErrorAction SilentlyContinue | ForEach-Object { $n = (Select-String -Path $_.FullName -Pattern '"text"' -AllMatches).Matches.Count; Write-Host "$($_.FullName): $n concepts"; if ($n -gt 0) { Select-String -Path $_.FullName -Pattern '"text"\s*:\s*"[^"]{0,100}' -AllMatches | Select-Object -First 3 -ExpandProperty Matches | ForEach-Object { $_.Value } } }
```

Ajustar `-Path C:\` para a raiz correta se souber onde ficam os stores de
teste (mais rápido que buscar o disco inteiro) — ex.:
`-Path C:\edp_data_hybrid_test`, `C:\edp_data_exp016`.

O que essa saída informa: se `n=0` em todos os stores de teste, o
`_concepts.json` está vazio na prática (write nunca disparou, ou store
recente demais) — reforça ainda mais a decisão de aposentar sem perda
percebida. Se `n>0`, a amostra de texto mostra se há conteúdo
minimamente relevante (mesmo sem influenciar o prompt hoje) antes de
decidir deletar.

---

## Decisão pré-registrada — resultado

**M1 = NÃO alcança → branch "APOSENTAR neste ciclo" confirmado pela
medição.** Passos definidos previamente para a Fase 1 (não executados
agora — Fase 0.5 é só leitura):

1. Teste de caracterização: prompt final byte-idêntico ao atual, COM a
   consulta a `semantic_memory.retrieve()` presente (rede de segurança
   antes de mexer).
2. Remover a consulta (`mem_results = semantic_memory.retrieve(...)` e os
   2 usos mortos identificados em M1) de `run_pipeline()`.
3. Grep de outros consumidores — **já feito** nesta medição (M2) e na
   Fase 0 (Item 3): zero consumidores vivos além do próprio
   `pipeline.py`. `scheduler.py` (morto) pode ser removido junto, mesma
   evidência.
4. Deleção de `edp/semantic_memory.py` com a evidência acima anexada ao
   commit.

Isso NÃO foi iniciado nesta sessão — Fase 0.5 é medição, não execução.

---

## Fim da Fase 0.5 — parando conforme instrução
