# DESIGN — Wiki de conversas (Palácio da Memória)

Idealização completa. **Não é pré-registro e não autoriza implementação** —
o critério falsificável do §9 precisa virar pré-registro próprio antes de
qualquer código, conforme `NORTE.md` §4.2.

Contrato: `NORTE.md@5fb4402` §1 (a plataforma é o norte) e §4 (o método).
Antecedentes: `docs/preregistro_degrau1_honeypot.md` (R1),
`docs/wiki_conversas_pendente.md` (segurança), `conversa_importante1.txt`
(método Karpathy/Memoriki), `conversa2.md` (arquitetura de 7 camadas).

---

## 1. A tese

A Wiki de código (`/wiki`, commit `01a2385`) compila **estrutura**: 198
páginas a partir do grafo AST. Ela responde "como o sistema é feito".

Esta Wiki compila **o que foi pensado e decidido**: conceitos, domínios e
asserções extraídos das conversas reais, com as fontes, as datas e as
contradições preservadas. Ela responde "o que nós já concluímos sobre X, e
quando isso mudou".

A diferença não é o formato — é que uma tem o código como corpus e a outra
tem o raciocínio.

## 2. Passo 0 — inventário verificado (07/08/2026)

| peça | estado | papel aqui |
|---|---|---|
| `runtime/cognitive_decisions.py` | **vivo, job ativo** (`main.py:181`), extrai `{key_assertion, concepts[1-5], domain}` por turno via Haiku | **matéria-prima da Wiki** |
| — auditoria sobre ele | *"ainda fora da fórmula de ranking, zero leituras"* — loop morto | a Wiki é o consumidor que faltava |
| `co_occurrence.py` | vivo, 9 consumidores; liga memórias por **co-recuperação real** | **fonte das arestas** |
| `memory_graph.py` | 76 linhas, **zero consumidores**; liga por similaridade ≥0.70 | **não usar** — ver §5 |
| `ingest/consolidator.py` | 144 linhas; 1 resumo plano por conversa (500 chars) | fonte de `sessions/`, não de `concepts/` |
| `contradiction_flagger` | resultado **descartado** em ambos os retrieves (`store.py:1537`, `:1749`) | segundo loop morto que a Wiki pode consumir |
| `model_router.py` | `claude-haiku-4-5` = US$1,00/M in, US$5,00/M out, tier 1 | base do cálculo de custo (§7) |
| exports do sensor | `turns[]` com `raw_text`, `thinking_blocks`, `thinking_summaries` (v4.9.0) | corpus histórico |
| store do EDP | `episodic.json` / `semantic.json`, blobs `Q:/A:` | corpus vivo |

**Consequência de desenho:** a Wiki não inventa uma camada de extração.
Ela dá consumidor a duas que já existem e estão mortas.

## 3. O fluxo (7 estágios)

```
  exports do sensor ┐
                    ├─► E1 inventário ──► E2 extração ──► E3 agregação
  store do EDP ─────┘      (sem LLM)       (Haiku,          (sem LLM)
                                            só o que falta)
                                                              │
   ┌──────────────────────────────────────────────────────────┘
   ▼
  E4 compilação de página ──► E5 ligação ──► E6 índice ──► E7 incremental
     (Haiku, 1 call/página)    (sem LLM,       (sem LLM)     (só o que
                                co-ocorrência)                 mudou)
```

### E1 — Inventário (read-only, sem LLM)

Varre o corpus e produz `_meta/fontes.json`: caminho, SHA-256, nº de
turnos, intervalo de datas. **Não copia conteúdo** — ver §8.

Falha explícita se um arquivo não parsear; nunca pula em silêncio
(`NORTE.md` §4.9, e o precedente do JSON truncado da auditoria).

### E2 — Extração de conceitos (Haiku, só o que falta)

Para cada turno: se já tem `cognitive_decisions`, **reusa**. Se não tem,
chama o extrator existente. Nada de prompt novo — o de
`runtime/cognitive_decisions.py:81-93` já produz o formato certo e já foi
calibrado.

Saída por turno: `{key_assertion, concepts[], domain, fonte, timestamp,
epistemic_status, source}`.

### E3 — Agregação (sem LLM)

Inverte o índice: `conceito → [ocorrências]`. Uma ocorrência carrega
fonte, data, `key_assertion` e `epistemic_status` da entry de origem.

**Piso de existência:** conceito com menos de **3 ocorrências** não vira
página — vira linha em `concepts/_orfaos.md`. Motivo: o mesmo critério que
o `GRAPH_REPORT` usa ao omitir comunidades finas, e o que impede a Wiki de
virar 4.000 páginas de uma ocorrência cada.

