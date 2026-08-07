# FILA_FUTURO.md — Ideias boas, fora de escopo até a meta do NORTE.md

Regra de entrada (ver NORTE.md): uma linha, sem desenvolver, sem virar
prompt. Reabrir só depois do vencimento do NORTE.md (02/09/2026) ou de
uma meta nova.

- **Plataforma "enterprise" para o ecossistema** (K8s, OpenTelemetry,
  Protobuf, blue-green deploy, OAuth2) — direção real, mas nenhum
  cliente de R$3k/mês exige isso; reavaliar só se um cliente pedir
  disponibilidade/observabilidade específica. (origem: 06/08)
- **Agente autônomo de experimentação no lab_edp_novo** (gera
  hipótese → pré-registra → executa → julga CONFIRMADA/REFUTADA sem
  humano no meio) — risco de integridade epistêmica se o julgamento
  final não tiver gate humano; se retomado, manter humano assinando
  cada veredito, nunca automatizar o passo de decisão. (origem: 06/08)
- **"Sala de espelhos" / streaming passivo entre contas Claude.ai** —
  capturar raciocínio de múltiplas contas via extensão e centralizar
  no EDP. Risco de política de uso não verificado (múltiplas contas
  gratuitas + agregação automatizada de conteúdo capturado é uma zona
  cinzenta real, não confirmada como segura só por "não envia prompt
  automaticamente"); exige revisão explícita dos termos de uso atuais
  do Claude.ai antes de qualquer código, não só leitura por analogia.
  (origem: 06/08)
- **Honeypot / cache de respostas (Degrau 1)** — **REFUTADO por dado**,
  não adiado por escopo. Medido 06/08 sobre 210 entries reais: 0/14
  acertos. O gate de similaridade bruta ≥0.70 disparou em 4 queries,
  todas as 4 anafóricas ("vamos continuar nossa conversa"), zero
  factuais — seletividade invertida: o cache dispararia só onde a
  resposta depende da sessão, ou seja, 100% confabulação. Reabrir só se
  alguém medir F1 (repetição real de perguntas, instrumento pronto em
  `scripts/medir_repeticao_honeypot.py`) E desenhar um gate que não
  selecione vagueza. Ver `docs/preregistro_degrau1_honeypot.md`.
  (origem: 06/08)
- **Corrigir o blob `Q+A` do `websocket.py:1200`** — achado colateral do
  Degrau 1: armazenar `Q: …\nA: …` junto destrói 47% da faixa dinâmica
  da similaridade (amplitude 0.5721 → 0.3009) e dilui seletivamente os
  sinais fortes (−0.2755 onde sim≥0.70 vs −0.0633 onde sim<0.70). Um
  match idêntico vira 0.6523. Afeta o retrieval inteiro, não só o cache.
  (origem: 06/08)
- **Wiki/"Palácio da Memória" (grafo de conhecimento navegável para o
  EDP, inspirado no método Karpathy/LLM-Wiki)** — ideia tecnicamente
  sólida; antes de construir do zero, avaliar reaproveitar
  `edp/co_occurrence.py` (já vivo, 9 consumidores) e reabrir
  `edp/memory_graph.py` (76 linhas, zero consumidores desde antes de
  julho — candidato natural em vez de módulo novo), e avaliar se o
  `graphify` (já usado neste próprio fluxo de trabalho, com exportação
  `--wiki` e servidor `--mcp` nativos) já cobre a maior parte do
  escopo antes de reimplementar. (origem: 06/08)
