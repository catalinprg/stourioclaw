from __future__ import annotations
import logging
import httpx
from src.notifications.base import BaseNotifier
from src.models.schemas import Notification, NotificationResult

logger = logging.getLogger("stourio.notifications.email")

_SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"


class EmailNotifier(BaseNotifier):
    supports_threads: bool = False
    supports_severity: bool = False

    def __init__(self, name: str, api_key: str, from_email: str, to_email: str):
        self.name = name
        self.api_key = api_key
        self.from_email = from_email
        self.to_email = to_email

    async def send(self, notification: Notification) -> NotificationResult:
        subject = f"[{notification.severity.upper()}] Stourio Notification"
        body = {
            "personalizations": [
                {"to": [{"email": self.to_email}]}
            ],
            "from": {"email": self.from_email},
            "subject": subject,
            "content": [
                {"type": "text/plain", "value": notification.message}
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(_SENDGRID_URL, json=body, headers=headers)
                logger.info(
                    "POST %s status=%d channel=%s",
                    _SENDGRID_URL, resp.status_code, notification.channel,
                )
                resp.raise_for_status()
            return NotificationResult(success=True, channel=notification.channel)
        except Exception as exc:
            logger.error("EmailNotifier %s failed: %s", self.name, exc)
            return NotificationResult(success=False, channel=notification.channel, error=str(exc))
