from __future__ import annotations
import logging
import asyncio
import httpx
from datetime import datetime
from src.config import settings
from src.models.schemas import (
    AgentTemplate, AgentExecution, ExecutionStatus, ChatMessage, ToolDefinition, new_id,
)
from src.adapters.registry import get_agent_adapter
from src.persistence import redis_store, audit

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


def get_template(agent_type: str) -> AgentTemplate | None:
    return AGENT_TEMPLATES.get(agent_type)


def list_templates() -> list[AgentTemplate]:
    return list(AGENT_TEMPLATES.values())


import re

# Build whitelist of valid tool names from all agent templates at import time
_VALID_TOOL_NAMES: set[str] | None = None

def _get_valid_tool_names() -> set[str]:
    global _VALID_TOOL_NAMES
    if _VALID_TOOL_NAMES is None:
        _VALID_TOOL_NAMES = set()
        for template in AGENT_TEMPLATES.values():
            for tool in template.tools:
                _VALID_TOOL_NAMES.add(tool.name)
    return _VALID_TOOL_NAMES

# Strict pattern: alphanumeric, underscores, hyphens only
_SAFE_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_\-]+$")


async def default_tool_executor(tool_name: str, arguments: dict) -> str:
    """
    Production tool executor. Routes LLM tool calls to the MCP gateway's
    single /execute endpoint. The gateway dispatches internally by tool_name.
    """
    # SECURITY: reject tool names not in whitelist
    valid_names = _get_valid_tool_names()
    if tool_name not in valid_names:
        logger.warning(f"SECURITY: LLM requested unknown tool '{tool_name}'. Rejected.")
        return f'{{"error": "Unknown tool: {tool_name}. Not in allowed set."}}'

    # SECURITY: reject path traversal characters even if name passed whitelist
    if not _SAFE_TOOL_NAME.match(tool_name):
        logger.warning(f"SECURITY: Tool name contains illegal characters: '{tool_name}'")
        return '{"error": "Invalid tool name format."}'

    if not settings.mcp_server_url:
        logger.error("MCP_SERVER_URL not configured. Cannot execute tool calls.")
        return '{"error": "MCP gateway not configured. Set MCP_SERVER_URL in .env."}'

    try:
        headers = {}
        if settings.mcp_shared_secret:
            headers["Authorization"] = f"Bearer {settings.mcp_shared_secret}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{settings.mcp_server_url}/execute",
                json={"tool_name": tool_name, "arguments": arguments},
                headers=headers,
            )
            response.raise_for_status()
            return response.text
    except httpx.HTTPStatusError as e:
        logger.error(f"Tool execution HTTP error [{tool_name}]: {e.response.status_code} {e.response.text[:200]}")
        return f'{{"error": "Gateway returned {e.response.status_code} for tool {tool_name}."}}'
    except httpx.HTTPError as e:
        logger.error(f"Tool execution network failure [{tool_name}]: {e}")
        return f'{{"error": "Execution network failure: {str(e)}"}}'
    except Exception as e:
        logger.error(f"Tool execution critical failure [{tool_name}]: {e}")
        return f'{{"error": "Internal error: {str(e)}"}}'


async def execute_agent(
    agent_type: str,
    objective: str,
    context: str,
    input_id: str | None = None,
    tool_executor: callable | None = None,
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
                    system_prompt=template.role,
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
                    system_prompt=template.role,
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

    return execution