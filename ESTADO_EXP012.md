# ESTADO exp012 — regra v1 REFUTADA na calibração; mecanismo preservado

**Data:** 2026-07-08 · **Regra: NÃO CONGELADA.** Sem novo diff neste registro.

## Resultado da calibração (rodada do pesquisador, C:\edp_data_hybrid_test)
Regra v1 (`n_mem_prompt==0 AND recusa-alta`): **0/6 lixos pegas** (e 0
falso-positivo). `recusa_alta=False` em TODAS as 14 entradas (6 lixos + 8
legítimos). Pelo critério §2.3 (100% ou reavaliar, nunca afrouxar): **REFUTADA**.

## Causa (confirmada na fonte)
`detectar_auto_sinal_de_limite` (`echo_chamber.py:158`) mira **ADMISSÃO DE
LIMITE DE CONHECIMENTO** ("não tenho base sólida para afirmar") — não **NEGAÇÃO
DE RECUPERAÇÃO** ("não encontro registro sobre X"). O sinal está correto para o
que foi construído (Dívida #49, read-path); é o **sinal errado** para o exp012.
Segunda lição da fase na mesma direção: reusar infra só depois de conferir que
ela mede o MESMO fenômeno.

## O que fica de pé (nesta branch, tudo default OFF, byte-idêntico)
- **(b)-lite**: `_build_enriched_context` publica `runtime._last_ctx_provenance`
  {n_mem_prompt, retrieval_tokens} — exato por id() com EDP_CTX_SLOTS=1.
- **Camada A (carimbo)**: `ctx_provenance` persistido no entry — correto e útil
  independente da política.
- **Camada B (política)**: gate defensivo + peso-piso (cosine ×0.05, fora do
  índice híbrido, nunca deleta) — mecanismo pronto, aguardando regra válida.
- `exp012_calibracao.py`: o instrumento que refutou a v1; reusável para a v2.

## Questão aberta (próximo ciclo — NÃO agora)
Desenhar o **segundo sinal determinístico** de "negação de recuperação":
- **NÃO textual** (Fase 0: 3/6 escaparam do padrão de texto; esta calibração:
  0/6 no sinal textual existente).
- **Candidato a investigar:** marcar `answer_class` **no fluxo de geração**, no
  momento em que a resposta NASCE como negação de contexto (o gerador sabe o que
  está fazendo; o classificador retroativo não). Etapa 0 futura: localizar na
  fonte o ponto do fluxo onde essa autodeclaração é determinística.

## Integridade
Sem congelamento, sem PR, sem merge. Produção intocada. Calibração foi
somente-leitura sobre cópia.

## Fase 3 (rebase pós-promoção) — 12/07/2026
Regra R4 (negacao_textual OR kw_continuidade) CONGELADA em 2 estratos (matriz
fase 2: P=0.90 R=0.69 F1=0.78, zero FP em LEGITIMO_META; estrato B usa
n_mem_prompt, resolvendo o quadrante inseparável da PR-5). Achado: branch
nasceu com EDP_CTX_SLOTS="0" (pré-PR#4) — nesse regime n_mem_prompt mente
(Defeito 1), colapsando a Camada B em silêncio. Rebase corrige a causa
(defaults promovidos: HYBRID=1, CTX_SLOTS=1); guarda em `classify()` corrige
a classe do erro (descarta n_mem_prompt se CTX_SLOTS OFF). Pendente: rodada
Daniel/Windows contra stores + `push --force-with-lease` (histórico reescrito).

## Teste vivo pós-Fase-3 — RAMO A confirmado (12/07/2026, hybrid_test)
Discriminante do Daniel (`GET /memory/list`): `ctx_provenance` PRESENTE nas 2
entradas novas (Redis n_mem_prompt=2/540tok, homomórfica n_mem_prompt=3/734tok;
ambas sem `answer_class`). **Mecanismo Camada A/B funciona ponta a ponta** — o
caso observado é semântico, não bug de gravação.

**PR-6 CONFIRMADA ao vivo, com agravante:** estrato B não quarentena negação
nova quando o slot de `n_mem_prompt` está ocupado por memórias IRRELEVANTES
(n_mem>0, sem checar relevância). No caso Redis, as 2 memórias contadas pelo
carimbo eram as 2 CÓPIAS da negação antiga (`757b3aa2`, acessos=2, último
acesso no turno de hoje) — **n_mem_prompt mede quantidade, não qualidade;
proveniência de lixo conta como proveniência**. Falso negativo conhecido e
agora observado in vivo.

**Vazamento textual confirmado:** respostas novas usaram "não chegou no
contexto" / "não consigo recuperar" — fora da regex `NEG` congelada. Só
`kw_continuidade` pegou. Consistente com a matriz (R1 `negacao_textual`
recall=0.46).

**Backlog nu confirmado in loco:** `757b3aa2`, `3d34504c`, `b9cfb9c5`
aparecem no store sem `answer_class`, competindo normalmente no retrieve — é
esse backlog (não o write-path novo) que produz o sintoma observado hoje.

**Refinamento CANDIDATO para exp012-v3 (NÃO implementado, registrado só como
hipótese futura):** estrato B assimétrico — `negacao_textual` na resposta ⇒
quarentena SEMPRE (independe de `n_mem_prompt`); `kw_continuidade` sem
negação ⇒ continua exigindo `n_mem_prompt==0`. Racional: negação textual é
evidência direta de que a recuperação falhou, mesmo com o slot ocupado por
lixo — `kw_continuidade` sozinho não é (é só um pedido de continuidade).

**Achado para exp012-v3/Fase 4:** os itens do backlog já têm
`cognitive_decisions.key_assertion` extraído e semanticamente correto (ex.:
"Contexto anterior sobre Redis/Memcached não está disponível"). Candidato a
3º sinal de custo zero (já materializado) — o dry-run da Fase 4 coleta esse
campo para análise futura; **não participa da decisão desta fase**.

**Restrição de ambiente (registrar, não consertar):** `pressure=CRITICAL`
constante (0.3–0.9GB) manteve o `background_loop` pulando TODOS os ticks
nesta sessão — `cognitive_decisions_extractor` nunca rodou. Qualquer
classificador semântico futuro que dependa desse job precisa de RAM que esta
máquina não tem hoje.

## Fase 4 (backfill) — desenhada, dry-run implementado, passada real PENDENTE
`exp012_fase4_backfill_dryrun.py`: aplica `classify()` no estrato A (R4 puro)
sobre as entries episódicas/semânticas existentes do store-alvo; SÓ LISTA
(id, query, features, `key_assertion` coletado) o que carimbaria como
`not_found`; nunca grava. Validado contra `gt_rotulacao.csv` (proxy do
mesmo store, N=97 pós-dedup): `carimbaria=20` = 18 verdadeiros positivos + os
mesmos 2 FP de continuação já conhecidos da matriz (`728c1579`, `eceb81dc`);
FN=8 idêntico à matriz fase 2; **ZERO vazamento nos 10 LEGITIMO_META** —
lógica do dry-run byte-a-byte consistente com R4 congelada. Passada REAL
(gravar `answer_class` de fato) fica para depois, só com OK explícito do
pesquisador, script separado, e só sobre cópias — produção jamais.

## FASE 5: fechamento do arco (15-16/07/2026)

Arco exp012→exp016 fechado. Placar final registrado aqui para referência —
próximos experimentos partem deste estado, não do zero.

**3 classes de veneno cobertas pelo mesmo mecanismo** (`answer_class` →
peso-piso + exclusão do índice híbrido, `TOXIC_ANSWER_CLASSES` desde o
exp016):
- **NEG/CONFAB** (negação textual R1 OR continuidade R4) — regra R4
  CONGELADA na Fase 3: matriz fase 2, N=97 pós-dedup, P=0.90 R=0.69 F1=0.78,
  zero FP em LEGITIMO_META. Gatilho → `answer_class="not_found"`, estratos
  A/B por `n_mem_prompt`.
- **DISQ** (desqualificação auto-referente, exp016) — regra DISQ-v1
  CONGELADA na Etapa 0: dry-run 239 entradas, 2 candidatas, **zero falsos
  positivos**, predições pré-registradas 100% confirmadas. Gatilho →
  `answer_class="disqualification"`, **incondicional** (sem estrato, decisão
  do pesquisador — ataca conteúdo presente, não ausência de recuperação).

**23 entradas carimbadas** no total ao longo do arco (backfill exp012 Fase 4
+ backfill exp016).

**exp015 REFUTADO (14/07)** — registrado para não repetir a tentativa:
cabeçalho de proveniência física + proibição explícita no system prompt NÃO
impediram o modelo de reafirmar uma desqualificação presente na janela
imediata. O veneno sequestra o próprio frame da honestidade epistêmica —
não se vence por prompt, se remove do contexto. Motivou o desenho do exp016.

**11 hipóteses de Claude refutadas** ao longo do arco (classify v1, PR-2,
polaridade textual isolada nos quadrantes ambíguos, e outras — refutação é
sinal de disciplina experimental, não de falha do arco).

**Ciclo de 4 gerações quebrado in vivo (15/07):** rodada de fechamento sobre
store contaminado, pós-backfill exp016 — o ciclo negação → corroboração →
desqualificação → recusa, observado no exp015, **não se reproduziu**.
`f623b2ac` foi excluída do retrieval por similaridade apesar de conter a
query literal no texto (evidência direta da exclusão do híbrido em ação);
zero desqualificação na resposta.

**Dívidas registradas, NÃO resolvidas neste arco** (candidatas a próximos
ciclos, cada uma como decisão separada do pesquisador):
- **NEG v2**: família "não consigo recuperar o conteúdo completo" fora da
  regex `NEG` atual — vazamento textual já confirmado in vivo (Teste vivo
  pós-Fase-3 acima) e reobservado como resíduo na rodada de fechamento do
  exp016 (2 cópias de um FN de 1ª classe nascido 00:47 de 13/07, na janela
  entre a seleção do dry-run e o apply do exp012).
- **Dedup do retrieve**: 3ª medição pendente (2 já feitas ao longo do arco).
- **`SemanticMemory` sem peso-piso**: `SemanticMemory.retrieve()` não lê
  `answer_class` — o piso isolado (`EpisodicMemory.retrieve()`) só cobre
  episodic; a exclusão do índice híbrido cobre as duas camadas, mas se
  `EDP_HYBRID_RETRIEVAL` for desligado essa proteção some para semantic.
  Fase 5/BRANCH 1 (fix/consolidation-toxicity-guard) reduz a EXPOSIÇÃO
  (bloqueia a promoção de conteúdo tóxico para semantic) mas não resolve a
  dívida em si — uma entry semântica ORIGINALMENTE marcada tóxica (não
  promovida, gravada direto) continua sem piso no cosine puro.
- **`anchor_boost` polaridade**: registrado como pendência de revisão, não
  investigado neste arco.
- **`session_summary` fora do parser**: entries desse `source_type` não são
  cobertas pelo parser Q/A (`QA` regex) usado pelos dry-runs/backfills —
  fora do escopo de detecção de veneno atual.
- **Backfill de produção**: toda a auditoria acima rodou contra cópias
  (`C:\edp_data_hybrid_test`, `C:\edp_data_exp016`). Rodar o backfill contra
  o store de produção real é decisão separada, não tomada neste arco.
