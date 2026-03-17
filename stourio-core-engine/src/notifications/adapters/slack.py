from __future__ import annotations
import logging
import httpx
from src.notifications.base import BaseNotifier
from src.models.schemas import Notification, NotificationResult

logger = logging.getLogger("stourio.notifications.slack")

_SEVERITY_EMOJI: dict[str, str] = {
    "info": ":information_source:",
    "warning": ":warning:",
    "error": ":x:",
    "critical": ":rotating_light:",
}


class SlackNotifier(BaseNotifier):
    supports_threads: bool = True
    supports_severity: bool = True

    def __init__(self, name: str, webhook_url: str, default_channel: str = ""):
        self.name = name
        self.webhook_url = webhook_url
        self.default_channel = default_channel

    async def send(self, notification: Notification) -> NotificationResult:
        emoji = _SEVERITY_EMOJI.get(notification.severity, ":bell:")
        text = f"{emoji} {notification.message}"

        body: dict = {"text": text}
        if self.default_channel:
            body["channel"] = self.default_channel
        if notification.thread_id:
            body["thread_ts"] = notification.thread_id

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(self.webhook_url, json=body)
                logger.info(
                    "POST %s status=%d channel=%s",
                    self.webhook_url, resp.status_code, notification.channel,
                )
                resp.raise_for_status()
            return NotificationResult(success=True, channel=notification.channel)
        except Exception as exc:
            logger.error("SlackNotifier %s failed: %s", self.name, exc)
            return NotificationResult(success=False, channel=notification.channel, error=str(exc))
