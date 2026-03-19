"""Agent inbox — Redis stream-based message queue.

Each agent has an inbox stream: stourio:inbox:{agent_name}
Messages are enqueued by other agents (send_message tool),
the orchestrator (routing to daemons), or cron/webhooks.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from src.persistence.redis_store import get_redis, notify_inbox
import redis

logger = logging.getLogger("stourio.daemons.inbox")

INBOX_PREFIX = "stourio:inbox:"
INBOX_GROUP_PREFIX = "stourio:inbox_group:"
MAX_MESSAGE_SIZE = 10000


async def init_inbox_group(agent_name: str) -> None:
    """Ensure consumer group exists for an agent's inbox stream."""
    r = await get_redis()
    stream = f"{INBOX_PREFIX}{agent_name}"
    group = f"{INBOX_GROUP_PREFIX}{agent_name}"
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except redis.exceptions.ResponseError as e:
        if "already exists" not in str(e).lower():
            raise


async def enqueue_message(
    target_agent: str,
    message: str,
    from_agent: str = "system",
    context: str = "",
    conversation_id: str | None = None,
) -> str | None:
    """Add a message to an agent's inbox. Returns stream entry ID or None if rejected."""
    if len(message) > MAX_MESSAGE_SIZE:
        logger.warning("Message to '%s' rejected: %d chars exceeds max %d", target_agent, len(message), MAX_MESSAGE_SIZE)
        return None

    r = await get_redis()
    stream = f"{INBOX_PREFIX}{target_agent}"

    payload = json.dumps({
        "from_agent": from_agent,
        "message": message,
        "context": context,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "conversation_id": conversation_id,
    })

    entry_id = await r.xadd(stream, {"data": payload})
    logger.info("Inbox message enqueued: %s -> %s (id=%s)", from_agent, target_agent, entry_id)

    # Wake daemon if running
    await notify_inbox(target_agent)

    return entry_id


async def dequeue_messages(
    agent_name: str,
    count: int = 10,
    consumer_name: str = "daemon",
) -> list[tuple[str, dict]]:
    """Read pending messages from an agent's inbox."""
    r = await get_redis()
    stream = f"{INBOX_PREFIX}{agent_name}"
    group = f"{INBOX_GROUP_PREFIX}{agent_name}"

    entries = await r.xreadgroup(group, consumer_name, {stream: ">"}, count=count)

    results = []
    if entries:
        for _, messages in entries:
            for message_id, data in messages:
                results.append((message_id, json.loads(data["data"])))
    return results


async def ack_message(agent_name: str, message_id: str) -> None:
    """Acknowledge a processed inbox message."""
    r = await get_redis()
    stream = f"{INBOX_PREFIX}{agent_name}"
    group = f"{INBOX_GROUP_PREFIX}{agent_name}"
    await r.xack(stream, group, message_id)
    await r.xdel(stream, message_id)
