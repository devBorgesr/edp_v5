# AUDITORIA_CURADORIA — EDP v5

> Branch: `auditoria-curadoria` | Baseline commit: `feb0db9`
> Repositório: `devborgesr/edp_v5` | Data: 2026-06-24
> Regra: toda afirmação tem `arquivo:linha` ou a frase "nenhuma evidência encontrada".
> Ambiente de auditoria: sem `.venv` — análise estática completa; execução (Fase 5b) pendente no ambiente com dependências.

---

## FASE 0 — Preparação

| Item | Resultado |
|---|---|
| Branch de trabalho | `auditoria-curadoria` criada de `claude/glossopetrae-analysis-5fqfys` |
| Commit baseline | `feb0db9 baseline antes da curadoria` — ponto de retorno |
| Árvore antes do baseline | limpa (zero mudanças não commitadas) |
| Total de arquivos `.py` | 128 arquivos / 42.244 linhas |
| `sentence_transformers` | ❌ ausente |
| `fastapi` | ❌ ausente |
| `numpy` | ❌ ausente |
| `sklearn` | ❌ ausente |
| `uvicorn` | ❌ ausente |
| Consequência | Fase 5a (poda estática) executável; Fase 5b (servidor vivo) pendente |

### Top 10 maiores arquivos

| # | Arquivo | Linhas |
|---|---|---|
| 1 | `edp/llm_adapter.py` | 2.722 |
| 2 | `benchmark_edp.py` | 1.876 |
| 3 | `edp/memory.py` | 1.787 |
| 4 | `edp/api/routes/memory.py` | 1.775 |
| 5 | `edp/api/routes/websocket.py` | 1.383 |
| 6 | `edp/adaptive_controller.py` | 1.209 |
| 7 | `edp/api_v2.py` | 1.110 |
| 8 | `edp/echo_chamber.py` | 1.100 |
| 9 | `edp/snapshot_manager.py` | 1.048 |
| 10 | `edp/lab/scorer.py` | 1.025 |

---

## FASE 1 — Verificação cética (20 afirmações auditadas)

**Resultado: 19 CONFIRMADAS, 1 CORRIGIDA.**

### 1.1 Dois sistemas paralelos / ponte nunca ligada

| Afirmação | Status | Linha atual | Evidência |
|---|---|---|---|
| `MemoryBridgeV32._v32_store` nasce `None` | CONFIRMADA | `pipeline.py:90` | `self._v32_store = None  # injeta OrchestratorV32 quando disponível` |
| `register_v32_store` zero chamadores no repo inteiro | CONFIRMADA | `pipeline.py:93` | `grep -rn "register_v32_store"` → única ocorrência é a definição |
| Ramo v3.2 de `consolidate()` nunca executa | CONFIRMADA | `pipeline.py:107` | `if self._v32_store is not None:` — sempre False |
| `OrchestratorV32` instanciado só em `run.py` | CONFIRMADA | `run.py:219, 474` | Grep completo: zero instanciações em `edp/api/` |

### 1.2 Gargalo G1

| Afirmação | Status | Linha atual | Evidência |
|---|---|---|---|
| Re-embedding após dedup recalcula embeddings já computados | CONFIRMADA | `pipeline.py:390` | `chunk_embs = embed(chunks_deduped)` — descarta `chunk_embs` existentes |
| `run_pipeline` chamado em produção via websocket | CONFIRMADA | `websocket.py:629` | `lambda: run_pipeline(message, message, session_id=session_id)` |

**Nota:** G1 só executa quando `len(chunks_deduped) < len(chunks)` (`pipeline.py:389`) — não é sempre, mas quando há deduplicação real todo o trabalho de embedding anterior é descartado.

### 1.3 Módulos VIVOS vs MORTOS

| Módulo | Classificação afirmada | Status | Evidência |
|---|---|---|---|
| `contradiction_flagger` | VIVO | CONFIRMADA | `memory.py:1647` + 5 endpoints `flags.py` + `dashboard_state.py:61` |
| `auto_consolidation` | VIVO | CONFIRMADA | Background job em `api/main.py:213-224` |
| `lineage` | VIVO | CONFIRMADA | `websocket.py:1305-1313` |
| `cognitive_decisions` | VIVO | CONFIRMADA | Background job em `api/main.py:177` |
| `MetaStabilityController` morto no serve path | CORRIGIDA | Tem chamador real: `orchestrator_v32.py:188` — mas esse orquestrador só é instanciado de `run.py`. Conclusão prática: **morto no serve path**. |
| `SemanticBiodiversityEngine` morto | CONFIRMADA | Só em `orchestrator_v32.py:192` |
| `FractalPressureRegulator` morto | CONFIRMADA | Só em `orchestrator_v32.py:196` |
| `CognitiveEconomyEngine` morto | CONFIRMADA | Só em `orchestrator_v32.py:184` |
| `RetrievalStormGuard` morto | CONFIRMADA | Só em `orchestrator_v32.py:180` |
| `AsyncDecisionGraph` morto | CONFIRMADA | Só em `orchestrator_v32.py:176` |
| `get_causal_path` zero chamadores | CONFIRMADA | `decision_graph_v32.py:164` — grep: zero chamadores externos |
| `request_*` da economy sem chamador externo | CONFIRMADA | `economy.py:206-227` — zero chamadores fora de `economy.py` e `orchestrator_v32.py` |
| `update_consolidation` sem chamador → dimensão nunca atualiza | CONFIRMADA | `pressure_monitor.py:143` — grep: zero chamadores externos |

