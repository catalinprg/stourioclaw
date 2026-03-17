from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Any

import redis.asyncio as aioredis  # Rename alias to avoid conflict
import redis                      # Import top-level redis for exceptions
from src.config import settings

logger = logging.getLogger("stourio.redis")

_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(settings.redis_url, decode_responses=True)
        logger.info("Redis connected")
    return _pool


# --- Kill Switch ---

async def activate_kill_switch() -> None:
    r = await get_redis()
    await r.set(settings.kill_switch_key, "1")
    logger.warning("KILL SWITCH ACTIVATED")


async def deactivate_kill_switch() -> None:
    r = await get_redis()
    await r.delete(settings.kill_switch_key)
    logger.info("Kill switch deactivated")


async def is_killed() -> bool:
    r = await get_redis()
    return await r.exists(settings.kill_switch_key) == 1


# --- Reliable Signal Queue (Consumer Groups) ---

SIGNAL_STREAM = "stourio:signals"
SIGNAL_GROUP = "stourio:consumer_group"


async def init_consumer_group():
    """Ensure the consumer group exists for reliable processing."""
    r = await get_redis()
    try:
        await r.xgroup_create(SIGNAL_STREAM, SIGNAL_GROUP, id="0", mkstream=True)
        logger.info(f"Redis consumer group '{SIGNAL_GROUP}' initialized")
    except redis.exceptions.ResponseError as e:  # Use top-level redis package
        if "already exists" not in str(e).lower():
            raise


async def enqueue_signal(signal: dict[str, Any]) -> str:
    """Push a signal into the Redis stream. Returns the stream entry ID."""
    r = await get_redis()
    entry_id = await r.xadd(SIGNAL_STREAM, {"data": json.dumps(signal)})
    return entry_id


async def dequeue_signals_reliable(consumer_name: str, count: int = 10) -> list[tuple[str, dict]]:
    """Read pending signals using consumer groups. Requires ACK to confirm completion."""
    r = await get_redis()
    # Read messages not yet delivered to other consumers in the group (">")
    entries = await r.xreadgroup(SIGNAL_GROUP, consumer_name, {SIGNAL_STREAM: ">"}, count=count)
    
    results = []
    if entries:
        for _, messages in entries:
            for message_id, data in messages:
                results.append((message_id, json.loads(data["data"])))
    return results


async def ack_signal(message_id: str):
    """Acknowledge and remove processed entries from the stream."""
    r = await get_redis()
    await r.xack(SIGNAL_STREAM, SIGNAL_GROUP, message_id)
    await r.xdel(SIGNAL_STREAM, message_id)

# ... existing code ...

# --- Distributed Locking with Fencing Tokens ---

LOCK_PREFIX = "stourio:lock:" # Added for consistency

async def acquire_lock(resource: str, ttl_seconds: int = 60) -> bool:
    """Standard lock acquisition used for heartbeats."""
    r = await get_redis()
    key = f"{LOCK_PREFIX}{resource}"
    acquired = await r.set(key, "locked", nx=True, ex=ttl_seconds)
    return bool(acquired)

async def extend_lock(resource: str, ttl_seconds: int = 30) -> bool:
    """Extend the TTL of an existing lock."""
    r = await get_redis()
    key = f"{LOCK_PREFIX}{resource}"
    return await r.expire(key, ttl_seconds)

async def acquire_lock_with_token(resource: str, ttl_seconds: int = 60) -> int | None:
    # ... (existing logic) ...
    r = await get_redis()
    key = f"{LOCK_PREFIX}{resource}"
    token = int(datetime.utcnow().timestamp() * 1000)
    acquired = await r.set(key, token, nx=True, ex=ttl_seconds)
    return token if acquired else None

async def validate_fencing_token(resource: str, token: int) -> bool:
    r = await get_redis()
    key = f"{LOCK_PREFIX}{resource}"
    current_token = await r.get(key)
    return current_token == str(token)

async def release_lock(resource: str) -> None:
    r = await get_redis()
    key = f"{LOCK_PREFIX}{resource}"
    await r.delete(key)
    logger.info(f"Lock released: {resource}")


# --- Approval Cache ---

APPROVAL_PREFIX = "stourio:approval:"


async def cache_approval(approval_id: str, data: dict, ttl: int | None = None) -> None:
    r = await get_redis()
    ttl = ttl or settings.approval_ttl_seconds
    await r.set(
        f"{APPROVAL_PREFIX}{approval_id}",
        json.dumps(data),
        ex=ttl,
    )


async def get_cached_approval(approval_id: str) -> dict | None:
    r = await get_redis()
    raw = await r.get(f"{APPROVAL_PREFIX}{approval_id}")
    if raw:
        return json.loads(raw)
    return None


async def delete_cached_approval(approval_id: str) -> None:
    r = await get_redis()
    await r.delete(f"{APPROVAL_PREFIX}{approval_id}")