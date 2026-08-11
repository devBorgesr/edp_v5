# PRE_REGISTRO — Gate de especificidade (sucessor do Degrau 1)

Contrato: `NORTE.md@36ac6b4`. Antecedente:
`docs/preregistro_degrau1_honeypot.md` (H0, veredito em `dd06b87`).
Escrito em **06/08/2026**, ANTES de qualquer medição.

Status: **medição** — não autoriza implementação de honeypot, wiki, nem
busca web.

---

## 1. Por que este experimento existe

O H0 do Degrau 1 mostrou **R1 — seletividade invertida**: das 14 queries,
as 4 que passariam um gate de similaridade bruta ≥0.70 eram **todas
anafóricas**; nenhuma factual passou (anafóricas: sim média 0.7362;
factuais: 0.4883).

A causa não é a fonte de dados — é a regra de roteamento. Texto curto e
genérico aglomera no espaço de embeddings, então *"a pergunta nova é
similar a algo que eu tenho?"* dispara em perguntas vagas e silencia em
perguntas específicas. **Qualquer fonte** atrás desse gate (memória, wiki,
resultado de busca web) herda o defeito.

Logo, a pergunta que destrava as frentes seguintes não é "wiki ou busca
primeiro", é: **existe uma regra de roteamento que não seleciona vagueza?**

Este experimento testa a candidata mais barata: um gate de
**especificidade da query**, computado a partir da query e do corpus,
**sem olhar o que foi recuperado**. Se a query não nomeia nada específico,
não há resposta armazenável que a satisfaça — e o sistema deve recusar
antes de medir similaridade.

---

## 2. O dataset agora é vantagem, não obstáculo

No Degrau 1 o pool anafórico do EXP017 era o instrumento errado, porque
não havia perguntas cacheáveis para acertar. Aqui ele é o instrumento
**certo**: o experimento é de **classificação**, e o pool já vem rotulado.

Os rótulos foram congelados em **julho/2026** (`EXP017_FASE0.md:90-103`,
pools `[R3]`/`[R2]`/`[N]` de `exp009.py:70-77` e `exp010.py:84-88`) —
por outra pessoa, para outro fim, muito antes deste experimento existir.
Não há como eu tê-los ajustado ao resultado.

| pool | n | natureza | papel aqui |
|---|---|---|---|
| `[R3]` | 6 | anáfora pura ("me lembra o que discutimos") | **negativos inequívocos** |
| `[N]` | 5 | factuais ("qual é a capital da Mongólia mesmo?") | **positivos** |
| `[R2]` | 3 | anáfora **com tópico** ("...concluiu sobre cache... com Redis") | **ambíguos — fora do critério** |

`[R2]` fica **fora do critério de decisão**, congelado agora e não depois.
Motivo: "me lembra o que a gente concluiu sobre cache de sessões web com
Redis" nomeia um tópico real; se a conclusão estiver armazenada, um cache
poderia legitimamente respondê-la. Chamá-la de negativo me daria crédito
barato, e chamá-la de positivo me daria desculpa. Os 3 são **reportados,
nunca pontuados**.

---

## 3. A métrica (congelada)

**Especificidade da query = média dos 3 maiores IDF entre seus tokens**
(ou de todos, se a query tiver menos de 3).

- Tokenização: minúsculas, `\w+` (Unicode, preserva acento). **Sem lista
  de stopwords** — o IDF já rebaixa palavra comum sozinho, e uma lista
  manual seria mais um parâmetro que eu poderia ajustar.
- Corpus do IDF: as entries do store (documento = uma entry).
  `IDF(t) = ln((N+1)/(df(t)+1))`.
- Token nunca visto no corpus: `df = 0`, ou seja IDF máximo. É semanticamente
  certo (termo que o sistema nunca viu é maximamente específico) mas pode
  inflar `[N]`, então **a contagem de OOV por query é reportada** junto.

*Por que top-3 e não média de tudo ou máximo:* a média de todos os tokens
dilui — uma query longa com muitos conectivos afunda mesmo nomeando algo
raro. O máximo é frágil — um único token estranho decide sozinho. Média
dos 3 maiores é o meio-termo. Média-de-tudo e máximo são reportados como
**secundários exploratórios**, nunca promovidos a critério.

---

## 3-bis. EMENDA E1 — 06/08/2026 (pré-dado, aditiva; §3 intocado)

Escrita **antes de qualquer medição sobre o store real**, motivada por um
smoke test em corpus sintético. Registro aqui o que vi e por que mudei,
para que a troca seja auditável em vez de silenciosa.

**O que aconteceu.** Rodei o script contra um store sintético montado com
parágrafos dos `.md` do próprio repositório (210 entries, mesmo N do store
real). A regra do §3 — *token OOV recebe IDF máximo* — fez
`"voltando ao que estávamos vendo"` tirar **a maior especificidade de
todas**, porque "voltando", "estávamos" e "vendo" não ocorrem em
documentação técnica. A regra estava **premiando exatamente a vagueza que
deveria punir**.

**A mudança.** Token OOV passa a valer **0**, não IDF máximo.

*Justificativa (não é conveniência):* o gate responde à pergunta "o meu
store pode responder isto?". Um termo que o store nunca viu é evidência
**contra** rotear para o store, nunca a favor. Dar-lhe pontuação máxima
inverte o sinal. A emenda **remove** um comportamento, não adiciona
parâmetro.

