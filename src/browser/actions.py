"""Browser action dispatcher — maps action names to Playwright calls."""
from __future__ import annotations

import base64
import logging
from playwright.async_api import Page

logger = logging.getLogger("stourio.browser.actions")


async def execute_action(page: Page, action: str, params: dict) -> dict:
    """Execute a named browser action on the given page.

    Supported actions: navigate, click, type, screenshot, extract_text, get_url.
    """
    handler = _ACTIONS.get(action)
    if handler is None:
        return {"error": f"Unknown browser action: '{action}'. Supported: {list(_ACTIONS.keys())}"}

    try:
        return await handler(page, params)
    except Exception as e:
        logger.error("Browser action '%s' failed: %s", action, e)
        return {"error": f"Action '{action}' failed: {str(e)}"}


async def _navigate(page: Page, params: dict) -> dict:
    url = params.get("url")
    if not url:
        return {"error": "Missing required parameter: url"}
    await page.goto(url, wait_until="domcontentloaded")
    title = await page.title()
    return {"status": "ok", "url": page.url, "title": title}


async def _click(page: Page, params: dict) -> dict:
    selector = params.get("selector")
    if not selector:
        return {"error": "Missing required parameter: selector"}
    await page.click(selector)
    return {"status": "ok", "action": "click", "selector": selector}


async def _type(page: Page, params: dict) -> dict:
    selector = params.get("selector")
    text = params.get("text")
    if not selector or not text:
        return {"error": "Missing required parameters: selector, text"}
    await page.fill(selector, text)
    return {"status": "ok", "action": "type", "selector": selector}


async def _screenshot(page: Page, params: dict) -> dict:
    full_page = params.get("full_page", False)
    raw = await page.screenshot(full_page=full_page)
    encoded = base64.b64encode(raw).decode()
    return {"status": "ok", "base64": encoded, "size_bytes": len(raw)}


async def _extract_text(page: Page, params: dict) -> dict:
    selector = params.get("selector", "body")
    text = await page.inner_text(selector)
    max_len = params.get("max_length", 10000)
    if len(text) > max_len:
        text = text[:max_len] + f"\n...(truncated, {len(text)} total chars)"
    return {"status": "ok", "text": text, "selector": selector}


async def _get_url(page: Page, params: dict) -> dict:
    title = await page.title()
    return {"status": "ok", "url": page.url, "title": title}


_ACTIONS = {
    "navigate": _navigate,
    "click": _click,
    "type": _type,
    "screenshot": _screenshot,
    "extract_text": _extract_text,
    "get_url": _get_url,
}
