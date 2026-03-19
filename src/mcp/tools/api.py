"""HTTP API call tool for external service integration."""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("stourio.tools.api")

ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
MAX_RESPONSE_BYTES = 1_000_000  # 1 MB

BLOCKED_HOSTS = {"localhost", "postgres", "redis", "jaeger", "0.0.0.0", "127.0.0.1"}
BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
]


def _is_url_safe(url: str) -> bool:
    """Block requests to internal/private network addresses."""
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    if hostname in BLOCKED_HOSTS:
        return False

    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return False
        for network in BLOCKED_NETWORKS:
            if ip in network:
                return False
    except ValueError:
        pass  # DNS name, not IP — allow (DNS resolution happens at request time)

    return True


async def call_api(arguments: dict) -> dict:
    """Make an HTTP request to an external API."""
    method = arguments.get("method", "GET").upper()
    url = arguments["url"]
    headers = arguments.get("headers", {})
    body = arguments.get("body")
    timeout = arguments.get("timeout", 30)

    if method not in ALLOWED_METHODS:
        return {"error": f"Method not allowed: {method}"}

    if not _is_url_safe(url):
        return {"error": f"URL blocked: {url} targets an internal or private network address"}

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
