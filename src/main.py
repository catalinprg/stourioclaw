import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router
from src.api.rate_limit import RateLimitMiddleware
from src.persistence.database import init_db, async_session
from src.rules.engine import seed_default_rules
from src.mcp.registry import init_registry
from src.config import settings
from src.persistence import redis_store
from src.orchestrator import core as orchestrator_module
from src.scheduler.worker import run_scheduler_loop
from src.browser.engine import shutdown_browser_pool
from src.models.schemas import OrchestratorInput, SignalSource, WebhookSignal
from src.telemetry import setup_tracing

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s | %(name)-24s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("stourio")


def _validate_env():
    """Check required env vars on startup. Fail loud, not silent."""
    missing = []
    if not settings.openrouter_api_key:
        missing.append("OPENROUTER_API_KEY")
    if not settings.telegram_bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not settings.telegram_allowed_user_ids:
        missing.append("TELEGRAM_ALLOWED_USER_IDS")
    if not settings.stourio_api_key:
        missing.append("STOURIO_API_KEY")

    warnings = []
    if not settings.openai_api_key:
        warnings.append("OPENAI_API_KEY (needed for embeddings + voice transcription)")
    if not settings.search_api_key:
        warnings.append("SEARCH_API_KEY (needed for web_search tool)")
    if not settings.telegram_webhook_url and not settings.telegram_use_polling:
        warnings.append("TELEGRAM_WEBHOOK_URL (required unless TELEGRAM_USE_POLLING=true)")

    if missing:
        msg = (
            "\n\n"
            "===== MISSING REQUIRED ENVIRONMENT VARIABLES =====\n"
            + "\n".join(f"  - {var}" for var in missing)
            + "\n\nCopy .env.example to .env and fill in your keys:\n"
            "  cp .env.example .env\n"
            "===================================================\n"
        )
        logger.critical(msg)
        raise SystemExit(1)

    for w in warnings:
        logger.warning("Optional env var not set: %s", w)


_validate_env()


async def signal_consumer_worker():
    """Background worker to dequeue and process signals reliably."""
    logger.info("Signal consumer worker started.")
    while True:
        try:
            # Use reliable consumer group dequeue
            entries = await redis_store.dequeue_signals_reliable(consumer_name="worker-primary")
            if not entries:
                await asyncio.sleep(1)
                continue

            for msg_id, raw_sig in entries:
                sig_model = WebhookSignal(**raw_sig)
                content = f"[{sig_model.source.upper()}] {sig_model.event_type}: {sig_model.title}"
                if sig_model.payload:
                    content += f"\nPayload: {sig_model.payload}"

                orchestrator_input = OrchestratorInput(
                    source=SignalSource.SYSTEM,
                    content=content,
                    raw_signal=sig_model,
                )

                # Process signal through orchestrator
                await orchestrator_module.process(orchestrator_input)

                # Acknowledge ONLY after successful processing to prevent signal loss
                await redis_store.ack_signal(msg_id)

        except asyncio.CancelledError:
            logger.info("Signal consumer worker cancelled.")
            break
        except Exception as e:
            logger.error(f"Consumer worker error: {e}")
            await asyncio.sleep(5)


async def approval_escalation_worker():
    """Background task to check for stalling approvals and escalate."""
    import json
    import time
    from src.persistence.redis_store import get_redis
    from src.notifications.dispatcher import get_dispatcher
    from src.models.schemas import Notification

    redis = await get_redis()
    while True:
        try:
            keys = []
            async for key in redis.scan_iter("stourio:approval_escalation:*"):
                keys.append(key)
            for key in keys:
                data = await redis.get(key)
                if not data:
                    continue
                info = json.loads(data)
                if info.get("notified"):
                    continue
                if time.time() >= info.get("escalation_time", 0):
                    dispatcher = get_dispatcher()
                    await dispatcher.send(Notification(
                        channel=info.get("channel", "oncall-slack"),
                        message=f"Approval stalling: {info.get('action', 'unknown')}. Respond urgently.",
                        severity="critical",
                        context={"approval_id": info.get("approval_id")},
                    ))
                    info["notified"] = True
                    ttl = await redis.ttl(key)
                    if ttl > 0:
                        await redis.setex(key, ttl, json.dumps(info))
        except Exception as e:
            logger.error(f"Escalation worker error: {e}")
        await asyncio.sleep(10)


