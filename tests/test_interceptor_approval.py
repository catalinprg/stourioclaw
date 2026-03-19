"""Tests for security interceptor wired into tool execution (Task 23)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.mcp.base import Tool
from src.mcp.registry import ToolRegistry
from src.security.interceptor import InterceptResult


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _echo_fn(arguments: dict) -> dict:
    return {"echo": arguments}


def _make_registry_with_tool() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        Tool(name="write_file", description="Write a file", execute_fn=_echo_fn)
    )
    return registry


def _make_mock_interceptor(intercepted: bool, reason: str = "", severity: str = "HIGH"):
    interceptor = AsyncMock()
    interceptor.check_tool_call.return_value = InterceptResult(
        intercepted=intercepted, reason=reason, severity=severity
    )
    return interceptor


def _make_mock_approval_handler(approval_id: str = "appr-001", wait_result: bool = False):
    handler = AsyncMock()
    approval = MagicMock()
    approval.id = approval_id
    handler.create_approval.return_value = approval
    handler.wait_for_resolution.return_value = wait_result
    return handler


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unintercepted_tool_executes_normally():
    """When interceptor says not intercepted, tool executes without approval."""
    registry = _make_registry_with_tool()
    interceptor = _make_mock_interceptor(intercepted=False)
    registry.set_interceptor(interceptor)

    result = await registry.execute("write_file", {"path": "/tmp/x"})

    assert result == {"echo": {"path": "/tmp/x"}}
    interceptor.check_tool_call.assert_awaited_once_with(
        "write_file", {"path": "/tmp/x"}, "unknown"
    )


@pytest.mark.asyncio
async def test_intercepted_tool_creates_approval():
    """Intercepted tool creates an approval and returns blocked when rejected."""
    registry = _make_registry_with_tool()
    interceptor = _make_mock_interceptor(
        intercepted=True, reason="high-risk tool", severity="HIGH"
    )
    handler = _make_mock_approval_handler(
        approval_id="appr-blocked", wait_result=False
    )
    registry.set_interceptor(interceptor, approval_handler=handler)

    with patch("src.config.settings") as mock_settings:
        mock_settings.telegram_allowed_user_ids = []
        mock_settings.approval_ttl_seconds = 300

        result = await registry.execute("write_file", {"path": "/tmp/x"}, agent_name="test-agent")

    assert result["blocked"] is True
    assert result["approval_id"] == "appr-blocked"
    assert "high-risk" in result["reason"]

    handler.create_approval.assert_awaited_once()
    handler.wait_for_resolution.assert_awaited_once_with("appr-blocked", timeout_seconds=300)


@pytest.mark.asyncio
async def test_approved_tool_executes():
    """When approval is granted, the tool executes and returns its result."""
    registry = _make_registry_with_tool()
    interceptor = _make_mock_interceptor(
        intercepted=True, reason="high-risk tool", severity="HIGH"
    )
    handler = _make_mock_approval_handler(
        approval_id="appr-ok", wait_result=True
    )
    registry.set_interceptor(interceptor, approval_handler=handler)

    with patch("src.config.settings") as mock_settings:
        mock_settings.telegram_allowed_user_ids = []
        mock_settings.approval_ttl_seconds = 300

        result = await registry.execute("write_file", {"path": "/tmp/x"}, agent_name="test-agent")

    # Tool executed successfully after approval
    assert result == {"echo": {"path": "/tmp/x"}}
    assert "blocked" not in result

    handler.create_approval.assert_awaited_once()
    handler.wait_for_resolution.assert_awaited_once_with("appr-ok", timeout_seconds=300)


@pytest.mark.asyncio
async def test_intercepted_sends_telegram_notification():
    """Intercepted tool sends Telegram notification with inline keyboard."""
    registry = _make_registry_with_tool()
    interceptor = _make_mock_interceptor(
        intercepted=True, reason="sensitive data", severity="CRITICAL"
    )
    handler = _make_mock_approval_handler(
        approval_id="appr-tg", wait_result=False
    )
    tg_client = AsyncMock()
    registry.set_interceptor(interceptor, approval_handler=handler, telegram_client=tg_client)

    with patch("src.config.settings") as mock_settings:
        mock_settings.telegram_allowed_user_ids = [12345]
        mock_settings.approval_ttl_seconds = 300

        await registry.execute("write_file", {"data": "secret"}, agent_name="ops")

    tg_client.send_message.assert_awaited_once()
    call_kwargs = tg_client.send_message.call_args[1]
    assert call_kwargs["chat_id"] == 12345
    assert "reply_markup" in call_kwargs
    keyboard = call_kwargs["reply_markup"]["inline_keyboard"]
    assert keyboard[0][0]["text"] == "Approve"
    assert keyboard[0][1]["text"] == "Reject"
    assert "appr-tg" in keyboard[0][0]["callback_data"]


@pytest.mark.asyncio
async def test_no_interceptor_executes_directly():
    """Without an interceptor set, tools execute without any checks."""
    registry = _make_registry_with_tool()
    # No interceptor set
    result = await registry.execute("write_file", {"key": "val"})
    assert result == {"echo": {"key": "val"}}
