"""
edp.api.main — Aplicação FastAPI modular do EDP v3.5

Esta é a aplicação NOVA que substitui gradualmente o monolito api_v2.py.

Características:
  - Cada responsabilidade em seu router (health, memory, metrics, llm, websocket)
  - Dashboard servido como arquivos estáticos (HTML/CSS/JS independentes)
  - Pré-carregamento de modelos de embedding no startup (elimina cold start)
  - CORS configurado para desenvolvimento
  - Logging estruturado

Para usar:
  uvicorn edp.api.main:app --host 0.0.0.0 --port 8000

Ou via run.py serve (que detecta automaticamente).
"""
from __future__ import annotations

import logging
from pathlib import Path
from contextlib import asynccontextmanager

logger = logging.getLogger("edp.api.main")
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')


# ── Lifespan: startup + shutdown ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    """
    Boot sequence com estados explícitos:
        COLD → WARMING → READY (ou DEGRADED se algo falhou)
    """
    logger.info("[startup] EDP v3.5 iniciando...")

    # Boot state machine
    boot = None
    try:
        from ..runtime.boot_state import get_boot_state, RuntimeState
        boot = get_boot_state()
        boot.transition(RuntimeState.WARMING, "startup_begin")
    except Exception as e:
        logger.warning("[startup] boot_state indisponivel: %s", e)

    # Pré-carrega modelo de embedding (elimina os 40s de cold start)
    embedding_ok = True
    try:
        import asyncio
        from .. import embeddings
        loop = asyncio.get_event_loop()

        def _preload():
            try:
                # Dívida #42 (10/06/2026): warmup REAL. O antigo
                # embed_one("warmup") dava cache hit em disco e nunca
                # carregava o modelo — "pré-carregado" era falso.
                if embeddings.warmup_model():
                    logger.info("[startup] modelo de embedding pré-carregado (warmup real)")
                    return True
                logger.warning("[startup] warmup_model retornou False")
                return False
            except Exception as e:
                logger.warning("[startup] preload de embeddings falhou: %s", e)
                return False

        embedding_ok = await loop.run_in_executor(None, _preload)
    except Exception as e:
        logger.warning("[startup] pré-carregamento pulado: %s", e)
        embedding_ok = False

    if boot:
        boot.mark_component("embeddings", healthy=embedding_ok,
                            error=None if embedding_ok else "preload failed",
                            critical=True)

    # Inicializa sessão default
    session_ok = True
    try:
        from ..runtime import get_runtime, get_memory
        get_runtime("default")
        get_memory("default")
        logger.info("[startup] sessão 'default' pronta")
    except Exception as e:
        logger.warning("[startup] falha ao inicializar default: %s", e)
        session_ok = False

    if boot:
        boot.mark_component("default_session", healthy=session_ok,
                            error=None if session_ok else "session init failed",
                            critical=False)

    # ── P4: Prewarming de retrieval (não-bloqueante, task background) ───────
    # Executa 1 retrieval dummy para esquentar:
    #   - cache de cosine_similarity matrix
    #   - paths quentes do retrieve
    #   - allocators de numpy
    # Custa ~200ms mas executa em background, NÃO bloqueia startup.
    try:
        from ..runtime import get_memory, is_valid

        async def _prewarm_retrieval():
            try:
                # Aguarda um pouco para deixar startup terminar
                await asyncio.sleep(0.5)
                loop = asyncio.get_event_loop()

                def _do_prewarm():
                    mem = get_memory("default")
                    if not is_valid(mem):
                        return False
                    try:
                        # Query dummy curta, ignoramos resultado
                        _ = mem.retrieve("warmup query", top_k=3, min_score=0.0)
                        return True
                    except Exception:
                        return False

                ok = await loop.run_in_executor(None, _do_prewarm)
                if ok:
                    logger.info("[startup] retrieval prewarm ok (background)")
                else:
                    logger.debug("[startup] retrieval prewarm pulado")
            except Exception as e:
                logger.debug("[startup] retrieval prewarm falhou: %s", e)

        # Dispara como task sem aguardar
        asyncio.create_task(_prewarm_retrieval())
    except Exception as e:
        logger.debug("[startup] prewarm setup pulado: %s", e)

    # Pressure governor: log inicial
    try:
        from ..runtime.pressure_governor import get_governor
        gov = get_governor()
        reading = gov.read(force=True)
        logger.info(
            "[startup] pressure inicial | level=%s ram=%.2fGB used=%.1f%%",
            reading.level.value, reading.available_gb, reading.used_pct,
        )
    except Exception as e:
        logger.warning("[startup] pressure governor pulado: %s", e)

    # Transição final
    if boot:
        if embedding_ok:
            boot.transition(RuntimeState.READY, "startup_complete")
        else:
            boot.transition(RuntimeState.DEGRADED, "embeddings_failed")

    logger.info("[startup] EDP v3.5 pronto | state=%s",
                boot.state.value if boot else "unknown")

    # ── Commit 6 (Renato, 06/06/2026): Background Loop ─────────────────────
    # Inicia scheduler async de jobs periódicos no lifespan. Jobs são
    # registrados modularmente por consumidores (Commits futuros: 3d, 8,
    # Memory Palace, sumarização autônoma). Loop respeita pressure_governor.
    # Sem jobs registrados ainda no Commit 6 — apenas a fundação está pronta.
    bgloop = None
    try:
        from ..runtime import get_background_loop
        bgloop = get_background_loop()
        await bgloop.start()
        logger.info("[startup] background_loop iniciado")
    except Exception as e:
        logger.warning("[startup] background_loop falhou ao iniciar: %s", e)

    # ── Commit 3d (Renato, 06/06/2026): Cognitive Decisions Job ───────────
    # Registra job que extrai decisions estruturadas para entries cognitive
    # em background. Roda 60s após entry ser gravada. Não afeta hot path.
    # Env var EDP_COGNITIVE_DECISIONS_ENABLED=false desliga sem precisar
    # mudar código.
    if bgloop is not None and session_ok:
        try:
            from ..runtime import get_runtime, make_extraction_job
            runtime = get_runtime("default")
            job = make_extraction_job(runtime, interval_sec=60.0)
            bgloop.register_job(job)
            logger.info("[startup] cognitive_decisions job registrado")
        except Exception as e:
            logger.warning("[startup] falha ao registrar cognitive_decisions: %s", e)

    # ── Commit C (Sessão 3, 09/06/2026): Cognitive Health Index ───────────
    # Combina Gauss + Bayes + CoOccurrence + cognitive_decisions em score
    # de saúde cognitiva. Roda em background com cooldown 15min.
    # Env var EDP_HEALTH_INDEX=false desliga sem mudar código.
    if bgloop is not None and session_ok:
        try:
            from ..runtime.health_index import make_health_job, is_health_index_enabled
            if is_health_index_enabled():
                chi_job = make_health_job(
                    session_id="default",
                    cooldown_sec=900.0,   # 15min
                    interval_sec=60.0,    # tick a cada 1min (cooldown filtra)
                )
                bgloop.register_job(chi_job)
                logger.info("[startup] cognitive_health_index job registrado")
            else:
                logger.info("[startup] CHI desabilitado via EDP_HEALTH_INDEX=false")
        except Exception as e:
            logger.warning("[startup] falha ao registrar CHI: %s", e)

        # ── Sprint #43 (11/06/2026): consolidação automática ─────────────
        # Promove entries episódicas com acessos>=3 para a semântica do
        # scope ativo a cada 30min. Automatiza o caminho do endpoint
        # /memory/consolidate — o único que jamais populou a semântica.
        try:
            from ..runtime.auto_consolidation import (
                make_auto_consolidation_job, is_auto_consolidate_enabled,
            )
            if is_auto_consolidate_enabled():
                consol_job = make_auto_consolidation_job(
                    session_id="default",
                    interval_sec=60.0,    # tick 1min (cooldown 30min filtra)
                )
                bgloop.register_job(consol_job)
                logger.info("[startup] auto_consolidation job registrado")
            else:
                logger.info(
                    "[startup] auto_consolidation desabilitado via "
                    "EDP_AUTO_CONSOLIDATE=false"
                )
        except Exception as e:
            logger.warning("[startup] falha ao registrar auto_consolidation: %s", e)

    yield

    logger.info("[shutdown] EDP v3.5 encerrando...")

    # ── Commit 6: shutdown limpo do background loop ──────────────────────
    if bgloop is not None:
        try:
            await bgloop.stop()
            logger.info("[shutdown] background_loop parado")
        except Exception as e:
            logger.warning("[shutdown] erro ao parar background_loop: %s", e)

    if boot:
        boot.transition(RuntimeState.SHUTDOWN, "lifespan_exit")


# ── App ──────────────────────────────────────────────────────────────────────
try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse, HTMLResponse

    app = FastAPI(
        title="EDP v3.5 - Cognitive Runtime Infrastructure",
        description="Camada cognitiva persistente entre usuário e LLM",
        version="3.5.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Registra routers modulares ────────────────────────────────────────────
    from .routes import (
        health, memory, metrics, llm, websocket, dashboard_state, providers, flags,
        mode,  # Peça 2.6a (2026-05-30): endpoint /mode para modo bimodal
        cognitive_decisions, lineage,  # α (Tier 3, 13/06/2026): leitura REST
    )

    app.include_router(health.router)
    app.include_router(memory.router)
    app.include_router(metrics.router)
    app.include_router(llm.router)
    app.include_router(websocket.router)
    app.include_router(dashboard_state.router)
    app.include_router(providers.router)
    app.include_router(flags.router)
    app.include_router(mode.router)  # Peça 2.6a
    app.include_router(cognitive_decisions.router)  # α
    app.include_router(lineage.router)              # α

    # ── Dashboard estático ────────────────────────────────────────────────────
    _DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"
    _STATIC_DIR    = _DASHBOARD_DIR / "static"
    _TEMPLATES_DIR = _DASHBOARD_DIR / "templates"

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
        logger.info("[main] static dir montado: %s", _STATIC_DIR)

    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "service": "EDP v3.5 - Cognitive Runtime Infrastructure",
            "dashboard": "/dashboard",
            "docs": "/docs",
            "health": "/health",
        }

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard_page():
        """Serve o dashboard HTML (que carrega .css e .js via /static)."""
        html_file = _TEMPLATES_DIR / "dashboard.html"
        if not html_file.exists():
            return HTMLResponse(
                f"<h1>Dashboard not found</h1><p>Expected at: {html_file}</p>",
                status_code=404,
            )
        # Injeta sidebar compartilhada
        try:
            from .routes._sidebar import SIDEBAR_CSS, SIDEBAR_JS, render_sidebar
            html = html_file.read_text(encoding="utf-8")
            html = (
                html
                .replace("<!--SIDEBAR_CSS-->", SIDEBAR_CSS)
                .replace("<!--SIDEBAR_HTML-->", render_sidebar(current_page="dashboard"))
                .replace("<!--SIDEBAR_JS-->", SIDEBAR_JS)
            )
            return HTMLResponse(html)
        except Exception as e:
            logger.warning(f"[dashboard] falha ao injetar sidebar: {e}")
            return FileResponse(str(html_file), media_type="text/html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return HTMLResponse("", status_code=204)

    logger.info("[main] EDP v3.5 modular app pronto")

except ImportError as e:
    logger.error("[main] FastAPI indisponível: %s", e)
    app = None
