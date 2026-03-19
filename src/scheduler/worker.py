"""Background worker that fires cron jobs on schedule.

Calls the agent pool directly (not the orchestrator) because the cron job
already specifies the target agent_type — no LLM routing needed.
"""
from __future__ import annotations

import logging

from src.orchestrator.concurrency import get_pool
from src.persistence import audit
from src.scheduler.store import CronStore

logger = logging.getLogger("stourio.scheduler")


async def scheduler_tick(store: CronStore, session_factory) -> int:
    """Check for due jobs and execute them via the agent pool.

    Bypasses the orchestrator's LLM routing since the cron job already
    specifies which agent should run.

    Returns the number of jobs fired.
    """
    due_jobs = await store.get_due_jobs()
    fired = 0

    for job in due_jobs:
        try:
            await audit.log(
                "CRON_FIRED",
                f"Cron job '{job.name}' fired -> agent '{job.agent_type}': {job.objective[:200]}",
            )

            async with session_factory() as exec_session:
                execution = await get_pool().execute(
                    agent_type=job.agent_type,
                    objective=job.objective,
                    context=f"Triggered by cron job '{job.name}' (schedule: {job.schedule})",
                    session=exec_session,
                    conversation_id=job.conversation_id,
                )

            await audit.log(
                "CRON_COMPLETED",
                f"Cron job '{job.name}' completed: status={execution.status.value}",
            )

            # Deliver result via Telegram
            if execution.result:
                try:
                    from src.mcp.tools.notification import get_telegram_client, get_allowed_user_ids
                    tg = get_telegram_client()
                    if tg:
                        for cid in get_allowed_user_ids():
                            await tg.send_message(
                                chat_id=cid,
                                text=f"[Cron: {job.name}]\n\n{execution.result}",
                            )
                except Exception as e:
                    logger.warning("Failed to deliver cron result via Telegram: %s", e)

            await store.mark_executed(job)
            fired += 1

        except Exception as e:
            logger.error("Cron job '%s' failed: %s", job.name, e)
            await audit.log(
                "CRON_FAILED",
                f"Cron job '{job.name}' failed: {e}",
            )

    return fired


async def run_scheduler_loop(session_factory, tick_seconds: int = 30):
    """Long-running loop that ticks the scheduler."""
    import asyncio

    logger.info("Scheduler worker started (tick=%ds)", tick_seconds)

    while True:
        try:
            async with session_factory() as session:
                store = CronStore(session)
                fired = await scheduler_tick(store, session_factory)
                if fired:
                    logger.info("Scheduler tick: %d job(s) fired", fired)
        except asyncio.CancelledError:
            logger.info("Scheduler worker cancelled.")
            break
        except Exception as e:
            logger.error("Scheduler worker error: %s", e)

        await asyncio.sleep(tick_seconds)