### E4 — Compilação de página (Haiku, 1 chamada por página)

Uma chamada por conceito, recebendo suas ocorrências ordenadas por data e
devolvendo o corpo da página. O prompt exige:

1. **Estado atual** — o que se sabe hoje.
2. **Como mudou** — se as asserções divergem ao longo do tempo, as duas
   versões aparecem **com data**, nunca fundidas.
3. **Contradições explícitas** — se duas fontes se contradizem, a página
   fica `contested` e mostra as duas. Nunca escolhe vencedor.
4. **Zero afirmação sem fonte** — cada parágrafo referencia ocorrências.

Este é o ponto onde a governança epistêmica do EDP entra na Wiki. É também
o que a diferencia de um resumo: **um resumo comprime, esta página
preserva o desacordo.**

### E5 — Ligação (sem LLM) — ver §5

### E6 — Índice e manifesto

`index.md` com todas as páginas por domínio, ordenadas por nº de
ocorrências. `_meta/manifest.json` com hashes das fontes, custo real da
rodada, contagem de páginas e data.

### E7 — Incremental

Turno novo → só os conceitos que ele cita recompilam. O manifesto guarda o
hash do conjunto de ocorrências por página; hash igual, não recompila.
Rodada típica após uma conversa: 2 a 5 páginas.

## 4. Estrutura de arquivos

```
edp_wiki/                        ← local, gitignored, NÃO servido pela API
  index.md
  concepts/<slug>.md             1 por conceito (>= 3 ocorrências)
  domains/<slug>.md              1 por domínio, lista seus conceitos
  sessions/<conversation_id>.md  1 por conversa (usa o consolidator)
  concepts/_orfaos.md            conceitos abaixo do piso
  _meta/fontes.json              caminho + sha256 + turnos
  _meta/manifest.json            hashes por página, custo, data
```

Frontmatter YAML por página:

```yaml
---
tipo: concept
slug: rrf
titulo: "RRF (Reciprocal Rank Fusion)"
dominio: retrieval
ocorrencias: 14
primeira: 2026-06-12
ultima: 2026-08-05
epistemic_status: verified | hypothesis | contested
fontes: ["mem:31162822", "conv:abc123#turn7"]
links: ["bm25", "retrieval-hibrido", "exp010"]
---
```

`[[wiki-links]]` no corpo, como no Memoriki.

## 5. A decisão de desenho central: arestas por comportamento

`memory_graph.build_from_entries()` liga memórias por **similaridade
≥0.70**. É exatamente o gate que o R1 refutou: no dado real, das 14
queries, as 4 que passariam 0.70 eram **todas anafóricas**, nenhuma
factual (anafóricas 0.7362 de média, factuais 0.4883). Construir o grafo
da Wiki sobre similaridade herdaria essa patologia — páginas vagas ligadas
a tudo.

`co_occurrence` não tem esse defeito porque **não é uma métrica de texto**:
duas memórias co-ocorrem porque uma consulta real recuperou as duas
juntas. É evidência de uso, não de parecença.

**Regra:** as arestas da Wiki vêm de co-ocorrência e de conceito
compartilhado. **Nunca de similaridade de embedding.** `memory_graph.py`
continua morto — e agora com motivo registrado, não por esquecimento.

## 6. Como a Wiki é lida (e por que isso resolve o R1)

Três caminhos:

| caminho | quem usa | mecanismo |
|---|---|---|
| navegação | humano | `index.md` → `[[links]]`. Sem algoritmo. É a proposta do Karpathy: navegar, não buscar. |
| busca | humano | léxica ponderada por IDF, com stopwords — o mesmo motor de `edp/wiki.py`, já corrigido contra o R1 léxico |
| contexto | EDP / LLM | **casamento contra `concepts[]` e `domain`**, não contra o texto |

O terceiro é o que importa. Os `concepts` são strings técnicas curtas que
a extração já destilou — o preenchimento conversacional foi jogado fora no
E2. Então:

- `"me lembra o que discutimos"` → zero conceitos casados → **não roteia**.
- `"como funciona o RRF"` → casa `rrf` → roteia para a página.

O gate de especificidade que eu havia pré-registrado (`preregistro_gate_
especificidade.md`) tentava **calcular** a especificidade a partir do texto
cru, e o smoke test mostrou que o IDF sozinho não separa. Aqui a
especificidade não é calculada: ela é **estrutural**, porque o campo contra
o qual se casa só contém termos específicos por construção.

Isso não dispensa medir — dispensa inventar um limiar.

## 7. Custo (calculado, não estimado)

Preços verificados em `model_router.py:30` — Haiku 4.5, US$1,00/M in,
US$5,00/M out.

