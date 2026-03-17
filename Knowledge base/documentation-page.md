# Engine Documentation

Complete technical reference for the Stourio Core Engine and the MCP Gateway. Two repositories, two servers, one system.

**NOTE:** This is the content of the public-facing documentation.html page on stourio.com. When regenerating this page as HTML, use the Stourio design system: Crimson Pro serif for headings, DM Sans for body, background #f4f1e8, accent #2e2e2e, border #d8d4c8, max-width 780px. Components: .callout (principle/warning), .diagram (dark code blocks), .layer (numbered cards), .table-wrap, .timeline.

---

## Deployment architecture

The system runs across two isolated servers. The Engine Server holds the orchestration logic, all state, and the automation engine. It has no access to your infrastructure credentials. The MCP Server holds only the gateway that executes privileged tool calls. It sits in a private subnet and accepts traffic exclusively from the Engine Server.

```
Server 1: Engine Server                     Server 2: MCP Server
Public or internal network                      Private subnet only

  ┌────────────────────────────┐              ┌──────────────────────┐
  │  Stourio Core        :8000 │              │  MCP Gateway    :8080│
  │  Orchestrator + API        │              │  Tool execution      │
  │                            │   POST       │                      │
  │  default_tool_executor ────│──/execute───▶│  TOOL_REGISTRY       │
  │                            │  + Bearer    │    ├ read_runbook    │
  ├────────────────────────────┤   token      │    ├ get_metrics     │
  │  PostgreSQL         :5432  │              │    ├ get_logs        │
  │  Redis              :6379  │              │    ├ execute_remed.  │
  │  n8n                :5678  │              │    ├ send_notif.     │
  │  Jaeger            :16686  │              │    ├ call_api        │
  └────────────────────────────┘              │    └ generate_report │
                                              └──────────────────────┘
No infrastructure credentials                 Holds AWS keys, service
on this server.                               tokens, internal docs.
                                              Firewall: allow only
                                              Engine Server IP.
```

> **Why two servers?**
> Credential isolation. The orchestrator sends LLM-generated tool calls to the gateway. If the orchestrator is compromised, the attacker gets conversation history and routing logic but never touches your infrastructure keys. The gateway validates every request against a shared secret and only executes tools from its own registry.

---

## Server 1: Stourio Core Engine

Repository: `stourio-core-Engine`. This is the orchestration brain. It receives inputs (user chat or system webhooks), routes them through a rules engine and an LLM, and delegates work to agents or automation workflows. All state is persisted to PostgreSQL. All real-time coordination goes through Redis. All tool execution is forwarded to the MCP Gateway over the network.

### Prerequisites

Docker + Docker Compose, at least one LLM API key, Python 3.12 (for key generation script).

### Quick start

```
# 1. Clone and configure
cp .env.example .env
python3 scripts/generate_key.py          # generates STOURIO_API_KEY

# 2. Add your LLM keys to .env
# 3. Change POSTGRES_PASSWORD and REDIS_PASSWORD from defaults

# 4. Start everything
docker compose up -d

# 5. Verify
curl http://localhost:8000/               # Should return JSON with version
curl http://localhost:8000/docs           # Swagger UI
```

On first start, the system creates all PostgreSQL tables and seeds four default safety rules. The signal consumer worker begins listening on the Redis stream immediately.

---

## Infrastructure services

Five containers run on the Engine Server via Docker Compose. Only Stourio (:8000) and Jaeger (:16686) expose ports to the host. PostgreSQL and Redis are internal-only (no published ports).

### 1. Stourio Core

The FastAPI application. Hosts the API, the orchestrator, the agent runtime, the rules engine, and the automation dispatcher. Runs on port 8000. On startup, it initializes the database schema, seeds default rules, configures OpenTelemetry tracing to Jaeger, and starts the background signal consumer worker that dequeues system events from Redis Streams.

### 2. PostgreSQL

The permanent state store. Four tables are created automatically on first start:

| Table | Purpose | Key columns |
|---|---|---|
| audit_log | Immutable record of every decision, action, and event | action, detail, input_id, execution_id, risk_level |
| conversation_messages | Chat history per conversation | conversation_id, role, content |
| rules | User-defined routing rules (versioned, active/inactive) | pattern, pattern_type, action, risk_level, automation_id |
| approvals | Pending and resolved approval requests | action_description, status, expires_at |

