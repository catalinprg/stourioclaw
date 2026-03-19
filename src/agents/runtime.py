from __future__ import annotations
import json
import logging
import asyncio
import re
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from src.config import settings
from src.mcp.registry import get_registry
from src.models.schemas import (
    AgentTemplate, AgentExecution, ExecutionStatus, ChatMessage, ToolDefinition, new_id,
)
from src.adapters.registry import get_agent_adapter
from src.agents.registry import AgentRegistry
from src.persistence import redis_store, audit
from src.persistence.conversations import get_history
from src.persistence.database import AgentModel

logger = logging.getLogger("stourio.agents")

# --- Deprecated stubs (removed in Task 19 when callers migrate to DB) ---
AGENT_TEMPLATES: dict[str, AgentTemplate] = {}


def load_agent_templates(directory: str | None = None) -> dict[str, AgentTemplate]:
    """Deprecated: agents now live in the DB. Returns empty dict."""
    logger.warning("load_agent_templates() is deprecated — agents are DB-backed now")
    return {}


def get_template(agent_type: str) -> AgentTemplate | None:
    """Deprecated: use AgentRegistry.get_by_name() instead."""
    return AGENT_TEMPLATES.get(agent_type)


def list_templates() -> list[AgentTemplate]:
    """Deprecated: use AgentRegistry.list_active() instead."""
    return list(AGENT_TEMPLATES.values())


def _resolve_tools(agent: AgentModel) -> list[ToolDefinition]:
    """Resolve tool name strings from DB agent into ToolDefinition objects."""
    registry = get_registry()
    tool_defs = []
    for tool_name in (agent.tools or []):
        tool = registry.get(tool_name)
        if tool:
            tool_defs.append(ToolDefinition(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
            ))
        else:
            logger.warning("Tool '%s' referenced by agent '%s' not found in registry", tool_name, agent.name)
    return tool_defs


# Strict pattern: alphanumeric, underscores, hyphens only
_SAFE_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_\-]+$")


async def default_tool_executor(tool_name: str, arguments: dict, agent_name: str = "unknown") -> str:
    """
    Production tool executor. Dispatches LLM tool calls via the ToolRegistry.
    Each tool's execution_mode determines local vs gateway dispatch.
    """
    registry = get_registry()

    if not _SAFE_TOOL_NAME.match(tool_name):
        logger.warning(f"SECURITY: Tool name contains illegal characters: '{tool_name}'")
        return json.dumps({"error": f"Invalid tool name: {tool_name}"})

    # Inject caller identity for tools that need it (messaging, etc.)
    arguments = {**arguments, "_agent_name": agent_name}

    try:
        result = await registry.execute(tool_name, arguments, agent_name=agent_name)
        return json.dumps(result) if isinstance(result, dict) else str(result)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        logger.error(f"Tool execution failed: {tool_name}: {e}")
        return json.dumps({"error": f"Tool execution failed: {str(e)}"})


