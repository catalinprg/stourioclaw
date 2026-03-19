from __future__ import annotations
import logging
from datetime import datetime
from src.models.schemas import AuditEntry, RiskLevel, new_id
from src.persistence.database import async_session, AuditLog

logger = logging.getLogger("stourio.audit")


async def log(
    action: str,
    detail: str,
    input_id: str | None = None,
    execution_id: str | None = None,
    risk_level: RiskLevel | None = None,
) -> AuditEntry:
    """Append an immutable audit entry."""
    entry = AuditEntry(
        id=new_id(),
        action=action,
        detail=detail,
        input_id=input_id,
        execution_id=execution_id,
        risk_level=risk_level,
    )

    async with async_session() as session:
        record = AuditLog(
            id=entry.id,
            action=entry.action,
            detail=entry.detail,
            input_id=entry.input_id,
            execution_id=entry.execution_id,
            risk_level=entry.risk_level.value if entry.risk_level else None,
            timestamp=entry.timestamp,
        )
        session.add(record)
        await session.commit()

    logger.info(f"AUDIT | {action} | {detail}")
    return entry


async def get_recent(limit: int = 50) -> list[dict]:
    """Get recent audit entries."""
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(AuditLog)
            .order_by(AuditLog.timestamp.desc())
            .limit(limit)
        )
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "action": r.action,
                "detail": r.detail,
                "input_id": r.input_id,
                "execution_id": r.execution_id,
                "risk_level": r.risk_level,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in rows
        ]