Runs on `postgres:16-alpine`. Internal port 5432 only. Connection pool size: 10 (configurable via SQLAlchemy engine).

### 3. Redis

Handles five distinct responsibilities through different data structures:

| Function | Redis type | Key pattern |
|---|---|---|
| Signal queue | Stream + Consumer Group | `stourio:signals` |
| Kill switch | Key/Value | `stourio:kill_switch` |
| Approval cache | Key/Value with TTL | `stourio:approval:{id}` |
| Distributed locks | Key/Value with NX + TTL | `stourio:lock:{resource}` |
| Rate limiting | Key/Value with INCR + TTL | `stourio:ratelimit:{ip}:{path}:{window}` |

Runs on `redis:7-alpine`. Requires password authentication. Max memory 256MB with LRU eviction. Internal port 6379 only.

Signals use consumer groups for reliable delivery. A signal is only removed from the stream after the orchestrator acknowledges successful processing. If the worker crashes, unacknowledged signals are redelivered on restart.

### 4. n8n

The deterministic automation engine. When the orchestrator triggers a workflow, it sends a POST request to `http://n8n:5678/webhook/stourio` with the workflow ID, execution context, and step definitions. n8n executes the steps sequentially (health check, apply fix, validate, notify) and returns a result.

Exposed on `127.0.0.1:5678` for local admin access to the visual workflow editor. Not exposed to the public network.

### 5. Jaeger

Receives OpenTelemetry traces from the Stourio application via OTLP gRPC on internal port 4317. The web UI is available at `127.0.0.1:16686` for viewing request traces, orchestrator decisions, agent execution steps, and timing data.

Stourio instruments every orchestrator call with custom spans: signal source, routing decision, agent type, execution duration. Every FastAPI endpoint is auto-instrumented via `opentelemetry-instrumentation-fastapi`.

---

## API reference

All endpoints require the `X-STOURIO-KEY` header. If the key is not set in the environment, the system rejects all requests with HTTP 503. The full interactive API documentation is available at `/docs` (Swagger UI).

| Method | Endpoint | Purpose | Rate limit |
|---|---|---|---|
| POST | /api/chat | Send a user message through the orchestrator | 30/min |
| POST | /api/webhook | Ingest a system signal (queued, returns 202) | 120/min |
| GET | /api/approvals | List pending approval requests | 60/min |
| POST | /api/approvals/{id} | Approve or reject an action | 60/min |
| POST | /api/kill | Activate the kill switch (halt all operations) | 5/min |
| POST | /api/resume | Deactivate the kill switch | 5/min |
| GET | /api/rules | List all active rules | 30/min |
| POST | /api/rules | Create a new rule | 30/min |
| DELETE | /api/rules/{id} | Delete a rule | 30/min |
| GET | /api/status | System status, kill switch state, pending approvals | 60/min |
| GET | /api/audit | Recent audit log entries (default: 50) | 30/min |

### Chat request

```
POST /api/chat
Headers:  X-STOURIO-KEY: your-api-key
Body:
{
  "message": "Why is latency high on the EU CDN?",
  "conversation_id": "optional-existing-id"    // omit to start new
}

Response:
{
  "conversation_id": "01JARX...",
  "status": "completed",                       // or awaiting_approval, needs_info
  "message": "The agent found...",
  "type": "agent",                              // or direct, automation
  "execution_id": "01JARX...",
  "steps": [...]
}
```

### Webhook signal

```
POST /api/webhook
Headers:  X-STOURIO-KEY: your-api-key
Body:
{
  "source": "datadog",
  "event_type": "alert",
  "title": "High CPU on web-server-03",
  "severity": "critical",
  "payload": {
    "host": "web-server-03",
    "cpu_percent": 97.3,
    "duration_minutes": 5
  }
}

Response: 202 Accepted
{ "status": "queued", "message": "Signal accepted for correlation." }
```

Webhook signals are enqueued to a Redis Stream and processed asynchronously by the background consumer worker. The endpoint returns immediately to prevent blocking your monitoring system's webhook delivery.

---

## Orchestrator routing

Every input follows the same five-step pipeline: kill switch check, deterministic rules evaluation, LLM routing (if no rule matched), execution, and result return.

