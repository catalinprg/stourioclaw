from __future__ import annotations
import json
import logging
from typing import Sequence
from src.models.schemas import (
    OrchestratorInput, RiskLevel, RuleAction, ChatMessage, ToolDefinition,
)
from src.adapters.registry import get_orchestrator_adapter
from src.rules.engine import get_rules, evaluate
from src.guardrails.approvals import create_approval_request, check_kill_switch
from src.agents.registry import AgentRegistry
from src.orchestrator.concurrency import get_pool
from src.persistence import audit
from src.persistence.database import get_session
from src.telemetry import tracer

logger = logging.getLogger("stourio.orchestrator")


SYSTEM_PROMPT = """You are Stourio, an AI operations orchestrator. Your job is to analyze incoming
signals (user requests or system events) and decide the best course of action.

Available agents: {agent_descriptions}

For each input, you MUST respond by calling exactly one of these tools:
- route_to_agent: when the situation needs reasoning, diagnosis, or adaptive response
- respond_directly: when you can answer the user without taking action
- request_more_info: when the input is ambiguous and you need clarification

Consider the risk level of any action. If an action could affect production systems,
flag it as high-risk so the guardrails layer can request human approval.

Be concise. Prioritize resolution over explanation."""


def build_routing_tools(agents: Sequence) -> list[ToolDefinition]:
    """Build routing tools dynamically from a list of agent objects.

    Each agent must have .name and .description attributes.
    The route_to_agent tool's agent_type enum is built from agent names.
    """
    agent_names = [a.name for a in agents]
    # Build description string so LLM understands routing options
    agent_desc_parts = [f"{a.name}: {a.description}" for a in agents]
    agent_type_description = (
        "Which agent to route to. Options: " + "; ".join(agent_desc_parts)
        if agent_desc_parts
        else "Which agent to route to"
    )

    return [
        ToolDefinition(
            name="route_to_agent",
            description="Route to an AI agent for reasoning-heavy, dynamic, or novel tasks",
            parameters={
                "type": "object",
                "properties": {
                    "agent_type": {
                        "type": "string",
                        "enum": agent_names,
                        "description": agent_type_description,
                    },
                    "objective": {
                        "type": "string",
                        "description": "Clear objective for the agent",
                    },
                    "risk_level": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Why this routing decision was made",
                    },
                },
                "required": ["agent_type", "objective", "risk_level", "reasoning"],
            },
        ),
        ToolDefinition(
            name="respond_directly",
            description="Respond to the user directly without taking any action",
            parameters={
                "type": "object",
                "properties": {
                    "response": {"type": "string"},
                    "reasoning": {"type": "string"},
                },
                "required": ["response"],
            },
        ),
        ToolDefinition(
            name="request_more_info",
            description="Ask the user for clarification before proceeding",
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "reasoning": {"type": "string"},
                },
                "required": ["question"],
            },
        ),
    ]


async def process(signal: OrchestratorInput) -> dict:
    """
    Main orchestration loop:
    1. Check kill switch
    2. Run rules engine (deterministic, pre-LLM)
    3. If no rule matched, ask the LLM to route
    4. Execute the routing decision
    5. Return result
    """
    span = tracer.start_span("orchestrator_process")
    span.set_attribute("signal.id", signal.id)
    span.set_attribute("signal.source", signal.source.value)

    try:
        return await _process_inner(signal, span)
    except Exception as e:
        span.set_attribute("error", True)
        span.set_attribute("error.message", str(e))
        raise
    finally:
        span.end()