| rodada | entrada | saída | custo |
|---|---|---|---|
| store atual (210 entries, E2+E4) | ~170k tok | ~25k tok | **~US$ 0,29** |
| corpus histórico completo (~2k turnos) | ~1,6M tok | ~120k tok | **~US$ 2,20** |
| incremental (1 conversa, 3 páginas) | ~8k tok | ~2k tok | **~US$ 0,02** |

Ordem de grandeza: **centavos**. O custo de compilação não é um fator de
decisão, e qualquer justificativa desta Wiki baseada em "economia de API"
deve ser descartada de saída — não é aí que está o valor.

## 8. Segurança (o que trava, e por quê)

`edp_wiki/` é **local e gitignored**, e **não é servido pela API**.

A API roda com `allow_origins=["*"]` (`api/main.py:260`) e
`EDP_LIVE_FEED_TOKEN` vazio (`config.py:219`): qualquer página servida por
ela é legível por qualquer origem, sem autenticação. Servir conteúdo de
conversa ali reabriria o que `3076559` e `99d827c` fecharam.

- `EDP_WIKI_CONVERSAS` permanece **OFF**, sem consumidor.
- Acesso à Wiki = sistema de arquivos + CLI. Nenhuma rota HTTP.
- `raw/` guarda **ponteiros e hashes**, não cópias — o dado de conversa não
  ganha mais um lugar onde existir.
- Para um dia servir: os 5 pré-requisitos de `docs/wiki_conversas_pendente.md`
  valem, e o primeiro é autenticação.

## 9. Como isso se valida (vira pré-registro antes de codar)

Dataset já congelado: as 14 queries do EXP017 (`EXP017_FASE0.md:90-103`),
rotuladas em julho, por outra pessoa, para outro fim.

- **H1a (utilidade)** — para as 5 queries `[N]` factuais, a página da Wiki
  contém a resposta em ≥3 casos. Baseline conhecido: o store cru entregou
  **0/14** (`dd06b87`). Juiz: o pesquisador, cego ao score.
- **H1b (guarda do R1)** — das 6 queries `[R3]` anafóricas, **zero** casam
  qualquer `concept`. Se alguma casar, o §6 está errado e o roteamento
  volta à mesa.
- **H0** — qualquer um dos dois falha.

H0 vencendo é resultado publicável: significa que compilar não entrega o
que recuperar não entregava, e a camada 3 cai antes de custar semanas.

## 10. Fora de escopo desta peça

- **Extração stealth** do histórico das contas. O corpus é o que já existe
  em disco: exports manuais + store. Fora por decisão de método e risco
  (`NORTE.md` §6), não por prazo.
- **Busca web** (camada 4) e **feedback loop** (camada 7) — dependem desta,
  não o contrário.
- **Honeypot** respondendo direto do cache — refutado (R1); a Wiki
  **alimenta** contexto, não substitui a resposta.
- **Servir por HTTP** — §8.
- **MCP server** — a ideia é boa (`conversa_importante1.txt:1174`) e vem
  depois de a Wiki existir e ser validada.

## 11. Sequência de construção sugerida

