# Auditoria de constantes não-calibradas — `edp_v5`

**Data:** 2026-08-12. **Escopo:** este repositório (`edp_v5`, kernel), não
`lab_edp_novo`/`sf_exportador` — mesmo recorte do `README.md` desta sessão.
**Método:** grep sistemático por padrões de nome (`THRESH|WEIGHT|FACTOR|
BOOST|BIAS|ALPHA|BETA|GAMMA|DECAY|RATIO|CUTOFF|MARGIN|PENALTY|BONUS|SCALE|
COEF|WINDOW|CAP|LIMIT`) e por literais decimais soltos (`= 0\.[0-9]+`) em
`edp/**/*.py`, excluindo `edp/lab/` (experimentos, não produção) e ruído de
CSS/HTML dos routers. Cada achado foi lido no contexto — comentário
imediatamente acima/abaixo — para decidir a categoria. Comandos exatos na
seção 6.

**O que conta como "não-calibrado" aqui**: não é "número que eu acho
errado". É número que decide ranking, corte ou classificação e **não tem,
no próprio código, nenhuma citação de medição, experimento ou argumento
formal** que explique por que é *esse* valor e não outro.

---

## 1. Legenda — 4 camadas, não 2

Categorizar como "calibrado / não calibrado" achata uma diferença real. O
código tem quatro níveis distintos de justificativa:

| tier | o que significa | quantos achados |
|---|---|---|
| **D — Medido** | vem de experimento pré-registrado com dado real (`exp0XX`), número citável | 6 mecanismos (cobrem dezenas de sub-parâmetros) |
| **C — Argumentado** | derivado de identidade matemática (razão áurea φ), com justificativa formal no código, mas **nunca validado contra dado real do EDP** | ~6 constantes |
| **B — Anedótico** | tem uma frase explicando a intenção, mas a evidência citada é **um caso só** ("caso real motivador: `16c659ea`") ou uma decisão de empatar com outro número igualmente não medido | ~7 constantes |
| **A — Nu** | literal solto, zero comentário sobre origem | **~90 constantes** (contagem por grep, ver §6) |

Tier D e C não são o problema. São exibidos aqui só para mostrar que o
projeto SABE fazer isso quando quer — o que torna o volume do Tier A mais
notável, não menos.

---

## 2. Os quatro achados que eu destacaria numa auditoria paga

### 2.1 — `score=0.65` hardcoded 4×, desconectado do único lugar que o trata como parâmetro

`edp/config.py:16`: `HIGH_SCORE = float(os.environ.get("EDP_HIGH_SCORE", "0.65"))`
— é a ÚNICA definição de "0.65" no repositório que é nomeada, comentável e
sobrescrevível por env var.

Mas os 4 call sites que gravam memória com esse score não importam
`HIGH_SCORE` — escrevem o literal `0.65` direto:

- `edp/llm_adapter.py:2892` — `self._memory.add(combined, score=0.65, ...)`
- `edp/llm_adapter.py:2901` — idem (branch de fallback)
- `edp/api/routes/websocket.py:1214` — idem
- `edp/api/routes/websocket.py:1236` — idem

