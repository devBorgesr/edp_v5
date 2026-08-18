"""
config.py — Configuração centralizada do EDP v3.
Todos os parâmetros sobrescrevíveis via variáveis de ambiente.
"""
import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(os.environ.get("EDP_BASE_DIR",   "/content/edp_v3_memory"))
CACHE_DB    = BASE_DIR / "embed_cache.sqlite"
MEMORY_DIR  = BASE_DIR / "sessions"
METRICS_LOG = BASE_DIR / "metrics.jsonl"

# ── Pipeline ───────────────────────────────────────────────────────────────────
CHUNK_SIZE   = int(os.environ.get("EDP_CHUNK_SIZE",   "40"))
HIGH_SCORE   = float(os.environ.get("EDP_HIGH_SCORE", "0.65"))
MID_SCORE    = float(os.environ.get("EDP_MID_SCORE",  "0.40"))
MIN_WORDS    = int(os.environ.get("EDP_MIN_WORDS",    "5"))
DEDUP_THRESH = float(os.environ.get("EDP_DEDUP",      "0.75"))

# ── Embeddings ─────────────────────────────────────────────────────────────────
EMBED_MODEL          = os.environ.get("EDP_EMBED_MODEL",    "sentence-transformers/all-MiniLM-L6-v2")
EMBED_MODEL_FALLBACK = os.environ.get("EDP_EMBED_FALLBACK", "sentence-transformers/paraphrase-MiniLM-L3-v2")
EMBED_DIM            = int(os.environ.get("EDP_EMBED_DIM",   "384"))
EMBED_NORMALIZE      = True
EMBED_BATCH_SIZE     = int(os.environ.get("EDP_BATCH_SIZE",  "64"))
EMBED_MODEL_VERSION  = "minilm-l6-v2-v3"

# ── Cache ──────────────────────────────────────────────────────────────────────
CACHE_MAX = int(os.environ.get("EDP_CACHE_MAX", "100000"))

# ── Retrieval ──────────────────────────────────────────────────────────────────
RETRIEVAL_BACKEND = os.environ.get("EDP_RETRIEVAL_BACKEND", "faiss_flat")  # cosine|faiss_flat|faiss_ivf|hnsw
RETRIEVAL_TOP_K   = int(os.environ.get("EDP_TOP_K",          "10"))
RETRIEVAL_MIN_SIM = float(os.environ.get("EDP_MIN_SIM",      "0.20"))
ANN_NPROBE        = int(os.environ.get("EDP_NPROBE",         "8"))
HNSW_EF_SEARCH    = int(os.environ.get("EDP_HNSW_EF",        "50"))
HNSW_M            = int(os.environ.get("EDP_HNSW_M",         "16"))

# ── Retrieval híbrido (exp010, 07/2026) ────────────────────────────────────────
# DESLIGADO por padrão: com "0", MemoryStore.retrieve é EXATAMENTE o atual
# (cosine puro). Com EDP_HYBRID_RETRIEVAL=1, o retrieve usa o HybridRetriever
# (BM25+vetorial+RRF, SEM MMR — o exp010 mostrou MMR piorando neste tamanho de
# store). Evidência (exp010, H1 confirmada sobre dados reais): Recall@5
# 25%→87.5%, Redis 3/3 no top-5, session_summary 40%→10% do top-5 em queries
# vagas, guarda (pedidos de resumo) intacta.
# PROMOVIDO A DEFAULT ON (Fase 1, 08/07/2026) apos suite de regressao 3/3
# (R1 CP3 presente, R2 Recall 2/3, R3 SS 13.3%). Para DESLIGAR (reverter ao
# cosine antigo): EDP_HYBRID_RETRIEVAL=0 — a env var e a rede de seguranca.
# DIVIDA ASSUMIDA: ranking_score agora e escala RRF (~0.016) em vez de cosine
# (~0.4); dashboards/telemetria Gauss/retrieval_score veem a escala nova. Nao
# quebra funcao; reajuste dos paineis e ciclo separado. Reversivel pela env.
EDP_HYBRID_RETRIEVAL = os.environ.get("EDP_HYBRID_RETRIEVAL", "1") == "1"