async def execute_agent(
    agent_name: str,
    objective: str,
    context: str,
    session: AsyncSession,
    input_id: str | None = None,
    tool_executor: callable | None = None,
    conversation_id: str | None = None,
) -> AgentExecution:
    # Load agent config from DB
    reg = AgentRegistry(session)
    agent = await reg.get_by_name(agent_name)
    if not agent:
        raise ValueError(f"Unknown agent: {agent_name}")

    # Resolve tool definitions from plugin registry
    tools = _resolve_tools(agent)

    execution = AgentExecution(
        id=new_id(),
        agent_type=agent_name,
        objective=objective,
        context=context,
        status=ExecutionStatus.RUNNING,
    )

    # Acquire lock with a fencing token to prevent state collisions
    resource_id = f"agent_work:{execution.id}"
    fencing_token = await redis_store.acquire_lock_with_token(resource_id, ttl_seconds=30)

    if not fencing_token:
        execution.status = ExecutionStatus.FAILED
        execution.result = "Lock acquisition failed: another agent is already handling this resource."
        return execution

    # Heartbeat to extend the lock/token validity
    async def heartbeat():
        while True:
            await asyncio.sleep(10)
            await redis_store.extend_lock(resource_id, ttl_seconds=30)

    heartbeat_task = asyncio.create_task(heartbeat())

    await audit.log(
        "AGENT_STARTED",
        f"Agent '{agent.display_name}' started: {objective}",
        input_id=input_id,
        execution_id=execution.id,
    )

    try:
        # Get adapter — OpenRouter handles failover via route: "fallback"
        adapter = get_agent_adapter(agent.model)

        messages = [
            ChatMessage(role="user", content=f"Objective: {objective}\n\nContext:\n{context}")
        ]

        # Load conversation history
        if conversation_id:
            history = await get_history(conversation_id, limit=settings.conversation_history_limit)
            if history:
                history_context = "\n".join(f"[{m.role}]: {m.content}" for m in history)
                messages.insert(0, ChatMessage(role="user", content=f"Previous conversation context:\n{history_context}"))

        # Semantic memory recall
        system_prompt = agent.system_prompt
        from src.tools.python.knowledge_search import _retriever
        if _retriever:
            try:
                memories = await _retriever.search(query=objective, source_type="agent_memory", top_k_final=settings.agent_memory_recall_count)
                if memories:
                    memory_text = "\n\n".join(f"- {m.content} (score: {m.score:.2f})" for m in memories)
                    system_prompt = system_prompt + f"\n\nRelevant past experience:\n{memory_text}"
            except Exception as e:
                logger.warning(f"Memory recall failed: {e}")

        for step in range(agent.max_steps):
            # 1. Kill switch check
            if await redis_store.is_killed():
                execution.status = ExecutionStatus.HALTED
                execution.result = "Halted by kill switch"
                await audit.log(
                    "AGENT_HALTED",
                    f"Agent '{agent.display_name}' halted by kill switch at step {step + 1}",
                    execution_id=execution.id,
                )
                break

            # 2. Fencing check
            if not await redis_store.validate_fencing_token(resource_id, fencing_token):
                execution.status = ExecutionStatus.FAILED
                execution.result = "Fencing violation: authorized execution window lost."
                await audit.log(
                    "AGENT_FENCED_OUT",
                    "Process terminated: lock overtaken by a newer process",
                    execution_id=execution.id,
                )
                break

            # 3. LLM Reasoning Call — no failover needed, OpenRouter handles it
            response = await adapter.complete(
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
            )

            if response.has_tool_call:
                tc = response.first_tool_call
                execution.steps.append({
                    "step": step + 1,
                    "type": "tool_call",
                    "tool": tc["name"],
                    "arguments": tc["arguments"],
                })

                await audit.log(
                    "AGENT_TOOL_CALL",
                    f"Step {step + 1}: {tc['name']}({tc['arguments']})",
                    execution_id=execution.id,
                )

                if tool_executor:
                    tool_result = await tool_executor(tc["name"], tc["arguments"])
                else:
                    tool_result = await default_tool_executor(tc["name"], tc["arguments"], agent_name=agent.name)

                messages.append(ChatMessage(
                    role="assistant",
                    content=f"Tool call: {tc['name']}\nArguments: {tc['arguments']}",
                ))
                messages.append(ChatMessage(
                    role="user",
                    content=f"Tool result for {tc['name']}:\n{tool_result}",
                ))

            else:
                execution.steps.append({
                    "step": step + 1,
                    "type": "response",
                    "content": response.text,
                })
                execution.result = response.text
                execution.status = ExecutionStatus.COMPLETED
                execution.completed_at = datetime.utcnow()

                await audit.log(
                    "AGENT_COMPLETED",
                    f"Agent '{agent.display_name}' completed in {step + 1} steps",
                    execution_id=execution.id,
                )
                break
        else:
            execution.status = ExecutionStatus.COMPLETED
            execution.result = f"Agent reached maximum steps ({agent.max_steps})."
            execution.completed_at = datetime.utcnow()

    except Exception as e:
        execution.status = ExecutionStatus.FAILED
        execution.result = f"Agent error: {str(e)}"
        logger.exception(f"Agent execution failed: {e}")
        await audit.log(
            "AGENT_FAILED",
            f"Agent '{agent.display_name}' failed: {str(e)}",
            execution_id=execution.id,
        )
    finally:
        heartbeat_task.cancel()
        await redis_store.release_lock(resource_id)

    # Persist agent memory
    try:
        from src.rag.ingestion import ingest_text
        from src.tools.python.knowledge_search import _retriever
        if _retriever and execution.result:
            actions = [s.get("tool_name", s.get("tool", "unknown")) for s in execution.steps if s.get("tool_name") or s.get("tool")]
            memory_text = (
                f"# Agent Execution: {agent_name}\n"
                f"## Trigger\n{objective}\n"
                f"## Actions Taken\n{', '.join(actions)}\n"
                f"## Conclusion\n{execution.result}\n"
            )
            await ingest_text(
                embedder=_retriever.embedder,
                content=memory_text,
                source_type="agent_memory",
                source_path=f"agent/{execution.id}",
                title=f"{agent_name} - {objective[:100]}",
                extra_metadata={
                    "agent_template": agent_name,
                    "execution_id": execution.id,
                    "conversation_id": conversation_id or "",
                },
            )
    except Exception as e:
        logger.warning(f"Failed to persist agent memory: {e}")

    return execution
