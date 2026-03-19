"""Security auditor - background worker that analyzes audit logs for anomalies."""

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.schemas import new_id
from src.persistence.database import SecurityAlertModel

logger = logging.getLogger("stourio.security.auditor")

FREQUENCY_THRESHOLD = 100  # Max actions per agent per audit interval
ERROR_THRESHOLD = 20  # Max errors per agent before alerting


@dataclass
class SecurityAlert:
    severity: str  # LOW, MEDIUM, HIGH, CRITICAL
    alert_type: str
    description: str
    source_agent: str
    source_execution_id: str
    raw_evidence: dict = field(default_factory=dict)


class SecurityAuditor:
    """Background worker that periodically analyzes audit logs for anomalies."""

    def __init__(self, session: AsyncSession, interval_seconds: int = 60):
        self.session = session
        self.interval_seconds = interval_seconds

    async def analyze_recent_activity(self, entries: list[dict[str, Any]]) -> list[SecurityAlert]:
        """Analyze audit log entries and return alerts for detected anomalies.

        Checks:
        1. High frequency per agent (> FREQUENCY_THRESHOLD actions)
        2. Repeated failures (> ERROR_THRESHOLD errors per agent)
        """
        alerts: list[SecurityAlert] = []

        # Count actions per agent
        agent_action_counts: Counter[str] = Counter()
        agent_error_counts: Counter[str] = Counter()
        # Track execution IDs per agent for evidence
        agent_execution_ids: dict[str, str] = {}

        for entry in entries:
            agent_id = entry.get("agent_id") or entry.get("source_agent") or "unknown"
            agent_action_counts[agent_id] += 1

            # Track first execution_id seen per agent
            if agent_id not in agent_execution_ids:
                agent_execution_ids[agent_id] = entry.get("execution_id") or ""

            # Count error entries
            action = entry.get("action", "")
            risk_level = entry.get("risk_level", "")
            if "error" in action.lower() or "fail" in action.lower() or risk_level in ("high", "critical"):
                agent_error_counts[agent_id] += 1

        # Check 1: High frequency per agent
        for agent_id, count in agent_action_counts.items():
            if count > FREQUENCY_THRESHOLD:
                alerts.append(SecurityAlert(
                    severity="HIGH",
                    alert_type="HIGH_FREQUENCY",
                    description=(
                        f"Agent '{agent_id}' made {count} actions in the audit interval "
                        f"(threshold: {FREQUENCY_THRESHOLD})"
                    ),
                    source_agent=agent_id,
                    source_execution_id=agent_execution_ids.get(agent_id, ""),
                    raw_evidence={"action_count": count, "threshold": FREQUENCY_THRESHOLD},
                ))

        # Check 2: Repeated failures
        for agent_id, count in agent_error_counts.items():
            if count > ERROR_THRESHOLD:
                alerts.append(SecurityAlert(
                    severity="MEDIUM",
                    alert_type="REPEATED_FAILURES",
                    description=(
                        f"Agent '{agent_id}' had {count} error/failure actions in the audit interval "
                        f"(threshold: {ERROR_THRESHOLD})"
                    ),
                    source_agent=agent_id,
                    source_execution_id=agent_execution_ids.get(agent_id, ""),
                    raw_evidence={"error_count": count, "threshold": ERROR_THRESHOLD},
                ))

        if alerts:
            logger.warning(f"Security auditor detected {len(alerts)} alert(s)")

        return alerts

    async def save_alerts(self, alerts: list[SecurityAlert]) -> None:
        """Persist alerts to the security_alerts table."""
        for alert in alerts:
            record = SecurityAlertModel(
                id=new_id(),
                severity=alert.severity,
                alert_type=alert.alert_type,
                description=alert.description,
                source_agent=alert.source_agent,
                source_execution_id=alert.source_execution_id,
                raw_evidence=alert.raw_evidence,
            )
            self.session.add(record)

        await self.session.commit()
        logger.info(f"Saved {len(alerts)} security alert(s)")