Confirmado por grep: nenhum dos dois arquivos importa `HIGH_SCORE` de
`config` (`grep -n "HIGH_SCORE" edp/llm_adapter.py edp/api/routes/websocket.py`
→ zero ocorrências). Setar `EDP_HIGH_SCORE=0.80` hoje muda o corte de
classificação em `pipeline.py`/`scoring.py`, mas os 4 sites continuam
gravando `0.65` — a env var vira mentira parcial sem avisar ninguém.
Já registrado, parcialmente, no `README.md §4` ("score=0.65 hardcoded em 4
locais... sem calibração documentada") — esta auditoria acrescenta a causa:
não é só "sem calibração", é "desconectado do parâmetro que já existe para
isso".

### 2.2 — Sprawl de thresholds de similaridade: 5 números para "parecido demais", 4 deles soltos

| valor | onde | nomeado? |
|---|---|---|
| 0.75 | `config.py:19` `DEDUP_THRESH` | sim, env-configurável |
| 0.78 | `compression.py:174`, `fuse_chunks(..., threshold=0.78)` | **não** — literal inline |
| 0.80 | `config.py:189` `CONSOLIDATION_SIM_THRESH` | sim, env-configurável, zero comentário de origem |
| 0.82 | `pipeline.py:300`, outra chamada a `fuse_chunks(..., threshold=0.82)` | **não** — literal inline |
| 0.88 | `pipeline.py:406`, `suppress_threshold=0.88` | **não** — literal inline |

A MESMA função (`fuse_chunks`) é chamada com 0.78 num arquivo e 0.82 noutro
— nenhum comentário explica por que a fusão de chunks em `compression.py`
tolera mais similaridade (threshold mais baixo = funde mais cedo) do que a
mesma operação em `pipeline.py`. Pode ser intencional; não há como saber
sem arqueologia de commit, porque a única evidência textual é o número.

### 2.3 — Cadeia de "empata com X": nenhum boost de fonte foi medido, todos foram copiados de outro

`edp/memory_classifier.py:142-154` (`SOURCE_TYPE_WEIGHTS`):

```
"external":        1.20   # sem comentário de origem
"session_summary":  1.15   # sem comentário de origem
"camara_response":  1.15   # "Subido de 1.00 → 1.15 (empata com session_summary)
                            #  ... Caso real motivador: 16c659ea"
```

`edp/memory/store.py:576` (`anchor_boost`):
```
anchor_boost = 1.20 if e.get("is_epistemic_anchor") else 1.0
# "ganham boost 1.20 — empata com source_type 'external'"
```

`camara_response` foi setado igual a `session_summary` porque pareceu certo
os dois empatarem — não porque uma medição mostrou que 1.15 é o valor que
maximiza alguma métrica. `anchor_boost` foi setado igual a `external` pelo
mesmo motivo. **Nenhum dos dois números-fonte (`external`=1.20,
`session_summary`=1.15) tem, por sua vez, uma justificativa própria no
código** — são o topo da cadeia e são Tier A. Encadear "empata com" a partir
de um número não medido não produz um número medido; produz um número
não medido replicado três vezes com aparência de consenso.

`dom_penalty = 0.70` (`memory/store.py:568`) tem o mesmo padrão: o
comentário explica o CONCEITO ("hiperdominante leva multiplicador, não
bloqueio"), nunca por que 0.70 e não 0.60 ou 0.80.

### 2.4 — `SESSION_GAP_THRESHOLD_SEC` definido duas vezes, independentemente

```
edp/llm_adapter.py:186:   SESSION_GAP_THRESHOLD_SEC = 4 * 3600   # 4h sem atividade
edp/memory/store.py:81:   SESSION_GAP_THRESHOLD_SEC = 4 * 3600   # 4h
```

Mesmo nome, mesmo valor, dois lugares — hoje concordam por coincidência de
manutenção, não por importação de uma fonte única. Se alguém recalibrar um
e esquecer o outro, os dois módulos passam a discordar sobre o que é "fim
de sessão" sem erro, sem log, sem teste que pegue — só resultado diferente
dependendo de qual caminho de código rodou.

---

## 3. Tabela completa — Tier A ("número nu", sem nenhuma justificativa no código)

| constante | valor | arquivo:linha | usada em |
|---|---|---|---|
| `SCORE_WEIGHTS` (6 pesos) | entropy .20 / diversity .15 / relevance .25 / novelty .15 / decay .15 / confidence .10 | `config.py:192-199` | `scoring.py::compute_score` |
| `PRIORIDADE_PESO` (3 pesos) | alta 1.3 / media 1.0 / baixa 0.7 | `config.py:185` | ranking de retrieval |
| `NOT_FOUND_FLOOR` | 0.05 | `config.py:97` | piso de exclusão tóxica (não env-configurável, ao contrário dos vizinhos) |
| `CONSOLIDATION_SIM_THRESH` | 0.80 | `config.py:189` | consolidação episódica→semântica |
| `TEMPORAL_GAUSSIAN_STD` | 7.0 dias | `config.py:203` | decay gaussiano (modo alternativo, não-default) |
| `COMPRESSION_MAX_RATIO` | 0.5 | `config.py:206` | limite de compressão de contexto |
| `DECAY_LAMBDA` | 0.1 | `config.py:181` | decay exponencial (meia-vida ~7d, comentado, mas 7d em si não justificado) |
| `HIGH_SCORE` / `MID_SCORE` | 0.65 / 0.40 | `config.py:16-17` | `pipeline.py` classificação de chunk |
| `RETRIEVAL_MIN_SIM` | 0.20 | `config.py:35` | corte mínimo de similaridade no retrieval cosine |
| `DEDUP_THRESH` | 0.75 | `config.py:19` | ver §2.2 |
| `CHUNK_SIZE` / `MIN_WORDS` | 40 / 5 | `config.py:15,18` | segmentação de texto |
| `ANN_NPROBE` / `HNSW_EF_SEARCH` / `HNSW_M` | 8 / 50 / 16 | `config.py:36-38` | parâmetros de índice ANN — este trio é do módulo **morto** `retrieval.py` (zero importador), risco prático baixo |
| `CACHE_MAX` / `EMBED_BATCH_SIZE` | 100000 / 64 | `config.py:30,26` | operacional, baixo risco epistêmico |
| `CONFLICT_THRESH` / `REDUNDANCY_THRESH` / `LOW_CONF_THRESH` / `HIGH_CONF_THRESH` | 0.20 / 0.92 / 0.35 / 0.70 | `meta_reasoner.py:19-22` | `MetaReasoner.reflect()` |
| `MAX_REFLECTION_DEPTH` / `REFLECTION_COOLDOWN` | 3 / 5.0s | `meta_reasoner.py:15-16` | throttle de reflexão |
| `hallucination_risk < 0.30` | 0.30 | `meta_reasoner.py`, `ReflectionResult.is_reliable` | inline, nem constante nomeada |
| `FORGET_THRESH`/`DEEPEN_THRESH`/`ARCHIVE_THRESH`/`REVIEW_THRESH`/`CONSOLIDATE_MIN` | 0.08/5/0.75/0.20/2 | `cognitive_scheduler.py:33-37` | agendador cognitivo |
| `TAG_SIMILARITY_THRESHOLD` | 0.75 | `session_summary.py:29` | agrupamento de tags |
| `LOOP_SIM_THRESHOLD` | 0.65 | `trajectory.py:30` | detecção de loop temático |
| `WEIGHTS` do CHI (4 pesos) | .30/.30/.20/.20 | `runtime/health_index.py:73-78` | Cognitive Health Index — soma 1.0, sem justificativa da distribuição |
| `MATURE`/`DEVELOPING`/`GROWING`_THRESHOLD | 0.80/0.60/0.40 | `runtime/health_index.py:68-70` | classificação de maturidade — números redondos, sem citação |
| `MIN_SAMPLES_GAUSS`/`MIN_SAMPLES_MATURE` | 20/50 | `runtime/health_index.py` | idem |
| `TARGET_DOMAINS`/`PAIRS_PER_ENTRY` | 10/5 | `runtime/health_index.py` | idem |
| `SIMILARITY_THRESHOLD` | 0.85 | `runtime/contradiction_flagger.py:65` | detecção de contradição — **sinal cujo `scan_results()` já é descartado no ranking** (`README.md §4`), então este threshold hoje não afeta nada em produção |
| `DEFAULT_WINDOW_DAYS` | 7 | `runtime/bayes_calibrator.py:63`, `runtime/gauss_calibrator.py:63` | "coerente com... retrieval_monitor" — consistência interna, não medição |
| `DEFAULT_Z_THRESHOLD` | 2.0 | `runtime/gauss_calibrator.py:66` | convenção estatística padrão (95%), não validada contra a distribuição real do EDP |
| `EMA_ALPHA_SLOW` / `EMA_ALPHA_GATE` | 0.25 / 0.35 | `adaptive_controller.py:88-89` | as DUAS únicas EMAs do módulo que **não** são φ — o próprio docstring diz "φ usado em exatamente três lugares", estas duas são as exceções não documentadas como tal |
| `HIGH_THRESHOLD_LO/HI`, `MID_THRESHOLD_LO/HI` | 0.50/0.95, 0.20/0.65 | `adaptive_controller.py:103-106` | bandas de clamp |
| `COMPRESSION_BASE/MIN/MAX` | 0.5/0.10/1.00 | `adaptive_controller.py:99-101` | força de compressão adaptativa |
| `ATTENTION_WINDOW_BASE/MIN/MAX` | 12/4/32 | `adaptive_controller.py:92-94` | janela de atenção base (o φ entra só no expoente, a base 12 é nua) |
| `stress_delta` cap / `risk_delta` cap / guarda de inversão | 0.20 / 0.15 / `high−0.15` | `adaptive_controller.py:486-498` | inline, dentro de `_compute_thresholds()` |
| `THRESHOLD_KEEP` / `THRESHOLD_SUMMARIZE` | 0.72 / 0.42 | `scoring.py:52-53` | fonte dos `HIGH_THRESHOLD_BASE`/`MID_THRESHOLD_BASE` do adaptive_controller — mas nus na própria origem |
| numerador de `REDUNDANCY_WEIGHT` | 0.3 (de `0.3/φ`) | `scoring.py:48` | metade-φ, metade nu |
| coeficiente de `access_boost` | 0.05 (de `1+0.05×log1p(n)`) | `temporal.py:46` | reforço por frequência de acesso |
| `_MAX_RETRIES`/`_RETRY_DELAY`/`_OOM_BATCH_MIN` | 3/1.0s/8 | `embeddings.py:48-50` | operacional (retry), risco epistêmico baixo |
| `MAX_PAIRS`/`CLEANUP_KEEP_FRACTION` | 50000/0.9 | `co_occurrence.py:33-34` | operacional |
| thresholds inline de `fuse_chunks`/`suppress` | 0.78/0.82/0.88 | `compression.py:174`, `pipeline.py:300,406` | ver §2.2 |
| `BLOCO_CAP_CHARS` | 1500 | `llm_adapter.py:2293` | corte de bloco no prompt |
| `CURRENT_SESSION_TRUST_THRESHOLD` | 0.30 | `memory/store.py:103` | confiança de sessão atual |
| `score=0.65` × 4 sites | 0.65 | ver §2.1 | ver §2.1 |
| `SESSION_GAP_THRESHOLD_SEC` × 2 definições | 4×3600 | ver §2.4 | ver §2.4 |

## 4. Tier B/C — para contraste, o que TEM alguma justificativa

**Tier C (argumento formal, não medição):**
`learning_gate.py` (`_INV_PHI`≈0.618, `_INV_PHI2`≈0.382 como thresholds de
gate por prioridade, expoente `score^(1/φ)`) e `adaptive_controller.py`
(`EMA_ALPHA_FAST=PHI_INV`, escala de janela de atenção por `φ^adj`) usam a
razão áurea com uma justificativa matemática real no docstring ("minimiza a
derivada local", "φ NÃO é usado decorativamente... exatamente três
lugares"). É o padrão de engenharia mais maduro do repositório para
constantes — mas continua sendo **argumento, não medição**: nenhum dos dois
módulos cita um teste que compare φ contra um valor concorrente usando dado
real do EDP. `scoring.py::DIVERSITY_WEIGHT = 1/(φ³×2)` é da mesma família.

**Tier B (uma frase de intenção, evidência é 1 caso):**
`SESSION_BOOST_FACTOR=1.60`/`OUT_OF_SESSION_PENALTY=0.85` (`memory/store.py:82-83`,
rotulado "calibrado 04/06/2026") é o exemplo mais citado nesta sessão — é a
mesma constante que o pré-registro do Gap Score (`docs/preregistro_gap_score.md`,
pergunta Q3) tentou e não conseguiu achar material verificável na wiki para
justificar. O comentário no código é honesto sobre a origem: "calibração
corrige alucinação observada empiricamente" — **um** incidente (cache Docker
vs. Redis), não uma varredura de valores. Chamar isso de "calibrado" no
comentário é uma palavra mais forte do que o processo por trás dela.

**Tier D (medido, com experimento citável):**
`EDP_HYBRID_RETRIEVAL` (exp010, Recall@5 25%→87.5%), `EDP_CTX_SLOTS` (exp011),
`EDP_WRITE_PROVENANCE`/regras R4 e DISQ-v1 (exp012/exp016, matriz N=97),
`EDP_RETRIEVE_DEDUP` (exp017, H1), `EDP_ANCHOR_COMPACT` (medição de 9.100/
11.486 chars), Dívida #41 (pressão de RAM recalibrada para hardware real).
Todos em `config.py`, todos com data, experimento e número citados no
comentário — o padrão que o resto do arquivo não segue.

---

## 5. Cruzamento com `docs/DIVIDAS.md`

O catálogo formal de dívidas tem **3 entradas** (#41, #46d, #53) — nenhuma
delas é sobre constantes de scoring/ranking. `anchor_boost`/`score=0.65`
já apareciam no `README.md §4` desta sessão como "sem calibração
documentada", mas **não têm ID de dívida formal** em `DIVIDAS.md` — ou
seja, o achado mais repetido desta auditoria (a cadeia de boosts do §2.3)
não está no registro que o projeto usa para rastrear isso. Se a disciplina
de dívida for para valer, os itens do Tier A com maior superfície de
impacto (`SCORE_WEIGHTS`, o sprawl de similaridade do §2.2, e a
desconexão `score=0.65`↔`HIGH_SCORE` do §2.1) são candidatos a dívida
nova — decisão de priorização é do Daniel, não desta auditoria.

---

## 6. Reprodutibilidade

```bash
# named constants
grep -rnE '\b[A-Z_]*(THRESH|WEIGHT|FACTOR|BOOST|BIAS|ALPHA|BETA|GAMMA|DECAY|RATIO|CUTOFF|MARGIN|PENALTY|BONUS|SCALE|COEF|WINDOW|CAP|LIMIT)[A-Z_]*\s*[:=]\s*[0-9]' edp/ --include="*.py" | grep -v "^edp/lab/"

# lowercase / inline literals
grep -rnE '(^|[^0-9a-zA-Z_])[a-z_]*(thresh|weight|factor|boost|bias|alpha|beta|gamma|decay|ratio|cutoff|margin|penalty|bonus|scale|coef)[a-z_]*\s*[:=]\s*[0-9]' edp/ --include="*.py" | grep -v "^edp/lab/"

# config.py completo
grep -nE '^[A-Z_]+ *=' edp/config.py
```

Contagem por grep, ~90 ocorrências Tier A após remover ruído de CSS/HTML
dos routers (`edp/api/routes/*.py` gera páginas HTML inline com `margin:`/
`font-size:` que casam com `\bMARGIN\b`/`\bSCALE\b` por acaso — filtrados
manualmente, listados na tabela do §3 apenas os que decidem alguma coisa
sobre memória/ranking). Não é contagem exaustiva linha-a-linha do
repositório inteiro — é o que os padrões de nome usados no método
capturam; um valor sem nenhuma palavra-chave no nome (ex.: uma constante
chamada `X` em vez de `X_THRESHOLD`) não apareceria aqui.
