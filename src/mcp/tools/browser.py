"""Browser automation MCP tool.

Pages persist across sequential calls within an agent execution via
a session_id parameter. This allows multi-step workflows:
navigate -> click -> type -> screenshot.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import time
from urllib.parse import urlparse

from src.config import settings

logger = logging.getLogger("stourio.tools.browser")

# Page cache keyed by session_id — allows multi-step browser workflows
_page_cache: dict[str, object] = {}
_page_timestamps: dict[str, float] = {}
MAX_SESSION_AGE = 600  # 10 minutes
MAX_SESSIONS = 20


def _is_url_safe_network(url: str) -> bool:
    """Block navigation to internal/private network addresses.

    Resolves DNS names to IPs before checking to prevent DNS rebinding attacks.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    blocked_hosts = {"localhost", "postgres", "redis", "jaeger", "0.0.0.0", "127.0.0.1"}
    if hostname in blocked_hosts:
        return False
    # Resolve DNS to IP and check resolved address
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in results:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    logger.warning("SSRF blocked: %s resolves to private IP %s", hostname, ip_str)
                    return False
            except ValueError:
                pass
    except socket.gaierror:
        pass
    return True


def _is_domain_allowed(url: str) -> bool:
    """Check if the URL's domain is in the allowed list.

    If browser_allowed_domains is empty, all domains are allowed.
    """
    if not settings.browser_allowed_domains:
        return True
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in settings.browser_allowed_domains
    )


async def browser_action(arguments: dict) -> dict:
    """Execute a browser action.

    Args (via arguments dict):
        action: One of navigate, click, type, screenshot, extract_text, get_url, close_session
        session_id: Reuse an existing page session for multi-step workflows.
                    If omitted, creates a new page (kept open for reuse via returned session_id).
        url: URL to navigate to (required for navigate action)
        selector: CSS selector (required for click, type, extract_text)
        text: Text to type (required for type action)
        full_page: Whether to screenshot full page (optional for screenshot)
        max_length: Max text length for extract_text (default 10000)

    Returns:
        dict with action result or error. Includes session_id for page reuse.
    """
    action = arguments.get("action")
    if not action:
        return {"error": "Missing required parameter: action"}

    session_id = arguments.get("session_id")

    # Handle close_session action
    if action == "close_session":
        if session_id and session_id in _page_cache:
            page = _page_cache.pop(session_id)
            _page_timestamps.pop(session_id, None)
            try:
                await page.close()
            except Exception:
                pass
            return {"status": "ok", "action": "close_session", "session_id": session_id}
        return {"status": "ok", "action": "close_session", "detail": "no active session"}

    # Domain allowlist check for navigate
    url = arguments.get("url")
    if action == "navigate" and url:
        if not _is_domain_allowed(url):
            return {
                "error": f"URL '{url}' is not in allowed domains. "
                         f"Allowed: {settings.browser_allowed_domains}",
            }
        if not _is_url_safe_network(url):
            return {"error": f"URL blocked: '{url}' targets an internal network address"}

    try:
        from src.browser.engine import get_browser_pool
        from src.browser.actions import execute_action
        from src.models.schemas import new_id

        pool = await get_browser_pool()

        # Evict stale sessions
        now = time.time()
        stale = [sid for sid, ts in _page_timestamps.items() if now - ts > MAX_SESSION_AGE]
        for sid in stale:
            if sid in _page_cache:
                try:
                    await _page_cache.pop(sid).close()
                except Exception:
                    pass
                _page_timestamps.pop(sid, None)

        # Enforce max sessions
        if len(_page_cache) >= MAX_SESSIONS and session_id not in _page_cache:
            return {"error": f"Maximum browser sessions ({MAX_SESSIONS}) reached. Close existing sessions first."}

        # Reuse existing page or create new one
        page = None
        if session_id and session_id in _page_cache:
            page = _page_cache[session_id]
        else:
            page = await pool.get_page()
            if not session_id:
                session_id = new_id()
            _page_cache[session_id] = page
            _page_timestamps[session_id] = time.time()

        result = await execute_action(page, action, arguments)
        result["session_id"] = session_id
        return result

    except Exception as e:
        logger.error("browser_action failed: %s", e)
        # Clean up failed session
        if session_id and session_id in _page_cache:
            try:
                await _page_cache.pop(session_id).close()
            except Exception:
                pass
            _page_timestamps.pop(session_id, None)
        return {"error": f"Browser action failed: {str(e)}"}
