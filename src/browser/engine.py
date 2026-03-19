"""Playwright-based browser pool for web automation."""
from __future__ import annotations

import asyncio
import logging
from playwright.async_api import async_playwright, Browser, Page, Playwright

logger = logging.getLogger("stourio.browser.engine")


class BrowserPool:
    """Manages a single Chromium browser instance with page lifecycle."""

    def __init__(self, headless: bool = True, timeout_ms: int = 30000):
        self._headless = headless
        self._timeout_ms = timeout_ms
        self._pw_ctx = None
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def start(self) -> None:
        """Launch the browser."""
        if self._browser is not None:
            return
        self._pw_ctx = async_playwright()
        self._playwright = await self._pw_ctx.__aenter__()
        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
        )
        logger.info("Browser pool started (headless=%s)", self._headless)

    async def get_page(self) -> Page:
        """Create a new page (tab) in the browser."""
        if self._browser is None:
            raise RuntimeError("Browser not started. Call start() first.")
        page = await self._browser.new_page()
        page.set_default_timeout(self._timeout_ms)
        return page

    async def stop(self) -> None:
        """Close browser and cleanup."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright and hasattr(self, "_pw_ctx"):
            await self._pw_ctx.__aexit__(None, None, None)
            self._playwright = None
        logger.info("Browser pool stopped")


# Global singleton — lazily initialized with lock for async safety
_pool: BrowserPool | None = None
_pool_lock: asyncio.Lock | None = None


def _get_pool_lock() -> asyncio.Lock:
    """Lazy-init the lock (must be created inside an event loop)."""
    global _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    return _pool_lock


async def get_browser_pool() -> BrowserPool:
    """Get or create the global browser pool (async-safe)."""
    global _pool
    async with _get_pool_lock():
        if _pool is None:
            from src.config import settings
            _pool = BrowserPool(
                headless=getattr(settings, "browser_headless", True),
                timeout_ms=getattr(settings, "browser_timeout_ms", 30000),
            )
            await _pool.start()
        return _pool


async def shutdown_browser_pool() -> None:
    """Shutdown the global browser pool."""
    global _pool
    if _pool:
        await _pool.stop()
        _pool = None
