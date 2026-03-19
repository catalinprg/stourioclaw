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


@pytest.mark.asyncio
async def test_navigate_action():
    from src.browser.actions import execute_action

    mock_page = AsyncMock()
    mock_page.title = AsyncMock(return_value="Example Domain")
    mock_page.url = "https://example.com"

    result = await execute_action(mock_page, "navigate", {"url": "https://example.com"})
    assert result["status"] == "ok"
    assert result["title"] == "Example Domain"
    mock_page.goto.assert_called_once_with("https://example.com", wait_until="domcontentloaded")


@pytest.mark.asyncio
async def test_extract_text_action():
    from src.browser.actions import execute_action

    mock_page = AsyncMock()
    mock_page.inner_text = AsyncMock(return_value="Hello world")

    result = await execute_action(mock_page, "extract_text", {"selector": "body"})
    assert result["status"] == "ok"
    assert result["text"] == "Hello world"


@pytest.mark.asyncio
async def test_unknown_action_returns_error():
    from src.browser.actions import execute_action

    mock_page = AsyncMock()
    result = await execute_action(mock_page, "fly_to_moon", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_click_action():
    from src.browser.actions import execute_action

    mock_page = AsyncMock()
    result = await execute_action(mock_page, "click", {"selector": "#submit"})
    assert result["status"] == "ok"
    mock_page.click.assert_called_once_with("#submit")


@pytest.mark.asyncio
async def test_type_action():
    from src.browser.actions import execute_action

    mock_page = AsyncMock()
    result = await execute_action(mock_page, "type", {"selector": "#email", "text": "test@example.com"})
    assert result["status"] == "ok"
    mock_page.fill.assert_called_once_with("#email", "test@example.com")


@pytest.mark.asyncio
async def test_screenshot_action():
    from src.browser.actions import execute_action
    import base64

    mock_page = AsyncMock()
    fake_bytes = b"fake-png-data"
    mock_page.screenshot = AsyncMock(return_value=fake_bytes)

    result = await execute_action(mock_page, "screenshot", {})
    assert result["status"] == "ok"
    assert result["base64"] == base64.b64encode(fake_bytes).decode()
