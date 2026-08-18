# EDP — Metodologia e Estado do Sistema (v5, kernel)

**Data desta rodada de verificação: 2026-08-11.** **Commit:** `556b4d9`.
**Escopo:** este documento cobre `edp_v5` — o **kernel** na formulação de
três repositórios do `NORTE.md §1`. `lab_edp_novo` (certificação) e
`sf_exportador` (sensor/copiloto) são vizinhos, não conteúdo coberto aqui.

**Sucessor de** [`docs/edp_metodologia.md`](edp_metodologia.md) (07/06/2026,
preservado intacto com errata datada no topo — regra 3 do método, §4.4).
Não é reescrita: é o mesmo território, medido de novo, com número
reproduzível em vez de herdado. Onde este documento cita "não verificado
nesta rodada", é honesto — não foi checado, não é "não existe".

---

## 1. Pergunta de pesquisa

O que o EDP tenta provar, na formulação do `NORTE.md §1`: que é possível
tornar um sistema de memória para LLM **auditável, econômico e
verificável** o suficiente para uso com dados reais — não "IA mais
inteligente", mas IA cuja confiabilidade é medida, não presumida.

A aposta metodológica central, citável e testada nesta própria sessão de
verificação: **o rigor do processo de pesquisa é, em si, o produto mais
maduro do projeto** — mais do que qualquer módulo de código. É a única
dimensão que tirou nota 9 em 10 numa auditoria de engenharia externa
(`AVALIACAO_ENGENHARIA_EDP.md`, dimensão 4, 22/07/2026, **[P] 9 / [C] 5**).

---

## 2. O método

Este documento **estende** `docs/edp_metodologia.md` — os 6 Princípios EDP
e as 4/5 Dimensões de Investigação Prévia continuam em vigor e são citados
por `NORTE.md §4.8`. O que muda aqui é a codificação mais recente e mais
citada externamente, em `NORTE.md §4` (reescrito 07/08/2026), com origem
rastreável em incidentes reais deste próprio repositório:

