from __future__ import annotations
import json
import logging
from fastapi import APIRouter, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from src.config import settings
from src.models.schemas import (
    ChatRequest, WebhookSignal, OrchestratorInput, SignalSource,
    ApprovalDecision, Rule, new_id,
)
from src.orchestrator.core import process
from src.persistence import conversations, audit
from src.persistence.redis_store import (
    activate_kill_switch, deactivate_kill_switch, is_killed, enqueue_signal
)
from src.guardrails.approvals import (
    resolve_approval, get_pending_approvals,
)
from src.rules.engine import get_rules, add_rule, remove_rule
from src.agents.runtime import list_templates
from src.orchestrator.concurrency import get_pool
from src.automation.workflows import list_workflows

logger = logging.getLogger("stourio.api")

# Security Dependency
api_key_header = APIKeyHeader(name="X-STOURIO-KEY", auto_error=False)

async def get_api_key(header_key: str = Security(api_key_header)):
    if not settings.stourio_api_key:
        raise HTTPException(
            status_code=503,
            detail="STOURIO_API_KEY not configured. Run: python3 scripts/generate_key.py"
        )
    if not header_key:
        raise HTTPException(
            status_code=401,
            detail="Missing X-STOURIO-KEY header."
        )
    if header_key != settings.stourio_api_key:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Invalid Stourio API Key."
        )
    return header_key

router = APIRouter(dependencies=[Depends(get_api_key)])


# =============================================================================
# CHAT - Human input channel
# =============================================================================

@router.post("/chat")
async def chat(req: ChatRequest):
    conv_id = req.conversation_id or new_id()
    await conversations.save_message(conv_id, "user", req.message)

    signal = OrchestratorInput(
        source=SignalSource.USER,
        content=req.message,
        conversation_id=conv_id,
    )

    result = await process(signal)

    response_text = result.get("message", "")
    await conversations.save_message(conv_id, "assistant", response_text)

    return {
        "conversation_id": conv_id,
        **result,
    }


# =============================================================================
# WEBHOOK - System signal channel (Queue Decoupled)
# =============================================================================

@router.post("/webhook", status_code=202)
async def webhook(signal: WebhookSignal):
    """Ingest signal to Redis stream. Return immediately to prevent blocking."""
    payload = signal.model_dump()
    await enqueue_signal(payload)
    return {"status": "queued", "message": "Signal accepted for correlation."}


# =============================================================================
# APPROVALS - Human-in-the-loop
# =============================================================================

@router.get("/approvals")
async def list_approvals():
    return await get_pending_approvals()


@router.post("/approvals/{approval_id}")
async def decide_approval(approval_id: str, decision: ApprovalDecision):
    result = await resolve_approval(approval_id, decision)
    if result is None:
        raise HTTPException(
            status_code=410,
            detail="Approval expired. Target state assumed mutated. Action auto-rejected.",
        )

    if result.status == "approved":
        # Recover original routing context stored at approval time
        agent_type = "take_action"
        objective = result.action_description
        context = "Execution authorized via manual override."
        try:
            routing_ctx = json.loads(result.blast_radius)
            agent_type = routing_ctx.get("agent_type", agent_type)
            objective = routing_ctx.get("objective", objective)
            context = routing_ctx.get("original_content", context)
        except (json.JSONDecodeError, TypeError):
            pass  # Fall back to defaults if blast_radius isn't JSON

        exec_result = await get_pool().execute(
            agent_type=agent_type,
            objective=objective,
            context=context,
            input_id=result.original_input_id,
        )
        
        return {
            "status": "approved_and_executed",
            "execution_id": exec_result.id,
            "approval_id": approval_id,
        }

    return {
        "status": "rejected",
        "message": "Action rejected.",
        "approval_id": approval_id,
    }


# =============================================================================
# KILL SWITCH
# =============================================================================

@router.post("/kill")
async def kill():
    await activate_kill_switch()
    await audit.log("KILL_SWITCH_ACTIVATED", "Manual activation via API")
    return {"status": "killed", "message": "All operations halted."}


@router.post("/resume")
async def resume():
    await deactivate_kill_switch()
    await audit.log("KILL_SWITCH_DEACTIVATED", "Manual deactivation via API")
    return {"status": "operational", "message": "Operations resumed."}


# =============================================================================
# RULES
# =============================================================================

@router.get("/rules")
async def list_rules():
    rules = await get_rules()
    return [r.model_dump() for r in rules]


@router.post("/rules")
async def create_rule(rule: Rule):
    created = await add_rule(rule)
    await audit.log("RULE_CREATED", f"Rule '{rule.name}' created")
    return created.model_dump()


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str):
    removed = await remove_rule(rule_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Rule not found")
    await audit.log("RULE_DELETED", f"Rule {rule_id} deleted")
    return {"status": "deleted", "rule_id": rule_id}


# =============================================================================
# STATUS & AUDIT
# =============================================================================

@router.get("/status")
async def status():
    killed = await is_killed()
    approvals = await get_pending_approvals()
    return {
        "status": "killed" if killed else "operational",
        "kill_switch": killed,
        "pending_approvals": len(approvals),
        "agents": [t.model_dump() for t in list_templates()],
        "workflows": [w.model_dump() for w in list_workflows()],
        "agent_pool": get_pool().status(),
    }


@router.get("/audit")
async def audit_log(limit: int = 50):
    return await audit.get_recent(limit=limit)


# =============================================================================
# DOCUMENTS - RAG ingestion
# =============================================================================

@router.post("/documents/ingest")
async def ingest_documents(api_key: str = Depends(get_api_key)):
    from src.rag.embeddings.openai_embedder import OpenAIEmbedder
    from src.rag.ingestion import ingest_runbooks
    embedder = OpenAIEmbedder(api_key=settings.openai_api_key, model=settings.embedding_model)
    count = await ingest_runbooks(embedder)
    return {"status": "ok", "chunks_ingested": count}


# =============================================================================
# USAGE - Token/cost tracking
# =============================================================================

@router.get("/usage")
async def get_usage(api_key: str = Depends(get_api_key), from_date: str | None = None, to_date: str | None = None):
    from src.tracking.tracker import get_usage_summary
    summary = await get_usage_summary(from_date=from_date, to_date=to_date)
    return {"usage": summary}


@router.get("/usage/summary")
async def get_usage_summary_endpoint(api_key: str = Depends(get_api_key), group_by: str = "agent_template"):
    from src.tracking.tracker import get_usage_summary
    summary = await get_usage_summary(group_by=group_by)
    return {"summary": summary}