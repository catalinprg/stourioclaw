"""Tests for Redis pub/sub connection helper."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_get_pubsub_connection_returns_pubsub():
    from src.persistence.redis_store import get_pubsub_connection

    with patch("src.persistence.redis_store.get_redis") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_pubsub = MagicMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
        mock_get_redis.return_value = mock_redis

        result = await get_pubsub_connection()
        assert result is mock_pubsub


@pytest.mark.asyncio
async def test_publish_daemon_event():
    from src.persistence.redis_store import publish_daemon_event

    with patch("src.persistence.redis_store.get_redis") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis

        await publish_daemon_event("start", "my-daemon")
        mock_redis.publish.assert_called_once()