| regra | por quê existe (evidência) |
|---|---|
| Passo 0 — verificar antes de afirmar | cadeia de evidência de bug de timestamp com elo falso, 06/08 |
| Pré-registro antes do dado, com critério numérico congelado | `PRE_REGISTRO_EXP017.md`, `docs/preregistro_degrau1_honeypot.md` |
| Critério inatingível se declara, não se esconde | "H2 INFALSIFICÁVEL COMO DESENHADO" citado nominalmente pela auditoria |
| Errata pública, texto original preservado | ERR-1/2/3 do EXP017; corrigido de novo agora, neste documento |
| Controle negativo + predição pré-dado | auditoria dimensão 4: "predição pré-dado batendo exatamente (E6)" |
| Prova de inércia antes de deletar | `test_run_pipeline_characterization.py:73-109` |
| Feature flag + flag-off byte-idêntico | `tests/test_flag_off_byte_identical.py`, aplicado a toda flag nova |
| Forma, nunca categoria | bug reincidente com 1 mês de distância (3c.α-fix2 → ζ/#46c) |
| Honestidade de escopo do resultado | o que um número **não** autoriza concluir é parte do resultado |

**O que a mesma auditoria disse contra, e que segue verdade hoje**
(`AVALIACAO_ENGENHARIA_EDP.md`, dimensão 4, **[C] 5**): *"esse rigor é
recente e concentrado nos ciclos exp0XX; a maior parte dos parâmetros de
scoring do sistema nunca passou por esse processo — é um padrão adotado,
não retroaplicado."* Ver §4 abaixo para os parâmetros específicos, e §5
para o inventário completo que **este próprio documento** aplicou o
método a si mesmo enquanto foi escrito (Passo 0 em cada claim herdada dos
30+ relatórios da raiz).

---

## 3. Inventário de experimentos — validado / hipótese / refutado

Fonte primária de cada linha: o relatório do próprio experimento. Datas
como escritas nos documentos-fonte; não recontadas nesta rodada salvo
indicação.

| experimento | veredito | evidência | data |
|---|---|---|---|
| exp008 — retrieval baseline | validado, mergeado | branch `lab/exp008-retrieval`, mergeada em `main` | 27/06 |
| exp009 — dominância de session_summary | **validado** — dominância caiu de ~87% para ~33% após remover prioridade hardcoded | `docs/edp_metodologia.md` (herdado, não re-medido nesta rodada — **não verificado nesta rodada**) | 02/07 |
| exp010 — retrieval híbrido (BM25+vetorial, RRF) | validado, atrás de flag `EDP_HYBRID_RETRIEVAL` (default ON hoje, `config.py`) | mergeado em `main`; citado por `AVALIACAO_ENGENHARIA_EDP.md` §7 | 03/07 |
| exp011 — ctx-slots | validado no §1.4 + guardas 1-4; guardas 5-6 com mecânica verificada, rodada oficial delegada ao pesquisador | `VALIDACAO_EXP011.md` | 07/07 |
| exp012 — write-provenance / detecção de veneno | regra v1 **REFUTADA** na calibração (0/6 lixos pegos); regra composta R4 (OR) **CONFIRMADA**, F1 superior a R1/R2 isolados | `ESTADO_EXP012.md`, `RELATORIO_ETAPA0_EXP012V2.md` | 08-12/07 |
| exp015 — strong provenance | **REFUTADO** — cabeçalho de proveniência + proibição no system prompt não impediram o modelo de reafirmar desqualificação da janela imediata | `RELATORIO_ETAPA0_EXP016.md`, citando exp015 | 14/07 |
| exp016 — desqualificação auto-referente (3ª classe de veneno) | desenhado e parcialmente aplicado (branch mergeada); dry-run e regra DISQ v1 entregues; **rodada oficial não confirmada nesta verificação** | `RELATORIO_ETAPA0_EXP016.md` | 15/07 |
| exp017 — dedup do retrieve (read-side) | **H1 PASSA** (critério conjuntivo integral); H2 **INCONCLUSIVO-POR-DESENHO** (declarado, não escondido — §4.3 do método) | `EXP017_FASE2.md`, `PRE_REGISTRO_EXP017.md` | 22/07 |
| Degrau 1 — honeypot (cache de respostas) | **H0 vence** — REFUTADO. R1: seletividade invertida, as 4 queries que passariam gate ≥0.70 eram as 4 anafóricas, não as específicas | `docs/preregistro_degrau1_honeypot.md` | 06/08 |
| Wiki de conversas, camada 3 | **REFUTADA por dado** — E-2/E-2.1: 2 de 5 alvos recuperados, critério era ≥3 | `docs/design_wiki_conversas.md` (status no topo do arquivo) | 07/08 |
| Rodagem cruzada da wiki (W) | **H1 FALHA** — bruta 7/24, diferencial 0; grep tem material para 12/12, a wiki para 3 | `docs/preregistro_rodagem_cruzada_wiki.md` | 07/08 |
| Gap Score (3 fórmulas: bruta, IDF, IDF⁰) | **as três FALHAM**, convergindo no mesmo critério (b) — achado localizado num mecanismo (peso de termo ausente), não em "o conceito não presta" | `docs/preregistro_gap_score.md` | 11/08 |
| Gap Score + Haiku (E-2) | **FALHA** — mesma condição (b), 6/8 quando exige 7/8; melhor dos 4 métodos testados, ainda insuficiente | `docs/preregistro_gap_score.md`, resultado E-2 | 11/08 |

**Nota sobre a linha exp009:** este documento herdou o número de
`docs/edp_metodologia.md` sem re-executar a medição — Passo 0 aplicado
parcialmente (o código-fonte da alegação não foi re-lido nesta rodada).
Declarado como tal, não escondido.

---

## 4. Dívida técnica e parâmetros não calibrados — números reais

### 4.1 Cobertura de `docs/DIVIDAS.md`

`docs/DIVIDAS.md` se autodeclara (linha 3) *"lar único e versionado das
dívidas técnicas do projeto"*. Censo real, 11/08/2026:

```
grep -rniE "TODO|FIXME|XXX|HACK|d[ií]vida|hardcod" edp/ --include="*.py"
```
→ **168 ocorrências**.

```
grep -rohE "[Dd]ívida #[0-9]+[a-z]?" edp/ tests/ --include="*.py" | sort -u
```
→ **21 identificadores distintos** referenciados em comentários de
código: `#8, #9, #9b, #10, #11, #12, #24, #40, #41, #42, #45, #46, #46b,
#46c, #46d, #47, #48, #49, #50, #52, #53`.

`docs/DIVIDAS.md` documenta **3**: #41 (FECHADA), #46d (registrada,
não-bloqueante), #53 (FECHADA COM RESSALVA).

**Cobertura real: 3/21 = 14,3%** (por identificador distinto, incluindo
variantes de letra) ou **3/17 = 17,6%** (por número-base, agrupando
variantes). `NORTE.md §4.13` estimava "~12%" sem recalcular — o número
real está na mesma ordem de grandeza, ligeiramente acima da estimativa.

### 4.2 Parâmetros citados pelo `NORTE.md §4.13` — confirmados vivos

