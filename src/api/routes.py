from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.schemas import (
    WebhookSignal, OrchestratorInput, SignalSource,
    ApprovalDecision, Rule, new_id,
)
from src.persistence import audit
from src.persistence.database import get_session, SecurityAlertModel, McpServerRecord, async_session as db_async_session
from src.mcp.client import get_mcp_client_pool
from src.persistence.redis_store import (
    activate_kill_switch, deactivate_kill_switch, is_killed, enqueue_signal,
    publish_daemon_event,
)
from src.guardrails.approvals import (
    resolve_approval, get_pending_approvals,
)
from src.rules.engine import get_rules, add_rule, remove_rule
from src.agents.registry import AgentRegistry
from src.agents.runtime import list_templates
from src.orchestrator.concurrency import get_pool
from src.automation.workflows import list_workflows
from src.scheduler.models import CronJob
from src.scheduler.store import CronStore

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

    # Daemon status
    daemon_status = {}
    try:
        async with db_async_session() as sess:
            registry = AgentRegistry(sess)
            daemons = await registry.list_daemons()
            daemon_status = {d.name: {"execution_mode": "daemon", "active": d.is_active} for d in daemons}
    except Exception:
        pass

    # MCP server status
    mcp_status = {}
    try:
        pool = get_mcp_client_pool()
        for name in pool._connections:
            mcp_status[name] = {"connected": True, "tools_count": len(pool.get_tools(name))}
    except Exception:
        pass

    return {
        "status": "killed" if killed else "operational",
        "kill_switch": killed,
        "pending_approvals": len(approvals),
        "agents": [t.model_dump() for t in list_templates()],
        "workflows": [w.model_dump() for w in list_workflows()],
        "agent_pool": get_pool().status(),
        "daemons": daemon_status,
        "mcp_servers": mcp_status,
    }


@router.get("/audit")
async def audit_log(limit: int = 50):
    limit = min(limit, 500)
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
    execution_mode: str = Field(default="oneshot", pattern="^(oneshot|daemon)$")
    daemon_config: Optional[dict] = None
    mcp_servers: list[str] = Field(default_factory=list)
    allowed_peers: list[str] = Field(default_factory=list)


class AgentUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    tools: Optional[list[str]] = None
    max_steps: Optional[int] = Field(default=None, ge=1, le=50)
    max_concurrent: Optional[int] = Field(default=None, ge=1, le=20)
    is_active: Optional[bool] = None
    execution_mode: Optional[str] = Field(default=None, pattern="^(oneshot|daemon)$")
    daemon_config: Optional[dict] = None
    mcp_servers: Optional[list[str]] = None
    allowed_peers: Optional[list[str]] = None


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
        "execution_mode": agent.execution_mode,
        "daemon_config": agent.daemon_config,
        "mcp_servers": agent.mcp_servers,
        "allowed_peers": agent.allowed_peers,
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
        execution_mode=req.execution_mode,
        daemon_config=req.daemon_config,
        mcp_servers=req.mcp_servers,
        allowed_peers=req.allowed_peers,
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
# DAEMONS - Runtime control
# =============================================================================

