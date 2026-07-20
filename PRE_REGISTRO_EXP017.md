# PRE_REGISTRO_EXP017.md — Dedup do retrieve (read-side)

Registrado ANTES de qualquer implementação. Baseline: main f8a3725
(pós-PR #15). Nada aqui altera produção; flag default OFF.
Decisões do pesquisador congeladas em 19/07/2026: corte H2 = 15pp
(efeito líquido sobre controle negativo); ≈ significa |dif| ≤ 5pp;
piso H3 = 10% (mantido após contagem real — ver H3); calibrador
Bayes-vs-Gauss ADIADO até pós-exp017 (registrado em DIVIDAS.md).
Controle negativo (shuffle) e piso-provisório incorporados por
revisão externa em 19/07 — pré-dado. Ponto de medição, seed por
query, caso MISTO, gate degenerado e controle-reserva declarados
na mesma revisão, antes do commit. Contagens de 19/07 (scripts
conta_catalogo.py / discrimina_par.py) incorporadas pré-registro:
10×"oi" write-side; f54471a1/31162822 presentes em AMBAS as camadas
com MESMO ID (fenômeno D descoberto).

## Motivação — medições observacionais acumuladas
1. retrieval_monitor: 80% dos turnos retornam memórias iguais
   (persistente desde jun/2026)
2. 4 cópias de Redis/Memcached num retrieve (arco do veneno)
3. 2 cópias do FN (arco do veneno)
4. 10 cópias de "Q: oi" no store cognitive (contagem por hash
   normalizado, 19/07 — o retrieve de 16/07 mostrou 5; o store tem
   o dobro: a janela top-k subamostra o write-side)
5. 2× session_summary "Nada. Esta é..." + 2× "Q: sim" num retrieve
   (smoke 18/07 manhã, kept 548/548 + 248/248)
6. Mesma dupla reproduzida em boot independente (18/07 tarde)
7. Scores empatados até a 6ª casa (0.015275==0.015275); spread do
   top-5 ~7% — escala RRF esmagada
8. DISCRIMINAÇÃO 19/07: os pares de #5/#6 são a MESMA entry (mesmo
   ID) presente em episódica E semântica — promoção-por-cópia
   (mecanismo pinado por test_carimbo_sobrevive_na_copia_semantica)
   + retrieve multi-camada = dupla aparição. Não é near-dup.

## Quatro fenômenos DISTINTOS (não conflar)
A. Duplicação no STORE (write-side): N registros de texto idêntico,
   IDs DISTINTOS, mesma camada (os 10 "oi")
B. Duplicação no RESULTADO (read-side): slots do top-k queimados com
   conteúdo idêntico num mesmo retrieve — sintoma; causas: A e/ou D
C. Repetitividade CROSS-TURN: queries diferentes → mesmo conjunto
D. Duplicação CROSS-CAMADA por promoção: 1 registro lógico, MESMO
   ID em episódica e semântica; retrieve multi-camada devolve ambos.
   Não é defeito de write (é a promoção funcionando); é defeito de
   READ não colapsar identidade.

exp017 INTERVÉM apenas em B (colapsando A-no-resultado e D).
A, C e D-na-origem são medidos, não tocados.

## Hipóteses e predições (registradas antes do dado)