| parâmetro | valor | local(is), 11/08/2026 | calibração documentada? |
|---|---|---|---|
| `score` hardcoded | `0.65` | `edp/api/routes/websocket.py:1214,1236` **e também** `edp/llm_adapter.py:2892,2901` (não citado pelo NORTE — achado adicional) | ❌ |
| `DEDUP_THRESH` | `0.75` (default) | `edp/config.py:19`, env `EDP_DEDUP` | ❌ |
| `anchor_boost` | `1.20` | `edp/memory/store.py:576` | ❌ (por analogia, per `RESULTADO_AUDITORIA_EDP_v5.md §3.5`) |

### 4.3 Sinais computados e nunca lidos (loops abertos) — reverificados hoje

Catalogados originalmente em `RESULTADO_AUDITORIA_EDP_v5.md` (24/06) e
`AVALIACAO_ENGENHARIA_EDP.md` (22/07). Reverificado nesta rodada, direto
no código:

| sinal | onde é produzido | status hoje, 11/08 |
|---|---|---|
| `cognitive_decisions` | background job, extrator Haiku | **ainda ausente** de `edp/memory/store.py` — zero ocorrências (`grep -n "cognitive_decisions" edp/memory/store.py`) |
| `contradiction_flagger.scan_results()` | `edp/memory/store.py:1550,1764` | **ainda chamado sem capturar retorno** nos dois pontos — mesmo padrão descartado |
| `reflection.reweights` (`MetaReasoner`) | `edp/pipeline.py:383`, via `meta.reflect()` | **ainda não aplicado** — `reweights=` nunca é atribuído a `PipelineResult` em `pipeline.py` (confirmado por grep, zero ocorrências) |
| `RETRIEVAL_BACKEND` | `edp/config.py:33` | **ainda decorativo** — zero consumidor fora da própria definição (`grep -rn "RETRIEVAL_BACKEND" edp/ tests/` só acha a linha de definição) |

**Em contraste, dois itens do mesmo catálogo original foram corrigidos
desde então** (já registrado por `AVALIACAO_ENGENHARIA_EDP.md`, não
re-medido nesta rodada): `pareto_store` e `health_index` passaram de
"zero leitores" para múltiplos consumidores.

### 4.4 Crash de boot por JSON truncado — CORRIGIDO, confirmado hoje

O item classificado como "achado mais grave" da auditoria de 22/07
(dimensão 6, Segurança & integridade) está **fechado**. Confirmado por
leitura direta: `edp/memory/store.py:348` usa
`_load_json_or_quarantine()` (não mais parse direto sem `try/except`).
Corresponde a Dívida #53 em `docs/DIVIDAS.md` (FECHADA, commit `2524c55`,
6/6 call sites, confirmado em clone limpo real — `docs/VEREDITO_fix_corrupcao_json.md`).

### 4.5 `edp/dashboard/`

```
find edp/dashboard -type f
```
→ `static/dashboard.css`, `static/dashboard.js`, `templates/dashboard.html`.
**Zero arquivos `.py`.** 36K em disco. É front-end estático servido pela
API, não um pacote Python — não é código morto nem scaffold vazio, é
exatamente o que o nome sugere, e a ausência de `.py` é esperada, não um
gap.

### 4.6 Dependências — declarado vs. usado

```
requirements.txt: numpy, scikit-learn, sentence-transformers, fastapi,
uvicorn, pydantic, psutil (opcional), faiss-cpu (opcional), pyyaml
(listado DUAS VEZES no arquivo — defeito editorial, não funcional)
```

Import real (`grep` de `^import\|^from` em todo `edp/`, filtrado para
pacotes externos): `faiss`, `fastapi`, `hnswlib`, `numpy`, `psutil`,
`pydantic`, `sentence_transformers`, `sklearn`, `starlette`, `yaml`,
**`ntplib`**.

**Usado e não declarado:**
- `ntplib` (`edp/clock.py`) — ausente de `requirements.txt` **e**
  `requirements-test.txt`. Instalação limpa sem esse pacote falha ao
  importar `edp.clock`.
- `hnswlib` (`edp/retrieval.py`) — declarado só **comentado**
  (`# hnswlib>=0.7`), não instalado por padrão.

**Achado adjacente:** `edp/retrieval.py` (o módulo que usa `hnswlib` e
`faiss` diretamente) **tem zero importadores em todo o repositório** —
`grep -rln "from.*retrieval import\|import edp.retrieval"` não encontra
nenhum consumidor fora do próprio arquivo. O retrieval vivo é
`edp/retrieval_hybrid.py` (importado por `edp/memory/store.py` e
`edp/lab/exp010.py`). `edp/retrieval.py` está no mesmo estado que
`edp/vector_store.py` e `edp/memory_graph.py` — presente no repositório,
zero consumidor — catalogados pela auditoria de 22/07 e ainda vivos
(mortos) hoje.