async def _process_inner(signal: OrchestratorInput, span) -> dict:

    # --- Step 0: Kill switch ---
    if await check_kill_switch():
        await audit.log(
            "ORCHESTRATOR_BLOCKED",
            "Signal rejected: kill switch active",
            input_id=signal.id,
        )
        return {
            "status": "halted",
            "message": "System is halted. Kill switch is active. Deactivate before sending commands.",
        }

    await audit.log(
        "SIGNAL_RECEIVED",
        f"Source: {signal.source.value} | Content: {signal.content[:200]}",
        input_id=signal.id,
    )

    # --- Step 1: Deterministic rules engine ---
    rules = await get_rules()
    matched_rule = evaluate(signal.content, rules)

    if matched_rule:
        await audit.log(
            "RULE_MATCHED",
            f"Rule '{matched_rule.name}' matched -> {matched_rule.action.value}",
            input_id=signal.id,
        )

        if matched_rule.action == RuleAction.HARD_REJECT:
            await audit.log(
                "RULE_REJECTED",
                f"Hard reject by rule '{matched_rule.name}'",
                input_id=signal.id,
                risk_level=matched_rule.risk_level,
            )
            return {
                "status": "rejected",
                "message": f"Blocked by rule: {matched_rule.name}. This action is not allowed.",
                "rule": matched_rule.name,
            }

        if matched_rule.action == RuleAction.REQUIRE_APPROVAL:
            approval = await create_approval_request(
                action_description=signal.content,
                risk_level=matched_rule.risk_level,
                blast_radius="Defined by rule: " + matched_rule.name,
                reasoning=f"Intercepted by rule '{matched_rule.name}'",
                input_id=signal.id,
            )
            return {
                "status": "awaiting_approval",
                "message": f"High-risk action detected. Approval required.",
                "approval_id": approval.id,
                "risk_level": matched_rule.risk_level.value,
            }

        if matched_rule.action == RuleAction.FORCE_AGENT:
            agent_type = matched_rule.config.get("agent_type", "diagnose_repair")
            result = await get_pool().execute(
                agent_type=agent_type,
                objective=signal.content,
                context={"rule_id": matched_rule.id, "source": "forced"},
                input_id=signal.id,
                conversation_id=signal.conversation_id,
            )
            return {"status": "ok", "message": result.result, "execution_id": result.id, "type": "agent"}

    # --- Step 2: LLM routing ---
    adapter = get_orchestrator_adapter()

    async with get_session() as session:
        registry = AgentRegistry(session)
        routable_agents = await registry.list_routable()

    routing_tools = build_routing_tools(routable_agents)
    agent_descriptions = "; ".join(f"{a.name}: {a.description}" for a in routable_agents) or "none"

    system = SYSTEM_PROMPT.format(agent_descriptions=agent_descriptions)
    messages = [ChatMessage(role="user", content=signal.content)]

    try:
        response = await adapter.complete(
            system_prompt=system,
            messages=messages,
            tools=routing_tools,
        )
    except Exception as e:
        logger.exception(f"LLM routing failed: {e}")
        await audit.log(
            "LLM_ROUTING_FAILED",
            f"LLM error: {str(e)}. Falling back to direct response.",
            input_id=signal.id,
        )
        return {
            "status": "error",
            "message": f"Routing failed: {str(e)}. Please try again or check your LLM provider configuration.",
        }

    # --- Step 3: Parse and execute routing decision ---

    if not response.has_tool_call:
        # LLM responded with text directly (no tool call)
        await audit.log(
            "ORCHESTRATOR_DIRECT_RESPONSE",
            "LLM responded without routing",
            input_id=signal.id,
        )
        return {
            "status": "completed",
            "message": response.text or "No response generated.",
            "type": "direct",
        }

    tc = response.first_tool_call
    tool_name = tc["name"]
    args = tc["arguments"]

    await audit.log(
        "ORCHESTRATOR_ROUTED",
        f"Routed to: {tool_name} | Args: {json.dumps(args)[:300]}",
        input_id=signal.id,
    )

    # --- respond_directly ---
    if tool_name == "respond_directly":
        return {
            "status": "completed",
            "message": args.get("response", ""),
            "type": "direct",
            "reasoning": args.get("reasoning", ""),
        }

    # --- request_more_info ---
    if tool_name == "request_more_info":
        return {
            "status": "needs_info",
            "message": args.get("question", "Could you provide more details?"),
            "reasoning": args.get("reasoning", ""),
        }

    # --- route_to_agent ---
    if tool_name == "route_to_agent":
        agent_type = args.get("agent_type", "take_action")
        risk_level = RiskLevel(args.get("risk_level", "low"))

        # High/critical risk: require approval first
        if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            approval = await create_approval_request(
                action_description=f"Agent '{agent_type}': {args.get('objective', '')}",
                risk_level=risk_level,
                blast_radius=json.dumps({
                    "agent_type": agent_type,
                    "objective": args.get("objective", ""),
                    "original_content": signal.content,
                }),
                reasoning=args.get("reasoning", ""),
                input_id=signal.id,
            )
            return {
                "status": "awaiting_approval",
                "message": f"Agent action requires approval. Risk: {risk_level.value}",
                "approval_id": approval.id,
                "agent_type": agent_type,
                "objective": args.get("objective", ""),
                "risk_level": risk_level.value,
            }

        # Check if target agent is a daemon — route to inbox if running
        async with get_session() as sess:
            _reg = AgentRegistry(sess)
            _target = await _reg.get_by_name(agent_type)

        if _target and _target.execution_mode == "daemon":
            from src.daemons.inbox import enqueue_message
            # Check if daemon is actually running (errata E1)
            daemon_running = False
            try:
                from src.daemons.manager import DaemonManager
                # Access the global manager — it's stored in main.py lifespan
                # For now, always enqueue (daemon wakes or oneshot fires via inbox handler)
                daemon_running = True  # Inbox enqueue + notify will wake it or trigger oneshot
            except Exception:
                pass

            entry_id = await enqueue_message(
                target_agent=agent_type,
                message=args.get("objective", signal.content),
                from_agent="orchestrator",
                context=signal.content,
            )
            return {
                "status": "routed_to_daemon",
                "message": f"Message delivered to daemon '{agent_type}' inbox.",
                "agent_type": agent_type,
                "entry_id": entry_id,
            }

        # Low/medium risk: execute immediately
        execution = await get_pool().execute(
            agent_type=agent_type,
            objective=args.get("objective", signal.content),
            context=signal.content,
            input_id=signal.id,
            conversation_id=signal.conversation_id,
        )
        return {
            "status": execution.status.value,
            "message": execution.result or "Agent completed.",
            "execution_id": execution.id,
            "type": "agent",
            "agent_type": agent_type,
            "steps": execution.steps,
            "reasoning": args.get("reasoning", ""),
        }

    # Unknown tool call
    return {
        "status": "error",
        "message": f"Unknown routing decision: {tool_name}",
    }
