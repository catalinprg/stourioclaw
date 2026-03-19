from __future__ import annotations
import json
import logging
from src.models.schemas import (
    OrchestratorInput, OrchestratorResponse, RoutingDecision,
    RiskLevel, RuleAction, SignalSource, ChatMessage, ToolDefinition, new_id,
)
from src.adapters.registry import get_orchestrator_adapter
from src.rules.engine import get_rules, evaluate
from src.guardrails.approvals import create_approval_request, check_kill_switch
from src.agents.runtime import list_templates
from src.orchestrator.concurrency import get_pool
from src.automation.workflows import execute_workflow, list_workflows
from src.orchestrator.chains import execute_chain, list_chains
from src.persistence import audit
from src.telemetry import tracer

logger = logging.getLogger("stourio.orchestrator")


SYSTEM_PROMPT = """You are Stourio, an AI operations orchestrator. Your job is to analyze incoming
signals (user requests or system events) and decide the best course of action.

You have three types of capabilities:
1. AI AGENTS - for dynamic, novel, or complex situations requiring reasoning
2. AUTOMATION - for known patterns with predefined workflows
3. CHAINS - for complex workflows requiring multiple agents in sequence or parallel

Available agent types: {agent_types}
Available automation workflows: {workflow_ids}
Available chains: {chain_ids}

For each input, you MUST respond by calling exactly one of these tools:
- route_to_agent: when the situation needs reasoning, diagnosis, or adaptive response
- route_to_automation: when a known workflow matches the situation
- route_to_chain: when the situation requires multiple agents working in sequence or parallel
- respond_directly: when you can answer the user without taking action
- request_more_info: when the input is ambiguous and you need clarification

Consider the risk level of any action. If an action could affect production systems,
flag it as high-risk so the guardrails layer can request human approval.

Be concise. Prioritize resolution over explanation."""


ROUTING_TOOLS = [
    ToolDefinition(
        name="route_to_agent",
        description="Route to an AI agent for reasoning-heavy, dynamic, or novel tasks",
        parameters={
            "type": "object",
            "properties": {
                "agent_type": {
                    "type": "string",
                    "enum": ["diagnose_repair", "escalate", "take_action"],
                    "description": "Which agent template to use",
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
        name="route_to_automation",
        description="Route to a predefined automation workflow for known patterns",
        parameters={
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "Which automation workflow to trigger",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why this workflow matches",
                },
            },
            "required": ["workflow_id", "reasoning"],
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
    ToolDefinition(
        name="route_to_chain",
        description="Route to a multi-agent chain for complex workflows requiring multiple agents",
        parameters={
            "type": "object",
            "properties": {
                "chain_name": {"type": "string", "description": "Name of the chain to execute"},
                "context": {"type": "string", "description": "Additional context for the chain"},
            },
            "required": ["chain_name"],
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

        if matched_rule.action == RuleAction.TRIGGER_AUTOMATION:
            if matched_rule.automation_id:
                result = await execute_workflow(
                    matched_rule.automation_id,
                    trigger_context=signal.content,
                    input_id=signal.id,
                )
                return {
                    "status": result.status.value,
                    "message": result.result,
                    "execution_id": result.id,
                    "type": "automation",
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

        if matched_rule.action == RuleAction.FORCE_CHAIN:
            chain_name = matched_rule.config.get("chain_name")
            result = await execute_chain(
                chain_name=chain_name,
                context={"signal": signal.content, "rule_id": matched_rule.id},
                input_id=signal.id,
                conversation_id=signal.conversation_id,
            )
            return {"status": "ok", "message": result.get("summary", ""), "execution_id": result.get("id", ""), "type": "chain"}

    # --- Step 2: LLM routing ---
    adapter = get_orchestrator_adapter()

    agent_types = ", ".join(t.id for t in list_templates())
    workflow_ids = ", ".join(w.id for w in list_workflows())
    chain_ids = ", ".join(c.name for c in list_chains())

    system = SYSTEM_PROMPT.format(agent_types=agent_types, workflow_ids=workflow_ids, chain_ids=chain_ids)
    messages = [ChatMessage(role="user", content=signal.content)]

    try:
        response = await adapter.complete(
            system_prompt=system,
            messages=messages,
            tools=ROUTING_TOOLS,
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

    # --- route_to_automation ---
    if tool_name == "route_to_automation":
        workflow_id = args.get("workflow_id", "")
        result = await execute_workflow(
            workflow_id,
            trigger_context=signal.content,
            input_id=signal.id,
        )
        return {
            "status": result.status.value,
            "message": result.result,
            "execution_id": result.id,
            "type": "automation",
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

    # --- route_to_chain ---
    if tool_name == "route_to_chain":
        chain_name = args.get("chain_name", "")
        extra_context = args.get("context", "")
        result = await execute_chain(
            chain_name=chain_name,
            context={"signal": signal.content, "context": extra_context},
            input_id=signal.id,
            conversation_id=signal.conversation_id,
        )
        return {
            "status": "ok",
            "message": result.get("summary", ""),
            "execution_id": result.get("id", ""),
            "type": "chain",
            "chain": chain_name,
            "steps": result.get("steps", {}),
        }

    # Unknown tool call
    return {
        "status": "error",
        "message": f"Unknown routing decision: {tool_name}",
    }
