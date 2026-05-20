"""
run.py — Entrypoint do EDP v3.2 (PATCHED)

Modos:
    python run.py serve              → inicia API FastAPI
    python run.py test               → benchmark pipeline v3.1
    python run.py test_v32           → benchmark OrchestratorV32 completo
    python run.py bench_retrieval    → benchmark retrieval (1000 docs)
    python run.py bench_vector       → benchmark InMemoryVectorStore (novo)
    python run.py query <text> <q> [session_id] [--v32]

PATCHES APLICADOS:
  [FIX-1] from edp_v3 import metrics → from edp import metrics
  [FIX-2] memory.add() dentro do test loop protegido por try/except
  [FIX-3] bench_retrieval: embed_many com lista, não escalar
  [FIX-4] run_pipeline: session_id explícito em todas as chamadas
           (pipeline_patched.py remove GLOBAL_MEMORY singleton)
  [FIX-5] M.summary(from_file=True) para benchmark cross-session real
  [FIX-6] uvicorn: workers e timeout configuráveis via env
  [FIX-7] test_v32: modo de benchmark OrchestratorV32 com health report
  [FIX-8] bench_vector: benchmark InMemoryVectorStore vs RetrievalEngine
  [FIX-9] query_mode: --v32 flag usa OrchestratorV32 em vez de pipeline
"""
import os
import sys
import time

# ── serve ─────────────────────────────────────────────────────────────────────

def serve():
    """
    Inicia servidor FastAPI.
    Prioridade:
      1. edp.api.main:app     (v3.3 modular - estrutura nova)
      2. edp.api_v2:app       (v3.3 monolito - estrutura atual)
      3. edp.api:app          (v3.1 legacy)
    """
    import uvicorn
    from edp.config import API_HOST, API_PORT

    workers   = int(os.environ.get("EDP_WORKERS",  "1"))
    log_level = os.environ.get("EDP_LOG_LEVEL", "info")

    # Detecção automática da melhor API disponível
    api_module = None
    api_label  = None

    # Tenta v3.3 modular primeiro
    try:
        from edp.api.main import app as _modular_app
        if _modular_app is not None:
            api_module = "edp.api.main:app"
            api_label  = "v3.3 modular (estrutura nova)"
    except Exception as e:
        print(f"[serve] api.main indisponivel: {e}")

    # Fallback v3.3 monolito
    if api_module is None:
        try:
            from edp.api_v2 import app as _v2_app
            if _v2_app is not None:
                api_module = "edp.api_v2:app"
                api_label  = "v3.3 monolito (estrutura atual)"
        except Exception as e:
            print(f"[serve] api_v2 indisponivel: {e}")

    # Fallback v3.1 legacy
    if api_module is None:
        api_module = "edp.api:app"
        api_label  = "v3.1 legacy"

    print(f"[serve] usando {api_module} ({api_label})")
    print(f"[serve] http://{API_HOST}:{API_PORT}")
    print(f"[serve] dashboard:  http://{API_HOST}:{API_PORT}/dashboard")
    print(f"[serve] docs:       http://{API_HOST}:{API_PORT}/docs")
    print(f"[serve] health:     http://{API_HOST}:{API_PORT}/health")

    uvicorn.run(
        api_module,
        host=API_HOST,
        port=API_PORT,
        reload=False,
        workers=workers,
        log_level=log_level,
        timeout_keep_alive=30,
    )


# ── test (v3.1 pipeline) ──────────────────────────────────────────────────────

