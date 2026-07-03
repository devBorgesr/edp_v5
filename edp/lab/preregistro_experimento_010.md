# Pré-registro — Experimento 010
## Retrieval híbrido (BM25 + vetorial + RRF, MMR opcional) melhora a recuperação vs cosine puro?

**Bancada de Contexto — EDP.** Categoria **RETRIEVAL-QUALITY** (exp008/exp009):
mede o que o retrieve **SELECIONA**, antes do modelo. **LLM nunca é chamado.
Custo de API zero.**

> **Régua:** critério declarado ANTES do dado; congela após o 1º disparo real;
> NÃO se descongela para ajustar parâmetros depois de ver resultado (bug que
> invalide ⇒ exp010b novo). Anti-mock: `HybridRetriever` REAL
> (`edp/retrieval_hybrid.py:105`) e retrieve REAL (`edp/memory.py:1612`) sobre
> memórias REAIS — nenhum dos dois reimplementado. **Este experimento decide se
> vale ligar o híbrido ao hot path; NÃO liga nada, só mede.**

Data de pré-registro: **2026-07-02**. Congelar ao primeiro disparo real.

---

## §1. Motivação (verificada, não assumida)

- O exp009 + migração despriorizaram os `session_summary`, mas **resíduo
  permanece**: no store migrado, a memória de conteúdo do Redis ainda tem SS
  competindo no top-5 (o `"| Redis | Memcached |"` fica em rank 1, empatado com
  a resposta real).
- **Causa provável do resíduo:** o cosine de 384-dim dilui termos técnicos
  literais ("Redis", "Memcached"). BM25 (léxico) captura o termo literal — é
  exatamente o que o cosine não faz. Por isso o híbrido é candidato.
