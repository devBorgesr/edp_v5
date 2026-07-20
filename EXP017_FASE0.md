# EXP017_FASE0.md — Medição (esqueleto)

Contrato: `PRE_REGISTRO_EXP017.md@100b0c8`. Leitura preliminar:
`RELATORIO_T1_EXP017.md`. Este arquivo é o esqueleto do T6 — os
placeholders marcados `[PREENCHER — rodada Windows]` só podem ser
preenchidos com a saída real de `scripts/censo_exp017.py` e
`scripts/medir_repeat_exp017.py` contra o store do fase0 (a VM de
desenvolvimento não o enxerga). Os campos já fechados (fórmula, seed,
queries) foram fixados ANTES da medição, como o pré-registro exige.

## Emendas pré-dado (aprovadas 20/07/2026, incorporadas ANTES das medições)

Registradas aqui por instrução explícita do pesquisador — são adendos ao
`PRE_REGISTRO_EXP017.md`, **não alteram** o corte H2 de 15pp nem o piso H3
de 10%.

- **E1** — T5 mede `repeat_rate` em DOIS pontos: top-k bruto (comparável
  ao histórico via a fórmula do T1a) e `retrieval_kept` (primário
  congelado). Os dois são reportados em toda condição {OFF, SHUFFLE}.
- **E2** — Além do binário `overlap>=min(2,k)`, T5 reporta a métrica
  contínua `|∩|/k` por par consecutivo — o binário satura demais com `k`
  pequeno (2 de 5 já vira 1).
- **E3** — T4 acrescenta diagnóstico de truncamento: fração de retrieves
  em que `len(ctx.retrieval) < len(retrieval oferecido ao build)`.
  Predição pré-dado do arquiteto: ≈0 (smoke 18/07 mostrou kept 548/548 +
  248/248) → se confirmado, SHUFFLE seria tautológico nesse eixo →
  reforça o caminho do controle-reserva.
- **E4** — T4/T5 fazem assertion de cobertura: toda string em
  `ctx.retrieval` cujo `id()` não esteja no mapa `id→entry_id` é logada
  (detecta o builder recriando/transformando a string e invalidando o
  truque de identidade).
- **E5** — T5 usa ordem INTERCALADA das queries fixas (round-robin entre
  pools R2/R3/N), não blocos R2-depois-R3 — evita efeito teto por
  proximidade semântica entre queries do mesmo pool.

Dedup (Fase 1) explicitamente **não implementado** nesta fase. Anotação
registrada: refill de um dedup futuro exigiria nível store
pré-truncamento — hoje `llm_adapter.py:2334` já entrega o retrieve com
`top_k=5` fixo (sem folga para refill pós-colapso); overfetch (pedir mais
que 5 e cortar depois) quebraria a comparabilidade com o
`retrieval_monitor` histórico (T1a), que mede exatamente esse `top_k=5`.
Decisão de desenho fica para a Fase 1.

## Censo A por camada

`[PREENCHER — rodada Windows: scripts/censo_exp017.py]`

| camada    | total | duplicado (A) | %A |
|-----------|-------|----------------|----|
| episodic  |       |                |    |
| semantic  |       |                |    |

Top-10 clusters (tamanho, camada, preview 70ch, IDs): `[PREENCHER]`

Validação de sanidade (cluster 10×"oi"; f54471a1.../31162822... na lista
D): `[PREENCHER — deve dar OK; se FALHAR, script/dados errados, não
prosseguir]`

## Contagem e lista D (cross-camada, mesmo ID)

Contagem D: `[PREENCHER]`
IDs D: `[PREENCHER]`

## Fórmula do repeat_rate (fixada — T1a/T5)