H1 (intervenção): dedup em duas passadas zera dup_rate@10 nos
   espécimes de calibração (#4: 10×"oi"; #5/#6: pares mesmo-ID),
   sem regredir R1/R2/R3.

H2 (discriminador causal do 80%): julgado contra CONTROLE NEGATIVO.

   Controle (shuffle): flag EDP_RETRIEVE_SHUFFLE — o conjunto top-k
        do retrieve é ENTREGUE ao context_builder em ordem
        embaralhada; ZERO remoção. Seed determinística POR QUERY
        (seed_global fixa registrada em EXP017_FASE0.md, combinada
        com hash da query): reprodutível entre runs, permutações
        DISTINTAS entre queries — seed única global degeneraria no
        próprio fenômeno C (listas iguais → permutação igual).
        Mecanismo assumido (a confirmar na Fase 0): com scores
        quase empatados (item 7), etapa posterior sensível a ordem
        pode mudar QUAIS itens vencem só por reordenar a entrada.
        Se a seleção final for insensível à ordem, ver
        CONTROLE-RESERVA.

   PONTO DE MEDIÇÃO (fixo para OFF/ON/SHUFFLE): repeat_rate e
        dup_rate computados sobre retrieval_kept — o conjunto que
        sobrevive ao context_builder (já logado em ctx-DEBUG) — não
        sobre o top-k bruto. Medido no bruto,
        repeat(SHUFFLE)==repeat(OFF) por identidade de conjunto e o
        controle é tautológico por construção.

   CONTROLE-RESERVA (pré-registrado; ativação = decisão do
        pesquisador em EXP017_FASE0.md, antes da Fase 1): se a Fase
        0 mostrar repeat_SHUFFLE == repeat_OFF no kept (builder
        insensível a ordem), substituir por remoção aleatória
        pareada — remover d itens aleatórios do top-k (d = dup_rate
        log-only daquele retrieve) com refill do ranking; mesmo par
        mecânico do dedup, critério aleatório.

   Efeito líquido do dedup := repeat_SHUFFLE − repeat_ON

   H2-A (duplicatas causam): efeito líquido ≥ 15pp
   H2-B (escala RRF causa): efeito líquido < 15pp E
        repeat_SHUFFLE ≈ repeat_OFF (|dif| ≤ 5pp) → promove reajuste
        do ranking_score de cosmético a funcional
   MISTO (terceira saída): efeito líquido < 15pp E repeat_SHUFFLE
        nem ≈ OFF nem ≈ ON → reportar os três brutos, tratar como
        inconclusivo, decidir com o pesquisador — não classificar
        por eliminação.
   GATE DEGENERADO (avaliado na FASE 0, antes da Fase 1 — por
        construção nunca colide com H2-A): se o shuffle SOZINHO
        derrubar ≥15pp vs OFF, repeat_rate é artefato de
        estabilidade de ordenação; PARAR e redesenhar.

H3 (diagnóstico write-side): piso = 10% (MANTIDO: contagem dirigida
   real de 19/07 = 10/133 = 7.5% < 10%, regra do piso-provisório
   aplicada). Censo cego da Fase 0a cobre AS DUAS CAMADAS
   (episódica + semântica), com contagem SEPARADA por fenômeno:
   - H3 conta apenas fenômeno A (IDs distintos, texto idêntico,
     mesma camada) — cross-camada mesmo-ID (D) NÃO conta (é
     promoção funcionando, não desperdício de write)
   - D é reportado à parte: quantos IDs vivem em ambas as camadas
   PASSA se %A ≥ 10% em qualquer camada → write-side dedup entra na
   fila como decisão separada (produção — protocolo dry-run próprio).

## Desenho

FASE 0 — MEDIÇÃO (zero mudança de comportamento):
  a) Censo cego nas DUAS camadas do fase0: hash normalizado
     (strip+casefold+colapso de whitespace), clusters por camada
     (fenômeno A) + interseção de IDs entre camadas (fenômeno D)
  b) Instrumentar dup_rate@k no retrieval_kept (log-only, duas
     métricas: dup por ID e dup por hash); formalizar repeat_rate
     nas queries fixas da suite; medir repeat_SHUFFLE (seed por
     query, seed_global registrada)
  c) EXP017_FASE0.md com: censo A por camada, contagem D,
     repeat_OFF, repeat_SHUFFLE, decisão sobre controle-reserva,
     veredito do gate degenerado — TUDO antes da Fase 1

FASE 1 — INTERVENÇÃO:
  a) EDP_RETRIEVE_DEDUP (default OFF). DUAS PASSADAS ordenadas:
     1ª por ID (colapsa D — determinística, sem normalização),
     2ª por hash exato normalizado (colapsa A-no-resultado).
     Near-dup por embedding: só numa v2, novo pré-registro.
     EDP_RETRIEVE_DEDUP e EDP_RETRIEVE_SHUFFLE mutuamente
     exclusivas — SHUFFLE é instrumento da Fase 0, nunca produção.
  b) Mecânica: sobre a lista JÁ filtrada, colapsar (ID, depois
     hash) e puxar próximos do ranking até preencher k
  c) INVARIANTES DE SEGURANÇA:
     - Dedup roda DEPOIS de piso NOT_FOUND_FLOOR e exclusão do
       híbrido (choke-point store.py:9-21) — refill NUNCA
       reintroduz entry excluída por quarentena
     - Flag OFF = byte-idêntico (teste no padrão
       test_flag_off_byte_identical.py)
     - Scoring CONGELADO durante todo o exp017

FASE 2 — VEREDITO contra os critérios; promoção a default ON é
  decisão separada (etapa própria, como exp010/011).

## Critérios PASSA/FALHA (calibração: #4 e #5/#6)
PASSA H1 sse, com flag ON:
  - dup_rate@10 = 0 (por ID E por hash) nos retrieves de calibração
  - R1 CP3 presente=True | R2 Recall@5 ≥ 2/3 | R3 %SS vagas ≤ 20%
  - suite pytest verde, incluindo novo teste flag-off
H2: reportar SEMPRE os três brutos (OFF/ON/SHUFFLE); classificar
  pelos quatro casos acima — nenhum é falha do experimento.
H3: reportar %A por camada, clusters, e contagem D à parte;
  gatilho da fila conforme piso.

## Fora de escopo (explícito)
Unificação de scoring (congelada); dedup write-side (decisão
separada); dedup/retração NA ORIGEM do fenômeno D (mexe na
promoção = consolidação = produção, ciclo próprio); reajuste
ranking_score (H2-B pode promovê-la); benchmark_edp δ (obrigatório
antes de qualquer benchmark, não é parte deste); calibrador
Bayes-vs-Gauss (adiado); eco do session_summary promovido à
semântica (31162822 — o modo de falha exp009 sendo CANONIZADO pela
consolidação; registrado aqui como achado 19/07, ciclo próprio).