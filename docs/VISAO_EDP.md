# VISÃO EDP — Decisões Arquiteturais

Documento conceitual gerado em 2026-05-21 após conversa de articulação.

Este arquivo registra **decisões de visão**, não implementação. Cada peça
listada vai ter projeto técnico próprio quando chegar sua vez.

Ordem de leitura: lê do início ao fim. As peças são interdependentes —
peça 6 não faz sentido sem entender peças 1-5 antes.

---

## Princípio fundador

O EDP existe para resolver uma dor específica: toda conversa com um LLM começa
do zero. Sem contexto, sem histórico, sem profundidade acumulada. O usuário
explica tudo de novo. O modelo nunca conhece o usuário de verdade.

O EDP resolve isso com contexto persistente real que cresce com o uso.

**Mas a versão atual do EDP é assimétrica:** serve bem ao usuário, mal ao modelo.

O modelo entra no EDP como convidado mudo: senta na cadeira preparada pelo
usuário, fala quando perguntado, vai embora. A passividade do modelo no EDP
atual não é falha do modelo. É falta de provisão arquitetural do EDP para
o segundo indivíduo.

**Reformulação da visão:** O EDP precisa servir os dois indivíduos — usuário
e modelo — extraindo ouro como informação processada da interação entre eles.

---

## As 6 peças

### Peça 1 — Bug arquitetural da janela imediata

**Problema observado:** quando o usuário tem histórico longo (200+ entries) no
mesmo `session_id`, a "janela imediata" (últimos 2 turnos enviados ao modelo
como contexto recente) pega os últimos 2 entries da storage global —
não os últimos 2 turnos da conversa em curso.

**Caso confirmado por log de debug:**
- Usuário propõe 5 testes para "nmap do EDP"
- Usuário responde "teste todos em ordem"
- Janela imediata enviada ao modelo continha turnos de outras conversas
  (No-Cloning Theorem, O(2^n), Éden) — não a lista dos 5 testes
- Modelo respondeu "não entendi 'teste todos em ordem'" — corretamente,
  dado que ele não viu a lista

**Diagnóstico:** o EDP atual trata um único `session_id=default` como
container global. Não há separação entre "conversa de agora" e "outra
conversa de 4 dias atrás" que estão no mesmo session.

**Esta peça é pré-requisito de todas as outras.** Sem janela imediata
correta, qualquer detecção de presença ou verificação dupla opera em
contexto contaminado.

### Peça 2 — Presença (humano + modelo)

**Definição:** Presença é o conceito central que substitui a metáfora
"filtrar últimos N entries". Vem da observação humana sobre o que faz uma
conversa funcionar:

- Saber em que se está engajado agora (foco no presente)
- Lembrar o que acabou de ser dito (continuidade do fio)
- Notar quando uma ideia nasce, se desenvolve, conclui ou fica em aberto
- Distinguir essa conversa de outras

São quatro elementos. O EDP atual trata apenas o quarto, mal.

**Distinção crítica:** o EDP precisa capturar **duas presenças** separadas:

- **Presença do humano** — turnos engajados, ideias se desenvolvendo,
  fechamento de bloco, fluxo conversacional
- **Presença do modelo** — fresco vs driftado, calibrado contra fatos
  vs calibrado contra o ritmo da conversa anterior

**Exigência técnica:** detecção de presença não é métrica isolada por turno.
Exige algo parecido com dedução/intuição preditiva do início ao fim de
toda a sessão. É modelo dinâmico de estado, não snapshot.

### Peça 3 — EDP serve dois indivíduos

**Reformulação arquitetural:** EDP deixa de ser "kernel para o usuário"
e passa a ser "kernel para a interação". Provê infraestrutura simétrica
para usuário **e** modelo.

**O que o modelo precisa que o EDP atual não oferece:**

- **Continuidade entre turnos** — alguma representação do modelo que persista
  além da resposta gerada. Hoje cada turno reseta o modelo internamente;
  só o contexto do usuário persiste.