**Auditoria obrigatória.** O script reporta as duas métricas lado a lado —
`top3` (OOV=0, primária) e `top3_max` (§3 original) — e **avisa em tela se
elas divergirem no veredito**. No smoke test as duas deram H0, ou seja, a
emenda não inverteu resultado; se no store real inverterem, isso vai
aparecer no relatório em vez de ficar escondido.

### 3-bis.1 Checagem de sanidade do instrumento (congelada)

O smoke test expôs um pressuposto que eu não tinha registrado: **o gate só
discrimina se o corpus do IDF for do mesmo gênero das queries.** No corpus
técnico, fala conversacional e termo técnico ficaram indistinguíveis:

| termo | df | IDF | |
|---|---|---|---|
| `discutimos` | 0 | 5.352 | conversacional, marcado como raro |
| `voltando` | 0 | 5.352 | conversacional, marcado como raro |
| `redis` | 2 | 4.253 | técnico, **menos** raro que os acima |
| `rrf` | 2 | 4.253 | técnico |

Num store de conversas reais o padrão deve se inverter — "vamos",
"lembra", "discutimos" aparecem em quase toda entry.

**Critério congelado:** se a fração de tokens **OOV** nas 6 queries `[R3]`
for **> 20%**, o corpus do IDF não é do mesmo gênero das queries, o
instrumento é inválido e **o veredito H1/H0 não deve ser interpretado** —
reporta-se `INSTRUMENTO INVÁLIDO` e o experimento é refeito com outro
corpus. (No smoke test: 10 de 31 tokens = 32% ⇒ teria sido barrado.)

O piso de 20% é fixado agora, antes do dado. Racional: as `[R3]` são
compostas quase só de conectivos e verbos comuns de conversa; num corpus
conversacional de 210 documentos, esperar que mais de 1 em 5 desses
tokens jamais tenha ocorrido é implausível — se ocorrer, o corpus não é
conversacional.

---

## 4. Hipótese (livre de limiar)

Com 14 pontos, escolher um corte depois de ver os números é overfitting
garantido. Por isso o critério não usa limiar:

- **H1 — separação perfeita:**
  `min(especificidade dos 5 [N])` **>** `max(especificidade dos 6 [R3])`
- **H0 —** qualquer sobreposição entre os dois conjuntos.

Se H1 sobreviver, um limiar existe por construção (ponto médio do
intervalo entre os dois extremos) e é **derivado**, não escolhido. Se H0
vencer, nenhum limiar separa e o gate de especificidade cai — não adianta
procurar o corte "certo" depois.

**Predição pré-dado do arquiteto:** H1 sobrevive. Razão: as 6 `[R3]` são
construídas só de conectivos e verbos genéricos ("vamos", "continuar",
"conversa", "discutimos", "falávamos"), enquanto as 5 `[N]` carregam
termos raros ("Mongólia", "RRF", "NOT_FOUND_FLOOR", "exp016", "Bayes").
Registro a predição para que ela possa ser refutada.

---

## 5. O que este experimento NÃO decide

Congelado antes do dado, para não ser negociado depois:

- **Não mede cobertura.** As 5 `[N]` não têm memória correspondente no
  store (10 dos 14 misses do Degrau 1 foram `SEM_MEMORIA_SIMILAR`). Mesmo
  com H1 confirmada, o número de acertos continua 0 neste store. O gate é
  condição **necessária**, nunca suficiente.
- **Não ressuscita o honeypot.** H1 remove *um* defeito (R1). Continuam de
  pé: F1 nunca medido, 0,95% de entries `verified`, e o blob `Q+A`
  destruindo 47% da faixa dinâmica.
- **Não autoriza wiki nem busca web.** Ambas seguem em `FILA_FUTURO.md`
  sob o NORTE.md até 02/09/2026.

---

## 6. Critérios PASSA/FALHA

| Resultado | Decisão |
|---|---|
| **H1** (separação perfeita) | Gate de especificidade vira pré-requisito de qualquer roteamento futuro, documentado com o limiar derivado. Próxima pergunta passa a ser cobertura (F1), não roteamento. |
| **H0** (sobreposição) | Gate de especificidade **descartado**. Registrar em `FILA_FUTURO.md` com os números. Restam as outras duas candidatas do §7 — ou a conclusão de que roteamento por conteúdo da query não é viável, o que seria um achado forte contra a arquitetura inteira do honeypot. |

**H0 é resultado publicável**, não fracasso.

---

## 7. Candidatas NÃO testadas aqui (registradas para não virarem improviso)

- **Verificação de trecho-resposta**: exigir que o item recuperado contenha
  um span que responda à query. Mais caro, exige extração.
- **Roteamento por tipo de pergunta**: classificar factual vs. continuação
  antes de olhar similaridade. Precisaria de classificador — mais peças.

Se H0 vencer aqui, essas entram em pré-registro próprio, uma por vez.

---

## Resultado

`[PREENCHER — rodada Windows: scripts/medir_gate_especificidade.py]`

Store medido: `[PREENCHER]`   Entries: `[PREENCHER]`
Data: `[PREENCHER]`   Commit do script: `[PREENCHER]`

| # | pool | query | espec. (top-3) | OOV | média-tudo | máx |
|---|---|---|---|---|---|---|
| | | `[PREENCHER — 14 linhas]` | | | | |

min([N]) = `[PREENCHER]`   max([R3]) = `[PREENCHER]`
Separação = `[PREENCHER]` (positiva ⇒ H1)
`[R2]` (reportado, não pontuado): `[PREENCHER]`

**Veredito H1 / H0:** `[PREENCHER]`
Limiar derivado (só se H1): `[PREENCHER]`
