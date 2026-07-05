# RELATÓRIO — FASE 0: memória real vs. negações (confirmação de mecanismos)

**Tipo:** medição pura. ZERO conserto — nenhum diff em `edp/`, nenhuma branch de
fix. Esta branch (`fase0/memoria-vs-negacoes`) contém apenas o harness de medição
e este relatório.

---

## §0. PREVISÕES — registradas ANTES de qualquer teste rodar (sem ajuste pós-hoc)

Registradas em 2026-07-05, antes de qualquer execução dos Testes 1/2:

- **P1:** no store LIMPO, `4c57ed7a` chega aos 3 checkpoints (a contaminação era
  o fator dominante). → **veredito: PENDENTE (aguarda rodada no store real)**
- **P2:** no store CONTAMINADO, o rank BM25 isolado da negação > rank da
  `4c57ed7a` para a query canônica (eco de query: a negação contém a pergunta
  verbatim). → **veredito: PENDENTE**
- **P3:** no store contaminado, `4c57ed7a` passa o CP1 (lineage já provou) e
  morre no CP2 (seen_ids ou top-k dominado pelas negações). → **veredito: PENDENTE**

---

## §1. OBSTÁCULO DE AMBIENTE (regra da tarefa: reportar e parar, não improvisar)

Os dois stores sob teste vivem na máquina do pesquisador (Windows) e **não são
alcançáveis deste ambiente**:
- `C:\edp_data` (produção, fonte da cópia limpa do Teste 1) — inexistente aqui;
- `C:\edp_data_hybrid_test` (contaminado, espécime do Teste 2) — inexistente aqui.

Verificação: `ls C:\edp_data /c/edp_data /mnt/c/edp_data` → nada; `find /` por
`edp_data` → nada. O único dado local é a fixture `/content/edp_v3_memory`
(11 entries, **sem** memórias Redis e **sem** negações — inútil para os vereditos).

**Aplicação da regra anti-recontaminação por analogia:** assim como "método que
exige estado do servidor → reportar o obstáculo e parar", dados que só existem na
máquina do pesquisador → **nenhum número de veredito é fabricado aqui**. Em vez
disso, esta fase entrega o instrumento pronto e validado; a rodada final é sua,
em minutos, com os comandos abaixo.

---

## §2. O INSTRUMENTO — `fase0_checkpoints.py` (chamada direta, zero chat)

Cumpre TODAS as regras da fase:
- **Sem turno vivo:** chama diretamente `mem.retrieve` (CP1 — o mesmo objeto e
  método do websocket:716), `rt._retrieve_context(QUERY)` (CP2 — inclui o
  retrieve interno da :2334 e o skip de seen_ids :2340-2342) e
  `rt._build_enriched_context(QUERY, system)` (CP3 — prompt final renderizado).
  O runtime vem de `registry.get_runtime` **sem conectar LLM** (nenhuma resposta
  é gerada, logo **nenhuma negação nova é gravada**).
- **Anti-mutação:** snapshot pristine + restore antes de CADA checkpoint (o
  retrieve muta `acessos++` e o `_retrieve_context` grava co-ocorrência); no fim,
  restore + hash de no-divergência — o store sai como entrou.
- **IDs, não tamanhos:** cada CP imprime os ids (8 chars) presentes; no CP2, o
  harness captura **em processo** (spy no `MemoryStore.retrieve`, sem tocar
  `edp/`) o que o retrieve interno da :2334 devolveu, e reconstrói a janela
  imediata (últimos 6 turnos não-summary) — se o alvo entrou no retrieve interno,
  está na janela e sumiu dos blocks, o veredito impresso é **"seen_ids skip"**;
  se entrou e não está na janela, **"descartado pós-retrieve (outra causa)"**.
- **Teste 2 isolado:** indexa o store contaminado com o `BM25` REAL do
  `retrieval_hybrid` e imprime o ranking lexical PURO (com marcação
  `NEGACAO`/`CONTEUDO-REAL` por id/texto), depois o ranking RRF completo com a
  decomposição bm25/vec por item.
- **(b) do Teste 1 embutido:** antes dos checkpoints, conta e lista as negações
  ("não encontro"+"redis") no store usado — se a PRÓPRIA cópia da produção tiver
  negações históricas, o harness avisa em destaque (muda a leitura de P1).

