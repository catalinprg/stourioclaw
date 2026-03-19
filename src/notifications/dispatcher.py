from __future__ import annotations
import logging
import os
import re
from typing import Optional
from jinja2 import Template
from src.notifications.base import BaseNotifier
from src.models.schemas import Notification, NotificationResult

logger = logging.getLogger("stourio.notifications.dispatcher")

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _resolve_env_vars(value: str) -> str:
    """Replace ${ENV_VAR} tokens with their environment variable values."""
    def _replace(m: re.Match) -> str:
        return os.environ.get(m.group(1), m.group(0))
    return _ENV_VAR_RE.sub(_replace, value)


def _resolve_dict(d: dict) -> dict:
    """Recursively resolve env vars in all string values of a dict."""
    out = {}
    for k, v in d.items():
        if isinstance(v, str):
            out[k] = _resolve_env_vars(v)
        elif isinstance(v, dict):
            out[k] = _resolve_dict(v)
        else:
            out[k] = v
    return out


class NotificationDispatcher:
    def __init__(self) -> None:
        self._channels: dict[str, BaseNotifier] = {}

    def register_channel(self, channel_name: str, notifier: BaseNotifier) -> None:
        self._channels[channel_name] = notifier
        logger.info("Registered notification channel '%s' (%s)", channel_name, type(notifier).__name__)

    async def send(self, notification: Notification) -> NotificationResult:
        notifier = self._channels.get(notification.channel)
        if notifier is None:
            err = f"No notifier registered for channel '{notification.channel}'"
            logger.warning(err)
            return NotificationResult(success=False, channel=notification.channel, error=err)
        return await notifier.send(notification)

    async def send_templated(
        self,
        channel: str,
        template_str: str,
        context: dict,
        severity: str = "info",
    ) -> NotificationResult:
        rendered = Template(template_str).render(**context)
        notification = Notification(
            channel=channel,
            message=rendered,
            severity=severity,
            context=context,
        )
        return await self.send(notification)

    @classmethod
    def from_config(cls, config_path: str) -> "NotificationDispatcher":
        import yaml
        from src.notifications.webhook import WebhookNotifier

        with open(config_path) as f:
            raw = yaml.safe_load(f)

        dispatcher = cls()
        channels = raw.get("channels", {})
        for channel_name, cfg in channels.items():
            cfg = _resolve_dict(cfg)
            notifier_type = cfg.get("type", "webhook")

            if notifier_type == "webhook":
                notifier = WebhookNotifier(
                    name=channel_name,
                    url=cfg["url"],
                    headers=cfg.get("headers"),
                )
                dispatcher.register_channel(channel_name, notifier)
            else:
                logger.warning(
                    "Unsupported notifier type '%s' for channel '%s'; skipping.",
                    notifier_type, channel_name,
                )

        return dispatcher


# --- Lazy singleton ---

_dispatcher: Optional[NotificationDispatcher] = None


def get_dispatcher() -> NotificationDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = _load_default_dispatcher()
    return _dispatcher


def _load_default_dispatcher() -> NotificationDispatcher:
    """Load from default config path if it exists, otherwise return empty dispatcher."""
    import pathlib
    default_paths = [
        pathlib.Path("notifications.yaml"),
        pathlib.Path("config/notifications.yaml"),
        pathlib.Path("src/config/notifications.yaml"),
    ]
    for path in default_paths:
        if path.exists():
            logger.info("Loading notification config from %s", path)
            return NotificationDispatcher.from_config(str(path))
    logger.info("No notification config found; dispatcher has no channels")
    return NotificationDispatcher()
