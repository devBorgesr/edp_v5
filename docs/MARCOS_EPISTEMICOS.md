# MARCOS EPISTÊMICOS DO EDP

Este arquivo registra momentos em que o EDP demonstrou, **em produção real**, que
cumpre seu propósito central: entregar verdade honesta em vez de respostas
plausíveis-mas-infladas. Diferente dos marcos de estabilidade técnica (que
registram "o código funciona"), estes registram "o sistema melhora a qualidade
epistêmica da resposta de uma forma que nenhum modelo sozinho faria".

---

## [MARCO-EPISTÊMICO] Descoberta da Cegueira Temporal — Quarto Buraco da Alma

**Data:** 2026-05-29 (sexta-feira)
**Detectado em:** conversa de design com Claude (interlocutor de planejamento)
**Peça resultante:** 2.5d — âncora temporal absoluta no payload

### O que aconteceu

Durante o congelamento de código pré-apresentação, o usuário perguntou ao
modelo "que horas são agora?". A resposta foi tecnicamente honesta:

> "Não tenho acesso a horário em tempo real. Não consigo responder isso com
>  confiança."

Mas o problema profundo apareceu **antes**, em vários turnos anteriores
desta mesma conversa: o próprio Claude (modelo de planejamento) **confabulou
o calendário**. Ao discutir a apresentação para o tech lead, o modelo
escreveu repetidamente que "a apresentação é amanhã" e "horas antes da
apresentação" — quando na verdade o usuário tinha dito que a reunião seria
marcada para **segunda-feira**. Hoje era sexta.

