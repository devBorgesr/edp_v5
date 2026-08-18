# Diagnóstico — "session_summary dominante no retrieve"

**Tipo:** auditoria de diagnóstico (somente leitura + medição). **Nenhum código
de produção alterado, nenhum fix aplicado.** Todas as medições rodaram sobre
CÓPIAS de dados reais, nunca sobre `C:\edp_data` / a produção original.

**Régua:** zero confiança em intuição. Cada afirmação é ancorada em `file:line`
executável ou em número real medido. Onde não há dado de produção acessível, isto
é declarado explicitamente — nada é inventado.

---

## ⚠️ Limite de ambiente (declarado antes dos números)

A produção vive em `C:\edp_data` na máquina do pesquisador (Windows). Este
diagnóstico rodou num container Linux isolado onde **essa pasta não existe**. Os
dados reais acessíveis aqui são **fixtures de teste/curadoria**
(`/content/edp_v3_memory/sessions/test_antigo_cognitive`,
`test_curado_cognitive`), NÃO a produção que gerou a observação original do
session_summary de 16 acessos.

Consequência honesta:
- As seções de **código/mecanismo (3, 4)** são **definitivas** — o código é o
  mesmo que roda em produção.
- As seções **empíricas (1, 2)** trazem **números reais medidos**, mas sobre a
  fixture de teste (11 entries, sintética e homogênea), não sobre produção.
  Servem para **demonstrar o mecanismo disparando de verdade**, com a ressalva
  de escala/representatividade. A quantificação definitiva da produção exige
  rodar o mesmo harness (`measure_ss_dominance.py`, anexado no fim) sobre uma
  cópia de `C:\edp_data`.

Mesmo com essa ressalva, a fixture **reproduz o fenômeno** para o cenário
reportado (queries de continuação) — ver §2.

---

## §1. Inventário (sobre a cópia real — fixture `test_antigo_cognitive`)

Medido com `json.load` puro (sem instanciar retrieve, sem mutação):

| métrica | valor real medido |
|---|---|
| entries totais | **11** |
| `session_summary` | **3** |
| `llm_response` | 7 |
| `user_input` | 1 |
| entries com `embedding` | 11/11 |
| **acessos das session_summary** | **[13, 15, 20]** — média 16.0, **máx 20** |
| acessos das normais | média 12.4 |

Observação: as 3 `session_summary` carregam os acessos **no topo** da
distribuição (uma delas, `acc=20`, empata com o máximo global). Isso é
**consistente** com a observação de produção (o session_summary de `acc=16`
dominando), mas num snapshot estático não se distingue causa de efeito — o
`acessos` sobe **toda vez que o retrieve devolve a entry** (feedback loop, ver
§3/§4). A fixture é sintética (tópicos todos de RAG/embeddings), então os números
absolutos não representam produção; o padrão (summary no topo dos acessos) sim.

---

## §2. Dominância empírica — retrieve REAL sobre a cópia

Rodei o **retrieve REAL** (`edp.memory.MemoryStore.retrieve`, o mesmo do hot
path) sobre uma CÓPIA da fixture. Isolamento contra mutação: `EDP_BASE_DIR`
aponta para uma cópia de trabalho, e o diretório da sessão é **restaurado de um
snapshot pristine antes de cada query** — o `acessos++`/`save()` (memory.py:871-880)
nunca acumula nem toca o original. `top_k=10`, `min_score=0.0`.

### 2a. Queries ESPECÍFICAS (alvo claro existe no store) — n=6

| métrica | valor real |
|---|---|
| % médio do **top-10** que é `session_summary` | **28.3 %** |
| % médio do **top-5** que é `session_summary` | **16.7 %** |
| queries em que o alvo específico foi **expulso do top-5** | **0 / 6** |
| ranks do alvo específico | `[2, 1, 1, 1, 1, 1]` |

