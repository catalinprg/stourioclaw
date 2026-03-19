"""Notification tool — wired to Telegram client."""

from __future__ import annotations

import logging

logger = logging.getLogger("stourio.tools.notification")

_telegram_client = None
_allowed_user_ids: list[int] = []


def get_telegram_client():
    """Return the wired Telegram client, or None if not configured."""
    return _telegram_client


def get_allowed_user_ids() -> list[int]:
    """Return the list of allowed Telegram user IDs."""
    return _allowed_user_ids


def set_telegram_client(client, allowed_user_ids: list[int]):
    """Wire the Telegram bot client and allowed recipients. Called during app startup."""
    global _telegram_client, _allowed_user_ids
    _telegram_client = client
    _allowed_user_ids = allowed_user_ids
    logger.info(
        "Telegram client wired: %s, recipients=%d",
        type(client).__name__,
        len(allowed_user_ids),
    )


async def send_notification(arguments: dict) -> dict:
    """Send a notification via Telegram to all allowed users."""
    if _telegram_client is None:
        return {"error": "Telegram client not configured"}

    message = arguments["message"]
    severity = arguments.get("severity", "info")

    if not _allowed_user_ids:
        return {"error": "No allowed user IDs configured"}

    try:
        prefix = f"[{severity.upper()}] " if severity != "info" else ""
        text = f"{prefix}{message}"

        for user_id in _allowed_user_ids:
            await _telegram_client.send_message(chat_id=user_id, text=text)

        logger.info(
            "Notification sent: severity=%s, recipients=%d",
            severity,
            len(_allowed_user_ids),
        )
        return {
            "status": "sent",
            "channel": "telegram",
            "recipients": len(_allowed_user_ids),
        }
    except Exception as exc:
        logger.exception("send_notification failed")
        return {"error": str(exc)}