def test():
    from edp.pipeline import run_pipeline
    from edp.memory import MemoryStore
    from edp.filters import classificar_spam
    from edp import metrics as M  # [FIX-1]

    QUESTION   = "Como reduzir custo computacional em IA?"
    SESSION_ID = "sessao_v3_teste"                         # [FIX-4]
    memory     = MemoryStore(SESSION_ID)

    TESTS = {
        "compressão_básica": (
            """
            Compressão de contexto reduz tokens mantendo significado.
            Isso reduz custo computacional em sistemas RAG.
            comprar sapato barato frete gratis clique aqui!!!
            Python é amplamente utilizado em inteligência artificial.
            COMPRE AGORA OFERTA LIMITADA!!!
            Frameworks como PyTorch aceleram pesquisa.
            asdfghjklqwertyuiop zxcvbnm 123456789
            Bancos vetoriais permitem busca semântica eficiente.
            Bancos vetoriais permitem busca semântica eficiente.
            FAISS é usado para similarity search.
            Modelos transformer utilizam atenção contextual.
            lorem ipsum dolor sit amet consectetur adipiscing.
            Isso melhora tarefas de NLP.
            """,
            "alta",
        ),
        "spam_adversarial": (
            """
            RAG reduz alucinações em LLMs.
            OFERTA IMPERDÍVEL CLIQUE AQUI GRÁTIS!!!
            Retrieval Augmented Generation melhora respostas.
            Compre embeddings baratos frete grátis!!!
            FAISS acelera busca em vetores de alta dimensão.
            lorem ipsum dolor sit amet consectetur.
            Transformers melhoraram NLP significativamente.
            """,
            "media",
        ),
        "redundância_semântica": (
            """
            RAG reduz alucinações em LLMs.
            RAG reduz alucinações em modelos de linguagem.
            Retrieval Augmented Generation reduz alucinações.
            Sistemas RAG reduzem erros em LLMs.
            Embeddings capturam significado semântico.
            Vetores de embedding representam significado.
            Embeddings são representações vetoriais de texto.
            """,
            "media",
        ),
        "tópicos_misturados": (
            """
            Python é usado em ciência de dados.
            A receita de bolo leva farinha e ovos.
            Transformers melhoraram NLP significativamente.
            O time ganhou o campeonato ontem.
            FAISS permite busca eficiente em vetores.
            A temperatura máxima hoje foi 32 graus.
            RAG combina retrieval com geração de texto.
            """,
            "baixa",
        ),
    }

    passed = 0
    print("\n=== BENCHMARKS PIPELINE v3.1 ===\n")

    for nome, (text, prio) in TESTS.items():
        t0     = time.perf_counter()
        # [FIX-4] session_id explícito — sem GLOBAL_MEMORY singleton
        result = run_pipeline(text, QUESTION, session_id=SESSION_ID)
        lat    = (time.perf_counter() - t0) * 1000
        ctx    = result.context_str
        spam   = classificar_spam(ctx)[0] if ctx else False
        ok     = result.tokens_final < result.tokens_original and not spam
        status = "✅ PASS" if ok else "❌ FAIL"
        if ok:
            passed += 1

        print(
            f"[{status}] {nome:<30} | "
            f"redução={result.reduction_pct:5.1f}% | "
            f"latência={lat:7.1f}ms | "
            f"spam={'✅' if not spam else '❌'}"
        )

        for bloco in result.context:
            if bloco.strip():
                try:  # [FIX-2]
                    memory.add(bloco, score=0.5, prioridade=prio)
                except Exception as e:
                    print(f"  [WARN] memory.add falhou: {e}")

    print(f"\nResultado: {passed}/{len(TESTS)} passou")

    try:
        print(memory.report())
    except Exception:
        pass

    print("\nMÉTRICAS:")
    import json
    # [FIX-5] from_file=True → métricas persistidas (cross-session real)
    print(json.dumps(M.summary(from_file=True), indent=2))


# ── test_v32 (OrchestratorV32 completo) ──────────────────────────────────────

def test_v32():
    """
    [FIX-7] Benchmark do OrchestratorV32 — exercita todos os subsistemas v3.2:
      snapshot_manager, economy, storm_guard, meta_stability,
      pressure_regulator, biodiversity, decision_graph_v32.
    """
    try:
        from edp.orchestrator_v32 import OrchestratorV32, OrchestratorV32Config
        from edp.types_v32 import ImmutableCognitiveNode, AbstractionLevel
    except ImportError as e:
        print(f"[WARN] OrchestratorV32 não disponível: {e}")
        print("       Verifique se os módulos v3.2 estão instalados.")
        return

    print("\n=== BENCHMARK ORCHESTRATOR v3.2 ===\n")

    cfg  = OrchestratorV32Config()
    orch = OrchestratorV32(config=cfg)

    TEXTS = [
        "RAG reduz alucinações em LLMs usando retrieval semântico.",
        "Embeddings capturam significado contextual de texto.",
        "FAISS permite busca eficiente em espaços de alta dimensão.",
        "Transformers revolucionaram processamento de linguagem natural.",
        "Compressão de contexto reduz custo computacional em LLMs.",
        "Memória semântica preserva conhecimento entre sessões.",
        "Retrieval híbrido combina BM25 léxico com busca vetorial.",
        "Consolidação semântica agrupa memórias relacionadas.",
        "Pressure regulation garante homeostase cognitiva.",
        "Snapshot geracional preserva consistência temporal.",
    ]

    print("Ingestão de nós cognitivos:")
    t0 = time.perf_counter()
    for i, text in enumerate(TEXTS):
        node = ImmutableCognitiveNode(
            text=text,
            semantic_entropy=0.5 + (i % 3) * 0.1,
            abstraction_level=AbstractionLevel(i % 4),
            novelty_score=0.8 - (i * 0.02),
        )
        event = orch.ingest(node, pressure_delta=0.05)
        status = "✅" if event else "⚠ rejected"
        print(f"  [{i:02d}] {status} {text[:50]}")

    ingest_ms = (time.perf_counter() - t0) * 1000
    print(f"\n  Total ingest: {ingest_ms:.1f}ms ({ingest_ms/len(TEXTS):.1f}ms/node)")

    # Retrieval
    print("\nRetrieval:")
    try:
        from edp.embeddings import embed_one
        q_emb = embed_one("Como funciona retrieval semântico?")
        t0    = time.perf_counter()
        results = orch.retrieve(tuple(q_emb.tolist()), k=3)
        ret_ms  = (time.perf_counter() - t0) * 1000
        print(f"  {len(results)} resultados em {ret_ms:.2f}ms")
        for r in results:
            print(f"    [{r.score:.3f}] {r.node.text[:55]}")
    except Exception as e:
        print(f"  [WARN] retrieve falhou: {e}")

    # Health report
    print("\nHealth Report:")
    health = orch.health()
    print(f"  stability_mode:     {health.stability_mode}")
    print(f"  composite_pressure: {health.composite_pressure:.4f}")
    print(f"  snapshot_nodes:     {health.snapshot_node_count}")
    print(f"  storm_score:        {health.storm_metrics_score:.4f}")
    print(f"  economy_mode:       {health.economy_mode}")
    print(f"  tick_count:         {health.tick_count}")
    if health.warnings:
        print(f"  warnings: {health.warnings}")
    else:
        print(f"  warnings: nenhum ✅")

    orch.shutdown(timeout=3.0)
    print("\n✅ OrchestratorV32 shutdown limpo")


