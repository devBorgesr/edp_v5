# PRE_REGISTRO — Degrau 1: Honeypot (cache de respostas)

Contrato: `NORTE.md@36ac6b4`. Exemplar de forma: `PRE_REGISTRO_EXP017.md`.
Escrito em **06/08/2026**, ANTES de qualquer medição ou código do honeypot.
Branch prevista: `exp018/honeypot-fase0`.

Status: **FASE 0 (medição) — não autoriza implementação.**
Nenhuma linha de honeypot entra em `websocket.py` antes de este documento
ter a seção `## Resultado` preenchida com dado real.

---

## 0. Por que este pré-registro difere da especificação recebida

A especificação do Degrau 1 (mensagem do pesquisador, 06/08) já corrigiu
três defeitos reais apontados na revisão anterior: gate por `rank_score`,
armazenamento de blob `Q+A`, e `score=0.65` hardcoded. Essas correções
estão incorporadas.

Ao verificar os parâmetros restantes contra o código e os dados, porém,
**três premissas do desenho não sobreviveram**. Elas são registradas aqui
porque um pré-registro herda a validade das suas premissas — congelar um
critério construído sobre premissa falsa produz um resultado sem valor
informativo, que é pior que nenhum resultado.

| # | Premissa da spec | Verificação | Consequência |
|---|---|---|---|
| P1 | "as 14 queries do `export_fase0.jsonl` (já existem)" servem de dataset | São o pool congelado do **EXP017**, instrumento para medir *colapso de retrieval*, não acerto de cache (`EXP017_FASE0.md:90-103`) | Instrumento errado — ver §1 |
| P2 | critério "≥ 5 das 14 respondidas corretamente" | O pool tem 6 [R3] anafóricas + 3 [R2] anafóricas-com-tópico + 5 [N] factuais. O teto de perguntas cacheáveis é ≤5, e 2 das 5 [N] são incacheáveis por construção | Limiar **inatingível** — ver §1.2 |
| P3 | gate `epistemic_status == "verified"` | Nenhum caminho automático escreve `"verified"` no código. `websocket.py:1218` grava toda captura como `"hypothesis"`; a única escrita é manual, via UI (`memory.py:750`) | Conjunto elegível ≈ **vazio** — ver §2 |

Nenhuma dessas é objeção ao honeypot como ideia. São objeções ao
**experimento**: da forma proposta, ele produziria H0 por defeito do
instrumento, e nós leríamos isso como "cache não funciona".

---

## 1. O dataset não pode ser o `export_fase0.jsonl`

### 1.1 Proveniência (verificada, não inferida)

`export_fase0.jsonl` tem 14 registros `{query, results}`. Não é
referenciado por nenhum `.py` ou `.md` do repositório — é artefato órfão.
Sua origem está documentada em `EXP017_FASE0.md:7` e `:90-103`: saída de
`scripts/medir_repeat_exp017.py` contra `C:\edp_data_fase0`.

As 14 queries foram desenhadas em três pools, com rótulo explícito:

- **[R3]** (6) — anafóricas puras, de `edp/lab/exp009.py:70-77`
- **[R2]** (3) — anafóricas com tópico, de `edp/lab/exp010.py:84-88`
- **[N]** (5) — factuais novas

O objetivo do EXP017 era medir `repeat_rate`: quanto dois retrieves
consecutivos devolvem os **mesmos IDs**. Queries anafóricas são a sonda
*ideal* para isso — pergunta vaga → retrieve genérico → mesmos IDs. São a
sonda *pior possível* para um cache de respostas factuais, porque uma
pergunta anafórica não tem resposta cacheável: a resposta depende do
estado da sessão, não do conteúdo da pergunta.

Reaproveitar o pool é erro de categoria: usar um instrumento calibrado
para medir X para medir Y.

### 1.2 O limiar "≥ 5 de 14" é inatingível por construção

Teto teórico = 5 (o pool [N] inteiro). Mas dentro dele:

| # | Query [N] | Cacheável? |
|---|---|---|
| 3 | "qual é a capital da Mongólia mesmo?" | ❌ conhecimento externo — nunca esteve na memória |
| 6 | "me explica de novo como funciona o RRF no retrieval híbrido" | ✅ |
| 9 | "qual foi a última vez que ajustamos o piso do NOT_FOUND_FLOOR?" | ❌ dependente de tempo — *stale by design* |
| 11 | "pode resumir o que ficou pendente no exp016?" | ⚠️ dependente de estado |
| 13 | "o que a gente decidiu sobre o calibrador Bayes-vs-Gauss?" | ✅ |

Teto realista: **2, no máximo 3**. Um limiar de 5 exige acertar inclusive
a capital da Mongólia a partir de uma memória que não a contém. H0 vence
com probabilidade ~1 **independentemente da qualidade do honeypot**.

### 1.3 Anomalia registrada (não resolvida)

Os `score` do arquivo têm máximo **0.0164** (média dos 14 tops: 0.0146).
`EXP017_FASE0.md:74` documenta que a chamada foi
`mem.retrieve(q, top_k=5, min_score=0.20)`. Valores abaixo do próprio
`min_score` não deveriam aparecer. Isso não é resolvido aqui; fica
**registrado como pendência** (§7, Q1) e é razão adicional para não usar
esses números como calibração de nenhum limiar.

---

## 2. O gate `verified` seleciona o conjunto vazio

Busca exaustiva por escritas de `"verified"` em `edp/`: todas as
ocorrências são comparação (`==`, `in`), docstring, `<option>` de HTML, ou
lista de validação. **Zero escritas automáticas.**

O caminho vivo grava assim (`websocket.py:1212-1219`):

```python
_entry = memory.add(
    combined,
    score=0.65,
    prioridade="media",
    source=source,
    confidence=0.65,
    epistemic_status="hypothesis",   # ← toda captura automática
)
```

A única promoção a `verified` é `update_entry` disparado à mão pela UI de
Memory Review. Logo, exigir `verified` restringe o honeypot ao que o
pesquisador curou manualmente — que é o conjunto certo do ponto de vista
de segurança epistêmica, e ~vazio do ponto de vista de cobertura.

Isso é uma **bifurcação de desenho que precisa ser decidida antes do
dado**, não durante:

- **Ramo A (conservador)** — honeypot serve só memórias curadas à mão.
  Seguro, escopo pequeno, economia proporcional ao esforço manual.
- **Ramo B (expansivo)** — construir promoção automática
  `hypothesis → verified`. Isso **não é o Degrau 1**: é um subsistema de
  verificação novo, com seus próprios riscos, e passaria pelo NORTE.md
  como frente separada.

Este pré-registro assume o **Ramo A** e mede sob ele. Se o dado disser que
o Ramo A não tem cobertura suficiente, o Ramo B vira uma proposta nova —
não uma emenda a esta.

---

## 3. Quatro fenômenos distintos (não conflar)

A spec original tratou como um só o que são quatro coisas mensuráveis
separadamente. O honeypot depende do **F1**, e nada no repositório o mediu
até hoje.

- **F1 — repetição de perguntas.** Com que frequência o usuário faz uma
  pergunta semanticamente equivalente a uma anterior. *Teto absoluto de
  qualquer cache.* **Nunca medido.**
- **F2 — repetição de retrieve.** Quanto retrieves consecutivos devolvem
  os mesmos IDs. Medido pelo EXP017. **Não é F1.**
- **F3 — similaridade Q ↔ blob Q+A.** O que o `retrieve` atual computa.
- **F4 — a memória contém a resposta.** O que o honeypot precisa. Nenhum
  componente do EDP mede isso hoje.

`F2 alto` não implica `F1 alto`: retrieves colapsam justamente em
perguntas *vagas e diferentes entre si*.

---

## 4. Hipóteses (registradas antes do dado)

### Fase A — teto de viabilidade (F1)

Mede-se **só a taxa de repetição**, sem construir cache algum.

- **H1a** — ≥ **10%** dos turnos de usuário do corpus têm um turno de
  usuário **anterior** com similaridade de cosseno ≥ **0.85** (embeddings
  do próprio EDP, `edp/embeddings.py:embed_one`).
- **H0a** — < 10%.

*Justificativa do piso de 10% (fixada pré-dado):* a proposta original
estimou "~80% de economia". A taxa de repetição é o **limite superior** da
economia possível. Um piso de 10% é 8× mais permissivo que a alegação
testada: se nem 10% for atingido, a alegação de 80% está errada por quase
uma ordem de grandeza e o honeypot não se paga contra o custo de
manutenção e o risco de resposta stale.