### 1.4 Loops abertos

| Sinal | Status | Linha | Evidência |
|---|---|---|---|
| `scan_results()` retorno descartado | CONFIRMADA | `memory.py:1647` | `get_flagger().scan_results(final_top)` sem atribuição, dentro de `try/except: pass` |
| `cognitive_decisions` destruído em `merge_cluster` | CONFIRMADA | `consolidation.py:140-149` | Dict retornado: 9 campos explícitos, `cognitive_decisions` ausente |
| `cognitive_decisions` ausente da fórmula de ranking | CONFIRMADA | `memory.py:723-726` | `sim × d × prio × ab × epi_mult × src_weight × dom_penalty × anchor_boost × session_boost` — 9 fatores, nenhum lê o campo |
| `quality_score` ignorado; `memory.add` usa `score=0.65` fixo | CONFIRMADA | `websocket.py:1212` | `memory.add(combined, score=0.65, ...)` hardcoded; `aggregate_score` só vai para `lineage_quality` |
| `adaptive_decision` só vai para `_trace` (debug=False) | CONFIRMADA | `pipeline.py:448-449` | `_trace("adaptive_controller", adaptive_decision.to_dict())` — variável nunca lida depois |
| `reflection` do MetaReasoner descartado | CONFIRMADA | `pipeline.py:460-472` | `reflection.` nunca aparece no arquivo após a linha 472; `reweights` computados, nunca aplicados |

---

## FASE 2 — Dúvidas empíricas respondidas

### 2.1 Há tráfego real e sustentado?

**VEREDITO: NÃO VERIFICÁVEL NESTE AMBIENTE.**

- `EDP_BASE_DIR` não definida; default em `config.py:9` é `/content/edp_v3_memory` (Google Colab)
- Busca em todo o filesystem: zero arquivos `episodic.json`, `lineage.jsonl`, `events.jsonl`
- Os dados vivem na máquina onde o EDP roda, não no repositório

**Impacto no roadmap:** Todo item que depende de dados reais (calibração de `promote_threshold`, análise de distribuição de acessos) está **bloqueado — rodar onde os dados vivem**.

---

### 2.2 O v3.2 era o futuro ou foi abandonado?

**VEREDITO: ABANDONADO. Evidência categórica.**

| Fato | Evidência |
|---|---|
| Todos os 9 arquivos v3.2 chegaram no **mesmo commit** | `14179eb` — 2026-05-20 |
| Foi um **upload em bloco** (50+ arquivos de uma vez) | `git show 14179eb --stat` |
| O caminho vivo teve **15+ commits** após 20/05 | Último: `952b9cd` — 2026-06-17 (28 dias depois) |
| Os arquivos v3.2 tiveram **1 único commit posterior** | `070ea53` — refatoração global `time.time()→clock.now()`, 16 arquivos |
| `register_v32_store` **nunca teve chamador** em nenhum commit | `git log -S "register_v32_store("` → apenas commit de criação |

O v3.2 chegou como bloco completo, foi imediatamente superado pelo desenvolvimento ativo do caminho vivo, e a ponte nasceu desligada sem nunca ter sido ligada em nenhum momento da história do repositório.

---

### 2.3 O v3.2 é sobre "memória de qualidade"?

**VEREDITO: v3.2 é 100% GOVERNANÇA. Ortogonal ao gap diagnosticado.**

| Módulo | O que decide | Classificação |
|---|---|---|
| `economy.py` | Budget cognitivo (tokens, recursão, embedding, snapshot) | GOVERNANÇA |
| `meta_stability.py` | Modo operacional do sistema (NORMAL→EMERGENCY) | GOVERNANÇA |
| `biodiversity.py` | Diversidade semântica, evita collapse e dominância | GOVERNANÇA |
| `storm_guard.py` | Circuit breaker para retrieval storms, recursion explosion | GOVERNANÇA |
| `pressure_regulator.py` | Pressão global regulada com hysteresis e EMA | GOVERNANÇA |
| `decision_graph_v32.py` | Causalidade de decisões, rastreamento de eventos | GOVERNANÇA |
| `snapshot_manager.py` | Consistência temporal geracional (CoW) | GOVERNANÇA |
| `orchestrator_v32.py` | Coordena os 7 anteriores | GOVERNANÇA |

