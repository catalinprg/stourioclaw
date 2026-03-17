import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router
from src.api.rate_limit import RateLimitMiddleware
from src.persistence.database import init_db
from src.rules.engine import seed_default_rules
from src.config import settings
from src.persistence import redis_store
from src.orchestrator.core import process
from src.models.schemas import OrchestratorInput, SignalSource, WebhookSignal
from src.telemetry import setup_tracing

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s | %(name)-24s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("stourio")


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
                await process(orchestrator_input)
                
                # Acknowledge ONLY after successful processing to prevent signal loss
                await redis_store.ack_signal(msg_id)
                
        except asyncio.CancelledError:
            logger.info("Signal consumer worker cancelled.")
            break
        except Exception as e:
            logger.error(f"Consumer worker error: {e}")
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    from src.agents.runtime import list_templates as _list_templates

    logger.info("=" * 60)
    logger.info("STOURIO - Operational Intelligence Framework")
    logger.info(f"Orchestrator: {settings.orchestrator_provider} / {settings.orchestrator_model}")
    logger.info(f"Agent fallback: {settings.agent_provider} / {settings.agent_model}")
    for _t in _list_templates():
        _p = _t.provider_override or settings.agent_provider
        _m = _t.model_override or settings.agent_model
        _src = "override" if _t.provider_override else "fallback"
        logger.info(f"  {_t.id}: {_p} / {_m} ({_src})")
    logger.info("=" * 60)

    if not settings.stourio_api_key:
        logger.warning("!" * 60)
        logger.warning("STOURIO_API_KEY is not set. ALL API requests will be rejected.")
        logger.warning("Run: python3 scripts/generate_key.py")
        logger.warning("!" * 60)


    await init_db()
    await seed_default_rules()
    
    # Initialize reliable messaging infrastructure
    await redis_store.init_consumer_group()

    consumer_task = asyncio.create_task(signal_consumer_worker())

    logger.info("Ready.")
    yield
    logger.info("Shutting down.")
    
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass


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