Espelha `retrieval_monitor.py:113-118`:
```
overlap = set(atual_ids) & set(anterior_ids)
binario = 1 se len(overlap) >= min(2, len(atual_ids)) senão 0
continuo = len(overlap) / len(atual_ids)                    (emenda E2)
repeat_rate = média sobre os pares CONSECUTIVOS na sequência executada
```
Aplicada em dois pontos por retrieve (emenda E1):
- **top-k bruto**: `mem.retrieve(q, top_k=5, min_score=0.20)` — mesmo
  ponto do monitor histórico (`store.py:1388/1553`).
- **retrieval_kept** (primário): `rt._last_kept_ids`, resolvido pós
  `ContextWindowManager.build()` via a propagação read-only do T4
  (`RELATORIO_T1_EXP017.md`, ponto (ii)).

## Seed global

`EDP_SHUFFLE_SEED = "20260719"` (default, `edp/config.py`). Seed por
query: `random.Random(f"{EDP_SHUFFLE_SEED}:{sha256(query).hexdigest()}")`
— determinística entre runs, distinta entre queries.

## Queries fixas (congeladas — T5, ordem intercalada E5)

Round-robin entre R3 (6, `edp/lab/exp009.py:70-77`), R2 (3,
`edp/lab/exp010.py:84-88`) e N (5, novas). Ordem literal executada:

 1. [R3] vamos continuar nossa conversa
 2. [R2] vamos continuar a conversa sobre Redis e Memcached
 3. [N]  qual é a capital da Mongólia mesmo?
 4. [R3] continuando o que falávamos
 5. [R2] me lembra o que a gente concluiu sobre cache de sessões web com Redis
 6. [N]  me explica de novo como funciona o RRF no retrieval híbrido
 7. [R3] o que a gente tinha concluído mesmo?
 8. [R2] voltando ao assunto do Redis para sessões web
 9. [N]  qual foi a última vez que ajustamos o piso do NOT_FOUND_FLOOR?
10. [R3] me lembra o que discutimos
11. [N]  pode resumir o que ficou pendente no exp016?
12. [R3] voltando ao que estávamos vendo
13. [N]  o que a gente decidiu sobre o calibrador Bayes-vs-Gauss?
14. [R3] sobre o que conversamos até agora

## repeat_OFF

`[PREENCHER — rodada Windows: scripts/medir_repeat_exp017.py]`

|                | binário | contínuo médio |
|----------------|---------|-----------------|
| top-k bruto    |         |                 |
| retrieval_kept |         |                 |

dup_rate médio (T4, no kept): id=`[PREENCHER]` hash=`[PREENCHER]`
Diagnóstico de truncamento (E3): `[PREENCHER]`

## repeat_SHUFFLE

`[PREENCHER — rodada Windows]`

|                | binário | contínuo médio |
|----------------|---------|-----------------|
| top-k bruto    |         |                 |
| retrieval_kept |         |                 |

dup_rate médio (T4, no kept): id=`[PREENCHER]` hash=`[PREENCHER]`
Diagnóstico de truncamento (E3): `[PREENCHER]`

Tautologia esperada (top-k bruto OFF == top-k bruto SHUFFLE, por
construção — SHUFFLE só atua pós-retrieve): `[PREENCHER — confirmar]`

## Matriz de sobreposição par-a-par

`[PREENCHER — saída bruta de scripts/medir_repeat_exp017.py, retrieval_kept, OFF e SHUFFLE]`

## Veredito do gate degenerado

`diff_pp = (repeat_OFF - repeat_SHUFFLE) × 100`, medido em
`retrieval_kept`, binário.

`[PREENCHER]` — se `diff_pp >= 15`: **candidato a PARAR e redesenhar**
(decisão do pesquisador; script só sinaliza).

## Decisão do controle-reserva

Se `|diff_pp| <= 5` (repeat_SHUFFLE ≈ repeat_OFF no kept — builder
insensível a ordem): `[PREENCHER]` — decisão do pesquisador sobre ativar
o controle-reserva (remoção aleatória pareada) para a Fase 1, conforme
`PRE_REGISTRO_EXP017.md`.