Nenhum módulo v3.2 avalia qualidade da resposta do LLM, classifica memórias por utilidade, ou toca `memory.add`. O gap "nenhum sinal de qualidade retroalimenta o ranking" não seria fechado nem se o v3.2 fosse conectado. São problemas diferentes.

---

### 2.4 Os thresholds não calibrados

| Threshold | Valor | Documentação de calibração | Risco de calibrar |
|---|---|---|---|
| `SESSION_BOOST_FACTOR` | `1.60` | ✅ **DOCUMENTADA** — incidente Docker/Redis 04/06/2026 (`memory.py:76`) | Referência de boa prática |
| `promote_threshold` | `3` | ❌ Nenhuma — "mesmo default do endpoint" (`auto_consolidation.py:47`) | **BAIXO** — loop fechado, afinar parâmetro existente |
| `SIMILARITY_THRESHOLD` | `0.85` | ❌ Prometida, nunca executada — "palpite inicial. Commit 4 (Gauss) calibrará empiricamente no futuro" (`contradiction_flagger.py:73`) | **MÉDIO** — binário sem bounds; e retorno de `scan_results()` é descartado |
| `anchor_boost` | `1.20` | ❌ Analogia, não empírico — "empata com source_type external" (`memory.py:695`) | **BAIXO** — efeito local, campo opcional |
| Dominância trigger | `12%` | ❌ Nenhuma (`memory.py:664`) | **BAIXO** — proteção anti-monopólio |
| Dominância penalidade | `×0.70` | ❌ Nenhuma (`memory.py:691`) | **BAIXO** — efeito local |

**`promote_threshold` é loop FECHADO confirmado:** `consolidate_promote_only()` (`consolidation.py:261`) → `memory.semantic.promote(e)` → `MemoryStore.retrieve()` busca no scope semântico. Calibrar este threshold é afinar um parâmetro já conectado, bloqueado apenas por falta de dados reais.

---

## FASE 3 — Registro de curadoria

### 3.1 Tabela MÚSCULO / GORDURA / INERTE / CONSERTAR