# ── Slots de contexto (exp011 / Fase 1, 07/2026) ──────────────────────────────
# DESLIGADO por padrão (OFF = byte-idêntico ao atual). Ligado, os metadados
# estruturais (âncora temporal, histórico, bloco atual, summaries) saem da
# CONTAGEM retrieval[:max_retrieval] do ContextWindowManager — o corte passa a
# valer só para memórias recuperadas por similaridade. Defeito 1 da Fase 0:
# blocks tinha 5 metadados na frente (llm_adapter:2070-2319) e as memórias em
# 6+ (:2364); manager:305 cortava [:5] e decapitava todas as memórias mesmo
# com remaining=1164 tokens.
# PROMOVIDO A DEFAULT ON (Fase 1, 08/07/2026) junto com o hibrido, apos a
# suite 3/3. Para DESLIGAR (metadados voltam a contagem): EDP_CTX_SLOTS=0.
EDP_CTX_SLOTS = os.environ.get("EDP_CTX_SLOTS", "1") == "1"

# ── exp012 (write-path): proveniência na gravação — PROMOVIDO A DEFAULT ON ────
# Camada A: carimbo {n_mem_prompt, retrieval_tokens} na memória gravada.
# Camada B: answer_class={not_found,disqualification} + peso-piso/exclusão do
# híbrido quando proveniência indica falha de recuperação (R4, exp012) OU
# desqualificação auto-referente (DISQ-v1, exp016). Sinal EXATO só com
# EDP_CTX_SLOTS=1 (default; se alguém sobrescrever para 0 em runtime, o sinal
# volta a ser lista mista — a guarda de defesa de write_provenance.classify()
# cobre esse caso, descartando n_mem_prompt e caindo no estrato A/backlog).
# Regra R4 CONGELADA na Fase 3 (matriz fase 2, avaliador_matriz.py):
# negacao_textual OR kw_continuidade, estratificada por n_mem_prompt quando
# exato. Regra DISQ-v1 CONGELADA na Etapa 0 do exp016 (dry-run 239/2/0FP,
# predições 100%) — incondicional, sem estrato. Ver write_provenance.py.
# PROMOVIDO A DEFAULT ON (Fase 5, 15/07/2026) pós-arco exp012→exp016:
# auditoria acumulada (matriz N=97, 23 entries carimbadas nos backfills,
# 3 validações in vivo — Teste vivo pós-Fase-3, rodada de fechamento do
# exp016 15/07, ciclo de 4 gerações do exp015 quebrado — e DISQ com zero
# falsos positivos). Ver ESTADO_EXP012.md, seção "FASE 5: fechamento do
# arco", para o placar completo. ROLLBACK: EDP_WRITE_PROVENANCE=0 (env var
# — nenhum código precisa mudar, é a mesma rede de segurança usada para
# promover EDP_HYBRID_RETRIEVAL/EDP_CTX_SLOTS na Fase 1).
EDP_WRITE_PROVENANCE = os.environ.get("EDP_WRITE_PROVENANCE", "1") == "1"

# EDP_TOXIC_GUARDS (fix/toxic-guards, 30/07/2026) — governa a LEITURA das
# defesas de toxicidade (piso NOT_FOUND_FLOOR, exclusão híbrida, guarda de
# consolidação). EDP_WRITE_PROVENANCE passa a governar APENAS a escrita do
# carimbo answer_class. Motivo: rollback de escrita (EDP_WRITE_PROVENANCE=0)
# desarmava as três defesas de leitura sobre carimbos já persistidos em
# disco — achado do lab_edp, docs/ACHADO_FLAG_UNICA_TOXICIDADE.md. Default
# ON: com as duas flags ON (o caso hoje), comportamento byte-idêntico.
EDP_TOXIC_GUARDS = os.environ.get("EDP_TOXIC_GUARDS", "1") == "1"
NOT_FOUND_FLOOR = 0.05

