"""Tests for daemon loop."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_daemon_cycle_heartbeat_no_messages():
    """Daemon cycle with no inbox messages runs heartbeat prompt."""
    from src.daemons.loop import run_daemon_cycle

    mock_execution = MagicMock()
    mock_execution.status.value = "completed"
    mock_execution.result = "Everything looks normal."
    mock_execution.steps = []
    mock_execution.id = "exec-123"

    with patch("src.daemons.loop.dequeue_messages", new_callable=AsyncMock, return_value=[]), \
         patch("src.daemons.loop.get_pool") as mock_pool, \
         patch("src.daemons.loop.async_session") as mock_session_factory, \
         patch("src.daemons.loop.audit") as mock_audit, \
         patch("src.daemons.loop.ack_message", new_callable=AsyncMock):
        mock_audit.log = AsyncMock()
        mock_pool.return_value.execute = AsyncMock(return_value=mock_execution)
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await run_daemon_cycle("test-daemon", "Check things.", max_messages=10)

    assert result["suppressed"] is False
    assert result["result"] == "Everything looks normal."


@pytest.mark.asyncio
async def test_daemon_cycle_suppressed_on_heartbeat_ack():
    """Daemon cycle is suppressed when agent calls heartbeat_ack tool."""
    from src.daemons.loop import run_daemon_cycle

    mock_execution = MagicMock()
    mock_execution.status.value = "completed"
    mock_execution.result = "All clear."
    mock_execution.steps = [{"tool": "heartbeat_ack", "type": "tool_call"}]
    mock_execution.id = "exec-456"

    with patch("src.daemons.loop.dequeue_messages", new_callable=AsyncMock, return_value=[]), \
         patch("src.daemons.loop.get_pool") as mock_pool, \
         patch("src.daemons.loop.async_session") as mock_session_factory, \
         patch("src.daemons.loop.audit") as mock_audit, \
         patch("src.daemons.loop.ack_message", new_callable=AsyncMock):
        mock_audit.log = AsyncMock()
        mock_pool.return_value.execute = AsyncMock(return_value=mock_execution)
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await run_daemon_cycle("test-daemon", "Check things.", max_messages=10)

    assert result["suppressed"] is True


@pytest.mark.asyncio
async def test_is_in_active_hours():
    from src.daemons.loop import is_in_active_hours

    # Always active if no window set
    assert is_in_active_hours(None) is True

    # Full-day window should always be active
    config = {"start": "00:00", "end": "23:59"}
    assert is_in_active_hours(config) is True
