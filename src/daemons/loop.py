"""Single daemon loop — one cycle of heartbeat check + inbox processing.

The daemon manager (manager.py) calls run_daemon_loop() which loops forever.
Each iteration calls run_daemon_cycle() which is one heartbeat tick.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timezone

from src.daemons.inbox import dequeue_messages, ack_message, init_inbox_group
from src.orchestrator.concurrency import get_pool
from src.persistence import audit
from src.persistence.database import async_session
from src.persistence.redis_store import get_pubsub_connection, INBOX_NOTIFY_PREFIX

logger = logging.getLogger("stourio.daemons.loop")


def is_in_active_hours(active_hours: dict | None) -> bool:
    """Check if current time is within active hours window."""
    if active_hours is None:
        return True

    now = datetime.now(timezone.utc).time()
    start = time.fromisoformat(active_hours["start"])
    end = time.fromisoformat(active_hours["end"])

    if start <= end:
        return start <= now <= end
    else:
        # Overnight window (e.g., 22:00 - 06:00)
        return now >= start or now <= end


async def run_daemon_cycle(
    agent_name: str,
    heartbeat_prompt: str,
    max_messages: int = 10,
    inbox_messages: list | None = None,
) -> dict:
    """Execute one daemon cycle.

    Returns dict with: result, suppressed, messages_processed, execution_id, status
    """
    # Read inbox if not provided
    if inbox_messages is None:
        inbox_messages = await dequeue_messages(agent_name, count=max_messages)

    # Build objective
    if inbox_messages:
        msg_texts = []
        for msg_id, data in inbox_messages:
            msg_texts.append(f"[From {data.get('from_agent', 'unknown')}]: {data.get('message', '')}")
        objective = f"You have {len(inbox_messages)} new message(s):\n" + "\n".join(msg_texts) + f"\n\n{heartbeat_prompt}"
    else:
        objective = heartbeat_prompt

    # Execute one agent cycle
    conversation_id = f"daemon:{agent_name}"

    async with async_session() as session:
        execution = await get_pool().execute(
            agent_type=agent_name,
            objective=objective,
            context=f"Daemon cycle for '{agent_name}'",
            session=session,
            conversation_id=conversation_id,
        )

    # Check if heartbeat_ack was called
    suppressed = any(
        step.get("tool") == "heartbeat_ack"
        for step in (execution.steps or [])
        if step.get("type") == "tool_call"
    )

    if suppressed:
        await audit.log("DAEMON_HEARTBEAT", f"Daemon '{agent_name}' heartbeat OK (suppressed)")
    else:
        await audit.log("DAEMON_CYCLE", f"Daemon '{agent_name}' cycle completed: {(execution.result or '')[:200]}")

    # Ack processed inbox messages
    for msg_id, _ in inbox_messages:
        await ack_message(agent_name, msg_id)

    return {
        "result": execution.result,
        "suppressed": suppressed,
        "messages_processed": len(inbox_messages),
        "execution_id": execution.id,
        "status": execution.status.value,
    }


async def run_daemon_loop(
    agent_name: str,
    daemon_config: dict,
    stopping: asyncio.Event,
    on_cycle_complete: callable | None = None,
) -> None:
    """Run the daemon loop indefinitely until stopping is set.

    Wakes on: inbox pub/sub notification OR tick_seconds timeout.
    on_cycle_complete is called after each cycle for health monitoring.
    """
    tick_seconds = daemon_config.get("tick_seconds", 300)
    heartbeat_prompt = daemon_config.get("heartbeat_prompt", "Check inbox. If nothing needs attention, call heartbeat_ack.")
    active_hours = daemon_config.get("active_hours")
    max_messages = daemon_config.get("max_messages_per_cycle", 10)

    logger.info("Daemon loop started: %s (tick=%ds)", agent_name, tick_seconds)

    # Init inbox consumer group
    await init_inbox_group(agent_name)

    # Subscribe to inbox notifications
    pubsub = await get_pubsub_connection()
    channel = f"{INBOX_NOTIFY_PREFIX}{agent_name}"
    await pubsub.subscribe(channel)

    async def _wait_for_inbox_message():
        """Poll pub/sub until an actual message arrives."""
        while not stopping.is_set():
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is not None:
                return msg
            await asyncio.sleep(0.1)
        return None

    try:
        while not stopping.is_set():
            # Wait for inbox message OR tick timeout (whichever first)
            try:
                msg = await asyncio.wait_for(
                    _wait_for_inbox_message(),
                    timeout=tick_seconds,
                )
            except asyncio.TimeoutError:
                msg = None  # Tick timeout — run heartbeat

            if stopping.is_set():
                break

            # Check active hours
            if not is_in_active_hours(active_hours):
                await asyncio.sleep(60)
                continue

            # Run one cycle
            try:
                result = await run_daemon_cycle(
                    agent_name=agent_name,
                    heartbeat_prompt=heartbeat_prompt,
                    max_messages=max_messages,
                )

                # Health monitoring callback
                if on_cycle_complete:
                    on_cycle_complete(agent_name)

                if not result["suppressed"] and result["result"]:
                    # Deliver to user via Telegram
                    try:
                        from src.mcp.tools.notification import _send_telegram
                        await _send_telegram(
                            f"[Daemon: {agent_name}]\n\n{result['result']}",
                            severity="info",
                        )
                    except Exception as e:
                        logger.warning("Failed to deliver daemon output: %s", e)

            except Exception as e:
                logger.error("Daemon '%s' cycle failed: %s", agent_name, e)
                await audit.log("DAEMON_ERROR", f"Daemon '{agent_name}' cycle error: {e}")

    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        logger.info("Daemon loop stopped: %s", agent_name)