# EDP_STORE_QUARANTINE (Dívida #53, docs/preregistro_fix_corrupcao_json.md,
# 04/08/2026) — governa edp/memory/atomic_io.py::_load_json_or_quarantine.
# Default ON: JSON truncado no meio (episodic/semantic/echo_chamber/blocks/
# ingest.session_index/profiles.registry) é quarentenado (os.replace
# atômico, byte-idêntico preservado) + logger.critical + evento Pareto
# "store_degraded", em vez de crashar o boot OU ser engolido em silêncio
# (`except Exception: self.x = []` sem log — os dois padrões que existiam
# antes desta dívida, ambos considerados errados no pré-registro).
# DIFERENTE de EDP_HYBRID_RETRIEVAL/EDP_CTX_SLOTS/EDP_WRITE_PROVENANCE/
# EDP_TOXIC_GUARDS acima: aqui o estado seguro é o NOVO comportamento —
# crash-on-corrupt não tem defensor. Esta flag é válvula de emergência
# só para ESTE mecanismo (ex.: bug não previsto na lógica de quarentena),
# NÃO um rollback de feature — por isso nome e leitura isolados, nunca
# perto de EDP_TOXIC_GUARDS/EDP_WRITE_PROVENANCE (o projeto já mediu o
# antipadrão de flag compartilhada — guarda de toxicidade morrendo junto
# com EDP_WRITE_PROVENANCE=0 — e não repete aqui). Com "0": comportamento
# pré-fix (propaga JSONDecodeError/UnicodeDecodeError sem quarentena).
EDP_STORE_QUARANTINE = os.environ.get("EDP_STORE_QUARANTINE", "1") == "1"
# exp016 (3ª classe de veneno — desqualificação auto-referente, RELATORIO_
# ETAPA0_EXP016.md P1): mesmo piso/exclusão do exp012, gate estendido de
# comparação pontual (== "not_found") para pertencimento a este conjunto.
# "disqualification" é INCONDICIONAL (decisão do pesquisador, 15/07/2026) —
# não passa pelos estratos A/B de n_mem_prompt, que continuam valendo só
# para NEG/KW (write_provenance.classify()).
TOXIC_ANSWER_CLASSES = {"not_found", "disqualification"}
# min_score do caminho híbrido: RRF produz scores ~1/(60+rank) (máx ≈0.016).
# O RETRIEVAL_MIN_SIM (0.20, escala cosine) zeraria TUDO — escala própria.
HYBRID_MIN_SCORE = float(os.environ.get("EDP_HYBRID_MIN_SCORE", "0.0"))

# ── exp017 Fase 0 (07/2026): controle negativo do retrieve (read-side) ────────
# DESLIGADO por padrão (OFF = byte-idêntico). Ligado, embaralha a ORDEM do
# conjunto top-k já pronto (edp/llm_adapter.py:2334) antes de entrar no
# context_builder — ZERO remoção, conjunto intacto. Seed determinística POR
# QUERY: random.Random(f"{EDP_SHUFFLE_SEED}:{sha256(query)}") — reprodutível
# entre runs, mas permutações distintas entre queries (seed única global
# degeneraria no próprio fenômeno C: listas iguais -> permutação igual).
# Instrumento de MEDIÇÃO (H2, controle negativo do PRE_REGISTRO_EXP017.md) —
# mutuamente exclusiva com EDP_RETRIEVE_DEDUP (Fase 1, ainda inexistente).
# SHUFFLE nunca é produção; se ambas as flags ligarem, é erro de configuração.
EDP_RETRIEVE_SHUFFLE = os.environ.get("EDP_RETRIEVE_SHUFFLE", "0") == "1"
EDP_SHUFFLE_SEED      = os.environ.get("EDP_SHUFFLE_SEED", "20260719")

# ── exp017 Fase 1 (07/2026): dedup do retrieve (read-side) ────────────────────
# DESLIGADO por padrão (OFF = byte-idêntico). Ligado, colapsa duplicatas do
# ranking já filtrado por governança — 1a passada por ID (fenômeno D:
# duplicação cross-camada por promoção, mesmo ID em episódica+semântica), 2a
# por hash normalizado (fenômeno A-no-resultado: texto idêntico, IDs
# distintos) — ANTES do truncamento em top_k, com refill dos próximos do
# ranking (RELATORIO_F1T1_EXP017.md). Mutuamente exclusiva com
# EDP_RETRIEVE_SHUFFLE e EDP_RETRIEVE_RANDOM_DROP.
EDP_RETRIEVE_DEDUP = os.environ.get("EDP_RETRIEVE_DEDUP", "0") == "1"

