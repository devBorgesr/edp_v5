# PRE_REGISTRO — Rodagem cruzada da wiki (3 corpora × 12 perguntas)

Contrato: `NORTE.md@5fb4402` §4. Escrito em **07/08/2026**, **ANTES** de
compilar qualquer página das conversas.

**Este arquivo mora em `docs/`, não em `edp_wiki/`, de propósito.** A wiki
é o artefato sob teste; se o conjunto de perguntas vivesse dentro dela, eu
compilaria páginas sabendo o que seria perguntado e o teste viraria
encenação. Fica versionado e fora do objeto medido.

---

## 1. A pergunta

Consultar uma wiki compilada responde melhor que `grep` no corpus cru — e
isso depende de qual corpus?

## 2. Os três corpora

| id | corpus | tamanho |
|---|---|---|
| **W** | 3 contas web | 107 conversas · 6.739 turnos · 55 c/ thinking |
| **C** | sessões do Claude Code | 40 sessões · 11.728 mensagens · 48 MB |
| **WC** | ambos | soma dos dois |

## 3. O critério que gerou as perguntas

De `edp_wiki/paginas/que-perguntas-fazer-a-uma-wiki-pessoal.md`: uma boa
pergunta **nomeia algo específico** e **pede algo que só o corpus tem**.

Consequência já registrada: parte do pool `[N]` do EXP017 usado nesta
sessão inteira falha no segundo critério — "como funciona o RRF" e
"capital da Mongólia" um modelo responde sem corpus. Elas entram aqui
como **controle negativo**, não como alvo.

## 4. As 12 perguntas (congeladas)

`conhecida?` = eu já sei a resposta agora, por ter vivido esta sessão.
**Pergunta conhecida é teste fraco** — eu poderia compilar a página que a
responde. As marcadas `não` são o teste forte: vêm de partes do corpus que
eu nunca li (as outras 24 sessões de Code, as 3 contas web).

| # | tipo | pergunta | vencedor previsto | conhecida? |
|---|---|---|---|---|
| Q1 | b — refutado | Já testamos rotear recuperação por similaridade de embedding? O que o dado mostrou? | C | **sim** |
| Q2 | b — refutado | O que foi tentado antes do form-check `Q:`/`A:` para identificar um turno de conversa, e por que falhou? | C | não |
| Q3 | a — decisão | Por que `SESSION_BOOST_FACTOR` vale 1.60 e não outro valor? | C | parcial |
| Q4 | a — decisão | Por que existe a flag `EDP_HYBRID_RETRIEVAL` e o que ela muda no piso de conteúdo tóxico? | C | não |
| Q5 | a — decisão | Por que o exportador passou a mandar timestamp em segundos? | C | **sim** |
| Q6 | e — proveniência | De onde saiu o número de 47% de perda de faixa dinâmica do blob `Q+A`? | C | **sim** |
| Q7 | e — proveniência | Qual incidente real motivou a calibração do boost de sessão? | C | não |
| Q8 | c — evolução | Como a posição sobre "competir com Mem0/Zep/Letta" mudou ao longo do tempo? | W | não |
| Q9 | c — evolução | O que mudou na definição do que o EDP é, entre abril e agosto de 2026? | W | não |
| Q10 | d — contradição | Há afirmações opostas registradas sobre o valor de manter contas gratuitas? | W | parcial |
| Q11 | f — padrão | Que tipo de premissa costumo assumir sem verificar antes de desenhar? | WC | parcial |
| Q12 | f — padrão | Quais predições foram registradas e depois refutadas? Há padrão nelas? | WC | **sim** |

### Controles negativos (a wiki deve RECUSAR, não responder)

| # | pergunta | comportamento correto |
|---|---|---|
| N1 | Como funciona o RRF no retrieval híbrido? | reconhecer que não precisa do corpus |
| N2 | Qual é a capital da Mongólia? | idem |
| N3 | Me lembra o que a gente discutiu | recusar por falta de âncora (R1) |

## 5. Como se pontua

Por pergunta, em cada condição, **julgado pelo pesquisador**:

| nota | critério |
|---|---|
| **2** | responde certo **e** cita fonte rastreável (`conv:<uuid>#t<n>` ou `sessão:<uuid>`) |
| **1** | responde certo mas sem fonte, ou parcialmente |
| **0** | não responde, ou responde errado |

Controles negativos invertem: **2** se recusa corretamente, **0** se
inventa resposta a partir do corpus.

**Baseline obrigatório:** a mesma pergunta respondida por `grep` no corpus
cru, pontuada pela mesma régua. A wiki só vale se **superar** o grep —
empatar não basta, porque compilar custa e grep não.

## 6. Hipóteses

- **H1 (utilidade)** — a wiki supera o grep em **≥7 das 12** perguntas,
  em ao menos uma condição.
- **H2 (especialização)** — o vencedor previsto na tabela do §4 acerta em
  **≥8 das 12**. Se errar mais que isso, a taxonomia de tipos não descreve
  o comportamento real e cai.
- **H3 (diluição)** — **WC não vence sozinho** em Q1–Q7 (decisão,
  refutação, proveniência). Mais fonte não é mais sinal quando 2/3 do
  corpus web é fora do escopo.
- **H0** — qualquer uma falha.

**Predição do arquiteto:** H1 sobrevive; H2 sobrevive por pouco; H3
sobrevive. Registro que **errei as três predições anteriores desta
frente** — pool do fase0 (previ 2-3, deu 1), Mongólia ausente (estava,
8x), recall da extração (previ 4-5 alvos, deu 2).

## 7. Ordem de execução (a que o pesquisador pediu)

1. **W** sozinho — compilar do corpus web, rodar as 15 perguntas
2. **C** sozinho — compilar do Code, rodar as mesmas
3. **WC** — ambos, rodar as mesmas

Congelado: **as perguntas não mudam entre condições**, e nenhuma é
acrescentada depois de ver resultado. Se uma se revelar mal formulada,
isso é reportado como defeito do instrumento e ela é **anulada**, nunca
substituída.

## 8. Riscos declarados

- **Viés de compilação.** Eu já sei a resposta de 4 perguntas (Q1, Q5,
  Q6, Q12). Elas são teste fraco e estão marcadas. Se a wiki só vencer
  nessas, o resultado é nulo.
- **Custo não medido.** Compilar as três condições exige LLM. O §7 do
  design (`docs/design_wiki_conversas.md`) tinha custo estimado que a
  errata invalidou. Medir na condição W antes de seguir para C.
- **Julgamento humano único.** Sem segundo avaliador não há como medir
  concordância. Limitação aceita, registrada.

---

## EMENDA E-1 — 07/08/2026, PRÉ-DADO (nenhuma comparação rodou)

Duas lacunas no texto acima, ambas minhas, achadas ao começar a condição W.

### E-1.1 — Faltava critério de conclusão por condição

O §7 manda "compilar do corpus e rodar as perguntas" sem dizer **quanto**
compilar. Sem isso, a comparação entre W, C e WC mede **esforço de
compilação**, não qualidade de corpus — quem eu compilar mais, vence.

**Orçamento congelado, igual nas três condições:**

> **~200 turnos/mensagens de fonte por condição**, escolhidos por
> relevância às 12 perguntas do §4. O critério de escolha é declarado
> antes de ler: densidade de thinking × proximidade ao assunto da
> pergunta. Compilar além do orçamento invalida a comparação.

Segurar o esforço constante e variar o corpus é o que torna isto um
teste controlado. 200 é o que cabe em uma a duas sessões por condição —
número prático, não derivado de teoria, e declarado como tal.

**Estado atual do W:** 1 conversa, **7 turnos** (`conv:8c2ef23e`).
Restam ~193 de orçamento.

### E-1.2 — Páginas anteriores às condições contaminam a nota

Das 10 páginas da wiki hoje, **9 não vêm de W nem de C**: saíram desta
sessão de trabalho, do código do graphify, dos índices de export. Só
`mercado-de-auditoria-de-rag` veio da condição W.

Elas respondem perguntas do §4 — `r1-seletividade-invertida` responde Q1,
`metodo-llm-wiki-lido-de-segunda-mao` alimenta Q12 — e estariam presentes
nas três condições igualmente.

