# EDP v5 — Kernel de memória e governança epistêmica para LLM

**Não é um chatbot, não é AGI.** É um runtime de memória persistente para
LLMs — WebSocket + FastAPI, embeddings locais, retrieval híbrido, e uma
camada de governança que trata **confiabilidade da memória** como estado
de primeira classe (`contestado`/`quarentenado`/`hipótese`), não como
detalhe de implementação.

Este é o **kernel** de um ecossistema de três repositórios
(`NORTE.md §1`) — ver §6.

---

## 1. O que é (escopo declarado)

O EDP recebe mensagens via WebSocket, recupera contexto relevante de
memória episódica e semântica antes de cada resposta do LLM, e persiste
o turno com rastreabilidade de proveniência (*lineage*) por resposta.

O diferencial genuíno, comparado a produtos de mercado (Mem0, memória
nativa de assistentes, LangChain memory): a maioria não modela
confiabilidade da memória como estado — grava e recupera. O EDP marca
memória como `contradicted`/`quarantined`/`hypothesis` e propaga isso ao
ranking (`AVALIACAO_MEMORIA_VS_MERCADO.md`).

**O que este repositório não é:** um produto pronto para múltiplos
usuários em escala. Rodou 36 dias em produção real, single-user,
sem crash reportado nesse período — não há dado de comportamento em
volume 10×/100× (`AVALIACAO_ENGENHARIA_EDP.md §7`).

---

## 2. Arquitetura atual (verificada em 11/08/2026, não herdada)

```
edp/
├── api/            (18 arquivos) — FastAPI app, lifespan, rotas REST/WS
├── memory/         (4 arquivos)  — MemoryStore, EpisodicMemory,
│                                   SemanticMemory, atomic_io (quarentena)
├── runtime/        (17 arquivos) — background jobs: cognitive_decisions,
│                                   contradiction_flagger, auto_consolidation,
│                                   pareto_store, health_index, lineage
├── llm/            (6 arquivos)  — providers (Anthropic, Ollama), model_router
├── lab/            (18 arquivos) — experimentos pré-registrados (exp001-exp010)
├── ingest/         (5 arquivos)  — WebSocket live-feed do sensor externo
├── tools/          (8 arquivos)  — ferramentas do agente
├── profiles/       (6 arquivos)  — seleção/rastreio de perfil de uso
├── observability/  (3 arquivos)  — métricas
└── dashboard/      — estático (CSS/JS/HTML), zero .py, servido pela API

+ 40 módulos de topo (lista completa, `find edp -maxdepth 1 -name "*.py"`):
  adaptive_controller.py, affective_calibration.py, analytics.py (†),
  attention.py, blocks.py, cache.py, clock.py, cognitive_scheduler.py,
  compression.py, config.py, consolidation.py, context_builder.py,
  context_debug.py, co_occurrence.py, echo_chamber.py, embeddings.py,
  epistemic_classifier.py, exceptions.py, failsafe.py, filters.py,
  learning_gate.py, llm_adapter.py, memory_classifier.py,
  meta_reasoner.py, metrics.py, model_router.py,
  pipeline.py, pressure.py, reranker.py (†), retrieval.py,
  retrieval_hybrid.py, schema_v1.py, scoring.py, session_summary.py,
  temporal.py, trajectory.py, types.py, vector_store.py,
  wiki.py, write_provenance.py

  (†) sem importador em todo o repositório. A marca deixou de ser prosa:
  `tests/test_catalogo_de_modulos_mortos.py` recalcula o conjunto por AST
  e quebra o build se a lista, a contagem ou os (†) divergirem.
```

Este mapa substitui a categorização anterior do README
(CAMADA COGNITIVA / MEMÓRIA / HOMEOSTASE / INTEGRAÇÃO / OBSERVABILIDADE),
que descrevia módulos removidos em 24/06/2026 — ver §4 do relatório de
mudanças.

**Caminho vivo:** `run.py:serve()` → uvicorn → `edp/api/main.py` →
14 routers (`live_feed`, `health`, `memory`, `metrics`, `llm`,
`websocket`, `dashboard_state`, `providers`, `flags`, `mode`,
`cognitive_decisions`, `lineage`, `live_feed_ws`, `wiki`) →
`edp/api/routes/websocket.py` (handler por turno) → `run_pipeline()`
(`pipeline.py`) → `edp/memory/store.py` (`MemoryStore`) →
`edp/llm_adapter.py` (`EDPRuntime`).

Retrieval vivo: `edp/retrieval_hybrid.py` (BM25 + vetorial, RRF), atrás
da flag `EDP_HYBRID_RETRIEVAL` (default ON). `edp/retrieval.py` (que usa
FAISS/hnswlib diretamente) **não está no caminho servido**, mas é
importado por `run.py:187` no subcomando `bench_retrieval` — assim como
`edp/vector_store.py` em `run.py:250`. Não são código morto.

> **Errata — 13/08/2026.** Até esta data este parágrafo dizia que
> `retrieval.py`, `vector_store.py` e `memory_graph.py` estavam "sem
> nenhum importador", catalogados assim desde 22/07/2026. A recontagem
> por AST mostrou que o catálogo errava nas **duas** direções: os dois
> primeiros têm importador real em `run.py`, e `analytics.py` e
> `reranker.py` estavam mortos sem marca. Só `memory_graph.py` estava
> certo — e foi deletado em 13/08 (ver
> `docs/design_wiki_conversas.md` §5, que refuta aresta por similaridade
> de embedding). O erro sobreviveu três semanas porque "sem importador"
> era conferido por leitura, não por ferramenta; agora é build gate.

---

