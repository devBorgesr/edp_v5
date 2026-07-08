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
