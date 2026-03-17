from __future__ import annotations
from pydantic import BaseModel, Field, model_validator
from typing import Optional, Any
from enum import Enum
from datetime import datetime
from ulid import ULID


def new_id() -> str:
    return str(ULID())


# --- Enums ---

class SignalSource(str, Enum):
    USER = "user"
    SYSTEM = "system"


class RoutingDecision(str, Enum):
    AGENT = "agent"
    AUTOMATION = "automation"
    RESPOND = "respond"
    GATHER = "gather"
    CONFIRM = "confirm"


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    HALTED = "halted"
    REJECTED = "rejected"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# --- Input schemas ---

class ChatMessage(BaseModel):
    role: str = Field("user", max_length=20)
    content: str = Field(..., max_length=32_000)


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=32_000)
    conversation_id: Optional[str] = Field(None, max_length=64)


class WebhookSignal(BaseModel):
    source: str = Field(..., max_length=100, description="e.g. 'datadog', 'pagerduty', 'kubernetes'")
    event_type: str = Field(..., max_length=100, description="e.g. 'alert', 'metric', 'ticket'")
    title: str = Field(..., max_length=1_000)
    payload: dict[str, Any] = Field(default_factory=dict, max_length=50)
    severity: Optional[str] = Field(None, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def limit_payload_size(cls, values):
        """Reject payloads exceeding 64KB when serialized."""
        import json as _json
        payload = values.get("payload", {})
        if payload and len(_json.dumps(payload, default=str)) > 65_536:
            raise ValueError("Payload exceeds 64KB limit")
        return values


# --- Orchestrator schemas ---

class OrchestratorInput(BaseModel):
    id: str = Field(default_factory=new_id)
    source: SignalSource
    content: str
    conversation_id: Optional[str] = None
    raw_signal: Optional[WebhookSignal] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class OrchestratorResponse(BaseModel):
    decision: RoutingDecision
    reasoning: str = ""
    tool_call: Optional[dict[str, Any]] = None
    text_response: Optional[str] = None
    agent_type: Optional[str] = None
    automation_id: Optional[str] = None
    risk_level: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False


# --- Rule schemas ---

class RuleAction(str, Enum):
    REQUIRE_APPROVAL = "require_approval"
    HARD_REJECT = "hard_reject"
    TRIGGER_AUTOMATION = "trigger_automation"
    FORCE_AGENT = "force_agent"
    ALLOW = "allow"


class Rule(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    pattern: str = Field(..., description="Regex or keyword pattern to match")
    pattern_type: str = "regex"  # regex, keyword, event_type
    action: RuleAction
    risk_level: RiskLevel = RiskLevel.MEDIUM
    automation_id: Optional[str] = None
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


# --- Agent schemas ---

class AgentTemplate(BaseModel):
    id: str
    name: str
    role: str = Field(..., description="System prompt describing the agent's role")
    tools: list[ToolDefinition] = Field(default_factory=list)
    max_steps: int = 10
    provider_override: Optional[str] = None
    model_override: Optional[str] = None


class AgentExecution(BaseModel):
    id: str = Field(default_factory=new_id)
    agent_type: str
    objective: str
    context: str
    status: ExecutionStatus = ExecutionStatus.PENDING
    steps: list[dict[str, Any]] = Field(default_factory=list)
    result: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None


# --- Automation schemas ---

class AutomationWorkflow(BaseModel):
    id: str
    name: str
    description: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)
    active: bool = True


class AutomationExecution(BaseModel):
    id: str = Field(default_factory=new_id)
    workflow_id: str
    trigger_context: str
    status: ExecutionStatus = ExecutionStatus.PENDING
    result: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)


# --- Approval schemas ---

class ApprovalRequest(BaseModel):
    id: str = Field(default_factory=new_id)
    action_description: str
    risk_level: RiskLevel
    blast_radius: str = ""
    reasoning: str = ""
    original_input_id: str = ""
    status: str = "pending"  # pending, approved, rejected, expired
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None


class ApprovalDecision(BaseModel):
    approved: bool
    note: Optional[str] = None


# --- Audit schemas ---

class AuditEntry(BaseModel):
    id: str = Field(default_factory=new_id)
    action: str
    detail: str
    input_id: Optional[str] = None
    execution_id: Optional[str] = None
    risk_level: Optional[RiskLevel] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# --- API responses ---

class ChatResponse(BaseModel):
    conversation_id: str
    response: str
    decision: Optional[RoutingDecision] = None
    execution_id: Optional[str] = None
    approval_required: bool = False
    approval_id: Optional[str] = None


class SystemStatus(BaseModel):
    status: str  # operational, killed
    active_agents: int = 0
    active_automations: int = 0
    pending_approvals: int = 0
    kill_switch: bool = False