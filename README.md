# Stourioclaw

Self-hosted AI agentic operating system. Persistent daemon agents, async inter-agent messaging, external tool discovery via MCP, scheduled jobs, browser automation, and security-first orchestration with human-in-the-loop approval.

## Architecture

```
Telegram / Webhook API
        |
   [Kill Switch]
        |
   [Rules Engine] → deterministic match → action
        |  (no match)
   [LLM Orchestrator] → routes via OpenRouter
        |
   [Agent] → user-defined agents (create via API or admin panel)
        |
   [Delegation] → agents can delegate to other agents (depth-limited)
        |
   [Messaging] → async agent-to-agent via Redis stream inboxes
        |
   [CyberSecurity] → inline intercept (high-risk) + passive audit
        |
   [Approval Flow] → human-in-the-loop if flagged
        |
   [Audit Trail] → immutable log

   [Daemons]   → persistent agents with heartbeat loops + inbox wake
   [Scheduler] → cron jobs fire agents on schedule
   [Browser]   → Playwright-based web automation (domain-restricted)
   [MCP Client]→ agents consume external tool servers (Notion, GitHub, etc.)
```

## Quick Start (Local)

**Prerequisites:** Docker, Docker Compose, Telegram account.

1. Create a Telegram bot with [@BotFather](https://t.me/BotFather), save the token
2. Get your user ID from [@userinfobot](https://t.me/userinfobot)
3. Configure environment:
   ```bash
   cp .env.example .env
   ```
4. Fill in `.env`:
   - `OPENROUTER_API_KEY` (required)
   - `TELEGRAM_BOT_TOKEN` (required)
   - `TELEGRAM_ALLOWED_USER_IDS` (required — your user ID from step 2)
   - `TELEGRAM_WEBHOOK_SECRET` (required — any random string)
   - `STOURIO_API_KEY` (auto-generated on first startup if empty — check logs or `.env` for the key)
   - `TELEGRAM_WEBHOOK_URL` — set to your public URL, or use polling for local dev:
     ```
     TELEGRAM_USE_POLLING=true
     ```
   - `OPENAI_API_KEY` (optional — embeddings + voice transcription)
   - `SEARCH_API_KEY` (optional — web search via Tavily)
5. Start:
   ```bash
   docker compose up -d
   ```
6. Message your bot on Telegram
7. Open admin panel at `http://localhost:8000/admin` (login with your `STOURIO_API_KEY`)

## VPS Deployment

```bash
# On your VPS
git clone https://github.com/catalinprg/stourioclaw.git
cd stourioclaw
cp .env.example .env
```

Edit `.env` with production values:
```
OPENROUTER_API_KEY=your-key
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_ALLOWED_USER_IDS=your-telegram-user-id
TELEGRAM_WEBHOOK_URL=https://your-domain.com/api/telegram/webhook
TELEGRAM_WEBHOOK_SECRET=generate-a-random-string
STOURIO_API_KEY=            # leave empty to auto-generate on first startup
POSTGRES_PASSWORD=generate-a-strong-password
REDIS_PASSWORD=generate-a-strong-password
DATABASE_URL=postgresql+asyncpg://stourio:your-postgres-password@postgres:5432/stourio
REDIS_URL=redis://:your-redis-password@redis:6379/0
```

Start:
```bash
docker compose up -d
```

The Telegram webhook registers automatically on startup. Set up a reverse proxy (nginx/caddy) to expose port 8000 over HTTPS.

Admin panel: `https://your-domain.com/admin` (login with your `STOURIO_API_KEY`)

## Agents

Create agents via the API (`POST /api/agents`) or admin panel. Each agent has a name, model, system prompt, assigned tools, and execution mode (`oneshot` or `daemon`).

**Default agent:**

| Agent | Role | Model | Tools |
|-------|------|-------|-------|
| CyberSecurity | Monitors all agent actions for threats | gpt-4o | read_audit_log, send_notification |

**Available tools for agents:**

| Tool | Description | Risk Level |
|------|-------------|------------|
| `web_search` | Search the web via Tavily | Low |
| `read_file` | Read files from workspace | Low |
| `write_file` | Write files to workspace | High (requires approval) |
| `execute_code` | Run Python or bash | High (requires approval) |
| `call_api` | HTTP requests to external APIs | External |
| `send_notification` | Send Telegram alerts | External |
| `query_data` | Parse CSV/JSON data | Low |
| `search_knowledge` | Semantic search over RAG knowledge base | Low |
| `read_audit_log` | Query the audit trail | Low |
| `generate_report` | Create markdown reports | Low |
| `delegate_to_agent` | Delegate work to another agent (depth-limited to 3) | External |
| `browser_action` | Web automation: navigate, click, type, screenshot, extract text | External |
| `send_message` | Send async message to another agent's inbox (peer-allowlisted) | External |
| `read_messages` | Check your inbox for pending messages | Low |
| `heartbeat_ack` | Daemon signals "nothing to report" (suppresses output) | Low |
| MCP tools | Any tool from connected MCP servers (e.g. `notion__search`) | External |

## Daemon Agents

Persistent agents that run continuously with a heartbeat loop. Create a daemon by setting `execution_mode: "daemon"` on an agent:

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "X-STOURIO-KEY: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "watchdog",
    "display_name": "Watchdog",
    "description": "Monitors audit logs for anomalies",
    "model": "openai/gpt-4o-mini",
    "tools": ["read_audit_log", "send_notification", "heartbeat_ack"],
    "execution_mode": "daemon",
    "daemon_config": {
      "tick_seconds": 300,
      "heartbeat_prompt": "Check audit logs for anomalies. If nothing unusual, call heartbeat_ack.",
      "active_hours": {"start": "08:00", "end": "22:00"},
      "max_messages_per_cycle": 10
    }
  }'
```

Control daemons at runtime:
```bash
POST /api/daemons/{name}/start
POST /api/daemons/{name}/stop
POST /api/daemons/{name}/restart
```

Daemons wake on: inbox messages (instant via pub/sub) or tick interval (heartbeat). If a daemon calls `heartbeat_ack`, output is suppressed. Daemons restart fresh on container restart — no checkpointing, but inbox messages are durable (Redis streams).

## Inter-Agent Messaging

Agents can send async messages to each other via inbox streams. Messaging is open by default — all agents can message all others. To restrict a specific agent, set `allowed_peers`:

```bash
# Only allow "monitor" to message "watchdog"
curl -X PUT http://localhost:8000/api/agents/watchdog \
  -H "X-STOURIO-KEY: your-key" \
  -H "Content-Type: application/json" \
  -d '{"allowed_peers": ["monitor"]}'
```

Empty `allowed_peers` (default) = accept from everyone. Messages to running daemons wake them immediately. Messages to non-running agents trigger a oneshot execution.

## MCP Client — External Tool Servers

Connect external MCP servers so agents can use their tools (Notion, GitHub, Slack, etc.):

```bash
# 1. Set the secret in .env
# NOTION_MCP_TOKEN=your-notion-token

# 2. Register the MCP server
curl -X POST http://localhost:8000/api/mcp-servers \
  -H "X-STOURIO-KEY: your-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "notion", "endpoint_url": "http://localhost:3456/sse", "transport": "sse", "auth_env_var": "NOTION_MCP_TOKEN"}'

# 3. Assign to an agent
curl -X PUT http://localhost:8000/api/agents/assistant \
  -H "X-STOURIO-KEY: your-key" \
  -H "Content-Type: application/json" \
  -d '{"mcp_servers": ["notion"]}'
```

The agent now sees all tools from the Notion MCP server (e.g., `notion__search`, `notion__create_page`). Tools are discovered automatically on connect. All MCP tools go through the security interceptor.

```bash
# List connected servers + discovered tools
GET /api/mcp-servers

# Re-discover tools (if server added new ones)
POST /api/mcp-servers/{name}/refresh

# Remove
DELETE /api/mcp-servers/{name}
```

## Cron Jobs

Schedule agents to run automatically. Manage via API or admin panel.

```bash
# Create a cron job (runs analyst every day at 9am)
curl -X POST http://localhost:8000/api/cron \
  -H "X-STOURIO-KEY: your-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "daily-report", "schedule": "0 9 * * *", "agent_type": "analyst", "objective": "Generate daily summary"}'

# List cron jobs
curl http://localhost:8000/api/cron -H "X-STOURIO-KEY: your-key"

# Delete
curl -X DELETE http://localhost:8000/api/cron/daily-report -H "X-STOURIO-KEY: your-key"
```

## Browser Automation

Agents with the `browser_action` tool can interact with web pages. Set `BROWSER_ALLOWED_DOMAINS` to restrict which sites agents can visit (empty = allow all).

```
BROWSER_ALLOWED_DOMAINS=["example.com","docs.python.org","github.com"]
```

Actions: `navigate`, `click`, `type`, `screenshot`, `extract_text`, `get_url`, `close_session`. Pages persist across calls via `session_id` for multi-step workflows.

## Admin Panel

`http://localhost:8000/admin` — login with your `STOURIO_API_KEY`.

Views: Console, Agents, Security, Telegram, Rules, Audit, Costs, Deployment.

## Claude Code MCP Integration

Add to your Claude Code MCP config:

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

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `OPENROUTER_API_KEY` | OpenRouter API key for LLM routing | Yes |
| `OPENROUTER_DEFAULT_MODEL` | Default model for agents | No (`anthropic/claude-sonnet-4-20250514`) |
| `ORCHESTRATOR_MODEL` | Model for routing decisions | No (`openai/gpt-4o-mini`) |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from BotFather | Yes |
| `TELEGRAM_WEBHOOK_URL` | Public URL for Telegram webhook | Yes |
| `TELEGRAM_WEBHOOK_SECRET` | Secret for webhook verification | Yes |
| `TELEGRAM_USE_POLLING` | Use polling instead of webhook | No (`false`) |
| `TELEGRAM_ALLOWED_USER_IDS` | Comma-separated authorized user IDs | Yes |
| `STOURIO_API_KEY` | API key for all endpoints (auto-generated if empty) | No |
| `POSTGRES_PASSWORD` | PostgreSQL password | Yes |
| `REDIS_PASSWORD` | Redis password | Yes |
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `REDIS_URL` | Redis connection string | Yes |
| `MAX_AGENT_DEPTH` | Max recursive agent depth | No (`4`) |
| `APPROVAL_TTL_SECONDS` | Auto-reject pending approvals after | No (`300`) |
| `KILL_SWITCH_KEY` | Redis key for kill switch | No (`stourio:kill_switch`) |
| `SECURITY_AUDIT_INTERVAL_SECONDS` | Passive security scan interval | No (`60`) |
| `SECURITY_INLINE_ENABLED` | Enable inline security intercepts | No (`true`) |
| `OPENAI_API_KEY` | OpenAI key (embeddings, voice) | No |
| `EMBEDDING_MODEL` | Embedding model name | No (`text-embedding-3-small`) |
| `SEARCH_API_KEY` | Web search API key | No |
| `WORKSPACE_DIR` | Agent workspace directory | No (`/app/workspace`) |
| `AGENT_CONCURRENCY_DEFAULT` | Max concurrent agents per type | No (`3`) |
| `BROWSER_ALLOWED_DOMAINS` | Restrict browser to these domains | No (`[]` = all) |
| `BROWSER_HEADLESS` | Run Chromium headless | No (`true`) |
| `BROWSER_TIMEOUT_MS` | Browser action timeout | No (`30000`) |
| `SCHEDULER_TICK_SECONDS` | Cron job check interval | No (`30`) |
| `DAEMON_MANAGER_ENABLED` | Enable daemon agent support | No (`true`) |
| `DAEMON_DEFAULT_TICK_SECONDS` | Default heartbeat interval for daemons | No (`300`) |
| `MCP_CLIENT_TIMEOUT` | Timeout for MCP server connections | No (`30`) |
| `MCP_STDIO_ALLOWED_COMMANDS` | Allowlist for stdio MCP server commands | No (`[]`) |

## Project Structure

```
stourioclaw/
  src/
    adapters/          # LLM provider adapters + cache
    agents/            # Agent runtime, execution loop, templates
    api/               # FastAPI routes + rate limiting
    automation/        # Workflow integration
    guardrails/        # Approval flow + kill switch
    daemons/           # Daemon manager, loop, inbox (Redis streams)
    mcp/               # MCP server + client (SSE transport)
      tools/           # MCP tool definitions (local + messaging)
    models/            # Pydantic schemas
    notifications/     # Dispatcher + adapters (Slack, PagerDuty, email, webhook)
      adapters/        # Notification channel adapters
    orchestrator/      # Core routing, chains, concurrency pool
    persistence/       # PostgreSQL + Redis + audit
    rag/               # Embeddings, reranker, chunker, ingestion, retriever
      embeddings/      # Embedding providers
      reranker/        # Re-ranking providers
    rules/             # Deterministic rule engine
    scheduler/         # Cron job models, store, background worker
    security/          # CyberSecurity agent, inline + passive modes
    browser/           # Playwright browser pool + action dispatcher
    telegram/          # Telegram bot integration
    tools/             # Tool registry + plugins
      python/          # Python tool plugins
      yaml/            # YAML HTTP tool definitions
    tracking/          # Token usage + cost tracking
  config/
    agents/            # Agent template YAML definitions
    notifications.yaml # Notification channel config
  migrations/          # Alembic database migrations
  tests/               # pytest test suite
  static/              # Admin panel frontend
  scripts/             # Setup and utility scripts
```

## Docker Services

| Service | Image | Purpose |
|---------|-------|---------|
| `postgres` | pgvector/pgvector:pg16 | Primary datastore + vector search |
| `redis` | redis:7-alpine | Caching, kill switch, pub/sub |
| `jaeger` | jaegertracing/all-in-one | Distributed tracing (port 16686) |
| `stourioclaw` | FastAPI (Dockerfile) | Application server (port 8000) |

## License

Apache License 2.0 — see [LICENSE](LICENSE).
