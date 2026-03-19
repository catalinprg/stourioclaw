import pytest
from unittest.mock import AsyncMock
from src.notifications.dispatcher import NotificationDispatcher
from src.models.schemas import Notification, NotificationResult


@pytest.mark.asyncio
async def test_notification_dispatched_on_approval():
    dispatcher = NotificationDispatcher()
    mock_notifier = AsyncMock()
    mock_notifier.send = AsyncMock(
        return_value=NotificationResult(success=True, channel="oncall-slack")
    )
    dispatcher.register_channel("oncall-slack", mock_notifier)

    notification = Notification(
        channel="oncall-slack",
        message="Approval requested: drop database (risk=critical)",
        severity="warning",
        context={"approval_id": "test-approval-123", "risk_level": "critical"},
    )
    result = await dispatcher.send(notification)

    mock_notifier.send.assert_called_once_with(notification)
    assert result.success is True
    assert result.channel == "oncall-slack"
