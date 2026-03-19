"""Notification tool — stub, wired to real dispatcher in Task 12B."""

from __future__ import annotations

import logging

logger = logging.getLogger("stourio.tools.notification")

_dispatcher = None


def set_dispatcher(dispatcher):
    """Wire the notification dispatcher. Called during app startup."""
    global _dispatcher
    _dispatcher = dispatcher
    logger.info("Notification dispatcher wired: %s", type(dispatcher).__name__)


async def send_notification(arguments: dict) -> dict:
    """Send a notification through the configured dispatcher."""
    if _dispatcher is None:
        return {"error": "Notification dispatcher not initialized"}

    channel = arguments.get("channel", "default")
    message = arguments["message"]
    severity = arguments.get("severity", "info")

    try:
        await _dispatcher.send(channel=channel, message=message, severity=severity)
        logger.info("send_notification: channel=%s, severity=%s", channel, severity)
        return {"status": "sent", "channel": channel}
    except Exception as exc:
        logger.exception("send_notification failed")
        return {"error": str(exc)}