| Módulo | Classificação | No caminho vivo? | Evidência | Ação proposta |
|---|---|---|---|---|
| `edp/memory.py` | **MÚSCULO** | SIM — núcleo do retrieve e storage | `websocket.py:716, 1210` | MANTER |
| `edp/api/routes/websocket.py` | **MÚSCULO** | SIM — handler de turno | `run.py:50-53` | MANTER |
| `edp/pipeline.py` | **MÚSCULO** | SIM — chamado por websocket | `websocket.py:629` | CONSERTAR (G1) |
| `edp/llm_adapter.py` | **MÚSCULO** | SIM — LLM + echo chamber | `api/main.py:81-83` | MANTER |
| `edp/echo_chamber.py` | **MÚSCULO** | SIM — câmara de verificação | `websocket.py:951` | MANTER |
| `edp/consolidation.py` | **MÚSCULO** | SIM — promoção episódic→semântico | `auto_consolidation.py:93` | MANTER |
| `edp/runtime/auto_consolidation.py` | **MÚSCULO** | SIM — background job | `api/main.py:213` | CALIBRAR (bloqueado por dados) |
| `edp/runtime/lineage.py` | **MÚSCULO** | SIM — observabilidade por turno | `websocket.py:1305` | MANTER |
| `edp/runtime/cognitive_decisions.py` | **INERTE** | Background job ativo, mas efeito nulo | Campo gravado nunca lido em retrieve: `memory.py:723-726`; destruído em `consolidation.py:140-149` | CONECTAR ou PODAR (decidir depois) |
| `edp/runtime/contradiction_flagger.py` | **INERTE** | Chamado em `memory.py:1647`, retorno descartado | `memory.py:1647` — `try/except: pass`, sem atribuição | CONECTAR (fechar loop) |
| `edp/runtime/health_index.py` (CHI) | **INERTE** | Background job ativo, output em `.jsonl` | Nenhum módulo lê `health_history.jsonl` para decisão | MANTER observabilidade |
| `edp/runtime/pareto_store.py` | **INERTE** | Escrita ativa, leitura zero para decisões | `memory.py:881` escreve; nenhum decisor lê | MANTER observabilidade |
| `edp/adaptive_controller.py` | **INERTE** | Chamado em `pipeline.py:448`, output ignorado | `pipeline.py:449` → só `_trace(debug=False)` | CONECTAR (baixo risco) |
| `edp/meta_reasoner.py` | **INERTE** | Chamado em `pipeline.py:460`, output ignorado | `reflection.` nunca lido após linha 472 | CONECTAR ou PODAR |
| `edp/orchestrator_v32.py` | **GORDURA** | NÃO — só `run.py` benchmarks | `run.py:219, 474`; zero em `edp/api/` | PODAR |
| `edp/types_v32.py` | **GORDURA** | NÃO — só via orchestrator_v32 | Dependência exclusiva do orchestrator | PODAR |
| `edp/decision_graph_v32.py` | **GORDURA** | NÃO — só via orchestrator_v32 | `orchestrator_v32.py:72, 176` | PODAR |
| `edp/biodiversity.py` | **GORDURA** | NÃO — só via orchestrator_v32 | `orchestrator_v32.py:79, 192` | PODAR |
| `edp/economy.py` | **GORDURA** | NÃO — só via orchestrator_v32 | `orchestrator_v32.py:74, 184` | PODAR |
| `edp/storm_guard.py` | **GORDURA** | NÃO — só via orchestrator_v32 | `orchestrator_v32.py:73, 180` | PODAR |
| `edp/meta_stability.py` | **GORDURA** | NÃO — só via orchestrator_v32 | `orchestrator_v32.py:75, 188` | PODAR |
| `edp/pressure_regulator.py` | **GORDURA** | NÃO — só via orchestrator_v32 | `orchestrator_v32.py:80, 196` | PODAR |
| `edp/snapshot_manager.py` | **GORDURA** | NÃO — só orchestrator_v32 + benchmark | `orchestrator_v32.py:71, 172`; `benchmark_edp.py:670, 755` | PODAR |
| `edp/embedding_cache.py` | **GORDURA** | NÃO — só via orchestrator_v32 | `orchestrator_v32.py:82, 201` | PODAR |
| `edp/pressure_monitor.py` | **GORDURA** | NÃO — só via orchestrator_v32 | `update_consolidation`: zero chamadores externos | PODAR |
| `edp/semantic_memory.py` | **GORDURA** | NÃO no serve path — caminho standalone deprecado | `auto_consolidation.py:7`: "SemanticMemory PARALELA... DEPRECADO" | PODAR (confirmar antes) |
| `edp/api_v2.py` | **GORDURA** | Fallback do serve se `api/main.py` falhar | `run.py:59-65` — segunda prioridade | DECIDIR-DEPOIS |
| `benchmark_edp.py` | **GORDURA** | NÃO — ferramenta de desenvolvimento | `run.py` não o importa | MANTER no repo (é util) |
| `edp/lab/` (todos) | **GORDURA** | NÃO — experimentos | Nenhum importado pelo serve path | MANTER no repo |
| `edp/affective_calibration.py` | **MÚSCULO** | SIM — `llm_adapter.py:2447`, `api/routes/memory.py:302` | Chamado por dois módulos ativos | MANTER |
| `edp/co_occurrence.py` | **MÚSCULO** | SIM — `llm_adapter.py:1842`, `api/routes/memory.py:637` | Chamado por dois módulos ativos | MANTER |
| `edp/belief_graph.py` | **MÚSCULO** | SIM — `scheduler.py:6`, `llm_adapter.py` implícito | Importado por módulos ativos | MANTER |

---

### 3.2 Tabela dos LOOPS ABERTOS

| # | Sinal | Retorno descartado? | O que fecha o loop | Prioridade |
|---|---|---|---|---|
| A1 | `cognitive_decisions` (key_assertion, concepts, domain) | SIM — gravado em entry mas nunca lido em retrieve (`memory.py:723-726`) | **Conectar:** adicionar `concepts_boost` na fórmula de ranking; ou usar `domain` como filtro pré-scoring | Alta — custo LLM já pago, dado rico |
| A2 | `scan_results()` do contradiction_flagger | SIM — `memory.py:1647` sem atribuição | **Conectar:** quando flag detectada, escalar `epistemic_status` da entry para `"stale"` ou `"contradicted"` automaticamente | Alta — detecção gratuita, ação zero |
| A3 | `adaptive_decision` (AdaptiveController) | SIM — `pipeline.py:449` só `_trace(debug=False)` | **Conectar:** ler `adaptive_decision.memory_mode` para ajustar `top_k` ou `min_score` do retrieve seguinte | Média — controller já decide, só falta ser lido |
| A4 | `reflection.reweights` (MetaReasoner) | SIM — `pipeline.py:460`, variável nunca lida | **Conectar:** aplicar reweights antes do estágio 8 (Keep/Summarize/Drop) | Média — requer entender semântica dos reweights |
| A5 | `quality_score` / `aggregate_score` | SIM — `websocket.py:1212` usa `score=0.65` fixo | **NÃO CONECTAR SEM REFATORAÇÃO** — mismatch semântico: mede qualidade do input do usuário, não da resposta LLM | Baixa — requer criar nova métrica de qualidade da resposta |
| A6 | `lineage` records | SIM — só observabilidade | Por design — não há gap aqui | N/A |
| A7 | `pareto_store` eventos | SIM — só observabilidade | Útil para calibração offline (quando os dados existirem) | Baixa — análise batch, não loop em tempo real |
| A8 | `CHI` score | SIM — `health_history.jsonl` | Poderia triggerar modo conservador no router, mas requer dados suficientes primeiro | Baixa — bloqueado por dados |
| A9 | `ranking_breakdown` | SIM — computado e descartado | Nenhuma ação necessária — é debug info | N/A |
| A10 | `scheduler.evaluate()` | SIM — `pipeline.py:608` `_trace(debug=False)` | Opera em episódios sintéticos (`acessos=1` fixo, `pipeline.py:604`) — **não conectar**, dados são ficticios | N/A |
| A11 | `learnable_blocks` → SemanticMemory standalone | SIM no serve path | Substituído por `auto_consolidation` — caminho correto já existe | N/A (depreciado) |