# Controle-reserva (ativado na Fase 0 — EXP017_FASE0.md §3, condição do Patch
# D satisfeita + truncamento=0% torna o SHUFFLE tautológico no kept): remove
# d itens ALEATÓRIOS do top-k bruto (d = quantas duplicatas o EDP_RETRIEVE_
# DEDUP removeria até k) com refill igual — mesmo par mecânico do dedup,
# critério aleatório em vez de duplicata. Seed por query = EDP_SHUFFLE_SEED
# (mesma disciplina do SHUFFLE). Mutuamente exclusiva com as outras duas.
EDP_RETRIEVE_RANDOM_DROP = os.environ.get("EDP_RETRIEVE_RANDOM_DROP", "0") == "1"


def resolve_retrieve_instrumentation_exp017(dedup: bool, shuffle: bool, random_drop: bool) -> str:
    """exp017 — guard de exclusividade mútua entre EDP_RETRIEVE_DEDUP,
    EDP_RETRIEVE_SHUFFLE e EDP_RETRIEVE_RANDOM_DROP. Mais de uma ligada ao
    mesmo tempo é erro de configuração: loga e prioriza OFF (nenhuma se
    aplica). Retorna "dedup" | "shuffle" | "random_pareado" | "off".
    """
    active = [
        name for name, on in (
            ("dedup", dedup), ("shuffle", shuffle), ("random_pareado", random_drop),
        ) if on
    ]
    if len(active) > 1:
        import logging
        logging.getLogger("edp.memory").error(
            "[exp017] flags de retrieve mutuamente exclusivas ligadas ao mesmo "
            "tempo: %s — priorizando OFF", active,
        )
        return "off"
    return active[0] if active else "off"

# ── Memória ────────────────────────────────────────────────────────────────────
DECAY_LAMBDA      = float(os.environ.get("EDP_DECAY_LAMBDA",  "0.1"))
MAX_MEMORY        = int(os.environ.get("EDP_MAX_MEMORY",       "500"))
WORKING_MEM_SIZE  = int(os.environ.get("EDP_WORKING_SIZE",    "20"))
EPISODIC_MEM_SIZE = int(os.environ.get("EDP_EPISODIC_SIZE",   "200"))
PRIORIDADE_PESO   = {"alta": 1.3, "media": 1.0, "baixa": 0.7}

# ── Consolidação ───────────────────────────────────────────────────────────────
CONSOLIDATION_CLUSTER_MIN = int(os.environ.get("EDP_CLUSTER_MIN",    "2"))
CONSOLIDATION_SIM_THRESH  = float(os.environ.get("EDP_CLUSTER_THRESH","0.80"))

# ── Scoring ────────────────────────────────────────────────────────────────────
SCORE_WEIGHTS = {
    "entropy":    float(os.environ.get("EDP_W_ENTROPY",    "0.20")),
    "diversity":  float(os.environ.get("EDP_W_DIVERSITY",  "0.15")),
    "relevance":  float(os.environ.get("EDP_W_RELEVANCE",  "0.25")),
    "novelty":    float(os.environ.get("EDP_W_NOVELTY",    "0.15")),
    "decay":      float(os.environ.get("EDP_W_DECAY",      "0.15")),
    "confidence": float(os.environ.get("EDP_W_CONFIDENCE", "0.10")),
}

# ── Temporal ───────────────────────────────────────────────────────────────────
TEMPORAL_DECAY_TYPE   = os.environ.get("EDP_DECAY_TYPE", "exponential")  # exponential|gaussian
TEMPORAL_GAUSSIAN_STD = float(os.environ.get("EDP_GAUSS_STD", "7.0"))    # dias

# ── Compression ────────────────────────────────────────────────────────────────
COMPRESSION_MAX_RATIO = float(os.environ.get("EDP_COMPRESS_RATIO", "0.5"))

# ── API ────────────────────────────────────────────────────────────────────────
# Dívida #48 (13/06/2026): default localhost-only (seguro por padrão).
# API sem auth + CORS ["*"] → bind 0.0.0.0 exporia tudo na rede local.
# Para expor conscientemente: EDP_API_HOST=0.0.0.0. Auth/CORS ficam p/ item D.
API_HOST    = os.environ.get("EDP_API_HOST", "127.0.0.1")
API_PORT    = int(os.environ.get("EDP_API_PORT", "8000"))
API_VERSION = "v3"