async def security_auditor_worker():
    """Periodic background worker that scans audit logs for anomalies."""
    from src.security.auditor import SecurityAuditor
    from src.mcp.tools.audit import read_audit_log

    interval = settings.security_audit_interval_seconds
    logger.info("Security auditor worker started (interval=%ds)", interval)

    while True:
        try:
            await asyncio.sleep(interval)
            # Fetch recent audit entries via the audit tool
            result = await read_audit_log({"hours": 1, "limit": 200})
            entries = result.get("entries", [])
            if not entries:
                continue

            async with async_session() as session:
                auditor = SecurityAuditor(session=session, interval_seconds=interval)
                alerts = await auditor.analyze_recent_activity(entries)
                if alerts:
                    await auditor.save_alerts(alerts)
                    logger.warning("Security auditor raised %d alert(s)", len(alerts))
        except asyncio.CancelledError:
            logger.info("Security auditor worker cancelled.")
            break
        except Exception as e:
            logger.error(f"Security auditor worker error: {e}")
            await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    # 1. Database
    await init_db()
    await seed_default_rules()

    # 2. Redis consumer group
    await redis_store.init_consumer_group()

    # 3. Plugin system + MCP tool registry
    init_registry()

    from src.mcp.tools import register_all_tools
    register_all_tools()

    # 4. Agent seed from YAML
    from src.agents.registry import AgentRegistry
    async with async_session() as session:
        registry = AgentRegistry(session)
        count = await registry.seed_from_yaml("config/agents")
        await session.commit()
    if count:
        logger.info("Seeded %d agent(s) from YAML", count)

    # 5. Embeddings + RAG retriever
    from src.rag.embeddings.openai_embedder import OpenAIEmbedder
    from src.rag.reranker.cohere_reranker import CohereReranker
    from src.rag.retriever import Retriever
    from src.mcp.tools.knowledge import set_retriever

    embedder = OpenAIEmbedder(api_key=settings.openai_api_key, model=settings.embedding_model)
    assert embedder.dimension == settings.embedding_dimension, "Embedder dimension mismatch"
    reranker = None
    if settings.reranker_provider == "cohere" and settings.cohere_api_key:
        reranker = CohereReranker(api_key=settings.cohere_api_key)
    retriever = Retriever(embedder=embedder, reranker=reranker)
    set_retriever(retriever)

    # 6. Wire placeholder tools
    from src.mcp.tools.audit import set_session_factory
    from src.mcp.tools.notification import set_telegram_client
    set_session_factory(async_session)

    # 8. Telegram client init + webhook registration
    from src.telegram.client import TelegramClient
    from src.telegram.webhook import init_telegram_handler

    telegram_client = None
    if settings.telegram_bot_token:
        telegram_client = TelegramClient(token=settings.telegram_bot_token)
        init_telegram_handler(orchestrator_module, telegram_client)
        set_telegram_client(telegram_client, settings.telegram_allowed_user_ids)
        if not settings.telegram_use_polling:
            await telegram_client.set_webhook(
                settings.telegram_webhook_url, settings.telegram_webhook_secret
            )
        logger.info("Telegram bot initialized (polling=%s)", settings.telegram_use_polling)

    # Startup banner
    logger.info("=" * 60)
    logger.info("STOURIO - Operational Intelligence Framework")
    logger.info(f"Orchestrator model: {settings.orchestrator_model}")
    logger.info(f"LLM gateway: OpenRouter (default: {settings.openrouter_default_model})")
    logger.info("=" * 60)

    if not settings.stourio_api_key:
        logger.warning("!" * 60)
        logger.warning("STOURIO_API_KEY is not set. ALL API requests will be rejected.")
        logger.warning("Run: python3 scripts/generate_key.py")
        logger.warning("!" * 60)

    # 9. Background workers
    consumer_task = asyncio.create_task(signal_consumer_worker())
    escalation_task = asyncio.create_task(approval_escalation_worker())
    auditor_task = asyncio.create_task(security_auditor_worker())
    scheduler_task = asyncio.create_task(
        run_scheduler_loop(async_session, settings.scheduler_tick_seconds)
    )

    logger.info("Ready.")
    yield
    logger.info("Shutting down.")

    consumer_task.cancel()
    escalation_task.cancel()
    auditor_task.cancel()
    scheduler_task.cancel()
    for task in (consumer_task, escalation_task, auditor_task, scheduler_task):
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Cleanup browser pool
    await shutdown_browser_pool()

    # Cleanup Telegram client
    if telegram_client:
        await telegram_client.close()


app = FastAPI(
    title="Stourio",
    description="Operational Intelligence Framework - LLM-agnostic orchestration for autonomous operations",
    version="0.1.0",
    lifespan=lifespan,
)
setup_tracing(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(",") if settings.cors_origins else [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-STOURIO-KEY"],
)
app.add_middleware(RateLimitMiddleware)

app.include_router(router, prefix="/api")

# Mount new routers
from src.telegram.webhook import telegram_router
from src.mcp.router import mcp_router
app.include_router(telegram_router)
app.include_router(mcp_router)

from fastapi.staticfiles import StaticFiles
import os

# Create static directory if it doesn't exist
os.makedirs("static", exist_ok=True)

# Mount the static directory to serve the SPA at /admin
app.mount("/admin", StaticFiles(directory="static", html=True), name="admin")

@app.get("/")
async def root():
    return {
        "name": "Stourio",
        "version": "0.1.0",
        "docs": "/docs",
        "status": "/api/status",
    }