> **Errata — 13/08/2026.** O parágrafo acima está errado, e o próprio
> comando que ele publica como evidência o refuta. Rodando
> `grep -rln "from.*retrieval import\|import edp.retrieval"` hoje:
> `benchmark_edp.py` e `run.py`. A linha é
> `run.py:187: from edp.retrieval import RetrievalEngine`, dentro do
> subcomando `bench_retrieval` — e `run.py:250` importa
> `edp/vector_store.py` do mesmo jeito. Nenhum dos dois é código morto;
> o correto é dizer que **não estão no caminho servido**.
>
> A recontagem por AST mostrou que o catálogo errava nas duas direções:
> `analytics.py` e `reranker.py` estavam mortos e sem marca. Só
> `memory_graph.py` estava certo, e foi deletado em 13/08 — o "ligar"
> dele já estava fechado por `docs/design_wiki_conversas.md` §5, que
> refuta aresta por similaridade de embedding.
>
> A lição não é a lista errada, é o método: um `grep` de substring foi
> aceito como prova de ausência sem ser rodado de novo, e sobreviveu três
> semanas em dois documentos. Agora quem confere é
> `tests/test_catalogo_de_modulos_mortos.py`, por AST, no build.

---

## 5. Fronteiras explícitas — o que os resultados acima NÃO autorizam concluir

- **A cobertura de dívida (3/21) não autoriza concluir "17 dívidas
  perdidas foram esquecidas".** Autoriza só: não migraram para o registro
  único. Algumas podem estar resolvidas sem baixa formal; não
  verificado nesta rodada, item a item.
- **"268 testes passam" (ver §6 do README) não autoriza "o sistema não
  tem bug".** Autoriza: os comportamentos que os 298 testes exercitam
  não regrediram. A suite é 100% sintética (`AVALIACAO_ENGENHARIA_EDP.md`
  T2 dim.3) — nunca roda contra um store real; o marker `live_store`
  existe e não tem teste.
- **exp017 H1 PASSAR não autoriza "dedup está calibrado para
  produção".** O próprio relatório fecha com pendências de prioridade
  elevada (furo do piso da `SemanticMemory`) não resolvidas por este
  experimento.
- **As quatro falhas do Gap Score (bruta/IDF/IDF⁰/Haiku) não autorizam
  "navegação por grafo não funciona".** Autorizam: essas quatro
  implementações específicas, contra este gabarito específico de 15
  perguntas, falham. Ver `docs/preregistro_gap_score.md` para o que fica
  aberto (definição de `necessidade`/`capacidade_de_satisfação`,
  vocabulário de tipo de aresta) e `docs/AVISO_INSTANCIA_LIMPA.md` para
  quem pode retomar essa frente sem reconstaminar o gabarito.
- **Nada neste documento autoriza conclusão sobre `lab_edp_novo` ou
  `sf_exportador`.** Fora do escopo declarado no cabeçalho.
- **O veredito [P]/[C] de `AVALIACAO_ENGENHARIA_EDP.md` (6/10 e 3/10) não
  foi recalculado nesta rodada.** As dimensões individuais foram
  reverificadas onde citadas acima (crash de boot: melhorou; loops
  abertos: majoritariamente inalterados); um recálculo completo do
  scorecard de 10 dimensões exigiria nova auditoria externa, não uma
  reconciliação de documentação.

---

## 6. O que mudou desde a versão anterior (`edp_metodologia.md`, 07/06/2026), e por quê

A versão anterior descrevia um sistema num ponto anterior à curadoria de
24/06 (remoção do bloco v3.2) e a tudo que veio depois — 9+ ciclos de
experimento pré-registrado, 4 fases de hardening, e a frente de wiki/Gap
Event de 07-11/08. Manteve-se como documento histórico (regra 3) porque
os 6 Princípios e as Dimensões de Investigação continuam citados e em
uso — só o "Estado Atual" e o vocabulário de commit (letras gregas)
ficaram obsoletos.

Este documento existe porque a alternativa — editar o anterior in-place —
apagaria a data em que cada afirmação deixou de ser verdade, o que o
próprio método (`NORTE.md §4.4`) proíbe.

**Última verificação:** 2026-08-11, commit `556b4d9`. Todo número acima
tem comando ou arquivo:linha citado; onde não foi possível verificar
nesta rodada, está escrito "não verificado nesta rodada", não silêncio.
