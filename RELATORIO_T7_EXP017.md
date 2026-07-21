# RELATORIO_T7_EXP017.md — Relato final da Fase 0 (T2-T6 implementados)

Branch `exp017/fase0-medicao`. Fecha o ciclo de implementação da Fase 0
(T1-T6 já commitados nesta branch). Este arquivo é o T7: achados do T1 com
file:line, fórmula fixada, pontos de inserção, contagem de testes novos, e
os comandos exatos da rodada Windows. **PARA AQUI** — as medições reais
(censo + repeat_rate) são do pesquisador; o store do fase0 não é visível
desta VM.

## Achados do T1 (RELATORIO_T1_EXP017.md) — resumo com file:line

- **Monitor histórico** (`edp/runtime/retrieval_monitor.py:93-120`): mede
  **top-k bruto**, não pós-builder — chamado de `edp/memory/store.py:1388`
  (cosine) e `:1553` (híbrido), ambos ANTES do `context_builder`. Janela =
  par consecutivo dentro do bucket do dia (`retrieval_monitor.py:38-91`),
  não N-turnos fixo.
- **Caminho de produção real**: `EDPRuntime._retrieve_context()`
  (`llm_adapter.py:1950`) → `_build_enriched_context()` (`:2452`) →
  `ContextWindowManager.build()` (`context_window_manager.py:259`).
  `edp/context_builder.py::build_context()` (MMR-like) **não** é usado no
  chat — só em `benchmark_edp.py`.
- **Ponto (i), SHUFFLE**: `llm_adapter.py:2334`
  (`results = self._memory.retrieve(query, top_k=5, min_score=0.20)`),
  antes do loop `:2365` que consome `results` em ordem.
- **Ponto (ii), retrieval_kept**: `ctx.retrieval`
  (`context_window_manager.py:134`, `BuiltContext.retrieval: List[str]`) —
  **sem ID** até o T4. O loop que preenche esse campo
  (`context_window_manager.py:320-327`) é guloso e sensível à ordem (para
  no primeiro item que estoura o budget) — mecanismo que H2 assume.

## Fórmula fixada (T1a → T5, ver EXP017_FASE0.md)

```
overlap = set(atual_ids) & set(anterior_ids)
binario = 1 se len(overlap) >= min(2, len(atual_ids)) senão 0     [T1a]
continuo = len(overlap) / len(atual_ids)                          [E2]
repeat_rate = média sobre pares CONSECUTIVOS da sequência executada
```
Aplicada em dois pontos por retrieve (E1): top-k bruto (comparável ao
histórico) e `retrieval_kept` (primário, congelado).

## Pontos de inserção implementados

| # | O quê | Onde |
|---|-------|------|
| T3 | `EDP_RETRIEVE_SHUFFLE` (default OFF) | `llm_adapter.py:2334-2354`; flags em `edp/config.py` |
| T4 | Propagação read-only `id(bloco)→entry_id` | `llm_adapter.py:2360-2401` (`_sim_id_map`), `:2445-2450` (`self._last_similarity_ids`) |
| T4 | dup_rate@k (log-only) + E3 + E4 | `llm_adapter.py:2715-2769`, após `ctx = mgr.build(...)` (`:2693`) |
| T4 (add.) | `self._last_kept_ids` / `_last_kept_hashes` | `llm_adapter.py:2731-2736`, expostos para o T5 sem parsear log |

## Testes novos (21, todos verdes — `python -m pytest -q`: 83 passed, era 62)

- `tests/test_flag_off_byte_identical.py` (+2): ordem do top-k preservada
  com flag OFF; flag OFF é o default.
- `tests/test_exp017_shuffle.py` (3): SHUFFLE reordena sem remover; é
  determinístico por query; não degenera no fenômeno C entre queries
  diferentes.
- `tests/test_exp017_dup_rate.py` (6): dup_rate=0 no conjunto único;
  detecção isolada de fenômeno D (por ID) e fenômeno A (por hash);
  diagnóstico de truncamento presente; instrumentação sobrevive a dados
  degenerados; `_last_kept_ids` exposto corretamente.
- `tests/test_exp017_medir_repeat_formulas.py` (10): fórmulas puras
  (overlap binário/contínuo, repeat_rate, matriz); lista de queries
  congelada bate com `REDIS_QUERIES`/`VAGUE_QUERIES` reais; ordem
  intercalada (E5) sem 3 blocos seguidos do mesmo pool.

## Comandos exatos da rodada Windows

Servidor parado, mesma disciplina de `suite_regressao_fase1.py` (cópia do
fase0, nunca produção):

```powershell
$env:EDP_BASE_DIR = "C:\edp_data_fase0"
$env:EDP_HYBRID_RETRIEVAL = "1"
$env:EDP_CTX_SLOTS = "1"

# 1) Censo cego (read-only absoluto, zero escrita no store)
python scripts\censo_exp017.py | Tee-Object -FilePath censo_exp017_saida.txt

# 2) Medição repeat_rate OFF vs SHUFFLE (usa snapshot/restore — não deixa
#    o store sujo ao final)
python scripts\medir_repeat_exp017.py | Tee-Object -FilePath medir_repeat_exp017_saida.txt

# 3) (opcional, mas recomendado) confirmar a suite de regressão intacta
#    nesta mesma cópia, antes de transcrever os números
python suite_regressao_fase1.py
```

Depois: transcrever as saídas de `censo_exp017_saida.txt` e
`medir_repeat_exp017_saida.txt` para os placeholders `[PREENCHER]` de
`EXP017_FASE0.md` — inclusive a validação de sanidade do censo (que
**precisa** dar OK) e os dois vereditos que ficam com o pesquisador (gate
degenerado, controle-reserva).