1. **E1+E3 como teste de PRÉ-CONDIÇÃO** — sem LLM, sem custo, minutos.

   A versão anterior desta linha dizia "revela se o corpus sustenta uma
   Wiki". **"Sustenta" não tem limiar** — critério mole, do tipo que o
   `NORTE.md` §4.3 manda declarar em vez de deixar passar. Contar
   conceitos mede tamanho de corpus, não valor de Wiki. Substituído por:

   **Critério congelado (pré-dado).** As 5 queries `[N]` do EXP017
   perguntam sobre `RRF`, `NOT_FOUND_FLOOR`, `exp016`, `calibrador
   Bayes-vs-Gauss` e capital da Mongólia. Conta-se quantas têm seu termo
   central presente no conjunto de conceitos extraídos.

   - **≤1 de 5 presentes → PARAR.** A Wiki não pode bater o baseline de
     0/14 neste corpus, por ausência de conteúdo — não por desenho.
     Camada 3 cai sem gastar um centavo de LLM.
   - **≥2 de 5 → segue** para o passo 2.

   *Predição pré-dado do arquiteto:* 2 ou 3 presentes. `RRF`, `exp016` e
   `Bayes-vs-Gauss` são termos do próprio trabalho e devem aparecer;
   `NOT_FOUND_FLOOR` é parâmetro de código e pode não ter virado conceito
   extraído; Mongólia quase certamente não está.

   **O que este teste NÃO decide**, e nenhuma contagem decidirá: se uma
   página compilada vale mais que os turnos crus (precisa de E2+E4 e do
   julgamento humano), e se o roteamento por `concepts[]` funciona
   (precisa dos conceitos existirem primeiro). É assimétrico de propósito:
   pode refutar, não pode confirmar.

   **Subproduto obrigatório:** a cobertura de `cognitive_decisions` no
   corpus. O §7 calculou US$0,29 assumindo reuso; se a cobertura for
   baixa, o custo real é proporcionalmente maior e a afirmação do §2 ("a
   Wiki não cria camada de extração nova") enfraquece. Reportar junto.
### RESULTADO do passo 1 — 07/08/2026

Store: `C:\edp_data_fase0`, 210 entries. Script: `4367927`.

| medida | valor |
|---|---|
| cobertura `cognitive_decisions` | **84/210 = 40,0%** |
| conceitos distintos | 236 (52 domínios) |
| conceitos ≥3 ocorrências | **28** ← nº de páginas |
| abaixo do piso | 208/236 = **88%** (154 são singletons) |
| **pré-condição** | **1 de 5** |

**VEREDITO: PARAR**, conforme o critério congelado em `b76828b`.
`1 ≤ 1`. Só `bayes` apareceu, e "dentro de conceito", não exato.

**Predição do arquiteto REFUTADA.** Eu havia registrado "2 ou 3
presentes"; deu 1. `RRF` e `exp016`, que eu dei como quase certos, não
estão no corpus.

#### O que o dado mostra além do veredito

Os domínios mais frequentes são `conversacao geral` (8), `postgresql
indexing` (6), `probabilidade e estatistica` (4), `fisica acustica` (3),
`acustica nao-linear` (3), `java resilience patterns` (2). **Este corpus
não é sobre o EDP.**

E isso é verificável, não interpretação: `EXP017_FASE0.md:156` registra que
`C:\edp_data_fase0` é uma **cópia** criada para a medição do EXP017, com
*"produção intocada"* — ou seja, a produção é outro store. As 5 queries
`[N]` perguntam sobre internals do EDP (`RRF`, `exp016`,
`NOT_FOUND_FLOOR`); o corpus medido é de sondagens sobre PostgreSQL e
acústica.

**Isto é falha de Passo 0 minha** (`NORTE.md` §4.1): assumi que o store
continha as conversas de trabalho sem verificar, e o §11 nomeou esse store
sem checar seu conteúdo.

#### O risco de custo que eu havia sinalizado NÃO se materializou

Recalculando com a cobertura real de 40% (126 entries precisando de
extração), preços de `model_router.py:30`:

- E2 (126 entries): ~101k tok in (US$0,10) + ~13k out (US$0,06) = **US$0,16**
- E4 (28 páginas): ~56k tok in (US$0,06) + ~17k out (US$0,08) = **US$0,14**
- **Total ≈ US$0,30** — contra os US$0,29 estimados no §7.

A estimativa sobreviveu porque extração é barata perto de compilação. O
aviso do script sobre cobertura baixa está correto como alerta, mas neste
caso não muda a decisão.

#### Segunda rodada: regra congelada ANTES de rodar

Rodar o mesmo teste num segundo corpus depois de um resultado indesejado é
exatamente o movimento que vira p-hacking. Portanto, congelado agora:

- **Permitida UMA segunda rodada**, contra o store de **produção**, pelo
  mesmo script e mesmo critério, sem alterar `ALVOS` nem o corte.
- **Se produção também der ≤1 de 5: a camada 3 cai**, definitivamente.
  Não há terceiro corpus. Não se troca a lista de alvos.
- O resultado da segunda rodada é reportado **ao lado** deste, nunca no
  lugar dele.

Justificativa de que não é caça a resultado: a pergunta "os conceitos
existem no corpus?" é sobre um corpus específico, e o corpus medido é
documentadamente uma cópia de sondagem, não o acervo de trabalho. Trocar
`fase0` por produção corrige um erro de Passo 0 identificado; trocar o
critério, não.

#### O achado da distribuição fica pendente, confundido

88% dos conceitos abaixo do piso e 154 singletons **poderiam** indicar que
a extração produz termos granulares demais, ou que o piso de 3 é alto. Mas
está confundido pela mesma causa: num corpus espalhado entre PostgreSQL,
acústica e Java, conceito não repete mesmo. Só é interpretável depois da
segunda rodada — **não ajustar o piso agora.**

---

2. Pré-registro com o §9 congelado.
3. E2+E4 numa fatia (um domínio só), medindo custo real contra o §7.
4. Julgamento das 14 queries.
5. Se H1: E5+E6+E7 e a Wiki completa. Se H0: relatório e a camada 3 cai.
