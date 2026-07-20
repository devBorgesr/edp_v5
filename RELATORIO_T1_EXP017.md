# RELATORIO_T1_EXP017.md — Leitura e relato (T1)

Branch `exp017/fase0-medicao`, a partir de `main@100b0c8`. Contrato:
`PRE_REGISTRO_EXP017.md@100b0c8`. Este relato é só leitura — nenhum código
de produção foi alterado para produzi-lo. Aceito integralmente pelo
pesquisador em 20/07/2026; achados incorporados ao desenho de T2-T7.

## T1a — `retrieval_monitor.py`: como o "80% repetitivo" é computado HOJE

**Ponto de medição: top-k BRUTO, não pós-builder.**

`RetrievalQualityMonitor.record_turn()` (`edp/runtime/retrieval_monitor.py:93-120`)
é chamado de dois lugares em `MemoryStore`, ambos **antes** do
`context_builder`/`ContextWindowManager`:
- `edp/memory/store.py:1388` — caminho cosine (padrão), passa `final_top`
  já ordenado por `ranking_score` e truncado em `top_k`
  (`store.py:1377-1381`).
- `edp/memory/store.py:1553` — caminho híbrido (`EDP_HYBRID_RETRIEVAL`),
  mesma semântica, `final_top` do RRF.

Ou seja: o monitor mede o conjunto que `MemoryStore.retrieve()` devolve,
**não** o que sobrevive ao `ContextWindowManager.build()` (esse é o
`retrieval_kept` que o pré-registro define como novo ponto de medição
primário).

**Janela: não é N-turnos fixo — é comparação turno-a-turno dentro do
bucket do dia corrente.**
- Buckets diários (`DailyBucket`, `retrieval_monitor.py:38-71`),
  `day_start = now - (now % 86400)` (`:88`). Cruzar meia-noite gera bucket
  novo com `last_top_ids=[]`.
- A cada `record_turn`, compara `result_ids` do turno atual
  (`current_ids`) contra `bucket.last_top_ids` (turno **imediatamente
  anterior** no mesmo dia) — não uma janela de N turnos, é sempre par
  consecutivo.

**Fórmula exata** (`retrieval_monitor.py:113-118`, `:60-61`):
```
overlap = set(current_ids) & set(last_top_ids)
repeat_count += 1  se  len(overlap) >= min(2, len(current_ids))
repeat_rate (do dia) = repeat_count / turn_count
```
Comparação **por ID**, não por hash de texto — mede repetitividade
cross-turn (fenômeno C do pré-registro), não fenômeno A.

O "80%" citado na motivação vem do `snapshot()`/warning: dispara quando
`last["turns"] >= 5 and last["repeat_rate"] > 0.6` (`:146`), texto do log
em `:147-151` — exatamente esse `repeat_rate`, do bucket do dia mais
recente.

**Conclusão T1a:** o número histórico mede repetição de IDs entre turnos
consecutivos, no **top-k bruto pré-builder**. A formalização nova (T5)
espelha a fórmula (overlap≥min(2,n)) mas aplica-a no `retrieval_kept`
pós-builder — pontos de medição diferentes por desenho do pré-registro,
não um bug a corrigir.

## T1b — mapeamento retrieve → context_builder → retrieval_kept

`edp/context_builder.py::build_context()` (MMR-like, dedup por embedding)
**não é o caminho de produção do chat** — só é chamado por
`benchmark_edp.py:750/759/771`. O caminho real é
`EDPRuntime._retrieve_context()` → `EDPRuntime._build_enriched_context()`
→ `ContextWindowManager.build()`.

**Ponto (i) — inserção do SHUFFLE**, `edp/llm_adapter.py:2334`:
```python
results = self._memory.retrieve(query, top_k=5, min_score=0.20)
```
`results` já vem ordenado por `ranking_score` desc e truncado (mesmo
`final_top` que alimenta o monitor em T1a). Logo depois (`:2343`,
`for r in results:`), cada item vira uma string (`prefix+txt`) empilhada
em `blocks`/`_sim_blocks`, na ordem de `results`. Os filtros do loop
(`seen_ids`, `if not txt`) são por membership/conteúdo — independentes de
ordem, então embaralhar `results` antes do loop preserva o CONJUNTO final
e só reordena — ponto de inserção correto e seguro para
`EDP_RETRIEVE_SHUFFLE`.

