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

## Resultado

`[PREENCHER — rodada Windows]`

Hash SHA-256 do corpus congelado: `[PREENCHER]`
Data da medição: `[PREENCHER]`
Commit do script: `[PREENCHER]`

| limiar | fração de turnos com repetição anterior |
|---|---|
| 0.80 | |
| **0.85 (decisão)** | |
| 0.90 | |

Distribuição da similaridade máxima (p50 / p75 / p90 / p95 / max):
`[PREENCHER]`

Veredito H1a / H0a: `[PREENCHER]`
