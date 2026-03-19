"""Tests for the browser automation subsystem."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_browser_pool_lifecycle():
    """BrowserPool can start and stop without errors."""
    from src.browser.engine import BrowserPool

    with patch("src.browser.engine.async_playwright") as mock_pw:
        mock_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_instance.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_pw.return_value.__aexit__ = AsyncMock(return_value=False)

        pool = BrowserPool(headless=True)
        await pool.start()
        assert pool._browser is not None
        await pool.stop()
        mock_browser.close.assert_called_once()


@pytest.mark.asyncio
async def test_browser_pool_get_page():
    """BrowserPool.get_page() returns a new page from the browser."""
    from src.browser.engine import BrowserPool

    mock_browser = AsyncMock()
    mock_page = AsyncMock()
    mock_browser.new_page = AsyncMock(return_value=mock_page)

    pool = BrowserPool(headless=True)
    pool._browser = mock_browser

    page = await pool.get_page()
    assert page is mock_page
    mock_browser.new_page.assert_called_once()