```
Input received
     │
     ▼
Kill switch active? ──yes──▶ Return "System halted"
     │ no
     ▼
Rules engine match?
     │
     ├── hard_reject      ──▶ Block. Return rejection message.
     ├── require_approval  ──▶ Create approval request. Pause.
     ├── trigger_automation──▶ Fire workflow via n8n. Return result.
     ├── force_agent       ──▶ Skip LLM routing, go to agent.
     │
     └── No match ──▶ Send to LLM with routing tools
                          │
                          ├── route_to_agent
                          │     ├── risk high/critical ──▶ Require approval first
                          │     └── risk low/medium ────▶ Execute immediately
                          ├── route_to_automation ──▶ Fire workflow
                          ├── respond_directly ───▶ Return text
                          └── request_more_info ──▶ Ask for clarification
```

The rules engine runs before the LLM on every request. Known patterns (destructive commands, known alert signatures) are handled deterministically with zero LLM token cost. The LLM only sees inputs that don't match any rule.

---

## Rules engine

Rules are stored in PostgreSQL, cached in memory, and evaluated in order (first match wins). Four pattern types are supported:

| Pattern type | Matches against | Example pattern |
|---|---|---|
| regex | Input content (sanitized + raw) | `DROP\s+(DATABASE\|TABLE)` |
| keyword | Normalized input text | `production deploy` |
| event_type | Signal header text | `alert:critical` |
| payload_match | Parsed webhook JSON payload | `severity:critical` |

Regex patterns are evaluated against both the raw input and a sanitized version that strips SQL comments, C-style block comments, and excessive whitespace. This prevents obfuscation bypasses on destructive commands.

### Default safety rules

Seeded automatically on first start if no rules exist:

| Rule | Pattern | Action | Risk |
|---|---|---|---|
| prevent_db_drop | `DROP\s+(DATABASE\|TABLE)` | Require approval | Critical |
| block_ssh_root | `ssh\s+root@` | Hard reject | Critical |
| block_rm_rf | `rm\s+-rf\s+/` | Hard reject | Critical |
| auto_scale_cpu | `CPU\s*>\s*9[0-9]%` | Trigger automation | Low |

### Creating a rule via API

```
POST /api/rules
{
  "name": "block_prod_delete",
  "pattern": "DELETE.*FROM.*production",
  "pattern_type": "regex",
  "action": "hard_reject",
  "risk_level": "critical"
}

// Actions: require_approval, hard_reject, trigger_automation, force_agent, allow
// Risk levels: low, medium, high, critical
// For trigger_automation, include "automation_id": "workflow_id_here"
```

---

## Agent templates

Agents are stored as templates in `src/agents/runtime.py`. Each template defines a role (system prompt), a set of allowed tools, and a maximum step count. The orchestrator selects the right template based on the LLM's routing decision, then the agent runtime executes it in a loop: LLM call, tool call, LLM call, tool call, until the agent produces a final text response or hits the step limit.

| Template | Role | Tools | Max steps |
|---|---|---|---|
| diagnose_repair | Diagnose system issues, fetch runbooks, propose and apply fixes | get_system_metrics, get_recent_logs, execute_remediation, read_internal_runbook | 8 |
| escalate | Summarize situation, assess severity, notify the right people | send_notification | 4 |
| take_action | General-purpose: API calls, report generation, data lookups | call_api, generate_report | 6 |

Every tool call from every agent is routed through `default_tool_executor`, which forwards it to the MCP Gateway's `/execute` endpoint. The agent never executes tools locally.

> **Agent safety mechanisms**
> Each agent loop checks the kill switch before every LLM call. Each agent acquires a distributed lock with a fencing token on its work resource. If the lock is overtaken by a newer process, the agent terminates. A background heartbeat extends the lock TTL every 10 seconds while the agent is active.

### Adding a new agent template

Add a new entry to the `AGENT_TEMPLATES` dictionary in `src/agents/runtime.py`. The template ID must also be added to the orchestrator's routing tools enum in `src/orchestrator/core.py` (the `agent_type` enum list in the `route_to_agent` tool definition).

```python
# In src/agents/runtime.py, add to AGENT_TEMPLATES:

"security_audit": AgentTemplate(
    id="security_audit",
    name="Security Audit",
    role="""You are a security agent. Analyze access logs,
check for anomalies, and report findings.""",
    tools=[
        ToolDefinition(
            name="get_recent_logs",
            description="Retrieve recent log entries",
            parameters={...},
        ),
    ],
    max_steps=6,
),

# Then in src/orchestrator/core.py, update the route_to_agent tool:
# "enum": ["diagnose_repair", "escalate", "take_action", "security_audit"]
```

