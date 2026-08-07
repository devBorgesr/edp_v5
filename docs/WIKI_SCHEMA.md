# WIKI_SCHEMA.md — convenções da wiki de conversas do EDP

Este é o **schema layer** do método `llm-wiki`
([gist do Karpathy](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)):
o documento que define estrutura, convenções e fluxos, para que a wiki não
apodreça.

**Este arquivo é versionado. O conteúdo da wiki não** — `edp_wiki/` está no
`.gitignore`. Páginas compiladas a partir de conversa real herdam a
sensibilidade da fonte, e este repositório é público. Protocolo commitado,
conteúdo local. Mesma lógica de `3076559`/`99d827c`.

---

## 1. As três camadas

| camada | onde | regra |
|---|---|---|
| **raw** | exports do sensor, store do EDP, `graphify-out/` | **imutável.** Lê-se, nunca se escreve. |
| **wiki** | `edp_wiki/paginas/` | markdown gerado; toda afirmação com fonte |
| **schema** | este arquivo | as convenções abaixo |

`edp_wiki/index.md` — catálogo por categoria.
`edp_wiki/log.md` — registro append-only de cada ingest e manutenção.
`edp_wiki/_meta/` — hashes de fonte, nada de conteúdo.

## 2. Anatomia de uma página

Nome de arquivo em kebab-case, um assunto por página.

```markdown
---
titulo: "R1 — seletividade invertida"
tipo: achado | conceito | decisao | componente | sessao
status: verificado | hipotese | contestado | obsoleto
fontes: ["commit:dd06b87", "conv:opus_copiloto#t2693", "docs/preregistro_degrau1_honeypot.md"]
criada: 2026-08-07
atualizada: 2026-08-07
links: ["gate-de-similaridade", "cognitive-decisions"]
---

Corpo. Cada afirmação com fonte rastreável.
```

## 3. As sete regras

1. **Nada sem fonte.** Afirmação sem `commit:`, `conv:`, arquivo ou linha
   não entra. Se não dá para citar, é hipótese e o `status` diz isso.
2. **Contradição não se funde.** Duas fontes divergentes → `status:
   contestado`, as duas versões com data. A wiki preserva desacordo; um
   resumo o apagaria. É a governança epistêmica do EDP dentro da wiki.
3. **O que mudou fica.** Ao atualizar, a versão anterior vira parágrafo
   datado, não é sobrescrita. Sem isso não há como responder "quando isso
   mudou".
4. **Uma página por assunto.** Se precisa de "e" no título, são duas.
5. **Link é obrigatório, não decorativo.** Página órfã é defeito — o lint
   pega.
6. **Predição refutada é conteúdo de primeira classe.** Registrar o que
   se previu, o que deu, e por quê. É o que a wiki tem que grep não tem.
7. **A wiki não é servida por HTTP.** A API roda com `allow_origins=["*"]`
   e `EDP_LIVE_FEED_TOKEN` vazio (`api/main.py:260`, `config.py:219`).
   Ver `docs/wiki_conversas_pendente.md` antes de qualquer rota.

## 4. Os três fluxos

### ingest
1. Ler a fonte (conversa, commit, documento).
2. **Discutir os takeaways** — o passo que o método exige e que um ETL
   pula. Sem conversa, não há compilação, há transcrição.
3. Escrever/atualizar as páginas afetadas.
4. Atualizar `index.md` e acrescentar linha em `log.md`.

### query
1. Buscar em `edp_wiki/paginas/`.
2. Responder **com citação da página e da fonte dela**.
3. Se a resposta boa não estava lá, ela vira página nova. É aqui que a
   wiki compõe.

### lint (periódico)
- afirmações sem fonte
- páginas órfãs (sem link de entrada)
- `status: verificado` cuja fonte foi refutada depois
- contradições entre páginas que ninguém marcou como `contestado`
- páginas não tocadas há muito tempo cujo assunto teve commit novo

## 5. O que esta wiki NÃO é

- **Não é RAG.** Não há recuperação por similaridade. O R1
  (`docs/preregistro_degrau1_honeypot.md`) mostrou que gate de
  similaridade dispara em consulta vaga e silencia em específica.
  Navegação por link e busca léxica; nunca cosseno.
- **Não é pipeline.** Não há extração estruturada em lote, nem piso
  estatístico de ocorrências. Foi o erro registrado na errata de
  `docs/design_wiki_conversas.md`.
- **Não é resumo.** Resumo comprime e perde o desacordo; aqui o desacordo
  é o ativo.
