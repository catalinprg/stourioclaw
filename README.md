# Stourio Engine

LLM-agnostic autonomous operations engine. Routes operational signals through deterministic rules, then LLM-based decision-making, with safety guardrails and full audit trails.

## Architecture

```
Signal (user/system)
    |
[Kill Switch] --> halt
    |
[Rules Engine] --> deterministic match? --> action (reject/approve/automate/force agent)
    |  (no match)
[LLM Orchestrator] --> routes via tool calling
    |
[Agent / Chain / Automation / Direct Response]
    |
[Guardrails] --> approval flow if high-risk
    |
[Audit Trail] --> immutable log
```

## Key Features

- **LLM-Agnostic**: OpenAI, Anthropic, Google, DeepSeek — per-agent provider routing with failover
- **Rules Before LLM**: Deterministic pattern matching intercepts before any LLM call
- **Plugin Tool System**: Python plugins for complex logic, YAML definitions for HTTP-based tools
- **RAG Pipeline**: pgvector semantic search over runbooks with pluggable embedders and re-rankers
- **Agent Memory**: Conversation history + semantic recall of past agent actions across sessions
- **Multi-Agent Chaining**: Sequential pipelines and parallel DAG execution with Jinja2 conditions
- **Agent Concurrency**: Per-type semaphore pooling — multiple incidents handled simultaneously
- **Notification Framework**: Webhook, Slack, PagerDuty, email — platform-agnostic with native adapters
- **LLM Response Caching**: Redis-backed deterministic cache at the adapter layer
- **Cost Tracking**: Per-model token pricing, usage aggregation, budget alerts
- **Kill Switch**: One API call halts all operations instantly
- **Human-in-the-Loop**: High-risk actions require approval with TTL-based auto-rejection
- **Full Audit Trail**: Immutable, queryable history of every decision
- **OpenTelemetry**: Distributed tracing with Jaeger integration

## Quick Start

```bash
# Clone
git clone https://github.com/your-org/stourio-engine.git
cd stourio-engine/stourio-core-engine

# Setup
cp .env.example .env
./scripts/setup.sh        # generates API key, sets passwords
./scripts/generate_key.py # if setup.sh doesn't run

# Start
docker-compose up --build
```

Services:
- **Stourio API**: http://localhost:8000
- **Admin Panel**: http://localhost:8000/admin
- **Swagger Docs**: http://localhost:8000/docs
- **Jaeger Tracing**: http://localhost:16686
- **n8n Workflows**: http://localhost:5678

## Configuration

All config via environment variables (see `.env.example`):

| Variable | Description | Default |
|----------|-------------|---------|
| `ORCHESTRATOR_PROVIDER` | LLM for routing decisions | `openai` |
| `ORCHESTRATOR_MODEL` | Model for routing | `gpt-4o-mini` |
| `OPENAI_API_KEY` | OpenAI API key | — |
| `ANTHROPIC_API_KEY` | Anthropic API key | — |
| `GOOGLE_API_KEY` | Google API key | — |
| `COHERE_API_KEY` | Cohere re-ranker key | — |
| `EMBEDDING_PROVIDER` | Embedding provider | `openai` |
| `EMBEDDING_MODEL` | Embedding model | `text-embedding-3-small` |
| `CACHE_ENABLED` | Enable LLM response cache | `true` |
| `AGENT_CONCURRENCY_DEFAULT` | Max concurrent agents per type | `3` |

## API

All endpoints require `X-STOURIO-KEY` header.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat` | POST | Send message through orchestrator |
| `/api/webhook` | POST | Ingest system signal (async, 202) |
| `/api/approvals` | GET | List pending approvals |
| `/api/approvals/{id}` | POST | Approve/reject action |
| `/api/kill` | POST | Activate kill switch |
| `/api/resume` | POST | Deactivate kill switch |
| `/api/rules` | GET/POST | List/create routing rules |
| `/api/rules/{id}` | DELETE | Delete rule |
| `/api/status` | GET | System status + agent pool utilization |
| `/api/audit` | GET | Query audit trail |
| `/api/usage` | GET | Token usage by date range |
| `/api/usage/summary` | GET | Aggregated usage by provider/agent |
| `/api/documents/ingest` | POST | Trigger runbook re-ingestion |

## Adding Tools

### YAML (HTTP-based tools)

Drop a `.yaml` file in the configured tools directory:

```yaml
name: get_metrics
description: Query Prometheus for metrics
parameters:
  metric: {type: string, required: true}
endpoint:
  url: "${PROMETHEUS_URL}/api/v1/query"
  method: POST
  headers:
    Authorization: "Bearer ${PROMETHEUS_TOKEN}"
  body_template: '{"query": "{{metric}}"}'
response:
  extract: "data.result[0].value[1]"
execution:
  mode: local  # or gateway
```

### Python (complex logic)

Drop a `.py` file in the tools directory:

```python
from src.plugins.base import BaseTool

class MyTool(BaseTool):
    name = "my_tool"
    description = "Does something useful"
    parameters = {"type": "object", "properties": {"input": {"type": "string"}}, "required": ["input"]}

    async def execute(self, arguments: dict) -> dict:
        return {"result": f"processed {arguments['input']}"}
```

## Adding Agent Templates

Drop a `.yaml` file in `config/agents/`:

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

## Adding Notification Channels

Edit `config/notifications.yaml`:

```yaml
notification_channels:
  oncall-slack:
    type: slack
    webhook_url: "${SLACK_WEBHOOK_URL}"
  oncall-pagerduty:
    type: pagerduty
    api_key: "${PAGERDUTY_API_KEY}"
    service_id: "P123ABC"
  custom-webhook:
    type: webhook
    url: "https://your-endpoint.com/notify"
```

## Multi-Agent Chains

Define in `config/chains.yaml`:

```yaml
chains:
  incident_response:
    type: pipeline
    steps:
      - agent: diagnose_repair
      - agent: escalate
        condition: "{{ previous.resolution_status == 'escalated' }}"
        input_mapping:
          diagnosis: "{{ previous.conclusion }}"

  parallel_investigation:
    type: dag
    nodes:
      check_db:
        agent: diagnose_database
      check_cache:
        agent: diagnose_cache
      synthesize:
        agent: take_action
        input_mapping:
          db_findings: "{{ steps['check_db'].conclusion }}"
          cache_findings: "{{ steps['check_cache'].conclusion }}"
    edges:
      - [check_db, synthesize]
      - [check_cache, synthesize]
```

## Project Structure

```
stourio-core-engine/
  src/
    adapters/          # LLM provider adapters (OpenAI, Anthropic, Google) + cache
    agents/            # Agent runtime, execution loop, template loading
    api/               # FastAPI routes + rate limiting
    automation/        # n8n workflow integration
    guardrails/        # Approval flow + kill switch
    models/            # Pydantic schemas
    notifications/     # Dispatcher + adapters (Slack, PagerDuty, email, webhook)
    orchestrator/      # Core routing, chains, concurrency pool
    persistence/       # PostgreSQL + Redis + audit
    plugins/           # Tool registry, loaders, base interfaces
    rag/               # Embeddings, reranker, chunker, ingestion, retriever
    rules/             # Deterministic rule engine
    tracking/          # Token usage + cost tracking
    tools/             # YAML + Python tool plugins
  config/
    agents/            # Agent template YAML definitions
    chains.yaml        # Multi-agent chain definitions
    notifications.yaml # Notification channel config
  tests/               # pytest test suite

stourio-mcp-engine/    # Tool execution gateway (separate service)
```

## License

Apache License 2.0 — see [LICENSE](LICENSE).
