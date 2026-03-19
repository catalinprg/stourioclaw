"""Background worker that periodically re-scans the runbooks directory for changes."""
from __future__ import annotations

import asyncio
import logging

from src.config import settings

logger = logging.getLogger("stourio.rag.reindex")


async def run_reindex_loop(embedder, interval_seconds: int = 300) -> None:
    """Periodically re-scan runbooks directory and re-index changed files."""
    from src.rag.ingestion import ingest_runbooks

    logger.info("Document re-index worker started (interval=%ds)", interval_seconds)

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            count = await ingest_runbooks(embedder, settings.runbooks_dir)
            if count:
                logger.info("Re-indexed %d chunks from changed documents", count)
        except asyncio.CancelledError:
            logger.info("Document re-index worker cancelled.")
            break
        except Exception as e:
            logger.error("Re-index worker error: %s", e)
