# AVALIACAO_ENGENHARIA_EDP.md — Avaliação de Engenharia Independente (EDP v5)

**SHA avaliado:** `0cacde86459b8acf6371bd50e8411ed8ab6b862b` (branch `exp017/fase1-dedup`, 6 commits à frente de `main@67f2f5b`)
**Data:** 22/07/2026. **Modo:** read-only absoluto — nenhum arquivo de código tocado, nenhum store real acessado.
**Método:** verificação direta no código-fonte atual. Docs do projeto (RELATORIO_*, RESULTADO_*, AVALIACAO_*) são citados como PONTO DE PARTIDA, nunca como fundamento de nota — toda afirmação relevante foi checada contra o código de hoje, não contra o que o doc diz que era verdade quando foi escrito.

Duas colunas por dimensão: **[P]** como projeto de pesquisa pessoal solo; **[C]** como candidato a produto/mercado. As réguas divergem de propósito.

---

## T1 — Verificações mínimas

**T1a — pytest:** `112 passed, 1 deselected` (`-m windows_only`, semântica `os.replace`/`PermissionError` específica de Windows, corretamente pulado fora de win32). Confirmado batendo com `docs/RUNBOOK.md` §11 executado literalmente e com o CI (`.github/workflows/tests.yml`, dois jobs `ubuntu-latest`/`windows-latest`, o job Windows roda `-m windows_only` adicionalmente — zero divergência entre runbook e CI).

**T1b — caminho vivo e código morto:** `run.py:serve()` → uvicorn → `edp/api/main.py:lifespan()` → `websocket.py` (handler por turno) → `run_pipeline()` (`pipeline.py`) → `edp/memory/store.py` (`MemoryStore`) → `edp/llm_adapter.py` (`EDPRuntime`). Módulos confirmados FORA do caminho vivo, com zero importador em todo o repo:
- `edp/vector_store.py` (280 linhas — `VectorStoreProtocol` ABC completa + `InMemoryVectorStore`, com `metrics()`/`health_report()`/`compact()`): `grep -rln "InMemoryVectorStore\|from.*vector_store import" edp/ tests/` → só o próprio arquivo.
- `edp/memory_graph.py`: `grep -rn "memory_graph" --include="*.py" .` → só o próprio docstring.
- Confirmado que o bloco v3.2 (8 módulos satélite: `biodiversity.py`, `economy.py`, `meta_stability.py`, `storm_guard.py`, `pressure_regulator.py`, `decision_graph_v32.py`, `snapshot_manager.py`, `orchestrator_v32.py`) **foi de fato removido** do código atual (`find . -iname "biodiversity.py" -o -iname "orchestrator_v32.py" ...` → vazio) — achado POSITIVO, `RESULTADO_AUDITORIA_EDP_v5.md` §5.2 (commit `f99835c`) confirmado contra o filesystem real, não só contra o próprio doc.

**T1c — LOC** (levantado por sub-tarefa dedicada, confirmado):

| Categoria | LOC |
|---|---|
| Total do repo (.py) | 42.976 |
| `edp/` (core, excl. lab) | 28.726 |
| `edp/lab/` (experimentos) | 5.995 |
| `edp/memory/` | 2.259 |
| `edp/runtime/` | 5.356 |
| `edp/api/` | 4.907 |
| `tests/` | 2.470 |
| `scripts/` | 1.611 |
| `*.py` da raiz | 4.174 |

`edp/llm_adapter.py`: **2.881 linhas / 57 métodos numa única classe** (`EDPRuntime`). `edp/memory/store.py`: **1.889 linhas**. `edp/vector_store.py` (280 linhas, código morto) não entra no "custo vivo" mas entra no custo de manutenção.

**T1d — inventário de flags** (`edp/config.py`, 29 env-vars): 3 default-ON produção (`EDP_HYBRID_RETRIEVAL`, `EDP_CTX_SLOTS`, `EDP_WRITE_PROVENANCE`, todas `"1"`), 3 experimento-OFF do exp017 (`EDP_RETRIEVE_SHUFFLE`/`DEDUP`/`RANDOM_DROP`, todas `"0"`), o resto são parâmetros de tuning com consumidor confirmado — **exceto `RETRIEVAL_BACKEND`** (`config.py:33`, default `"faiss_flat"`): zero referências fora da própria definição em todo o repositório — flag decorativa, o retrieval real é `sklearn.cosine_similarity` força-bruta.

