from __future__ import annotations
import logging
import httpx
from src.notifications.base import BaseNotifier
from src.models.schemas import Notification, NotificationResult

logger = logging.getLogger("stourio.notifications.pagerduty")

_PAGERDUTY_URL = "https://events.pagerduty.com/v2/enqueue"

_SEVERITY_MAP: dict[str, str] = {
    "info": "info",
    "warning": "warning",
    "error": "error",
    "critical": "critical",
}


class PagerDutyNotifier(BaseNotifier):
    supports_threads: bool = False
    supports_severity: bool = True

    def __init__(self, name: str, api_key: str, service_id: str):
        self.name = name
        self.api_key = api_key
        self.service_id = service_id

    async def send(self, notification: Notification) -> NotificationResult:
        severity = _SEVERITY_MAP.get(notification.severity, "info")
        payload = {
            "routing_key": self.api_key,
            "event_action": "trigger",
            "payload": {
                "summary": notification.message,
                "severity": severity,
                "source": self.service_id,
                "custom_details": notification.context,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(_PAGERDUTY_URL, json=payload)
                logger.info(
                    "POST %s status=%d channel=%s",
                    _PAGERDUTY_URL, resp.status_code, notification.channel,
                )
                resp.raise_for_status()
            return NotificationResult(success=True, channel=notification.channel)
        except Exception as exc:
            logger.error("PagerDutyNotifier %s failed: %s", self.name, exc)
            return NotificationResult(success=False, channel=notification.channel, error=str(exc))
