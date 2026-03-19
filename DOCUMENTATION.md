# Stourioclaw Documentation

Complete reference for configuring and operating stourioclaw — a self-hosted AI agentic operating system.

---

## Table of Contents

- [System Overview](#system-overview)
- [Getting Started](#getting-started)
- [Agents](#agents)
- [Daemon Agents](#daemon-agents)
- [Inter-Agent Messaging](#inter-agent-messaging)
- [MCP Client — External Tool Servers](#mcp-client--external-tool-servers)
- [Tools Reference](#tools-reference)
- [Cron Jobs](#cron-jobs)
- [Browser Automation](#browser-automation)
- [Security](#security)
- [Rules Engine](#rules-engine)
- [Orchestrator](#orchestrator)
- [RAG Knowledge Base](#rag-knowledge-base)
- [Telegram Integration](#telegram-integration)
- [Admin Panel](#admin-panel)
- [API Reference](#api-reference)
- [Database Schema](#database-schema)
- [Redis Key Patterns](#redis-key-patterns)
- [Docker Services](#docker-services)
- [Environment Variables](#environment-variables)
- [Cost Tracking](#cost-tracking)
- [Troubleshooting](#troubleshooting)

---

## System Overview

Stourioclaw processes signals (user messages, webhooks, cron triggers) through a layered pipeline:

```
Input (Telegram / Webhook / Cron / Daemon Heartbeat)
  |
Kill Switch ── halts everything if active
  |
Rules Engine ── deterministic pattern match (regex/keyword)
  |  (no match)
LLM Orchestrator ── routes to the right agent via OpenRouter
  |
Agent Execution ── step loop: reason → act → observe → repeat
  |                 tools: local registry + MCP servers
  |                 delegation: agents can spawn sub-agents
  |
Security Layer ── inline interceptor (pre-execution)
  |                passive auditor (post-execution, background)
  |
Approval Flow ── human-in-the-loop via Telegram buttons
  |
Audit Trail ── immutable log of every action
```

**Background processes running at all times:**
- Signal consumer (dequeues webhook signals from Redis stream)
- Approval escalation (checks for stalling approvals)
- Security auditor (scans audit logs for anomalies every 60s)
- Scheduler (checks for due cron jobs every 30s)
- Daemon manager (manages persistent agent lifecycles)

---

## Getting Started

### Prerequisites
- Docker and Docker Compose
- A Telegram account

### Setup

```bash
git clone https://github.com/catalinprg/stourioclaw.git
cd stourioclaw
cp .env.example .env
```

Edit `.env` with your values:

```
OPENROUTER_API_KEY=your-openrouter-key     # Required
TELEGRAM_BOT_TOKEN=your-bot-token          # Required (from @BotFather)
TELEGRAM_ALLOWED_USER_IDS=[your-user-id]   # Required (from @userinfobot)
TELEGRAM_WEBHOOK_SECRET=any-random-string  # Required
```

`STOURIO_API_KEY` is auto-generated on first startup. Check the logs or `.env` file after starting.

```bash
docker compose up -d
```

The system is ready when you see `Ready.` in the logs. Message your bot on Telegram.

### Local Development (Polling Mode)

```
TELEGRAM_USE_POLLING=true
```

No public URL needed. The bot polls Telegram for updates.

### VPS Deployment

Set `TELEGRAM_WEBHOOK_URL=https://your-domain.com/api/telegram/webhook` and expose port 8000 via reverse proxy (nginx/caddy) with HTTPS.

---

## Agents

Agents are the core execution units. Each agent has a model, system prompt, tools, and execution mode.

### Creating an Agent

Via API:
```bash
curl -X POST http://localhost:8000/api/agents \
  -H "X-STOURIO-KEY: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "analyst",
    "display_name": "Analyst",
    "description": "Data analysis, research, structured reasoning",
    "system_prompt": "You are a data analyst. Analyze data, produce structured reports.",
    "model": "anthropic/claude-sonnet-4-20250514",
    "tools": ["web_search", "call_api", "generate_report", "read_file", "query_data"],
    "max_steps": 10,
    "max_concurrent": 3
  }'
```

Via admin panel: `http://localhost:8000/admin` → Agents → Create.

### Agent Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | required | Unique identifier (alphanumeric + underscore) |
| `display_name` | string | required | Human-readable name |
| `description` | string | "" | What this agent does (shown to orchestrator for routing) |
| `system_prompt` | string | "" | Instructions for the agent's LLM |
| `model` | string | required | LLM model via OpenRouter (e.g., `anthropic/claude-sonnet-4-20250514`) |
| `tools` | list[string] | [] | Tool names this agent can use |
| `max_steps` | int | 8 | Maximum reasoning steps per execution (1-50) |
| `max_concurrent` | int | 3 | Max parallel executions of this agent (1-20) |
| `execution_mode` | string | "oneshot" | `oneshot` (execute and return) or `daemon` (persistent) |
| `daemon_config` | object | null | Daemon settings (see [Daemon Agents](#daemon-agents)) |
| `mcp_servers` | list[string] | [] | MCP server names this agent can access |
| `allowed_peers` | list[string] | [] | Agent names allowed to send messages to this agent |
| `is_active` | bool | true | Whether agent is available for routing |
| `is_system` | bool | false | System agents cannot be deleted |

### Default Agent

The only pre-configured agent is **CyberSecurity**:

| Name | Model | Tools | Purpose |
|------|-------|-------|---------|
| `cybersecurity` | openai/gpt-4o | read_audit_log, send_notification | Monitors all agent actions for security threats |

This agent is flagged `is_system: true` and cannot be deleted.

### How Agents Execute

1. Orchestrator selects an agent based on the input
2. Agent loads its system prompt, tools, and conversation history
3. Semantic memory recall — searches knowledge base for relevant past experiences
4. **Step loop** (up to `max_steps`):
   - LLM reasons about the objective with available tools
   - If LLM calls a tool → execute it (with security check), feed result back
   - If LLM responds without tool call → execution complete
5. Result delivered to user (Telegram) and logged to audit trail
6. Execution persisted as agent memory for future recall

### Concurrency

Each agent type has a semaphore limiting parallel executions. Default: 3. Override per agent via `max_concurrent` or globally via `AGENT_CONCURRENCY_CONFIG`.

---

## Daemon Agents

Persistent agents that run continuously with a heartbeat loop. Use for monitoring, watching, or autonomous operation.

### Creating a Daemon

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "X-STOURIO-KEY: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "watchdog",
    "display_name": "Watchdog",
    "description": "Monitors audit logs for anomalies",
    "system_prompt": "You are a security watchdog. Monitor audit logs and alert on anomalies.",
    "model": "openai/gpt-4o-mini",
    "tools": ["read_audit_log", "send_notification", "heartbeat_ack", "read_messages"],
    "execution_mode": "daemon",
    "daemon_config": {
      "tick_seconds": 300,
      "heartbeat_prompt": "Check the last hour of audit logs for anomalies. Check your inbox for messages. If nothing needs attention, call heartbeat_ack.",
      "active_hours": {"start": "08:00", "end": "22:00"},
      "max_messages_per_cycle": 10
    }
  }'
```

### Daemon Config

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tick_seconds` | int | 300 | Heartbeat interval in seconds |
| `heartbeat_prompt` | string | "Check inbox..." | Objective for each heartbeat cycle |
| `active_hours` | object/null | null | Time window for execution (`{"start": "HH:MM", "end": "HH:MM"}` in UTC). Null = 24/7 |
| `max_messages_per_cycle` | int | 10 | Max inbox messages processed per cycle |

### Daemon Lifecycle

**Startup:** All active daemons launch automatically when the system starts.

**Cycle:** Each tick (or inbox message), the daemon:
1. Checks if within `active_hours` — skips if outside window
2. Reads up to `max_messages_per_cycle` from inbox
3. Builds objective: inbox messages + `heartbeat_prompt`
4. Executes one agent cycle (same runtime as oneshot agents)
5. If agent called `heartbeat_ack` → output suppressed (not sent to Telegram)
6. Otherwise → result delivered to Telegram

**Wake triggers:**
- Tick timeout (`tick_seconds` elapsed)
- Inbox message (instant wake via Redis pub/sub)

**Crash recovery:** If a daemon crashes, the manager auto-restarts it after 10 seconds.

**Restart behavior:** On container restart, daemons launch fresh. No checkpointing. Inbox messages are durable (Redis streams) — nothing is lost.

**Conversation context:** Each daemon maintains a persistent conversation ID (`daemon:{agent_name}`), so conversation history accumulates across cycles.

### Controlling Daemons

```bash
# Start a daemon
POST /api/daemons/{name}/start

# Stop (finishes current cycle, then stops)
POST /api/daemons/{name}/stop

# Restart
POST /api/daemons/{name}/restart
```

These work at runtime — no container restart needed.

---

## Inter-Agent Messaging

Agents can send asynchronous messages to each other via inbox streams.

### How It Works

Each agent has an inbox — a Redis stream at `stourio:inbox:{agent_name}`. Messages are fire-and-forget: the sender doesn't wait for a response.

### Access Control

Messaging is **open by default** — any agent can message any other agent. If you need to restrict messaging for a specific agent, set its `allowed_peers` to a list of permitted senders:

```bash
# Only allow "watchdog" to receive messages from "monitor" and "scheduler"
curl -X PUT http://localhost:8000/api/agents/watchdog \
  -H "X-STOURIO-KEY: your-key" \
  -H "Content-Type: application/json" \
  -d '{"allowed_peers": ["monitor", "scheduler"]}'
```

- `allowed_peers: []` (empty, default) → all agents can message this agent
- `allowed_peers: ["agent1", "agent2"]` → only listed agents can message

System-level routing (orchestrator, cron, webhooks) always bypasses `allowed_peers`.

### Message Delivery

- **To a running daemon:** Message wakes the daemon immediately via pub/sub. Processed on the next cycle.
- **To a non-running agent:** System falls back to oneshot execution with the message as the objective. No dead letters.

### Message Limits

- Max message size: 10,000 characters
- Max messages per daemon cycle: configurable via `max_messages_per_cycle` (default 10)
- Remaining messages stay queued (FIFO) for the next cycle

### Tools

| Tool | Description |
|------|-------------|
| `send_message` | Send a message to another agent. Parameters: `target_agent` (required), `message` (required), `context` (optional). |
| `read_messages` | Check your own inbox. Returns pending messages. Parameter: `limit` (default 10). Automatically scoped to the calling agent — agents cannot read other inboxes. |
| `heartbeat_ack` | Daemon signals "nothing to report." No parameters. Suppresses output delivery for this cycle. |

---

## MCP Client — External Tool Servers

Connect external MCP (Model Context Protocol) servers so agents can use their tools. Examples: Notion, GitHub, Slack, databases, custom APIs.

### Connecting an MCP Server

**Step 1:** Set the auth secret in your `.env`:

```
NOTION_MCP_TOKEN=your-notion-integration-token
```

**Step 2:** Register the server:

```bash
curl -X POST http://localhost:8000/api/mcp-servers \
  -H "X-STOURIO-KEY: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "notion",
    "endpoint_url": "http://localhost:3456/sse",
    "transport": "sse",
    "auth_env_var": "NOTION_MCP_TOKEN"
  }'
```

The system connects immediately, discovers all tools, and caches them.

**Step 3:** Assign to an agent:

```bash
curl -X PUT http://localhost:8000/api/agents/assistant \
  -H "X-STOURIO-KEY: your-key" \
  -H "Content-Type: application/json" \
  -d '{"mcp_servers": ["notion"]}'
```

The agent now sees all Notion tools alongside its local tools.

### Transport Types

| Transport | When to Use | Config |
|-----------|-------------|--------|
| `sse` | Server exposes an HTTP SSE endpoint | `endpoint_url`: the URL |
| `stdio` | Server runs as a subprocess | `endpoint_command`: the shell command (must be in `MCP_STDIO_ALLOWED_COMMANDS` allowlist) |

`endpoint_url` and `endpoint_command` are mutually exclusive.

### Tool Naming

MCP tools are namespaced: `{server_name}__{tool_name}`. Example: `notion__search`, `github__list_prs`. The agent sees these as normal tools — it doesn't need to know they're remote.

### Security

All MCP tools are classified as **EXTERNAL_RISK** by the security interceptor. If the tool arguments contain sensitive patterns (API keys, tokens, passwords), the call is intercepted and requires human approval via Telegram.

### Auth Resolution

Auth tokens are resolved from environment variables **at connection time**, not at startup. This means you can add a new secret to the environment and register the server via API without restarting the container.

### Managing MCP Servers

```bash
# List all servers + discovered tools
GET /api/mcp-servers

# Re-discover tools (if the server added new capabilities)
POST /api/mcp-servers/{name}/refresh

# Remove a server
DELETE /api/mcp-servers/{name}
```

---

## Tools Reference

### Built-in Tools

| Tool | Description | Risk | Approval Required |
|------|-------------|------|-------------------|
| `web_search` | Search the web via Tavily | Low | No |
| `read_file` | Read file from workspace | Low | No |
| `write_file` | Write file to workspace | **High** | **Yes, always** |
| `execute_code` | Run Python or bash | **High** | **Yes, always** |
| `call_api` | HTTP requests to external APIs | External | Only if sensitive data detected |
| `send_notification` | Send Telegram notification | External | Only if sensitive data detected |
| `query_data` | Parse CSV/JSON data | Low | No |
| `search_knowledge` | Semantic search over RAG | Low | No |
| `read_audit_log` | Query the audit trail | Low | No |
| `generate_report` | Create markdown reports | Low | No |
| `delegate_to_agent` | Delegate to another agent | External | Only if sensitive data detected |
| `browser_action` | Web page automation | External | Only if sensitive data detected |
| `send_message` | Message another agent | Low | No |
| `read_messages` | Check your inbox | Low | No |
| `heartbeat_ack` | Signal "nothing to report" | Low | No |

### Tool Parameters (Detailed)

#### web_search
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | yes | - | Search query |
| `max_results` | int | no | 5 | Number of results |

#### read_file / write_file
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | yes | Relative path within `WORKSPACE_DIR` |
| `content` | string | yes (write only) | File content to write |

Path traversal is blocked — agents cannot access files outside the workspace.

#### execute_code
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `code` | string | yes | - | Code to execute |
| `language` | string | no | "python" | `python` or `bash` |
| `timeout` | int | no | 30 | Timeout in seconds |

**Sandboxed execution:** By default, code runs in a disposable Docker container with no network access, no environment variables, read-only filesystem, and memory/CPU limits. If Docker is unavailable (local dev), falls back to subprocess with stripped environment. Configure via `CODE_SANDBOX_*` env vars.

#### call_api
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | yes | - | Target URL |
| `method` | string | no | "GET" | GET, POST, PUT, PATCH, DELETE |
| `headers` | object | no | - | HTTP headers |
| `body` | object | no | - | JSON body |
| `timeout` | int | no | 30 | Timeout in seconds |

Response body capped at 1MB.

#### browser_action
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | yes | `navigate`, `click`, `type`, `screenshot`, `extract_text`, `get_url`, `close_session` |
| `session_id` | string | no | Reuse existing browser session |
| `url` | string | for navigate | URL to navigate to |
| `selector` | string | for click/type/extract | CSS selector |
| `text` | string | for type | Text to type |
| `full_page` | bool | no | Full page screenshot (default false) |
| `max_length` | int | no | Max text for extract_text (default 10,000) |

Pages persist across calls via `session_id`. First call returns a `session_id` — pass it to subsequent calls for multi-step workflows.

#### delegate_to_agent
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agent_type` | string | yes | Target agent name |
| `objective` | string | yes | Task for the sub-agent |
| `context` | string | no | Additional context |
| `conversation_id` | string | no | For context continuity |

Depth-limited to 3 levels. Synchronous — caller waits for sub-agent to complete.

---

## Cron Jobs

Schedule agents to run automatically on a cron expression.

### Creating a Cron Job

```bash
curl -X POST http://localhost:8000/api/cron \
  -H "X-STOURIO-KEY: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "daily-report",
    "schedule": "0 9 * * *",
    "agent_type": "analyst",
    "objective": "Generate a daily summary of all agent activity"
  }'
```

### Cron Expression Format

Standard 5-field cron: `minute hour day-of-month month day-of-week`

| Expression | Meaning |
|------------|---------|
| `*/5 * * * *` | Every 5 minutes |
| `0 9 * * *` | Daily at 9:00 AM |
| `0 */6 * * *` | Every 6 hours |
| `0 9 * * 1-5` | Weekdays at 9:00 AM |

### How It Works

The scheduler checks every `SCHEDULER_TICK_SECONDS` (default 30s) for due jobs. When a job is due, it calls the specified agent directly via the agent pool — no LLM routing needed since the target agent is already specified.

### Managing Cron Jobs

```bash
GET /api/cron                    # List all jobs
POST /api/cron                   # Create
DELETE /api/cron/{name}          # Delete
POST /api/cron/{name}/toggle     # Enable/disable
```

---

## Browser Automation

Agents with the `browser_action` tool can interact with web pages using a Chromium browser (Playwright).

### Domain Allowlist

Restrict which sites agents can visit:

```
BROWSER_ALLOWED_DOMAINS=["example.com","docs.python.org","github.com"]
```

Empty list = allow all domains. Subdomains are automatically included (e.g., `github.com` allows `api.github.com`).

### Multi-Step Workflows

Pages persist across tool calls via `session_id`:

1. Agent calls `browser_action(action="navigate", url="https://example.com")` → gets `session_id`
2. Agent calls `browser_action(action="click", selector="#login", session_id="...")` → same page
3. Agent calls `browser_action(action="screenshot", session_id="...")` → captures current state
4. Agent calls `browser_action(action="close_session", session_id="...")` → cleanup

### Docker Configuration

Chromium requires shared memory. The `docker-compose.yml` sets `shm_size: 2gb` on the app container. If you see browser crashes, increase this value.

---

## Security

### Layers

1. **Kill Switch** — immediately halts all operations. Activated via `POST /api/kill`, deactivated via `POST /api/resume`. Flushes LLM response cache on activation.

2. **Rules Engine** — deterministic pattern matching before LLM routing. Can block, require approval, or force-route to a specific agent.

3. **Security Interceptor** — pre-execution check on every tool call:
   - **HIGH_RISK tools** (`write_file`, `execute_code`): always intercepted, always require approval
   - **EXTERNAL_RISK tools** (`call_api`, `send_notification`, `delegate_to_agent`, `browser_action`, all MCP tools): intercepted if arguments contain sensitive patterns
   - **Sensitive patterns detected**: API keys, secrets, passwords, tokens, credentials, OpenAI keys (`sk-*`), GitHub PATs (`ghp_*`)

4. **Approval Workflow** — intercepted tool calls create an approval request. You receive a Telegram message with Approve/Reject buttons. Auto-expires after `APPROVAL_TTL_SECONDS` (default 300s).

5. **Passive Security Auditor** — background worker scans audit logs every 60 seconds for:
   - High frequency: >30 actions per agent per interval
   - Repeated failures: >10 errors per agent per interval
   - Creates security alerts in the DB

6. **Fencing Tokens** — distributed locking prevents stale agent processes from corrupting state after lock expiration.

7. **Audit Trail** — every action logged immutably: signals received, routing decisions, tool calls, approvals, errors. Query via `GET /api/audit` or `read_audit_log` tool.

### Peer Access Control

Agent-to-agent messaging is open by default. If `allowed_peers` is set on an agent, only listed agents can message it. Empty `allowed_peers` (default) allows all agents. System-level routing (orchestrator, cron) always bypasses this check.

---

## Rules Engine

Deterministic pattern matching that runs before LLM routing. Rules are evaluated in order — first match wins.

### Creating Rules

```bash
curl -X POST http://localhost:8000/api/rules \
  -H "X-STOURIO-KEY: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "block-production-deploys",
    "pattern": "deploy.*production",
    "pattern_type": "regex",
    "action": "hard_reject",
    "risk_level": "critical"
  }'
```

### Pattern Types

| Type | Description |
|------|-------------|
| `regex` | Regular expression match on input content |
| `keyword` | Exact keyword match (case-insensitive) |
| `event_type` | Match on webhook signal `event_type` field |

### Actions

| Action | Behavior |
|--------|----------|
| `hard_reject` | Block immediately, return rejection message |
| `require_approval` | Create approval request, wait for human decision |
| `force_agent` | Bypass LLM routing, send directly to specified agent (set `config.agent_type`) |
| `allow` | Pass through to LLM routing |

---

## Orchestrator

The orchestrator is a lightweight LLM (default: `gpt-4o-mini`) that makes routing decisions. It sees the input and the list of available agents, then decides:

1. **Route to agent** — with an objective and risk level
2. **Respond directly** — answer without involving any agent
3. **Request more info** — ask the user for clarification

The orchestrator does not execute anything. It's a router.

### Routing to Daemons

When the orchestrator routes to an agent with `execution_mode: daemon`:
- If the daemon is **running**: message is enqueued to its inbox (instant wake via pub/sub)
- If the daemon is **not running**: falls back to oneshot execution (no dead letters)

### Customizing the Orchestrator Model

```
ORCHESTRATOR_MODEL=openai/gpt-4o-mini
```

Use a fast, cheap model. The orchestrator makes simple routing decisions — it doesn't need a powerful model.

---

## RAG Knowledge Base

Semantic search over documents using pgvector embeddings.

### Ingesting Documents

Place `.md` files in `RUNBOOKS_DIR` (default `/app/docs`). The system chunks them by headers, embeds via OpenAI `text-embedding-3-small`, and stores in PostgreSQL.

Requires `OPENAI_API_KEY` to be set.

### Querying

Agents with the `search_knowledge` tool can search the knowledge base. Results are ranked by cosine similarity, optionally reranked by Cohere (if `COHERE_API_KEY` is set).

### Agent Memory

After each execution, agent results are embedded and stored as `agent_memory` type. On future executions, relevant past memories are recalled (up to `AGENT_MEMORY_RECALL_COUNT`, default 3).

---

## Telegram Integration

### Setup

1. Create a bot via [@BotFather](https://t.me/BotFather)
2. Get your user ID from [@userinfobot](https://t.me/userinfobot)
3. Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_IDS`, `TELEGRAM_WEBHOOK_SECRET`

### Features

- **Text messages** → processed by orchestrator
- **Voice messages** → transcribed via Whisper, then processed (requires `OPENAI_API_KEY`)
- **Photos** → analyzed via vision model, description processed
- **Approval buttons** → inline keyboard for approve/reject on intercepted tool calls
- **Daemon output** → non-suppressed daemon results delivered as messages

### Polling vs Webhook

| Mode | Config | Use Case |
|------|--------|----------|
| Polling | `TELEGRAM_USE_POLLING=true` | Local development |
| Webhook | `TELEGRAM_WEBHOOK_URL=https://...` | Production (requires HTTPS) |

---

## Admin Panel

Access at `http://localhost:8000/admin`. Login with your `STOURIO_API_KEY`.

### Views

| View | Description |
|------|-------------|
| Console | Real-time signal + agent activity |
| Agents | Create, edit, delete agents |
| Security | View and manage security alerts |
| Telegram | Bot status, allowed users |
| Rules | Manage deterministic rules |
| Audit | Query audit log with filters |
| Costs | Token usage and cost tracking per model/agent |
| Deployment | VPS setup instructions, webhook status |

---

## API Reference

All endpoints require header: `X-STOURIO-KEY: {your-api-key}`

Base URL: `http://localhost:8000/api`

### System
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/status` | System status (kill switch, agents, daemons, MCP servers) |
| POST | `/kill` | Activate kill switch |
| POST | `/resume` | Deactivate kill switch |
| GET | `/audit?limit=50` | Recent audit log entries |

### Agents
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/agents` | List active agents |
| POST | `/agents` | Create agent |
| PUT | `/agents/{name}` | Update agent |
| DELETE | `/agents/{name}` | Delete agent (not system agents) |

### Daemons
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/daemons/{name}/start` | Start daemon |
| POST | `/daemons/{name}/stop` | Stop daemon (graceful) |
| POST | `/daemons/{name}/restart` | Restart daemon |

### Cron Jobs
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/cron` | List cron jobs |
| POST | `/cron` | Create cron job |
| DELETE | `/cron/{name}` | Delete cron job |
| POST | `/cron/{name}/toggle?active=bool` | Enable/disable |

### MCP Servers
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/mcp-servers` | List servers + tools |
| POST | `/mcp-servers` | Register server |
| DELETE | `/mcp-servers/{name}` | Remove server |
| POST | `/mcp-servers/{name}/refresh` | Re-discover tools |

### Approvals
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/approvals` | Pending approvals |
| POST | `/approvals/{id}` | Approve or reject |

### Rules
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/rules` | List rules |
| POST | `/rules` | Create rule |
| DELETE | `/rules/{id}` | Delete rule |

### Webhooks
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/webhook` | Ingest external signal (async, returns 202) |

### Usage
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/usage?from_date=&to_date=` | Token usage summary |
| GET | `/usage/summary?group_by=` | Grouped usage (model/agent/provider) |

### Security Alerts
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/security/alerts` | Open alerts |
| POST | `/security/alerts/{id}` | Update status |

### MCP Server (Outbound)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/mcp/sse` | SSE stream for MCP clients (e.g., Claude Code) |
| GET | `/mcp/tools` | List available tools (REST) |

---

## Database Schema

PostgreSQL with pgvector extension. 9 tables:

| Table | Purpose |
|-------|---------|
| `agents` | Agent definitions (name, model, tools, mode, config) |
| `audit_log` | Immutable action trail |
| `conversation_messages` | Chat history per conversation |
| `rules` | Deterministic routing rules |
| `approvals` | Pending/resolved approval requests |
| `document_chunks` | RAG knowledge base with vector embeddings |
| `token_usage` | LLM cost tracking per call |
| `security_alerts` | Detected anomalies |
| `cron_jobs` | Scheduled agent execution |
| `mcp_servers` | External MCP server connections |

Migrations managed via Alembic in `migrations/versions/`.

---

## Redis Key Patterns

| Pattern | Type | Purpose |
|---------|------|---------|
| `stourio:kill_switch` | String | Kill switch flag |
| `stourio:signals` | Stream | Webhook signal queue |
| `stourio:consumer_group` | Consumer Group | Signal processing |
| `stourio:lock:{resource}` | String | Distributed locks with fencing tokens |
| `stourio:approval:{id}` | String (JSON) | Approval cache (TTL-based) |
| `stourio:llm_cache:*` | String (JSON) | LLM response cache (flushed on kill) |
| `stourio:inbox:{agent}` | Stream | Agent inbox messages |
| `stourio:inbox_group:{agent}` | Consumer Group | Inbox processing |
| `stourio:inbox_notify:{agent}` | Pub/Sub Channel | Daemon wake notification |
| `stourio:daemon:events` | Pub/Sub Channel | Daemon control (start/stop/restart) |

---

## Docker Services

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `postgres` | pgvector/pgvector:pg16 | 5432 (internal) | Primary datastore + vector search |
| `redis` | redis:7-alpine | 6379 (internal) | Caching, queues, pub/sub, locks |
| `jaeger` | jaegertracing/all-in-one:1.50 | 16686 (localhost) | Distributed tracing UI |
| `stourioclaw` | Dockerfile | 8000 (external) | Application server |

Memory: Redis capped at 256MB (allkeys-lru). Chromium gets 2GB shared memory (`shm_size`).

---

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_ALLOWED_USER_IDS` | Authorized Telegram user IDs |
| `TELEGRAM_WEBHOOK_SECRET` | Webhook verification secret |

### Auto-Generated

| Variable | Description |
|----------|-------------|
| `STOURIO_API_KEY` | System API key (auto-generated on first startup if empty) |

### Optional — LLM

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_DEFAULT_MODEL` | `anthropic/claude-sonnet-4-20250514` | Default agent model |
| `ORCHESTRATOR_MODEL` | `openai/gpt-4o-mini` | Routing model |
| `OPENROUTER_FALLBACK_MODELS` | `[]` | Fallback models |
| `VISION_MODEL` | `openai/gpt-4o` | Image analysis model |

### Optional — Embeddings & RAG

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | - | For embeddings + voice transcription |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model |
| `COHERE_API_KEY` | - | For result reranking |

### Optional — Infrastructure

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_PASSWORD` | `changeme` | Database password |
| `REDIS_PASSWORD` | `changeme` | Redis password |
| `DATABASE_URL` | (see .env.example) | PostgreSQL connection |
| `REDIS_URL` | (see .env.example) | Redis connection |

### Optional — Security

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_AGENT_DEPTH` | `4` | Max delegation depth |
| `APPROVAL_TTL_SECONDS` | `300` | Approval timeout |
| `SECURITY_AUDIT_INTERVAL_SECONDS` | `60` | Audit scan interval |
| `SECURITY_INLINE_ENABLED` | `true` | Enable tool interception |

### Optional — Features

| Variable | Default | Description |
|----------|---------|-------------|
| `SEARCH_API_KEY` | - | Tavily web search |
| `BROWSER_ALLOWED_DOMAINS` | `[]` | Domain allowlist (empty = all) |
| `BROWSER_HEADLESS` | `true` | Headless Chromium |
| `SCHEDULER_TICK_SECONDS` | `30` | Cron check interval |
| `DAEMON_MANAGER_ENABLED` | `true` | Enable daemons |
| `DAEMON_DEFAULT_TICK_SECONDS` | `300` | Default heartbeat interval |
| `CODE_SANDBOX_ENABLED` | `true` | Run code in Docker containers |
| `CODE_SANDBOX_IMAGE` | `python:3.12-slim` | Docker image for sandbox |
| `CODE_SANDBOX_MEMORY` | `256m` | Memory limit per execution |
| `CODE_SANDBOX_CPUS` | `0.5` | CPU limit per execution |
| `MCP_CLIENT_TIMEOUT` | `30` | MCP connection timeout |
| `MCP_STDIO_ALLOWED_COMMANDS` | `[]` | Stdio command allowlist |

### Optional — Memory & Caching

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_MEMORY_TTL_DAYS` | `90` | Memory retention |
| `AGENT_MEMORY_RECALL_COUNT` | `3` | Memories per execution |
| `CONVERSATION_HISTORY_LIMIT` | `20` | Messages in context |
| `CACHE_ENABLED` | `true` | LLM response cache |

---

## Cost Tracking

Every LLM call is tracked with token counts and estimated cost. View via:

```bash
# Usage summary
GET /api/usage?from_date=2026-03-01&to_date=2026-03-19

# Grouped by agent, model, or provider
GET /api/usage/summary?group_by=agent_template
GET /api/usage/summary?group_by=model
```

Supported models for cost estimation: OpenAI (gpt-4o, gpt-4o-mini), Anthropic (claude-sonnet, claude-opus), Google Gemini, DeepSeek, Cohere.

Set `COST_ALERT_DAILY_THRESHOLD` to get alerts when daily spend exceeds a threshold.

---

## Troubleshooting

### System won't start
Check required env vars: `OPENROUTER_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_IDS`. Logs will show which are missing.

### Bot doesn't respond
- Verify `TELEGRAM_ALLOWED_USER_IDS` matches your Telegram user ID
- Check `docker compose logs stourioclaw` for errors
- If using webhook mode, ensure `TELEGRAM_WEBHOOK_URL` is accessible via HTTPS

### Kill switch is stuck
```bash
curl -X POST http://localhost:8000/api/resume -H "X-STOURIO-KEY: your-key"
```

### Agent times out
Increase `max_steps` on the agent. Default is 8 — complex tasks may need 15-20.

### MCP server won't connect
- Verify the env var for auth is set: `echo $NOTION_MCP_TOKEN`
- Check the endpoint is reachable from inside Docker
- Check logs: `docker compose logs stourioclaw | grep mcp`

### Daemon not waking on messages
- Verify the daemon is running: `GET /api/status` → check `daemons` section
- Verify the sender is in `allowed_peers` of the target agent
- Check Redis connectivity: daemon inbox uses pub/sub for wake notifications

### Browser crashes
Increase `shm_size` in `docker-compose.yml`. Default is 2GB. Chromium needs shared memory for rendering.

### High token costs
- Use cheaper models for simple agents (`openai/gpt-4o-mini`)
- Reduce `max_steps` where possible
- Increase daemon `tick_seconds` to reduce heartbeat frequency
- Monitor via `GET /api/usage/summary?group_by=agent_template`
