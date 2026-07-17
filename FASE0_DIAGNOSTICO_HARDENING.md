# Fase 0 — Diagnóstico obrigatório (Hardening EDP v5)

**Baseline confirmado:** `git rev-parse HEAD` = `e2b0b2d` (main, pós-merge PRs #7/#8/#9).
Bate exatamente com o adendo. Grafo estava em `07dc13ea` (2 commits atrás) —
`graphify update .` rodado antes de qualquer número abaixo (3225→3224 nodes,
5628→5629 edges, custo zero de API, 4 arquivos re-extraídos: `consolidation.py`,
`config.py`, `ESTADO_EXP012.md` + 1). `built_at_commit` no `graph.json`
confirmado = `e2b0b2d`.

**Tag de rollback criada (local, sem push):** `v3.16-quarantine-stable` sobre `e2b0b2d`.

Regra 6 aplicada em todo este documento: nenhuma afirmação de "morto" ou
"duplicado" sem grep exaustivo + leitura de fonte. Onde a fonte contradisse
o grafo ou a documentação, a fonte venceu — registrado explicitamente.

---

## Números re-extraídos dos god nodes (pós-`graphify update .`)

| Node | Total edges | EXTRACTED | INFERRED |
|---|---|---|---|
| `MemoryStore` | 97 | 75 | 22 |
| `now()` | 60 | 60 | 0 |
| `EDPRuntime` | 48 | 40 | 8 |
| `run_pipeline()` | 47 | 46 | 1 |
| `is_valid()` | 45 | 45 | 0 |
| `get_memory()` | 44 | 44 | 0 |
| `BenchmarkRunner` | 28 | 20 | 8 |
| `ws_chat()` | 28 | 27 | 1 |
| `get_prontuario()` | 28 | 28 | 0 |
| `AnthropicProvider` | 28 | 15 | 13 |

Ranking praticamente idêntico ao original (pequena deriva: `MemoryStore`
94→97, `now()` 57→60, `EDPRuntime` estável em 48). **A prioridade de
caracterização do Eixo 2 §5.2 continua válida como está.**

---

## Item 1 — `graphify query MemoryStore`: as 22 (não 19) edges INFERRED

Extraí as 22 edges INFERRED diretamente de `graph.json` (não só a listagem
textual do `graphify query`) e verifiquei cada uma contra a fonte.

**Achado principal — padrão sistemático de ruído do extrator:** toda edge
INFERRED com `confidence_score=0.5` e `relation=uses` ancorada numa linha de
`import` isolada está **superatribuída**: o extrator marca "uses
MemoryStore" para **todas** as classes definidas no mesmo arquivo, não só a
que de fato usa. Exemplos confirmados por leitura de fonte:

- 6 edges `edp_api_*Request/*Response -[uses]-> MemoryStore` @ `api.py:L18`
  — L18 é `from .memory import MemoryStore` no topo do arquivo; as 6
  classes citadas (`ProcessRequest`, `ProcessResponse`,
  `MemoryAddRequest`, etc.) são schemas Pydantic que **não referenciam
  MemoryStore em lugar nenhum** — falso positivo por co-ocorrência de
  arquivo. (E `api.py` é código morto — ver Item 4.)
- 5 edges análogas em `api_v2.py:L86` — o fato real (`_get_memory()`
  instancia `MemoryStore`, `api_v2.py:86-88`) existe, mas a edge foi
  atribuída a 5 classes Pydantic não relacionadas, não à função
  `_get_memory()` que de fato faz a chamada.
- 6 edges análogas em `llm_adapter.py:L1818` — mesmo padrão: o fato real
  (`EDPRuntime` instancia `MemoryStore` como fallback quando o registry
  falha, `llm_adapter.py:1817-1818`) é verdadeiro, mas a mesma linha gerou
  edges também para `LLMProvider`, `LLMConfig`, `ChatResponse`,
  `LLMMetrics`, `LLMClient` — nenhuma delas usa `MemoryStore`.

**Edge MISATRIBUÍDA (não é ruído de arquivo, é erro de escopo de classe):**
`MemoryStore -[uses]-> StorePressureMonitor` @ `memory.py:L475`. Fonte real:
linha 475-477 fica dentro de `EpisodicMemory.__init__`, não de
`MemoryStore.__init__` (`MemoryStore` começa em `L1376`). A relação real é
`EpisodicMemory -[uses]-> StorePressureMonitor`.

**Edges CONFIRMADAS corretas** (fonte lida, atribuição bate):
`MemoryStore -[uses]-> HybridRetriever` @ `memory.py:L1717/1718` (dentro de
`MemoryStore._hybrid_index()`, confirmado); `run_exp009 -[indirect_call]->
MemoryStore` e `measure_ss_dominance.main -[indirect_call]-> MemoryStore`
(confidence 0.8, relation diferente de "uses" — script de lab/standalone
instanciando diretamente, plausível e não verificado linha-a-linha por ser
código de laboratório, não núcleo).

**Conclusão para o Eixo 1:** dos 97 edges de `MemoryStore`, os **75
EXTRACTED são a superfície real** a ser reduzida a ≤35. Das 22 INFERRED,
~17 são ruído de co-ocorrência de arquivo (descartáveis), 1 é
misatribuída (corrigível apontando para `EpisodicMemory`), e ~4 são reais.
**Recomendação: ao medir "edges de MemoryStore" como critério de aceite,
filtrar por `confidence=EXTRACTED` — INFERRED com `score=0.5` e `relation=uses`
ancorada em linha de import isolada não deve contar como responsabilidade
real do god node.**

---

## Item 2 — `graphify query EDPRuntime`: as 8 edges INFERRED

Número bateu exato com o adendo (8). Mesmo padrão do Item 1:

- 5 edges `edp_api_v2_*Request/*Response -[uses]-> EDPRuntime` @
  `api_v2.py:L69` — mesmo ruído de co-ocorrência (`_get_runtime()` é quem
  de fato instancia `EDPRuntime`, `api_v2.py:69-71`, não as 5 classes
  Pydantic citadas).
- **3 edges CONFIRMADAS corretas**, todas corretamente fonte=`EDPRuntime`:
  `EDPRuntime -[uses]-> CoOccurrenceTracker` @ `llm_adapter.py:L1842`
  (dentro do método de init de subsistemas), `EDPRuntime -[uses]->
  EchoChamber` @ `llm_adapter.py:L1921` (dentro do lazy-getter
  `get_echo_chamber()`), `EDPRuntime -[uses]-> MemoryStore` @ `L1818` (já
  confirmada no Item 1).

**Conclusão:** dos 8 INFERRED de `EDPRuntime`, 5 são ruído descartável e 3
são reais e corretamente atribuídas — proporção de ruído menor que em
`MemoryStore`, mas o mesmo padrão sistemático se repete. Reforça a
recomendação do Item 1: **filtrar INFERRED `score=0.5`/`relation=uses`
ancorada em linha de import de topo de arquivo antes de julgar severidade
de god node.**

---

## Item 3 — Tabela de responsabilidade real dos arquivos de memória

O escopo pedido (`memory.py`, `memory_classifier.py`, `memory_graph.py`,
`semantic_memory.py`) **cresceu durante a investigação**: seguindo os
imports reais a partir desses 4 arquivos, apareceram mais 4 arquivos
genuinamente entrelaçados (`scheduler.py`, `belief_graph.py`,
`cognitive_scheduler.py`, e o par `pressure.py`/`pressure_governor.py` —
este último por conexão com Item 8). Reportado conforme achado, não
escondido para caber no escopo original.

| Arquivo | O que faz DE FATO hoje | Vivo no caminho `run.py→websocket→pipeline→memory→LLM`? |
|---|---|---|
| `edp/memory.py` | **A memória real.** `MemoryStore`/`EpisodicMemory`/`SemanticMemory`/`WorkingMemory` — os "Dois Exocórtices" (scopes cognitive/sprint), persistência em `episodic.json`/`semantic.json`, retrieval (cosine + híbrido), toda a máquina de quarentena exp012-016 (`answer_class`, `TOXIC_ANSWER_CLASSES`). | **SIM** — é a memória usada por `edp/api/routes/websocket.py` e pelo registry (`edp/runtime/registry.py`). |
| `edp/memory_classifier.py` | Heurística leve (regex, sem LLM, <1ms) de `source_type` (`user_input`/`llm_response`/`meta_conversation`/...) + pesos por tipo de fonte. | **SIM** — importado por `memory.py:339` (`classify_memory`) e `memory.py:668` (`get_source_weight`, usado no scoring de `EpisodicMemory.retrieve()`). Módulo limpo, responsabilidade única, bem separado — não é um problema de modularidade. |
| `edp/memory_graph.py` | Grafo leve dict-based (`_graph` global em módulo) de relações entre ids de memória — `link_memories`/`get_related`/`get_memory_hubs`/`build_from_entries`. | **NÃO** no caminho vivo — só importado por `edp/api.py` (morto, Item 4) e citado (não importado de fato, ver abaixo) em comentários de `edp/belief_graph.py`/`edp/scheduler.py`. |
| `edp/belief_graph.py` | Segunda implementação de grafo de crenças — classe `BeliefGraph`, com wrapper standalone `build_from_entries()` "para compatibilidade com scheduler". | **NÃO** no caminho vivo — só importado por `benchmark_edp.py` e `edp/scheduler.py` (este último morto, ver abaixo). |
| `edp/scheduler.py` | Consolidação/manutenção periódica **v3.1**, precursora do `edp/runtime/background_loop.py` + `edp/runtime/auto_consolidation.py` atuais. Docstring do próprio arquivo documenta 4 patches (`P-C3-9` a `P-C3-12`) sobre imports quebrados. | **CÓDIGO MORTO confirmado por grep exaustivo** — zero importadores em todo o repo fora do próprio arquivo. Superseded pelo par `background_loop.py`/`auto_consolidation.py` (este sim vivo, testado na Fase 5 desta sessão). |
| `edp/cognitive_scheduler.py` | Módulo **diferente** de `scheduler.py` (nome parecido, código não relacionado) — `CognitiveScheduler`, instanciado dentro de `run_pipeline()`. | **SIM** — `pipeline.py:281`, `scheduler = CognitiveScheduler("default")`, dentro do `run_pipeline()` que É chamado pelo caminho vivo (`llm.py`/`websocket.py`). |
| `edp/semantic_memory.py` | **Split-brain confirmado e ATIVO.** Segunda classe `SemanticMemory`, completamente diferente da de `memory.py`: armazena `Concept` em `_concepts.json` (não `_semantic.json`). Docstring do próprio arquivo já nomeia o problema ("split-brain semântico... coexistem sem sincronização"). | **PARCIALMENTE SIM — achado crítico não previsto no prompt original.** Ver abaixo. |

### Achado crítico: `run_pipeline()` toca o split-brain em TODO turno vivo

`edp/pipeline.py:279`: `semantic_memory = memory_bridge or
get_pipeline_memory(session_id)`. Os dois chamadores reais de
`run_pipeline()` no caminho vivo (`edp/api/routes/llm.py:75` e
`edp/api/routes/websocket.py:629`) **chamam sem passar `memory_bridge=`**,
então o default cai em `get_pipeline_memory()` → instancia
`semantic_memory.py.SemanticMemory` (o `_concepts.json`, NÃO o
`memory.py.SemanticMemory` real).

Rastreado o uso de `semantic_memory` dentro de `run_pipeline()`:
- **Leitura:** `mem_results = semantic_memory.retrieve(question, top_k=3)`
  (`pipeline.py:327`) roda em **todo turno**, contra o store errado.
  Alimenta `retrieval_quality` (métrica) e `meta.reflect(...)`
  (`MetaReasoner`) — não entra diretamente no texto de contexto retornado
  (`blocos_final`), que vem só de chunking/fusão do input.
- **Escrita:** `semantic_memory.consolidate(...)`/`consolidate_from_episodes(...)`
  (`pipeline.py:606-611`), **já envolvida em try/except com warning
  explícito** ("Caminho standalone (`_concepts.json`) DEPRECADO em favor do
  job `auto_consolidation`" — comentário do próprio time, Sprint #43,
  11/06/2026).

**Consequência prática confirmada:** a máquina de quarentena (exp012-016,
`TOXIC_ANSWER_CLASSES`, guarda de consolidação da Fase 5) **não tem
nenhuma relação com este caminho** — ela protege exclusivamente
`memory.py.SemanticMemory`. Isso não é um buraco na quarentena (a escrita
real de memória do usuário passa por `memory.add()` +
`stamp_and_classify()` no handler do websocket, caminho separado deste). É,
sim, uma dívida de modularidade/observabilidade ativa: toda conversa paga o
custo de uma consulta (`retrieve`) a um store paralelo e desatualizado, cujo
resultado influencia telemetria e o `MetaReasoner` sem que ninguém tenha
decidido isso conscientemente hoje. O próprio time já sinalizou a intenção
de remoção ("warning mantém qualquer falha visível até a remoção total") —
**recomendo tratar isso como item explícito do Eixo 1, não deixar implícito
dentro do split de `MemoryStore`.**

### Achado secundário: comentário "módulo ausente" está desatualizado

`edp/belief_graph.py:351` documenta (patch `P-C3-8`, histórico) que
`scheduler._job_graph_link()` importava `from .memory_graph import
build_from_entries` e que esse módulo "não existe". **Isso não é mais
verdade** — `edp/memory_graph.py` existe hoje (76 linhas) e tem
`build_from_entries()` própria (linha 41), implementação diferente da de
`belief_graph.py`. E o arquivo `scheduler_patched.py`, citado em 2
comentários como correção, **não existe no repo** — nunca foi criado, ou
`scheduler.py` (que já se autodenomina "(PATCHED)" no cabeçalho) absorveu
as correções sem que os comentários cruzados fossem limpos. Como
`scheduler.py` é código morto (confirmado acima), isso é debt cosmético de
documentação, não bug ativo — mas ilustra por que Regra 6 (evidência antes
de crer em comentário) importa.

---

## Item 4 — Superfície de API viva

**Confirmado por leitura de fonte + teste de import real, não só grep:**
`edp/api/` (pacote modular) é a **única superfície viva**.

`run.py:serve()` tenta, em ordem: `edp.api.main:app` → `edp.api_v2:app` →
`edp.api:app`. Testei o primeiro (`from edp.api.main import app`) nesta VM:
**importa com sucesso, sem exceção** — logo os fallbacks nunca são
alcançados em instalação saudável.

Grep exaustivo confirma zero acoplamento: nenhum módulo do repo (fora do
próprio `run.py`, que só os referencia dentro do fallback nunca atingido)
importa de `edp/api.py` (410 linhas) ou `edp/api_v2.py` (1110 linhas).
`edp/api/main.py` só menciona `api_v2.py` num comentário de docstring
("Esta é a aplicação NOVA que substitui gradualmente o monolito
api_v2.py"). Nenhum arquivo dentro de `edp/api/` importa de volta de
`api.py`/`api_v2.py`.

**Conclusão: 1520 linhas de código morto candidatas a remoção/depreciação
formal** (`api.py` + `api_v2.py`), sem ambiguidade — evidência forte,
critério da Regra 6 satisfeito.

---

## Item 5 — `bayes_calibrator` vs `gauss_calibrator`

**Não é duplicação — são dois calibradores estatísticos genuinamente
diferentes, compostos intencionalmente**, confirmado por leitura de fonte:

- `gauss_calibrator.py`: distribuição (média/desvio/percentis) e detecção
  de outlier sobre métricas numéricas de eventos Pareto — "Aplicações
  futuras: Bayes usar média/σ como prior" (o próprio docstring já prevê a
  composição com Bayes).
- `bayes_calibrator.py`: probabilidade condicional `P(B|A)` via contagem
  de `correlation_id` compartilhado entre eventos do mesmo turno —
  escopo explicitamente limitado (`Escopo NÃO incluído: decisões
  automáticas, priors bayesianos, association mining`).

**Ambos inicializados juntos** em `edp/runtime/health_index.py:129-133`,
alimentando termos **diferentes** da fórmula do Cognitive Health Index:
`retrieval_precision × 0.30` (Gauss) + `memory_utility × 0.30` (Bayes).

**Conclusão: "contrato de uso + teste", não convergência** — confirma a
hipótese do adendo. O que falta não é decidir qual é canônico (os dois
são), é **testar a composição** (nenhum teste dedicado hoje cobre
`health_index.py` usando os dois calibradores juntos).

---

## Item 6 — Retrieval: Strategy pattern ou acúmulo histórico?

**Acúmulo histórico sem contrato compartilhado — confirmado, não é
Strategy pattern.** As 3 classes (`RetrievalEngine`, `ANNRetrievalEngine`,
`HybridRetriever`) não compartilham base/interface — nenhuma herda de nada
em comum, nenhum protocolo formal.

Vivacidade real por grep exaustivo (nome da classe, não só nome de
arquivo):
- `HybridRetriever` (`retrieval_hybrid.py`) — **VIVA**: `edp/memory.py`
  (`MemoryStore._hybrid_index()`, é o caminho real usado por
  `EDP_HYBRID_RETRIEVAL=1`, default desde a Fase 1) + `edp/lab/exp010.py`.
- `RetrievalEngine` (`retrieval.py`) — só `benchmark_edp.py` e o modo CLI
  `run.py bench_retrieval`. **Não é o caminho vivo da API.**
- `ANNRetrievalEngine` (`retrieval_ann.py`) — **código morto confirmado**:
  zero referências em todo o repo fora do próprio arquivo (nem a classe,
  nem o módulo, buscados separadamente).

**Conclusão: convergir para `HybridRetriever` como única implementação
viva; `RetrievalEngine` fica só como dependência do benchmark legado
(decisão explícita: manter para benchmark ou também aposentar);
`ANNRetrievalEngine` é candidato direto a remoção (evidência da Regra 6
satisfeita — grep exaustivo, zero uso).**

---

## Item 7 — Autociclo `edp/api/routes/__init__.py -> edp/api/routes/__init__.py`

**Confirmado: NÃO é um ciclo de import real.** `graph.json` já rotula como
"1-file cycle" (não multi-arquivo). Testei diretamente: `import
edp.api.routes` funciona sem erro, sem `RecursionError`/`ImportError`.
Grep confirma que nenhum submódulo de `edp/api/routes/` importa de volta
`edp.api.routes` (o padrão que causaria um ciclo real).

O `__init__.py` só faz `from . import (health, memory, metrics, llm,
websocket, dashboard_state, providers, flags)` — reexport padrão de
pacote. **Hipótese confirmada: artefato de extração do grafo** — o
resolvedor de imports do graphify provavelmente atribui `from . import X`
(import relativo ao PRÓPRIO pacote) como uma aresta do pacote para si
mesmo, em vez de resolver para os submódulos individuais.

**Não há nada para corrigir no código** (Regra 6: sem evidência de bug
real, não mexer). Registrado como falso positivo do grafo, não como
achado de dívida — recomendo não gastar orçamento de Fase 1 nisso.

---

## Item 8 — Dívida #41 (RAM) e WAL/`WALEpisodicMemory`

**Dívida #41 CONFIRMADA aberta, com uma nuance que o próprio texto da
dívida não deixa óbvia:** existem **dois sistemas de "pressure" com nomes
quase idênticos**, cobrindo coisas diferentes — outro par candidato à
mesma confusão de nomenclatura já sinalizada para `cognitive_decisions.py`
(rotas vs runtime):

- `edp/pressure.py` (`StorePressureMonitor`) — mede **taxa de ocupação do
  store** (`len(episodic.entries)/max_size`), thresholds `0.65`/`0.82`
  **hardcoded**, sem env var. Instanciado dentro de
  `EpisodicMemory.__init__` (`memory.py:475`).
- `edp/runtime/pressure_governor.py` (`MemoryPressureGovernor`) — mede
  **RAM real do host** via `psutil`, é quem lê `EDP_PRESSURE_WARNING_GB`/
  `EDP_PRESSURE_CRITICAL_GB` (a Dívida #41 real). Defaults no código:
  `CRITICAL_GB=1.2`, `WARNING_GB=2.0` — **diferentes** dos valores que a
  própria Dívida #41 documenta como "workaround em uso" (`1.0`/`1.5`),
  confirmando que o "caminho de correção (futuro)" descrito na dívida
  ("encodar os limites corretos como defaults") **ainda não aconteceu** —
  os defaults no código não são os valores empiricamente validados.
  Este é o sistema que `edp/runtime/background_loop.py:284-296` consulta
  para decidir `pressure=CRITICAL` → "tick pulado completamente".

**Custo medido, não hipotético** (achado desta própria sessão, já
registrado em `ESTADO_EXP012.md` antes deste diagnóstico): durante a
investigação do exp016 (Fase Etapa 0, 15/07), confirmei que
`pressure=CRITICAL` constante manteve o `background_loop` pulando todos os
ticks do `cognitive_decisions_extractor` — causa direta de
`key_assertion` nunca ter sido extraído para as entries-alvo do exp016.
**Critério de aceite proposto (adendo E): ticks executam sob carga desktop
normal, com teste dos thresholds** — recomendo que esse teste cubra os
DOIS sistemas de pressure separadamente (não confundir um pelo outro).

**WAL/`WALEpisodicMemory`: confirmado como PROPOSTA, não implementação.**
Busca exaustiva (`grep -rl "WALEpisodicMemory"` em todo `.py` do repo):
zero resultados. Existe **só** em `docs/EDP_ARCHITECTURE_v4.md:114` como
classe de exemplo num documento de arquitetura. Nenhum código-fonte
implementa WAL para `EpisodicMemory` hoje. Decisão explícita necessária do
pesquisador (conforme já antecipado): entra neste ciclo (risco de dado
real) ou fica registrada como risco conhecido não resolvido — **este
diagnóstico não decide por conta própria**, só confirma que hoje é
zero-implementação.

---

## Itens fora do checklist original, descobertos durante a investigação

Reportados por completude (Regra 6 exige mostrar o que foi encontrado, não
só o que foi perguntado):

1. **Par de nomes confuso `pressure.py` / `pressure_governor.py`** (Item
   8) — mesma classe de problema do par `cognitive_decisions.py`
   (`api/routes/` vs `runtime/`) já citado no prompt original. Candidato a
   mesmo tratamento (renomear por camada/conceito).
2. **`run_pipeline()` consulta um SemanticMemory paralelo e desatualizado
   em todo turno vivo** (Item 3) — achado que muda o desenho recomendado
   do split de memória: não é só separar responsabilidades de
   `memory.py`, é decidir explicitamente o destino de
   `semantic_memory.py`/`get_pipeline_memory()`/`MemoryBridgeV32` (remover,
   conforme já sinalizado pelo próprio time em comentário, ou formalizar).
3. **`edp/scheduler.py` e `edp/belief_graph.py` são código morto/quase
   morto** no caminho vivo (só `benchmark_edp.py` toca `belief_graph.py`)
   — mais candidatos a remoção com evidência forte, na mesma categoria de
   `api.py`/`api_v2.py`/`retrieval_ann.py`.

---

## Recalibração de métrica (antecipa Regra F do adendo)

A tabela de critério de aceite do Eixo 1 (§4.2 do prompt original) usa
"edges de MemoryStore ≤ 35" como alvo. Com a decomposição EXTRACTED/INFERRED
acima, a métrica correta para acompanhar é **EXTRACTED apenas** (hoje 75,
não 97) — INFERRED de baixo score ancorado em linha de import isolada é
ruído sistemático do extrator, não responsabilidade real do node, e não
deve contar contra nem a favor de nenhum módulo no meio do refactor.

---

## Fim da Fase 0 — checkpoint humano necessário

Conforme Regra 5/estrutura do prompt ("a Fase 1 em diante só começa depois
dessas respostas estarem escritas") e a instrução explícita de não rodar em
modo autônomo sem checkpoint: **parando aqui.** Antes de iniciar a Fase 1
(refactor), preciso de decisão humana em pelo menos estes pontos que este
diagnóstico não pode resolver sozinho:

1. Destino de `api.py`/`api_v2.py` (1520 linhas), `retrieval_ann.py`,
   `edp/scheduler.py`, `edp/belief_graph.py` — remoção formal agora, ou
   depreciação com aviso por um ciclo?
2. Destino de `semantic_memory.py`/`get_pipeline_memory()` — o achado do
   Item 3 (split-brain ativo em todo turno) é maior que o esperado; decidir
   se entra neste ciclo de hardening (é comportamento no caminho vivo) ou
   fica registrado como dívida separada.
3. WAL/`WALEpisodicMemory` — entra neste ciclo ou não (Item 8).
4. Confirmar se a recalibração de métrica proposta acima (EXTRACTED só)
   é aceitável como substituto do "≤35 edges" bruto da tabela original.