Leitura honesta: quando a query é específica e casa forte com uma memória
concreta, o **cosseno cru** daquela memória (0.50–0.76) domina, e os
`session_summary` (score 0.2–0.36) ficam no **miolo** (posições 5–10) — **não
expulsam** o alvo. Ou seja: para query específica, a similaridade vence os
boosts. O session_summary **consome orçamento** de retrieval (≈1/6 do top-5), mas
não é o pior caso.

### 2b. Queries VAGAS de continuação (o cenário reportado) — n=4

Exatamente o caso "vamos continuar a conversa…": queries sem alvo específico.

| métrica | valor real |
|---|---|
| % médio do **top-5** que é `session_summary` | **45.0 %** |
| exemplo `"vamos continuar nossa conversa"` | **3/5** do top-5 são summary, ocupando **ranks 1 e 2** |

Trecho real (query = `"vamos continuar nossa conversa"`):
```
 1. score=0.345 [session_summary] acc=20 | Memória semântica preserva conhecimento...   <<TOPO
 2. score=0.335 [session_summary] acc=13 | Chunking semântico preserva coerência...     <<TOPO
 3. score=0.313 [llm_response   ] acc=11 | FAISS permite busca eficiente...
 4. score=0.285 [user_input     ] acc=11 | Python é uma linguagem lenta...
 5. score=0.281 [session_summary] acc=15 | Retrieval híbrido combina BM25...
```

**Este é o fenômeno.** Sem um alvo específico para "ancorar" o cosseno, as
memórias `session_summary` — embedding genérico + vantagem de score (§3/§4) —
**tomam os primeiros lugares**. O `retrieval_monitor` inclusive disparou
`"retrieval REPETITIVO: 80% dos turnos retornam memorias iguais"` durante a
medição — sintoma do mesmo punhado de entries (incluindo summaries) voltando.

**Contraste medido:** vaga 45 % vs específica 16.7 % do top-5. O dano é
**condicional ao tipo de query**, e concentra-se justamente nas aberturas de
conversa ("continuando…", "o que falávamos…"), que são frequentes na vida real.

> Nota de subestimação: nesta fixture os `session_summary` estão na camada
> **semântica**, cujo retrieve (memory.py:1207-1258) aplica **só** o multiplicador
> epistêmico. Em **produção** os `session_summary` nascem na **episódica**
> (§3), cujo retrieve aplica a **pilha completa** de boosts (§4). Ou seja, o
> número real de produção tende a ser **pior** que os 45 % medidos aqui.

---

## §3. Causa — por que o embedding do session_summary é "genérico"

**Confirmado em código:** o embedding do session_summary é gerado sobre o
**texto-resumo**.

- `edp/session_summary.py:214` → `summary_emb = embed_one(summary_text)` — embedda
  o resumo.
- `edp/session_summary.py:240` → `text_to_store = f"[session_summary] {summary_text}"`
  e é persistido com `source_type="session_summary"` (`:252`),
  `prioridade="alta"` (`:244`), `epistemic_status="verified"` (`:247`).