**Princípio:** conectar antes de sofisticar. A2 (scan_results → epistemic_status) é a conexão mais barata — zero custo adicional, lógica trivial. A1 (cognitive_decisions → ranking) é mais rica mas requer design do boost.

---

### 3.3 Tabela dos THRESHOLDS

| Threshold | Valor atual | Arquivo:Linha | Calibração documentada? | Tipo de loop | Risco de calibrar | Acionável agora? |
|---|---|---|---|---|---|---|
| `SESSION_BOOST_FACTOR` | `1.60` | `memory.py:76` | ✅ SIM — incidente 04/06/2026 | Fechado | Baixo | SIM |
| `OUT_OF_SESSION_PENALTY` | `0.85` | `memory.py:77` | ✅ SIM — mesmo incidente | Fechado | Baixo | SIM |
| `promote_threshold` | `3` | `auto_consolidation.py:47` | ❌ NÃO | Fechado | Baixo | **NÃO** — bloqueado por dados |
| `SIMILARITY_THRESHOLD` | `0.85` | `contradiction_flagger.py:65` | ❌ Prometida, nunca executada | Aberto (retorno descartado) | Médio | **NÃO** — fechar o loop antes |
| `anchor_boost` | `1.20` | `memory.py:699` | ❌ Por analogia | Fechado | Baixo | SIM (ajuste menor) |
| Dominância trigger | `12%` | `memory.py:664` | ❌ NÃO | Fechado | Baixo | **NÃO** — bloqueado por dados |
| Dominância penalidade | `×0.70` | `memory.py:691` | ❌ NÃO | Fechado | Baixo | **NÃO** — bloqueado por dados |
| `CURRENT_SESSION_TRUST_THRESHOLD` | `0.30` | `memory.py:97` | Parcial | Fechado | Médio | **NÃO** — bloqueado por dados |
| `DEDUP_THRESH` | `0.75` | `config.py:19` | ❌ NÃO | Fechado (compressão pipeline) | Médio | **NÃO** — bloqueado por dados |
| `CONSOLIDATION_SIM_THRESH` | `0.80` | `config.py:41` | ❌ NÃO | Fechado | Baixo | **NÃO** — bloqueado por dados |

**Regra:** thresholds em loops abertos não devem ser calibrados antes de o loop ser fechado. `SIMILARITY_THRESHOLD = 0.85` é o caso mais claro: é um chute, mas calibrá-lo sem fechar o `scan_results()` não muda nada observável.

---

### 3.4 Dependência de dados reais (resultado de 2.1)

Items **bloqueados** (precisam de dados de produção em `/content/edp_v3_memory` ou equivalente):
- Calibração de `promote_threshold` — precisa de distribuição real de `acessos`
- Calibração de `SIMILARITY_THRESHOLD` — precisa de flags revisadas por operador
- Calibração de dominância (12%, 0.70) — precisa de distribuição de concentração de acessos
- CHI como gate de decisão — precisa de histórico de saúde vs. qualidade percebida
- Análise de continuidade de uso (Fase B do plano de calibração)

Items **acionáveis agora** (análise estática suficiente):
- Poda do v3.2 (4.362 linhas) — confirmado por git history e grep
- Patch G1 (re-embedding) — bug confirmado em `pipeline.py:390`
- Conexão de `scan_results()` retorno a `epistemic_status` — custo zero, lógica trivial
- Conexão de `adaptive_decision` — variável já existe no escopo

---

## FASE 4 — Proposta de curadoria

