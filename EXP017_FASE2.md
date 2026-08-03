# EXP017_FASE2.md — Veredito (Fase 2 FECHADA)

Contrato: `PRE_REGISTRO_EXP017.md` (com ERRATA ERR-1/2/3 + E6) +
`EXP017_FASE0.md` + `RELATORIO_F1T1_EXP017.md` (com ERR-T1-1) +
`RELATORIO_F1_EXP017.md`. Branch: `exp017/fase1-dedup`.
Medições: 22/07/2026, store `C:\edp_data_fase0`. Decisões do pesquisador
nesta data: aceitar o pacote com rótulo corrigido (§3) e condicionar a
promoção ao isolamento pool_k/return_k (§4).

## 1. VEREDITO H1: PASSA — critério conjuntivo integral

Critério congelado (19/07): dup_rate@10 = 0 (por ID E por hash) nos
retrieves de calibração com ON, E R1 CP3 presente, E R2 Recall@5 ≥ 2/3,
E R3 %SS ≤ 20%, E suite pytest verde.
(O §6 do RELATORIO_F1_EXP017.md escreveu "R2 OU R3" — errata registrada
aqui: o pré-registro diz E, conjuntivo, e foi assim que se julgou.)

Calibração dup_rate@10 (C1-C5, top_k=10 direto no store):

| Query | OFF id | OFF hash | ON id | ON hash |
|---|---|---|---|---|
| C1 "oi" | 0/10 | **9/10** | 0 | 0 |
| C2 "me lembra o que discutimos" | 2/10 | 2/10 | 0 | 0 |
| C3 "…piso do NOT_FOUND_FLOOR?" | 3/10 | 3/10 | 0 | 0 |
| C4 "voltando ao que estávamos vendo" | 3/10 | 3/10 | 0 | 0 |
| C5 "…pendente no exp016?" | 2/10 | 2/10 | 0 | 0 |

Sanidade OFF: dup>0 nas cinco — nenhum espécime evaporou. C1 é o retrato
do problema: o top-10 INTEIRO era um único texto ("oi") em dez slots.
Nota: dup_id=0 no C1 refuta a predição do arquiteto de que C1 exercitaria
as duas passadas — com 15 docs empatados, o desempate por ordem de
indexação deixa os pares D fora do top-10; a passada por hash resolveu
sozinha. C2-C5: dup_id ≡ dup_hash (fenômeno D puro), consistente com a
Fase 0.

Suite de regressão com EDP_RETRIEVE_DEDUP=1 (+ HYBRID=1, CTX_SLOTS=1):
R1 CP3 presente=True — CP1 byte-idêntico à baseline até a 6ª casa
(0.016393/0.015417/0.015385/0.015275/0.015275, mesmos 5 IDs).
R2 Recall@5 = 2/3 = 67% — o risco do refill (ERR-T1-1) não se
materializou. R3 = 3/30 = 10,0% (baseline 16,7%). restore==True nos três.
pytest: 112 passed, 1 deselected.

Repetição da Fase 0 com flags OFF (controle de regressão): intercalada e
agrupada reproduziram 15,4%/14,5% e dup 12,4% exatos — código novo com
flag desligada é byte-idêntico também em medição real.

## 2. 18ª REFUTAÇÃO — monotonicidade do RRF sob pool dependente de k

