"""Messaging tools for inter-agent communication.

- send_message: fire-and-forget message to another agent's inbox
- read_messages: check your own inbox for pending messages
- heartbeat_ack: daemon signals "nothing to report" (suppresses output)
"""
from __future__ import annotations

import logging

from src.daemons.inbox import enqueue_message, dequeue_messages
from src.persistence.database import async_session, AgentModel
from sqlalchemy import select

logger = logging.getLogger("stourio.tools.messaging")


async def _check_peer_allowed(from_agent: str, target_agent: str) -> bool:
    """Check if from_agent is allowed to message target_agent.

    If allowed_peers is empty, all agents can message (open by default).
    If allowed_peers is set, only listed agents can message (restricted mode).
    """
    async with async_session() as session:
        result = await session.execute(
            select(AgentModel).where(AgentModel.name == target_agent)
        )
        agent = result.scalars().first()
        if not agent:
            return False
        peers = agent.allowed_peers or []
        if not peers:
            return True  # Empty list = allow all
        return from_agent in peers


async def send_message(arguments: dict) -> dict:
    """Send a message to another agent's inbox."""
    target = arguments.get("target_agent")
    message = arguments.get("message")
    context = arguments.get("context", "")
    from_agent = arguments.pop("_agent_name", "unknown")

    if not target:
        return {"error": "Missing required parameter: target_agent"}
    if not message:
        return {"error": "Missing required parameter: message"}

    if not await _check_peer_allowed(from_agent, target):
        return {"error": f"Agent '{from_agent}' is not allowed to message '{target}'. Update allowed_peers on the target agent."}

    entry_id = await enqueue_message(
        target_agent=target,
        message=message,
        from_agent=from_agent,
        context=context,
    )

    if entry_id is None:
        return {"error": "Message rejected: exceeds maximum size (10,000 characters)"}

    return {"status": "delivered", "target_agent": target, "entry_id": entry_id}


async def read_messages(arguments: dict) -> dict:
    """Check your own inbox for pending messages."""
    agent_name = arguments.pop("_agent_name", None)
    limit = arguments.get("limit", 10)

    if not agent_name:
        return {"error": "Cannot determine calling agent identity"}

    messages = await dequeue_messages(agent_name, count=limit)

    return {
        "count": len(messages),
        "messages": [
            {"id": msg_id, **data}
            for msg_id, data in messages
        ],
    }


async def heartbeat_ack(arguments: dict) -> dict:
    """Daemon signals nothing needs attention this cycle."""
    return {"status": "ok", "action": "heartbeat_ack"}
