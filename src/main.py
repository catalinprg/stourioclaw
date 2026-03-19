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
from src.daemons.manager import DaemonManager, set_daemon_manager
from src.mcp.client import get_mcp_client_pool

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s | %(name)-24s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("stourio")


def _auto_generate_api_key():
    """Auto-generate STOURIO_API_KEY if not set. Persists to .env file if possible."""
    import secrets
    import os

    if settings.stourio_api_key:
        return

    key = secrets.token_urlsafe(32)

    # Set in memory (Pydantic v2 model_config may block direct assignment)
    try:
        settings.stourio_api_key = key
    except Exception:
        # Pydantic frozen model — set via __dict__
        object.__setattr__(settings, 'stourio_api_key', key)

    # Also set as environment variable so it's available to the process
    os.environ["STOURIO_API_KEY"] = key

    # Try to persist to .env so it survives restarts
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    persisted = False
    try:
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                content = f.read()
            if "STOURIO_API_KEY=" in content:
                lines = content.splitlines()
                for i, line in enumerate(lines):
                    if line.startswith("STOURIO_API_KEY=") and not line.split("=", 1)[1].strip():
                        lines[i] = f"STOURIO_API_KEY={key}"
                        break
                with open(env_path, "w") as f:
                    f.write("\n".join(lines) + "\n")
                persisted = True
            else:
                with open(env_path, "a") as f:
                    f.write(f"\nSTOURIO_API_KEY={key}\n")
                persisted = True
        else:
            with open(env_path, "w") as f:
                f.write(f"STOURIO_API_KEY={key}\n")
            persisted = True
    except OSError as e:
        logger.warning("Could not persist STOURIO_API_KEY to .env: %s", e)

    logger.info("=" * 60)
    logger.info("AUTO-GENERATED STOURIO_API_KEY%s:", " (saved to .env)" if persisted else " (in-memory only, set STOURIO_API_KEY in .env to persist)")
    logger.info("  %s", key)
    logger.info("Use this key for admin panel login and API auth.")
    logger.info("=" * 60)


def _validate_env():
    """Check required env vars on startup. Fail loud, not silent."""
    _auto_generate_api_key()

    missing = []
    if not settings.openrouter_api_key:
        missing.append("OPENROUTER_API_KEY")
    if not settings.telegram_bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not settings.telegram_allowed_user_ids:
        missing.append("TELEGRAM_ALLOWED_USER_IDS")

    warnings = []
    if not settings.openai_api_key:
        warnings.append("OPENAI_API_KEY (needed for embeddings + voice transcription)")
    if not settings.search_api_key:
        warnings.append("SEARCH_API_KEY (needed for web_search tool)")
    if not settings.telegram_webhook_url and not settings.telegram_use_polling:
        warnings.append("TELEGRAM_WEBHOOK_URL (required unless TELEGRAM_USE_POLLING=true)")

    # Check for default passwords in production
    if not settings.telegram_use_polling:  # Production mode (webhook)
        if settings.postgres_password == "changeme":
            missing.append("POSTGRES_PASSWORD (still set to 'changeme')")
        if settings.redis_password == "changeme":
            missing.append("REDIS_PASSWORD (still set to 'changeme')")

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

    # 3b. Wire security interceptor
    from src.mcp.registry import tool_registry
    if settings.security_inline_enabled:
        from src.security.interceptor import SecurityInterceptor
        interceptor = SecurityInterceptor(enabled=True)
        tool_registry.set_interceptor(interceptor)
        logger.info("Security interceptor wired (inline enabled)")

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
    from src.rag.retriever import Retriever
    from src.mcp.tools.knowledge import set_retriever

    embedder = OpenAIEmbedder(api_key=settings.openai_api_key, model=settings.embedding_model)
    assert embedder.dimension == settings.embedding_dimension, "Embedder dimension mismatch"
    retriever = Retriever(embedder=embedder)
    set_retriever(retriever)

    # 5b. Document re-indexing worker
    from src.rag.reindex_worker import run_reindex_loop
    reindex_task = asyncio.create_task(
        run_reindex_loop(embedder, interval_seconds=settings.reindex_interval_seconds)
    )

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
        tool_registry._telegram_client = telegram_client
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

    # 9. Background workers
    consumer_task = asyncio.create_task(signal_consumer_worker())
    escalation_task = asyncio.create_task(approval_escalation_worker())
    auditor_task = asyncio.create_task(security_auditor_worker())
    scheduler_task = asyncio.create_task(
        run_scheduler_loop(async_session, settings.scheduler_tick_seconds)
    )

    # Periodic sandbox cleanup
    async def sandbox_cleanup_worker():
        from src.sandbox.session import SessionSandbox
        while True:
            try:
                await asyncio.sleep(3600)  # Every hour
                SessionSandbox.cleanup_stale_sessions(max_age_hours=24)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Sandbox cleanup error: %s", e)

    sandbox_cleanup_task = asyncio.create_task(sandbox_cleanup_worker())

    # 10. Daemon manager
    daemon_manager = None
    if settings.daemon_manager_enabled:
        daemon_manager = DaemonManager()
        set_daemon_manager(daemon_manager)
        await daemon_manager.start()

    # 11. MCP client pool — connect to registered servers
    mcp_pool = get_mcp_client_pool()
    async with async_session() as session:
        from src.persistence.database import McpServerRecord
        from sqlalchemy import select as sa_select
        result = await session.execute(
            sa_select(McpServerRecord).where(McpServerRecord.active == True)
        )
        for server in result.scalars().all():
            await mcp_pool.connect(server.name, {
                "transport": server.transport,
                "endpoint_url": server.endpoint_url,
                "endpoint_command": server.endpoint_command,
                "auth_env_var": server.auth_env_var,
            })

    logger.info("Ready.")
    yield
    logger.info("Shutting down.")

    consumer_task.cancel()
    escalation_task.cancel()
    auditor_task.cancel()
    scheduler_task.cancel()
    reindex_task.cancel()
    sandbox_cleanup_task.cancel()
    for task in (consumer_task, escalation_task, auditor_task, scheduler_task, reindex_task, sandbox_cleanup_task):
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Stop daemons gracefully
    if daemon_manager:
        await daemon_manager.stop()

    # Disconnect MCP clients
    await get_mcp_client_pool().disconnect_all()

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

origins = settings.cors_origins.split(",") if settings.cors_origins else []
if "*" in origins:
    logger.warning("CORS origin '*' with credentials=True is insecure. Replacing with empty origins.")
    origins = []
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
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