- **Não é automática.** O humano curadoria as fontes e faz as perguntas;
  o agente faz o registro, o cruzamento e a manutenção.

## 6. Como retomar em sessão nova

1. Ler este arquivo.
2. Ler `edp_wiki/index.md`.
3. Ler as últimas 20 linhas de `edp_wiki/log.md`.
4. Só então responder ou ingerir.

---

## 7. As camadas (a cebola) e a regra de promoção

Acrescentado em 07/08/2026. Resolve dois problemas de uma vez: qual
memória é confiável, e **o que a `[ÂNCORA DE TAREFA EM CURSO]` carrega**
(peça 2.6d, pendente desde 30/05 — `docs/MARCOS_EPISTEMICOS.md`).

As duas perguntas são a mesma: *o que é sólido o bastante para carregar
adiante.*

### 7.1 As camadas

| camada | `status` | o que exige | pode entrar na âncora? |
|---|---|---|---|
| **0 · núcleo** | `nucleo` | passou em teste com **critério congelado antes do dado**, ou medição com **controle declarado** | **sempre** |
| **1 · verificado** | `verificado` | citação a `arquivo:linha`, commit ou medição **re-checável mecanicamente** | se relevante à tarefa |
| **2 · contestado** | `contestado` | duas fontes divergem, **sem** resolução | **sempre** — ver 7.4 |
| **3 · hipótese** | `hipotese` | plausível e com fonte, **não testada** | **nunca** |
| **4 · obsoleto** | `obsoleto` | superada; fica pelo registro | nunca |

### 7.2 A regra que impede autoconfirmação

**Promover para dentro exige evidência externa ao próprio sistema.**

- `hipotese → verificado` — exige citação que outra pessoa possa
  re-checar sem confiar em mim: `arquivo:linha`, hash de commit, ou saída
  de script. Nenhuma métrica interna promove nada.
- `verificado → nucleo` — exige **uma** das duas:
  1. pré-registro com critério congelado **antes** do dado, e o dado
     rodou (`NORTE.md` §4.2)
  2. medição com **controle declarado** — controle negativo, baseline, ou
     predição registrada antes (`NORTE.md` §4.5)

O núcleo não é o que a métrica gostou. É **o que não foi derrubado**.

Isto é o que impede o defeito do bot que se pontua pelo próprio grafo:
nenhuma pontuação interna move nada para dentro.

### 7.3 Rebaixamento — a metade que se esquece

Automático e obrigatório:

- fonte citada foi refutada depois → **volta para `contestado`**, nunca
  apaga
- `arquivo:linha` citado não existe mais → **`verificado` cai para
  `hipotese`**; detectável por lint, sem julgamento
- **predição registrada na página foi refutada** → a página cai uma
  camada. Nesta sessão houve **4 predições refutadas**; cada uma deve
  rebaixar o que a carregava.

Camada que só sobe é ranking, não epistemologia.

### 7.4 O que a âncora carrega (fecha a 2.6d)

A âncora hoje carrega desafio + seções entregues + próxima esperada. Falta
a **decisão**, e é isso que faz o modelo escolher Kafka na Seção 1 e
RabbitMQ na Seção 4.

Ordem de preenchimento, até o teto:

1. **todo o núcleo relevante** — é pequeno por construção e é o que não
   pode ser re-litigado
2. **todo `contestado` relevante** — decisão em aberto precisa aparecer
   *como aberta*. Omitir contestado é o que produz o Kafka/RabbitMQ: o
   modelo não sabe que há divergência e escolhe arbitrariamente
3. **`verificado` relevante**, por proximidade à tarefa, até encher
4. **`hipotese` nunca entra** — propagaria alegação não validada para
   dentro do trabalho futuro

**Teto obrigatório.** Sem teto, a âncora incha e reproduz a inflação
crônica que o modo sprint diagnosticou em 30/05 (peça 2.6a). O teto é
declarado por tarefa e o que não coube é **listado por título**, para o
modelo saber que existe e poder pedir.

### 7.5 Como isto é auditável

Um lint pode verificar mecanicamente, sem julgamento:

- toda página `nucleo` cita pré-registro ou medição com controle
- toda página `verificado` tem ao menos uma citação re-checável
- todo `arquivo:linha` citado ainda existe
- toda página com predição registrada diz se ela foi confirmada ou
  refutada

O que o lint **não** decide: se a evidência é boa. Isso é humano, e é o
ponto — a camada externa de validação é uma pessoa, não uma métrica.
