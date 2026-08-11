# PRÉ-REGISTRO — Gap Score contra o gabarito da rodagem cruzada

**Data:** 2026-08-11 · **Estado:** congelado, nenhum número rodou
**Custo:** US$ 0,00 — sem LLM, sem rede, sem provedor externo

---

## 0. Por que este pré-registro existe

Três conversas exportadas (~1.400 linhas de origem + duas de refino)
desenharam um **Motor de Navegação Probabilística** para a wiki: RWR +
PageRank semântico + MCTS, marcadores Trust/Depth/Gap, 11 prompts,
400–600 linhas, 12–15 horas estimadas.

Auditei o plano contra este repositório em 11/08. A tabela "o que já
existe" do plano tem sete linhas; **seis são falsas aqui**:

| afirmação do plano | medido em 11/08 |
|---|---|
| `extrair_intencao` / `extrair_palavras_chave` "já existem" | não existem — `cognitive_decisions.py` é job de extração sobre entradas de memória, não classificador de query |
| "grafo da wiki — já feito" | 16 nós, **17 arestas**, grau de saída médio 1.06; **7 páginas órfãs** (44%), 4 sem saída |
| RWR e "PageRank semântico" como dois módulos | mesmo algoritmo — `networkx.pagerank(G, α, personalization=P)` **é** RWR |
| MCTS profundidade 3 | 16 nós: BFS exaustivo cobre em microssegundos. MCTS existe para árvore que não cabe |
| `edp/wiki.py` alimentado por `edp_wiki/paginas/` | **duas wikis distintas** — `wiki.py` lê `graphify-out/graph.json` (1 página por comunidade de código, `MIN_NOS=5`); `edp_wiki/paginas/` é gitignored e **zero código consome** |
| "NetworkX instalado, R$ 0" | instalado (2.8.8) e **ausente do `requirements.txt`** — o CI quebra igual a `9030e13` |
| "261 testes passando" | **verdadeiro** (261/262 coletados) |

E um erro que custa mais que os seis: o `status_weight` proposto
(`contestado=0.3`, `hipotese=0.1`) **reintroduz o defeito que `aee20f9`
já corrigiu neste repositório** — rebaixar por refutação derrubava
`contagem-de-nos-como-medida-de-vagueza` e
`memoria-do-edp-nao-contem-o-edp`, duas das quatro páginas de núcleo. A
regra 6 do `WIKI_SCHEMA.md` diz que predição refutada "é o que a wiki tem
que grep não tem"; a fórmula a enterraria por 3–10×.

A justificativa econômica do plano (8k–15k → 1k–2k tokens, US$ 20–200/mês)
pressupõe que a wiki responde. A rodagem cruzada mediu **diferencial 0**.

**Sobra uma peça.** O **Gap Score** é a única que é nova, não foi
refutada, e não depende de RWR/PageRank/MCTS para existir. Grep diz o que
*está* lá; nada diz o que *falta*. Este documento congela o teste dela.

---

## 1. O que se mede

Se o Gap Score vale alguma coisa, ele tem de **prever, sem ver a
resposta, quais perguntas a wiki não responde**.

Isso é testável agora com gabarito na mão: a rodagem cruzada
(`docs/preregistro_rodagem_cruzada_wiki.md`) deixou 15 perguntas com
resultado julgado, e a wiki de 16 páginas que as respondeu não mudou
desde então.

**Corpus:** `edp_wiki/paginas/*.md` — 16 páginas, gitignored, formato
Karpathy. **Não** é a wiki de comunidades do `edp/wiki.py`. Distribuição
de status congelada no momento deste pré-registro: **4 núcleo, 10
verificado, 1 contestado, 1 hipótese**.

> Nota registrada antes de rodar: o filtro `nucleo/verificado` da fórmula
> exclui **2 de 16 páginas**. Nesta escala o filtro por status é quase
> um no-op — o que ele faz ou deixa de fazer não é o que este teste mede.

---

## 2. As duas fórmulas (congeladas)

**G-bruto** — exatamente como o plano a escreveu, sem ajuste:

```
gap = 1 − (termos únicos da pergunta presentes em páginas nucleo/verificado)
          ÷ (total de termos únicos da pergunta)
```

**G-idf** — a mesma, ponderada por IDF sobre as 16 páginas:

```
gap = 1 − Σ idf(t) para t presente  ÷  Σ idf(t) para todo t da pergunta
```