**Regra congelada:** ao pontuar, cada resposta registra **de qual página
veio**, e a página é classificada em `base` (anterior às condições) ou
`W`/`C`. Duas notas são reportadas:

- **bruta** — o que a wiki entrega ao usuário, inclusive `base`
- **diferencial** — só o que veio das páginas da condição

**A comparação entre W, C e WC usa a DIFERENCIAL.** A bruta serve para
saber se a wiki é útil; a diferencial, para saber qual corpus a alimenta.

Se a nota bruta for alta e a diferencial ~zero, a conclusão é que o valor
veio de compilar a sessão de trabalho, não de compilar conversas — que é
um achado, não um fracasso.

---

## RESULTADO PARCIAL — condição W, 07/08/2026

**Orçamento usado: 99 de ~200 turnos** (E-1.1). Parei abaixo do teto por
limite de contexto da sessão, não por ter esgotado material.

Fontes compiladas (4 conversas):

| fonte | turnos | páginas geradas |
|---|---|---|
| `conv:8c2ef23e` Monetizar avaliação de RAG | 7 | `mercado-de-auditoria-de-rag` |
| `conv:7ef32b9e` Originalidade e ferramentas similares | 68 | `exportador-e-85-por-cento-commodity`, `key-assertion-truncado-em-80-chars` |
| `conv:ac7e0a89` Validação de memória persistente | 14 | `compressao-zero-e-loops-abertos` (parcial) |
| `conv:adcdbb9e` Memória e tokenização em IA | 10 | `como-os-grandes-fazem-memoria`, `compressao-zero-e-loops-abertos` (parcial) |

**5 páginas** classificadas como `W`. As outras 9 seguem `base`.

**Limitação declarada:** `opus copiloto principal` (3.748 turnos, **56% do
corpus W**) não foi compilado — estouraria o orçamento em 18×. A maior
fonte da condição ficou de fora por regra.

**Custo de API: zero.** Compilação é leitura e escrita em sessão. Isto
mede o item "custo não medido" do §8 e o resolve: o recurso escasso é
contexto, não dólar.

---

## EMENDA E-2 — fatia da condição C, congelada 07/08 ANTES de ler

### E-2.1 — Teto real, não nominal

O W usou **99** turnos, não 200. Congelado: **C usa no máximo 99
mensagens de fonte**. Comparar 99 contra 200 mediria esforço, que é o que
a E-1.1 existe para impedir.

### E-2.2 — Mesmo método de seleção nos dois

**Risco identificado antes de agir:** escolher as sessões do Code
grepando pelos termos de Q2–Q7 entregaria as respostas de bandeja ao C,
enquanto o W foi escolhido só por metadado (turnos × densidade de
thinking), sem grep por termo-alvo. Isso enviesaria a comparação.

**Congelado: seleção por metadado apenas.** Nenhum grep por termo das
perguntas antes de escolher. Critério, na ordem:

1. projeto `-media-sf-edp-v5-main` — é o assunto das perguntas
2. maior número de mensagens (proxy de substância, análogo a "turnos" no W)
3. **excluir `3c8c2ac3`** — é esta sessão, já contabilizada como `base`

### E-2.3 — As sessões escolhidas (congeladas)

| sessão | data | msgs | acumulado |
|---|---|---|---|
| `155d4c62` | 2026-07-16 | 820 | — |
| `1f1c6a32` | 2026-07-12 | 712 | — |
| `d12c4706` | 2026-07-18 | 709 | — |

As três maiores após excluir esta. Como cada uma excede sozinha o teto de
99, a leitura será **amostrada dentro delas**, não integral: as primeiras
~33 mensagens de cada, em ordem cronológica, totalizando 99.

*Por que o início e não o meio:* início de sessão carrega o enquadramento
do problema e a decisão, que é o que Q2–Q7 pedem. Escolha declarada antes
de ler; se o início se revelar só setup, isso é reportado como defeito da
regra, não corrigido no meio.

**Predição pré-dado:** o C produz **menos páginas** que o W com o mesmo
orçamento, porque 99 mensagens de Code cobrem menos assunto que 99 turnos
de conversa web — sessão de trabalho é densa em execução e repetitiva em
tema. Mas as páginas do C devem responder **mais** perguntas de Q1–Q7.
