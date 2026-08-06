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
- **Wiki/"Palácio da Memória" (grafo de conhecimento navegável para o
  EDP, inspirado no método Karpathy/LLM-Wiki)** — ideia tecnicamente
  sólida; antes de construir do zero, avaliar reaproveitar
  `edp/co_occurrence.py` (já vivo, 9 consumidores) e reabrir
  `edp/memory_graph.py` (76 linhas, zero consumidores desde antes de
  julho — candidato natural em vez de módulo novo), e avaliar se o
  `graphify` (já usado neste próprio fluxo de trabalho, com exportação
  `--wiki` e servidor `--mcp` nativos) já cobre a maior parte do
  escopo antes de reimplementar. (origem: 06/08)
