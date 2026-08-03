# RELATORIO_F1_EXP017.md — Fase 1 fechada (T1-T6)

Branch `exp017/fase1-dedup`, a partir de `main@67f2f5b` (pós-PR #16).
Contrato: `PRE_REGISTRO_EXP017.md` (com ERRATA ERR-1/2/3 + E6) +
`EXP017_FASE0.md` (Fase 0 fechada). Gate: pytest verde após cada commit
(mantido em todos os 5 commits desta fase). Sem push. Scoring CONGELADO —
nenhum ajuste de score, peso ou fórmula.

Este relatório fecha T1-T6. **Medições reais (T5/T6) e veredito de H1/H2
são do pesquisador — não decididos aqui.** Ver seção 6.

## 1. T1 — achados (leitura, `RELATORIO_F1T1_EXP017.md`)

Relatório completo (com ERRATA de T3) em `RELATORIO_F1T1_EXP017.md`.
Resumo dos achados que orientaram T2-T4:

| Achado | Cosine | Híbrido |
|---|---|---|
| Merge episódica+semântica | `store.py:1365-1369`(*) | `store.py:1436-1466`(*) |
| Ponto candidato do dedup | `final`, entre o sort e o slice em `top_k` | `res.indices` de `HybridRetriever.search()` |
| Piso NOT_FOUND_FLOOR | `store.py:572-573`, só episódica | — |
| Exclusão híbrida (toxic) | n/a | `store.py:1455-1457`, ambas as camadas |
| Dedup por ID pré-existente | SIM (`seen` dict no merge) | NÃO |
| **Truncamento por CAMADA (achado corrigido durante T3)** | `EpisodicMemory.retrieve()`/`SemanticMemory.retrieve()`/`WorkingMemory.retrieve()` truncam em `top_k` **internamente**, antes do merge — overfetch tinha que entrar em CADA chamada de camada, não só no merge | `HybridRetriever.search()` já devolve pré-truncado em `top_k` — overfetch entra no `top_k` passado à busca |

(*) Números de linha na leitura original do T1; a implementação do T3
inseriu código antes desses pontos, deslocando os números atuais no
arquivo — ver `RELATORIO_F1T1_EXP017.md` para as referências exatas
pós-implementação.

**Achados de segurança carregados para T4:**
- Caminho híbrido: exclusão de `answer_class` tóxico acontece ANTES da
  indexação — refill estruturalmente seguro (nunca há o que reintroduzir).
- Caminho cosine: `SemanticMemory.retrieve()` não lê `answer_class` (dívida
  documentada, `semantic.py:8-13`) — uma cópia semântica de uma entry
  tóxica escapa do piso HOJE, independente de dedup. Fora de escopo do
  exp017 (scoring congelado); T4 testa que o dedup não agrava esse gap
  pré-existente (não afirma a invariante nesse caso específico).
- Nenhum dos 4 gates de parada do T1 se confirmou de forma absoluta —
  Fase 1 prosseguiu.

## 2. T2 — função pura

```python
def _dedup_ranked(candidates: list[dict], k: int, mode: str, rng=None) -> list[dict]
```
`edp/memory/store.py:1122` (mais `_normalize_text_exp017`:1083 e
`_dedup_pass_exp017`:1090, auxiliares privadas do mesmo módulo — respeita
o choke-point `store.py:9-21`, nada sai do módulo).

- `mode="off"` → `candidates[:k]`, byte-idêntico.
- `mode="dedup"` → 1ª passada por ID, 2ª por hash normalizado
  (strip+casefold+colapso de whitespace — mesma normalização do censo),
  lazy até `k`, representante = primeira ocorrência (maior score).
- `mode="random_pareado"` → controle-reserva: `d` = quantas duplicatas o
  modo "dedup" removeria até `k`; remove `d` aleatórios do top-k bruto
  (`rng` obrigatório), refill igual. Mesma degradação honesta do "dedup"
  quando o pool não tem conteúdo único suficiente para completar `k`
  (documentado no docstring — não é bug).

## 3. T3 — integração

**Flags** (`edp/config.py:121-160`, padrão do SHUFFLE):
- `EDP_RETRIEVE_DEDUP` (default OFF)
- `EDP_RETRIEVE_RANDOM_DROP` (default OFF, seed `EDP_SHUFFLE_SEED` por
  query — mesma disciplina do SHUFFLE)
- `resolve_retrieve_instrumentation_exp017(dedup, shuffle, random_drop)` —
  guard de exclusividade mútua entre as três flags: loga erro e prioriza
  OFF se mais de uma estiver ligada.

**Call sites** (import da flag DENTRO da função, padrão do SHUFFLE):
- Cosine, `MemoryStore.retrieve()` (`store.py:1437`): resolve o modo ANTES
  das três chamadas de camada (working/episodic/semantic) — overfetch
  condicional pede a camada inteira (`len(self.episodic)` etc.) quando
  dedup/random_pareado; dedup aplicado sobre `final` (pós piso/exclusão,
  pré-truncamento), antes do `record_turn`.
- Híbrido, `MemoryStore._retrieve_hybrid()` (`store.py:1620`): overfetch
  condicional no `top_k` passado a `HybridRetriever.search()` (pede
  `len(index["entries"])` — o corpus inteiro do índice, já livre de toxic
  por construção); mutação de `acessos`/`ultimo_acesso` ADIADA para depois
  do dedup — só toca as entries que sobrevivem ao refill, para não inflar
  acesso de candidatos descartados (disciplina read-only do experimento).
- `edp/llm_adapter.py` (SHUFFLE, call site pré-existente): passou a usar o
  mesmo guard compartilhado — as três flags agora são mutuamente
  exclusivas de fato dos dois lados (dedup E shuffle detectam a colisão).

`mode="off"` preserva byte-idêntico nos dois caminhos e nos três pontos de
overfetch (top_k pedido inalterado quando a flag está off).

## 4. T4 — testes de integração

Contagem de testes desta fase (Fase 0 fechou com 85 passed; suite atual:
**112 passed, 1 deselected** — **+27 testes**):

| Arquivo | Testes | Cobertura |
|---|---|---|
| `tests/test_exp017_dedup_ranked.py` | 16 | função pura `_dedup_ranked` (T2) |
| `tests/test_exp017_dedup_integration.py` | 8 | ON com refill, invariante de quarentena, gap pré-existente, random_pareado, guard |
| `tests/test_flag_off_byte_identical.py` (adições) | 3 | OFF preserva duplicata por hash (cosine) / por ID (híbrido); flags são default |

Cobre todos os itens pedidos: espécimes 10×mesmo-hash, pares mesmo-ID,
mix ID+hash, k>candidatos, lista vazia, random_pareado com seed fixa
reprodutível, off==slice (T2); flag-off byte-idêntico nos dois caminhos,
ON com dup_id=0 E dup_hash=0 e refill, invariante de quarentena (híbrido
estrutural + cosine caso limpo), random_pareado |resultado|=k reprodutível
(T4).

## 5. Nota do monitor — rodapé obrigatório de uma promoção futura

`get_monitor().record_turn(...)` (cosine `store.py` e híbrido `store.py`,
ambos logo após o ponto do dedup) grava `top_scores`/`result_ids` do que é
efetivamente `final_top` — ou seja, **quando `EDP_RETRIEVE_DEDUP` ou
EDP_RETRIEVE_RANDOM_DROP estiverem LIGADAS, o `record_turn` passa a gravar
a série JÁ deduplicada/perturbada, não o `final_top` bruto de hoje.**
Isso é intencional para o propósito do exp017 (o monitor deve refletir o
que é entregue) mas tem uma consequência que qualquer promoção futura de
`EDP_RETRIEVE_DEDUP` a default ON precisa herdar: **a série histórica do
`retrieval_monitor` (o "80% repetitivo" citado na motivação do
pré-registro) muda de definição no dia em que a flag ligar** — comparações
antes/depois desse ponto deixam de ser like-for-like sem essa ressalva
explícita. Nenhuma ação tomada agora (flag default OFF); registrado para
não se perder na Fase 2/promoção.

## 6. Comandos EXATOS da rodada Windows — PARAR aqui, medição é do pesquisador

Servidor parado, PowerShell, apontando para a cópia do store (nunca
produção):

```powershell
# 0. Suite completa (gate — precisa estar verde antes de qualquer medição)
python -m pytest

# 1. T5 — dup_rate@10 nas queries de calibração, OFF e ON
$env:EDP_BASE_DIR = "C:\edp_data_fase0"
python scripts/calibracao_h1_exp017.py

# 2. T6 — repeat_rate em três condições {OFF, SHUFFLE, RESERVA}, nas DUAS
#    ordens (intercalada e agrupada, E6)
python scripts/medir_repeat_exp017.py
python scripts/medir_repeat_exp017.py --ordem agrupada

# 3. Suite de regressão Fase 1 (R1/R2/R3), com EDP_RETRIEVE_DEDUP=1 —
#    a perna que calibracao_h1_exp017.py NÃO cobre do critério PASSA H1
$env:EDP_HYBRID_RETRIEVAL = "1"; $env:EDP_CTX_SLOTS = "1"
$env:EDP_RETRIEVE_DEDUP = "1"
python suite_regressao_fase1.py
```

Critérios de leitura (não decididos aqui — ver
`PRE_REGISTRO_EXP017.md`, seção "Critérios PASSA/FALHA"):
- H1 PASSA sse: dup_rate@10=0 (id E hash) em C1-C5 com ON (comando 1) **E**
  R1 CP3 presente **E** (R2 Recall@5≥2/3 **OU** R3 %SS≤20%) (comando 3) **E**
  suite pytest verde (comando 0).
- H2: reportar sempre os três brutos de OFF/SHUFFLE/RESERVA (comando 2,
  nas duas ordens) — já pré-classificado INCONCLUSIVO-POR-DESENHO na Fase 0
  (E6); o dado desta rodada alimenta o E7/H2-C, não reabre H2.

**PARAR — rodar os comandos acima, colar a saída e o veredito de H1/H2 é
decisão do pesquisador.**
