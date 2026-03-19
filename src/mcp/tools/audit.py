"""Audit log query tool — wired to AuditLog DB table."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

logger = logging.getLogger("stourio.tools.audit")

_session_factory = None


def set_session_factory(factory):
    """Wire the async session factory (async_sessionmaker). Called during app startup."""
    global _session_factory
    _session_factory = factory
    logger.info("Audit session factory wired")


async def read_audit_log(arguments: dict) -> dict:
    """Query the audit_log table with optional filters."""
    if _session_factory is None:
        return {"error": "DB session not initialized", "entries": []}

    limit = arguments.get("limit", 20)
    agent = arguments.get("agent")
    action = arguments.get("action")
    hours = arguments.get("hours", 24)

    try:
        from src.persistence.database import AuditLog

        async with _session_factory() as session:
            query = (
                select(AuditLog)
                .order_by(AuditLog.timestamp.desc())
                .limit(limit)
            )

            if agent:
                query = query.where(AuditLog.agent_id == agent)

            if action:
                query = query.where(AuditLog.action == action)

            since = datetime.utcnow() - timedelta(hours=hours)
            query = query.where(AuditLog.timestamp >= since)

            result = await session.execute(query)
            entries = result.scalars().all()

            return {
                "entries": [
                    {
                        "id": e.id,
                        "action": e.action,
                        "detail": e.detail or "",
                        "agent_id": e.agent_id or "",
                        "risk_level": e.risk_level or "",
                        "timestamp": e.timestamp.isoformat() if e.timestamp else "",
                        "execution_id": e.execution_id or "",
                    }
                    for e in entries
                ],
                "count": len(entries),
            }
    except Exception as exc:
        logger.exception("read_audit_log failed")
        return {"error": str(exc), "entries": []}
