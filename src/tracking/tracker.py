from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional
from sqlalchemy import func, select
from src.models.schemas import new_id
from src.persistence.database import TokenUsageRecord, async_session
from src.tracking.pricing import estimate_cost

logger = logging.getLogger("stourio.tracker")


async def track_usage(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    call_type: str = "llm",
    execution_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    agent_template: Optional[str] = None,
    cached_hit: bool = False,
    units_used: int = 0,
) -> None:
    cost = estimate_cost(model, input_tokens=input_tokens, output_tokens=output_tokens, units=units_used)
    record = TokenUsageRecord(
        id=new_id(),
        execution_id=execution_id,
        conversation_id=conversation_id,
        agent_template=agent_template,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        estimated_cost_usd=cost,
        call_type=call_type,
        cached_hit=cached_hit,
        units_used=units_used,
    )
    async with async_session() as session:
        session.add(record)
        await session.commit()
    logger.info(
        "token_usage model=%s provider=%s input=%d output=%d cost=%.6f",
        model, provider, input_tokens, output_tokens, cost,
    )


async def get_usage_summary(
    group_by: str = "model",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> list[dict]:
    group_col_map = {
        "model": TokenUsageRecord.model,
        "provider": TokenUsageRecord.provider,
        "agent_template": TokenUsageRecord.agent_template,
        "call_type": TokenUsageRecord.call_type,
    }
    group_col = group_col_map.get(group_by, TokenUsageRecord.model)

    stmt = select(
        group_col.label("group"),
        func.sum(TokenUsageRecord.input_tokens).label("total_input_tokens"),
        func.sum(TokenUsageRecord.output_tokens).label("total_output_tokens"),
        func.sum(TokenUsageRecord.total_tokens).label("total_tokens"),
        func.sum(TokenUsageRecord.estimated_cost_usd).label("total_cost_usd"),
        func.count(TokenUsageRecord.id).label("call_count"),
    ).group_by(group_col)

    if from_date:
        try:
            dt = datetime.fromisoformat(from_date)
            stmt = stmt.where(TokenUsageRecord.created_at >= dt)
        except ValueError:
            logger.warning("Invalid from_date value: %s", from_date)

    if to_date:
        try:
            dt = datetime.fromisoformat(to_date)
            stmt = stmt.where(TokenUsageRecord.created_at <= dt)
        except ValueError:
            logger.warning("Invalid to_date value: %s", to_date)

    async with async_session() as session:
        result = await session.execute(stmt)
        rows = result.fetchall()

    return [
        {
            group_by: row.group,
            "total_input_tokens": row.total_input_tokens or 0,
            "total_output_tokens": row.total_output_tokens or 0,
            "total_tokens": row.total_tokens or 0,
            "total_cost_usd": float(row.total_cost_usd or 0),
            "call_count": row.call_count,
        }
        for row in rows
    ]