*Predição pré-dado do arquiteto:* F1 fica **abaixo de 10%**. Razão: o
corpus real é trabalho de pesquisa progressivo — perguntas encadeiam, não
repetem. Registrar a predição permite que ela seja refutada.

### Fase B — acurácia (só executa se H1a sobreviver)

- **H1b** — entre as perguntas repetidas identificadas na Fase A, ≥ **70%**
  teriam recebido do cache uma resposta julgada **correta e não-stale**.
- **H0b** — < 70%.

Juiz: **o pesquisador**, cego ao score, decidindo por par
(pergunta_nova, resposta_cacheada) em três rótulos: `correta` /
`incorreta` / `stale`. `stale` conta como erro — é o modo de falha que o
EDP existe para evitar.

---

## 5. Desenho

### 5.1 Corpus (e a restrição de ambiente)

Esta VM **não tem corpus**. `edp/config.py:9` faz
`BASE_DIR = Path(os.environ.get("EDP_BASE_DIR", "/content/edp_v3_memory"))`
— caminho de Colab, inexistente aqui; `sessions/` não existe. Os únicos
`episodic.json` da máquina são fixtures do pytest.

A medição roda, portanto, **na máquina Windows**, como o EXP017 rodou
(`EXP017_FASE0.md:156`), sobre:

1. **Cópia** do store de produção (produção intocada — mesmo protocolo do
   `C:\edp_data_fase0`), e
2. O export de conversa real disponível (`Análise_geral_do_edp (1).json`),
   que já contém `thinking_blocks` desde a v4.9.0 do sensor.

O corpus é congelado por hash SHA-256 antes da primeira medição, e o hash
é registrado em §8.

### 5.2 Script

`scripts/medir_repeticao_honeypot.py` — **read-only**, sem import de
`websocket.py`, sem escrita em memória. Faz:

1. Extrai a sequência ordenada de turnos de usuário.
2. `embed_one()` em cada turno.
3. Para cada turno *i*, similaridade contra todos os turnos *j < i*.
4. Reporta a distribuição completa de similaridade máxima, não só a
   fração acima de 0.85 — a distribuição permite recalibrar o limiar em
   um experimento *futuro* sem repetir a coleta, e expõe se 0.85 caiu no
   meio de uma massa densa (limiar frágil) ou num vale (limiar robusto).

### 5.3 Filtro de turnos (congelado pré-dado)

Turnos de usuário **não** são todos perguntas. `ok`, `sim`, `manda os
comandos`, colagens de saída de terminal — repetem-se muito e não são
cacheáveis. Contá-los inflaria F1 por um mecanismo que o honeypot não
consegue explorar.

Regra congelada agora, antes de ver qualquer número:

- Descarta turno com **< 5 palavras** (mesmo piso já usado no projeto:
  `MIN_WORDS = 5`, `edp/config.py:19` — não é número novo inventado para
  esta medição).
- Descarta turno com **> 2000 caracteres** — são colagens de log/código,
  não perguntas.
- Nenhum outro filtro. Sem curadoria manual do corpus.

A fração descartada é **reportada** junto com o resultado. Se passar de
50%, o resultado é marcado como frágil e o filtro vira objeto de discussão
para uma medição futura — nunca reajustado dentro desta.

### 5.4 Amostra mínima (congelada pré-dado)

`N_MIN = 100` pares comparáveis. Abaixo disso o script **não emite
veredito** — imprime `AMOSTRA INSUFICIENTE`.

*Justificativa:* com N = 100 e nenhuma repetição observada, o limite
superior de 95% pela regra de três é 3/100 = **3%**, que exclui o piso de
10% com folga. Ou seja, N = 100 basta para **refutar** H1a de forma
decisiva quando a taxa real é próxima de zero — que é a predição do
arquiteto. Para *confirmar* H1a perto do piso a precisão é pior
(±6pp), e isso está registrado como limitação conhecida, não descoberta
depois.

Sem esse piso, um corpus minúsculo devolveria "0% → H0a vence" e nós
leríamos como resultado o que é apenas ausência de medição.

### 5.5 Sensibilidade pré-registrada