# ── Live feed / sensor ingest (WEBSOCKET_API.md) ────────────────────────────────
# Token vazio = aberto (default dev-friendly, igual ao CORS ["*"] acima).
# Setar EDP_LIVE_FEED_TOKEN para exigir autenticação no WS /stream.
EDP_LIVE_FEED_TOKEN  = os.environ.get("EDP_LIVE_FEED_TOKEN", "")
LIVE_FEED_LOG        = BASE_DIR / "live_feed.log"
LIVE_FEED_INDEX_PATH = BASE_DIR / "live_feed_index.json"

# ── Visualizador do grafo de conhecimento (GET /graph) ──────────────────────────
# Serve graphify-out/graph.html, gerado pelo graphify a partir do repositório.
#
# Default ON porque o conteúdo é derivado do próprio código do projeto — mas
# esta flag existe porque o conteúdo do grafo depende do que o graphify indexa,
# e isso é governado por .graphifyignore, não por este módulo. Se algum dia o
# ignore for removido/afrouxado, um arquivo de conversa ou ground-truth pode
# entrar no grafo e passaria a ser servido por este endpoint — numa API que
# roda com CORS ["*"] e EDP_LIVE_FEED_TOKEN vazio por padrão (acima).
# Desligue antes de expor o EDP fora de localhost.
#
# Verificado em 06/08/2026: grafo com 3.868 nós, zero vindos de arquivo de
# dado; detect() com o .graphifyignore em vigor exclui os 6 arquivos sensíveis
# (248 -> 246 detectados).
EDP_GRAPH_VIEWER = os.environ.get("EDP_GRAPH_VIEWER", "1") == "1"

# ── Wiki de conhecimento compilado (GET /wiki) ──────────────────────────────────
# Páginas por comunidade do grafo, derivadas de graphify-out/graph.json +
# GRAPH_REPORT.md. Mesmo perfil de risco do EDP_GRAPH_VIEWER acima: o conteúdo
# vem do código do projeto, e o que entra no grafo é governado por
# .graphifyignore. Default ON pelo mesmo motivo, e com a mesma ressalva —
# desligue antes de expor o EDP fora de localhost.
EDP_WIKI = os.environ.get("EDP_WIKI", "1") == "1"

# Indexar trecho de CONVERSA REAL nas páginas da wiki. Default OFF, e o
# default aqui não é conservadorismo genérico: esta API roda com
# allow_origins=["*"] (api/main.py:260) e EDP_LIVE_FEED_TOKEN vazio (acima),
# então uma página servida por ela é legível por qualquer origem sem
# autenticação. Ligar isto sem antes setar EDP_LIVE_FEED_TOKEN reabre a
# exposição fechada em 3076559 (.graphifyignore) e 99d827c (.gitignore).
# Nenhum código consome esta flag ainda — ver docs/wiki_conversas_pendente.md.
EDP_WIKI_CONVERSAS = os.environ.get("EDP_WIKI_CONVERSAS", "0") == "1"


# ── Âncora de tarefa compacta (peça 2.6f, 07/08/2026) ───────────────────────────
# A listagem "Decisões:" por seção da âncora custa 79% do bloco inteiro
# (medido: 9.100 de 11.486 chars em 10 seções x 6 decisões x 120 chars) e é
# quase toda redundante com o bloco consolidado, que já lista cada chave com
# sua seção de origem. "Quase" porque o consolidado guarda só a PRIMEIRA
# decisão de cada chave — se a Seção 4 muda `messaging`, o por-seção registra
# e o consolidado não.
#
# Com a flag ON: a linha por-seção sai, e o consolidado passa a carregar a
# cadeia de mudanças ("Kafka (S1) -> RabbitMQ (S4)"), que é a informação que
# realmente importa para continuidade de decisão e cresce só quando há mudança.
#
# Motivo do teto: a âncora é Camada 0.5, injetada ANTES da janela imediata, e
# é o único bloco sem limite — challenge tem 2000/800, decisões por seção têm
# 6 chaves, CAPS_POR_POSICAO limita cada slot. Em 10 seções ela chega a 96% do
# cap de 12000 do turno-1; a tarefa validada em 30/05 rodou nessa borda.
#
# Default OFF: muda o prompt que vai ao modelo (Tier 2/3, edp_metodologia.md).
EDP_ANCHOR_COMPACT = os.environ.get("EDP_ANCHOR_COMPACT", "0") == "1"