Alegação do T1 (RELATORIO_F1T1_EXP017.md, item b, "mitigação
verificada"): aumentar top_k em HybridRetriever.search() "nunca reordena
os que já estariam no top-k menor". FALSA. Contraexemplo:

  doc A: rank_vec=2, rank_bm25=16 | doc B: rank_vec=4, rank_bm25=14
  pool=15: A = 1/62 = 0,0161 (bm25 fora do pool); B = 1/64 + 1/74 = 0,0291 → B > A
  pool completo: A = 1/62 + 1/76 = 0,0293 → A > B

O rank de cada doc dentro das listas não muda; o score RRF muda, porque
docs além da fronteira do pool passam a contribuir 1/(60+rank). Na escala
esmagada do store (top-5 em 0,0153–0,0164), contribuições de fronteira
(~0,013) reordenam. Evidência observacional consistente: a query de
termos raros do CP1 (0.016393 = 1/61 exato, contribuição única, rankings
curtos) ficou idêntica; a query genérica do R3 ("voltando ao que
estávamos vendo", rankings densos) perdeu o summary do top-5 — 2/5 → 0/5,
onde dedup puro previa 1/5.

Refutação dupla, registrada com os dois nomes: a matemática era do agente
(T1); o arquiteto validou por escrito ("o trecho matemático confere") na
revisão do relatório. Lição: monotonicidade de fusão por rank precisa de
prova sob TODAS as dependências do parâmetro — o pool era função de
top_k, e ninguém derivou a consequência.

## 3. RÓTULO CORRIGIDO da intervenção

EDP_RETRIEVE_DEDUP=1 NÃO implementa "dedup puro". Implementa
**"dedup em duas passadas + fusão RRF sobre pool completo"** — duas
variáveis num único toggle. O invariante "scoring congelado" foi violado
no espírito (fórmula intacta, input efetivo da fusão alterado). Efeito
observável: parte da melhora do R3 (16,7% → 10,0%) vem do pool, não do
dedup. EDP_RETRIEVE_RANDOM_DROP carrega o mesmo confound (usa o mesmo
overfetch).

H1 PASSA sob este rótulo: o critério mede o resultado entregue
(dup_rate@10=0 + regressões verdes), e o resultado entregue passa. O que
o rótulo corrige é o QUE está sendo promovido, não SE funciona.

## 4. CONDIÇÃO DE PROMOÇÃO (decisão do pesquisador, 22/07)

Merge desta branch: AGORA, como está — flag default OFF, produção
intocada, H1 registrado com o rótulo do §3.

Promoção a default ON: CONDICIONADA a micro-ciclo de isolamento —
separar `pool_k` (congelado em min(top_k_original*3, len), o input de
fusão de hoje) de `return_k` em HybridRetriever.search(); o refill passa
a consumir mais itens DO MESMO ranking fundido, sem re-fundir sobre pool
maior. Critério do micro-ciclo: com pool_k congelado + dedup ON,
(a) calibração C1-C5 mantém dup_rate@10=0; (b) CP1 permanece
byte-idêntico; (c) R3 re-medido — a fração da melhora atribuível ao
dedup puro fica isolada da fração do pool. Se o pool completo se provar
fusão superior, promove-se SEPARADAMENTE, com pré-registro próprio.

Rodapé herdado (RELATORIO_F1_EXP017.md §5): no dia em que a flag ligar,
record_turn passa a gravar a série pós-dedup — a série histórica do "80%"
muda de definição; comparações antes/depois exigem esta ressalva.

## 5. RESERVA — dado para o dossiê E7/H2-C

| Condição | binário (kept) | contínuo (kept) | dup |
|---|---|---|---|
| OFF | 15,4% | 14,5% | 12,4% |
| SHUFFLE | 15,4% | 14,5% | 12,4% |
| RESERVA | 15,4% | **19,5%** | 6,1% |

Remoção aleatória pareada corta o dup pela metade (esperado) mas AUMENTA
a sobreposição cross-turn: o refill puxa itens mais fundos do ranking,
que se repetem mais entre queries. Registro-chave para o E7: separa
efeito-do-refill de efeito-do-critério — qualquer comparação futura de
dedup ON no eixo cross-turn precisa descontar isso.

## 6. H2 e H3 — sem alteração

H2: INCONCLUSIVO-POR-DESENHO (fechado na Fase 0, E6; esta rodada só
alimenta o dossiê). H3: PASSA (Fase 0; gatilho do write-side dedup
disparado, fila).

## 7. Fila pós-exp017 (ordem de prioridade)

1. Micro-ciclo pool_k/return_k (condição de promoção, §4)
2. Piso da SemanticMemory — PRIORIDADE ELEVADA: furo documentado
   (semantic.py:99-150 não lê answer_class; cópia semântica de toxic
   escapa do piso no caminho cosine — RELATORIO_F1T1_EXP017.md item c)
3. Write-side dedup (gatilho H3; produção, protocolo dry-run próprio)
4. E7: repeat_rate sobre sequência real de turnos (H2-C)
5. Ciclo do eco session_summary (canonização provada na Fase 0)
6. Fix benchmark_edp.py MEMORY_DIR (obrigatório antes de benchmark)
7. Reajuste ranking_score RRF; NEG v2; calibrador Bayes-vs-Gauss;
   pergunta arquitetural da camada semântica
   