# Stourio

**Operational Intelligence Framework**
LLM-agnostic orchestration for autonomous operations.

## What this does

Stourio is an orchestration layer that sits between your systems and your AI provider. It receives signals (human requests or system events), reasons about them using any LLM, and routes them to the right capability: an AI agent for novel situations, or an automation workflow for known patterns.

```
text
[You / Your Systems]
        ↓
   [Rule Engine]  ← Deterministic. Runs first. No LLM needed.
        ↓
  [LLM Orchestrator]  ← Routes ambiguous signals via tool use
        ↓
[AI Agents]  or  [Automation]
        ↓
   [Audit Log]  ← Every decision recorded
```
## Key properties
- LLM-agnostic: swap providers by changing environment variables. Supports OpenAI, Anthropic, Google, DeepSeek, and any OpenAI-compatible API.
- Per-agent LLM routing: each agent template can use a different provider and model. Diagnose with Claude, escalate with GPT, report with Gemini. Automatic failover to the default provider on outage.
- Rules before LLM: known patterns are intercepted deterministically before the LLM is called. Faster, cheaper, predictable.
- Human-in-the-loop: high-risk actions pause and wait for your approval. TTL-based: unapproved actions auto-reject.
- Kill switch: one API call halts everything.
- Full audit trail: every signal, every routing decision, every tool call. Immutable. Queryable.

## Quick Start

### Prerequisites
- Docker and Docker Compose v2+
- At least one LLM API key (OpenAI, Anthropic, Google, or DeepSeek)

### Setup

```bash
# 1. Clone and enter
git clone https://github.com/catalinprg/ai-ops-engine.git
cd ai-ops-engine/stourio-core-engine

# 2. Bootstrap the environment (generates API key, DB and Redis passwords)
chmod +x scripts/setup.sh
./scripts/setup.sh

# 3. Add your LLM configuration to .env
# ORCHESTRATOR_PROVIDER=openai
# ORCHESTRATOR_MODEL=gpt-4o-mini
# AGENT_PROVIDER=anthropic           (fallback for agents without overrides)
# AGENT_MODEL=claude-sonnet-4-5-20250929
# Add API keys for EVERY provider referenced in agent templates (see Per-Agent LLM section).

# 4. Start the infrastructure (Stourio, PostgreSQL, Redis, n8n)
docker-compose up --build

# 5. Open API docs
# http://localhost:8000/docs
```

## Security (Mandatory)
Stourio endpoints are protected via an API Key.
**Setup**: Run python3 scripts/generate_key.py to seed your .env file.
**Usage**: Every API request must include the X-STOURIO-KEY header.
**Protection**: This prevents unauthorized access to the Kill Switch and Agent execution logic.

## Observability & Tracing
- Stourio uses OpenTelemetry for distributed tracing.
   Local Debugging: Spans are printed to the container logs.
   Production: Configure the OTLP_ENDPOINT in src/telemetry.py to send traces to your observability platform (Jaeger, Datadog, etc.).
   Visualizer: If Jaeger is running, visit http://localhost:16686 to view the execution path of any operational signal.

## Reliability & Safety
At-Least-Once Delivery: Signals are ingested into Redis Consumer Groups. They are only acknowledged (ACK) after the orchestrator completes processing.
Fencing Tokens: Every agent acquires a versioned fencing token. If a newer agent starts for the same resource, the older agent is automatically "fenced out" to prevent state corruption.
Rate Limiting: LLM calls are throttled to prevent 429 Too Many Requests errors during high-volume incidents.

## Test the pipeline
Once the containers are running, open a new terminal window and run:
```
chmod +x scripts/test.sh
./scripts/test.sh
```
This runs through: simple chat, agent routing, webhook signals, rule interception, approval flow, kill switch, and audit trail.

## API endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/chat` | POST | Send a human message through the orchestrator |
| `/api/webhook` | POST | Receive system signals (alerts, events) |
| `/api/status` | GET | System status, available agents and workflows |
| `/api/approvals` | GET | List pending approval requests |
| `/api/approvals/{id}` | POST | Approve or reject an action |
| `/api/kill` | POST | Activate kill switch (halt everything) |
| `/api/resume` | POST | Deactivate kill switch |
| `/api/rules` | GET/POST | List or create routing rules |
| `/api/rules/{id}` | DELETE | Delete a rule |
| `/api/audit` | GET | Query the audit trail |
| `/api/usage` | GET | Token usage by date range |
| `/api/usage/summary` | GET | Aggregated usage by provider/agent |
| `/api/documents/ingest` | POST | Trigger runbook re-ingestion |

## Architecture

```
src/
  adapters/          LLM provider adapters (OpenAI, Anthropic, Google, DeepSeek)
  orchestrator/      Core routing logic (rules → LLM → execution)
  agents/            Agent templates and runtime (tool calling loop)
  automation/        External workflow execution (n8n integration)
  rules/             Deterministic rule engine (runs before LLM)
  guardrails/        Approval flow, kill switch, validation
  persistence/       PostgreSQL (state, audit) + Redis (queue, locks, cache)
  api/               FastAPI routes
  models/            Pydantic schemas
```
## Extending

### Add a new LLM provider

