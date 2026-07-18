# Relato — Fase 4: Split do MemoryStore + Clock Injetável

Branch `hardening/fase4-memoria-e-clock`, a partir de `main` (b20b2b4). Gate:
pytest completo verde após cada commit (62 passed, 1 deselected — marker
`windows_only`). Sem push, sem PR.

## T0 — fotografia

- `git log -1` no ponto de partida: `b20b2b4bf0f175bc846da15a2bca4fc1fed122b5`
  (merge PR #12, hardening/fase3-operacional).
- `graphify query MemoryStore` (edges EXTRACTED, nó da classe): **47**
  (Fase 0 media original: 75).

## T1a — tabela de classificação (grep fresco, módulos vivos de `edp/`, excluindo `clock.py`, `edp/lab/`, scripts)

### Timestamp-semântica — migradas para `edp.clock.now()`

| Arquivo:linha (após migração) | Contexto | Commit |
|---|---|---|
| `edp/api/routes/dashboard_state.py:26` | timestamp da resposta `/dashboard/state` | `71943ae` |
| `edp/api/routes/health.py:70` | timestamp da resposta `/health` | `71943ae` |
| `edp/cache.py:77` | `created_at` do embed cache (SQLite, insert single) | `657d391` |
| `edp/cache.py:130` | `created_at` do embed cache (SQLite, insert batch) | `657d391` |
| `edp/context_debug.py:78` | carimbo humano-legível do debug log de contexto | `0dee375` |
| `edp/failsafe.py:33` | `detect_corruption()` — rejeita timestamp futuro | `23f3d29` |
| `edp/failsafe.py:56` | `incremental_backup()` — sufixo do arquivo de backup (int) | `23f3d29` |
| `edp/llm_adapter.py:2167` | referência "agora" pro bloco de histórico cronológico compacto | `89e16c0` |
| `edp/llm_adapter.py:2201` | `_now_for_gap` — formatação de gap/idade nos labels da janela imediata | `89e16c0` |
| `edp/pressure.py:92` | `StorePressureSnapshot.ts` (exposto via dashboard) | `235097b` |
| `edp/runtime/auto_consolidation.py:147` | `now_ts` — cooldown gate entre consolidações | `4cbb431` |
| `edp/runtime/boot_state.py:69` | `_state_since` (init) | `4cbb431` |
| `edp/runtime/boot_state.py:97` | `now` — duration_s + `_state_since` na transição | `4cbb431` |
| `edp/runtime/boot_state.py:130` | `ComponentHealth.last_check` | `4cbb431` |
| `edp/runtime/boot_state.py:136` | `_state_since` na transição p/ DEGRADED | `4cbb431` |
| `edp/runtime/boot_state.py:148` | `_state_since` na recuperação p/ READY | `4cbb431` |
| `edp/runtime/boot_state.py:166` | `uptime_s` em `to_dict()` (exposto via `/health`) | `4cbb431` |
| `edp/runtime/inference_queue.py:214` | `InferenceToken.cancelled_at` | `4cbb431` |
| `edp/runtime/pressure_governor.py:136` | cache TTL da leitura de RAM (`CHECK_TTL_S`) | `4cbb431` |
| `edp/write_provenance.py:180` | `_log_quarantine()` — auditoria da quarentena (Dívida #8/exp012/exp016) | `a692971` |

`edp/memory.py` (agora pacote) já usava `edp.clock` desde a Peça 0.2a — nada
a migrar ali nesta fase.

### Medição-de-performance — **FICA** em `time.perf_counter()` (relógio NTP não é cronômetro)

| Arquivo:linha | Contexto |
|---|---|
| `edp/pipeline.py:211` | `t_start` do trace de pipeline |
| `edp/pipeline.py:241` | `ts` relativo no trace (`- t_start`) |
| `edp/llm_adapter.py:1481` | `t0` — latência de chamada LLM |
| `edp/llm_adapter.py:1535` | `ms` (`- t0`) |
| `edp/llm_adapter.py:1584` | `t_start` — streaming |
| `edp/llm_adapter.py:1699` | `t_first_token` (`- t_start`) |
| `edp/llm_adapter.py:1713` | `total_ms` (`- t_start`) |
| `edp/llm_adapter.py:1871` | `t0` (`_time.perf_counter()`) |
| `edp/llm_adapter.py:1892` | `latency_ms` (`- t0`) |
| `edp/llm_adapter.py:1903` | `latency_ms` em dict de retorno |
| `edp/metrics.py:20` | `t0` do decorator de timing |
| `edp/metrics.py:22` | `ms` (`- t0`) |
| `edp/vector_store.py:178` | `t0` — latência de busca vetorial |
| `edp/vector_store.py:226` | `ms` (`- t0`) |
| `edp/llm/providers/ollama.py:68` | `t_start` — latência do provider Ollama |
| `edp/llm/providers/ollama.py:77` | `latency_ms` (`- t_start`) |
| `edp/llm/providers/anthropic.py:226` | `t_start` — latência do provider Anthropic |
| `edp/llm/providers/anthropic.py:239` | `latency_ms` (`- t_start`) |
| `edp/llm/providers/anthropic.py:305` | `t_start` — streaming |
| `edp/llm/providers/anthropic.py:389` | `elapsed` (`- t_start`) |
| `edp/observability/tracing.py:38` | `Span.start` |
| `edp/observability/tracing.py:45` | `Span.end` (fallback) |
| `edp/observability/tracing.py:51` | `ts` relativo (`- self.start`) |
| `edp/observability/tracing.py:111` | `span.end` |
| `edp/runtime/inference_queue.py:133` | `wait_start` — tempo de espera na fila |
| `edp/runtime/inference_queue.py:151` | `wait_ms` (`- wait_start`) |
| `edp/runtime/inference_queue.py:158` | `run_start` — tempo de execução |
| `edp/runtime/inference_queue.py:169` | `run_ms` (`- run_start`) |
| `edp/tools/registry.py:87` | `t_start` — latência de tool call |
| `edp/tools/registry.py:123` | `latency` (`- t_start`) |

Nota: `edp/runtime/inference_queue.py` tem as duas classes lado a lado no
mesmo arquivo — `cancelled_at` (semântica, migrado) e `wait_start`/`run_start`
(performance, intocado) — confirma que a distinção é por uso, não por
arquivo.

## T1b/d — frozen_clock (injeção completa)

Esqueleto original só congelava `edp.clock.now` diretamente. Implementada
`_FrozenClock` (callable, `.advance()`/`.set()`) injetada simultaneamente em
`edp.clock.now` e em todo `_now` já vinculado por import
(`_CLOCK_BOUND_MODULES` em `tests/conftest.py`, 26 módulos vivos). Testes de
determinismo em `tests/test_clock_frozen.py`. Commit `d7922bf`.

## T2 — caracterização pré-split (`tests/test_memory_split_characterization.py`)

- `test_session_gap_marker_new_session_on_large_gap` / `_same_session_on_small_gap`:
  gap > `SESSION_GAP_THRESHOLD_SEC` (14400s) dispara novo `session_marker`;
  gap menor preserva o marker.
- `test_save_roundtrip_both_scopes`: add em `cognitive` e `sprint` → `save()`
  → reload do disco → os dois scopes íntegros.
- Commit `3a061c1`.

## T3 — o split: `memory.py` (1991 linhas) → pacote `edp/memory/`

### Mapeamento linha-de-origem → módulo-destino

| Origem (linhas, `memory.py` pré-split) | Conteúdo | Destino |
|---|---|---|
| 1–98 | docstring do módulo, imports, constantes de sessão (`SESSION_GAP_THRESHOLD_SEC` etc.) | docstring reescrita em `__init__.py`; imports + constantes movidos para `store.py` |
| 101–249 | `_serialize`, `_deserialize`, `_get_write_lock`, `_atomic_write_json`, `_safe_load_json` (Dívida técnica #8) | `atomic_io.py` |
| 254–296 | `_edp_lifetime_path`, `_get_edp_lifetime` | `store.py` |
| 296–403 | `_new_entry` | `store.py` |
| 405–433 | `WorkingMemory` | `store.py` |
| 436–1143 | `EpisodicMemory` (inclui o piso `NOT_FOUND_FLOOR`, linha original ~717–724) | `store.py` |
| 1144–1275 | `SemanticMemory` (inclui `promote()`, linha original ~1181–1204) | `semantic.py` |
| 1291–1361 | `_LEGACY_SUFFIX_MAP`, `_migrate_legacy_session_files` | `store.py` |
| 1362–1375 | `_ScopedView` | `store.py` |
| 1376–1991 | `MemoryStore` (inclui a exclusão do índice híbrido, linha original ~1731–1745, e `_retrieve_hybrid`) | `store.py` |
| — | contrato de re-export (`MemoryStore`, `EpisodicMemory`, `SemanticMemory`, `WorkingMemory`, `_safe_load_json`, `_atomic_write_json`, `_get_edp_lifetime`, `MEMORY_DIR`, `_now`, `SESSION_GAP_THRESHOLD_SEC` e afins) | `__init__.py` |

Resultado: `atomic_io.py` (164 linhas), `semantic.py` (157 linhas), `store.py`
(1704 linhas), `__init__.py` (48 linhas) — 2073 linhas totais no pacote.

### Confirmação explícita — restrição do choke-point (T3c, item G do adendo)

**O piso `NOT_FOUND_FLOOR` e a exclusão do índice híbrido pousaram
ADJACENTES no mesmo módulo (`edp/memory/store.py`), conforme exigido:**

- Piso: `edp/memory/store.py:572` —
  `from ..config import EDP_WRITE_PROVENANCE as _WP, NOT_FOUND_FLOOR as _NF, TOXIC_ANSWER_CLASSES as _TAC`,
  dentro de `EpisodicMemory.retrieve()` (`def retrieve` em `store.py:476`).
- Exclusão do híbrido: `edp/memory/store.py:1455` —
  `from ..config import EDP_WRITE_PROVENANCE as _WP12, TOXIC_ANSWER_CLASSES as _TAC12`,
  dentro de `MemoryStore._hybrid_index()` (`def _hybrid_index` em `store.py:1405`).
- Comentário CHOKE-POINT no topo do arquivo (`store.py:9-21`) documenta a
  adjacência e aponta onde o one-liner equivalente para `SemanticMemory`
  deve entrar quando o piso for estendido para lá (`semantic.py`).

**Desvio do corte proposto originalmente**: o corte inicial previa
`episodic.py` separado de `store.py`. Isso foi **proibido** pela restrição
acima — `EpisodicMemory` (dona do piso) e `MemoryStore` (dona da exclusão do
híbrido) tiveram que ficar no mesmo arquivo. `episodic.py` não existe; seu
conteúdo está em `store.py` junto com `MemoryStore`.

### ALERTA δ (pego 2x neste split)

`MEMORY_DIR` e `_now` são nomes vinculados por `from ..config import
MEMORY_DIR` / `from ..clock import now as _now` — cada módulo do pacote que
faz esse import tem sua PRÓPRIA cópia do nome, não uma referência viva ao
módulo de origem. Dois lugares precisaram de patch explícito em
`tests/conftest.py` além do `edp.memory` original:

1. `edp.memory.semantic.MEMORY_DIR` (extração 2/3, commit `53703bb`) — sem
   isso, `SemanticMemory` gravava fora do `tmp_path` isolado nos testes.
2. `edp.memory.store.MEMORY_DIR` e `edp.memory.store._now` (extração 3/3,
   commit `2feccf2`) — sem isso, `frozen_clock` e `isolated_base_dir` não
   afetariam `EpisodicMemory`/`MemoryStore` de verdade (o código real que
   roda vive em `store.py`, `edp.memory` é só re-export).

`test_synthetic_store_isolated` (sentinela) e a suite completa passaram
depois dos dois fixes.

## T4 — medição

| Métrica | Antes (T0) | Depois (pós-split) |
|---|---|---|
| `MemoryStore` (classe) — edges EXTRACTED | 47 (Fase 0 original: 75) | **48** |
| `atomic_io.py` — edges EXTRACTED (nível arquivo) | — | 9 |
| `semantic.py` — edges EXTRACTED (nível arquivo) | — | 11 |
| `store.py` — edges EXTRACTED (nível arquivo) | — | 41 |
| `__init__.py` — edges EXTRACTED (nível arquivo) | — | 17 |
| `memory.py` (arquivo único, pré-split) | 1991 linhas, 1 nó de arquivo | dissolvido em 4 nós |
| Ciclos de import no pacote | — | **0** |

`MemoryStore` ficou praticamente estável (47→48) — **esperado**: MOVE-ONLY
não reduz o acoplamento da classe-facade em si (ainda ~30 métodos + ~8
importadores externos: `benchmark_edp.py`, `llm_adapter.py`, `exp009.py`,
`exp010.py`, `measure_ss_dominance.py`, `run.py`, `registry.py`,
`cleanup_orphans.py`); o que o split reduz é a concentração no **arquivo**,
não na classe. 48 > 35 (meta direcional do adendo F) — reportado o número
real conforme instruído; o gate duro (suite verde + superfície intacta) foi
cumprido. `store.py` continua o maior arquivo do pacote por causa do
choke-point (não é possível reduzi-lo mais sem violar a restrição de
adjacência).

Ciclos de import: 0 — DAG linear `atomic_io.py` ← `semantic.py` ← `store.py`
← `__init__.py`; todos os imports de volta para `echo_chamber`/`blocks`/
`retrieval_hybrid`/`runtime.*` já eram lazy (dentro de método) no arquivo
original, preservados como estavam.

## Os 14 commits (branch `hardening/fase4-memoria-e-clock`, a partir de `main`)

1. `71943ae` — refactor(clock): migra timestamps de resposta em api/routes para edp.clock
2. `657d391` — refactor(clock): migra created_at do embed cache para edp.clock
3. `0dee375` — refactor(clock): migra timestamp do debug log de contexto para edp.clock
4. `235097b` — refactor(clock): migra timestamp de StorePressureSnapshot para edp.clock
5. `4cbb431` — refactor(clock): migra timestamps de estado runtime para edp.clock
6. `89e16c0` — refactor(clock): migra 2 chamadas time.time() remanescentes em llm_adapter
7. `23f3d29` — refactor(clock): migra failsafe.py (backup/corrupção) para edp.clock
8. `a692971` — refactor(clock): migra timestamp de auditoria de quarentena para edp.clock
9. `d7922bf` — test(clock): frozen_clock propaga para todo _now vinculado por import
10. `3a061c1` — test(memory): caracterização pré-split — gap de sessão + roundtrip 2 scopes
11. `a5ab641` — refactor(memory): memory.py vira pacote edp/memory/ — extrai atomic_io.py
12. `53703bb` — refactor(memory): extrai SemanticMemory para edp/memory/semantic.py
13. `2feccf2` — refactor(memory): extrai store.py e finaliza split — __init__.py vira contrato puro
14. `a8ab483` — chore(memory): remove import duplicado de SemanticMemory em store.py

(Ordem cronológica; `git log --oneline main..HEAD` mostra do mais recente
para o mais antigo.)

## Pendências de lógica avistadas — NÃO tocadas

- `import math` e `DECAY_LAMBDA` (config) eram dead code no `memory.py`
  original (nunca usados em lugar nenhum do arquivo, confirmado por grep
  antes de qualquer edição). `math` não foi ressuscitado em nenhum dos 3
  arquivos novos; `DECAY_LAMBDA` foi mantido na tupla de import de
  `store.py` (junto de `MEMORY_DIR`/`MAX_MEMORY`) para não fazer um corte de
  julgamento além do MOVE-ONLY.
- `benchmark_edp.py` faz `mem_mod.MEMORY_DIR = tmp_dir` (atribuição direta de
  atributo de módulo, não `monkeypatch`) em dois pontos (linhas ~389, ~728).
  É a mesma classe do ALERTA δ, mas em código de produção/benchmark fora do
  gate pytest: depois do split essa atribuição não propaga mais para
  `edp.memory.store.MEMORY_DIR`/`edp.memory.semantic.MEMORY_DIR`. Não
  corrigido nesta fase — corrigir tocaria a lógica de `benchmark_edp.py`,
  fora do escopo de `memory.py`.
- `NOT_FOUND_FLOOR`/`TOXIC_ANSWER_CLASSES` são reimportados de `config` a
  cada chamada de `EpisodicMemory.retrieve()` e `MemoryStore._hybrid_index()`
  (import local, não hoisted para o topo do módulo) — ineficiência
  pré-existente, não mexida.
- `_get_edp_lifetime()` lê o `edp_lifetime.json` do disco em toda chamada
  (sem cache em memória) — potencial custo de I/O repetido em `_new_entry()`,
  não mexido.
- Scoring com lógica visivelmente duplicada entre `EpisodicMemory.retrieve()`
  (cosine com todos os multiplicadores — epistemic/source_type/dominance/
  anchor/session/piso) e o caminho híbrido (`_retrieve_hybrid`) — candidato
  natural a dedup, mas é ciclo experimental próprio (ver "fora de escopo").
- Uso inconsistente de logger: alguns blocos `except` fazem
  `import logging as _lg; _lg.getLogger("edp.memory")` local em vez de usar
  o `logger` já vinculado no módulo — estilístico, não mexido.

## Fora de escopo (com motivo — já definido pelo adendo do pesquisador)

- **Reorganização da raiz `edp/` e scripts soltos**: churn alto, valor
  estético, decisão separada do pesquisador.
- **Remoção de `AdaptiveController`/`MetaReasoner`**: cadeia de morte já
  documentada, decisão separada.
- **Dedup de scoring** (episódico vs. híbrido, ver pendências acima): ciclo
  experimental próprio.

---

PARAR.