- O `retrieval_monitor` (`edp/runtime/retrieval_monitor.py:113-118`) mede
  repetição por overlap de ids entre turnos consecutivos (repeat se
  `|overlap| >= min(2, len(top))`; warning com taxa > 60%,
  `:146`) e **já disparou em medição real** ("80% dos turnos retornam memórias
  iguais"). O MMR do híbrido ataca isso forçando diversidade — o exp010 mede a
  repetição, não só o Recall.
- O `HybridRetriever` **existe e está desligado** do hot path (websocket usa
  `memory.retrieve`, cosine puro — `websocket.py:716`).

### §1a. Fatos mecânicos do sujeito (lidos na fonte, acomodados no desenho)

1. **Escala do RRF:** scores RRF valem ~`Σ peso/(60+rank+1)` ⇒ máximo ≈ **0.016**.
   O default `min_score=RETRIEVAL_MIN_SIM=0.20` (`config.py:35`) **zeraria o RRF**.
   Por isso TODAS as condições usam `min_score=0.0` (congelado). O dry-run
   **verifica** que o RRF retorna resultados não-vazios.
2. **Bug registrado (NÃO corrigido aqui):** em `retrieval_hybrid.py:213-215`, no
   bloco MMR, a 1ª atribuição a `sc_list` indexa `fused` (lista de tuplas) como
   se fosse dict — é **código morto**, imediatamente sobrescrito pela 2ª
   atribuição (`:216-219`), que está correta. Não altera o resultado. Alterar
   `retrieval_hybrid.py` mudaria o sujeito medido; fica para limpeza futura SE o
   híbrido for promovido.
3. **Comparação declarada e honesta:** o baseline (`memory.retrieve`) inclui a
   pilha de governança de produção (decay, prioridade, epistemic, session_boost,
   dominance penalty); o híbrido, como existe, é **cru** (BM25+cosine+fusão).
   Comparamos os dois sujeitos REAIS como são — a pergunta é "o módulo que
   existe, ligado no lugar do atual, melhoraria?", não "BM25 vs cosine em
   condições idealizadas".

---

## §2. Hipóteses

- **H1:** o híbrido (BM25+vetorial+RRF) recupera a memória de conteúdo
  específica no top-K **mais vezes** que o cosine puro, **E/OU** reduz a taxa de
  repetição (quando MMR ligado), **SEM** derrubar o caso legítimo (query que pede
  resumo).
- **H0:** o híbrido não melhora o Recall nem reduz a repetição de forma
  relevante.

---

## §3. Condições (mesmas queries, mesmo store, MESMOS embeddings)

| rótulo | o que roda |
|---|---|
| `baseline_cosine` | retrieve REAL atual: `MemoryStore.retrieve(q, top_k=10, min_score=0.0)` — o hot path |
| `hibrido_rrf` | `HybridRetriever(alpha=0.5, rrf_k=60).search(q, q_emb, top_k=10, min_score=0.0, method="rrf", mmr=False)` |
| `hibrido_rrf_mmr` | idem + `mmr=True, mmr_lambda=0.5` (diversidade — ataca a repetição) |
| `hibrido_weighted` **(§EXPLORATÓRIA)** | `method="weighted", alpha=0.5` — RRF vs soma ponderada; gera hipótese, não confirma |

**Justiça de embeddings (§ ponto delicado):** o índice do híbrido é alimentado
com os **textos + embeddings REAIS** já salvos nas entries (episodic + semantic
do scope, os mesmos que o baseline enxerga); o embedding da query é gerado com
`edp/embeddings.py` (`all-MiniLM-L6-v2`, 384-dim) — o MESMO modelo do retrieve.
Mesmas memórias, mesmos vetores; só muda o algoritmo de ranking.

---

## §4. Dataset (CONGELADO — reusa o ground truth validado do exp009)

**Ordem do plano congelada** (necessária para a métrica de repetição): vagas →
Redis → específicas → guarda.

- **VAGAS (n=6):** as mesmas 6 do exp009/measure (`"vamos continuar nossa
  conversa"`, `"continuando o que falávamos"`, `"o que a gente tinha concluído
  mesmo?"`, `"me lembra o que discutimos"`, `"voltando ao que estávamos vendo"`,
  `"sobre o que conversamos até agora"`).
- **REDIS (n=3):** mesmas 3 queries do exp009; alvo = qualquer um dos ids REAIS
  que o exp009 validou: `0c78fa08-8a51-4a04-ad15-2b23d0800a0b`,
  `4c57ed7a-c275-4155-93eb-e1efa5a164d5`.
- **ESPECÍFICAS (n=5):** as needles do exp009 **que resolveram** no store real:
  `transformer`, `embedding`, `rag`, `python`, `episódic` (mesmo mecanismo
  needle→ids não-SS; dry-run lista as não resolvidas; pesquisador troca por id
  explícito antes de armar).
- **FAISS — OMITIDA (documentado):** o exp009 provou que **não há memória de
  conteúdo sobre FAISS** no store. Sem ground truth, uma query FAISS não mede
  Recall (mediria "o BM25 acha o quê quando não há alvo?" — outra pergunta,
  outro experimento). Fica fora do dataset.
- **GUARDA (n=3):** as mesmas 3 queries de resumo do exp009 — SS no top-5 aqui é
  SUCESSO.

Total: **17 queries × 4 condições**.

---

## §5. Métricas (por condição; Wilson via `edp.lab.scorer._wilson`)

1. **Recall@3 / Recall@5 / MRR** da memória-alvo (queries com alvo resolvido;
   Wilson sobre queries).
2. **%SS no top-5** (proporção de slots, Wilson), separado vagas/específicas —
   o híbrido deve reduzir vs o resíduo pós-migração (~40% nas vagas).
3. **REPETIÇÃO** (espelha o monitor, `retrieval_monitor.py:113-118`), calculada
   **dentro de cada condição** sobre os **16 pares consecutivos** da ordem
   congelada do plano:
   - `repeat_rate` (primária): fração de pares consecutivos com
     `|top5_i ∩ top5_{i+1}| >= min(2, |top5_{i+1}|)` — o critério literal do
     monitor;
   - `overlap_frac` (apoio): média de `|top5_i ∩ top5_{i+1}| / 5`.
4. **GUARDA:** nº de queries de resumo com ≥1 SS no top-5.
5. **EXEMPLOS** completos do top-5 por (condição × query) no prontuário
   (id, rank, score, bm25/vec score no híbrido, is_ss, is_target, preview) —
   **o número não é o achado**; para as queries-chave (Redis, transformers) a
   auditoria mostra o top-k integral.

---

## §6. Critério de decisão (limiares CONGELADOS)

Avaliado pós-coleta (`--score`), só registros REAIS do experimento `010`.
A comparação confirmatória é **ponto-a-ponto** (limiar declarado); os ICs de
Wilson são reportados para leitura honesta do n pequeno (mesma postura do exp009).

- **GUARDA (pré-condição para qualquer confirmação):** a condição vencedora
  precisa manter **≥1** query de resumo com **≥1 SS no top-5**.
- **H1-CONTEÚDO confirmada** se, em `hibrido_rrf` OU `hibrido_rrf_mmr` (com
  guarda ok): **Recall@5 > baseline** E **MRR > baseline** (ambos estritos).
- **H1-REPETIÇÃO confirmada** se, em `hibrido_rrf_mmr` (com guarda ok):
  **repeat_rate ≤ repeat_rate(baseline) − 15 pontos percentuais**.
- **H1 global** = H1-CONTEÚDO **OU** H1-REPETIÇÃO. **H0** se nenhuma.
- **Red flag (reportada, não decide):** se %SS top-5 nas vagas do híbrido
  exceder o baseline em >10pp, registrar — BM25 pode re-inflar summaries por
  match léxico de palavras de resumo.

**Decisão derivada (fora do experimento):** H1-CONTEÚDO+H1-REPETIÇÃO fortes ⇒
candidatar o híbrido ao hot path (com limpeza do bug §1a.2 antes); H0 ⇒ o
resíduo pede outro fix (ex.: canal de injeção).

---

## §7. Isolamento e mecânica (padrão exp009, citado)

- `EDP_BASE_DIR` numa **CÓPIA** (guarda recusa basename `edp_data` sem
  `ALLOW_PROD=1`); snapshot pristine + **restore antes de CADA query** (o
  retrieve real MUTA: `acessos++`/`save()`, `memory.py:871-880`); verificação
  final de **no-divergência**. O índice do híbrido é construído **do snapshot**
  (read-only; o híbrido não toca disco).
- Embedding singleton carrega **uma vez**; `query_emb` é calculado **uma vez por
  query** e reutilizado nas 3 condições híbridas (justiça).
- Trava **`EDP_LAB_ARMED=1`** para o disparo real. `--dry-run` (prova-no-espelho):
  resolução dos alvos, `HybridRetriever.stats()` do índice REAL, top-k das
  queries-chave nas 4 condições, verificação de RRF não-vazio com
  `min_score=0.0`, no-leak. `--score` (Wilson + veredito §6). `--audit`
  (top-k reais).
- `record_run` no prontuário por (condição × query), `dry_run` marcado no
  andaime.

## §8. Constantes congeladas (espelhadas em `exp010.py`)

| constante | valor |
|---|---|
| `EXPERIMENTO` | `"010"` |
| `TOP_K` / `MIN_SCORE` | `10` / `0.0` (ver §1a.1) |
| condições | `baseline_cosine`, `hibrido_rrf`, `hibrido_rrf_mmr` (+ `hibrido_weighted` exploratória) |
| híbrido | `alpha=0.5`, `rrf_k=60`, `mmr_lambda=0.5` |
| repetição | pares consecutivos da ordem congelada; repeat = `|∩| >= min(2, |top5|)`; limiar H1-REPETIÇÃO = −15pp |
| H1-CONTEÚDO | Recall@5 e MRR estritamente > baseline |
| guarda | ≥1 query de resumo com ≥1 SS no top-5 |
| dataset | §4 (17 queries; FAISS omitida, documentado) |

**CONGELADO ao primeiro disparo real. Mudou a régua → exp010b/011.**
