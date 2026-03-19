"""HTTP API call tool for external service integration."""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("stourio.tools.api")

ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
MAX_RESPONSE_BYTES = 1_000_000  # 1 MB

BLOCKED_HOSTS = {"localhost", "postgres", "redis", "jaeger", "0.0.0.0", "127.0.0.1"}


def _is_ip_safe(ip_str: str) -> bool:
    """Check if an IP address is safe (not private/internal)."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)
    except ValueError:
        return True


def _is_url_safe(url: str) -> bool:
    """Block requests to internal/private network addresses.

    Resolves DNS names to IPs before checking to prevent DNS rebinding attacks.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    if hostname in BLOCKED_HOSTS:
        return False

    # Resolve DNS to IP and check the resolved address
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in results:
            ip_str = sockaddr[0]
            if not _is_ip_safe(ip_str):
                logger.warning("SSRF blocked: %s resolves to private IP %s", hostname, ip_str)
                return False
    except socket.gaierror:
        pass  # DNS resolution failed — let httpx handle the error

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
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
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