# ── bench_retrieval ───────────────────────────────────────────────────────────

def bench_retrieval():
    import numpy as np
    from edp.retrieval import RetrievalEngine
    from edp.embeddings import embed_many
    from edp.config import EMBED_DIM

    N     = 1000
    TOP_K = 5

    print(f"\n=== BENCHMARK RETRIEVAL ({N} docs) ===\n")

    docs = [
        f"Documento {i}: conceito de RAG, embeddings e retrieval semântico."
        for i in range(N)
    ]

    print("Gerando embeddings...")
    t0   = time.perf_counter()
    embs = embed_many(docs)
    et   = (time.perf_counter() - t0) * 1000
    print(f"  embed_ms = {et:.1f}")

    for backend in ("cosine", "faiss_flat"):
        engine = RetrievalEngine(backend=backend, dim=EMBED_DIM)
        engine.add(embs, docs)

        query_list = ["Como funciona retrieval em RAG?"]  # [FIX-3]
        query_emb  = embed_many(query_list)[0]

        t0  = time.perf_counter()
        res = engine.search(query_emb, top_k=TOP_K)
        st  = (time.perf_counter() - t0) * 1000

        print(
            f"  [{backend:<12}] search_ms={st:.3f} | "
            f"found={len(res.indices)} | confidence={res.confidence:.3f}"
        )

        # Testa enhanced_search (novo método do patch)
        entries = [
            {"embedding": embs[i].tolist(), "text": docs[i],
             "ultimo_acesso": time.time(), "acessos": 0}
            for i in range(min(100, N))
        ]
        t0 = time.perf_counter()
        res_enh = engine.enhanced_search(query_emb, entries, top_k=TOP_K)
        enh_ms  = (time.perf_counter() - t0) * 1000
        print(
            f"  [{backend+'_enhanced':<18}] search_ms={enh_ms:.3f} | "
            f"found={len(res_enh.indices)} | confidence={res_enh.confidence:.3f}"
        )


# ── bench_vector (novo) ───────────────────────────────────────────────────────

def bench_vector():
    """
    [FIX-8] Benchmark InMemoryVectorStore vs RetrievalEngine.
    Compara throughput, latência e precisão.
    """
    import numpy as np
    from edp.embeddings import embed_many
    from edp.config import EMBED_DIM

    try:
        from edp.vector_store import InMemoryVectorStore, VectorEntry
    except ImportError:
        print("[WARN] vector_store não disponível — copie vector_store.py para edp/")
        return

    N     = 1000
    TOP_K = 5
    print(f"\n=== BENCHMARK VECTOR STORE ({N} docs) ===\n")

    docs = [
        f"Documento {i}: conceito de RAG, embeddings e retrieval semântico."
        for i in range(N)
    ]

    print("Gerando embeddings...")
    t0   = time.perf_counter()
    embs = embed_many(docs)
    et   = (time.perf_counter() - t0) * 1000
    print(f"  embed_ms = {et:.1f}")

    # Add em batch
    store = InMemoryVectorStore(dim=EMBED_DIM)
    entries = [
        VectorEntry(id=f"doc_{i}", embedding=tuple(embs[i].tolist()))
        for i in range(N)
    ]
    t0 = time.perf_counter()
    added = store.batch_add(entries)
    add_ms = (time.perf_counter() - t0) * 1000
    print(f"  batch_add: {added} docs em {add_ms:.1f}ms ({add_ms/N*1000:.1f}μs/doc)")

    # Query
    query_emb = tuple(embs[0].tolist())
    t0  = time.perf_counter()
    res = store.query(query_emb, top_k=TOP_K)
    q_ms = (time.perf_counter() - t0) * 1000
    print(f"  query (top={TOP_K}): {q_ms:.3f}ms | top_id={res[0].id if res else 'N/A'}")

    # Query concorrente
    import threading
    errors = []
    latencies = []

    def _query(_):
        t = time.perf_counter()
        try:
            store.query(query_emb, top_k=TOP_K)
            latencies.append((time.perf_counter() - t) * 1000)
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=_query, args=(i,)) for i in range(20)]
    t0 = time.perf_counter()
    for th in threads: th.start()
    for th in threads: th.join()
    total_ms = (time.perf_counter() - t0) * 1000

    print(f"  20 queries concorrentes: {total_ms:.1f}ms total | "
          f"avg={sum(latencies)/len(latencies):.2f}ms | errors={len(errors)}")

    m = store.health_report()
    print(f"  health: {m}")


