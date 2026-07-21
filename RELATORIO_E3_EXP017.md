# RELATORIO_E3_EXP017.md — FIX do diagnóstico de truncamento (T1-T5)

Branch `exp017/fase0-medicao`. Contrato: `PRE_REGISTRO_EXP017.md`, com
ERRATA + E6. Gate: `pytest` verde (85 passed, 1 deselected). Nenhum código
de produção fora da instrumentação de medição foi alterado.

## O viés (E3, forma original)

`_build_enriched_context` (`edp/llm_adapter.py`, bloco exp017 T4) media
truncamento comparando duas populações incomensuráveis:

- `_n_kept` = blocos de `ctx.retrieval` resolvidos via `_sim_id_map`
  (`id(bloco) -> entry_id`) — só existe entrada quando `eid` é truthy.
- `_n_offered` = `len(_sim_blocks)` — **todo** bloco de similaridade
  coletado em `_retrieve_context`, com ou sem `eid`.

Um bloco sem `eid` nunca pode aparecer em `kept` (por construção do
mapa), mas sempre contava em `offered`. Resultado: `truncado=True` disparava
sempre que o retrieve trazia qualquer entrada sem id, mesmo com folga total
de budget — falso positivo, sem relação com o corte guloso de
`context_window_manager.py:320-327`.

## T1 — Fix

Comparação agora é `kept` vs `offered_mapeado = len(_sim_id_map)` —
populações comensuráveis, ambas passam pelo mesmo filtro de `eid`
truthy. `offered_total = len(_sim_blocks)` continua logado, mas só como
dado informativo (cobertura de `id()`, já existente como E4); não decide
mais `truncado`.

Log novo (`edp/llm_adapter.py`, mesmo ponto):
```
[exp017] truncamento kept=%d offered_mapeado=%d offered_total=%d truncado=%s
```

## T2 — Exposição em runtime

`self._last_trunc = {"kept": ..., "offered_mapeado": ..., "offered_total": ...}`,
mesmo espírito read-only do `_last_kept_ids` (T5 anterior) — populado no
mesmo `try` do T4, esvaziado (`{}`) no `except` de instrumentação
degenerada, para nunca vazar estado da rodada anterior em caso de falha.

## T3 / T3b — `scripts/medir_repeat_exp017.py`

- Cada query agora lê `rt._last_trunc` (sem parsear log) e acumula
  `trunc_evento` (1 se `kept < offered_mapeado`) e `trunc_gap`
  (`offered_mapeado - kept`). RESUMO por condição (OFF/SHUFFLE) reporta:
  `taxa` (fração de queries truncadas) e `gap_medio`.
- Nova flag `--ordem {intercalada,agrupada}` (default `intercalada` —
  comportamento idêntico ao T5 anterior). `agrupada` usa
  `QUERIES_AGRUPADA`, transcrição literal da lista do E6
  (`PRE_REGISTRO_EXP017.md` linhas 202-218): mesmas 14 queries do T5,
  reordenadas por pool (R2, R3, N), nenhuma adicionada/removida/reescrita.
  Verificado por assert de módulo: `sorted(QUERIES_AGRUPADA) ==
  sorted(QUERIES)`.

## T4 — Testes (`tests/test_exp017_dup_rate.py`)

Dois cenários novos, mais o teste existente atualizado para o novo formato
de log:

1. `test_truncamento_entry_sem_id_nao_e_falso_positivo` — 2 entries com
   id, 3 sem id (`None`/`""`), textos curtos (sem pressão de budget).
   Antes do fix: `offered=5, kept=2, truncado=True` (falso positivo).
   Depois: `kept=2 offered_mapeado=2 offered_total=5 truncado=False`.
   Reproduz exatamente o cenário que motivou o fix.
2. `test_truncamento_real_por_corte_de_budget_da_truncado_true` — 5
   entries, todas com id, textos de 40k chars cada (janela local
   default = 4096 tokens, `context_window_manager.py` `KNOWN_WINDOWS`).
   `offered_mapeado=offered_total=5`, `kept < offered_mapeado`,
   `truncado=True` — isola o corte real de budget do viés do E3.

`pytest tests/test_exp017_dup_rate.py`: 8 passed. Suíte completa: 85
passed, 1 deselected.

## Escopo não tocado

- `retrieval[:self.max_retrieval]` (slice por contagem em
  `context_window_manager.py:321`) não faz parte deste diagnóstico —
  E3 mede só o corte por budget dentro do slice já aplicado.
- Nenhuma query de T5/E6 foi adicionada, removida ou reescrita.
- Nenhuma decisão de H1/H2/H3 é alterada por este fix — ele corrige o
  instrumento de medição de truncamento, que é diagnóstico auxiliar, não
  entra nas fórmulas de `repeat_rate` pré-registradas.