> ⚠️ **NÃO EXECUTADO** — cada item abaixo tem o comando git exato. Nada foi aplicado. Aguarda aprovação item a item.

---

### [PODAR] — Código morto confirmado

**P1 — Bloco v3.2 completo (4.362 linhas, 9 arquivos + 2 dependências)**

Confirmação de abandono: todos os arquivos chegaram no mesmo commit (`14179eb`, 2026-05-20), zero evolução depois, `register_v32_store` nunca teve chamador em nenhum commit da história, v3.2 é 100% governança (ortogonal ao gap de qualidade).

```bash
git rm edp/orchestrator_v32.py \
       edp/types_v32.py \
       edp/decision_graph_v32.py \
       edp/biodiversity.py \
       edp/economy.py \
       edp/storm_guard.py \
       edp/meta_stability.py \
       edp/pressure_regulator.py \
       edp/snapshot_manager.py \
       edp/embedding_cache.py \
       edp/pressure_monitor.py
git commit -m "podar: remove bloco v3.2 (4.362 linhas) - abandonado em 20/05, zero chamadores em serve path"
```

**Teste que provaria que nada quebrou:**
```bash
python -c "from edp.api.main import app; print('api ok')"
python -c "from edp.pipeline import run_pipeline; print('pipeline ok')"
python -c "from edp.memory import MemoryStore; print('memory ok')"
# Confirmar que run.py ainda sobe (o modo test_v32 vai falhar com ImportError — esperado)
```

**Arquivos que referenciam o v3.2 e precisam de limpeza residual:**
- `run.py:200-280` — funções `test_v32()` e `_query_v32()` ficam órfãs; remover ou deixar com `try/except ImportError`
- `edp/pipeline.py:67-124` — classe `MemoryBridgeV32` inteira pode ser removida (zero chamadores com `register_v32_store`)

```bash
# Após confirmar P1, remover MemoryBridgeV32 de pipeline.py (pipeline.py:67-124)
# e as funções test_v32/_query_v32 de run.py (run.py:200-280 e run.py:456-492)
```

---

**P2 — `edp/semantic_memory.py` (295 linhas) — caminho standalone deprecado**

A própria codebase documenta: "SemanticMemory PARALELA (semantic_memory.py standalone → `_concepts.json`, 'coexistem sem sincronização') ... caminho standalone (_concepts.json) DEPRECADO em favor do job auto_consolidation" (`auto_consolidation.py:5-11` e `pipeline.py:651-658`).

**⚠️ ATENÇÃO antes de executar:** confirmar que nenhum endpoint do serve path lê `_concepts.json` diretamente. Verificar que `memory.py:SemanticMemory` (classe interna ao `memory.py`) é diferente de `semantic_memory.py` (arquivo standalone).

```bash
# Verificar antes:
grep -rn "from.*semantic_memory import\|_concepts\.json" edp/ --include="*.py"
# Se zero hits no serve path:
git rm edp/semantic_memory.py
git commit -m "podar: remove semantic_memory.py standalone (deprecado por auto_consolidation)"
```

---

### [CONSERTAR] — Bugs e loops abertos de baixo custo

**C1 — G1: Eliminar re-embedding após deduplicação (`pipeline.py:388-391`)**

**Bug atual:**
```python
# pipeline.py:388-391 (ATUAL — BUG)
chunks_deduped = deduplicate(chunks, DEDUP_THRESH, chunk_embs)
if len(chunks_deduped) < len(chunks):
    chunk_embs = embed(chunks_deduped)   # ← re-embeds tudo do zero
    chunks = chunks_deduped
```

**`deduplicate()` já recebe `chunk_embs` e retorna textos + mantém os índices internamente (`embeddings.py:212-232`).** O fix é expor esses índices e filtrar os embeddings existentes.

**Patch em 2 arquivos:**

```python
# embeddings.py:212 — adicionar return_indices=False (backward-compatible)
def deduplicate(
    items: list[str],
    threshold: float,
    embeddings: np.ndarray | None = None,
    return_indices: bool = False,          # NOVO
) -> list[str] | tuple[list[str], list[int]]:
    if not items:
        return ([], []) if return_indices else []
    emb = embeddings if embeddings is not None else embed(items)
    sim_matrix = cosine_similarity(emb, emb)
    keep: list[int] = []
    for i in range(len(items)):
        if not any(float(sim_matrix[i, j]) > threshold for j in keep):
            keep.append(i)
    result = [items[i] for i in keep]
    return (result, keep) if return_indices else result
```

```python
# pipeline.py:388-391 — usar índices em vez de re-embedar
chunks_deduped, kept_idx = deduplicate(chunks, DEDUP_THRESH, chunk_embs, return_indices=True)
if len(chunks_deduped) < len(chunks):
    chunk_embs = chunk_embs[kept_idx]   # ← filtrar, não re-embedar
    chunks = chunks_deduped
```