**T1e — RUNBOOK.md:** existe (`docs/RUNBOOK.md`, 237 linhas). Procedimento de gate (§11) executado literalmente (`python -m pytest tests/ -q` e `-m windows_only -q`) — **funciona exatamente como documentado**, os dois números somam o total real de testes. O próprio runbook admite duas fragilidades reais: branch protection **nunca configurada** no GitHub ("Ninguém fez isso ainda", §11 — CI roda mas não bloqueia merge) e um incidente real (17/07/2026, §2) de env var "fantasma" persistida em escopo User do Windows que derrotou defaults do repo silenciosamente por semanas.

**T1f — severidade do furo do piso** (`edp/memory/semantic.py`, `SemanticMemory.retrieve()`, linhas 99-150 — não lê `answer_class`): comparado com `EpisodicMemory.retrieve()` (piso de peso 0.05×, `store.py:572-573`) e a exclusão dura do híbrido (`store.py:1595`, cobre as duas camadas porque opera no dict da entry, não por camada). Classificação: **MAJOR** (não BLOCKER). Justificativa: o default efetivo hoje é `EDP_HYBRID_RETRIEVAL=1` (`config.py:53`), caminho onde a exclusão é uniforme — o furo só é alcançável com a flag revertida para `0`. Mas essa reversão é *precisamente* a "rede de segurança" documentada para rollback do híbrido (`config.py:49`, "a env var é a rede de segurança") — ou seja, o caminho de emergência designado para desligar o híbrido é o mesmo caminho onde a supressão de conteúdo tóxico (`not_found`/`disqualification`, meses de trabalho do arco exp012→exp016) silenciosamente para de funcionar. Um rollback de emergência que compromete uma proteção ortogonal sem avisar é grave o suficiente para MAJOR, mas não é BLOCKER porque não afeta o default e já está documentado como dívida (`semantic.py:8-13`), não é surpresa.

**T1g — 3 pontos do Graph Report:** satélites v3.2 fora do caminho vivo — confirmado E removidos (ver T1b). `memory_graph.py` sem consumidor — confirmado (T1b). `reflection.reweights` (`edp/meta_reasoner.py`, computado em `_reweights()`, incluído em `ReflectionResult`/`PipelineResult.reweights` — `pipeline.py:394`): `grep -rn "\.reweights\b" --include="*.py" .` → **zero ocorrências de leitura em todo o repositório**. É computado a cada turno vivo (`meta.reflect()` roda dentro de `run_pipeline()`, que É caminho vivo) mas o resultado nunca é lido por ninguém — sofisticação real, influência zero.

---

## T2 — Scorecard (10 dimensões)

### 1. Arquitetura & modularidade
**[P] 6 / [C] 4.**
A favor: choke-point deliberado e documentado (`store.py:9-21` — piso e exclusão híbrida mantidos no mesmo módulo de propósito, decisão arquitetural explícita, não acidente).
Contra: `edp/llm_adapter.py` — 2.881 linhas / 57 métodos numa classe (`EDPRuntime`); `edp/memory/store.py` — 1.889 linhas; existiu uma segunda implementação de memória semântica paralela (`edp.semantic_memory`, hoje "aposentado" — `tests/test_run_pipeline_characterization.py:82-86`), evidência de drift arquitetural que já exigiu um ciclo de medição dedicado (`FASE0_5_MEDICAO_SPLITBRAIN.md`).