1. Create `src/adapters/your_provider.py` inheriting from `BaseLLMAdapter`
2. Implement the `complete()` method
3. Register it in `src/adapters/registry.py`

### Add a new agent template

Drop a `.yaml` file in `config/agents/`. The engine merges YAML templates with the built-in defaults at startup — YAML definitions override built-ins with the same `id`.

```yaml
id: diagnose_database
name: diagnose_database
description: Database operations specialist
system_prompt: |
  You are a database specialist. You diagnose PostgreSQL, MySQL, and Redis issues.
tools:
  - search_knowledge
  - get_system_metrics
  - get_recent_logs
provider_override: anthropic/claude-3-5-sonnet-latest
max_steps: 8
```

Set `provider_override` to run the agent on a specific LLM. If omitted, the agent uses the `AGENT_PROVIDER`/`AGENT_MODEL` fallback from `.env`.

## Per-Agent LLM Configuration

Each agent template can run on a different LLM provider and model. This is configured via two optional fields on the `AgentTemplate` in `src/agents/runtime.py`:

```python
"diagnose_repair": AgentTemplate(
    id="diagnose_repair",
    name="Diagnose & Repair",
    provider_override="anthropic",       # Uses Claude for this agent
    model_override="claude-sonnet-4-5-20250929",
    role="...",
    tools=[...],
),
"escalate": AgentTemplate(
    id="escalate",
    name="Escalate",
    provider_override="openai",          # Uses GPT for this agent
    model_override="gpt-4o",
    role="...",
    tools=[...],
),
```

**How resolution works:**
1. If `provider_override` is set on the template, the agent uses that provider and model.
2. If `provider_override` is `None`, the agent falls back to `AGENT_PROVIDER` / `AGENT_MODEL` from `.env`.
3. If the override provider fails at runtime (API error, outage), the system automatically fails over to the `.env` fallback and retries the current step. This failover is logged to the audit trail.

**API key requirement:** You must supply API keys in `.env` for every provider referenced across all templates. If `diagnose_repair` uses Anthropic, `escalate` uses OpenAI, and `take_action` uses Google, then `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `GOOGLE_API_KEY` must all be set.

The `GET /api/status` endpoint shows the resolved provider and model for each agent template.

### Add a new automation workflow

Edit `src/automation/workflows.py` and add to `WORKFLOWS`. Ensure the corresponding workflow ID is configured in your running n8n instance to catch the webhook payload.

### Connect real tools (MCP)
Stourio routes all agent tool executions to a single `/execute` endpoint on the MCP gateway (`MCP_SERVER_URL`). The gateway dispatches internally by `tool_name`. You must deploy the companion `stourio-mcp-framework` on a separate server and configure both services to share the same `MCP_SHARED_SECRET`.

## MCP Payload Schema:
```
{
  "description": "Incoming HTTP POST from Stourio Agent Runtime to MCP Gateway",
  "method": "POST",
  "path": "/execute",
  "headers": {
    "Content-Type": "application/json",
    "Authorization": "Bearer <MCP_SHARED_SECRET>"
  },
  "body": {
    "tool_name": "get_system_metrics",
    "arguments": {
      "component": "web-server-01",
      "metric": "cpu"
    }
  },
  "expected_response": {
    "status": "200 OK",
    "content_type": "application/json",
    "body": "JSON object. The LLM parses this directly as the tool result."
  }
}

```
### Default rules
The system seeds these safety rules on first start:
| Rule | Pattern | Action |
|---|---|---|
| prevent_db_drop | `DROP (DATABASE\|TABLE)` | Require approval |
| block_ssh_root | `ssh root@` | Hard reject |
| block_rm_rf | `rm -rf /` | Hard reject |
| auto_scale_cpu | `CPU > 9x%` | Trigger automation |

### Running with Docker Desktop (GUI)

First-time setup requires a terminal (see Quick Start above). After that:

### Day-to-day usage
1. Open Docker Desktop on your Mac or Windows machine.
2. Click Containers in the left sidebar.
3. You will see a container group called stourio with four services:
   - `stourio-postgres-1` (database)
   - `stourio-redis-1` (cache/queue)
   - `stourio-stourio-1` (the framework)
   - `stourio-n8n-1` (automation engine)
4. Click the **Play** button (▶) on the `stourio` group to start everything
5. Click the **Stop** button (■) to shut it down
6. The **port `8000:8000`** link next to the container name opens the API in your browser
7. The **port `5678:5678`** link opens the n8n automation dashboard

### Accessing the framework

Once running (green status in Docker Desktop):

- Open `http://localhost:8000/docs` in your browser for the interactive API explorer
- You can send requests directly from that page (click any endpoint, then "Try it out")
- No terminal, no curl commands needed

### Updating your .env

If you change API keys or settings in `.env`:

1. Stop the containers in Docker Desktop (■)
2. Edit the `.env` file with any text editor
3. Start the containers again (▶)

### If something breaks

In Docker Desktop, click on the `stourio-stourio-1` container, then the **Logs** tab. All errors will be visible there. If you need a full rebuild after code changes, you'll need one terminal command: `docker-compose up --build`

## License

Apache License 2.0 — see [LICENSE](../LICENSE).