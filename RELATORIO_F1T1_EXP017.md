# RELATORIO_F1T1_EXP017.md — Leitura do interior de store.retrieve() (T1, Fase 1)

Branch `exp017/fase1-dedup`, a partir de `main@67f2f5b` (pós-PR #16, merge de
`exp017/fase0-medicao`). Contrato: `PRE_REGISTRO_EXP017.md` (com ERRATA
ERR-1/2/3 + E6) + `EXP017_FASE0.md` (Fase 0 fechada). Este relato é só
leitura — nenhum código de produção foi alterado para produzi-lo.

## (a) Onde episódica e semântica se MERGEM no resultado

**Caminho cosine** (`MemoryStore.retrieve()`, default quando
`EDP_HYBRID_RETRIEVAL=0`) — `edp/memory/store.py:1365-1369`:
```python
if "episodic" in layers:
    results.extend(self.episodic.retrieve(query_emb, top_k, min_score))
if "semantic" in layers:
    results.extend(self.semantic.retrieve(query_emb, top_k, min_score))
```
Concatenação flat em `results: list[dict]`. É aqui que o fenômeno D nasce
no caminho cosine: se o mesmo `id` existe em `episodic.entries` e
`semantic.entries` (Fase 0: 100% da camada semântica), as duas cópias
entram em `results` como itens distintos.

**Caminho híbrido** (`_retrieve_hybrid()`, default hoje —
`EDP_HYBRID_RETRIEVAL` tem default `"1"`, `edp/config.py:53`) —
`edp/memory/store.py:1436-1466`, dentro de `_hybrid_index()`:
```python
for layer, pool in (("episodic", epi), ("semantic", sem)):
    for e in pool:
        ...
        entries_kept.append(e)
        layer_of.append(layer)
```
Merge = indexação conjunta no `HybridRetriever` (`hr.add(...)`,
`store.py:1472-1475`). As duas cópias (mesmo `id`, textos idênticos) viram
DOIS documentos distintos no índice BM25+vetorial — sem qualquer colapso
por id neste ponto.

## (b) Onde a lista ranqueada completa existe ANTES do truncamento em top_k

**Cosine** — `final` em `store.py:1377`, ANTES do slice `final_top =
final[:top_k]` em `:1381`:
```python
final = sorted(seen.values(), key=lambda x: x["ranking_score"], reverse=True)  # :1377
...
final_top = final[:top_k]                                                      # :1381
```
`final` não é limitado por `top_k` — é toda entry (de qualquer camada) com
`rank_score >= min_score`, ordenada. Ponto candidato do dedup: operar sobre
`final`, produzir `final_top`. **Ranking completo ACESSÍVEL, sem overfetch
necessário.**

**Híbrido** — aqui está a complicação real. `_retrieve_hybrid()` chama:
```python
res = index["hr"].search(query, query_emb, top_k=top_k,
                          min_score=HYBRID_MIN_SCORE, method="rrf", mmr=False)  # :1499-1503
```
`HybridRetriever.search()` (`edp/retrieval_hybrid.py:150-232`) já retorna
**pré-truncado** em `top_k`: a fusão RRF roda sobre um pool amplo (`k =
min(top_k*3, len(self._texts))`, `:170`), mas o `fused` final é fatiado em
`[:top_k]` DENTRO do método (`:199-202`), antes de retornar. Logo,
`final_top` (`store.py:1506-1524`, construído de `res.indices`) **já
nasce do tamanho de `top_k`** — o ranking completo NÃO está acessível no
ponto candidato (~`store.py:1553`, onde o pré-registro aponta) como está
hoje.

**Mitigação verificada (não é obstáculo, é decisão de desenho):** o
`top_k` passado a `hr.search()` é um parâmetro livre, e a fusão RRF/scoring
(`_rrf`, `:236-255`) é determinística por posição de rank dentro dos
pools BM25/vetorial já coletados — aumentar `top_k` só AMPLIA
monotonicamente `k = min(top_k*3, len)` (mais candidatos entram no pool),
nunca reordena os que já estariam no top-k menor (rank/score de um
documento não dependem de quantos serão retornados, só a captação e o
slice final dependem). Ou seja: chamar `hr.search(top_k=top_k + folga,
...)` devolve um superset estritamente consistente do que
`hr.search(top_k=top_k, ...)` devolveria — os primeiros `top_k` itens são
idênticos. **Overfetch condicional (só quando a flag está ON) resolve o
acesso ao ranking completo sem tocar em `retrieval_hybrid.py` além do
argumento `top_k` da chamada.** Isso é mais que "uma linha" no call site
propriamente dito (T3 pede um insert no ponto do T1b) — é uma mudança
adicional no `top_k` passado a `hr.search()`, condicional à flag, que T3
precisa prever explicitamente.

## (c) Posição do piso NOT_FOUND_FLOOR e da exclusão do híbrido, RELATIVA ao ponto candidato

