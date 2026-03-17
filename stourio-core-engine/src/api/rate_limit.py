"""
Per-IP rate limiter middleware using Redis.
Prevents abuse of LLM-backed endpoints and webhook flooding.
"""
from __future__ import annotations
import logging
import time
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
}
DEFAULT_LIMIT = 60  # requests per minute


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
            # If Redis is down, allow the request (fail open for availability)
            logger.error(f"Rate limiter error: {e}. Allowing request.")

        return await call_next(request)
