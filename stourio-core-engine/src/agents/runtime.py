from __future__ import annotations
import json
import logging
import asyncio
from datetime import datetime
from src.config import settings
from src.plugins.registry import get_registry
from src.models.schemas import (
    AgentTemplate, AgentExecution, ExecutionStatus, ChatMessage, ToolDefinition, new_id,
)
from src.adapters.registry import get_agent_adapter
from src.persistence import redis_store, audit
from src.persistence.conversations import get_history

logger = logging.getLogger("stourio.agents")


# --- Built-in agent templates ---

AGENT_TEMPLATES: dict[str, AgentTemplate] = {
    "diagnose_repair": AgentTemplate(
        id="diagnose_repair",
        name="Diagnose & Repair",
        provider_override="anthropic",
        model_override="claude-3-5-sonnet-latest",
        role="""You are an operations agent specialized in diagnosing system issues and applying fixes.

Your process:
1. Analyze the alert/signal context provided
2. Use available tools to gather more data about the system state
3. Identify the root cause
4. Fetch relevant internal runbooks or documentation if the component is unknown or complex
5. Propose a fix
6. If the fix is safe (low blast radius), apply it
7. If the fix is risky, report back with your analysis and recommendation

Always explain your reasoning step by step. Never execute destructive operations without confirmation.""",
        tools=[
            ToolDefinition(
                name="get_system_metrics",
                description="Get current metrics for a system component (CPU, memory, disk, network)",
                parameters={"type": "object", "properties": {"component": {"type": "string"}, "metric": {"type": "string"}}, "required": ["component"]},
            ),
            ToolDefinition(
                name="get_recent_logs",
                description="Retrieve recent log entries for a service",
                parameters={"type": "object", "properties": {"service": {"type": "string"}, "lines": {"type": "integer", "default": 50}, "severity": {"type": "string"}}, "required": ["service"]},
            ),
            ToolDefinition(
                name="execute_remediation",
                description="Execute a safe remediation action (restart, scale, clear cache)",
                parameters={"type": "object", "properties": {"action": {"type": "string"}, "target": {"type": "string"}, "parameters": {"type": "object"}}, "required": ["action", "target"]},
            ),
            ToolDefinition(
                name="read_internal_runbook",
                description="Fetch internal documentation or troubleshooting guides for a specific service.",
                parameters={
                    "type": "object",
                    "properties": {
                        "service_name": {"type": "string", "description": "The exact name of the service or component to look up."}
                    },
                    "required": ["service_name"],
                },
            ),
        ],
        max_steps=8,
    ),
    "escalate": AgentTemplate(
        id="escalate",
        name="Escalate",
        provider_override="openai",
        model_override="gpt-4o",
        role="""You are an escalation agent. Your job is to:
1. Summarize the situation clearly and concisely
2. Assess severity and business impact
3. Identify who should be notified
4. Draft a clear escalation message
5. Send the notification through the appropriate channel

Be direct. Lead with impact, then cause, then recommended action.""",
        tools=[
            ToolDefinition(
                name="send_notification",
                description="Send a notification to a channel (Slack, email, PagerDuty)",
                parameters={
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string", "enum": ["slack", "email", "pagerduty"]},
                        "target": {"type": "string", "description": "Channel name, email, or service ID"},
                        "message": {"type": "string"},
                        "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                    },
                    "required": ["channel", "target", "message"],
                },
            ),
        ],
        max_steps=4,
    ),
    "take_action": AgentTemplate(
        id="take_action",
        name="Take Action",
        provider_override="google",
        model_override="gemini-3.1-pro-preview",
        role="""You are a general-purpose operations agent. You handle tasks that don't fit
into diagnosis or escalation: data lookups, status checks, report generation,
API calls, and coordination tasks.

Use the available tools to complete the user's request. Report results clearly.""",
        tools=[
            ToolDefinition(
                name="call_api",
                description="Make an API call to an internal or external service",
                parameters={
                    "type": "object",
                    "properties": {
                        "method": {"type": "string", "enum": ["GET", "POST", "PUT"]},
                        "url": {"type": "string"},
                        "body": {"type": "object"},
                    },
                    "required": ["method", "url"],
                },
            ),
            ToolDefinition(
                name="generate_report",
                description="Generate a formatted report from data",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "data": {"type": "object"},
                        "format": {"type": "string", "enum": ["text", "csv", "json"]},
                    },
                    "required": ["title", "data"],
                },
            ),
        ],
        max_steps=6,
    ),
}


def load_agent_templates(directory: str | None = None) -> dict[str, AgentTemplate]:
    import os
    import yaml as yaml_lib
    from src.plugins.registry import get_registry

    dir_path = directory or settings.agent_templates_dir
    templates = dict(AGENT_TEMPLATES)  # Start with built-in defaults
    if not os.path.isdir(dir_path):
        return templates
    for filename in sorted(os.listdir(dir_path)):
        if not filename.endswith((".yaml", ".yml")):
            continue
        filepath = os.path.join(dir_path, filename)
        try:
            with open(filepath, "r") as f:
                defn = yaml_lib.safe_load(f)
            if not defn or "id" not in defn:
                continue
            provider_override = model_override = None
            if "provider_override" in defn:
                parts = defn["provider_override"].split("/", 1)
                provider_override = parts[0]
                model_override = parts[1] if len(parts) > 1 else None
            tool_defs = []
            registry = get_registry()
            for tool_name in defn.get("tools", []):
                tool = registry.get(tool_name)
                if tool:
                    td = tool.to_tool_definition()
                    tool_defs.append(ToolDefinition(
                        name=td["name"],
                        description=td["description"],
                        parameters=td["parameters"],
                    ))
            template = AgentTemplate(
                id=defn["id"],
                name=defn.get("name", defn["id"]),
                description=defn.get("description", ""),
                role=defn.get("system_prompt", ""),
                tools=tool_defs,
                max_steps=defn.get("max_steps", 8),
                provider_override=provider_override,
                model_override=model_override,
            )
            templates[defn["id"]] = template
            logger.info(f"Loaded agent template: {defn['id']} from {filepath}")
        except Exception as e:
            logger.error(f"Failed to load agent template {filepath}: {e}")
    return templates