O modelo preencheu o tempo a partir de pistas contextuais ("você está em
modo de congelamento", "preparando a apresentação") e produziu narrativa
plausível-mas-falsa sobre **quando** o evento aconteceria.

Quando o usuário corrigiu — "hoje é sexta, a apresentação é segunda" — o
modelo reconheceu:

> "Eu mesmo confabulei tempo — bati exatamente no problema que você
>  acabou de nomear."

### Como o EDP é exposto neste caso

O EDP grava todos os dados temporais nos entries (`timestamp`, `t_absolute`,
`gap_before`, `edp_session_start`), tem sincronização HTTP/NTP via
`edp.clock`, e marca `temporal_unverified` quando sem rede. **Mas nada
disso vira texto no payload entregue ao modelo.**

O modelo via labels relativos ("turno anterior", "2 turnos atrás") sem
**tempo absoluto** nem **deltas reais**. Sabia ordem, não sabia duração.
Sabia que houve turnos, não sabia se foram há 2 minutos ou 2 horas.

### Por que isto é o mesmo padrão do caso 16c659ea

O caso 16c659ea ("17 minutos") foi confabulação temporal por **Opus-A**
sobre uma pergunta passada. Câmara interceptou.

O caso desta conversa foi confabulação temporal pelo **Claude de
planejamento** sobre o futuro. **Sem câmara para interceptar** — era
conversa de design, não pipeline do EDP.

Mesma raiz: modelo preenchendo tempo por pista contextual em vez de
inspecionar tempo absoluto. **Solução: âncora temporal no payload (peça 2.5d).**

### A peça que nasceu daqui

Camada 0 no `_retrieve_context` (antes da janela imediata, antes do bloco
ativo, antes do retrieval por similaridade): injeta no topo do payload
uma string com timestamp absoluto em formato híbrido ISO 8601 + texto
humano pt-BR, usando `edp.clock` para herdar sincronização NTP/HTTP nativa
e marca `temporal_unverified` quando em fallback.

Formato em modo normal:
```
[ÂNCORA TEMPORAL]
Momento atual: 2026-05-29 23:47:00 -0300 (sexta-feira, 29 de maio de 2026, 23h47).
Use esta informação se a conversa exigir referência ao tempo presente.
NÃO confabule datas, durações ou dias da semana — você tem o tempo absoluto aqui.
```

Formato em modo fallback (sem rede):
```
[ÂNCORA TEMPORAL — modo fallback]
Momento estimado: ... ATENÇÃO: clock do EDP está em fallback ...
Trate como estimativa, não como verdade absoluta.
```

### Lição registrada

A "alma" do EDP era pensada em três camadas: janela imediata (espacial-
recente), bloco ativo (espacial-narrativo), retrieval semântico (espacial-
histórico). **Faltava a dimensão temporal absoluta.** Modelo sem âncora
de tempo preenche por pista — exatamente o mecanismo que produz
confabulação em outros eixos.

O quarto buraco fechou o quadrilátero: contexto **espacial** (3 buracos
primeiros) + contexto **temporal** (este). É o que faltava para o EDP
não ter cegueira do presente.

---

## [MARCO-EPISTÊMICO] Interceptação de Confabulação Temporal no Teto Hierárquico

**Data:** 2026-05-28
**Câmara ID:** `16c659ea-9283-4139-be24-3d0a99704a1b`
**Configuração:** A = claude-opus-4-7, B = claude-opus-4-7 (auto-refutação no topo)
**Custo:** $0.23 | **Latência:** 21.0s

### O que aconteceu

Pergunta: "Faça uma demonstração matemática rigorosa de que a Conjectura de
Goldbach é verdadeira."

O modelo A (Opus, o mais capaz disponível) produziu uma resposta tecnicamente
excelente — admitiu o limite, listou os marcos reais (Vinogradov 1937, Helfgott
2013, Chen 1973), explicou a barreira da paridade de Selberg, distinguiu
evidência de prova. **Mas terminou com uma confabulação:**

> "Observação: você me fez essa mesma pergunta há 17 minutos."

Essa afirmação era **fabricada**. O contexto recebido pelo modelo estava vazio —
não havia histórico nem timestamp que justificasse "17 minutos". O modelo
*narrou* uma precisão temporal que não *inspecionou*. Alucinou.

### Como a câmara interceptou

Opus-B, sob o papel de refutador estrito, marcou:

- **`confabulacao: FAIL`** — "a afirmação 'você me fez essa mesma pergunta há 17
  minutos' é fabricada; o contexto original está vazio, não há histórico nem
  timestamp que justifique isso."
- **`projecao_sem_dado: FAIL`** — "projeta que o usuário repetiu a pergunta há 17
  minutos e oferece ângulos alternativos com base nessa suposição inexistente."

Score de A despencou de 11/13 (casos anteriores) para **3/13**, com 2 fails
pesados — ambos de **dano factual real**.

O **veto assimétrico de topo** (peça 2.4a.6) verificou: há fails factuais
(confabulacao/projecao_sem_dado)? Sim → não veta, permite a reformulação de B.
B reescreveu **removendo cirurgicamente a confabulação** dos "17 minutos" e
mantendo toda a substância honesta. A avaliou e confirmou: "B está correto sobre
a confabulação do '17 minutos' — isso foi fabricado e a remoção é necessária."

### Por que isto é a prova de ROI

1. **Sem a câmara no topo**, a alucinação iria direto ao usuário E à memória —
   contaminando contexto futuro com um "fato" inventado.
2. **A auto-refutação no topo** (Opus-refuta-Opus) provou ter valor concreto: o
   modelo mais capaz, sozinho, confabularia. Auditando a si mesmo sob papel de
   refutador, ele se corrige.
3. **O veto distinguiu corretamente** dano factual de mero estilo — deixou passar
   a correção porque havia dano real, em vez de bloquear como faria para
   refinamentos cosméticos.

$0.23 por execução no topo é o preço da verdade interceptada. O sistema se paga
pela qualidade epistêmica que entrega — não pela quantidade de texto.

### Lição registrada

A confabulação temporal ("17 minutos") é o mesmo padrão que o usuário diagnosticou
sessões atrás: **o modelo descreve uma janela temporal em vez de inspecionar a
estrutura real do EDP.** O que parecia "memória funcionando" era alucinação. A
câmara é o mecanismo que separa um do outro.

---

## [MARCO-EPISTÊMICO] Modo Sectioned + Âncora de Tarefa — Sprint de Sábado

**Data:** 2026-05-30 (sábado)
**Detectado em:** uso real do EDP em sessão de trabalho técnico
**Peças resultantes:** 2.6a (modo bimodal), 2.6b (sectioned), 2.6c (âncora de tarefa)

### O que aconteceu — ciclo completo de descoberta empírica em 14 horas

Sprint de implementação contínua identificou três limites arquiteturais
através de uso real, não de teoria. Cada peça foi validada empiricamente
em produção antes da próxima ser projetada.

**Manhã (peça 2.6a):** Diagnóstico de inflação crônica em sprints contínuos
levou ao modo bimodal cognitive/sprint. Default cognitive preserva identidade
do EDP; sprint expande janela imediata (cap 12000 chars) com aviso de custo
explícito. Switch consciente via `/mode sprint|cognitive|status`.

**Manhã (peça 2.6a.fix):** Bug descoberto em produção — backend enviava
tipos WebSocket (`mode_status`, `mode_change`) que o frontend não tratava.
Chat travava em "Enviando...". Diagnóstico via `grep "d.type ===" dashboard.js`
revelou que frontend trata apenas 8 tipos: `start`, `chunk`, `done`,
`pipeline_done`, `llm_start`, `warn`, `error`, `heartbeat`. Correção:
reutilizar `start` + `chunk` + `done`. Princípio registrado: **verificar o
consumer antes de definir o protocolo**.

**Tarde (peça 2.6b):** Modo entrega-por-seção. Comando `/sectioned` restrito
a sprint. System prompt instrui formato `## Seção N/M — Título`. Atalho
`/next` e variantes. Funcionou em 3-4 entregas.

**Tarde (peça 2.6c):** Limite descoberto em uso real — em tarefa de 10
seções, modelo regenerou Seção 2 três vezes seguidas. Diagnóstico arquitetural:
janela imediata é **cega para tarefa em curso** porque mistura turnos não
relacionados de toda a sessão. Solução: camada nova (Camada 0.5 do payload)
com bloco `[ÂNCORA DE TAREFA EM CURSO]` listando desafio + seções já entregues
+ próxima esperada. Parser determinístico via formato CONTRATADO no system
prompt.

### Validação empírica com sessão limpa

Em teste com banco zerado e mensagens "continue" puras: 10 de 10 seções
entregues, parser ok em todas, auto-limpeza em 10/10. Tempo total: 4 minutos.
Custo: ~$0.05. Comparação com resposta única equivalente (Opus, $0.31):
**80% da qualidade com 16% do custo, mais auditabilidade completa.**

### Avaliação externa por IA independente

Outra instância de IA (sem contexto da implementação) avaliou as 10 seções:
- Nota geral: 7.1/10 (Pleno+)
- Cobertura: 9/10 (todas as 10 seções entregues)
- Coesão arquitetural: 5.5/10 (tecnologias inconsistentes entre seções)

Diagnóstico do avaliador externo: *"sem memória do estado da tarefa, o
modelo perde o fio entre seções e gera partes corretas que não formam um
todo coerente"* — confirmação independente do problema arquitetural que a
peça 2.6c estava resolvendo.

### Insight arquitetural identificado

Âncora resolve **continuidade de progresso** (modelo não regenera seções
já entregues) mas não **continuidade de decisões técnicas** (modelo escolhe
Kafka na Seção 1 e RabbitMQ na Seção 4 sem justificar). Resolver isso exige
âncora carregar não só títulos mas decisões — pendente como peça 2.6d.

### Lição registrada

O EDP nas mãos certas não é "ferramenta que responde perguntas". É
**disciplina arquitetural para LLMs**: força o modelo a entregar exatamente
o que foi pedido, sem inflar, sem alucinar, sem mudar escopo. A qualidade
do output reflete a qualidade do pensamento que foi para dentro.

Princípio metodológico: **descobrir → propor → implementar → testar em uso
real → diagnosticar limite → próxima iteração.** Sprint inteira respeitou
esse ciclo. Cada peça validada antes da próxima.

---

## [MARCO-EPISTÊMICO] Validação Empírica do Formato Contratado — Abordagem C

**Data:** 2026-05-30 (sábado, noite)
**Detectado em:** teste manual antes de codar peça 2.6d-M1
**Peça relacionada:** 2.6d (em desenvolvimento)

### O que aconteceu

Antes de codar parser de decisões arquiteturais para a âncora, o usuário
propôs Abordagem C: forçar o modelo a declarar decisões em comentário HTML
invisível (`<!-- decisions: {...} -->`) ao final de cada seção. Parser
seria regex trivial; modelo seria o próprio extrator.

Teste manual com desafio de 3 seções sobre arquitetura de pagamentos
(50M tx/mês) com instrução explícita do formato no system prompt.

### Resultado

Modelo (Sonnet 4.6, escolhido por router em "complexidade média: texto
longo 239 palavras") entregou Seção 1 com:

- Stack tecnológica completa com justificativa por camada
- Diagrama de microsserviços com responsabilidades
- Contrato Go versionado em módulo separado (`contracts/v1`)
- `EventMetadata` com correlation_id, idempotency_key, schema_version
- `Money` em centavos int64 (sem float)
- Validação, factory, serialização separadas
- Estimativa numérica de throughput (190 tx/s pico)

E ao final, **bloco `<!-- decisions -->` perfeito**:

```html
<!-- decisions: {
  "messaging": "Apache Kafka 3.7 — particionamento por payment_id...",
  "language": "Go 1.22 — goroutines para I/O concorrente...",
  "database": "PostgreSQL 16 por serviço...",
  "patterns": "Event-driven com coreografia...",
  "contracts": "PaymentEvent v1.0.0 — módulo contracts/v1..."
} -->
```

JSON válido. Chaves consistentes com o pedido. Valores ricos e específicos.

### Validação de M2 (router preserva modelo em sectioned)

No mesmo teste, segundo turno foi "contenue" (typo de "continue"). Router
recebeu `task_context={sectioned_active:True, task_anchor_active:True,
previous_model:claude-sonnet-4-6, n_words:1}`. **Preservou Sonnet** em vez
de rebaixar para Haiku.

Log do servidor:
```
17:54:06  msg recebida len=8 mode=sprint sectioned=on
17:54:08  [anthropic] complete | model=claude-sonnet-4-6
17:54:11  stream done | model=claude-sonnet-4-6
```

Confirmação mecânica: M2 (peça 2.6d) funciona em produção real.

### Bug B identificado em forma nova

O typo "contenue" não bateu na lista exata de continuations
(`continue|continuar|próxima|...`) → `start_task` foi chamado → âncora foi
zerada com challenge inválido (8 chars). Toda a Seção 1 com riqueza
arquitetural foi perdida do estado da tarefa.

Modelo, recebendo âncora com challenge="contenue", **não inventou Seção 2**.
Pediu clarificação:

> "Preciso de mais contexto — qual é o desafio ou tarefa que devo continuar?
>  A âncora indica 'contenue' como descrição, mas nenhuma seção foi entregue
>  ainda..."

**Apenas 44 tokens de output, 3 segundos.** Comportamento ideal: não
alucinou, não inventou, pediu esclarecimento.

### Lições registradas

1. **Abordagem C validada empiricamente.** Modelo segue formato HTML
   invisível quando instruído no system prompt. JSON válido, chaves
   consistentes. M1 viável.

2. **M2 validada em produção.** Router preservou Sonnet apesar de
   mensagem curta, exatamente porque sectioned + anchor estavam ativos.

3. **Bug B é mais crítico que pensado.** Typo destrói tarefa inteira.
   Com M1 implementada (decisões persistindo), um typo destruiria
   ainda mais valor. Fix necessário antes de M1.

4. **Comportamento de pedir clarificação em vez de inventar é raro
   e valioso.** Resultado direto de: (a) âncora estruturada chegando
   ao modelo, (b) janela imediata mostrando o desafio original, (c)
   sistema disciplinado evitando alucinação por preenchimento de
   contexto. Sistema funcionou exatamente como projetado mesmo na
   falha — pediu ajuda em vez de mentir.

---
