# Stourioclaw: Personal AI Transformation Design

**Date:** 2026-03-19
**Status:** Approved
**Approach:** Incremental Refactor (Approach A)

## Overview

Transform Stourioclaw from a business-grade SRE/ops platform into a self-hosted personal AI assistant. Single VPS deployment via Docker Compose. Inspired by OpenClaw (multi-channel personal AI agent) and NemoClaw (security-first agent runtime).

## Key Decisions

- **LLM Provider:** OpenRouter as single gateway. Model configurable per agent/orchestrator.
- **Input:** Telegram (webhook mode) + webhook APIs. No chat interface.
- **Deployment:** Single VPS, Docker Compose, 4 services (core, postgres, redis, jaeger).
- **Agents:** 6 seed agents, DB-backed, auto-deployable via API/admin panel.
- **Security:** Hybrid CyberSecurity — inline interceptor for high-risk actions, passive audit for everything else.
- **Claude Code:** MCP integration preserved, pointing to merged core server.

---

## Section 1: OpenRouter Adapter & Provider Consolidation

### Removed
- `src/adapters/` — all provider-specific adapters (OpenAI, Anthropic, Google, DeepSeek)
- Provider-specific API key env vars (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`)
- Provider routing logic in config

### Added
- Single `src/adapters/openrouter.py` adapter
- One env var: `OPENROUTER_API_KEY`
- OpenRouter uses OpenAI-compatible API format (`https://openrouter.ai/api/v1/chat/completions`)
- Each agent/orchestrator specifies a model string (e.g., `anthropic/claude-sonnet-4-20250514`, `openai/gpt-4o`)
- Model selection stored per agent in DB, configurable via admin panel and API
- Fallback model configurable globally via `OPENROUTER_DEFAULT_MODEL`

### Default Model Assignments
```yaml
orchestrator_model: "openai/gpt-4o-mini"  # fast, cheap for routing

agents:
  assistant:      "anthropic/claude-sonnet-4-20250514"
  analyst:        "anthropic/claude-sonnet-4-20250514"
  code_writer:    "anthropic/claude-sonnet-4-20250514"
  code_reviewer:  "anthropic/claude-sonnet-4-20250514"
  cybersecurity:  "openai/gpt-4o"
  intel:          "anthropic/claude-opus-4-20250918"  # deep reasoning
```

### Embeddings Strategy
OpenRouter does not provide an embeddings API. Embeddings are handled separately:
- **Provider:** OpenAI directly (retain `OPENAI_API_KEY` solely for embeddings)
- **Model:** `text-embedding-3-small` (1536 dimensions, matches existing `document_chunks` schema)
- **Adapter:** Keep a minimal `src/adapters/embeddings.py` — isolated from LLM adapter, single responsibility
- **Fallback option:** If avoiding any OpenAI dependency, switch to local `sentence-transformers/all-MiniLM-L6-v2` (384 dims) — requires re-indexing `document_chunks` and updating `embedding_dimension` in config
- **Env var:** `OPENAI_API_KEY` (retained, used only for embeddings) or `EMBEDDING_MODEL=local` to use sentence-transformers

### OpenRouter Failover
OpenRouter is a single point of failure for LLM inference. Mitigation:
- **Primary:** Use OpenRouter's built-in `route: "fallback"` parameter — if the requested model is down, OpenRouter routes to an equivalent model automatically
- **Secondary:** Config flag `OPENROUTER_FALLBACK_ENABLED=true` with `OPENROUTER_FALLBACK_MODELS` (comma-separated list) passed via OpenRouter's `models` array parameter
- **Monitoring:** Track OpenRouter error rates in `token_usage`. If error rate exceeds threshold (configurable, default 50% over 5 min window), emit a CyberSecurity alert and pause non-critical agent work
- **Not implemented (future):** Direct-to-provider fallback adapter. Out of scope for v1.

---

## Section 2: MCP Gateway Merge

### Removed
- Entire `stourio-mcp-engine/` directory
- MCP Docker service from `docker-compose.yml`
- `MCP_SERVER_URL` and `MCP_SHARED_SECRET` env vars
- HTTP calls from core to MCP gateway
- Runbooks, RAG ingestion for runbooks, `/app/docs` directory

### Added
- `src/mcp/router.py` — FastAPI router mounted at `/mcp/execute`
- `src/mcp/registry.py` — tool registry with `@register_tool` decorator (ported from gateway)
- `src/mcp/tools/` — tool implementations

### Key Change
Agent tool execution becomes a direct function call (`mcp_registry.execute(tool_name, arguments)`) instead of HTTP to a separate service. CyberSecurity inline interceptor hooks into this execution path.

### RAG Pipeline Clarification
- **Removed:** Runbook ingestion pipeline (`ingest_runbooks()` function), `/api/documents/ingest` endpoint, `/app/docs` runbook directory
- **Preserved:** `document_chunks` table for agent memory and personal knowledge base. `ingest_text()` function retained for agents to persist learnings. Semantic memory recall via pgvector cosine similarity search.
- The RAG retriever and reranker remain functional for agent memory lookups — only the runbook-specific ingestion path is removed.

### Tool Implementations Manifest
Existing tools ported from MCP gateway (adapted for personal AI use):
- `call_api` — HTTP calls to external APIs (preserved as-is)
- `send_notification` — send messages via configured channels (preserved)
- `generate_report` — structured report generation (preserved)

New tools to implement:
| Tool | Description | External Dependency |
|------|-------------|-------------------|
| `web_search` | Web search via API | SerpAPI or Tavily API key (`SEARCH_API_KEY`) |
| `read_file` | Read file contents from configured workspace directory | None (local filesystem, sandboxed to `WORKSPACE_DIR`) |
| `write_file` | Write/create files in workspace directory | None (local filesystem, sandboxed to `WORKSPACE_DIR`) |
| `execute_code` | Execute Python/shell code in sandboxed subprocess | None (uses `subprocess` with timeout + resource limits) |
| `query_data` | Query structured data (CSV, JSON, SQLite) | None (pandas/sqlite3, local) |
| `search_knowledge` | Semantic search over `document_chunks` via pgvector | None (uses existing retriever) |
| `read_audit_log` | Query `audit_log` table with filters | None (direct DB query) |

All tools registered via `@register_tool` decorator. CyberSecurity interceptor wraps `write_file`, `execute_code`, `call_api`, `send_notification` as high-risk.

### Claude Code MCP Config
The MCP endpoint must implement the MCP protocol (JSON-RPC over SSE), not just REST. The `/mcp/execute` route serves as the SSE transport endpoint. The `@anthropic-ai/mcp-proxy` bridges between Claude Code's stdio transport and our SSE endpoint.

```json
{
  "mcpServers": {
    "stourioclaw": {
      "command": "npx",
      "args": ["-y", "@anthropic-ai/mcp-proxy", "http://localhost:8000/mcp/sse"],
      "env": {
        "STOURIO_API_KEY": "your-api-key"
      }
    }
  }
}
```

Implementation: Use `mcp` Python SDK (`pip install mcp`) to implement the SSE transport at `/mcp/sse`. The SDK handles JSON-RPC protocol compliance. Our tool registry adapts tools to MCP's `Tool` schema automatically.

---

## Section 3: Agent System Redesign

### Removed
- 3 SRE agents (diagnose_repair, escalate, take_action) + YAML configs
- SRE-specific system prompts and tool bindings
- n8n automation integration (`AUTOMATION_WEBHOOK_URL`)

### 6 New Agents

| Agent | Role | Default Tools | Routing Trigger |
|-------|------|---------------|-----------------|
| **Assistant** | General tasks — weather, email, jokes, reminders, quick lookups | call_api, send_notification, web_search | Default fallback — anything that doesn't match another agent |
| **Analyst** | Data analysis, research, system analysis, structured reasoning | call_api, generate_report, read_file, query_data | Requests involving analysis, research, data, comparison, summarization |
| **Code Writer** | Code generation, scripts, file creation | read_file, write_file, execute_code, search_knowledge | Requests to write, create, build, implement code |
| **Code Reviewer** | Reviews Code Writer output, approves or requests changes | read_file, search_knowledge | Auto-triggered after Code Writer completes |
| **CyberSecurity** | Monitors all agent actions, flags threats | read_audit_log, send_notification | Runs as interceptor + audit subscriber, not routed by orchestrator |
| **Intel** | Deep thinking, planning, strategy, complex reasoning | search_knowledge, generate_report | Requests requiring planning, strategy, deep analysis, multi-step reasoning |

### Code Writer -> Code Reviewer Chain
- Orchestrator routes coding tasks to Code Writer
- On completion, output automatically feeds into Code Reviewer
- Code Reviewer approves (response goes to user) or rejects with feedback (loops back to Code Writer, max 3 iterations)
- Reuses existing `chains.yaml` pipeline pattern

Example `chains.yaml` entry:
```yaml
chains:
  code_review:
    type: pipeline
    description: Code generation with review loop
    max_iterations: 3
    steps:
      - agent: code_writer
      - agent: code_reviewer
        condition: "{{ always }}"
        input_mapping:
          code_output: "{{ previous.result }}"
          original_request: "{{ chain.initial_input }}"
        loop_back_to: code_writer
        loop_condition: "{{ previous.verdict == 'rejected' }}"
```

### Orchestrator Dynamic Routing
The orchestrator's `route_to_agent` tool definition must be built dynamically at routing time — not hardcoded. On each routing decision:
1. Query active agents from DB via `agent_registry.list_active()`
2. Build the `enum` array from agent names
3. Include each agent's description in the tool definition so the LLM understands routing options
4. CyberSecurity is excluded from the routing enum (it's not user-routable)
5. Code Reviewer is excluded (only triggered by chains)

This ensures dynamically created agents are immediately routable without restart.

### Automation Routing Path
The current `route_to_automation` tool (n8n integration) is **removed entirely**. The orchestrator system prompt is updated to remove references to "AUTOMATION" as a capability. All work is routed to agents. If workflow automation is needed in the future, it can be implemented as agent tools rather than a separate routing path.

### Agent DB Schema (new table: `agents`)
```
id            ULID, PK
name          string, unique
display_name  string
description   text
system_prompt text
model         string (e.g., "anthropic/claude-sonnet-4-20250514")
tools         JSON array of tool names
max_steps      int, default 8
max_concurrent int, default 3
is_active      bool
is_system      bool (true for 6 seed agents, prevents deletion)
created_at     timestamp
updated_at     timestamp
```

### Auto-Deployment Flow
1. YAML files in `config/agents/` seed the 6 default agents on first boot
2. DB is source of truth at runtime — YAML only for initial seed
3. `POST /api/agents` creates new agent (stored in DB, live immediately)
4. Admin panel provides UI for create/edit/delete/clone
5. No restart needed — agent registry reloads from DB

### Concurrency
- Each agent type has a configurable concurrency pool (default 3)
- Multiple instances of same agent type run in parallel for concurrent tasks
- Semaphore pooling, fencing tokens, heartbeat loops all preserved
- Per-agent concurrency stored in `agents` table (`max_concurrent` column, default 3)
- Adjustable live via admin panel Agent Deployment Manager — updates DB, runtime picks up on next dispatch
- Global default: `AGENT_CONCURRENCY_DEFAULT=3` env var (used when DB value is null)

---

## Section 4: Input Layer — Telegram + Webhooks

### Removed
- `POST /api/chat` endpoint
- Chat interface in admin panel

### Telegram Integration (`src/telegram/`)
- `src/telegram/webhook.py` — route at `POST /api/telegram/webhook`
- `src/telegram/client.py` — Bot API wrapper for sending messages
- `src/telegram/formatter.py` — response to Telegram-friendly markdown, message splitting for >4096 char limit

### Webhook Setup
- On startup, app calls `setWebhook` to register with Telegram automatically
- Secret token verification via `X-Telegram-Bot-Api-Secret-Token` header
- User ID restriction: `TELEGRAM_ALLOWED_USER_IDS` env var rejects messages from unauthorized users

### Message Flow
```
Telegram -> POST /api/telegram/webhook
  -> Verify secret token
  -> Check allowed user IDs
  -> Extract message text + chat_id
  -> Build OrchestratorInput(source=USER, conversation_id=chat_id)
  -> Process through orchestrator
  -> Send response back via Telegram Bot API
```

### Telegram Features
- Typing indicator while processing
- Inline keyboard buttons for approval requests (approve/reject from Telegram)
- CyberSecurity alerts sent as Telegram messages
- Dev mode: `TELEGRAM_USE_POLLING=true` for local development

### Preserved
- `POST /api/webhook` for external system webhooks (async, Redis Streams, 202 response)
- Orchestrator receives same `OrchestratorInput` regardless of source

### Setup (Plug and Play)
1. Create bot with BotFather, get token
2. Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_URL`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_ALLOWED_USER_IDS` in `.env`
3. `docker compose up` — webhook registers automatically

---

## Section 5: CyberSecurity — Hybrid Monitoring

### Inline Interceptor (`src/security/interceptor.py`)
- Hooks into `mcp_registry.execute()` — every tool call passes through
- High-risk triggers:
  - `write_file`, `execute_code` — code execution/file modification
  - `call_api` with external URLs — data exfiltration risk
  - `send_notification` — outbound communication
  - Sensitive keywords in arguments (API keys, tokens, passwords, credentials)
- On flag: execution pauses, Telegram notification with details, waits for approve/reject
- Reuses existing approval flow (`approvals` table + TTL + escalation)

### Passive Auditor (`src/security/auditor.py`)
- Background worker subscribing to audit log stream
- Periodically analyzes recent actions using CyberSecurity agent's LLM (configurable interval, default 60s)
- Detects: unusual tool call frequency, repeated failures, data access anomalies, prompt injection attempts
- Findings written to `security_alerts` table and pushed via Telegram

### Security Alerts DB Table
```
id                  ULID, PK
severity            enum (LOW, MEDIUM, HIGH, CRITICAL)
alert_type          string (e.g., "data_exfiltration_risk", "prompt_injection")
description         text
source_agent        string
source_execution_id string
raw_evidence        JSON
status              enum (OPEN, ACKNOWLEDGED, RESOLVED, FALSE_POSITIVE)
created_at          timestamp
resolved_at         timestamp
```

---

## Section 6: Admin Panel Rebuild

### Removed
- Chat interface
- n8n external link

### Preserved Views
- Operator Console — system status, kill switch, pending approvals
- Rules Engine — list/create/delete routing rules
- Audit Trail — query decision history
- Cost Dashboard — token usage by model/agent

### New Views

**Agent Manager:**
- List all agents with status, model, active/inactive toggle
- Create: name, description, system prompt, model selector (OpenRouter models), tool picker, max steps, concurrency limit
- Clone existing agent as template
- Edit live (no restart)
- Delete blocked for `is_system=true` seed agents
- Per-agent usage stats

**Security Feed:**
- Real-time CyberSecurity alerts from `security_alerts` table
- Severity badges, filterable
- Acknowledge/resolve/false-positive actions
- Drill into raw evidence

**Telegram Viewer (read-only):**
- Conversation history from `conversation_messages` where source is Telegram
- Shows which agent handled each message
- Execution trace per message
- No send capability

**Agent Deployment Manager:**
- Running agent instances overview (from semaphore pool state)
- Per-type concurrency config (adjust max_concurrent live)
- Queue depth visibility

### Structure
- Split `static/index.html` into multiple files if exceeding ~2000 lines
- Still served by FastAPI, no build step

---

## Section 7: Removals Summary

| Item | Reason |
|------|--------|
| `route_to_automation` orchestrator tool | n8n removed, all work routed to agents |
| `stourio-mcp-engine/` directory | Merged into core |
| MCP service in `docker-compose.yml` | Merged into core |
| n8n service in `docker-compose.yml` | No automation workflows needed |
| `src/adapters/` (all provider adapters) | Replaced by single OpenRouter adapter |
| 3 SRE agents + YAML configs | Replaced by 6 personal AI agents |
| `POST /api/chat` endpoint | Input moves to Telegram |
| Chat interface in admin panel | Removed per requirements |
| Runbooks + RAG ingestion | Not a business ops tool |
| `/app/docs` runbook directory | Not needed |
| `AUTOMATION_WEBHOOK_URL` + n8n integration | No n8n |
| Provider-specific env vars | Single `OPENROUTER_API_KEY` |
| `MCP_SERVER_URL`, `MCP_SHARED_SECRET` | MCP is in-process |

---

## Section 8: Environment & Configuration

### Notification Adapters
- **Kept:** Telegram (primary — all alerts, approvals, responses go here), webhook (generic outbound)
- **Removed:** Slack, PagerDuty, email adapters (SRE-specific, not needed for personal AI)
- **`send_notification` tool:** Routes to Telegram by default. Webhook adapter available for custom integrations.
- Future: re-add email/Slack if needed, but Telegram is the single pane of glass for v1.

### `.env.example`
```
# LLM (single provider)
OPENROUTER_API_KEY=
OPENROUTER_DEFAULT_MODEL=anthropic/claude-sonnet-4-20250514

# Orchestrator
ORCHESTRATOR_MODEL=openai/gpt-4o-mini

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_URL=https://your-domain.com/api/telegram/webhook
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_USE_POLLING=false
TELEGRAM_ALLOWED_USER_IDS=123456789

# Security
STOURIO_API_KEY=
POSTGRES_PASSWORD=changeme
REDIS_PASSWORD=changeme

# Infrastructure
DATABASE_URL=postgresql+asyncpg://stourio:changeme@postgres:5432/stourio
REDIS_URL=redis://:changeme@redis:6379/0

# Execution
MAX_AGENT_DEPTH=4
APPROVAL_TTL_SECONDS=300
KILL_SWITCH_KEY=stourio:kill_switch

# CyberSecurity
SECURITY_AUDIT_INTERVAL_SECONDS=60
SECURITY_INLINE_ENABLED=true

# Embeddings (retained for pgvector)
OPENAI_API_KEY=
EMBEDDING_MODEL=text-embedding-3-small

# Tools
SEARCH_API_KEY=              # SerpAPI or Tavily
WORKSPACE_DIR=/app/workspace  # Sandboxed directory for read_file/write_file

# Agent concurrency
AGENT_CONCURRENCY_DEFAULT=3
```

### Docker Compose (4 services)
- `postgres` (pgvector:pg16) — unchanged
- `redis` (redis:7-alpine) — unchanged
- `jaeger` (jaegertracing/all-in-one) — unchanged (kept for debugging)
- `stourioclaw` (core + MCP) — port 8000

---

## Section 9: Database Migrations

### New Tables
- `agents` — agent definitions (seed + user-created)
- `security_alerts` — CyberSecurity findings

### Modified Tables
- `token_usage` — `provider` always "openrouter", add `openrouter_model` column
- `conversation_messages` — add `source` column (telegram/webhook/api), add `agent_id` column
- `audit_log` — add `agent_id` column

### Unchanged Tables
- `rules`, `approvals`, `document_chunks`

### Approach
- Initialize Alembic migrations
- Seed script on first boot: YAML agent configs -> `agents` table if empty

### Data Migration Strategy
This is a **clean install only** deployment. No upgrade-in-place from the business version.

Rationale: The transformation changes the fundamental purpose, agents, tools, and routing logic. Migrating SRE audit logs, SRE token usage, and SRE conversation history into a personal AI has no value. A fresh database is simpler and correct.

Steps:
1. `alembic init` — set up migration infrastructure
2. Initial migration creates all tables (new schema from scratch)
3. Seed migration populates `agents` table from YAML configs
4. Old `stourio-core-engine/` database volume is not reused

If the user has an existing deployment they want to preserve data from: export `rules` table manually (only table with potentially reusable data).

---

## Section 10: Project Structure

```
stourioclaw/
├── docker-compose.yml
├── .env.example
├── config/
│   └── agents/                  # YAML seed files (6 agents)
│       ├── assistant.yaml
│       ├── analyst.yaml
│       ├── code_writer.yaml
│       ├── code_reviewer.yaml
│       ├── cybersecurity.yaml
│       └── intel.yaml
├── src/
│   ├── main.py
│   ├── config.py
│   ├── adapters/
│   │   └── openrouter.py
│   ├── agents/
│   │   ├── runtime.py
│   │   ├── registry.py
│   │   └── chains.py
│   ├── api/
│   │   └── routes.py
│   ├── mcp/
│   │   ├── router.py
│   │   ├── registry.py
│   │   └── tools/
│   ├── orchestrator/
│   │   └── core.py
│   ├── telegram/
│   │   ├── webhook.py
│   │   ├── client.py
│   │   └── formatter.py
│   ├── security/
│   │   ├── interceptor.py
│   │   └── auditor.py
│   ├── persistence/
│   │   ├── database.py
│   │   └── redis_store.py
│   ├── guardrails/
│   │   └── approvals.py
│   ├── notifications/
│   │   └── adapters/
│   ├── models/
│   │   └── schemas.py
│   ├── rules/
│   │   └── engine.py
│   ├── tracking/
│   │   ├── tracker.py
│   │   └── pricing.py
│   └── telemetry.py
├── migrations/
├── static/
│   ├── index.html
│   └── js/views/
├── tests/
└── scripts/
    └── generate_key.py
```

### Key Changes from Current
- `stourio-core-engine/` flattened to project root
- `stourio-mcp-engine/` absorbed into `src/mcp/`
- New modules: `src/telegram/`, `src/security/`
- `src/adapters/` reduced to single file + `embeddings.py`
- `migrations/` added (Alembic)

### Flattening Strategy
The `stourio-core-engine/` subdirectory currently holds all source code. Flattening to project root:
1. Single git commit: move `stourio-core-engine/src/` -> `src/`, `stourio-core-engine/config/` -> `config/`, etc.
2. Update `Dockerfile` COPY paths and WORKDIR
3. Update `docker-compose.yml` build context from `./stourio-core-engine` to `.`
4. All Python imports remain `from src.` — no import changes needed
5. Delete empty `stourio-core-engine/` directory
6. Delete `stourio-mcp-engine/` directory

---

## Memory & Conversation Context

- Conversation history in PostgreSQL (`conversation_messages` table, last 20 messages)
- Semantic memory recall via `document_chunks` with pgvector embeddings
- Both orchestrator and agents query conversation history
- `conversation_id` maps to Telegram chat ID — continuous conversation thread
- `document_chunks` table repurposed for personal knowledge base (future)
- Memory TTL: 90 days (configurable), enforced by background worker running daily (`memory_cleanup_worker`), deletes rows from `conversation_messages` and `document_chunks` where `created_at < now() - TTL`

---

## Section 11: Testing Strategy

### Unit Tests
- **OpenRouter adapter:** mock HTTP responses, verify request format, model routing, error handling, fallback params
- **Telegram webhook:** mock Bot API, verify secret token validation, user ID restriction, message parsing
- **Security interceptor:** verify high-risk tool calls are intercepted, low-risk pass through
- **Agent registry:** CRUD operations, seed loading, dynamic enum generation
- **Tool implementations:** each tool tested in isolation with mocked external deps

### Integration Tests
- **Message flow:** Telegram webhook -> orchestrator -> agent -> tool -> response (with test DB + Redis)
- **Code review chain:** Code Writer -> Code Reviewer loop with mock LLM responses
- **Approval flow:** CyberSecurity flag -> approval created -> Telegram notification -> approve/reject -> execution resumes
- **Agent auto-deployment:** create agent via API -> verify it appears in routing enum -> send message routed to it

### Smoke Tests (Docker)
- `docker compose up` -> health check all services
- Send test message via webhook API -> verify response
- Verify Telegram webhook registration on startup (mock Telegram API)

### Test Infrastructure
- `pytest` + `pytest-asyncio` (existing)
- `httpx` for async HTTP testing
- Test database: separate PostgreSQL instance or transaction rollback per test
- Fixtures for: DB session, Redis client, mock OpenRouter, mock Telegram Bot API