### Sua rodada (servidor EDP PARADO)

```powershell
# TESTE 1 — cópia NOVA e LIMPA da produção:
robocopy C:\edp_data C:\edp_data_fase0 /E
$env:EDP_BASE_DIR = "C:\edp_data_fase0"
$env:EDP_HYBRID_RETRIEVAL = "1"
python fase0_checkpoints.py --teste1

# TESTE 2 — o store contaminado, como está:
$env:EDP_BASE_DIR = "C:\edp_data_hybrid_test"
$env:EDP_HYBRID_RETRIEVAL = "1"
python fase0_checkpoints.py --teste2
```

A saída já vem no formato das tabelas de §3/§4 (ids por CP, ranking BM25 com
scores, posições das negações vs. alvo, veredito por checkpoint).

---

## §3. TESTE 1 — tabela CP1/CP2/CP3 *(preencher com a saída da rodada real)*

| checkpoint | ids presentes | 4c57ed7a? | evidência |
|---|---|---|---|
| CP1 `mem.retrieve` | *(rodada)* | ? | scores RRF impressos |
| CP2 `_retrieve_context` | *(rodada)* | ? | retrieve interno vs blocks vs janela-6 |
| CP3 prompt final | *(rodada)* | ? | `"chave-valor"` no rendered? rendered_len |

Contagem de negações na cópia da produção: *(rodada — item (b))*.

## §4. TESTE 2 — rankings *(preencher com a saída da rodada real)*

BM25 puro: posições das 3 negações vs. `4c57ed7a` vs. demais conteúdos reais.
RRF completo: negações no top-5? posições vs. memórias reais.

## §5. Tabela final de mecanismos *(após a rodada)*

| mecanismo | veredito | evidência |
|---|---|---|
| contaminação write-path (negações gravadas) | *(pend.)* | contagem §3(b) + T2 |
| eco de query (BM25 premia a negação que contém a pergunta) | *(pend.)* | ranking BM25 §4 |
| seen_ids skip (:2340-2342) | *(pend.)* | CP2 (spy + janela-6) |
| top-k/recência (negações dominam o top-5) | *(pend.)* | CP1 + RRF §4 |

## §6. Recomendação preliminar de alvo para a Fase 1 (condicional aos vereditos)

**Sem implementar nada.** Leitura condicional, declarada antes dos números:
- Se **P2 confirmar** (eco de query no BM25): o alvo primário é o **write-path**
  — negações do próprio EDP não deviam virar memória episódica recuperável
  (filtro de gravação por padrão de recusa, análogo ao filtro_recusa que já
  existe no READ-path, memory.py:849 — a assimetria é gravar o que já se recusa
  a ler). O eco de query é estrutural do BM25: enquanto a negação existir no
  índice, ela contém a pergunta verbatim e vence lexicalmente; limpar na leitura
  seria enxugar gelo.
- Se **P1 confirmar** e **P3 apontar seen_ids**: alvo secundário no
  `_retrieve_context` (o skip da janela imediata não deveria descartar a única
  cópia de conteúdo do slot de retrieval quando a versão da janela está truncada).
- Se **P1 REFUTAR** (alvo morre até no store limpo): o problema é estrutural da
  cadeia CP2/CP3 e a Fase 1 muda de endereço — reavaliar com os ids impressos.

## §7. Confirmações de integridade

- Produção `C:\edp_data` **intocada** — este ambiente nem a alcança; a rodada
  real usa cópias via robocopy, e o harness recusa `EDP_BASE_DIR` que aponte
  para `edp_data` (guard `ALLOW_PROD`).
- **Nenhuma negação nova gravada:** nenhum turno de chat em nenhum momento; o
  harness não chama LLM.
- **Nenhum arquivo de `edp/` modificado:** `git status` limpo em `edp/` nesta
  branch (só `fase0_checkpoints.py` e este relatório na raiz).
- Encanamento do harness validado na fixture local (plumbing só — a fixture não
  contém o espécime): CP1/CP2/CP3 executam por chamada direta, spy captura o
  retrieve interno, restore/no-divergência OK, e o caminho de OBSTÁCULO
  ("alvo não existe neste store") dispara corretamente.