# ── Telemetria de tokens — Fase 1 da calibração (12/08/2026) ────────────────────
# Grava o par (chars enviados, tokens REAIS cobrados pela API) por chamada, em
# `pareto_store`, para trocar o `4 chars ≈ 1 token` de
# `runtime/context_window_manager.py:12-13` — que nunca foi medido — por uma
# razão medida no corpus real (PT-BR + código), onde a razão é comprovadamente
# diferente da de inglês.
#
# NÃO altera prompt, resposta, ranking ou custo. É leitura pura: o dado já chega
# em toda resposta da Anthropic (`usage.input_tokens`) e hoje é descartado.
#
# Default OFF, e o motivo NÃO é risco de comportamento (não há) — é disciplina de
# coleta: **ligar esta flag abre a janela de coleta, e a janela de coleta congela
# o formato de injeção**. Mudar o formato dos blocos (compactar, podar, encurtar)
# no meio da coleta mistura dois regimes no mesmo dataset, e a razão resultante
# não descreve nenhum dos dois — sem dar erro, só um número errado com cara de
# medido. Ligar é, portanto, uma decisão explícita de "estou coletando agora".
#
# Fase 2 (calcular a razão) e Fase 3 (aplicar) não existem ainda e exigem
# pré-registro próprio. Ver lab_edp_novo/docs/sujeito_edp/AUDITORIA_FASE1_TOKENS.md.
EDP_TOKEN_TELEMETRY = os.environ.get("EDP_TOKEN_TELEMETRY", "0") == "1"

# ── Regime de formato — o que precisa ser gravado JUNTO de cada amostra ─────────
# O congelamento de formato da Fase 1 era só convenção: nada registrava qual
# formato valia em cada amostra. Se uma destas mudar no meio da coleta, o
# dataset mistura regimes e a contaminação fica INDETECTÁVEL — não dá erro, dá
# um número errado com cara de medido.
#
# Com o estado gravado por amostra, mudar de regime deixa de ser veneno e vira
# ESTRATO SEPARÁVEL: as amostras de antes e de depois ficam distinguíveis pelo
# hash, e a Fase 2 analisa cada uma no seu regime em vez de jogar tudo fora.
#
# O princípio, e não a lista: **tudo que altera a composição do prompt entra
# aqui**. A lista é só a instância de hoje. `tests/test_token_telemetry.py`
# trava isso — enumera os `EDP_*` deste módulo e falha se algum não estiver
# classificado numa das duas tuplas. Flag nova obriga uma decisão explícita, em
# vez de deixar o próximo desenvolvedor supor que "essa provavelmente não
# importa".
FORMAT_STATE_FLAGS = (
    "EDP_HYBRID_RETRIEVAL",    # troca o mecanismo de retrieval
    "EDP_CTX_SLOTS",           # muda o que entra na contagem do corte
    "EDP_WRITE_PROVENANCE",    # muda o que é gravado -> muda prompts futuros
    "EDP_TOXIC_GUARDS",        # piso/exclusão -> muda o conjunto recuperado
    "EDP_RETRIEVE_DEDUP",      # muda o conjunto recuperado
    "EDP_RETRIEVE_SHUFFLE",    # muda a ORDEM do conjunto recuperado
    "EDP_RETRIEVE_RANDOM_DROP",# remove itens do conjunto recuperado
    "EDP_ANCHOR_COMPACT",      # muda o bloco da âncora de tarefa
    "EDP_STORE_QUARANTINE",    # no caminho degradado, muda o que o store carrega
)