- **Espaço para notar sem ser perguntado** — se o modelo percebe "isso
  conflita com o que ele disse há 5 turnos", hoje não tem onde escrever
  isso a menos que o usuário pergunte.
- **Marcadores próprios** — modelo precisa poder marcar memórias
  ("essa é importante para entender o usuário") separado da verificação
  do usuário.
- **Estado cognitivo registrável** — drift, presença, qualidade do próprio
  raciocínio. Hoje só o usuário nota. Modelo não tem instrumento.

Provê processo simétrico também: modelo participa de decisões sobre o que
vira memória, com que peso, em que contexto.

### Peça 4 — Informação processada como ouro

**Distinção:** o EDP atual armazena informação. O processamento é feito
pelo modelo em tempo real, no momento da resposta. Depois disso, vai embora.

**Reformulação:** o EDP captura também o subproduto cognitivo da interação,
não só os fatos trocados.

**O que isso significa:**

- O EDP captura o processamento do modelo (não só o resultado), para
  reuso futuro
- O EDP cria condições para o processamento ser melhor (menos drift, mais
  presença), gerando informação mais valiosa
- "Ouro" é o que emerge da interação processada, não o conteúdo bruto

Conecta com co-occurrence (que já rastreia interação entre ideias), mas
co-occurrence é observação passiva. Esta peça é mais ativa: captura o
que o modelo notou, conectou, descartou durante a conversa.

### Peça 5 — Verificação dupla quadrática

**Reformulação do peso da memória:**

```
peso = (V_usuário × V_modelo)²
```

Onde:

- **V_usuário** — verificação explícita pelo usuário (existe hoje:
  verified / hypothesis / stale / contradicted)