### 2. Qualidade de código
**[P] 6 / [C] 4.**
A favor: comentários explicam o PORQUÊ com contexto de incidente real (ex.: `SESSION_BOOST_FACTOR` calibrado contra um incidente de alucinação Docker/Redis datado — `RESULTADO_AUDITORIA_EDP_v5.md` §3.5), não são ruído.
Contra: fórmula de ranking com 9+ fatores multiplicativos encadeados (`store.py`, `rank_score = sim*decay*prio*ab*epi_multiplier*src_weight*dom_penalty*anchor_boost*session_boost*nf_floor`), a maioria sem calibração documentada (`RESULTADO_AUDITORIA_EDP_v5.md` §3.5: `DEDUP_THRESH`, `anchor_boost`, dominância trigger/penalidade — "❌ Sem documentação"/"❌ Por analogia").

### 3. Testes & verificação
**[P] 7 / [C] 5.**
A favor: 112 testes, zero `assert True`/`xfail`/skip-teatro encontrado; técnica de "prova de inércia" via monkeypatch para provar código morto antes de deletar (`test_run_pipeline_characterization.py:73-109`); padrão flag-off byte-idêntico aplicado consistentemente a cada feature flag nova (`test_flag_off_byte_identical.py`, reaplicado por mim mesmo no exp017 Fase 1 nesta sessão). **Comparação de mercado** (nota ≥7 exige): a disciplina de "prova de inércia antes de deletar" + regressão byte-idêntica por flag é do nível de times de experimentação maduros (estilo guardrail/pre-registered-analysis de shops de XP sérios), incomum em projeto solo.
Contra: `tests/` = 2.470 LOC contra `edp/` core = 28.726 LOC (~8,6%); suite 100% sintética (embeddings fake determinísticos, `tests/conftest.py:33-42`) — nunca roda contra um store real; marker `live_store` existe no `pytest.ini`/`conftest.py` mas **zero testes o usam** (`grep -rln "live_store" tests/*.py` → só `conftest.py`) — a capacidade de testar persistência real está andaimada mas nunca exercitada.

### 4. Metodologia experimental & rigor epistêmico
**[P] 9 / [C] 5.**
A favor: pré-registro de hipóteses ANTES do dado (`PRE_REGISTRO_EXP017.md`), erratas assumidas publicamente quando um raciocínio falhou (ERR-1/2/3, e a declaração "H2 INFALSIFICÁVEL COMO DESENHADO" — o próprio pesquisador documentou que seu critério de corte era matematicamente inalcançável, em vez de esconder), controle negativo + controle-reserva desenhados e implementados, gate degenerado avaliado ANTES da fase seguinte, validação de instrumento com predição pré-dado batendo exatamente (E6). Vivenciei esse processo de dentro nesta mesma sessão (implementação do exp017 Fase 1) — é rigor real, não teatro de processo. **Comparação de mercado:** nível de disciplina de pré-registro de ensaio controlado, aplicado por uma pessoa a um sistema de memória de uso pessoal — incomum mesmo em times comerciais de tamanho médio.
Contra: esse rigor é recente (pré-registro datado de 07/2026) e concentrado nos ciclos exp0XX; a maior parte dos parâmetros de scoring do sistema (dimensão 2) nunca passou por esse processo — é um padrão adotado, não retroaplicado.

### 5. Operabilidade
**[P] 6 / [C] 3.**
A favor: RUNBOOK funciona exatamente como escrito (T1e); CI com 2 jobs bate 1:1 com o runbook; existe teste de round-trip de backup/restore.
Contra: branch protection nunca configurada (T1e) — CI é decorativo em relação a bloqueio de merge; incidente documentado de env var fantasma quebrando comportamento por semanas sem detecção (T1e); nenhuma observabilidade externa (APM/alerting) além de health/dashboard local.

### 6. Segurança & integridade de dados
**[P] 4 / [C] 2.**
A favor: writes atômicos existem (`edp/memory/atomic_io.py`); um bug real de disaster-recovery foi achado e corrigido via teste hermético (`incremental_backup()`/`restore_backup()` — padrão de nome de arquivo dessincronizado fazia `restore_backup()` retornar `{"ok": False}` mesmo logo após um backup bem-sucedido, commit `d551af5`).
Contra — **achado mais grave desta avaliação**: `EpisodicMemory._load()` (`store.py`, por volta de 333-338) não tem `try/except` ao redor do parse de JSON — truncamento no meio do objeto propaga `json.JSONDecodeError` e quebra a construção inteira do `MemoryStore` no boot. Isso **ainda está vivo hoje**, provado por um teste que confirma o crash de propósito (`tests/test_failsafe_roundtrip.py:168-186`, `test_reload_apos_truncamento_no_meio_do_objeto_propaga_erro`, usa `pytest.raises(json.JSONDecodeError)`) — ou seja, o comportamento correto documentado pelo próprio teste é "isso quebra", não "isso é tratado". Some a isso o furo do piso (T1f, MAJOR).

