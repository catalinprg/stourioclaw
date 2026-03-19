"""Telegram message formatting utilities."""

from __future__ import annotations


def to_telegram_markdown(text: str) -> str:
    """Basic pass-through — Telegram basic Markdown mode is lenient."""
    return text


def format_approval_request(
    approval_id: str,
    action_description: str,
    risk_level: str,
    reasoning: str,
) -> tuple[str, dict]:
    """Returns (text, reply_markup) with inline keyboard for approve/reject."""
    risk_emoji = {
        "low": "LOW",
        "medium": "MEDIUM",
        "high": "HIGH",
        "critical": "CRITICAL",
    }
    risk_label = risk_emoji.get(risk_level, risk_level.upper())

    text = (
        f"*Approval Required* [{risk_label}]\n\n"
        f"*Action:* {action_description}\n"
        f"*Risk:* {risk_label}\n"
        f"*Reasoning:* {reasoning}\n\n"
        f"_ID: {approval_id}_"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "Approve", "callback_data": f"approve:{approval_id}"},
                {"text": "Reject", "callback_data": f"reject:{approval_id}"},
            ]
        ]
    }

    return text, reply_markup


def format_security_alert(
    severity: str,
    alert_type: str,
    description: str,
    source_agent: str,
) -> str:
    """Format security alert for Telegram."""
    severity_label = severity.upper()
    return (
        f"*Security Alert* [{severity_label}]\n\n"
        f"*Type:* {alert_type}\n"
        f"*Source:* {source_agent}\n"
        f"*Details:* {description}"
    )