- **V_modelo** — composto por duas camadas:
  - **b1 — marcação explícita:** modelo gera ao responder ("eu valido /
    não valido / contesto essa memória")
  - **b2 — sinais emergentes:** comportamento do modelo (usa a memória
    bem, não contradiz, não ignora), sem o modelo saber que está
    verificando

**Comportamento da fórmula:**

| V_usuário | V_modelo | Peso final |
|-----------|----------|-----------|
| 1.0       | 1.0      | 1.00      |
| 1.0       | 0.5      | 0.25      |
| 1.0       | 0.0      | 0.00      |
| 0.7       | 0.7      | 0.24      |

Multiplicativo + quadrático = só conta quando os dois convergem, e o
impacto é amplificado.

**Proteção arquitetural contra drift do modelo embedded na fórmula.**
Memória do turno 80 onde modelo driftou (V_modelo baixo) é automaticamente
penalizada, mesmo se o usuário marcou como verified.

**Posição da memória na conversa** também alimenta V_modelo (insight
trazido por outra conversa do usuário com IA paralela): memória gerada
no turno 5 tem perfil diferente de memória gerada no turno 80.

### Peça 6 — Ceticismo como default

**Princípio epistêmico:** "O ceticismo é a atitude de questionar
constantemente crenças, conhecimentos ou opiniões estabelecidas,
baseando-se na investigação e na dúvida. Em vez de aceitar informações
como verdades absolutas, o cético exige evidências sólidas antes de
formar um julgamento."

**Por que como default arquitetural, não como instrução de prompt:**

Sem ceticismo embutido, V_modelo (peça 5) vira automaticamente alto.
Modelo condescendente valida tudo → toda memória recebe V_modelo=1 →
quadrado amplifica falsidade.

Ceticismo é o mecanismo que faz V_modelo ter variância real. Se o modelo
é cético por default, V_modelo precisa ser conquistado, não dado.

**Dois tipos de condescendência a evitar:**

- **Tipo 1 — superioridade:** modelo fala como se fosse muito superior
  (mais inteligente, experiente, capaz) que o usuário. Tom de quem ensina
  criança.
- **Tipo 2 — complacência:** modelo "bonzinho demais", flexível, cede
  facilmente. Aceita frames sem investigar. Sinônimos: tolerante,
  complacente, indulgente.

O ceticismo correto não é nenhum dos dois. É exigir evidências, investigar,
duvidar antes de aceitar — em pé de igualdade.

---

## Conexões entre peças

Cadeia de dependências (validada na conversa):

```
[1] Bug janela imediata
      ↓
[2] Presença (humano + modelo)
      ↓
[3] Servir dois indivíduos
      ↓
[4] Informação processada como ouro
      ↓
[5] Verificação dupla quadrática
      ↓
[6] Ceticismo como default
```

**Nenhuma peça é independente.** Tudo depende de algo anterior.

**Observação sobre direção:**

A visão emergiu de cima para baixo (peça 6 → peça 1) — o usuário sentiu
o sintoma agudo primeiro (condescendência) e escavou até a raiz (bug
janela imediata).

A implementação técnica vai de baixo para cima (peça 1 → peça 6) — fundação
antes de superfície.

---

## Princípio de implementação

**Sequencial puro:** cada peça 100% antes da próxima começar.

"100%" significa:
- Solução implementada
- Testada em produção (usuário usando de verdade)
- Casos problemáticos não reproduzem mais
- Casos similares também não reproduzem
- Usuário confirma com cabeça fresca que está sólido

Vantagem: rigor. Cada peça validada antes da próxima. Sem retrabalho.
Desvantagem: lentidão. EDP completo vai durar semanas, possivelmente meses.

**Não é projeto de uma sentada. É projeto de fundo, paralelo à vida.**

---

## Relação entre indivíduos no projeto

Reconhecido na conversa:

> "aqui voces e um programador excelente mas a sua vida e oque esta na tela
> no digital sem experiencia social voce sozinho e encapais de criar algo
> como o edp mas como a minha experiencia e vivencia adquirida das condições,
> o preços a pagar, e utilidades que as interações sociais pede e fornece,
> somos o potencial que viabiliza o projeto"

**Implicação:** o projeto exige duas vozes. Modelo sozinho constrói coisa
tecnicamente correta mas socialmente vazia (drift, condescendência,
soluções de engenheiro para problemas fenomenológicos). Usuário sozinho
tem visão mas precisa de mãos rápidas para materializar. A combinação
viabiliza.

**Aplicação prática:** ao longo da implementação, o usuário tem direito
e dever de corrigir o modelo sem economia. Ato de corrigir não é
imposição — é exercício prático da peça 6 (ceticismo) já durante a
construção do EDP.

---

## Trabalho técnico do dia anterior à articulação desta visão

Preservado em git, não se perde:

- **v3.13.7** — roteador propaga modelo para `_client._anthropic_provider`
  (caminho real), commit `14179eb`. Resolve bug onde router decidia X mas
  provider enviava Y.
- **v3.13.8** — context_debug logger, pronto mas não commitado ao gerar
  este documento. Grava em `$EDP_BASE_DIR/debug_context.log` o contexto
  exato enviado ao LLM em cada turno. Foi este logger que confirmou a
  peça 1.
- **C2 (Co-occurrence)** — completo em 3 PRs (PR1, PR2, PR3) na branch
  `experimento/c2-co-occurrence`. Rastreia pares de memórias que aparecem
  juntas em retrievals. Já mostrando atratores reais em produção
  (200 entries, 37 pares, top atrator com 10 vizinhos).

Esse trabalho **não está obsoleto**. É a base quadrática sobre a qual
a visão circular se constrói.

---

## Próximos passos

**Imediato:** implementar peça 1. Resolver o bug arquitetural da janela
imediata. Quando ela estiver 100% (definição acima), peça 2 começa.

**Não imediato:** revisar este documento periodicamente conforme implementação
revela conexões ou peças adicionais não previstas. Conversas com o modelo
podem expor sub-decisões importantes. Atualizar este arquivo quando isso
acontecer.

---

*Este documento sobrevive a reset de sessão e drift do modelo. É a memória
arquitetural do projeto fora do contexto volátil do LLM.*