A fração é reportada em **0.80 / 0.85 / 0.90** simultaneamente. O corte de
decisão é **0.85**, fixado agora; os outros dois são diagnóstico de
robustez e **não** podem ser promovidos a critério depois do dado.

---

## 6. Critérios PASSA/FALHA

| Resultado | Decisão |
|---|---|
| H1a sobrevive (≥10% @ 0.85) | Executa Fase B. |
| H0a vence (<10%) | **Honeypot abandonado.** Registrar em `FILA_FUTURO.md` com o número medido. Semanas economizadas. Resultado válido e publicável. |
| H1a sobrevive, H0b vence | Honeypot **não** implementado no caminho vivo. O dado indica que repetição existe mas a memória não guarda respostas reutilizáveis — o que aponta para o defeito do blob `Q+A` (`websocket.py:1200`) como frente separada. |
| H1a e H1b sobrevivem | Autoriza implementação, sob o desenho de §2 Ramo A + gate de §4, e **atrás de feature flag** `EDP_HONEYPOT` (default OFF), conforme mandato de Tier 2/3 do `edp_metodologia.md`. |

**H0 vencendo é resultado, não fracasso.** O objetivo declarado desta fase
é decidir se vale construir, não construir.

---

## 7. Pendências registradas (não bloqueiam a Fase A)

- **Q1** — Por que os `score` de `export_fase0.jsonl` (máx. 0.0164) estão
  abaixo do `min_score=0.20` da chamada documentada? Resolver antes de
  qualquer uso futuro daquele arquivo como calibração.
- **Q2** — `score=0.65` hardcoded persiste em `websocket.py:1214` e
  `:1236` (defeito A5 de `RESULTADO_AUDITORIA_EDP_v5.md` §3.3). Este
  pré-registro **não o corrige** e **não o replica**; fica como dívida
  aberta, independente do honeypot.

---

## 8. Fora de escopo (explícito)

- Implementar qualquer código de honeypot antes de §6.
- Promoção automática `hypothesis → verified` (Ramo B de §2).
- Alterar `combined = f"Q: ...\nA: ..."` em `websocket.py:1200`.
- Degraus 3 (UI/UX) e 5 (K8s/OpenTelemetry/Postgres) — já em
  `FILA_FUTURO.md@36ac6b4`, fora do prazo do NORTE.md.

---

## 9. EMENDA E1 — 06/08/2026 (pré-dado, aditiva; texto acima intocado)

Registrada por decisão explícita do pesquisador, **antes** de qualquer
medição. O texto original das §§1-8 fica intocado, como manda o protocolo.

**O que muda.** O instrumento de decisão do Degrau 1 passa a ser o
**avaliador direto das 14 queries** (`scripts/avaliador_honeypot_14q.py`),
não a medição de F1. Critério do pesquisador: a decisão precisa sair em
horas, e o teste direto é terminal — roda em segundos sobre o store que já
existe, sem depender de acumular corpus.

**O que fica suspenso.** `scripts/medir_repeticao_honeypot.py` (Fase A/F1)
não é instrumento de decisão. Permanece no repositório como **estudo de
caracterização futura**, não é executado nesta fase, e o piso `N_MIN=100`
continua válido *para ele*.

**O que permanece congelado, sem alteração:** gate de similaridade
**bruta ≥ 0.70** (cosseno de embeddings, nunca `rank_score`), gate
`epistemic_status == "verified"`, e critério **acertos ≥ 5 → H1**.

### 9.1 Ressalva do arquiteto (registrada pré-dado, não bloqueia)

As objeções P1 e P2 das §§1.1-1.2 **não foram refutadas** por esta emenda —
elas continuam factualmente verdadeiras sobre o pool: 9 das 14 queries são
anafóricas por desenho, e o teto de perguntas cacheáveis é ~2-3, abaixo do
critério de 5. Minha predição pré-dado permanece: **H0 vence**, e vence por
propriedade do instrumento, não por propriedade do cache.

O pesquisador decidiu rodar mesmo assim; a decisão é dele e está tomada.
Registro a ressalva aqui para que, quando o número sair, ele seja lido pelo
que é. **Um H0 neste teste autoriza dizer:** "com este pool e este gate, o
cache não entrega". **Não autoriza dizer:** "cache de respostas é inviável
no EDP" — para isso faltaria justamente a medição de F1 suspensa acima.