**Piso `NOT_FOUND_FLOOR`** — `store.py:572` (import local) / `:573`
(aplicação), dentro do laço de scoring de `EpisodicMemory.retrieve()`
(`:520-610` aprox.), portanto **ANTES** do merge (a) e do ponto candidato
(b) em ambos os caminhos — o piso já foi aplicado quando `episodic.retrieve()`
retorna. Ordem correta.

**Exclusão do híbrido** — `store.py:1455-1457`, dentro do laço que
constrói `entries_kept`/`layer_of` em `_hybrid_index()` (`:1436-1466`),
portanto **ANTES** de `hr.add()` (`:1472-1475`) e de qualquer `hr.search()`
— também antes do merge/candidato. Ordem correta.

Nenhuma das duas roda depois do ponto candidato em nenhum caminho —
condição de gate #2 (piso/exclusão atrasado) **não se aplica**.

### Achado crítico não listado nos 4 gates, mas do mesmo espírito de (c)

O piso e a exclusão **não cobrem as duas camadas com a mesma força**:

- `EpisodicMemory.retrieve()` aplica `NOT_FOUND_FLOOR` (multiplicador
  0.05, `:573`) — na prática, combinado com `min_score` (`:603`), isso
  quase sempre EXCLUI a entry episódica tóxica de `scored`.
- `SemanticMemory.retrieve()` (`edp/memory/semantic.py:99-150`) **não lê
  `answer_class`** (dívida documentada no docstring do módulo,
  `semantic.py:8-13`) — uma cópia semântica da MESMA entry tóxica (mesmo
  `id`, fenômeno D) é pontuada com peso cheio, sem piso algum.
- No caminho **cosine**, isso é um vazamento real: `results` recebe as
  duas versões (`store.py:1365-1369`); o dedup-por-id já existente
  (achado (d) abaixo) mantém a de MAIOR score — que é a cópia semântica
  sem piso. Um `id` tóxico pode sobreviver a `final`/`final_top` via a
  camada semântica, hoje, independente de qualquer dedup novo.
- No caminho **híbrido**, isso NÃO acontece: a exclusão em `:1455-1457`
  roda sobre `e.get("answer_class")` do dict da entry, e `promote()`
  (`semantic.py:81` — `entry = dict(entry)`) copia o dict inteiro,
  preservando `answer_class` na cópia semântica. A exclusão híbrida é
  portanto uniforme entre camadas — toxic nunca entra no índice, em
  nenhuma camada.

