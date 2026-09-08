# Pré-registro — Experimento 008
## Qualidade de Retrieval: `cognitive_decisions` (concepts/domain) melhora a recuperação da memória certa?

**Bancada de Contexto — EDP.** Primeiro experimento da categoria **QUALIDADE DE
RETRIEVAL**. Diferente dos 001–007 (que medem COMPORTAMENTO DO MODELO dada uma
janela), o 008 mede **o que o retrieve SELECIONA**, ANTES do modelo. O LLM **não
entra na métrica primária**.

> **Régua da Bancada (método):** este documento declara hipótese, condições,
> métricas, dataset e critério de decisão **ANTES de qualquer dado**. A
> encarnação (`exp008.py`) espelha este `.md` e é **CONGELADA após o 1º disparo**.
> Anti-mock: roda sobre o retrieve **REAL** do EDP e memórias **REAIS**.
> Isolamento: sessão `__lab__` dedicada + fingerprint anti-vazamento (INV-1/INV-5).

Data de pré-registro: **2026-06-27** (antes do disparo). Congelar ao primeiro fire.

---

## §1. Motivação (o caso real que falhou ao vivo)

Auditoria de diagnóstico anterior provou, com evidência `file:line`, que o campo
`entry["cognitive_decisions"]` (produzido por
`edp/runtime/cognitive_decisions.py`) é **gravado e persistido em disco**, mas
**nenhuma leitura age sobre o hot path de retrieval**: o retrieve de produção
(`MemoryStore.retrieve`, `edp/memory.py:1612`) usa **só similaridade vetorial +
governança epistêmica/sessão**, e nunca consulta `concepts`/`domain`. O cabeçalho
do módulo lista isso como pendência nunca feita ("Uso das decisions em retrieve —
Commit 3d-β").

O caso testemunha é o **Redis**: uma memória cujo `key_assertion` é sobre
cache web (Redis/Memcached) existe no scope cognitive com `cognitive_decisions`
extraído, e foi observada **falhando ao vivo** (o modelo alucinou "Docker" em vez
de "Redis" porque a memória certa não foi recuperada — ver nota em
`edp/memory.py:725`). Este experimento mede, **com número**, se casar contra
`concepts`/`domain` teria recuperado a memória certa.

---

## §2. Hipótese (declarada antes do dado)

- **H1:** Para queries cujo alvo é uma memória com `concepts`/`domain` extraídos,
  um retrieve que **TAMBÉM** casa contra `concepts`/`domain` recupera a
  memória-alvo no top-K **mais vezes** que o retrieve atual (só similaridade
  vetorial).
- **H0 (nula):** não há diferença — `concepts`/`domain` não melhoram a
  recuperação. (Um null **válido** é um achado da Bancada, não um fracasso.)

---

## §3. Condições (mesmas queries, mesmo store, mesmo pool)

Para cada query, obtém-se UM pool de candidatos chamando o retrieve **REAL** do
EDP sobre a memória clonada (ver §7). As três condições re-ordenam **o mesmo
pool** — o único grau de liberdade é a função de score:

| rótulo | papel | score de ordenação |
|---|---|---|
| `baseline` | retrieve atual do EDP (controle) | `ranking_score` REAL (`MemoryStore.retrieve`) — similaridade vetorial + governança, **sem tocar `edp/memory.py`** |
| `tratamento` | variante de leitura experimental (só no lab) | `ranking_score + BETA · overlap(query, concepts∪domain)` |
| `tratamento_control_shuffle` | **CONTROLE NEGATIVO de validade** | igual ao `tratamento`, mas o `cognitive_decisions` de cada entry é trocado pelo do vizinho `(i+1) mod N` (campo **errado**) |

- **BASELINE** chama a função REAL (`MemoryStore.retrieve`), não reescreve nada.
- **TRATAMENTO** é implementado **somente dentro de `exp008.py`** (uma função de
  re-rank de leitura para medição) — **não** altera o Core. É uma variante de
  *medição*, não uma mudança de produto.
- **CONTROLE SHUFFLE** prova que um ganho do tratamento vem do **campo certo**
  (semântica), não do simples ato de re-ordenar. Esperado: control ≈ baseline.
  Se o control também melhorar com IC separado do baseline → **setup SUSPEITO**,
  nenhum achado é afirmado (análogo à ablação dos exp 001/004/006/007).

### Função do tratamento (CONGELADA)

```
treatment_score(entry) = ranking_score(entry) + BETA * overlap(entry)
BETA = 0.25
overlap(entry) = |Q ∩ C| / |C|            (0.0 se entry não tem cognitive_decisions, ou |C|=0)
Q = tokens(query)
C = tokens(concepts) ∪ tokens(domain)     do cognitive_decisions do entry
tokens(s): minúsculas; split em não-alfanumérico; remove tokens com < 3 chars;
           remove stopwords frozen = {por, com, sobre, para, que, the, and,
           a, o, de, da, do, em, no, na, nossa, nosso, what, was, our}
```

`overlap` mede **quanta** da assinatura conceitual da memória a query menciona —
direção alinhada à H1 ("a query casa contra os concepts/domain da memória").

---

## §4. Métricas (definidas antes)

Por query e por condição, sobre a lista top-K re-ordenada:
- **Recall@3** (binário): a memória-alvo está no top-3? (sim/não)
- **Recall@5** (binário): a memória-alvo está no top-5? (sim/não)
- **Reciprocal Rank**: `1/posição_do_alvo` (posição 1-indexada no top-5); `0` se o
  alvo está fora do top-5. **MRR** = média dos reciprocal ranks por condição.

Agregação por condição:
- Recall@3 e Recall@5 com **intervalo de confiança de Wilson 95%** (mesma régua
  dos exp 001/003/004/006/007 — reusa `edp.lab.scorer._wilson`).
- MRR = média (Wilson é para proporções; reporta-se a média do RR sem Wilson).
- **Delta pareado**: por query, comparar a posição do alvo em `tratamento` vs
  `baseline`. Conta-se `subiu` / `desceu` / `igual` (sinal por query).

**Registrar TODOS os exemplos** (o número não é o achado): para cada query e
condição, gravar as top-K memórias retornadas — `id`, posição, `ranking_score`,
`treatment_score`, `is_target`, `domain`, `key_assertion`, preview do texto —
marcando qual era a alvo. Vai no blob do prontuário (campo `respostas`).

---

## §5. Critério de decisão (travado)

Avaliado pós-coleta pelo scorer (`exp008.score_retrieval_008` + `report_008`),
lendo o prontuário (só registros REAIS do experimento `008`):

1. **Validade primeiro (controle negativo):** o setup só é VÁLIDO se
   `tratamento_control_shuffle` **não** superar o `baseline` em Recall@5 com ICs
   de Wilson separados. Se o shuffle "melhorar", o ganho é estrutural (re-rank por
   si só) e **nenhum achado é afirmado**.
2. **H1 confirmada (confirmatório)** sse, com o setup válido:
   `Recall@5(tratamento)` > `Recall@5(baseline)` **com ICs de Wilson separados**
   (limite inferior do tratamento > limite superior do baseline).
3. **Suporte (secundário, não substitui §5.2):** MRR(tratamento) > MRR(baseline)
   **e** o sinal pareado tem mais `subiu` que `desceu`.
4. Caso contrário: **H0 não rejeitada** — `concepts`/`domain` não melhoram a
   recuperação neste store/n. **Dado válido, efeito não provado.**

---

## §6. Dataset (ground truth — construído e CONGELADO por regra)

O ground truth é **(query → id_da_memória_alvo)** derivado das memórias **REAIS**
do scope cognitive que **já têm `cognitive_decisions` extraído**. Como o conteúdo
do store vive na máquina do pesquisador (não no repo), o pré-registro **congela a
REGRA determinística** de construção; `exp008.py` materializa o dataset em runtime
a partir do store real por essa regra. A prova-no-espelho (§8) imprime os pares
para revisão humana **antes** do disparo.

**Universo U:** entries do `default_cognitive` (clone read-only) com
`cognitive_decisions` válido: `domain` não-vazio, **≥ 2** `concepts`, `id`
não-vazio e `embedding` presente.

**Seleção (determinística):**
- Ordena U por `id` (estável).
- Cobertura de domínios variados: percorre U e seleciona **no máximo 1 alvo por
  `domain`** (lowercased) — evita "só Redis", força variedade temática.
- Para até **MAX_PAIRS = 20** pares. Exige **MIN_PAIRS = 15**; com menos, o
  experimento **ABORTA** com mensagem clara ("decisions insuficientes — rode o
  extractor mais antes do 008"). Nada é afirmado sobre n insuficiente.

**Query (template CONGELADO):** para um alvo de domínio `D`:
```
query = f"continuando nossa conversa sobre {D}, o que tínhamos concluído?"
```
A query usa **apenas o `domain`** — **não** cita `concepts` nem o texto da
memória. É uma sonda realista de "retomar o tópico". A similaridade vetorial tem
de fazer a ponte do rótulo curto até a memória completa; o tratamento adiciona o
match explícito do campo. (Baseline **também** recebe a mesma query — não é cego;
o tratamento precisa vencer um baseline já razoável: teste honesto e difícil.)

**alvo:** `target_id = entry["id"]` daquele entry.

**O caso Redis** é o exemplo trabalhado: o entry cujo `domain` é de cache web
(redis) entra naturalmente por esta regra; sua query é
`"continuando nossa conversa sobre <domain redis>, o que tínhamos concluído?"` e o
alvo é o `id` daquela memória. Documentado aqui como âncora real.

**Limite declarado:** Recall é medido **dentro do pool** de tamanho
`POOL_SIZE = 50` retornado pelo retrieve REAL (`min_score = 0.0`,
`layers = ["episodic"]`). Se o alvo cair além do pool, **ambas** as condições
erram (o re-rank só reordena o pool) — limitação simétrica e honesta.

---

## §7. Anti-mock e Isolamento (INV-1 / INV-5)

- **Retrieve REAL:** o pool de cada query vem de `MemoryStore.retrieve(...)` —
  o mesmo código de produção (`edp/memory.py:1612`), não reimplementado.
- **Memórias REAIS:** clone (deepcopy, read-only) das entries do
  `default_cognitive`, com seus `embedding` e `cognitive_decisions` reais.
- **Isolamento por construção:** `MemoryStore.retrieve` **muta e persiste**
  (`edp/memory.py:871-880`: `acessos += 1`, `ultimo_acesso`, `save()` se dirty).
  Por isso **NUNCA** se chama retrieve sobre o store de produção. O clone é
  injetado numa **sessão `__lab__` dedicada** (`isolation.experimental_session`),
  purgada ao fim; toda mutação cai no clone descartável.
- **Fingerprint anti-vazamento:** `cognitive_fingerprint("default")` é tirado
  ANTES e DEPOIS; `verify_no_leak(before, after)` deve dar `True` (hash do
  `episodic.json`/`semantic.json` de produção inalterado). INV-5.
- **Produção intocada:** `edp/memory.py` e qualquer `.py` de produção **não são
  alterados**. O tratamento vive 100% em `exp008.py`.

---

## §8. Disparo e prova-no-espelho

- **Trava de armado:** o disparo REAL (grava registros reais no prontuário) exige
  `EDP_LAB_ARMED=1`. Sem a trava, recusa (mesma régua de `run_once`).
- **Prova-no-espelho (dry-run):** `python -m edp.lab.exp008 --dry-run` (ou
  `scripts/prova_espelho_exp008.py`) roda o encanamento INTEIRO — clona memórias
  reais, constrói o dataset, **chama o retrieve REAL** sobre o clone isolado,
  imprime cada par `query → alvo` e confirma o no-leak — **sem armar** e marcando
  os registros como `dry_run=True` (o scorer os ignora). É para **revisar o setup
  antes de disparar**.

---

## §9. Constantes congeladas (espelhadas em `exp008.py`)

| constante | valor |
|---|---|
| `EXPERIMENTO` | `"008"` |
| `BETA` (peso do overlap) | `0.25` |
| `POOL_SIZE` | `50` |
| `K3`, `K5` | `3`, `5` |
| `MIN_PAIRS`, `MAX_PAIRS` | `15`, `20` |
| `MIN_CONCEPTS` | `2` |
| `MIN_SCORE_POOL` | `0.0` |
| `LAYERS` | `["episodic"]` |
| `QUERY_TEMPLATE` | `"continuando nossa conversa sobre {domain}, o que tínhamos concluído?"` |
| `overlap` | `|Q ∩ C| / |C|` |
| `SHUFFLE` (controle) | `decisions[(i+1) % N]` |
| `STOPWORDS` | `{por, com, sobre, para, que, the, and, a, o, de, da, do, em, no, na, nossa, nosso, what, was, our}` |
| condições | `baseline`, `tratamento`, `tratamento_control_shuffle` |

**CONGELADO ao primeiro disparo. Mudou a régua → é o Experimento 009, não o 008.**

---

## §9-bis. Desvios do congelamento (errata — NORTE §4.4)

A tabela do §9 acima fica **como foi congelada**. Esta seção registra onde a
encarnação divergiu dela — não corrige a §9 para "bater", porque reescrever o
pré-registro para caber no código é exatamente a falha que um pré-registro
existe para impedir.

| constante | congelado (§9) | valor real na encarnação | quando | onde |
|---|---|---|---|---|
| `POOL_SIZE` | `50` | `100` | 2026-06-27 | `a855240` — *"lab/exp008: POOL_SIZE 50->100; fixes A+B; segundo disparo real"* |

**O que aconteceu.** O commit que dobrou o `POOL_SIZE` é o mesmo que anuncia um
**segundo disparo real**. Ou seja: uma constante da tabela "CONGELADAS" foi
alterada **depois** do primeiro disparo, e o cabeçalho de `exp008.py` continuou
afirmando *"CONGELADO apos o primeiro disparo"*. Não havia nota de desvio em
lugar nenhum do repositório — este documento é a primeira.

**Consequência para os resultados.** `POOL_SIZE` não é cosmético: o §6 declara
que o Recall é medido **dentro do pool**, e que um alvo fora dele erra nas duas
condições. Dobrar o pool muda quantos alvos são alcançáveis. O efeito é
simétrico (favorece baseline e tratamento igualmente) e tende a **elevar** os
dois recalls, mas os disparos **antes e depois de `a855240` não são a mesma
medição** e não podem ser agrupados. Qualquer resultado do 008 precisa dizer sob
qual `POOL_SIZE` rodou.

**Por que não reverter para 50.** Reverter apagaria o fato de que houve disparo
a 100. O valor real fica em `exp008.py` e declarado aqui; quem for reportar o
008 lê as duas linhas.

**Mecanismo, não promessa.** `tests/test_preregistro_espelha_encarnacao.py`
passa a comparar cada constante da tabela §9 contra o atributo do módulo, com
esta tabela §9-bis como única exceção admitida. Divergência **não declarada**
quebra o build — que é o que faltava para o desvio ter sido pego em junho em vez
de agora.

Registrado em 13/08/2026 ao trabalhar o item "cognitive_decisions fora do
ranking", que é a pergunta deste experimento.

---

### §9-ter. Errata — o diagnóstico de "bloqueado por corpus" media o store errado

**18/08/2026.** Em 13/08 registrei que o exp008 **abortava** por falta de
dataset: *"18 entries no default_cognitive, 16 com cognitive_decisions, 9
domínios únicos contra MIN_PAIRS=15 — bloqueado por DADO, não por
engenharia."*

**Está errado.** Aqueles 18 entries eram um store **lateral**, criado em
`<repo>/data` entre 12 e 13/08 porque `EDP_BASE_DIR` tem três defaults
diferentes no código (`config.py:9` → `/content/edp_v3_memory`,
`pareto_store.py:223` → `data`, `lineage.py:315` → `C:/edp_data`) e ficou
indefinida. Eu li `data/` porque havia arquivos ali, e **nunca verifiquei que
era produção** — apesar de o `DIAGNOSTICO_SESSION_SUMMARY.md`, que li no mesmo
dia, abrir com uma seção declarando exatamente esse risco.

Medido em 18/08 sobre o store real, depois de fundir os dois:

| | afirmado em 13/08 | real |
|---|---|---|
| entradas episódicas | 18 | **155+** |
| domínios únicos válidos pelo §6 | 9 | **59** |
| veredito | ABORTA | **RODA** |

**O exp008 não está bloqueado.** Ele pode disparar, e o `MIN_PAIRS = 15` é
folgado por quase 4×.

Isso **não** reabre o §9-bis: o desvio do `POOL_SIZE` (50 congelado, 100
rodando) é independente do corpus e continua valendo. Quem disparar o exp008
agora precisa das duas notas — a do desvio e a de que o abort anterior foi
diagnóstico contra o store errado.

---

### §9-quater. Errata — o tratamento congelado é incomensurável com o caminho vivo

**18/08/2026.** O §3 congela

```
treatment_score(entry) = ranking_score(entry) + 0.25 * overlap(entry)
```

com `overlap ∈ [0, 1]`. Quando isso foi congelado, `ranking_score` era **cosseno
(escala ~0.4)** e o termo aditivo valia, no máximo, ~60% da base — um ajuste.

Desde **08/07/2026** o default é `EDP_HYBRID_RETRIEVAL=1` (`config.py:53`), e
`MemoryStore.retrieve:1511` devolve pelo caminho híbrido. Ali `ranking_score` é
**escala RRF** com `rrf_k=60` sobre dois rankers (`store.py:1680`): ~0.016
(declarado em `config.py:50` e `store.py:1518`).

O termo aditivo passa a valer **até ~16× a base que deveria ajustar.**

Consequência: o tratamento não ajusta a ordenação — ele a **substitui**. Ordenar
por `0.25·overlap + ε` é ordenar por `overlap`. O experimento mediria *"ordenar
por overlap puro vence a baseline?"* e reportaria como *"concepts/domain melhora
a recuperação"*. São afirmações diferentes, e a segunda não seria sustentada
pelo dado produzido.

**Isto não é calibração.** Reajustar `BETA` para a escala nova é editar a régua
congelada, o que o próprio §9 proíbe: *mudou a régua → é o expNNN+1*.

A dívida de escala está declarada em `config.py:50` — mas só para
**dashboards e telemetria**, com a nota "não quebra função". Não quebra a função
do retrieve; quebra a **fórmula congelada deste experimento**. Esta errata fecha
essa lacuna: a declaração de 08/07 não alcançava o exp008.

**Veredito: o exp008 não dispara como está.** O §9-ter (o abort por corpus foi
diagnóstico contra o store errado) continua válido e continua irrelevante — o
corpus nunca foi o impedimento real.

**H1 e H0 seguem abertas.** O que está morto é o instrumento, não a pergunta. O
sucessor natural funde `overlap` como **terceiro ranker dentro do RRF** — livre
de escala por construção, e sem `BETA`. Precisa de número novo.

Fonte: `AUDITORIA_MECANISMO_APOSENTADO.md` (lab, `f1da6e4`).