---

## Automation workflows

Defined in `src/automation/workflows.py`. Each workflow has an ID, a name, and a list of steps. When triggered, the orchestrator sends the full step definition to n8n's webhook endpoint. n8n handles the actual execution.

| Workflow ID | Name | Steps |
|---|---|---|
| auto_scale_horizontal | Horizontal Auto-Scale | Get instance count, scale +2, verify health |
| restart_service | Rolling Restart | Drain oldest, restart, verify health, resume traffic |
| flush_cdn_cache | CDN Cache Flush | Purge CDN by region, verify origin response |

### Adding a workflow

```python
# In src/automation/workflows.py, add to WORKFLOWS:

"rotate_secrets": AutomationWorkflow(
    id="rotate_secrets",
    name="Secret Rotation",
    description="Rotate API keys for a service",
    steps=[
        {"action": "generate_new_key", "target": "{{service}}"},
        {"action": "update_secret_store", "target": "{{service}}"},
        {"action": "restart_service", "target": "{{service}}"},
        {"action": "verify_health", "target": "{{service}}", "timeout": 30},
        {"action": "revoke_old_key", "target": "{{service}}"},
    ],
),

# Then configure the matching workflow in n8n's visual editor
# to receive the webhook payload and execute each step.
```

The workflow ID here must match the workflow configured in your n8n instance. Stourio sends the payload; n8n defines how each step is actually executed.

---

## Server 2: MCP Gateway

Repository: `stourio-mcp-Engine`. A single-purpose FastAPI service with one endpoint: `POST /execute`. The orchestrator sends a tool name and arguments; the gateway looks up the handler in its internal registry and executes it. No routing logic, no LLM calls, no state management. Just tool dispatch.

### Setup

```
# 1. Generate shared secret
python3 setup_gateway.py                  # creates .env with MCP_SHARED_SECRET

# 2. Copy the secret to the Engine Server's .env too

# 3. Add your runbooks
mkdir runbooks
# Add .md files: runbooks/redis-cache.md, runbooks/api-errors.md, etc.

# 4. Build and run
docker build -t mcp-gateway .
docker run -d -p 8080:8080 --name mcp-gateway --env-file .env mcp-gateway

# 5. Verify
curl http://localhost:8080/health           # no auth required
```

### The /execute contract

```
POST /execute
Headers:  Authorization: Bearer <MCP_SHARED_SECRET>
Body:
{
  "tool_name": "read_internal_runbook",
  "arguments": {
    "service_name": "redis-cache"
  }
}

Success (200):
{ "result": "# Redis Cache Runbook\n..." }

Unknown tool (404):
{ "detail": "Tool 'unknown' is not registered on this gateway." }

Rate limited (429):
{ "error": "Rate limit exceeded. Max 60 requests/minute." }
```

---

## MCP tool registry

Tools are registered in `gateway.py` using the `@register_tool` decorator. The gateway dispatches by matching `tool_name` against the registry dictionary. Unknown tools are rejected with a 404.

| Tool | Status | Description |
|---|---|---|
| read_internal_runbook | Live | Reads a Markdown file from the `/app/docs` directory. Path traversal protected. |
| get_system_metrics | Stub | Connect to Prometheus, CloudWatch, or Datadog. |
| get_recent_logs | Stub | Connect to Loki, CloudWatch Logs, or ELK. |
| execute_remediation | Stub | Connect to AWS SSM, Ansible, or Rundeck. |
| send_notification | Stub | Connect to Slack webhook, SendGrid, or PagerDuty. |
| call_api | Stub | HTTP dispatch with URL allowlist. |
| generate_report | Stub | Report formatting and export. |

Stub tools return structured JSON explaining they are not yet connected. The agent LLM receives this as a tool result and handles it gracefully, typically reporting that the integration is not yet configured.

### Adding a tool

Three steps: add the handler to the gateway, add the tool definition to the agent template, and rebuild the gateway image.

```python
# Step 1: In gateway.py, add the handler

@register_tool("check_disk_usage")
async def tool_check_disk_usage(arguments: dict) -> dict:
    host = arguments.get("host", "localhost")
    # ... your implementation ...
    return {"usage_percent": 73.2, "mount": "/"}


# Step 2: In stourio-core-Engine/src/agents/runtime.py,
# add the tool definition to the appropriate agent template:

ToolDefinition(
    name="check_disk_usage",
    description="Check disk usage on a host",
    parameters={
        "type": "object",
        "properties": {
            "host": {"type": "string"}
        },
        "required": ["host"]
    },
),


# Step 3: Rebuild and redeploy the gateway
docker build -t mcp-gateway . && docker restart mcp-gateway
```