### 7. Performance & escalabilidade
**[P] 5 / [C] 2.**
A favor: fusão híbrida BM25+vetorial via RRF é escolha de arquitetura razoável e medida (exp010); cache de embeddings existe (`edp/cache.py`, SQLite).
Contra: retrieval é força-bruta — `sklearn.cosine_similarity` direto sobre arrays numpy em cada entry de cada camada, a cada query; `RETRIEVAL_BACKEND` (T1d) e `edp/vector_store.py` (T1b) — a abstração pra índice aproximado existe e nunca foi ligada. Overfetch do exp017 Fase 1 (`EDP_RETRIEVE_DEDUP`, quando ligado) pede `len(camada)` inteira por query — multiplica o custo justamente no caminho novo, sem medição de latência. Nenhum teste ou medição existe em escala além da real conhecida (594 episódicas / 134 semânticas após 36 dias, `ANALISE_SATELITES_V32.md`) — não há dado sobre 10x/100x/1000x, nem estimativa.

### 8. Gestão de dívida técnica & documentação
**[P] 5 / [C] 3.**
A favor: `docs/DIVIDAS.md` tem formato exemplar quando usado (status/workaround/correção) — Dívida #41 é um caso fechado com evidência e teste dedicado (`tests/test_divida_41.py`, 7 checks).
Contra — **claim auditado e refutado**: `docs/DIVIDAS.md:3` se autodeclara "Lar único e versionado das dívidas técnicas do projeto", mas `grep -rn "[Dd]ívida #" edp/ tests/ --include="*.py"` encontra pelo menos **16 IDs distintos** de dívida (#8, #9, #10, #11, #12, #24, #40, #41, #42, #45, #46, #47, #48, #49, #50, #52) referenciados em comentários de código — o arquivo documenta só 2 (#41, #46d). A "fonte única" cobre ~12% das dívidas realmente referenciadas.

### 9. Vitalidade do código (vivo/morto, loops produz-e-descarta)
**[P] 5 / [C] 3.**
A favor: poda ativa quando código morto é achado — `ANNRetrievalEngine` removida por evidência de grep exaustivo (`RELATORIO_HARDENING_EDP_V5.md:29`), bloco v3.2 inteiro removido (T1b), `edp.semantic_memory` aposentado. Isso é comportamento saudável — quando encontram, geralmente agem.
Contra — **claims auditados, resultado misto**: `RESULTADO_AUDITORIA_EDP_v5.md` (auditoria anterior) já catalogou 6 "loops abertos" (sinal produzido, nunca consumido). Reverifiquei hoje, direto no código atual: (a) **`reflection.reweights` — ainda morto** (T1g, zero leituras); (b) **`cognitive_decisions` — ainda fora da fórmula de ranking** (`grep -n "cognitive_decisions" edp/memory/store.py` → zero); (c) `contradiction_flagger.scan_results()` — chamado sem capturar retorno em AMBOS os caminhos de retrieve hoje (`store.py:1537`, `store.py:1749`), mesmo padrão "descartado" de quando a auditoria foi escrita. Em contraste, **dois outros itens da mesma tabela FORAM corrigidos** desde então: `pareto_store` (a auditoria dizia "zero leitores para decisão"; hoje tem 8 arquivos consumidores) e `health_index` (idem, hoje tem 3+ consumidores) — mostrando que o padrão é remediável, mas nem tudo que uma auditoria formal recomendou fechar foi de fato fechado, e novos órfãos apareceram no meio tempo (`vector_store.py`, `memory_graph.py`, T1b) que a auditoria anterior nem cobria.

### 10. Valor entregue vs complexidade carregada
**[P] 6 / [C] 3.**
A favor: sistema roda 36 dias reais sem crash reportado (`ANALISE_SATELITES_V32.md`), entrega governança epistêmica de fato funcional — algo sem equivalente pronto no mercado de ferramentas acessíveis (ver `AVALIACAO_MEMORIA_VS_MERCADO.md`, já escrito nesta sessão e cujos claims centrais — ausência de governança epistêmica em produtos de mercado, ausência de grafo de conhecimento aqui — seguem consistentes com o que foi reverificado nesta auditoria).
Contra: ~43.000 LOC e 29 flags de ambiente para servir memória de uso pessoal de um único usuário — desproporção real entre superfície de manutenção e o que chega ao usuário final; a dimensão 9 mostra parte relevante desse esforço sendo write-only (nunca influencia o comportamento observável).

---

## T3 — Síntese

### a) Sumário executivo

O EDP v5 é um sistema de memória para LLM construído com um rigor de metodologia experimental que está genuinamente acima do que se vê em projetos pessoais — e até em muitos times comerciais — mas essa disciplina não se estende à base de código como um todo. A arquitetura tem dois arquivos-deus (`llm_adapter.py` com 2.881 linhas/57 métodos, `store.py` com 1.889), um padrão recorrente de subsistemas sofisticados que nunca são lidos de volta (reweights, cognitive_decisions, e historicamente pareto_store/health_index antes de serem remediados), e pelo menos duas abstrações completas e mortas (`vector_store.py`, `memory_graph.py`). O achado mais sério não é arquitetural, é operacional: um JSON truncado no boot ainda derruba a construção inteira do `MemoryStore`, sem tratamento — e isso está provado, não suposto, por um teste que a própria equipe escreveu para confirmar que o crash acontece. Como projeto de pesquisa pessoal, é sólido e autoconsciente — o time se audita, às vezes age, mas frequentemente não fecha o que a própria auditoria anterior recomendou. Como candidato a produto, está numa fase de protótipo de laboratório: força-bruta sem índice real, sem branch protection, sem observabilidade externa, com um crash de boot não tratado. Nenhuma das duas leituras invalida a outra — são réguas diferentes, aplicadas ao mesmo código.

### b) Top-5 riscos (severidade × probabilidade)

1. **Crash de boot por JSON truncado, sem tratamento** (`store.py` ~333-338, `EpisodicMemory._load()`) — severidade alta (perde acesso à sessão inteira), probabilidade não-trivial (qualquer interrupção de processo no meio de um write não-atômico externo, ou disco cheio, pode truncar). Já existe teste provando o crash — falta o `try/except`.
2. **Rollback de emergência do híbrido reabre o furo do piso** (T1f) — se algum dia `EDP_HYBRID_RETRIEVAL=0` for usado como mitigação de incidente, a supressão de conteúdo tóxico para de funcionar ao mesmo tempo, sem aviso.
3. **Branch protection inexistente** — qualquer commit pode ir para `main` sem revisão nem CI verde obrigatório; o runbook admite isso abertamente.
4. **Ausência total de dado de escala** — força-bruta sem medição em volume; o primeiro sinal de problema de latência será em produção, não em teste.
5. **Dívida não-central** (`DIVIDAS.md` cobre ~12% das dívidas referenciadas em código) — decisões futuras sobre o que já foi tentado/descartado dependem de grep em comentários, não de um registro confiável.

### c) Top-5 forças genuínas (com comparativo de mercado)

1. **Governança epistêmica de primeira classe** (`contradicted`/`quarantined`/`hypothesis`, piso e exclusão de conteúdo tóxico) — a maioria das ferramentas de memória de mercado acessíveis (Mem0, memória nativa de assistentes, LangChain memory) não modela confiabilidade da memória como estado; aqui é tratado como cidadão de primeira classe desde o design.
2. **Pré-registro experimental com corte de decisão fixado antes do dado** (`PRE_REGISTRO_EXP017.md`) — nível de disciplina de ensaio controlado / guardrail metrics de times de XP maduros, aplicado por uma pessoa.
3. **Autoauditoria real e às vezes acionável** — `RESULTADO_AUDITORIA_EDP_v5.md` achou 19 problemas confirmados e corrigiu ativamente pelo menos 2 desta lista (`pareto_store`, `health_index`) entre a auditoria e hoje — evidência de que a crítica interna não é só documento, vira commit.
4. **RUNBOOK que bate 1:1 com a realidade** — testado literalmente nesta avaliação, sem divergência. Muitos produtos comerciais têm runbooks mais desatualizados que este projeto pessoal.
5. **Padrão "flag-off byte-idêntico" aplicado com disciplina** — toda feature experimental nova (shuffle, dedup, ctx_slots, hybrid) vem com teste dedicado provando que desligada não muda nada; é o tipo de rede de segurança que produtos comerciais com múltiplas feature flags em voo (ex.: qualquer plataforma com feature-flagging maduro) dependem para não quebrar produção silenciosamente.

### d) Se eu herdasse este codebase amanhã — plano de 90 dias

**Semanas 1-2:** (1) Envolver `EpisodicMemory._load()`/`SemanticMemory._load()` em `try/except` com fallback seguro (não crashar o boot; degradar para sessão vazia + alerta, nunca silencioso) — é o único item desta lista que eu trataria como bloqueante de verdade. (2) Ligar branch protection no GitHub (5 minutos, zero desculpa para não ter feito). (3) Fechar o furo do piso em `semantic.py` (aplicar o mesmo one-liner de `answer_class` que já existe em `EpisodicMemory.retrieve()` — a dívida já está documentada, é dívida de baixo custo de fechar).

**Mês 1:** Consolidar `docs/DIVIDAS.md` como fonte real — varrer todos os `#NN` em comentários e migrar pra lá ou fechar deliberadamente (não deixar como "achado numa auditoria antiga, nunca resolvido"). Decidir explicitamente sobre `reflection.reweights`/`cognitive_decisions`: ou conectar ao ranking (com o mesmo rigor de pré-registro do exp017) ou deletar o código que os produz — não deixar rodando sem efeito. Deletar `vector_store.py` e `memory_graph.py` (zero consumidor, zero plano documentado de uso) ou documentar por que ficam.

**Trimestre:** Quebrar `llm_adapter.py` (2.881 linhas) em módulos por responsabilidade (retrieval assembly, streaming, debug/telemetria) — sem tocar comportamento, no mesmo estilo cauteloso de "MOVE-ONLY, corpos byte-idênticos" que já usaram para o split de `store.py`. Rodar uma medição real de latência/custo em volume 10x-100x do atual antes de qualquer decisão de indexação aproximada — não implementar FAISS/HNSW especulativamente, decidir com dado (mesma cultura de pré-registro já existente, aplicada aqui).

**O que eu DELETARIA já:** `edp/vector_store.py` (280 linhas mortas), `edp/memory_graph.py`, o flag `RETRIEVAL_BACKEND` (decorativo), e qualquer resquício de referência a `edp.semantic_memory` (já "aposentado", só falta remover o que resta).

### e) Veredito final

**[P] 6/10** — sólido para o propósito declarado, com uma força de metodologia (9) puxando a média para cima e um risco de integridade de dados real (4) puxando pra baixo. **O perfil É bimodal**: a metade "processo de pesquisa" (metodologia experimental, autoauditoria, testes, runbook) vive na faixa 6-9; a metade "engenharia de sistema" (segurança de dados, performance, operabilidade, dívida) vive na faixa 2-5. Não é uma nota única representativa — é um projeto de pesquisa excelente rodando sobre uma fundação de sistema que não recebeu o mesmo rigor.

**[C] 3/10** — funciona mas frágil; dívida domina. Nenhuma dimensão de infraestrutura (operabilidade, segurança de dados, performance) passa de 3 nesta coluna. A força real (governança epistêmica, metodologia) não compensa: mercado não avalia rigor de processo interno, avalia o que sobrevive a produção em escala — e aqui não há evidência de que sobreviveria além da escala pessoal atual (594/134 entries, 36 dias, um usuário).
