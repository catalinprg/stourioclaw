"""Tests for agent inbox (Redis stream-based message queue)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_enqueue_message():
    from src.daemons.inbox import enqueue_message

    with patch("src.daemons.inbox.get_redis") as mock_get_redis, \
         patch("src.daemons.inbox.notify_inbox") as mock_notify:
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="1234-0")
        mock_get_redis.return_value = mock_redis
        mock_notify.return_value = None

        result = await enqueue_message("analyst", "hello", from_agent="assistant")
        assert result == "1234-0"
        mock_redis.xadd.assert_called_once()
        mock_notify.assert_called_once_with("analyst")


@pytest.mark.asyncio
async def test_enqueue_rejects_oversized_message():
    from src.daemons.inbox import enqueue_message

    long_msg = "x" * 10001
    result = await enqueue_message("analyst", long_msg, from_agent="assistant")
    assert result is None


@pytest.mark.asyncio
async def test_dequeue_messages():
    from src.daemons.inbox import dequeue_messages

    with patch("src.daemons.inbox.get_redis") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.xreadgroup = AsyncMock(return_value=[
            ("stourio:inbox:analyst", [
                ("msg-1", {"data": '{"from_agent":"assistant","message":"hi"}'})
            ])
        ])
        mock_get_redis.return_value = mock_redis

        messages = await dequeue_messages("analyst", count=10)
        assert len(messages) == 1
        assert messages[0][1]["from_agent"] == "assistant"


@pytest.mark.asyncio
async def test_ack_message():
    from src.daemons.inbox import ack_message

    with patch("src.daemons.inbox.get_redis") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis

        await ack_message("analyst", "msg-1")
        mock_redis.xack.assert_called_once()
        mock_redis.xdel.assert_called_once()


@pytest.mark.asyncio
async def test_send_message_tool_rejects_missing_target():
    from src.mcp.tools.messaging import send_message
    result = await send_message({"message": "hello"})
    assert "error" in result


@pytest.mark.asyncio
async def test_send_message_tool_rejects_disallowed_peer():
    from src.mcp.tools.messaging import send_message
    with patch("src.mcp.tools.messaging._check_peer_allowed") as mock_check:
        mock_check.return_value = False
        result = await send_message({
            "target_agent": "analyst",
            "message": "hello",
            "_agent_name": "assistant",
        })
        assert "error" in result
        assert "not allowed" in result["error"].lower()


@pytest.mark.asyncio
async def test_heartbeat_ack_tool():
    from src.mcp.tools.messaging import heartbeat_ack
    result = await heartbeat_ack({})
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_read_messages_tool():
    from src.mcp.tools.messaging import read_messages
    with patch("src.mcp.tools.messaging.dequeue_messages") as mock_dequeue:
        mock_dequeue.return_value = [("msg-1", {"from_agent": "assistant", "message": "hi"})]
        result = await read_messages({"_agent_name": "analyst"})
        assert result["count"] == 1
        assert len(result["messages"]) == 1
