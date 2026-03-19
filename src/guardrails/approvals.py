from __future__ import annotations
import logging
from datetime import datetime, timedelta
from sqlalchemy import select
from src.config import settings
from src.models.schemas import (
    ApprovalRequest, ApprovalDecision, RiskLevel, ExecutionStatus, new_id,
    Notification,
)
from src.persistence.database import async_session, ApprovalRecord
from src.persistence import redis_store, audit
from src.notifications.dispatcher import get_dispatcher

logger = logging.getLogger("stourio.guardrails")


async def check_kill_switch() -> bool:
    """Returns True if system is halted."""
    killed = await redis_store.is_killed()
    if killed:
        logger.warning("Operation blocked: kill switch is active")
    return killed


async def create_approval_request(
    action_description: str,
    risk_level: RiskLevel,
    blast_radius: str = "",
    reasoning: str = "",
    input_id: str = "",
) -> ApprovalRequest:
    """Create a pending approval and store it."""
    approval = ApprovalRequest(
        id=new_id(),
        action_description=action_description,
        risk_level=risk_level,
        blast_radius=blast_radius,
        reasoning=reasoning,
        original_input_id=input_id,
        status="pending",
        expires_at=datetime.utcnow() + timedelta(seconds=settings.approval_ttl_seconds),
    )

    # Store in DB
    async with async_session() as session:
        record = ApprovalRecord(
            id=approval.id,
            action_description=approval.action_description,
            risk_level=approval.risk_level.value,
            blast_radius=approval.blast_radius,
            reasoning=approval.reasoning,
            original_input_id=approval.original_input_id,
            status="pending",
            expires_at=approval.expires_at,
        )
        session.add(record)
        await session.commit()

    # Cache in Redis with TTL
    await redis_store.cache_approval(approval.id, {
        "id": approval.id,
        "action": approval.action_description,
        "risk_level": approval.risk_level.value,
        "input_id": approval.original_input_id,
    })

    await audit.log(
        "GUARDRAIL_APPROVAL_REQUESTED",
        f"Approval required: {action_description}",
        input_id=input_id,
        risk_level=risk_level,
    )

    try:
        dispatcher = get_dispatcher()
        await dispatcher.send(Notification(
            channel="oncall-slack",
            message=f"Approval requested: {action_description} (risk={risk_level.value})",
            severity="warning",
            context={"approval_id": approval.id, "risk_level": risk_level.value},
        ))
    except Exception as _exc:
        logger.warning("Notification failed for approval request %s: %s", approval.id, _exc)

    return approval


async def resolve_approval(
    approval_id: str, decision: ApprovalDecision
) -> ApprovalRequest | None:
    """Resolve a pending approval. Returns None if expired or not found."""

    # Check if still in Redis (not expired)
    cached = await redis_store.get_cached_approval(approval_id)
    if cached is None:
        # TTL expired - auto-reject
        async with async_session() as session:
            result = await session.execute(
                select(ApprovalRecord).where(ApprovalRecord.id == approval_id)
            )
            record = result.scalar_one_or_none()
            if record and record.status == "pending":
                record.status = "expired"
                record.resolved_at = datetime.utcnow()
                record.resolved_note = "TTL expired before resolution"
                await session.commit()

        await audit.log(
            "GUARDRAIL_APPROVAL_EXPIRED",
            f"Approval {approval_id} expired (TTL exceeded)",
        )
        return None

    # Resolve
    status = "approved" if decision.approved else "rejected"
    async with async_session() as session:
        result = await session.execute(
            select(ApprovalRecord).where(ApprovalRecord.id == approval_id)
        )
        record = result.scalar_one_or_none()
        if record:
            record.status = status
            record.resolved_at = datetime.utcnow()
            record.resolved_note = decision.note or ""
            await session.commit()

    await redis_store.delete_cached_approval(approval_id)

    await audit.log(
        f"GUARDRAIL_APPROVAL_{status.upper()}",
        f"Approval {approval_id}: {status}" + (f" - {decision.note}" if decision.note else ""),
    )

    resolved = ApprovalRequest(
        id=approval_id,
        action_description=cached["action"],
        risk_level=RiskLevel(cached["risk_level"]),
        original_input_id=cached.get("input_id", ""),
        status=status,
    )

    try:
        severity = "info" if decision.approved else "warning"
        dispatcher = get_dispatcher()
        await dispatcher.send(Notification(
            channel="oncall-slack",
            message=f"Approval {status}: {cached['action']}" + (f" — {decision.note}" if decision.note else ""),
            severity=severity,
            context={"approval_id": approval_id, "status": status},
        ))
    except Exception as _exc:
        logger.warning("Notification failed for approval resolution %s: %s", approval_id, _exc)

    return resolved


async def get_pending_approvals() -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(ApprovalRecord)
            .where(ApprovalRecord.status == "pending")
            .order_by(ApprovalRecord.created_at.desc())
        )
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "action_description": r.action_description,
                "risk_level": r.risk_level,
                "blast_radius": r.blast_radius,
                "reasoning": r.reasoning,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            }
            for r in rows
        ]
