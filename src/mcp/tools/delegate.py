"""Inter-agent delegation tool.

Allows an executing agent to spawn a sub-agent inline and receive its result.
Uses contextvars to track delegation depth and prevent infinite recursion.
"""
from __future__ import annotations

import contextvars
import logging

from src.config import settings
from src.persistence import audit
from src.persistence.database import async_session
from src.mcp.tools.messaging import _check_peer_allowed

logger = logging.getLogger("stourio.tools.delegate")

# Track delegation depth per-task to prevent infinite recursion
_delegation_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "delegation_depth", default=0
)

MAX_DELEGATION_DEPTH = 3


async def delegate_to_agent(arguments: dict) -> dict:
    """Delegate work to another agent and return its result.

    Args (via arguments dict):
        agent_type: Name of the target agent (e.g. "analyst", "code_writer")
        objective: Clear objective for the sub-agent
        context: Optional additional context string
        conversation_id: Optional conversation_id for context continuity

    Returns:
        dict with "result" key on success, "error" key on failure.
    """
    agent_type = arguments.get("agent_type")
    objective = arguments.get("objective")
    context = arguments.get("context", "")
    conversation_id = arguments.get("conversation_id")

    if not agent_type:
        return {"error": "Missing required parameter: agent_type"}
    if not objective:
        return {"error": "Missing required parameter: objective"}

    # Depth guard
    current_depth = _delegation_depth.get()
    if current_depth >= MAX_DELEGATION_DEPTH:
        logger.warning(
            "Delegation depth limit reached (%d/%d). Refusing delegation to '%s'.",
            current_depth, MAX_DELEGATION_DEPTH, agent_type,
        )
        return {
            "error": f"Maximum delegation depth ({MAX_DELEGATION_DEPTH}) reached. "
                     f"Cannot delegate further to avoid infinite recursion.",
        }

    # Check delegation ACL (reuses peer allowlist)
    from_agent = arguments.get("_agent_name", "unknown")
    if not await _check_peer_allowed(from_agent, agent_type):
        return {"error": f"Agent '{from_agent}' is not allowed to delegate to '{agent_type}'. Update allowed_peers on the target agent."}

    await audit.log(
        "AGENT_DELEGATED",
        f"Delegation depth={current_depth + 1}: -> '{agent_type}': {objective[:200]}",
    )

    # Increment depth for the sub-agent's execution
    token = _delegation_depth.set(current_depth + 1)
    try:
        from src.agents.runtime import execute_agent

        async with async_session() as session:
            execution = await execute_agent(
                agent_name=agent_type,
                objective=objective,
                context=context or f"Delegated task (depth={current_depth + 1})",
                session=session,
                conversation_id=conversation_id,
            )

        if execution.status.value == "completed":
            return {
                "result": execution.result,
                "agent_type": agent_type,
                "execution_id": execution.id,
                "steps_taken": len(execution.steps),
            }
        else:
            return {
                "error": f"Sub-agent '{agent_type}' finished with status: {execution.status.value}",
                "detail": execution.result,
                "execution_id": execution.id,
            }

    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.exception("Delegation to '%s' failed: %s", agent_type, e)
        return {"error": f"Delegation failed: {str(e)}"}
    finally:
        _delegation_depth.reset(token)
