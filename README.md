# Stourioclaw

Self-hosted personal AI assistant with 6 specialized agents, Telegram integration, and hybrid security monitoring.

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
   [Agent] → Assistant | Analyst | Code Writer | Code Reviewer | Intel
        |
   [CyberSecurity] → inline intercept (high-risk) + passive audit
        |
   [Approval Flow] → human-in-the-loop if flagged
        |
   [Audit Trail] → immutable log
```

## Quick Start

**Prerequisites:** Docker, Docker Compose, Telegram account.

1. Create a Telegram bot with [@BotFather](https://t.me/BotFather), save the token
2. Get your user ID from [@userinfobot](https://t.me/userinfobot)
3. Copy `.env.example` to `.env` and fill in:
   - `OPENROUTER_API_KEY` (required)
   - `TELEGRAM_BOT_TOKEN` (required)
   - `TELEGRAM_ALLOWED_USER_IDS` (required)
   - `TELEGRAM_WEBHOOK_URL` (required)
   - `TELEGRAM_WEBHOOK_SECRET` (required)
   - `STOURIO_API_KEY` (required)
   - `OPENAI_API_KEY` (optional — embeddings + voice transcription)
   - `SEARCH_API_KEY` (optional — web search)
4. `docker compose up -d`
5. Message your bot on Telegram

## Agents

| Agent | Role | Model | Tools |
|-------|------|-------|-------|
| Assistant | General tasks — weather, email, reminders | claude-sonnet | web_search, call_api, send_notification |
| Analyst | Data analysis, research, structured reasoning | claude-sonnet | call_api, generate_report, read_file, query_data |
| Code Writer | Code generation and implementation | claude-sonnet | read_file, write_file, execute_code, search_knowledge |
| Code Reviewer | Reviews Code Writer output | claude-sonnet | read_file, search_knowledge |
| CyberSecurity | Monitors all agent actions for threats | gpt-4o | read_audit_log, send_notification |
| Intel | Deep thinking, planning, strategy | claude-opus | search_knowledge, generate_report |

## Admin Panel

`http://localhost:8000/admin`

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
| `STOURIO_API_KEY` | API key for all endpoints | Yes |
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

## Project Structure

```
stourioclaw/
  src/
    adapters/          # LLM provider adapters + cache
    agents/            # Agent runtime, execution loop, templates
    api/               # FastAPI routes + rate limiting
    automation/        # Workflow integration
    guardrails/        # Approval flow + kill switch
    mcp/               # MCP server (SSE transport)
      tools/           # MCP tool definitions
    models/            # Pydantic schemas
    notifications/     # Dispatcher + adapters (Slack, PagerDuty, email, webhook)
      adapters/        # Notification channel adapters
    orchestrator/      # Core routing, chains, concurrency pool
    persistence/       # PostgreSQL + Redis + audit
    rag/               # Embeddings, reranker, chunker, ingestion, retriever
      embeddings/      # Embedding providers
      reranker/        # Re-ranking providers
    rules/             # Deterministic rule engine
    security/          # CyberSecurity agent, inline + passive modes
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
