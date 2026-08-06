# NORTE.md — Contrato de foco (ler ANTES de qualquer tarefa)

**Válido até: 02/09/2026 (6 semanas).** Após essa data, este arquivo
está VENCIDO: não seguir cegamente — reavaliar com o dado das métricas
abaixo e reescrever. Meta vencida que ninguém reavaliou é dogma.

## A META (única, falsificável)
**R$ 3.000/mês recorrentes vindos de serviço de auditoria de
retrieval/RAG, no prazo de validade.**
Caminho: 1 cliente de ~R$ 3k ou 2 de ~R$ 1,5k por mês. Nada além disso
é meta — todo o resto é meio ou distração.

## MÉTRICAS DA SEMANA (dado real, sem narrativa)
- Abordagens enviadas a PMEs (alvo acumulado: 20 até o vencimento)
- Respostas recebidas / conversas de venda iniciadas
- Propostas enviadas / fechadas / R$ faturado
- CRITÉRIO DE FALSIFICAÇÃO (congelado 22/07): 20 abordagens e ZERO
  conversas iniciadas = canal PME-direto não abre hoje. Pivô
  pré-definido: mesma ferramenta vira portfólio para vaga remota de
  eval engineer. Nenhum trabalho se perde.

## O FUNIL (toda tarefa serve a um degrau, senão não é tarefa)
1. Script de auditoria (métricas do exp017 sobre export JSONL) →
   gera relatório com números reais. Mínimo viável, não ferramenta
   polida.
2. 5 abordagens/semana a PMEs com chatbot/assistente: achado concreto
   grátis + oferta do diagnóstico completo (R$ 2,5–4k).
3. Fechou → entregar com o rigor de sempre; relatório vira depoimento.
4. Mês 2–3, financiado pelo serviço: E7 + estudo de caso em inglês
   como motor de entrada contínuo.

## TESTE DE ESCOPO (para o agente E para nós, antes de todo prompt)
Pergunta única: **"isto aproxima o primeiro/próximo cliente pagante
dentro do prazo?"**
- SIM demonstrável → executa.
- NÃO ou "indiretamente/um dia" → recusar e apontar este arquivo.
  Ideia boa fora de escopo vai para FILA_FUTURO.md com uma linha —
  nunca vira prompt agora.

## FORA DE ESCOPO ATÉ A META (não é lixo, é fila)
- Fila técnica do EDP: pool_k/return_k, piso semântico, write-dedup,
  eco do summary, reescala RRF, benchmark
- Echo Chamber como produto (aposta DOIS, condicionada a caixa)
- "Memória de agentes" e qualquer competição com Mem0/Zep/Letta
  (refutado com dado em 22/07 — não reabrir sem dado novo)
- Refactors do EDP não exigidos por entrega a cliente
- Perfeccionismo no script além do que o relatório pago exige
- Arquitetura "plataforma enterprise" (K8s, OpenTelemetry, Protobuf,
  blue-green deploy), agente autônomo de experimentação sem gate
  humano, e qualquer forma de streaming/observação entre contas —
  ideias registradas em FILA_FUTURO.md (06/08), não descartadas, só
  fora do prazo. Nenhuma dessas precisa de "plataforma vendável" para
  o cliente de R$3k/mês: é acumular escopo antes de ter 1 cliente.
EXCEÇÕES PERMANENTES (não precisam passar no teste): correção de perda
de dados em produção; segurança; obrigação legal. Aplicam-se aos TRÊS
repositórios do ecossistema (`edp_v5_main`, `lab_edp_novo`,
`sf_exportador`), não só a este — este arquivo foi escrito quando só
`edp_v5_main` existia e nunca foi atualizado para os outros dois.
Exemplo concreto vivo (06/08): dado de conversa real commitado sem
`.gitignore` em repositório público (`sf_exportador`) é "perda de
dados"/"segurança" — cai na exceção sem discussão. Um bug de unidade
de timestamp que hoje não tem consumidor de aritmética ativo (só
ordenação, verificado em código) NÃO cai na exceção — é dívida
técnica normal, passa pelo teste de escopo como qualquer outra.

## PAPÉIS (inalterados)
Claude desenha e audita · Agente executa · Daniel valida, envia as
abordagens e FECHA — vender é tarefa indelegável do humano.

## POR QUE ISTO EXISTE (ler quando bater vontade de "só mais um exp")
O EDP provou o método (lab 9/10, 18 refutações). Provas adicionais têm
valor marginal ~zero sem leitor. A única variável em zero é comercial.
Cada semana de código-sem-cliente é uma semana a mais de obra — no
sentido literal. Registrado em 22/07/2026, com o assentimento dos dois.