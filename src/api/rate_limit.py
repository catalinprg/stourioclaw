"""
Per-IP rate limiter middleware using Redis.
Prevents abuse of LLM-backed endpoints and webhook flooding.
Falls back to in-memory counters when Redis is unavailable.
"""
from __future__ import annotations
import logging
import time
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from src.config import settings

logger = logging.getLogger("stourio.ratelimit")

# Limits per endpoint prefix (requests per minute)
RATE_LIMITS = {
    "/api/chat": 30,        # LLM cost exposure
    "/api/webhook": 120,    # System signals, higher volume
    "/api/kill": 5,         # Kill switch, low volume
    "/api/resume": 5,
    "/api/approvals": 60,
    "/api/rules": 30,
    "/api/audit": 30,
    "/api/status": 60,
    "/api/telegram/webhook": 10,  # Telegram webhook, low limit to prevent flooding
}
DEFAULT_LIMIT = 60  # requests per minute

# In-memory fallback: {window_key: count}
_fallback_counters: dict[str, int] = defaultdict(int)
_fallback_timestamps: dict[str, float] = {}
_FALLBACK_LIMIT = 30  # requests per minute when Redis is down


def _fallback_check(client_ip: str, path: str) -> bool:
    """Returns True if request is allowed, False if rate limited.

    Uses a simple fixed 1-minute window keyed by (IP, path, minute-bucket).
    """
    bucket = int(time.time()) // 60
    key = f"{client_ip}:{path}:{bucket}"

    # Evict stale buckets (older than 2 minutes) to prevent unbounded growth
    now_bucket = bucket
    stale = [k for k in list(_fallback_counters.keys())
             if int(k.rsplit(":", 1)[-1]) < now_bucket - 1]
    for k in stale:
        del _fallback_counters[k]
        _fallback_timestamps.pop(k, None)

    _fallback_counters[key] += 1
    return _fallback_counters[key] <= _FALLBACK_LIMIT


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for docs and root
        path = request.url.path
        if path in ("/", "/docs", "/openapi.json", "/redoc"):
            return await call_next(request)

        # Determine limit for this path
        limit = DEFAULT_LIMIT
        for prefix, lim in RATE_LIMITS.items():
            if path.startswith(prefix):
                limit = lim
                break

        client_ip = request.client.host if request.client else "unknown"
        window_key = f"stourio:ratelimit:{client_ip}:{path}:{int(time.time()) // 60}"

        try:
            from src.persistence.redis_store import get_redis
            r = await get_redis()
            current = await r.incr(window_key)
            if current == 1:
                await r.expire(window_key, 60)

            if current > limit:
                logger.warning(f"Rate limit exceeded: {client_ip} on {path} ({current}/{limit})")
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": f"Rate limit exceeded. Max {limit} requests/minute for this endpoint.",
                        "retry_after_seconds": 60,
                    },
                    headers={"Retry-After": "60"},
                )
        except Exception as e:
            # Redis is down — apply in-memory fallback rate limit
            logger.error(f"Rate limiter error: {e}. Applying in-memory fallback (limit={_FALLBACK_LIMIT}/min).")
            if not _fallback_check(client_ip, path):
                logger.warning(f"In-memory rate limit exceeded: {client_ip} on {path}")
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": f"Rate limit exceeded (fallback). Max {_FALLBACK_LIMIT} requests/minute.",
                        "retry_after_seconds": 60,
                    },
                    headers={"Retry-After": "60"},
                )

        return await call_next(request)