```bash
git commit -m "fix G1: filtrar embeddings existentes após dedup em vez de re-embedar (pipeline.py:390, embeddings.py:212)"
```

**Teste:** comportamento idêntico — `chunk_embs[kept_idx]` contém exatamente os mesmos vetores que `embed(chunks_deduped)` retornaria (os embeddings originais dos chunks não-deduplicados).

---

**C2 — Fechar loop de `scan_results()` → `epistemic_status` (`memory.py:1647`)**

**Estado atual:** contradiction_flagger detecta pares contraditórios mas o retorno é silenciado.

**Patch mínimo:** capturar o retorno e escalar `epistemic_status` para `"stale"` nas entries sinalizadas como contraditadas.

```python
# memory.py:1644-1649 — ATUAL
if len(final_top) >= 2:
    try:
        from .runtime.contradiction_flagger import get_flagger
        get_flagger().scan_results(final_top)
    except Exception:
        pass

# PROPOSTO
if len(final_top) >= 2:
    try:
        from .runtime.contradiction_flagger import get_flagger
        flags = get_flagger().scan_results(final_top)
        if flags:
            flagged_ids = {fid for f in flags for fid in (f.entry_a_id, f.entry_b_id)}
            for entry in final_top:
                if entry.get("id") in flagged_ids:
                    if entry.get("epistemic_status") not in ("contradicted", "quarantined"):
                        entry["epistemic_status"] = "stale"
    except Exception:
        pass
```

```bash
git commit -m "conectar: scan_results() → epistemic_status='stale' em entries sinalizadas (memory.py:1647)"
```

**⚠️ Verificar antes:** confirmar assinatura de retorno de `scan_results()` e estrutura de `ConflictFlag` em `contradiction_flagger.py`. O patch acima assume que retorna lista de flags com `entry_a_id`/`entry_b_id`.

---

**C3 — Conectar `adaptive_decision` ao retrieve (`pipeline.py:448-449`)**

`ADAPTIVE_CONTROLLER.decide()` produz `memory_mode` e `attention_mode`. O resultado só vai para `_trace(debug=False)`. Conectar ao `top_k` do retrieve interno do pipeline é uma linha.

```python
# pipeline.py:448 em diante — após adaptive_decision calculado
adaptive_decision = ADAPTIVE_CONTROLLER.decide(adaptive_state)
_trace("adaptive_controller", adaptive_decision.to_dict())
# ADICIONAR: ajustar top_k interno se controller sinalizar CONSERVATIVE
# (verificar primeiro: quais valores tem adaptive_decision.memory_mode)
```

```bash
# Verificar antes:
grep -n "memory_mode\|CONSERVATIVE\|ARCHIVE_ONLY\|class AdaptiveDecision\|def decide" edp/adaptive_controller.py | head -20
git commit -m "conectar: adaptive_decision.memory_mode → top_k do retrieve interno (pipeline.py:448)"
```

---

### [CALIBRAR / DECIDIR-DEPOIS]

**D1 — `promote_threshold = 3` (auto_consolidation.py:47)**

Loop fechado confirmado, baixo risco de calibrar, mas **bloqueado por dados**. Procedimento quando os dados estiverem disponíveis:
1. Em `/content/edp_v3_memory/sessions/`: ler `episodic.json` de todas as sessões
2. Plotar distribuição de `acessos` por entry
3. Identificar bimodal: ruído (acessos=1-2) vs. sinal (acessos≥N)
4. Ajustar `EDP_CONSOLIDATE_THRESHOLD` via variável de ambiente antes de hardcodar

**D2 — Decisão sobre `cognitive_decisions` → ranking**

Dois caminhos mutuamente exclusivos:
- **CONECTAR:** adicionar `concepts_boost` multiplicativo (×1.05–1.15) quando query contém conceitos da entry. Custo: O(n_concepts) por entry. Cobertura atual: ≤30% (só episódicas, layer=episodic, source_type=llm_response, 60s-24h).
- **PODAR:** se cobertura nunca superar 50% (por design dos filtros), o campo não justifica o custo de $0.001/extração × volume. Decidir após medir cobertura real com dados.

**D3 — `SIMILARITY_THRESHOLD = 0.85` do contradiction_flagger**

**Dependência:** fechar C2 primeiro. Só faz sentido calibrar o threshold de detecção quando a detecção tiver consequência. Com o retorno descartado, ajustar 0.85 para qualquer valor produz o mesmo efeito observável: zero.

**D4 — Ligar a ponte v3.2 (register_v32_store)**