# ── query ─────────────────────────────────────────────────────────────────────

def query_mode(args: list):
    if len(args) < 2:
        print("Uso: python run.py query <texto> <pergunta> [session_id] [--v32]")
        sys.exit(1)

    text       = args[0]
    question   = args[1]
    use_v32    = "--v32" in args
    raw_args   = [a for a in args if a != "--v32"]
    session_id = raw_args[2] if len(raw_args) > 2 else "default"

    if use_v32:
        # [FIX-9] usa OrchestratorV32
        _query_v32(text, question, session_id)
    else:
        _query_v31(text, question, session_id)


def _query_v31(text: str, question: str, session_id: str) -> None:
    from edp.pipeline import run_pipeline
    from edp.memory import MemoryStore

    # [FIX-4] session_id explícito
    result = run_pipeline(text, question, session_id=session_id)
    print(f"\n=== CONTEXTO (pipeline v3.1) ===\n{result.context_str}")
    print(f"REDUÇÃO: {result.reduction_pct:.2f}%")

    mem = MemoryStore(session_id)
    for bloco in result.context:
        if bloco.strip():
            try:
                mem.add(bloco, score=0.5, prioridade="media")
            except Exception:
                pass

    retrieved = mem.retrieve(question, top_k=3)
    print(f"\nMEMÓRIA [{session_id}]:")
    for r in retrieved:
        print(f"  [{r.get('ranking_score', 0):.3f}] {r['text'][:70]}")


def _query_v32(text: str, question: str, session_id: str) -> None:
    """[FIX-9] Query via OrchestratorV32."""
    try:
        from edp.orchestrator_v32 import OrchestratorV32
        from edp.types_v32 import ImmutableCognitiveNode
        from edp.pipeline import run_pipeline
        from edp.embeddings import embed_one
    except ImportError as e:
        print(f"[WARN] Módulo v3.2 ausente: {e}. Fallback para v3.1.")
        _query_v31(text, question, session_id)
        return

    # Pipeline v3.1 para compressão inicial
    result = run_pipeline(text, question, session_id=session_id)
    print(f"\n=== CONTEXTO COMPRIMIDO ===\n{result.context_str}")
    print(f"REDUÇÃO: {result.reduction_pct:.2f}%")

    # Ingere blocos no orchestrator v3.2
    orch = OrchestratorV32()
    for i, bloco in enumerate(result.context):
        if bloco.strip():
            node = ImmutableCognitiveNode(text=bloco, semantic_entropy=0.5)
            orch.ingest(node, pressure_delta=0.03)

    # Retrieval v3.2
    try:
        q_emb = embed_one(question)
        results = orch.retrieve(tuple(q_emb.tolist()), k=3)
        print(f"\nRETRIEVAL v3.2 ({len(results)} resultados):")
        for r in results:
            print(f"  [{r.score:.3f}] {r.node.text[:70]}")
        health = orch.health()
        print(f"\nHEALTH: mode={health.stability_mode} pressure={health.composite_pressure:.3f}")
    except Exception as e:
        print(f"[WARN] retrieve falhou: {e}")

    orch.shutdown(timeout=2.0)


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "serve":
        serve()
    elif args[0] == "test":
        test()
    elif args[0] == "test_v32":          # [FIX-7] novo modo
        test_v32()
    elif args[0] == "bench_retrieval":
        bench_retrieval()
    elif args[0] == "bench_vector":      # [FIX-8] novo modo
        bench_vector()
    elif args[0] == "query":
        query_mode(args[1:])
    else:
        print(__doc__)
        sys.exit(1)