# Classificadas como NÃO afetando o prompt — com o motivo, porque "não importa"
# sem motivo é a suposição que esta lista existe para impedir.
# ── Telemetria de ranking (13/08/2026) ──────────────────────────────────────────
# Quatro cortes decidem o que chega ao prompt e três eram invisíveis: min_score
# (aplicado ANTES do append, então a candidata nem existia), filtro adaptativo
# de sessão, e filtro_recusa. Só o top_k final aparecia, como `memory_hits`.
#
# Grava a cascata da SELEÇÃO — mesma disciplina do §10 do contrato da Fase 1,
# toda redução explicável — mais os DEZ fatores multiplicativos das ~20 do topo.
# Não altera prompt, ranking nem resposta: é leitura. Default OFF pelo padrão de
# flag-off byte-idêntico do NORTE §4.7.
EDP_RANKING_TELEMETRY = os.environ.get("EDP_RANKING_TELEMETRY", "0") == "1"

# ── Telemetria de reflexão (13/08/2026) ─────────────────────────────────────────
# `MetaReasoner.reflect()` roda em TODO turno pelo caminho vivo
# (llm_adapter.py:2071 -> pipeline.py:383) e o `ReflectionResult` inteiro é
# descartado — não só `reweights`: confidence, hallucination_risk, conflitos,
# redundâncias e critique também. `pipeline.py:280` já chamava isso de "dead
# store" e deixou o subsistema de pé porque o escopo daquela fase proibia
# removê-lo. Custa três matrizes cosine_similarity por turno para ninguém ler.
#
# Antes de decidir entre APLICAR (`reweights` no corte de chunks) ou REMOVER,
# é preciso saber a distribuição: se o reweight quase não varia entre chunks,
# aplicá-lo é ligar ruído; se varia, é alavanca. Isto mede — não aplica nada.
EDP_REFLECTION_TELEMETRY = os.environ.get("EDP_REFLECTION_TELEMETRY", "0") == "1"

# ── Telemetria do detector de contradição (13/08/2026) ──────────────────────────
# `scan_results` roda em todo retrieve com top_k>=2 e o `data/flags/` está VAZIO.
# Zero flags é AMBÍGUO: não rodou / abortou por embedding ausente / rodou e nada
# cruzou o limiar. Medido por leitura pura em 13/08: os 153 pares do
# default_cognitive dão máximo 0.778 contra SIMILARITY_THRESHOLD=0.85 — o limiar
# está acima do máximo do corpus, enquanto 16 de 18 textos têm negação. Grava
# `max_sim` por scan, que é o número que falta para calibrar o 0.85 um dia.
EDP_CONTRADICTION_TELEMETRY = os.environ.get("EDP_CONTRADICTION_TELEMETRY", "0") == "1"

# ── Propagação do correlation_id (18/08/2026) ───────────────────────────────
# Medido: `correlation_id` nulo em 18/18 registros de lineage e em 18/18
# `memory_added`, contra 38/38 em `token_usage`. Causa em
# docs/sujeito_edp/ACHADO_CORRELATION_ID.md (lab): o id é gravado num
# thread-local dentro do executor (llm_adapter.py:1682) e lido na thread do
# handler (websocket.py:1318). `contextvars` NÃO resolve — `run_in_executor`
# não copia contexto, e mutação lá dentro não volta.
#
# Com a flag LIGADA o turno passa a ser dono do id: o handler gera antes de
# entrar no executor e o passa explicitamente. Com ela DESLIGADA todo caminho
# recebe None e cada função gera o próprio id — byte-idêntico ao de hoje.
EDP_CORRELATION_PROPAGATION = os.environ.get("EDP_CORRELATION_PROPAGATION", "0") == "1"

FORMAT_STATE_FLAGS_IGNORADAS = (
    "EDP_RANKING_TELEMETRY",  # telemetria de seleção; não muda o prompt
    "EDP_REFLECTION_TELEMETRY",  # lê o ReflectionResult; não aplica nada
    "EDP_CONTRADICTION_TELEMETRY",  # lê o scan; não muda limiar nem flag
    "EDP_CORRELATION_PROPAGATION",  # muda de ONDE vem o id, não o prompt
    "EDP_WIKI",            # governa endpoint HTTP, não o prompt
    "EDP_WIKI_CONVERSAS",  # idem
    "EDP_GRAPH_VIEWER",    # idem
    "EDP_TOKEN_TELEMETRY", # é a própria coleta
)
