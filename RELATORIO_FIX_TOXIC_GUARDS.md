# RELATORIO_FIX_TOXIC_GUARDS.md

Branch: `fix/toxic-guards`, a partir de `788d7f5` (`exp017/fase1-dedup`).
Evidência de origem: lab_edp `docs/VEREDITO_EXP018.md` e
`docs/ACHADO_FLAG_UNICA_TOXICIDADE.md` (ambos lidos antes desta mudança).

Escopo fechado: as quatro mudanças abaixo (T1–T4), mais testes (T5) e este
relatório (T6). Nenhuma mudança de comportamento com os defaults — com
`EDP_TOXIC_GUARDS=1` e `EDP_WRITE_PROVENANCE=1` (ambas ON, o caso hoje), o
sistema se comporta byte-idêntico ao pré-fix.

## O que mudou, por file:line

### T1 — nova constante (`edp/config.py:87-96`)

`EDP_TOXIC_GUARDS = os.environ.get("EDP_TOXIC_GUARDS", "1") == "1"`, logo
após `EDP_WRITE_PROVENANCE` (`config.py:87`). Default ON. Governa a LEITURA
das três defesas de toxicidade; `EDP_WRITE_PROVENANCE` passa a governar
apenas a ESCRITA do carimbo `answer_class`.

### T2 — troca de flag nos três pontos de leitura (só o nome, lógica intacta)

| Ponto | Antes | Depois |
|---|---|---|
| Piso `NOT_FOUND_FLOOR` — `edp/memory/store.py:576` (`EpisodicMemory.retrieve()`) | `EDP_WRITE_PROVENANCE as _WP` | `EDP_TOXIC_GUARDS as _WP` |
| Exclusão do índice híbrido — `edp/memory/store.py:1601` (`MemoryStore._hybrid_index()`) | `EDP_WRITE_PROVENANCE as _WP12` | `EDP_TOXIC_GUARDS as _WP12` |
| Guarda de `consolidate_promote_only()` — `edp/consolidation.py:312` | `EDP_WRITE_PROVENANCE` | `EDP_TOXIC_GUARDS` |

Nota de citação: o achado do lab citava `store.py:1455-1457` para a
exclusão híbrida; na árvore local (mesmo SHA `788d7f5`) esse trecho fica em
`store.py:1595-1596` (pré-mudança) / `:1601` (pós-T2) — mesma ressalva que o
próprio `VEREDITO_EXP018.md` registra ("citação file:line vale apenas com a
árvore declarada"). Conteúdo conferido por `grep`, não só por número de
linha.

`edp/api/routes/websocket.py:1226` **não foi tocado** — é escrita de
proveniência (carimbo na gravação), fica com `EDP_WRITE_PROVENANCE`.

### T3 — guarda dentro de `consolidate()` (`edp/consolidation.py`)

Os dois branches de promoção ganharam a mesma guarda de
`consolidate_promote_only`:
- pós-merge, `:205` (`merged.get("answer_class")`)
- entry-sozinha, `:214` (`entry.get("answer_class")`)

Import local de `EDP_TOXIC_GUARDS, TOXIC_ANSWER_CLASSES` em `:188`.

### T4 — `merge_cluster()` propaga `answer_class` (`edp/consolidation.py`)

`:126-129` — `answer_class_merged`: se qualquer entry do cluster tem
`answer_class` em `TOXIC_ANSWER_CLASSES`, a fundida herda esse valor (molde
de `melhor_prio`, `:118-119`). Chave nova no dict de retorno, `:165`
(antes tinha dez chaves, sem `answer_class`) — sem isto, a guarda de T3 no
branch pós-merge é cega (`merged.get("answer_class")` sempre `None`,
achado H3 do exp018, condição C7).

## Testes

- **133 passed** (era 123 antes deste fix; +10 no arquivo novo). 1 arquivo
  (`tests/test_health_check.py`) segue fora do gate por um problema de
  ambiente pré-existente e não relacionado (`email-validator` do sistema em
  `1.3.0`, pydantic exige `>=2.0`; confirmado com `git stash` que o erro já
  ocorre em `788d7f5` sem nenhuma mudança deste fix).
- `tests/test_consolidation_guard.py` e `tests/test_flag_off_byte_identical.py`
  tinham testes que fixavam o ACOPLAMENTO ANTIGO como comportamento
  esperado (`monkeypatch EDP_WRITE_PROVENANCE=False` ⇒ esperava guarda
  desligada) — exatamente a vulnerabilidade do achado. Migrados para
  `EDP_TOXIC_GUARDS` nesses pontos; ver docstrings atualizadas nos dois
  arquivos.
- `tests/test_toxic_guards_flag_separation.py` (novo, T5) — um teste por
  mudança: ambas ON (byte-idêntico), `EDP_TOXIC_GUARDS=0` desliga as três
  leituras, `EDP_WRITE_PROVENANCE=0` sozinho NÃO desliga nenhuma (achado
  pinado), `consolidate()` não promove tóxico em nenhum branch,
  `merge_cluster()` propaga `answer_class` tóxico.

## Critério de aceitação externo — exp018 é o oráculo

Nenhum teste novo substitui a validação real; ela é do pesquisador, rodando
o exp018 do lab_edp exatamente como está contra este branch:

| Cond. | Função | flag testada | Antes do fix | Esperado após o fix |
|---|---|---|---|---|
| C1 | `consolidate()` | `EDP_WRITE_PROVENANCE=1` | 4/4 promovidas | **0/4** (T3+T4) |
| C2 | `consolidate()` | `EDP_WRITE_PROVENANCE=0` | 4/4 promovidas | **0/4** (T3+T4 — independem da flag de escrita) |
| C3 | `consolidate_promote_only()` | `EDP_WRITE_PROVENANCE=1` | 0/4 | **continua 0** (agora via `EDP_TOXIC_GUARDS=1` default) |
| C4 | `consolidate_promote_only()` | `EDP_WRITE_PROVENANCE=0` | 4/4 promovidas | **0/4** (T1+T2 — guarda não depende mais da flag de escrita) |
| C5 | (+, normais, acessos=3) | — | 2 promovidas | **continua promovendo 2** (instrumento não deve regredir) |

PARANDO AQUI — a validação via exp018 do lab_edp é do pesquisador.