**Recomendação: NÃO fazer.** O v3.2 é governança, não qualidade. O gap real está em `quality_score` → `memory.add`. Conectar o v3.2 resolveria um problema diferente do que existe. Se o v3.2 for relevante no futuro, ele deve ser reescrito com integração nativa ao serve path, não através de uma ponte que nunca foi testada.

**D5 — Reescrever `scan_results()` como scorer graduado (não-binário)**

**Dependência:** C2 primeiro. A reescrita do flagger (de threshold binário para score graduado, inspirado em DIVERGENCE_WEIGHTS do GLOSSOPETRAE) é uma melhoria de qualidade do sinal — mas o sinal precisa ter consequência antes de ser sofisticado.

**D6 — `api_v2.py` (1.110 linhas) — fallback ou morto?**

`run.py:59-65` usa `api_v2.py` como segunda prioridade se `api/main.py` falhar. É um fallback de último recurso, não código morto. **Decisão:** manter até confirmar que `api/main.py` está estável em produção; então remover.

---

## FASE 5 — Execução (pendente de aprovação e ambiente)

### 5a — Poda e patches estáticos (sem .venv)

**Status: PENDENTE — aguardando aprovação item a item.**

Ordem de execução proposta (cada um é commit atômico reversível):
1. P1: `git rm` dos 11 arquivos v3.2 + commit
2. Validação: `python -c "from edp.pipeline import run_pipeline"` (sem .venv: confirmar que import chain não quebra estaticamente)
3. C1: Patch G1 em `embeddings.py` + `pipeline.py` + commit
4. C2: Patch scan_results em `memory.py` + commit (após confirmar assinatura de retorno)

**Em caso de falha:** `git revert <commit>` ou `git checkout HEAD~1 -- <arquivo>`. A branch `auditoria-curadoria` preserva tudo. `main` está intacta.

### 5b — Verificação de runtime vivo (precisa de .venv)

**Status: PENDENTE — rodar no ambiente com as dependências instaladas.**

```bash
# No ambiente com .venv ativo:
uvicorn edp.api.main:app --host 127.0.0.1 --port 8000 &
# Queries de smoke test:
curl -s http://127.0.0.1:8000/health
# WebSocket: conectar e enviar 1 mensagem, verificar resposta e lineage
```

### 5c — Como reverter tudo

```bash
# Reverter para antes de qualquer mudança desta branch:
git checkout main
# A branch auditoria-curadoria fica preservada para revisão:
git branch  # auditoria-curadoria ainda existe
# Para inspecionar o que foi feito:
git log --oneline auditoria-curadoria
# Para desfazer a branch inteira:
git branch -D auditoria-curadoria
```

**main está intacta. Nenhum merge foi feito.**

---

## RESUMO EXECUTIVO

### O que o EDP tem de sólido

O caminho vivo (`websocket → pipeline → memory → auto_consolidation → lineage`) está bem construído. O loop de aprendizado real baseado em `acessos` funciona: entradas acessadas ganham boost, acumulam `acessos ≥ 3`, são promovidas a semântica, e entram em retrieves futuros. O session_boost (`×1.60` documentado por incidente real) é o threshold mais bem fundamentado do sistema.

### O maior desperdício

**4.362 linhas de código (v3.2)** que nunca executaram no serve path, resolvem um problema diferente do gap existente, e foram introduzidas e imediatamente abandonadas. A poda é segura, reversível, e entrega a maior redução de complexidade com o menor risco.

### O maior ganho imediato

**C2: fechar o loop de `scan_results()`** é a conexão mais barata do sistema. O contradiction_flagger já roda (O(n²) pairwise) a cada retrieve. O resultado já existe. Conectar retorno → `epistemic_status` é ~8 linhas de código e fecha um loop que está aberto desde sempre.

### O maior ganho de médio prazo

**G1 (C1)**: eliminar o re-embedding após deduplicação elimina chamadas redundantes ao modelo de embedding no hot path conversacional. Custo: ~20 linhas (embeddings.py + pipeline.py). Benefício: proporcional à taxa de deduplicação real.

### O que não deve ser feito agora

- Conectar `quality_score` → `memory.add`: mismatch semântico fundamental. `quality_score` mede o input do usuário, não a resposta do LLM. Conectá-los introduziria um sinal errado como sinal de qualidade.
- Calibrar `SIMILARITY_THRESHOLD = 0.85`: é um chute, mas calibrá-lo sem fechar C2 não muda nada observável.
- Ligar a ponte v3.2: resolveria governança de recursos num sistema que tem um gap de qualidade de memória. Problemas diferentes.

---

*Auditoria executada em branch `auditoria-curadoria`. Commit baseline: `feb0db9`. main intacta.*
*Próximo passo: aprovação item a item da Fase 4 para execução na Fase 5.*