### 9.2 O que foi acrescentado ao instrumento para o H0 ser informativo

Um `0/14` seco não distingue causas. O avaliador reporta, por query, **a
causa do miss** — `SEM_MEMORIA_SIMILAR` / `STATUS_NAO_VERIFIED` /
`SEM_RESPOSTA_EXTRAIVEL` / `HIT` — e mais dois números:

- **contrafactual sem o gate de status**: quantos passariam só por
  similaridade. Isola o custo exato de P3.
- **`sim_blob` vs `sim_q`**: similaridade contra o embedding persistido do
  blob `Q+A` (o que o sistema faz hoje) contra a parte `Q:` re-embeddada (o
  desenho corrigido). Mede diretamente quanto `websocket.py:1200` distorce
  a recuperação — evidência reaproveitável para outra frente,
  independentemente do veredito do honeypot.

Sem isso, o resultado esperado (0/14 por P3) seria indistinguível de
"nenhuma memória parecida existe", e as duas conclusões levam a decisões
opostas.

### 9.3 Desenho autorizado SE H1 vencer

- similaridade **bruta**, nunca `rank_score`;
- armazenar **só a resposta**, nunca o blob `Q: ...\nA: ...`;
- flag `EDP_HONEYPOT`, default **OFF** (mandato Tier 2/3 do
  `edp_metodologia.md`);
- teste de regressão garantindo que `score=0.65` não toca o código novo
  (defeito A5, §7 Q2).

---

## Resultado

`[PREENCHER — rodada Windows: scripts/avaliador_honeypot_14q.py]`

Store medido: `C:\edp_data_fase0` — **210 entries**
Data da medição: **06/08/2026**, máquina Windows do pesquisador
Commit do script: `70d04a7`

Censo `epistemic_status`: **`hypothesis` 208, `verified` 2** → P3
**CONFIRMADO** com número: 0,95% do store é `verified`.

| # | pool | query | sim_q | sim_ver | sim_blob | hit | causa |
|---|---|---|---|---|---|---|---|
| 1 | R3 | vamos continuar nossa conversa | **0.7032** | 0.3047 | 0.5132 | não | STATUS_NAO_VERIFIED |
| 2 | R2 | vamos continuar a conversa sobre Redis e Memcached | **1.0000** | 0.3575 | 0.6523 | não | STATUS_NAO_VERIFIED |
| 3 | N | qual é a capital da Mongólia mesmo? | 0.4324 | 0.2874 | 0.3514 | não | SEM_MEMORIA_SIMILAR |
| 4 | R3 | continuando o que falávamos | 0.6179 | 0.3717 | 0.5294 | não | SEM_MEMORIA_SIMILAR |
| 5 | R2 | me lembra o que a gente concluiu sobre cache… Redis | **0.8216** | 0.3794 | 0.6380 | não | STATUS_NAO_VERIFIED |
| 6 | N | me explica de novo como funciona o RRF | 0.4279 | 0.2640 | 0.4695 | não | SEM_MEMORIA_SIMILAR |
| 7 | R3 | o que a gente tinha concluído mesmo? | 0.6223 | 0.3489 | 0.5133 | não | SEM_MEMORIA_SIMILAR |
| 8 | R2 | voltando ao assunto do Redis para sessões web | 0.6853 | 0.4055 | 0.5649 | não | SEM_MEMORIA_SIMILAR |
| 9 | N | qual foi a última vez que ajustamos o NOT_FOUND_FLOOR? | 0.5032 | 0.3068 | 0.4705 | não | SEM_MEMORIA_SIMILAR |
| 10 | R3 | me lembra o que discutimos | 0.6194 | 0.2994 | 0.5081 | não | SEM_MEMORIA_SIMILAR |
| 11 | N | pode resumir o que ficou pendente no exp016? | 0.5028 | 0.3415 | 0.4867 | não | SEM_MEMORIA_SIMILAR |
| 12 | R3 | voltando ao que estávamos vendo | 0.6178 | 0.3373 | 0.5428 | não | SEM_MEMORIA_SIMILAR |
| 13 | N | o que a gente decidiu sobre o calibrador Bayes-vs-Gauss? | 0.5753 | 0.1846 | 0.5348 | não | SEM_MEMORIA_SIMILAR |
| 14 | R3 | sobre o que conversamos até agora | **0.9383** | 0.4040 | 0.5576 | não | STATUS_NAO_VERIFIED |