@router.post("/daemons/{name}/start")
async def start_daemon(name: str, session: AsyncSession = Depends(get_session)):
    registry = AgentRegistry(session)
    agent = await registry.get_by_name(name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found.")
    if agent.execution_mode != "daemon":
        raise HTTPException(status_code=400, detail=f"Agent '{name}' is not a daemon.")
    await publish_daemon_event("start", name)
    await audit.log("DAEMON_CONTROL", f"Daemon '{name}' start requested via API")
    return {"status": "starting", "name": name}


@router.post("/daemons/{name}/stop")
async def stop_daemon(name: str):
    await publish_daemon_event("stop", name)
    await audit.log("DAEMON_CONTROL", f"Daemon '{name}' stop requested via API")
    return {"status": "stopping", "name": name}


@router.post("/daemons/{name}/restart")
async def restart_daemon(name: str, session: AsyncSession = Depends(get_session)):
    registry = AgentRegistry(session)
    agent = await registry.get_by_name(name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found.")
    if agent.execution_mode != "daemon":
        raise HTTPException(status_code=400, detail=f"Agent '{name}' is not a daemon.")
    await publish_daemon_event("restart", name)
    await audit.log("DAEMON_CONTROL", f"Daemon '{name}' restart requested via API")
    return {"status": "restarting", "name": name}


# =============================================================================
# CRON JOBS - Scheduled agent execution
# =============================================================================

@router.get("/cron")
async def list_cron_jobs(session: AsyncSession = Depends(get_session)):
    store = CronStore(session)
    jobs = await store.list_all()
    return [
        {
            "id": j.id,
            "name": j.name,
            "schedule": j.schedule,
            "agent_type": j.agent_type,
            "objective": j.objective,
            "conversation_id": j.conversation_id,
            "active": j.active,
            "last_run_at": j.last_run_at.isoformat() if j.last_run_at else None,
            "next_run_at": j.next_run_at.isoformat() if j.next_run_at else None,
        }
        for j in jobs
    ]


@router.post("/cron", status_code=201)
async def create_cron_job(
    job: CronJob,
    session: AsyncSession = Depends(get_session),
):
    store = CronStore(session)
    existing = await store.get_by_name(job.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Cron job '{job.name}' already exists.")
    record = await store.create(job)
    await audit.log("CRON_CREATED", f"Cron job '{job.name}' created (schedule={job.schedule})")
    return {"id": record.id, "name": record.name, "next_run_at": record.next_run_at.isoformat() if record.next_run_at else None}


@router.delete("/cron/{name}")
async def delete_cron_job(name: str, session: AsyncSession = Depends(get_session)):
    store = CronStore(session)
    deleted = await store.delete(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Cron job '{name}' not found.")
    await audit.log("CRON_DELETED", f"Cron job '{name}' deleted")
    return {"status": "deleted", "name": name}


@router.post("/cron/{name}/toggle")
async def toggle_cron_job(
    name: str,
    active: bool = True,
    session: AsyncSession = Depends(get_session),
):
    store = CronStore(session)
    record = await store.toggle(name, active)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Cron job '{name}' not found.")
    await audit.log("CRON_TOGGLED", f"Cron job '{name}' -> active={active}")
    return {"name": name, "active": record.active}


# =============================================================================
# MCP SERVERS - External tool server connections
# =============================================================================

class McpServerCreateRequest(BaseModel):
    name: str = Field(..., max_length=100)
    endpoint_url: Optional[str] = None
    endpoint_command: Optional[str] = None
    transport: str = Field(..., pattern="^(sse|stdio)$")
    auth_env_var: Optional[str] = None

    @model_validator(mode="after")
    def validate_endpoint(self):
        if self.transport == "sse" and not self.endpoint_url:
            raise ValueError("SSE transport requires endpoint_url")
        if self.transport == "stdio" and not self.endpoint_command:
            raise ValueError("stdio transport requires endpoint_command")
        if self.endpoint_url and self.endpoint_command:
            raise ValueError("Provide endpoint_url OR endpoint_command, not both")
        return self


@router.get("/mcp-servers")
async def list_mcp_servers(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(McpServerRecord).order_by(McpServerRecord.created_at.desc()))
    servers = result.scalars().all()
    pool = get_mcp_client_pool()
    return [
        {
            "id": s.id,
            "name": s.name,
            "transport": s.transport,
            "endpoint_url": s.endpoint_url,
            "endpoint_command": s.endpoint_command,
            "auth_env_var": s.auth_env_var,
            "active": s.active,
            "connected": pool.is_connected(s.name),
            "tools": [t.name for t in pool.get_tools(s.name)],
        }
        for s in servers
    ]


@router.post("/mcp-servers", status_code=201)
async def create_mcp_server(req: McpServerCreateRequest, session: AsyncSession = Depends(get_session)):
    existing = await session.execute(select(McpServerRecord).where(McpServerRecord.name == req.name))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail=f"MCP server '{req.name}' already exists.")

    if req.auth_env_var:
        import os
        if not os.environ.get(req.auth_env_var):
            raise HTTPException(status_code=400, detail=f"Environment variable '{req.auth_env_var}' is not set.")

    record = McpServerRecord(
        id=new_id(),
        name=req.name,
        endpoint_url=req.endpoint_url,
        endpoint_command=req.endpoint_command,
        transport=req.transport,
        auth_env_var=req.auth_env_var,
    )
    session.add(record)
    await session.commit()

    pool = get_mcp_client_pool()
    connected = await pool.connect(req.name, {
        "transport": req.transport,
        "endpoint_url": req.endpoint_url,
        "endpoint_command": req.endpoint_command,
        "auth_env_var": req.auth_env_var,
    })

    await audit.log("MCP_SERVER_CREATED", f"MCP server '{req.name}' registered (connected={connected})")
    return {"id": record.id, "name": req.name, "connected": connected}


@router.delete("/mcp-servers/{name}")
async def delete_mcp_server(name: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(McpServerRecord).where(McpServerRecord.name == name))
    record = result.scalars().first()
    if not record:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found.")

    pool = get_mcp_client_pool()
    await pool.disconnect(name)

    await session.delete(record)
    await session.commit()
    await audit.log("MCP_SERVER_DELETED", f"MCP server '{name}' removed")
    return {"status": "deleted", "name": name}


@router.post("/mcp-servers/{name}/refresh")
async def refresh_mcp_server(name: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(McpServerRecord).where(McpServerRecord.name == name))
    record = result.scalars().first()
    if not record:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found.")

    pool = get_mcp_client_pool()
    await pool.disconnect(name)
    connected = await pool.connect(name, {
        "transport": record.transport,
        "endpoint_url": record.endpoint_url,
        "endpoint_command": record.endpoint_command,
        "auth_env_var": record.auth_env_var,
    })

    tools = [t.name for t in pool.get_tools(name)]
    await audit.log("MCP_SERVER_REFRESHED", f"MCP server '{name}' refreshed: {len(tools)} tools")
    return {"name": name, "connected": connected, "tools": tools}


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