> **Why both sides?**
> The agent template defines what the LLM knows it can call (the tool's name, description, and parameter schema). The gateway defines what actually happens when it's called. The core's tool executor validates that every LLM tool call exists in the agent's allowed set before forwarding it to the gateway. This is defense in depth: even if the LLM hallucinates a tool name, it's rejected before reaching the network.

### Removing a tool

Remove the `@register_tool` function from `gateway.py` and remove the corresponding `ToolDefinition` from the agent template in the core. Rebuild both images. If the tool is removed from the gateway but not from the agent template, the agent will call it and receive a 404. If the tool is removed from the agent template but not from the gateway, it becomes unreachable (the core's whitelist blocks it before it ever reaches the gateway).

---

## Security model

### 1. API authentication

Every request to the Core API requires the `X-STOURIO-KEY` header. Generated via `python3 scripts/generate_key.py` (cryptographically secure, 32 characters). If the key is not configured in the environment, the system returns HTTP 503 on all endpoints until it is set.

### 2. MCP gateway authentication

Every request to the Gateway requires `Authorization: Bearer <MCP_SHARED_SECRET>`. Applied at the FastAPI dependency level (all endpoints protected by default, not opt-in). The same secret must be configured on both servers.

### 3. Rate limiting

The Core uses Redis-backed per-IP rate limiting with configurable limits per endpoint prefix (see API reference table). The Gateway uses in-memory sliding window rate limiting, defaulting to 60 requests/minute per IP. Both return HTTP 429 with a `Retry-After` header.

### 4. Tool call validation

The Core's `default_tool_executor` applies three checks before forwarding any tool call to the gateway: (1) whitelist check against all tool names defined in agent templates, (2) regex validation that the tool name contains only `[a-zA-Z0-9_-]`, (3) the gateway itself rejects any tool not in its registry. Three layers, three independent codebases.

### 5. Network isolation

PostgreSQL and Redis expose no ports to the host (Docker `expose` only, no `ports`). Jaeger and n8n are bound to `127.0.0.1`. The MCP Gateway should be firewalled to accept traffic only from the Engine Server IP on port 8080.

### 6. Kill switch

A Redis flag checked before every orchestrator decision and before every agent tool call. When activated via `POST /api/kill`, all new inputs are rejected and all running agents halt at their next step. Deactivate via `POST /api/resume`. Both actions are recorded in the audit log.

---

## Environment variables

### Stourio Core (.env)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| STOURIO_API_KEY | Yes | | API authentication key for all endpoints |
| ORCHESTRATOR_PROVIDER | No | openai | LLM provider for routing: openai, anthropic, deepseek, google |
| ORCHESTRATOR_MODEL | No | gpt-4o-mini | Model for routing decisions (fast, cheap recommended) |
| AGENT_PROVIDER | No | openai | LLM provider for agent reasoning |
| AGENT_MODEL | No | gpt-4o-mini | Model for agent work (strong reasoning recommended) |
| OPENAI_API_KEY | If using | | OpenAI API key |
| ANTHROPIC_API_KEY | If using | | Anthropic API key |
| DEEPSEEK_API_KEY | If using | | DeepSeek API key |
| GOOGLE_API_KEY | If using | | Google Gemini API key |
| POSTGRES_PASSWORD | Yes | changeme | PostgreSQL password (change before first start) |
| REDIS_PASSWORD | Yes | changeme | Redis password (change before first start) |
| DATABASE_URL | No | postgresql+asyncpg://stourio:changeme@postgres:5432/stourio | Full connection string (auto-composed from password in docker-compose) |
| REDIS_URL | No | redis://:changeme@redis:6379/0 | Full Redis URL |
| AUTOMATION_WEBHOOK_URL | No | http://n8n:5678/webhook/stourio | n8n webhook endpoint |
| MCP_SERVER_URL | Yes | | Full URL to MCP gateway (e.g. http://10.0.1.50:8080) |
| MCP_SHARED_SECRET | Yes | | Bearer token for gateway auth (must match gateway .env) |
| CORS_ORIGINS | No | http://localhost:3000,http://localhost:8000 | Comma-separated allowed origins |
| MAX_AGENT_DEPTH | No | 4 | Maximum agent nesting depth |
| APPROVAL_TTL_SECONDS | No | 300 | Seconds before unapproved actions auto-reject |
| LOG_LEVEL | No | info | Logging level: debug, info, warning, error |

### MCP Gateway (.env)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| MCP_SHARED_SECRET | Yes | | Bearer token for auth (must match core .env) |
| MCP_RATE_LIMIT | No | 60 | Max requests per minute per IP |
| MCP_DOCS_DIR | No | /app/docs | Runbook directory path inside container |

---

## Project structure

### stourio-core-Engine

```
stourio-core-Engine/
├── docker-compose.yml        All 5 services
├── Dockerfile                Stourio container
├── .env.example              Copy to .env and configure
├── scripts/
│   ├── generate_key.py       Generate STOURIO_API_KEY
│   ├── setup.sh              First-time setup helper
│   └── test.sh
├── src/
│   ├── main.py               FastAPI app, lifespan, signal worker
│   ├── config.py             Pydantic settings from .env
│   ├── telemetry.py          OpenTelemetry + Jaeger setup
│   ├── api/
│   │   ├── routes.py         All API endpoints
│   │   └── rate_limit.py     Redis-backed rate limiter middleware
│   ├── orchestrator/
│   │   └── core.py           Routing pipeline: rules → LLM → execute
│   ├── agents/
│   │   └── runtime.py        Agent templates, execution loop, tool executor
│   ├── automation/
│   │   └── workflows.py      Workflow definitions + n8n dispatch
│   ├── rules/
│   │   └── engine.py         Rule evaluation, sanitization, seeding
│   ├── guardrails/
│   │   └── approvals.py      Approval lifecycle (create/resolve/expire)
│   ├── persistence/
│   │   ├── database.py       SQLAlchemy models + table definitions
│   │   ├── redis_store.py    Kill switch, locks, queues, approvals
│   │   ├── audit.py          Audit log writes
│   │   └── conversations.py  Chat history persistence
│   ├── adapters/
│   │   ├── base.py           Abstract LLM adapter interface
│   │   ├── registry.py       Provider selection from config
│   │   ├── openai_adapter.py
│   │   ├── anthropic_adapter.py
│   │   └── google_adapter.py
│   └── models/
│       └── schemas.py        All Pydantic models and enums
└── tests/
```

### stourio-mcp-Engine

```
stourio-mcp-Engine/
├── Dockerfile                Gateway container (bakes in runbooks)
├── .env.example              Copy to .env and configure
├── gateway.py               FastAPI app, tool registry, all handlers
├── setup_gateway.py         Generate MCP_SHARED_SECRET
└── runbooks/                 Your internal docs (.md files)
    └── .gitkeep
```

---

## Production checklist

1. **Change all default passwords** — POSTGRES_PASSWORD, REDIS_PASSWORD, and generate STOURIO_API_KEY and MCP_SHARED_SECRET. Never deploy with "changeme".

2. **Configure LLM providers** — Set at least one API key. Recommended: a fast/cheap model for ORCHESTRATOR (routing) and a strong reasoning model for AGENT (execution).

3. **Set MCP_SERVER_URL to the gateway's private IP** — Not localhost. The actual internal IP of the MCP Server (e.g. http://10.0.1.50:8080).

4. **Firewall the MCP Gateway** — Port 8080 accepts traffic only from the Engine Server IP. Block everything else.

5. **Restrict CORS_ORIGINS** — Remove localhost entries. Set to your actual frontend domain(s) only.

6. **Verify Jaeger and n8n are not publicly exposed** — Both should be bound to 127.0.0.1 in docker-compose.yml (already the default).

7. **Add your runbooks to the MCP Gateway** — Place Markdown files in the runbooks/ directory. Rebuild the image. These are the docs your agents will reference when diagnosing issues.

8. **Configure n8n workflows** — Access n8n at localhost:5678, create workflows that match the IDs in your automation definitions (auto_scale_horizontal, restart_service, flush_cdn_cache).

9. **Replace MCP tool stubs with real implementations** — Connect get_system_metrics to your monitoring, get_recent_logs to your log aggregator, send_notification to Slack/PagerDuty. Each is a single async function in gateway.py.

10. **Test the kill switch** — POST /api/kill, verify all operations halt, POST /api/resume. Confirm both actions appear in the audit log.