Essa ordem importa de fato: `blocks` (via `_retr_blocks`,
`llm_adapter.py:2651/2661-2665`) vira `retrieval=` de
`ContextWindowManager.build()`, cujo loop é **guloso, sensível à ordem, e
para no primeiro item que estoura o budget**
(`context_window_manager.py:321-327`):
```python
for item in retrieval[:self.max_retrieval]:
    cost = estimate_tokens(item)
    if budget.remaining >= cost:
        ctx.retrieval.append(item); budget.retrieval_tokens += cost
    else:
        break
```
Confirma o mecanismo assumido em H2 (item 7 da motivação): com budget
apertado, reordenar `results` pode trocar QUAIS itens sobrevivem ao
`break`, não só a ordem de apresentação.

**Ponto (ii) — kept final com IDs acessíveis: NÃO EXISTE hoje.**
`ctx.retrieval` (`BuiltContext.retrieval`, `context_window_manager.py:134`)
é `List[str]` — texto puro, sem ID. O único log correlato,
`[ctx-DEBUG #46] retrieval_kept=...` (`llm_adapter.py:2706-2709`), loga
**apenas `len(b)` de cada bloco** — confirma a suspeita do pré-registro
("só lens").

O ID existe até `_debug_similarity` (`llm_adapter.py:2372-2378`, dict com
`"id"`) e até `co_occurrence_ids` (`:2352`), ambos locais à mesma
iteração de `results` — mas não propagam além do log de debug
(`log_context`, `:2412-2419`, grava em arquivo de debug, não retorna). O
único vínculo de identidade que sobrevive é implícito:
`_last_similarity_blocks` (`self._last_similarity_blocks = _sim_blocks`,
`:2423`) guarda os MESMOS objetos-string por `id()` Python, e
`EDP_CTX_SLOTS` já usa esse truque (`_sim_ids = {id(b) for b in _sim}`,
`:2655`) para separar retrieval de metadados.

**O que precisa ser propagado (declarado em T1, implementado no T4):** um
mapeamento `id(string) → entry_id` paralelo a `_sim_blocks`, construído no
mesmo loop que já produz `_debug_similarity` (`:2372`), guardado como
`self._last_similarity_ids` ao lado de `_last_similarity_blocks`
(`:2423`). Depois de `ctx = mgr.build(...)` (`:2661`), para cada string em
`ctx.retrieval` cujo `id()` está nesse mapa, resolve-se o `entry_id` — dá
o `retrieval_kept` por ID sem alterar nenhum comportamento (leitura pura,
dict novo, zero mudança no fluxo de blocos/budget). Enquadra-se em
"propagação de metadado read-only conta como instrumentação" do
pré-registro.

Hash normalizado (fenômeno A no resultado) é computável no mesmo ponto a
partir do texto do bloco (`strip+casefold+colapso de whitespace` sobre
`txt`, antes do prefixo de tags) — capturado no mesmo dict paralelo, já
que `ctx.retrieval` só tem a string prefixada.

## Resumo dos pontos fixados para T2-T7

- SHUFFLE entra em `edp/llm_adapter.py:2334`, embaralhando `results`
  antes do loop `:2343`.
- `retrieval_kept` (ponto primário de medição) = `ctx.retrieval` pós
  `ContextWindowManager.build()` (`llm_adapter.py:2661`), resolvido por ID
  via o mapa `id(string) → entry_id` propagado no T4.
- Monitor histórico (T1a) permanece como referência de comparabilidade,
  medido no top-k bruto (`store.py:1388/1553`) — T5 reporta os dois
  pontos (emenda E1), não substitui um pelo outro.
