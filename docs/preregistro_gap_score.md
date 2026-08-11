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