## 3. O que está validado hoje, com evidência

| item | evidência |
|---|---|
| 298 testes passam, 1 deselecionado | `pytest -q`, 11/08/2026, Python 3.11 — comando exato no §5 |
| Crash de boot por JSON truncado — corrigido | `edp/memory/store.py:348`, `_load_json_or_quarantine()`; Dívida #53 FECHADA, `docs/DIVIDAS.md` |
| Retrieval híbrido (BM25+vetorial+RRF) | exp010, mergeado; flag `EDP_HYBRID_RETRIEVAL` default ON |
| Governança epistêmica (piso + exclusão de conteúdo tóxico) | exp012 (regra composta R4/OR confirmada), exp016 |
| Dedup do retrieve (read-side) | exp017, H1 PASSA com critério conjuntivo integral |
| Flag-off byte-idêntico aplicado a cada feature nova | `tests/test_flag_off_byte_identical.py` |
| Método de pré-registro é real, não teatro | `AVALIACAO_ENGENHARIA_EDP.md`, dimensão 4, [P] 9/10 — a única nota 9 do scorecard |

**Refutado por dado, registrado como resultado (não escondido):**
honeypot de resposta (H0 vence, seletividade invertida), exp015
(proibição em prompt não evita reafirmação de desqualificação),
"Wiki de conversas camada 3" (2/5 alvos, critério era ≥3), Gap Score em
4 implementações distintas (bruta/IDF/IDF⁰/Haiku, todas falham a mesma
condição). Detalhe completo:
[`docs/edp_metodologia_v5.md §3`](docs/edp_metodologia_v5.md).

---

## 4. Limitações e dívidas conhecidas

- **`docs/DIVIDAS.md` cobre 3 de 21 dívidas referenciadas em código
  (14,3%)** — recalculado nesta rodada; `NORTE.md` estimava ~12% sem
  medir. Comando: `grep -rohE "[Dd]ívida #[0-9]+[a-z]?" edp/ tests/ --include="*.py" | sort -u`.
- **`score=0.65` hardcoded** em 4 locais (`websocket.py:1214,1236` e
  `llm_adapter.py:2892,2901` — os dois últimos não citados pelo
  `NORTE.md`, achados nesta rodada), `DEDUP_THRESH=0.75` e
  `anchor_boost=1.20` sem calibração documentada.
- **4 sinais computados nunca lidos**: `cognitive_decisions` fora do
  ranking, `contradiction_flagger.scan_results()` com retorno
  descartado, `reflection.reweights` nunca aplicado,
  `RETRIEVAL_BACKEND` decorativo — os quatro reverificados hoje, direto
  no código, mesmo estado do catálogo de 22/07/2026.
- **Suite 100% sintética** — nunca roda contra store real; marker
  `live_store` existe e não tem teste.
- **Duas dependências usadas e não declaradas**: `ntplib`
  (`edp/clock.py`, ausente dos dois `requirements*.txt`) e `hnswlib`
  (`edp/retrieval.py`, só comentado no `requirements.txt`) — este
  segundo módulo é código morto (zero importadores), então a lacuna de
  dependência é de baixo risco prático, mas real.
- **Branch protection nunca configurada no GitHub** — CI roda, não
  bloqueia merge (`docs/RUNBOOK.md §11`, não reverificado nesta rodada).
- **9 de 30 branches remotas não estão mergeadas em `main`** e a mais
  recente delas (`exp015/strong-provenance`) não tem commit há mais de
  14 dias — critério de "ativa" declarado: commit nos últimos 14 dias
  relativos a 11/08/2026. Só `fix/toxic-guards` (mesma data) qualifica.

Inventário completo, com número e comando de reprodução:
[`docs/edp_metodologia_v5.md §4`](docs/edp_metodologia_v5.md).

---

## 5. Como rodar

### Instalar

```bash
pip install -r requirements.txt
# nota (11/08/2026): ntplib é usado por edp/clock.py e não está listado
# acima nem em requirements-test.txt — instale manualmente se faltar.
```

### Rodar a suíte de testes (reproduz o "298 passed" do §3)

```bash
pytest -q
```

### Subir a API

```bash
python run.py serve
# API:       http://localhost:8000
# dashboard: http://localhost:8000/dashboard
# docs:      http://localhost:8000/docs
# health:    http://localhost:8000/health
```

### Manter o grafo de código atualizado (graphify)

```bash
graphify update .
```

---

## 6. Onde isto se encaixa

Três repositórios, um produto (`NORTE.md §1`):

| repo | papel |
|---|---|
| **`edp_v5` (este)** | **Kernel** — memória, governança epistêmica, retrieval, API |
| `lab_edp_novo` | Certificação — experimentos pré-registrados, oráculo externo |
| `sf_exportador` | Sensor + Copiloto — captura passiva, análise, interface |

Este README cobre só o kernel. Ver `NORTE.md` para o norte do
ecossistema inteiro.

---

## 7. Metodologia

O método de pesquisa deste projeto — pré-registro, controle negativo,
errata pública, feature flag com prova de byte-idêntico — é citado
externamente como o ativo mais forte do projeto (nota 9/10 numa
auditoria de engenharia onde o código tirou de 4 a 7). Descrição
completa, com evidência re-verificada nesta data:
[`docs/edp_metodologia_v5.md`](docs/edp_metodologia_v5.md).

Regras não-negociáveis para qualquer tarefa neste repositório:
[`NORTE.md`](NORTE.md).

---

*Última verificação: 2026-08-11, commit `556b4d9`. Este README é
falsificável como o resto do método — se um número aqui não bater ao
reproduzir o comando citado, é o número que está errado, não o comando.*
