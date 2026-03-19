from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.schemas import (
    WebhookSignal, OrchestratorInput, SignalSource,
    ApprovalDecision, Rule, new_id,
)
from src.persistence import audit
from src.persistence.database import get_session, SecurityAlertModel
from src.persistence.redis_store import (
    activate_kill_switch, deactivate_kill_switch, is_killed, enqueue_signal
)
from src.guardrails.approvals import (
    resolve_approval, get_pending_approvals,
)
from src.rules.engine import get_rules, add_rule, remove_rule
from src.agents.registry import AgentRegistry
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


# =============================================================================
# AGENTS - CRUD
# =============================================================================

class AgentCreateRequest(BaseModel):
    name: str = Field(..., max_length=100)
    display_name: str = Field(..., max_length=200)
    description: str = ""
    system_prompt: str = ""
    model: str = Field(..., max_length=100)
    tools: list[str] = Field(default_factory=list)
    max_steps: int = Field(default=8, ge=1, le=50)
    max_concurrent: int = Field(default=3, ge=1, le=20)


class AgentUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    tools: Optional[list[str]] = None
    max_steps: Optional[int] = Field(default=None, ge=1, le=50)
    max_concurrent: Optional[int] = Field(default=None, ge=1, le=20)
    is_active: Optional[bool] = None


class AlertStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(acknowledged|resolved|false-positive)$")


def _agent_to_dict(agent) -> dict:
    return {
        "id": agent.id,
        "name": agent.name,
        "display_name": agent.display_name,
        "description": agent.description,
        "model": agent.model,
        "tools": agent.tools,
        "max_steps": agent.max_steps,
        "max_concurrent": agent.max_concurrent,
        "is_active": agent.is_active,
        "is_system": agent.is_system,
    }


@router.get("/agents")
async def list_agents(session: AsyncSession = Depends(get_session)):
    registry = AgentRegistry(session)
    agents = await registry.list_active()
    return [_agent_to_dict(a) for a in agents]


@router.post("/agents", status_code=201)
async def create_agent(
    req: AgentCreateRequest,
    session: AsyncSession = Depends(get_session),
):
    registry = AgentRegistry(session)
    existing = await registry.get_by_name(req.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Agent '{req.name}' already exists.")
    agent = await registry.create(
        name=req.name,
        display_name=req.display_name,
        description=req.description,
        system_prompt=req.system_prompt,
        model=req.model,
        tools=req.tools,
        max_steps=req.max_steps,
        max_concurrent=req.max_concurrent,
    )
    await session.commit()
    await audit.log("AGENT_CREATED", f"Agent '{req.name}' created via API")
    return {"id": agent.id, "name": agent.name}


@router.put("/agents/{name}")
async def update_agent(
    name: str,
    req: AgentUpdateRequest,
    session: AsyncSession = Depends(get_session),
):
    registry = AgentRegistry(session)
    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")
    agent = await registry.update(name, **updates)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found.")
    await session.commit()
    await audit.log("AGENT_UPDATED", f"Agent '{name}' updated via API")
    return _agent_to_dict(agent)


@router.delete("/agents/{name}")
async def delete_agent(
    name: str,
    session: AsyncSession = Depends(get_session),
):
    registry = AgentRegistry(session)
    agent = await registry.get_by_name(name)
    if agent is None:
        raise HTTPException(status_code=400, detail=f"Agent '{name}' not found.")
    if agent.is_system:
        raise HTTPException(status_code=400, detail=f"Cannot delete system agent '{name}'.")
    deleted = await registry.delete(name)
    await session.commit()
    await audit.log("AGENT_DELETED", f"Agent '{name}' deleted via API")
    return {"status": "deleted", "name": name}


# =============================================================================
# SECURITY ALERTS
# =============================================================================

@router.get("/security/alerts")
async def list_security_alerts(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(SecurityAlertModel)
        .where(SecurityAlertModel.status == "OPEN")
        .order_by(SecurityAlertModel.created_at.desc())
        .limit(50)
    )
    alerts = result.scalars().all()
    return [
        {
            "id": a.id,
            "severity": a.severity,
            "alert_type": a.alert_type,
            "description": a.description,
            "source_agent": a.source_agent,
            "status": a.status,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in alerts
    ]


@router.post("/security/alerts/{alert_id}")
async def update_alert_status(
    alert_id: str,
    req: AlertStatusUpdate,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(SecurityAlertModel).where(SecurityAlertModel.id == alert_id)
    )
    alert = result.scalars().first()
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")

    status_map = {
        "acknowledged": "ACKNOWLEDGED",
        "resolved": "RESOLVED",
        "false-positive": "FALSE_POSITIVE",
    }
    alert.status = status_map[req.status]
    if req.status == "resolved":
        alert.resolved_at = datetime.now(timezone.utc)
    await session.commit()
    await audit.log("SECURITY_ALERT_UPDATED", f"Alert {alert_id} -> {alert.status}")
    return {"id": alert_id, "status": alert.status}