**Consequência para T4:** a invariante de quarentena ("NOT_FOUND/DISQ
nunca aparece via refill") é estruturalmente garantida no caminho híbrido
(exclusão pré-índice, refill só puxa de um índice já limpo). No caminho
cosine, a garantia é PRÉ-EXISTENTE e FRACA (peso 0.05 + min_score, não
exclusão dura) e tem uma brecha já hoje via a cópia semântica — brecha
de scoring, fora de escopo do exp017 (scoring congelado; consertar
`SemanticMemory.retrieve()` é a dívida documentada, ciclo próprio). O
teste do T4 deve isolar o efeito do REFILL (dedup não pode piorar o que
já vaza) e não conflar com esse gap pré-existente — recomendo casos
sintéticos separados: (i) toxic só-episódica (sem par semântico) para
testar a invariante limpa; (ii) toxic com par semântico, registrado como
caracterização do gap pré-existente, não como falha do dedup.

## (d) Dedup por ID já existente em algum ponto do merge?

**Cosine: SIM.** `store.py:1371-1375`:
```python
seen: dict[str, dict] = {}
for r in results:
    eid = r["id"]
    if eid not in seen or r["ranking_score"] > seen[eid]["ranking_score"]:
        seen[eid] = r
final = sorted(seen.values(), ...)
```
Colapsa por `id` mantendo o maior `ranking_score`, exatamente no ponto do
merge (a), antes do candidato (b). Isso já resolve o fenômeno D **no
caminho cosine, hoje**, sujeito à ressalva de (c) (a versão mantida pode
ser a cópia semântica sem piso).

**Híbrido: NÃO.** `_hybrid_index()` (`:1436-1466`) não deduplica por id —
as duas cópias entram como documentos distintos no `HybridRetriever`, e
`search()` pode retornar as duas em `res.indices`. **Este é o caminho
default (`EDP_HYBRID_RETRIEVAL` default `"1"`, `config.py:53`) e é o que
a Fase 0 mediu** (`EXP017_FASE0.md`: dup_id baseline = 12,4%, "100% da
duplicação observada no resultado é fenômeno D").

## Veredito do gate — PROSSEGUIR

Nenhum dos quatro bloqueios se confirma de forma absoluta:

1. **Ranking completo inacessível no ponto certo** — falso para cosine
   (acesso direto via `final`); para híbrido, acessível via overfetch
   condicional em `top_k` de `hr.search()` (verificado matematicamente:
   monotônico, não reordena o prefixo) — decisão de desenho, não
   bloqueio.
2. **Piso/exclusão depois do candidato** — falso nos dois caminhos; os
   dois mecanismos rodam durante o scoring, antes do merge.
3. **Dedup por ID já existente** — verdadeiro só no cosine (não-default);
   falso no híbrido (default, o caminho medido na Fase 0). Não invalida a
   necessidade da intervenção — muda o diagnóstico por caminho, não o
   diagnóstico geral. A passada por ID do T2 é um no-op idempotente e
   inofensivo quando aplicada sobre `final` (cosine); é o mecanismo
   central quando aplicada sobre o índice híbrido overfetched.
4. **Obstáculo ao flag-off byte-idêntico** — falso: `mode="off"` como
   `candidates[:k]` reproduz exatamente `final[:top_k]` (cosine) e
   `final_top[:top_k]` (híbrido, DESDE QUE o overfetch em `hr.search()`
   só seja aplicado quando a flag estiver ON — T3 precisa amarrar isso).

**PROSSEGUIR para T2/T3**, com dois achados registrados para orientar a
implementação:
- Overfetch condicional no `top_k` de `hr.search()` é parte necessária do
  call site híbrido (além da chamada a `_dedup_ranked`).
- T4 deve separar o teste de invariante de quarentena em caso limpo
  (sem par semântico) e caso com gap pré-existente (com par semântico,
  caminho cosine) — não são a mesma alegação.

## Resumo de referências (file:line)

| Item | Cosine | Híbrido |
|---|---|---|
| Merge episódica+semântica | `store.py:1365-1369` | `store.py:1436-1466` |
| Ranking completo pré-truncamento | `store.py:1377` (`final`) | dentro de `HybridRetriever.search()` — não exposto hoje; requer overfetch em `top_k` |
| Ponto candidato do dedup (pré-`top_k`) | entre `:1377` e `:1381` | `store.py:1506-1524`/`~1553`, condicionado a overfetch |
| Piso NOT_FOUND_FLOOR | `store.py:572-573` (só episódica) | — (não é piso, é exclusão) |
| Exclusão híbrida (toxic) | n/a | `store.py:1455-1457` (ambas as camadas, via campo copiado) |
| Dedup por ID pré-existente | `store.py:1371-1375` (SIM) | ausente (NÃO) |
| Governança dura (contradicted/quarantined) | `store.py:540-542` (epi) + `semantic.py:126-129` (sem) | `store.py:1447-1448` |

---

## ERRATA — descoberta durante T3 (implementação), texto original acima intocado

**ERR-T1-1. O achado (b) para o caminho cosine estava incompleto.** O texto
original afirma que `final` (`store.py:1377`, pré-`:1381`) "não é limitado
por `top_k`". Isso é verdade APENAS depois do merge — mas cada CAMADA já
trunca em `top_k` **internamente**, antes de `results.extend(...)` sequer
rodar:

- `EpisodicMemory.retrieve(query_emb, top_k, min_score)` faz
  `for rank_score, i, breakdown in scored[:top_k]:` (`store.py:728` — o
  slice `scored[:top_k]` corta ANTES de retornar).
- `SemanticMemory.retrieve(query_emb, top_k, min_score)` faz o mesmo
  (`semantic.py:150`, `scored[:top_k]`).
- `self.working.retrieve(top_k=top_k)` idem (`recency_rank(..., top_k)`,
  `temporal.py:78`).

Ou seja: `results` (antes do merge/seen-dict) já chega limitado a, no
máximo, `top_k` itens POR CAMADA (até 3×`top_k` no total) — não "toda
entry com `rank_score >= min_score`" como o texto original de (b) afirmou.
Passar `top_k` inalterado para as três chamadas de camada e só tentar
overfetch no ponto do merge (como T3 originalmente implementou) deixa o
refill "cego" a qualquer candidato que a própria camada já descartou —
reproduzido empiricamente por um teste de integração do T4 que devolvia
4 itens em vez de 5 com apenas 1 duplicata de hash entre 6 candidatos.

**Correção aplicada no T3:** a resolução do modo (`_resolve_retrieve_
instrumentation_exp017`) precisa rodar **antes** das três chamadas de
camada, não só antes do merge. Quando o modo é `dedup`/`random_pareado`,
cada chamada pede `top_k=len(camada)` (equivalente a "toda a camada") em
vez do `top_k` do chamador — mesma técnica de overfetch condicional já
usada no lado híbrido (achado original do item b), aplicada agora também
por camada no cosine. `mode="off"` continua pedindo exatamente `top_k` por
camada, preservando o byte-idêntico.

**Consequência para o veredito do gate:** não muda — ainda nenhum dos 4
gates se confirma de forma absoluta; a correção é de DESENHO (onde o
overfetch precisa entrar), não uma revogação da premissa de que o ranking
completo é alcançável. Mas reforça a lição do achado híbrido original: em
QUALQUER caminho, "ranking completo" tem que ser verificado em CADA ponto
de truncamento na cadeia (camada → merge → topo), não só no último.