def get_template(agent_type: str) -> AgentTemplate | None:
    return AGENT_TEMPLATES.get(agent_type)


def list_templates() -> list[AgentTemplate]:
    return list(AGENT_TEMPLATES.values())


import re

# Strict pattern: alphanumeric, underscores, hyphens only
_SAFE_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_\-]+$")


async def default_tool_executor(tool_name: str, arguments: dict) -> str:
    """
    Production tool executor. Dispatches LLM tool calls via the ToolRegistry.
    Each tool's execution_mode determines local vs gateway dispatch.
    """
    registry = get_registry()

    if not _SAFE_TOOL_NAME.match(tool_name):
        logger.warning(f"SECURITY: Tool name contains illegal characters: '{tool_name}'")
        return json.dumps({"error": f"Invalid tool name: {tool_name}"})

    try:
        result = await registry.execute(tool_name, arguments)
        return json.dumps(result) if isinstance(result, dict) else str(result)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        logger.error(f"Tool execution failed: {tool_name}: {e}")
        return json.dumps({"error": f"Tool execution failed: {str(e)}"})


async def execute_agent(
    agent_type: str,
    objective: str,
    context: str,
    input_id: str | None = None,
    tool_executor: callable | None = None,
    conversation_id: str | None = None,
) -> AgentExecution:
    template = get_template(agent_type)
    if not template:
        raise ValueError(f"Unknown agent type: {agent_type}")

    execution = AgentExecution(
        id=new_id(),
        agent_type=agent_type,
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
        f"Agent '{template.name}' started: {objective}",
        input_id=input_id,
        execution_id=execution.id,
    )

    try:
        # Dynamically fetch the primary adapter for this template
        current_provider = template.provider_override or settings.agent_provider
        current_model = template.model_override or settings.agent_model
        adapter = get_agent_adapter(current_provider, current_model)
        
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
        system_prompt = template.role
        from src.tools.python.knowledge_search import _retriever
        if _retriever:
            try:
                memories = await _retriever.search(query=objective, source_type="agent_memory", top_k_final=settings.agent_memory_recall_count)
                if memories:
                    memory_text = "\n\n".join(f"- {m.content} (score: {m.score:.2f})" for m in memories)
                    system_prompt = system_prompt + f"\n\nRelevant past experience:\n{memory_text}"
            except Exception as e:
                logger.warning(f"Memory recall failed: {e}")

        for step in range(template.max_steps):
            # 1. Kill switch check
            if await redis_store.is_killed():
                execution.status = ExecutionStatus.HALTED
                execution.result = "Halted by kill switch"
                await audit.log(
                    "AGENT_HALTED",
                    f"Agent '{template.name}' halted by kill switch at step {step + 1}",
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

            # 3. LLM Reasoning Call with Execution Failover
            try:
                response = await adapter.complete(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=template.tools,
                )
            except Exception as llm_error:
                fallback_provider = settings.agent_provider
                fallback_model = settings.agent_model

                # If we are already using the fallback, there is nowhere to fail over to
                if current_provider == fallback_provider and current_model == fallback_model:
                    raise Exception(f"Primary provider {current_provider} failed and no secondary fallback exists: {str(llm_error)}")

                logger.warning(
                    f"Provider outage ({current_provider}) for agent {agent_type}. "
                    f"Initiating failover to fallback config ({fallback_provider} / {fallback_model}). Error: {str(llm_error)}"
                )

                await audit.log(
                    "AGENT_FAILOVER",
                    f"Provider {current_provider} failed. Failing over to {fallback_provider}.",
                    execution_id=execution.id,
                )

                # Swap state to fallback configuration
                current_provider = fallback_provider
                current_model = fallback_model
                adapter = get_agent_adapter(current_provider, current_model)

                # Retry the exact same prompt against the fallback provider
                response = await adapter.complete(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=template.tools,
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
                    tool_result = await default_tool_executor(tc["name"], tc["arguments"])

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
                    f"Agent '{template.name}' completed in {step + 1} steps",
                    execution_id=execution.id,
                )
                break
        else:
            execution.status = ExecutionStatus.COMPLETED
            execution.result = f"Agent reached maximum steps ({template.max_steps})."
            execution.completed_at = datetime.utcnow()

    except Exception as e:
        execution.status = ExecutionStatus.FAILED
        execution.result = f"Agent error: {str(e)}"
        logger.exception(f"Agent execution failed: {e}")
        await audit.log(
            "AGENT_FAILED",
            f"Agent '{template.name}' failed: {str(e)}",
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
                f"# Agent Execution: {agent_type}\n"
                f"## Trigger\n{objective}\n"
                f"## Actions Taken\n{', '.join(actions)}\n"
                f"## Conclusion\n{execution.result}\n"
            )
            await ingest_text(
                embedder=_retriever.embedder,
                content=memory_text,
                source_type="agent_memory",
                source_path=f"agent/{execution.id}",
                title=f"{agent_type} - {objective[:100]}",
                extra_metadata={
                    "agent_template": agent_type,
                    "execution_id": execution.id,
                    "conversation_id": conversation_id or "",
                },
            )
    except Exception as e:
        logger.warning(f"Failed to persist agent memory: {e}")

    return execution