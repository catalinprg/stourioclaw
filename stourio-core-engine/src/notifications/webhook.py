from __future__ import annotations
import logging
import httpx
from src.notifications.base import BaseNotifier
from src.models.schemas import Notification, NotificationResult

logger = logging.getLogger("stourio.notifications.webhook")


class WebhookNotifier(BaseNotifier):
    supports_threads: bool = False
    supports_severity: bool = False

    def __init__(self, name: str, url: str, headers: dict | None = None):
        self.name = name
        self.url = url
        self.headers = headers or {}

    async def send(self, notification: Notification) -> NotificationResult:
        payload = {
            "message": notification.message,
            "severity": notification.severity,
            "context": notification.context,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(self.url, json=payload, headers=self.headers)
                logger.info(
                    "POST %s status=%d channel=%s",
                    self.url, resp.status_code, notification.channel,
                )
                resp.raise_for_status()
            return NotificationResult(success=True, channel=notification.channel)
        except Exception as exc:
            logger.error("WebhookNotifier %s failed: %s", self.name, exc)
            return NotificationResult(success=False, channel=notification.channel, error=str(exc))