Um resumo é, por construção, uma frase de **alto nível** ("Memória semântica
preserva conhecimento consolidado entre sessões") — semanticamente **central**,
logo próxima de muitas queries. Medição direta da "genericidade" (cosseno médio
de cada entry para **todas as outras**, usando os embeddings reais já
armazenados, sem modelo):

| grupo | genericidade média (cosseno médio a todas as outras) |
|---|---|
| `session_summary` (n=3) | **0.383** |
| entries normais (n=8) | **0.355** |
| razão | **1.08×** |

Sinal na direção da hipótese (summary é mais central), porém **fraco nesta
fixture** — porque ela é pequena e tematicamente homogênea (tudo RAG/embeddings),
o que comprime a diferença. Em um store real e diverso, um resumo "médio"
destaca-se muito mais do que memórias específicas. **Exemplos reais** dos 3
session_summary e seus acessos:

```
[session_summary] acc=20 | Memória semântica preserva conhecimento consolidado entre sessões.
[session_summary] acc=15 | Retrieval híbrido combina BM25 léxico com busca vetorial para maior recall.
[session_summary] acc=13 | Chunking semântico preserva coerência melhor que chunking por tamanho fixo.
```

Note como cada um é uma **generalização** — não um fato específico ancorável.

---

## §4. Escopo do dano — o hot path sofre? Há algum filtro?

### 4a. O hot path usa o retrieve sem filtro — confirmado

- `edp/api/routes/websocket.py:716` → `retrieved = memory.retrieve(message, top_k=5, min_score=0.20)`.
- `edp/memory.py:1612` (`MemoryStore.retrieve`) delega a
  `episodic.retrieve` (`:1632`) e `semantic.retrieve` (`:1635`). **Nenhum** dos
  três filtra `source_type == "session_summary"` (confirmado por leitura das três
  funções: 1612-1667, 627-899, 1207-1258).

### 4b. Não só não filtra — BOOSTA. A pilha de score (episódica, produção)

`edp/memory.py:739-743` compõe o rank assim:
```
rank_score = sim * decay * prio * access_boost * epi_multiplier * src_weight * dom_penalty * anchor_boost * session_boost
```

> **Errata — 13/08/2026 (registrada no original em 18/08).** A fórmula acima
> tem **nove** fatores e o produto real tinha **dez**. Falta `nf_floor`
> (`NOT_FOUND_FLOOR`, derruba o score em 20× quando a entrada é de classe
> tóxica e `EDP_WRITE_PROVENANCE` está ligada). Ele já estava no produto na
> mesma revisão auditada — `memory.py:723-724` (definição) e `:748-751`
> (multiplicação) —, fora do trecho `739-743` citado. Hoje o mesmo produto
> vive em `edp/memory/store.py:611-616`.
>
> A omissão **não muda a conclusão desta seção**: para uma `session_summary`,
> `nf_floor = 1.0` (não é classe tóxica), então o décimo fator é neutro
> justamente no caso analisado. O que ela mostra é que a fórmula foi enumerada
> de memória em vez de lida por inteiro — o mesmo erro se repetiu em 13/08 na
> instrumentação do ranking, e foi o que motivou o teste de guarda
> `tests/test_ranking_telemetry.py::test_nf_floor_esta_no_dict_do_ranking`,
> que trava os dez juntos. Texto original preservado.
>
> **Nota de localização:** esta errata esteve por engano na cópia do
> `lab_edp_novo` (`docs_edp_v5/`), pasta que a `docs/DIVISAO.md:87` daquele
> repo declara como herdada e **não editável**. Revertida lá, registrada aqui
> — no original, que é a fonte.
Para uma `session_summary` (episódica), cada fator joga a favor:

| fator | valor p/ session_summary | valor p/ llm_response típico | fonte |
|---|---|---|---|
| `prio` (prioridade) | **1.30** (alta) | 1.00 (media) | `config.py:45`; set em `session_summary.py:244` |
| `src_weight` (source_type) | **1.15** (boost) | 0.90 (desconto) | `memory_classifier.py:144,159` |
| `epi_multiplier` | **1.00** (verified) | 0.85 (hypothesis/default) | `memory.py:696,1241`; set em `session_summary.py:247` |
| `access_boost` | ~1.14 (acc=16) | ~1.06 (acc≈2) | `temporal.py:42` = `1+0.05·ln(1+n)` |
| `dom_penalty` | 0.70 **se** top-3 por acessos | 1.00 | `memory.py:707` |

**Multiplicador combinado (mesmo cosseno cru):**
- session_summary: `1.30 × 1.15 × 1.00 × 1.14 ≈ **1.70×**`
- llm_response típico: `1.00 × 0.90 × 0.85 × 1.06 ≈ 0.81×`
- **Vantagem ≈ 2.1×** a favor do session_summary no mesmo cosseno.
- Mesmo **com** a `dom_penalty` de 0.70 (quando é top-3 por acesso):
  `1.70 × 0.70 ≈ 1.19` → ainda **≈ 1.5×** acima do llm_response típico.

O único freio existente (`dom_penalty` 0.70) é **mais fraco** que a soma dos
boosts. E há um **feedback loop**: o retrieve muta `acessos += 1`
(`memory.py:871`) toda vez que devolve a entry → `access_boost` sobe → mais
provável voltar. A observação de "16 acessos" é esse loop rodando.

### 4c. Os filtros que EXISTEM não cobrem o pool de similaridade

Há 3 filtros de `session_summary` no código — **todos na janela cronológica de
"turnos recentes", nenhum no pool de similaridade**:

- `edp/api/routes/websocket.py:696` — filtra da **"janela imediata"** (últimos 2
  turnos exibidos na UI). Roda **antes** do retrieve da linha 716, é outra camada.
- `edp/llm_adapter.py:298` — filtra do **histórico de conversa** (últimas N
  entradas cronológicas).
- `edp/llm_adapter.py:2150` — filtra do **bloco histórico cronológico** (até 12
  turnos da sessão atual).

Ou seja: os desenvolvedores **já reconheceram** que o session_summary não deve
entrar na janela de turnos recentes (e o removem lá), mas o **deixaram — e o
boostaram — no canal de recuperação por similaridade**, que é o do hot path.
O dano atinge **toda conversa real** cuja abertura seja vaga/de continuação.

---

## §5. Veredito + opções de fix (descritas, NÃO implementadas)

### Veredito

**SIM, o fenômeno é real e o mecanismo está em produção — de forma CONDICIONAL:**

1. **Mecanismo (definitivo, código):** o pool de similaridade do hot path
   (websocket.py:716) **não filtra** session_summary e ainda lhe dá **~2.1× de
   vantagem de score** no mesmo cosseno (§4b), com feedback loop de acessos. Os
   filtros existentes cobrem só a janela cronológica, não o retrieve (§4c).
2. **Empírico (fixture real, com ressalva de escala):** em queries **vagas de
   continuação** — o cenário reportado — session_summary ocupam **45 % do top-5**
   e os **ranks 1–2** (§2b). Em queries **específicas**, o cosseno forte protege o
   alvo (**0/6 expulsos**, §2a). A produção tende a ser **pior** que a fixture,
   pois lá os summaries estão na episódica com a pilha completa de boosts.

**Portanto:** o session_summary **domina e prejudica** o retrieve **para as
aberturas vagas/de continuação de conversa** (comuns), e **desperdiça orçamento**
(1/6 do top-5) mesmo em queries específicas. Não é um "sempre domina": é um
"domina exatamente quando não há âncora específica" — que é justamente quando o
usuário mais depende do retrieve para reconstruir contexto.

> Número de produção pendente: rodar `measure_ss_dominance.py` (anexo) sobre uma
> cópia de `C:\edp_data` fecharia o valor exato para o store real. O harness já
> está pronto e é read-only-sobre-cópia.

### Opções de fix (prós/contras — nenhuma aplicada)

**(a) Filtrar `session_summary` do pool de retrieval por similaridade.**
- Prós: elimina a dominância no canal errado; simples (1 filtro em
  episodic/semantic.retrieve, espelhando o que já existe na janela cronológica).
- Contras: cego — perde o caso legítimo em que o resumo É a melhor resposta
  (ex.: "o que consolidamos na sessão passada?"); o resumo deixa de ter qualquer
  tração por similaridade.

**(b) Despriorizar (penalidade no score) em vez de remover.**
- Prós: mantém o resumo recuperável, mas tira o boost; basta inverter o
  `src_weight` de `1.15` → `<1.0` (ex.: 0.6) em `memory_classifier.py:144` e/ou
  remover `prioridade="alta"`/`verified` do gravador. Reversível e calibrável.
- Contras: escolher o peso é empírico (risco de over/under-correção); não ataca a
  causa-raiz (embedding genérico); o feedback loop de acessos persiste, só mais
  fraco.

**(c) Não recuperar por similaridade; injetar por outra via.**
- Prós: reconhece que resumo é **contexto de sessão**, não **memória
  recuperável por tópico** — melhor lugar arquitetural. Injetar 0–1 resumo
  relevante por regra explícita (ex.: resumo da sessão atual/último tópico) fora
  do pool de cosseno.
- Contras: mais trabalho (nova via de contexto + regra de seleção); precisa
  garantir que o resumo certo seja escolhido sem o cosseno.

**(d) Corrigir como o embedding é gerado.**
- Prós: ataca a causa-raiz (§3). Ex.: embeddar sobre os **conceitos/tópicos** do
  resumo (mais específico) em vez do texto-resumo médio; ou não indexar o resumo
  por embedding.
- Contras: o mais incerto — "resumo mais específico" pode continuar genérico;
  muda pipeline de gravação; efeito difícil de prever sem medir.

### O que medir num experimento limpo da Bancada (exp009)

**Opção (b) — despriorização — é a mais promissora para medir primeiro**, porque
é um **único parâmetro** (`src_weight` do session_summary), reversível, e
diretamente comparável A/B sobre o retrieve real. Desenho sugerido, no molde do
exp008 (retrieval-quality, sem tocar produção):

- **H1:** despriorizar session_summary (ex.: `src_weight` 1.15 → 0.6) **reduz** a
  fração do top-5 ocupada por session_summary em queries vagas **sem** derrubar o
  Recall@5 da memória-alvo específica em queries específicas.
- **Condições (mesmo store, mesmas queries):** `baseline` (peso atual 1.15) vs
  `despriorizado` (peso 0.6) — re-rank de **leitura** no lab, sem alterar
  `edp/memory.py`, exatamente como o tratamento do exp008.
- **Dataset:** dois conjuntos de queries reais — (i) **vagas de continuação**,
  (ii) **específicas com alvo conhecido** (reusar a regra de ground truth do
  exp008).
- **Métricas:** % do top-5 = session_summary (vaga) ↓ é bom; Recall@3/@5 do alvo
  específico deve **não** cair (guarda contra over-correção); MRR; tudo com
  Wilson. Controle: peso neutro (1.0) como ponto médio.
- **Isolamento/anti-mock:** clone read-only + sessão `__lab__` + fingerprint, como
  exp008 — o retrieve real muta (`memory.py:871-880`), então nunca rodar sobre
  produção.

Isso dá o número que decide entre (a)/(b)/(c)/(d) com dado, antes de tocar o Core.

---

## Anexo — reprodutibilidade (harness read-only-sobre-cópia)

O número de produção pode ser fechado rodando, sobre uma **cópia** de
`C:\edp_data` (nunca a original):

1. `robocopy C:\edp_data C:\edp_data_diag /E` (ou reusar `C:\edp_data_exp008`).
2. Apontar `EDP_BASE_DIR` para a cópia e restaurar o diretório da sessão de um
   snapshot pristine **antes de cada query** (o retrieve muta `acessos`/salva —
   `memory.py:871-880`).
3. Para cada query (10–15 de continuação + 10–15 específicas sobre tópicos que
   existem no store), chamar `MemoryStore(<sessão>).retrieve(q, top_k=10,
   min_score=0.0)` e contar `source_type=="session_summary"` no top-5/top-10 e o
   rank do alvo específico.

A lógica exata usada neste diagnóstico (com restauração pristine por query e a
distinção vaga/específica) está descrita em §2 e foi executada de fato sobre a
fixture `test_antigo` — os números de §2 são a saída real desse procedimento.

**Nada de produção foi alterado. Nenhum fix aplicado. Medições sobre cópias.**
