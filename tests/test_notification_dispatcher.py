import pytest
from unittest.mock import AsyncMock
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.base import BaseNotifier
from src.models.schemas import Notification, NotificationResult


class MockNotifier(BaseNotifier):
    name = "mock"

    def __init__(self):
        self.send = AsyncMock(return_value=NotificationResult(success=True, channel="test-channel"))

    async def send(self, notification: Notification) -> NotificationResult:  # type: ignore[override]
        return NotificationResult(success=True, channel=notification.channel)


@pytest.mark.asyncio
async def test_dispatcher_routes_to_channel():
    dispatcher = NotificationDispatcher()
    notifier = AsyncMock()
    notifier.send = AsyncMock(return_value=NotificationResult(success=True, channel="test-channel"))

    dispatcher.register_channel("test-channel", notifier)

    notification = Notification(channel="test-channel", message="Hello", severity="info")
    result = await dispatcher.send(notification)

    notifier.send.assert_called_once_with(notification)
    assert result.success is True


@pytest.mark.asyncio
async def test_dispatcher_unknown_channel():
    dispatcher = NotificationDispatcher()

    notification = Notification(channel="nonexistent-channel", message="Hello", severity="info")
    result = await dispatcher.send(notification)

    assert result.success is False
    assert result.error is not None
    assert "nonexistent-channel" in result.error
