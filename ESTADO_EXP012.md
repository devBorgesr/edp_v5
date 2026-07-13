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