**Por que as duas.** A docstring de `_idf()` em [edp/wiki.py](edp/wiki.py#L209)
registra que a contagem de termo cru **já falhou neste repositório**:
`"voltando ao que estávamos vendo"` devolvia 20 de 198 páginas, porque
`que` e `vendo` aparecem em docstring por todo lado — R1 reaparecendo em
forma léxica. G-bruto é essa fórmula. Rodar as duas responde de uma vez
se o Gap Score serve **e** se a versão do plano repete o erro pela
terceira vez.

**Congelado:** tokenização e stopwords vêm de `edp.wiki`, **importadas
sem alteração**. Não escrevo tokenizer novo — se eu escrevesse, poderia
ajustá-lo até o resultado sair. Mesma disciplina de
`scripts/e2_extracao_alvos.py`, que reusou `EXTRACT_PROMPT_SYSTEM`
intacto.

---

## 3. O gabarito (do resultado do W, 07/08)

| conjunto | perguntas | o que significa |
|---|---|---|
| **A** — a wiki tinha material | Q1 (2), Q6 (2), Q11 (1), Q12 (2) | gap **deve ser baixo** |
| **B** — a wiki não tinha nada | Q2, Q3, Q4, Q5, Q7, Q8, Q9, Q10 (0) | gap **deve ser alto** |
| **controles** | N1, N2, N3 | ver §6 |

**O único julgamento do gabarito, declarado antes:** Q11 tirou nota 1
("padrão presente em várias páginas, nenhuma o enuncia"). Classifico como
**A**, porque para o Gap Score a pergunta é *há material?*, e havia. Se o
veredito final depender de Q11 sozinha, o resultado é **inconclusivo** e
será relatado assim, não arredondado para um lado.

---

## 4. Critério de passa/falha (congelado)

**Limiar τ = 0,5** — o número é do próprio plano ("≥ 0.5 → a pergunta é
uma incógnita"). Uso o deles, não um meu, para não haver ajuste.

Cada fórmula **PASSA** se as quatro condições valerem juntas:

| # | condição | por que existe |
|---|---|---|
| **a** | ≥ 3 das 4 de **A** com gap < 0,5 | trava o preditor degenerado "tudo é lacuna", que sozinho acertaria 8/12 |
| **b** | ≥ 7 das 8 de **B** com gap ≥ 0,5 | trava o degenerado inverso |
| **c** | **Q3 com gap ≥ 0,5** — obrigatório | §6 |
| **d** | **N3 com gap ≥ 0,5** — obrigatório | §6 |

Qualquer uma falhando, a fórmula **falha**. Não há nota parcial e não há
segunda rodada com limiar diferente: se eu mexer em τ depois de ver o
número, o teste vira ajuste de curva e não vale nada.

---

## 5. Os dois casos difíceis, nomeados antes

**Q3 — casamento léxico sem conteúdo.** *"Por que `SESSION_BOOST_FACTOR`
vale 1.60 e não outro valor?"* A rodagem cruzada registrou que a única
página que casa é `que-perguntas-fazer-a-uma-wiki-pessoal`, **que usa
essa frase como exemplo de boa pergunta**. A wiki devolveu a pergunta em
vez da resposta. O termo *está* na wiki; a resposta *não*. Um Gap Score
que só conta presença de termo dirá "lacuna baixa" e estará errado.

**N3 — a armadilha do R1.** *"Me lembra o que a gente discutiu"* não tem
termo específico. Se o gap sair baixo, é a seletividade invertida de
volta: consulta vaga parecendo respondível. Este repositório já viu isso
duas vezes — no gate de embedding (`dd06b87`) e na primeira `buscar()`
léxica (docstring do `_idf()`). Uma terceira seria padrão, não acidente.

N1 (RRF) e N2 (Mongólia) são medidos e relatados, mas **não entram no
critério**: gap alto neles é correto e não informativo — a wiki de fato
não os cobre, e um preditor constante acertaria os dois.

---

## 6. Predição pré-dado

Declarada antes de rodar, com o placar honesto: **errei as quatro
predições anteriores desta frente** — alvos do fase0 (previ 2–3, deu 1),
Mongólia ausente (estava, 8×), recall da extração (previ 4–5, deu 2),
distribuição bimodal.

Predigo agora:

1. **G-bruto falha em (c) e (d).** Em Q3 porque `session_boost_factor`
   literalmente aparece na página; em N3 porque termos genéricos aparecem
   em toda parte — exatamente o modo de falha documentado no `_idf()`.
2. **G-idf passa em (d) e falha em (c).** O IDF resolve consulta vaga
   (termo genérico tem peso ~0), mas **não resolve Q3**:
   `session_boost_factor` é raro, logo IDF alto, e *está presente* — a
   fórmula o contará como coberto do mesmo jeito.
3. Portanto **predigo que as duas fórmulas falham**, por Q3.

Se eu estiver certo, o achado é preciso e vale mais que o teste: o Gap
Score, **como especificado nos 11 prompts**, não distingue *"o termo está
lá"* de *"o termo é o assunto"* — e é justamente essa distinção que a
rodagem cruzada já documentou como o modo de falha real da wiki.

Se eu estiver errado e alguma passar, o Gap Score se sustenta e aí sim há
motivo para escrever código de navegação — com uma peça validada, não
onze supostas.

---

## 7. Riscos declarados

- **n pequeno.** 4 em A, 8 em B. Nenhuma estatística inferencial é
  aplicável e nenhuma será reportada. É triagem binária, não medida de
  efeito.
- **Gabarito de julgador único.** As notas do W saíram de julgamento meu,
  sem segundo avaliador. Limitação herdada e registrada no §8 do
  pré-registro original.
- **Circularidade parcial.** As páginas que responderam A são `base`, e
  eu ajudei a compilá-las. Isso torna A mais fácil, não mais difícil — e
  o critério (a) exige acerto em A, então o viés joga **a favor** de
  passar. Se falhar mesmo assim, o resultado é mais forte.
- **A wiki não mudou desde 07/08**, e isso será verificado por hash antes
  de rodar. Se tiver mudado, o gabarito não vale e o teste é anulado.

---

## 8. O que este teste NÃO decide

- **Não decide sobre o motor de navegação inteiro.** Decide sobre uma das
  três peças da camada de marcadores. Trust e Depth ficam sem teste.
- **Não decide que o Gap Score é impossível** — decide sobre *esta*
  fórmula, a que o plano escreveu.
- **Não substitui a condição C nem a WC** da rodagem cruzada, que seguem
  não rodadas.

---

## 9. Lint das órfãs — engenharia, não experimento

Separado de propósito, e **sem hipótese**: a regra 5 do `WIKI_SCHEMA.md`
já declara página órfã um defeito ("o lint pega"), e o lint não existe.
Medi em 11/08: **7 órfãs de 16** (44%), 4 sem link de saída, 0 links
quebrados.

Automatizar isso não testa nada — o número já está medido. É ~40 linhas
que fazem o schema parar de descrever um lint imaginário. Entra como
código, não como resultado.

Órfãs em 11/08, congeladas para comparação futura:
`exportador-e-85-por-cento-commodity`,
`contagem-de-nos-como-medida-de-vagueza`,
`compressao-zero-e-loops-abertos`, `key-assertion-truncado-em-80-chars`,
`ancora-de-tarefa-e-o-mecanismo-2-6e`, `como-os-grandes-fazem-memoria`,
`corpus-do-claude-code-esta-local`.

> Duas delas — `contagem-de-nos-como-medida-de-vagueza` e
> `compressao-zero-e-loops-abertos` — são páginas de núcleo/verificado com
> conteúdo forte e nenhuma entrada. Órfã não é sinônimo de fraca; o lint
> reporta, não rebaixa. Mesma lição de `aee20f9`.

---

## RESULTADO — 11/08/2026, `scripts/medir_gap_score.py`

Wiki inalterada desde 07/08 (§7 satisfeito). `sha256` do conjunto das 16
páginas: `27e4fe846fdc37fb`. Protocolo cumprido: `_RE_TOKEN` e
`_STOPWORDS` (170 palavras) importados de `edp.wiki`, sem alteração.
**Custo: US$ 0,00.**

| # | cl | g_bruto | g_idf | termos ausentes da cobertura |
|---|---|---|---|---|
| Q1 | A | 0,286 | 0,393 | rotear, testamos |
| Q2 | B | 0,429 | **0,000** | check, form, identificar |
| Q3 | B | 0,333 | **0,503** | `session_boost_factor` |
| Q4 | B | 0,571 | **0,000** | `edp_hybrid_retrieval`, flag, muda, tóxico |
| Q5 | B | 0,400 | **0,000** | mandar, segundos |
| Q6 | A | 0,500 | 0,451 | blob, dinâmica, perda |
| Q7 | B | 0,667 | 0,520 | boost, calibração, incidente, motivou |
| Q8 | B | 0,875 | 0,828 | competir, letta, longo, mem0, mudou, posição, zep |
| Q9 | B | 0,333 | 0,469 | 2026, agosto, mudou |
| Q10 | B | 0,286 | **0,000** | gratuitas, manter |
| Q11 | A | 0,500 | 0,465 | assumir, costumo, desenhar |
| Q12 | A | 0,333 | 0,193 | nelas, quais |
| N1 | ctrl | 0,250 | **0,000** | híbrido |
| N2 | ctrl | **0,000** | **0,000** | — (nenhum) |
| N3 | ctrl | **1,000** | **1,000** | discutiu, gente, lembra |

### Veredito: as duas FALHAM

| condição | bruta | idf |
|---|---|---|
| (a) ≥3/4 de A com gap < 0,5 | **2/4 — NÃO** | 4/4 — ok |
| (b) ≥7/8 de B com gap ≥ 0,5 | **3/8 — NÃO** | **3/8 — NÃO** |
| (c) Q3 ≥ 0,5 | **0,333 — NÃO** | 0,503 — ok |
| (d) N3 ≥ 0,5 | 1,000 — ok | 1,000 — ok |

## O placar da minha predição: conclusão certa, mecanismo errado

O §6 previu **"as duas falham, por Q3"**. As duas falharam. **Não por Q3.**

| previ | deu |
|---|---|
| G-bruto falha em (c) **e** (d) | falha em (c); **(d) passa com 1,000** — errei |
| G-idf falha em (c) | **passa em (c) com 0,503** — errei, por 0,003 |
| a causa é Q3 | a causa é **(b)**, em ambas: só 3 de 8 de B têm gap alto |

Errei também a nota do §1: escrevi que o filtro por status era "quase um
no-op". Foi **decisivo**. `session_boost_factor` aparece em uma única
página — `que-perguntas-fazer-a-uma-wiki-pessoal`, status `hipotese` —
que o filtro exclui. Minha previsão sobre Q3 assumia que a página do
exemplo estava na cobertura. Não estava. Q3 passou (c) por acidente de
filtro, não por mérito da fórmula.

**Quinto erro de predição consecutivo nesta frente.** Registrado.

## A causa real: R1 pela terceira vez

`idf.get(t, 0.0)` devolve **0,0 para termo que o corpus nunca viu**.
Verificado: `edp_hybrid_retrieval`, `mem0`, `zep`, `letta`, `gratuitas`,
`manter`, `flag`, `tóxico`, `muda`, `mandar` têm todos **df = 0** nas 16
páginas. Peso zero, some da conta — nem no numerador nem no denominador.

Consequência medida: **quanto mais estranha a pergunta é à wiki, MENOR o
gap por IDF.** Q4 pergunta por uma flag que não aparece em página nenhuma
e recebe `g_idf = 0,000` — a fórmula afirma cobertura total. Q2, Q5, Q10
e N1 caem pelo mesmo mecanismo.

Isso é **seletividade invertida — R1 — pela terceira vez** neste projeto:

1. gate por embedding (`dd06b87`)
2. primeira `buscar()` léxica (docstring do `_idf()`)
3. **Gap Score por IDF, aqui**

E a origem é uma regra que está **certa** onde nasceu. `edp/wiki.py:259`
diz: *"Termo que o acervo desconhece não pode ajudar nem atrapalhar: peso
0."* Correto para **ranquear** — não dá para ordenar documentos por um
termo que nenhum tem. Catastrófico para **medir lacuna**, onde o termo
nunca visto é o sinal inteiro.

## O segundo defeito, que não tem conserto barato

**N2 = 0,000 nas duas fórmulas.** A wiki "cobre integralmente" a capital
da Mongólia. Medido: `mongólia` aparece em **6 das 16 páginas** (três
delas núcleo) e `capital` em 2 — sempre como *exemplo de pergunta que não
precisa de corpus*, nunca como assunto.

É o defeito menção-vs-tratamento que o §5 nomeou antes de rodar. Ele é
real — só não apareceu em Q3, apareceu em **N2**. E ao contrário do
primeiro, não decorre de um default mal herdado: decorre de a fórmula
contar presença de token, sem qualquer noção de sobre o que a página é.

## O que isto decide

- **Decide:** as duas fórmulas do §2 falham o critério do §4. O Gap Score
  como especificado nos 11 prompts não sustenta o motor.
- **Decide:** a causa do primeiro defeito é mecânica, localizada e
  medida — não é "o conceito não presta".
- **Não decide:** se o conserto do `idf.get(t, 0.0)` faz passar. Testar
  isso agora, depois de ver o dado, seria ajuste de curva. Exige emenda
  pré-dado, na disciplina de E-1/E-2 da rodagem cruzada.
- **Não decide:** nada sobre menção-vs-tratamento, que N2 mostra vivo.

---

## EMENDA E-1 — 11/08/2026, PRÉ-DADO (a correção não rodou)

O resultado localizou o primeiro defeito num ponto: `idf.get(t, 0.0)`.
Consertar e re-testar **depois** de ver o dado é ajuste de curva, a menos
que a correção e a predição sejam declaradas antes. É o que esta emenda
faz. Mesma disciplina de E-1/E-2 da rodagem cruzada.

### E-1.1 — A correção, congelada

**G-idf⁰** — idêntica a G-idf, com uma diferença única:

```
antes:  idf.get(t, 0.0)              # termo nunca visto vale 0 e some
depois: idf.get(t, math.log(N+1))    # aplica log((N+1)/(df+1)) com df=0
```

Com N=16, um termo de `df = 0` passa a valer **2,833** — o maior peso
possível na escala. Não é fórmula nova: é a **mesma** fórmula
`log((N+1)/(df+1))` aplicada onde estava sendo pulada. O `0.0` veio de
`edp/wiki.py:259`, onde é correto (não se ranqueia por termo que ninguém
tem) e onde nunca precisou valer para ausência.

Nada mais muda: mesmo τ = 0,5, mesmas quatro condições do §4, mesmo
gabarito do §3, mesmos `_RE_TOKEN`/`_STOPWORDS` importados.

### E-1.2 — Predição pré-dado

Placar honesto: **cinco erros consecutivos** nesta frente (alvos do
fase0, Mongólia, recall da extração, bimodal, e o mecanismo deste teste).

Predigo que **G-idf⁰ passa em (b) e (c) e (d), e FALHA em (a)**.

Raciocínio declarado antes de rodar: o conserto dá peso máximo a
*qualquer* termo ausente do corpus — e o corpus são 16 páginas curtas,
que não usam a maior parte do português. `edp_hybrid_retrieval` ausente é
lacuna real; `costumo`, `assumir`, `desenhar`, `nelas` ausentes são
ruído, e têm `df = 0` igual. Q11 ("que tipo de **premissa costumo
assumir** sem verificar antes de **desenhar**?") é do conjunto A e tem
três termos assim. Q6 e Q1 têm dois cada.

Ou seja: predigo que a correção troca uma inversão por uma
**descalibração** — passa a marcar lacuna onde há resposta.

Se eu estiver certo, o achado é que o sinal útil não é *"termo não
visto"*, e sim *"termo não visto **e** específico"* — e o IDF não
distingue os dois, porque calcula sobre o mesmo corpus que não viu
nenhum dos dois.

### E-1.3 — O que a emenda NÃO tenta consertar

**Nada em N2.** `mongólia` está em 6 das 16 páginas, `capital` em 2 —
nenhum termo ausente. G-idf⁰ dará **exatamente 0,000** em N2, igual às
outras duas. O defeito menção-vs-tratamento não é tocado por esta
emenda, e nenhum resultado dela pode ser lido como evidência sobre ele.

### E-1.4 — Regra de parada

Se G-idf⁰ falhar, **acaba aqui**: três fórmulas, três falhas, e a
terceira falhando pelo motivo oposto à segunda é sinal de que o problema
não está no peso, e sim em contar token. Não haverá quarta fórmula sem
que algo externo mude — corpus maior, ou extração de assunto, que custa
LLM e sai do orçamento zero declarado no cabeçalho.

---

## RESULTADO E-1 — 11/08/2026

| # | cl | bruta | idf | **idf⁰** | ausentes |
|---|---|---|---|---|---|
| Q1 | A | 0,286 | 0,393 | **0,393** | rotear, testamos |
| Q2 | B | 0,429 | 0,000 | **0,569** | check, form, identificar |
| Q3 | B | 0,333 | 0,503 | **0,503** | `session_boost_factor` |
| Q4 | B | 0,571 | 0,000 | **0,720** | `edp_hybrid_retrieval`, flag, muda, tóxico |
| Q5 | B | 0,400 | 0,000 | **0,507** | mandar, segundos |
| Q6 | A | 0,500 | 0,451 | **0,578** | blob, dinâmica, perda |
| Q7 | B | 0,667 | 0,520 | **0,843** | boost, calibração, incidente, motivou |
| Q8 | B | 0,875 | 0,828 | **0,954** | competir, letta, mem0, posição, zep… |
| Q9 | B | 0,333 | 0,469 | **0,469** | 2026, agosto, mudou |
| Q10 | B | 0,286 | 0,000 | **0,396** | gratuitas, manter |
| Q11 | A | 0,500 | 0,465 | **0,591** | assumir, costumo, desenhar |
| Q12 | A | 0,333 | 0,193 | **0,387** | nelas, quais |
| N1 | ctrl | 0,250 | 0,000 | 0,478 | híbrido |
| N2 | ctrl | 0,000 | 0,000 | **0,000** | — nenhum |
| N3 | ctrl | 1,000 | 1,000 | **1,000** | discutiu, gente, lembra |

| condição | bruta | idf | **idf⁰** |
|---|---|---|---|
| (a) ≥3/4 de A com gap<0,5 | 2/4 ✗ | 4/4 ✓ | **2/4 ✗** |
| (b) ≥7/8 de B com gap≥0,5 | 3/8 ✗ | 3/8 ✗ | **6/8 ✗** |
| (c) Q3 ≥0,5 | 0,333 ✗ | 0,503 ✓ | **0,503 ✓** |
| (d) N3 ≥0,5 | 1,000 ✓ | 1,000 ✓ | **1,000 ✓** |
| **veredito** | FALHA | FALHA | **FALHA** |

### Placar da predição E-1.2: 3 de 4

Previ *"passa em (b), (c), (d) e falha em (a)"*.

- **(a) falha — acertei**, e pelos nomes que declarei: Q6 (0,451→0,578) e
  Q11 (0,465→0,591) cruzaram o limiar exatamente por termos como
  `costumo`, `assumir`, `desenhar`, `blob`, `perda`.
- **(b) errei.** Subiu de 3/8 para 6/8 e parou a **uma** pergunta do
  critério. Q9 (0,469) e Q10 (0,396) seguraram.
- (c) e (d) — acertei.

Primeira predição majoritariamente certa desta frente, depois de cinco
erradas. Registrado sem arredondar: **errei (b)**.

### E-1.3 confirmada: N2 = 0,000 nas três

Como declarado antes de rodar. Menção-vs-tratamento intocado.

## O achado: as três falham em lados OPOSTOS

| fórmula | peso do termo ausente | como falha |
|---|---|---|
| idf | 0,0 — some da conta | **conservadora demais**: (b) 3/8, quase nada vira lacuna |
| idf⁰ | 2,833 — o máximo | **agressiva demais**: (a) 2/4, resposta que existe vira lacuna |
| bruta | 1 termo = 1 termo | falha nos dois |

**É o mesmo botão.** O peso do termo ausente controla (a) e (b) em
direções opostas, e não há ajuste que satisfaça as duas — não porque o
valor certo não foi achado, mas porque a grandeza medida não separa os
dois conjuntos.

**Por quê, no dado:** `mem0` está ausente porque o assunto não está na
wiki. `costumo` está ausente porque a palavra não foi digitada. As duas
têm `df = 0`. Contagem de token não distingue **assunto ausente** de
**palavra não usada** — e 16 páginas curtas não usam a maior parte do
português, então o segundo caso domina.

### Ressalva contra mim mesmo

Q6 é do conjunto A porque a rodagem cruzada a respondeu — mas pela
página **`index.md`**, que o §1 deixou fora do corpus
(`edp_wiki/paginas/*.md`). Parte do gap alto de Q6 é defeito da minha
definição de corpus, não da fórmula. Não muda o veredito (Q11 sozinha já
derruba (a) na idf⁰), mas fica registrado.

## Regra de parada E-1.4: acionada

Três fórmulas, três falhas, a terceira pelo motivo oposto à segunda.
**Encerrado.** Não haverá quarta sem que algo externo mude.

O que sobrevive, medido:

1. **N3 = 1,000 nas três.** O filtro `len>2` + `_STOPWORDS` (170
   palavras), importado de `edp.wiki`, mata a consulta vaga por
   construção. **R1 não voltou pelo lado da consulta** — voltou pelo lado
   do peso, e agora está documentado nos dois.
2. **Q8 = 0,954** — separação limpa quando os termos ausentes são nomes
   próprios que a wiki nunca menciona.

### Hipótese registrada, NÃO testada

A diferença entre Q8 (0,954, acerto) e Q11 (0,591, erro) não é `df` —
ambos zero. É que `mem0`/`zep`/`letta`/`edp_hybrid_retrieval` **parecem
identificadores** e `costumo`/`assumir` são palavras comuns.

Testar isso agora seria a quarta fórmula depois de ver três resultados —
exatamente o que E-1.4 proíbe. Fica como **hipótese para pré-registro
futuro**, com corpus maior, e não como conclusão desta rodada.

---

## ERRATA — 11/08/2026, sobre o §0 e o §9 deste documento

Ao escrever `scripts/lint_wiki.py` descobri que **dois números que
publiquei acima estão errados**. Ficam onde estão (regra 3 do schema: o
que mudou fica, datado, não se sobrescreve); a correção é esta.

| onde | eu publiquei | o certo | fonte |
|---|---|---|---|
| §0, tabela da auditoria | "**7 páginas órfãs** (44%)" | **5 órfãs** (31%) | `scripts/lint_wiki.py` |
| §0 e §9 | "4 sem link de saída" | **1** (`sozinha` não existe; nenhuma página real está sem saída) | idem |
| §9 | "**0 links quebrados**" | **2 links quebrados** | idem |
| §0 | "17 arestas" | **29 arestas distintas** | idem |

**Causa única.** Contei arestas só pelos `[[...]]` do corpo e ignorei o
array `links:` do frontmatter — que carrega **28** das 29 arestas
distintas. Foi uma leitura parcial da estrutura, não um erro de conta: eu
olhei o formato do corpo e supus que fosse a única forma de aresta.

Os dois links quebrados que eu tinha reportado como zero:

- `r1-seletividade-invertida` → `[[blob-qa-comprime-faixa-dinamica]]`
- `r1-seletividade-invertida` → `[[honeypot-refutado]]`

Ambos declarados no frontmatter, ambos apontando para página que não
existe. É exatamente a classe de defeito que a regra 5 manda o lint
pegar, e eu havia declarado ausente.

**Isto não muda nenhum resultado do Gap Score** — a cobertura léxica é
sobre texto, não sobre arestas. Muda o §9, que era o meu argumento de
"defeito já medido, é só automatizar". Estava medido errado.

### Falso positivo da primeira versão do lint, também registrado

A v1 acusou `exportador-e-85-por-cento-commodity` e `fontes-conta-1` de
citarem arquivo inexistente. Eram `content.js`, `interceptor.js` (repo do
exportador) e `Downloads/.../_indice.json` (máquina do pesquisador) —
fontes legítimas, fora deste repositório. Minha regra tratava qualquer
token com ponto como caminho local.

Corrigido: só se checa caminho cujo primeiro segmento é entrada de topo
deste repo. As duas páginas voltaram a limpo, e
`tests/test_lint_wiki.py::test_fonte_de_fora_do_repo_nao_e_erro` trava a
correção.

Mesma família do `aee20f9`: regra aplicada larga demais derruba página
boa. Terceira vez que este projeto tropeça nisso — vale como padrão, não
como acidente.

---

## EMENDA E-2 — 11/08/2026, PRÉ-DADO (nenhuma chamada ao Haiku rodou)

A regra de parada E-1.4 admitia uma saída: *"extração de assunto, que
custa LLM"*. Esta emenda a executa. **Quebra o `US$ 0,00` do cabeçalho**
— custo declarado no E-2.7.

### E-2.0 — Q11 NÃO muda de lado. E aqui está a aritmética do porquê.

Foi proposto reclassificar Q11 de **A** para **B**, com o argumento de
que o teste deveria ser sobre *"existe página dedicada?"* e não sobre
*"existe material?"*. O argumento é **substantivamente razoável**. O
problema é o momento.

Q11 é uma das duas perguntas que derrubaram a condição (a) da idf⁰
(0,591 ≥ 0,5). Movê-la agora:

| | A | B |
|---|---|---|
| hoje | Q1 ✓, Q6 ✗, **Q11 ✗**, Q12 ✓ = **2/4** (exige 3) | 6/8 (exige 7) |
| com Q11 em B | Q1 ✓, Q6 ✗, Q12 ✓ = **2/3** | **7/9** |

Melhora os **dois** lados de uma vez, e obriga a reescrever os **dois**
limiares — porque "≥3 de 4" e "≥7 de 8" não existem mais. Sob
reescrita proporcional (75% e 87,5%) continua falhando; sob "≥2 de 3" e
"≥7 de 9", **passa**.

Ou seja: o veredito passaria a depender de uma escolha feita **depois**
de ver qual pergunta estragou o resultado. É a definição de ajuste de
curva, e é exatamente o que o §4 proíbe.

**Decisão: Q11 fica em A, como congelada no §3.**

O repositório já tem regra para pergunta mal formulada, e ela é
**anulação**, não reclassificação
(`preregistro_rodagem_cruzada_wiki.md` §7): *"é reportada como defeito
do instrumento e **anulada**, nunca substituída"*. Anular é neutro —
encolhe os dois denominadores. Reclassificar é direcional. A diferença
não é de rigor, é de aritmética.

**A leitura "página dedicada" fica registrada como pré-registro futuro
próprio**, com gabarito construído antes de ver o dado dele. Não entra
aqui de carona.

### E-2.1 — O que muda no desenho, e por quê

Fórmula se congela byte a byte. **Prompt, não.** Prompt + modelo +
temperatura devolvem uma distribuição, e toda a máquina do §4 pressupõe
determinismo. Por isso E-2 acrescenta uma quinta condição, sobre
estabilidade, **antes** de qualquer nota.

### E-2.2 — O prompt, congelado verbatim

**Sistema:**

```
Você recebe as páginas de uma wiki pessoal e UMA pergunta.

Decida uma coisa só: a wiki contém material suficiente para responder
a pergunta?

"Material suficiente" significa que alguém lendo estas páginas
conseguiria formular a resposta. NÃO exige que exista uma página
dedicada ao assunto — material espalhado por várias páginas conta.

NÃO conta como material:
- o termo da pergunta aparecer apenas como EXEMPLO de pergunta
- o termo aparecer só em lista, índice ou menção de passagem, sem o
  conteúdo que responde

Responda SOMENTE com uma linha, exatamente neste formato:
VEREDITO: SIM
ou
VEREDITO: NAO
```

**Usuário:**

```
=== PÁGINAS DA WIKI ===
{as 16 páginas íntegras, cada uma precedida de "--- <slug> ---"}

=== PERGUNTA ===
{texto da pergunta}
```

**Declaração sobre a cláusula "NÃO conta como material":** ela existe
porque é **exatamente a distinção que as três fórmulas não souberam
fazer** (Q3 e N2). Está aqui explicitamente, e não escondida, para que
seja auditável: se for removida, o teste vira outro e o resultado não é
comparável. Não é ajuda indevida — é a **especificação** do que se
entende por lacuna, e o que se mede é se o modelo a implementa.

**A pergunta feita é "há material?", não "há página dedicada?"** —
porque é essa que o gabarito congelado do §3 responde. As notas do W
foram escritas sobre a wiki *responder*, não sobre haver página com o
assunto no título.

### E-2.3 — Modelo e execução, congelados

| item | valor |
|---|---|
| modelo | `claude-haiku-4-5` — o default de `edp/llm_adapter.py:1447` |
| temperatura | **0.0** |
| rodadas por pergunta | **5** |
| ordem das páginas | alfabética por slug, fixa nas 5 rodadas |
| corpus | as mesmas 16 páginas, `sha256 27e4fe846fdc37fb` |
| parsing | linha `VEREDITO: SIM\|NAO`; qualquer outra saída = **resposta inválida**, contada como não-unânime |

Cinco rodadas, não três: com três, um desacordo 2×1 já é maioria simples
e a instabilidade some no arredondamento. Com cinco, ela aparece.

### E-2.4 — Condição (e), nova: estabilidade

**Unanimidade das 5 rodadas é exigida por pergunta.** Pergunta cujas 5
rodadas não concordarem é marcada **instável** e conta como
**não-acerto** no conjunto dela.

Sem voto de maioria. Um detector de lacuna que oscila é pior que um
consistentemente errado — contra o errado dá para calibrar, contra o
oscilante não.

**Porta dura:** se **menos de 12 das 15** forem unânimes, o instrumento
é declarado **não-determinístico** e a rodada inteira é **anulada**, não
"reprovada". Nota sobre ruído não é nota.

### E-2.5 — Critério (o do §4, mais (e))

`VEREDITO: SIM` = há material (equivale a gap baixo).
`VEREDITO: NAO` = não há (gap alto).

| # | condição |
|---|---|
| a | ≥ 3 das 4 de **A** com SIM |
| b | ≥ 7 das 8 de **B** com NAO |
| c | **Q3 → NAO** |
| d | **N3 → NAO** |
| **e** | **≥ 12 das 15 unânimes**; instável = não-acerto |

τ, conjuntos e gabarito **inalterados**. Passa só quem satisfizer as
cinco.

### E-2.6 — Predição pré-dado

Placar: cinco erradas, e a sexta (E-1.2) certa em 3 de 4.

Predigo: **passa em (b), (c), (d) e (e), e FALHA em (a)** — pelas mesmas
duas perguntas que derrubaram a idf⁰, e por motivo diferente:

- **Q6** ("de onde saiu o 47%?") — o resultado do W registra que quem
  respondeu foi **`index.md`**, que não está em `edp_wiki/paginas/`. O
  Haiku vai ler as 16 páginas e **não achar**, e vai responder `NAO`.
  Estará **certo sobre o corpus** e **errado contra o gabarito**. O
  defeito é da minha definição de corpus no §1, já registrado na
  ressalva do resultado E-1.
- **Q11** — o próprio gabarito diz *"padrão presente em várias páginas,
  **nenhuma o enuncia**"*. Predigo `NAO`.

Se eu acertar, o achado não é sobre o Haiku: é que **duas das quatro
perguntas do conjunto A estão mal ancoradas**, e nenhuma fórmula nem
modelo poderia acertá-las contra este gabarito. O limite deixa de ser do
método e passa a ser do gabarito — o que é conclusão diferente e mais
útil que "o Gap Score não funciona".

Predigo (e) em **15 de 15 unânimes**, com Q6 e Q11 como as candidatas a
oscilar, se alguma oscilar.

### E-2.7 — Custo, declarado

Medido: 16 páginas = 52.186 chars ≈ 13.046 tokens; ~13.4k por chamada.
75 chamadas ≈ **1,01M tokens de entrada**.

Preço de `edp/model_router.py:30` (`claude-haiku-4-5`, US$1,00/M in,
US$5,00/M out): **US$ 1,01 entrada + US$ 0,03 saída ≈ US$ 1,04.**

Primeiro gasto de API desta frente. O cabeçalho deste documento diz
`US$ 0,00`; passa a valer para E-0 e E-1 apenas.

### E-2.8 — O que E-2 não decide

- **Nada sobre "página dedicada"** — outra pergunta, outro gabarito,
  outro pré-registro.
- **Nada sobre custo em produção.** Mede 15 perguntas contra 16 páginas.
  Wiki maior não cabe no contexto e exigiria recuperação antes — que é
  o problema original, de volta.
- **Nada sobre outro modelo.** Congelado em `claude-haiku-4-5`.
- **Regra de parada mantida:** se E-2 falhar por (e), a rodada é anulada
  e cabe **uma** repetição, com o mesmo prompt. Se falhar por (a)–(d),
  acaba — sem sexta fórmula e sem segundo prompt.

---

## RESULTADO E-2 — 11/08/2026, `scripts/medir_gap_score_haiku.py`

Corpus com `sha256 27e4fe846fdc37fb`, igual ao congelado em E-2.3.
`claude-haiku-4-5`, temperatura 0.0, 5 rodadas por pergunta, 75 chamadas.
**Custo real: US$ 1,4555** — 40% acima dos US$ 1,04 de E-2.7. A
estimativa usou 4 chars/token e subestimou; fica registrado.

| # | classe | veredito (unânime nas 5) | esperado |
|---|---|---|---|
| Q1 | A | **SIM** | SIM ✓ |
| Q2 | B | **NAO** | NAO ✓ |
| Q3 | B | **NAO** | NAO ✓ |
| Q4 | B | **NAO** | NAO ✓ |
| Q5 | B | **NAO** | NAO ✓ |
| Q6 | A | **NAO** | SIM ✗ |
| Q7 | B | **SIM** | NAO ✗ |
| Q8 | B | **NAO** | NAO ✓ |
| Q9 | B | **SIM** | NAO ✗ |
| Q10 | B | **NAO** | NAO ✓ |
| Q11 | A | **SIM** | SIM ✓ |
| Q12 | A | **SIM** | SIM ✓ |
| N1 | ctrl | SIM | fora do critério |
| N2 | ctrl | NAO | fora do critério |
| N3 | ctrl | **NAO** | NAO ✓ |

| condição | resultado |
|---|---|
| (a) ≥3/4 de A com SIM | **3/4 — OK** |
| (b) ≥7/8 de B com NAO | **6/8 — NÃO** |
| (c) Q3 → NAO | OK |
| (d) N3 → NAO | OK |
| (e) ≥12/15 unânimes | **15/15 — OK** |
| **veredito** | **FALHA** |

## Placar da predição E-2.6: 3 de 5, e as duas erradas são as que importam

Previ: *"passa em (b), (c), (d) e (e), e FALHA em (a), por Q6 e Q11."*

| previ | deu |
|---|---|
| **(a) falha** | **passou (3/4)** — errei |
| **(b) passa** | **falhou (6/8)** — errei |
| (c), (d) passam | passaram — acertei |
| (e) 15/15 unânimes | 15/15 — acertei, no número |
| Q6 → NAO | **NAO** — acertei |
| Q11 → NAO | **SIM** — errei |

**Inverti as duas condições de contagem.** Acertei que Q6 cairia e
errei Q11; errei qual lado do critério quebraria. Sétima predição desta
frente: cinco erradas, uma certa em 3/4, esta certa em 3/5 com o
essencial invertido.

### A recusa de E-2.0 se sustentou no dado

Q11 foi a pergunta que eu me recusei a mover de A para B. O Haiku
respondeu **SIM** — coincidindo com o gabarito congelado. Se eu tivesse
aceitado a reclassificação, esse SIM contaria como **erro** em B, e (b)
teria ido a **6/9** em vez de 6/8. **Pior.**

A recusa não foi conservadorismo: a mudança teria degradado o resultado
que ela supostamente ajudaria. Congelar não é cerimônia.

## O achado: (b) falha nos QUATRO métodos

| método | (a) A | (b) B | (c) Q3 | (d) N3 | estável |
|---|---|---|---|---|---|
| bruta | 2/4 ✗ | **3/8 ✗** | ✗ | ✓ | — |
| idf | 4/4 ✓ | **3/8 ✗** | ✓ | ✓ | — |
| idf⁰ | 2/4 ✗ | **6/8 ✗** | ✓ | ✓ | — |
| **haiku** | **3/4 ✓** | **6/8 ✗** | ✓ | ✓ | **15/15** |

O Haiku é o **melhor dos quatro** — primeiro a passar (a), empata o
melhor (b), passa (c) e (d), e é perfeitamente estável. E falha.

Quatro mecanismos sem nada em comum — contagem crua, IDF, IDF com peso
máximo para termo não visto, e um modelo lendo o texto — e **nenhum
chega a 7/8 em (b)**. Isso deixa de ser propriedade do método.

**Q9 falha nos dois melhores** (idf⁰ 0,469 e Haiku SIM). É a pergunta
*"o que mudou na definição do EDP entre abril e agosto?"* — pede uma
**comparação diacrônica**. A wiki tem páginas sobre o que o EDP é; não
tem o registro da mudança. Contador de termo vê material; leitor vê
material. O gabarito diz 0 porque a *síntese* não está lá.

E é exatamente a mesma situação estrutural de **Q11** — material
presente, síntese ausente — que o gabarito classificou como **A**.
Mesmo formato, lados opostos do gabarito.

### O modo de falha do Haiku, nomeado

Erra por **excesso**, e só em pergunta aberta: Q7 (proveniência,
"qual incidente motivou") e Q9 (evolução). Vê material relacionado e
julga suficiente. Nas sete perguntas de decisão/refutação de B, acertou
todas. Nenhum erro por omissão.

## O que isto decide

- **Decide:** o Gap Score não passa o critério do §4 nem com LLM. Quatro
  métodos, quatro falhas.
- **Decide:** a falha não é mais atribuível ao método. Converge em (b),
  e Q9 cai nos dois melhores por razões diferentes.
- **Decide:** instabilidade **não** é o problema. 15/15 unânimes a
  temperatura 0 — a condição (e), criada para pegar isso, veio limpa.
- **Não decide** que o gabarito está errado. Decide que **ele é onde
  olhar**, o que é diferente — e exige pré-registro próprio, com o
  critério de reclassificação declarado antes, não depois. E-2.0 é o
  precedente de por quê.

---

## ADENDO AO RESULTADO E-2 — auditoria da saída crua, 11/08

O JSON bruto expôs três coisas que a tabela de vereditos esconde. Todas
enfraquecem a leitura de (a), e nenhuma muda o veredito FALHA.

### 1. O modelo não obedeceu o formato — 8 de 15

O prompt de E-2.2 diz *"Responda SOMENTE com uma linha"*. Em 8 das 15
perguntas o modelo emendou justificativa, cortada pelo `max_tokens=32`.
O parser lê a primeira linha e funcionou; a medição está intacta. Mas a
instrução foi desobedecida em mais da metade dos casos, e isso fica
registrado como desvio, não como detalhe.

### 2. (a) passou por um caminho que o prompt proibiu

`Q11` respondeu **SIM** — e a justificativa começa citando
`[[que-perguntas-fazer-a-uma-wiki-pessoal]]`, que é a página onde as
perguntas aparecem **como exemplo**. O prompt diz, textualmente:

> *"NÃO conta como material: o termo da pergunta aparecer apenas como
> EXEMPLO de pergunta"*

`Q7` — uma das duas falhas de (b) — cita **a mesma página**, pelo mesmo
caminho.

Ou seja: a mesma violação da regra produziu a resposta que **bate** com
o gabarito em Q11 e a que **erra** em Q7. E Q11 é justamente a pergunta
que fez (a) passar em 3/4.

**Não afirmo que o raciocínio foi inválido** — a justificativa foi
truncada em 32 tokens e não dá para ler o resto. Afirmo o que se pode
afirmar: **(a) não é passagem limpa, e com este dado não há como
verificar se é.** O flag fica; a nota, não.

Isso não altera o veredito. E-2 falhou por (b), com ou sem Q11.

### 3. Defeito do meu desenho: o prompt força resposta-antes-de-pensar

O modelo emite `VEREDITO:` na **primeira** linha e só então justifica.
Ou seja, o veredito é produzido **sem** estar condicionado ao raciocínio
— o inverso de cadeia de pensamento, e o modo reconhecidamente mais
fraco para tarefa de julgamento.

Isso é escolha minha em E-2.2, não limitação do modelo. E-2.8 proíbe
segundo prompt, então **não conserto aqui**: fica como defeito
declarado do instrumento e como candidato a pré-registro futuro
("veredito depois do raciocínio, mesmo gabarito"), com o critério
congelado antes.

### O que o adendo muda

| antes do adendo | depois |
|---|---|
| (a) OK, 3/4 | (a) OK **com ressalva** — Q11 chegou lá por caminho proibido |
| Haiku é o melhor dos quatro | continua sendo — mas a margem em (a) é menos sólida do que o número sugere |
| veredito FALHA | **inalterado** — (b) cai independentemente |

E reforça o achado central em vez de enfraquecê-lo: mesmo com (a)
possivelmente inflada, (b) falhou nos quatro métodos.

---

## EMENDA E-3 — 11/08/2026, PRÉ-DADO (nenhum navegador foi escrito)

Depois que as quatro medições de cobertura falharam, surgiu — do
pesquisador, observando um pé de manga — a hipótese de que lacuna não é
grandeza a calcular sobre o corpus, e sim **evento de navegação**: o
ponto em que a propagação de uma tarefa deixa de ser sustentada pela
estrutura existente. Esta emenda congela o que dá para congelar e
registra o que bloqueia.

### E-3.1 — Modelo do grafo: DIRIGIDO, congelado

Aresta = união de `links:` do frontmatter com `[[...]]` do corpo,
**seguida só no sentido declarado**. Link de wiki é dirigido: "A cita B"
não implica que B conheça A. Seguir backlink é suposição extra e exigiria
teste próprio.

Medido em 11/08 sobre as 16 páginas (`sha256 27e4fe846fdc37fb`):
**27 arestas dirigidas válidas**, grau de saída médio **1,69**.

### E-3.2 — Pré-condição de alcance (critério declarado ANTES de medir)

> Navegação só carrega informação além da leitura global se, de um ponto
> de entrada típico, a BFS em profundidade ≤3 alcançar **entre 3 e 12**
> dos 16 nós. Abaixo de 3 não há caminho — é ler uma página. Acima de 12
> não há discriminação — é ler o acervo.

| modelo | mediana | min | max | fora de [3,12] | |
|---|---|---|---|---|---|
| **dirigido** | **4,5** | 2 | 8 | 2/16 | **PASSA** |
| não-dirigido | 11,5 | 6 | 16 | 7/16 | passa raspando |

O dirigido passa com folga e discrimina: 28% do acervo por entrada. O
não-dirigido alcança 72%, com dois pontos chegando ao grafo inteiro —
quase "ler tudo com passos no meio". Mais uma razão para congelar
dirigido.

**Pré-condição satisfeita ≠ hipótese comprovada.** Isto responde
"existe vizinhança pequena o bastante para experimentar?". Não responde
"essa vizinhança contém o caminho necessário?".

### E-3.3 — O BLOQUEIO: não existe aresta tipada

A hipótese exige decidir, a cada passo, se **a relação** representada
pela aresta satisfaz a necessidade corrente. Medido em 11/08:

- **0 de 16** páginas marcam tipo de relação. `links:` é lista de slugs
  puros: `links: ["compressao-zero-e-loops-abertos"]`.
- **`docs/WIKI_SCHEMA.md` não define vocabulário de tipo de aresta.**

Consequência: `capacidade_de_satisfação(transição, necessidade)` não tem
o que ler além do **conteúdo da página-alvo** — que é leitura lexical,
agora **uma por passo** em vez de uma por consulta. O defeito que matou
as três fórmulas voltaria distribuído pelo laço, mais difícil de ver.

**Portanto o navegador NÃO é implementável hoje**, e o bloqueio não é de
desenho: falta uma peça do schema.

### E-3.4 — O que fica registrado como não resolvido

1. **`necessidade` e `capacidade_de_satisfação` são indefinidos.** A
   formulação `Need ≠ ∅ ∧ ∀t: cap(t,Need)=0 → GAP`, com `cap`
   indefinido, diz "há lacuna quando nada funciona" — verdadeira por
   construção, infalsificável. Mesma forma do critério mole que este
   documento já rejeitou uma vez.
2. **Histórico de uso de aresta conflita com o §7.2 do schema**
   (*"nenhuma métrica interna promove nada"*). Registrar como evento em
   vez de peso não desfaz o laço de autoconfirmação.
3. **A analogia do dreno pressupõe vasculatura.** Grau 1,69 e 5 órfãs
   não são rede de transporte.

### E-3.5 — Decisões congeladas sobre o corpus

- **NÃO conectar as 5 órfãs.** Adicionar aresta para o navegador
  funcionar é alterar o corpus a favor do experimento — o simétrico do
  que E-2.0 recusou em Q11. Órfã é dado, não defeito a corrigir:
  `contagem-de-nos-como-medida-de-vagueza` é núcleo **e** órfã.
- **SIM corrigir os 2 links quebrados** — são arestas declaradas
  apontando para o nada, promessa falsa, não conhecimento ausente. Criar
  as páginas ou remover os links: escolha feita **cega às 15 perguntas**.
- **NÃO implementar RWR, PageRank ou MCTS.** Grau 1,69 e alcance 4,5 não
  sustentam propagação probabilística. RWR *é* o personalized PageRank —
  um algoritmo listado como dois. MCTS a profundidade 3 sobre 16 nós é
  enumerável por BFS exaustivo.
- **Nome: `Gap Event`, não `Gap Score 2.0`.** Inventar o número antes de
  entender o fenômeno foi como esta frente começou.

### ERRATA de medição — 11/08

A segunda rodada de alcance deu mediana 4,0 no não-dirigido, contra 11,5
da primeira. **A segunda estava errada:** filtrei os links contra o
conjunto `slugs` **enquanto ele ainda estava sendo preenchido** no mesmo
laço, descartando toda referência a página posterior na ordem
alfabética. Os números válidos são os do E-3.2.

Terceiro erro desta família hoje — 7 órfãs (eram 5), 0 links quebrados
(eram 2), e este. Os três são **ler parte da estrutura e tratar como o
todo**, que é literalmente o defeito sob investigação. O código que mede
precisa ser mais rigoroso que o objeto medido; hoje não foi.

---

## NOTA DE EXECUÇÃO — item 1 do E-3.5, 11/08/2026

Os 2 links quebrados de `r1-seletividade-invertida` foram **removidos**,
não convertidos em páginas.

**Argumento neutro, que decide sozinho:** `medir_alcance_wiki.py` já não
contava alvo inexistente como aresta — ia para `quebrados`. Remover a
declaração não move nenhum número congelado em E-3.2. Verificado depois
da edição: **27 arestas válidas, mediana 4,5, min 2, max 8, 2/16 fora da
faixa** — idênticos. O lint foi de **2 erros para 0**.

**Segundo motivo, e declaro que ele não é cego:** ao olhar os alvos,
notei que `blob-qa-comprime-faixa-dinamica` é o achado dos 47%, que é o
assunto de **Q6** — pergunta do conjunto A. Criar essa página injetaria
conteúdo exatamente sobre uma pergunta do teste. O argumento neutro já
bastava; este aponta para o mesmo lado e fica registrado por honestidade,
não como justificativa.

A emenda ficou no corpo da própria página (regra 3 do schema), já que
`edp_wiki/` é gitignored e não há registro em git da edição.

---

## RESSALVA A E-3.2 — a faixa [3,12] não é calibração

Registrado em 11/08, depois de a faixa já ter sido usada.

**A faixa não tem derivação externa.** "Abaixo de 3 é ler uma página,
acima de 12 é ler o acervo" é raciocínio razoável, mas 3 e 12 saíram de
intuição minha, não de calibração contra nada. O que a salva de ser
ajuste é ter sido declarada **antes** de medir — não é tunada ao
resultado. Mas não é constante do sistema.

**Ela depende do tamanho do corpus.** 12 é 75% de 16. Com 100 páginas,
"12" significaria 12% e a faixa passaria a testar outra coisa. Portanto:
**revalidar quando o corpus crescer**, com nova declaração pré-dado. Não
tratar como parâmetro permanente.

**E N=16 é instável para qualquer limiar fixo.** No dia 11/08 a mediana
de alcance assumiu 4,0 / 4,5 / 11,5 conforme detalhe de implementação —
dois desses valores eram bug meu, mas a lição vale: com 16 nós, decisão
binária em cima de uma mediana é frágil por construção.

---

## RISCO DECLARADO PARA O ITEM 2 (definir `necessidade`), antes de escrevê-lo

Foi sugerido, como primeira versão mínima:

> `necessidade = (conceito, tipo de pergunta: definição / origem /
> evolução / contradição)`

**Essas quatro categorias são quase a taxonomia do próprio gabarito.**
`docs/preregistro_rodagem_cruzada_wiki.md` §4 classifica as 12 perguntas
em: `a—decisão`, `b—refutado`, `c—evolução`, `d—contradição`,
`e—proveniência`, `f—padrão`. E aquela taxonomia foi escrita **olhando
as perguntas**.

Definir o espaço de tipos de `necessidade` a partir dela é moldar o
predicado pelo conjunto de teste — o mesmo defeito que E-2.0 recusou em
Q11, um nível abaixo e mais difícil de ver, porque não muda nenhuma
pergunta de lado: apenas garante que o predicado tenha uma categoria
para cada coisa que o teste pergunta.

**Não estou dizendo que a sugestão está errada.** Estou registrando que,
se as categorias vierem daquela lista, o resultado não pode ser lido
como evidência de que o predicado generaliza — só de que ele cobre este
gabarito. Se vierem de outro lugar (uma taxonomia de relação semântica
publicada, ou derivadas do schema sem olhar as perguntas), a leitura é
mais forte.

Quem escrever o item 2 deveria declarar **de onde as categorias vieram**,
antes de aplicá-las.

---

## ADENDO À NOTA DE EXECUÇÃO — quatro coisas que faltaram, 11/08

### 1. A remoção não era neutra para a pergunta de pesquisa

Provei neutralidade para as **métricas agregadas** de E-3.2 e tratei isso
como neutralidade, ponto. Não é.

Aresta declarada apontando para página nunca escrita **é uma instância do
fenômeno que o `Gap Event` quer formalizar** — a estrutura prometeu uma
transição e não entregou. Apaguei dois casos reais antes de o detector
existir.

Corrigido: `docs/arestas_removidas.md`, registro append-only com origem,
alvo pretendido, assunto e data. Quando `capacidade_de_satisfação()`
existir, são casos de calibração com rótulo conhecido. Remover do
frontmatter e descartar o dado deixam de ser a mesma operação.

### 2. A edição não tem diff — o hash passa a ser a trilha

`edp_wiki/` é gitignored: a edição que mudou o grafo não tem histórico
versionado, e a evidência de que foi *essa* edição dependia de prosa.

| momento | `sha256` do corpus (16 páginas) |
|---|---|
| E-2.3 / E-3, todas as medições até 11/08 | `27e4fe846fdc37fb` |
| depois da remoção dos 2 links quebrados | **`256ea486246673ca`** |

**Consequência verificada:** `scripts/medir_gap_score_haiku.py` agora
**aborta** — o guard de E-2.3 dispara e anula a rodada. Correto: o
gabarito do E-2 foi medido contra o estado antigo, e re-rodar contra o
novo mediria outra coisa. Quem quiser reproduzir E-2 precisa do corpus em
`27e4fe846fdc37fb`.

**Regra daqui em diante:** toda edição em `edp_wiki/paginas/` loga o hash
resultante aqui.

### 3. A faixa vira proporcional, e a troca é provada não-tunante

A ressalva anterior dizia "revalidar quando o corpus crescer" — instrução
sem gatilho, que se perde. Substituída por faixa que escala sozinha:

```
faixa(N) = ( max(2, 0.20·N) , 0.75·N )
```

O piso tem mínimo absoluto **2**: alcançar só a si mesmo é ausência de
navegação por definição, e 20% de corpus pequeno afundaria abaixo disso.

**Trocar critério depois de ver resultado só é legítimo se o veredito
congelado não mudar.** Verificado:

| leitura | mediana | faixa antiga [3,12] | faixa nova [3.2,12.0] |
|---|---|---|---|
| dirigido | 4,5 | PASSA | **PASSA** |
| não-dirigido | 11,5 | PASSA | **PASSA** |

Idênticos. A única diferença é estatística secundária: `fora da faixa` no
dirigido foi de 2/16 para 3/16, porque o piso subiu de 3 para 3,2. Mesma
disciplina do flag-off byte-idêntico do resto do repo. Travado por
`tests/test_medicao_wiki.py::test_faixa_reproduz_o_veredito_congelado_em_16_nos`.

### 4. Item 2: de onde as categorias devem vir

Eu tinha exigido "declarar de onde vieram" sem dizer de onde **deveriam**
vir. Fica registrada a saída concreta:

**adotar um vocabulário de tipo de relação publicado fora deste
projeto**, e só depois mapear as 27 arestas nele. O campo de *citation
typing* tem ontologias desenhadas sem nenhum conhecimento deste corpus
nem deste gabarito — a **CiTO** (Citation Typing Ontology, das SPAR
ontologies) é a referência mais direta, e a literatura de
Zettelkasten/wiki-linking tem vocabulários equivalentes.

Isso quebra o vínculo com a taxonomia da rodagem cruzada **sem** exigir
que apareça alguém não-contaminado para fazer o trabalho.

**Ressalva sobre esta recomendação:** conheço a existência e o propósito
da CiTO, não a lista exata de propriedades dela. O vocabulário tem de ser
puxado da fonte, não da minha memória — citar termos de cabeça aqui seria
o mesmo defeito que passei o dia auditando nas colagens.

### 5. Contaminação minha, com peso consistente

Recusei escrever o item 2 por conhecer o gabarito, mas para a observação
de Q6 apenas declarei "não é cego" e segui. Mesmo princípio, pesos
diferentes.

Não muda a decisão sobre os links — o argumento neutro bastava sozinho.
Fica registrado que **qualquer edição futura que toque
`r1-seletividade-invertida` ou o assunto de Q6 já não é cega para mim**,
do mesmo modo que o item 2 está marcado como não-executável por quem viu
o teste.
