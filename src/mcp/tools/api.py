"""HTTP API call tool for external service integration."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("stourio.tools.api")

ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
MAX_RESPONSE_BYTES = 1_000_000  # 1 MB


async def call_api(arguments: dict) -> dict:
    """Make an HTTP request to an external API."""
    method = arguments.get("method", "GET").upper()
    url = arguments["url"]
    headers = arguments.get("headers", {})
    body = arguments.get("body")
    timeout = arguments.get("timeout", 30)

    if method not in ALLOWED_METHODS:
        return {"error": f"Method not allowed: {method}"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            logger.info("call_api: %s %s", method, url)
            resp = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=body if body else None,
            )
            content = resp.text[:MAX_RESPONSE_BYTES]

        return {
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "body": content,
        }
    except httpx.TimeoutException:
        return {"error": f"Request timed out after {timeout}s", "status_code": -1}
    except Exception as exc:
        logger.exception("call_api failed")
        return {"error": str(exc), "status_code": -1}