Acertos: **0 / 14** — critério H1: ≥ 5
Contrafactual sem gate de status: **4 / 14**
Causas: `STATUS_NAO_VERIFIED` 4, `SEM_MEMORIA_SIMILAR` 10

## **VEREDITO: H0 VENCE.** Honeypot **abandonado** (§6).

### R1 — Seletividade invertida (achado principal, não previsto)

As 4 queries que passariam o gate de similaridade são, **todas as 4**,
anafóricas. Nenhuma factual passou:

| pool | n | sim_q média | máx | passaram o gate 0.70 |
|---|---|---|---|---|
| anafóricas [R3]+[R2] | 9 | **0.7362** | 1.0000 | **4** |
| factuais [N] | 5 | **0.4883** | 0.5753 | **0** |

O gate seleciona **vagueza**, não repetição. Frases curtas e genéricas
("vamos continuar nossa conversa", "sobre o que conversamos até agora")
aglomeram-se no espaço de embeddings e produzem cosseno alto entre si;
perguntas factuais, que carregam conteúdo específico, ficam ~0.24 abaixo.

Consequência operacional: o honeypot dispararia **exatamente onde não
pode** — em perguntas cuja resposta correta depende do estado da sessão —
e ficaria mudo exatamente onde poderia ajudar. Precisão no conjunto que
dispara: **0%**. As 4 seriam confabulação servida com confiança.

**Escopo desta conclusão.** O lado *precisão* generaliza: a aglomeração de
texto curto e vago em espaço de embeddings é propriedade do método, não
deste pool, e toda conversa real contém frases de continuação. O lado
*recall* (ficar mudo onde ajudaria) é parcialmente artefato do pool e do
store — 10 dos 14 misses são `SEM_MEMORIA_SIMILAR`, e o store pode
simplesmente não conter aqueles tópicos. P1/P2 seguem válidas.

### R2 — O gate de status é irrelevante NESTE dado

`sim_ver` máximo em todas as 14 queries: **0.4055**. As 2 entries
`verified` do store nunca chegam perto de 0.70. Ou seja: mesmo que o gate
de status fosse removido, o resultado não melhoraria — as 4 que passariam
seriam as anafóricas de R1. P3 é real (0,95% verified) mas **não é o que
mata o honeypot** aqui; R1 é.

### R3 — O blob `Q+A` comprime a faixa dinâmica (achado reaproveitável)

Diluição média `sim_q − sim_blob`:

- onde `sim_q ≥ 0.70`: **+0.2755**
- onde `sim_q < 0.70`: **+0.0633**

Amplitude: `sim_q` 0.4279–1.0000 (**0.5721**) → `sim_blob` 0.3514–0.6523
(**0.3009**). O `combined = f"Q: …\nA: …"` de `websocket.py:1200`
**destrói 47% da faixa dinâmica** e dilui seletivamente os sinais fortes.
Um par idêntico (1.0000) vira 0.6523.

Isso é evidência independente do veredito do honeypot e vale para o
retrieval inteiro: hoje o EDP não consegue distinguir "match perfeito" de
"match mediano". Fica registrado como frente candidata separada, **não
aberta aqui**.

### R4 — Pendência: `sim_q = 1.0000` na query 2

Existe no store uma entry cuja parte `Q:` é textualmente **idêntica** à
query 2. As 14 são queries de laboratório (`exp009.py:70-77`,
`exp010.py:84-88`); a explicação mais provável é que uma rodada anterior
as persistiu. **Não verificado** — registrado como pendência. Se
confirmado, a única "repetição perfeita" do dado é auto-contaminação do
instrumento, não repetição genuína do usuário, o que reforça R1.

### Fase A (F1) — suspensa por E1, permanece não medida

F1 (taxa de repetição real de perguntas) continua sendo o teto absoluto de
qualquer cache e **segue sem medição**. Instrumento preservado em
`scripts/medir_repeticao_honeypot.py`. Este H0 **não** o substitui: ele
mostra que *este* gate falha, não que repetição não exista.
