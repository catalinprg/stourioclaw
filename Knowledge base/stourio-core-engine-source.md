# Stourio Core Engine — Complete Source

Last consolidated: 2026-02-25

---


## .env.example

```
# =============================================================================
# STOURIO CONFIGURATION
# Copy this to .env and fill in your values
# =============================================================================

# --- LLM Providers ---
# Set the provider for each role. Options: openai, anthropic, deepseek, google
# You can use different providers for different roles.

# Orchestrator: makes routing decisions for every input (fast, cheap model recommended)
ORCHESTRATOR_PROVIDER=openai
ORCHESTRATOR_MODEL=gpt-4o-mini

# Agent Fallback: Default provider used ONLY if an agent template lacks an override
AGENT_PROVIDER=anthropic
AGENT_MODEL=claude-3-5-sonnet-latest

# --- API Keys ---
# WARNING: You must supply API keys for every provider explicitly referenced 
# in your AgentTemplate overrides (e.g., Google for 'take_action', OpenAI for 'escalate').
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=
GOOGLE_API_KEY=

# --- DeepSeek / OpenAI-compatible endpoints ---
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

# --- Google Gemini ---
GOOGLE_MODEL=gemini-3.1-pro-preview

# --- Security (MANDATORY) ---
# Generate with: python3 scripts/generate_key.py
# All API requests require X-STOURIO-KEY header. System rejects all requests if unset.
STOURIO_API_KEY=

# Infrastructure passwords (change these before first start)
POSTGRES_PASSWORD=changeme
REDIS_PASSWORD=changeme

# CORS: comma-separated list of allowed origins. Empty = no CORS requests allowed.
CORS_ORIGINS=http://localhost:3000,http://localhost:8000

# --- Execution Endpoints ---
AUTOMATION_WEBHOOK_URL=http://n8n:5678/webhook/stourio
MCP_SERVER_URL=
MCP_SHARED_SECRET=

# --- Infrastructure (uses passwords from above via docker-compose substitution) ---
DATABASE_URL=postgresql+asyncpg://stourio:changeme@postgres:5432/stourio
REDIS_URL=redis://:changeme@redis:6379/0

# --- Server ---
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=info

# --- Guardrails ---
MAX_AGENT_DEPTH=4
APPROVAL_TTL_SECONDS=300
KILL_SWITCH_KEY=stourio:kill_switch

```

---


## Dockerfile

```
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

```

---


## docker-compose.yml

```
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: stourio
      POSTGRES_USER: stourio
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme}
    # No external ports: only accessible within Docker network
    expose:
      - "5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U stourio"]
      interval: 5s
      timeout: 5s
      retries: 5

  jaeger:
    image: jaegertracing/all-in-one:1.50
    ports:
      - "127.0.0.1:16686:16686"  # Web UI, localhost only
    expose:
      - "4317"  # OTLP gRPC, internal only
    environment:
      - COLLECTOR_OTLP_ENABLED=true
    volumes:
      - jaeger_data:/data

  redis:
    image: redis:7-alpine
    command: >
      redis-server
      --requirepass ${REDIS_PASSWORD:-changeme}
      --maxmemory 256mb
      --maxmemory-policy allkeys-lru
    # No external ports: only accessible within Docker network
    expose:
      - "6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD:-changeme}", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  n8n:
    image: docker.n8n.io/n8nio/n8n
    ports:
      - "127.0.0.1:5678:5678"  # localhost only
    environment:
      - N8N_HOST=0.0.0.0
      - N8N_PORT=5678
      - N8N_PROTOCOL=http
      - NODE_ENV=production
      - WEBHOOK_URL=http://localhost:5678/
    volumes:
      - n8n_data:/home/node/.n8n
    restart: unless-stopped

  stourio:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://stourio:${POSTGRES_PASSWORD:-changeme}@postgres:5432/stourio
      REDIS_URL: redis://:${REDIS_PASSWORD:-changeme}@redis:6379/0
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - .:/app
    restart: unless-stopped

volumes:
  pgdata:
  n8n_data:
  redis_data:
  jaeger_data:

```

---


## requirements.txt

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
pydantic==2.10.4
pydantic-settings==2.7.1
sqlalchemy[asyncio]==2.0.36
asyncpg==0.30.0
alembic==1.14.1
redis[hiredis]==5.2.1
httpx==0.28.1
openai==1.58.1
anthropic==0.42.0
google-genai==1.5.0
python-dotenv==1.0.1
python-ulid==3.0.0
opentelemetry-api==1.29.0
opentelemetry-sdk==1.29.0
opentelemetry-instrumentation-fastapi==0.50b0
opentelemetry-exporter-otlp==1.29.0
```

---


## README.md

```
# Stourio

**Operational Intelligence Engine**
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

## Quick start

## Prerequisites
- Docker and Docker Compose
- At least one LLM API key (OpenAI, Anthropic, Google, or DeepSeek)

## Setup
Do not attempt to configure this project by double-clicking files in macOS Finder or Windows Explorer. Hidden files (dotfiles) will trigger OS security warnings or remain invisible. Use your terminal and a code editor.

```
# 1. Clone and enter
git clone <your-repo-url>
cd stourio

# 2. Bootstrap the environment
chmod +x scripts/setup.sh
./scripts/setup.sh

# 3. Add your configuration
# Open the newly created .env file in your code editor (e.g., VS Code, nano).
# Set the orchestrator (routing) and fallback agent providers:
# ORCHESTRATOR_PROVIDER=openai
# ORCHESTRATOR_MODEL=gpt-4o-mini
# AGENT_PROVIDER=anthropic           (fallback for agents without overrides)
# AGENT_MODEL=claude-sonnet-4-5-20250929
# Add API keys for EVERY provider referenced in agent templates (see Per-Agent LLM section).

# 4. Start the infrastructure (Stourio, PostgreSQL, Redis, n8n)
docker-compose up --build

# 5. Open API docs
http://localhost:8000/docs
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

Edit `src/agents/runtime.py` and add to `AGENT_TEMPLATES`. Define the role (system prompt), available tools, and optionally a `provider_override` and `model_override` to run the agent on a specific LLM. If omitted, the agent uses the `AGENT_PROVIDER`/`AGENT_MODEL` fallback from `.env`.

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
Stourio routes all agent tool executions to a single `/execute` endpoint on the MCP gateway (`MCP_SERVER_URL`). The gateway dispatches internally by `tool_name`. You must deploy the companion `stourio-mcp-Engine` on a separate server and configure both services to share the same `MCP_SHARED_SECRET`.

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

### Running with Docker Desktop (GUI, no terminal required)
If you prefer using Docker Desktop (the app with the whale icon) instead of the terminal for daily operations, follow these steps:
First-time setup (Terminal required once)

```
cd stourio
./scripts/setup.sh
# Edit .env in your code editor
docker-compose up --build
```

### Day-to-day usage
1. Open Docker Desktop on your Mac or Windows machine.
2. Click Containers in the left sidebar.
3. You will see a container group called stourio with four services:
   - `stourio-postgres-1` (database)
   - `stourio-redis-1` (cache/queue)
   - `stourio-stourio-1` (the Engine)
   - `stourio-n8n-1` (automation engine)
4. Click the **Play** button (▶) on the `stourio` group to start everything
5. Click the **Stop** button (■) to shut it down
6. The **port `8000:8000`** link next to the container name opens the API in your browser
7. The **port `5678:5678`** link opens the n8n automation dashboard

### Accessing the Engine

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

### License
Private. Internal use only.
```

---


## scripts/generate_key.py

```
import secrets
import os

def generate_stourio_key():
    # Generate a secure 32-byte (256-bit) hex key
    new_key = secrets.token_hex(32)
    env_path = ".env"
    
    if not os.path.exists(env_path):
        print(f"Error: {env_path} not found. Run ./scripts/setup.sh first.")
        return

    with open(env_path, "r") as f:
        lines = f.readlines()

    key_exists = False
    with open(env_path, "w") as f:
        for line in lines:
            if line.startswith("STOURIO_API_KEY="):
                f.write(f"STOURIO_API_KEY={new_key}\n")
                key_exists = True
            else:
                f.write(line)
        
        if not key_exists:
            f.write(f"\n# Security\nSTOURIO_API_KEY={new_key}\n")

    print(f"Successfully generated and saved key: {new_key[:4]}...{new_key[-4:]}")

if __name__ == "__main__":
    generate_stourio_key()
```

---


## scripts/setup.sh

```
#!/bin/bash
# =============================================================================
# Stourio - Environment Bootstrap
# Generates secure passwords and API key on first run.
# =============================================================================

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BOLD}Initializing Stourio environment...${NC}\n"

if [ ! -f .env ]; then
    cp .env.example .env

    # Generate secure random passwords
    PG_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    REDIS_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    API_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

    # Replace placeholders in .env
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS sed
        sed -i '' "s|POSTGRES_PASSWORD=changeme|POSTGRES_PASSWORD=${PG_PASS}|g" .env
        sed -i '' "s|REDIS_PASSWORD=changeme|REDIS_PASSWORD=${REDIS_PASS}|g" .env
        sed -i '' "s|STOURIO_API_KEY=|STOURIO_API_KEY=${API_KEY}|" .env
        sed -i '' "s|stourio:changeme@postgres|stourio:${PG_PASS}@postgres|g" .env
        sed -i '' "s|redis://:changeme@redis|redis://:${REDIS_PASS}@redis|g" .env
    else
        # Linux sed
        sed -i "s|POSTGRES_PASSWORD=changeme|POSTGRES_PASSWORD=${PG_PASS}|g" .env
        sed -i "s|REDIS_PASSWORD=changeme|REDIS_PASSWORD=${REDIS_PASS}|g" .env
        sed -i "s|STOURIO_API_KEY=|STOURIO_API_KEY=${API_KEY}|" .env
        sed -i "s|stourio:changeme@postgres|stourio:${PG_PASS}@postgres|g" .env
        sed -i "s|redis://:changeme@redis|redis://:${REDIS_PASS}@redis|g" .env
    fi

    echo -e "${GREEN}✓ Created .env with secure passwords.${NC}"
    echo -e "${GREEN}✓ API Key: ${API_KEY:0:8}...${NC}"
    echo ""
    echo -e "${YELLOW}Save your API key. You will need it for every request:${NC}"
    echo -e "  ${BOLD}X-STOURIO-KEY: ${API_KEY}${NC}"
else
    echo -e "${YELLOW}⚠ .env already exists. Skipping to prevent overwriting keys.${NC}"
fi

echo ""
echo -e "${BOLD}Next Steps:${NC}"
echo "1. Add your LLM API key(s) to the .env file."
echo "   -> macOS: Do NOT double-click .env in Finder. Use a code editor."
echo "2. Run: docker-compose up --build"
echo "3. API docs: http://localhost:8000/docs"
echo ""

```

---


## scripts/test.sh

```
#!/bin/bash
# =============================================================================
# Stourio - Production-Hardened Test Script
# =============================================================================

BASE="http://localhost:8000/api"
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

# Load API Key from .env
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Use the key defined in .env or fall back to a placeholder for testing
API_KEY=${STOURIO_API_KEY:-"change_me_in_env"}

# Helper function to inject the security header into curl
function s_curl() {
    curl -s -H "X-STOURIO-KEY: $API_KEY" "$@"
}

echo ""
echo -e "${BOLD}========================================${NC}"
echo -e "${BOLD}  STOURIO HARDENED PIPELINE TEST${NC}"
echo -e "${BOLD}========================================${NC}"
echo -e "Using API Key: ${YELLOW}${API_KEY:0:4}****${NC}"
echo ""

# --- 1. System status ---
echo -e "${BOLD}[1] System Status (Authenticated)${NC}"
s_curl $BASE/status | python3 -m json.tool
echo ""

# --- 2. Chat - simple question ---
echo -e "${BOLD}[2] Chat: Simple direct response${NC}"
s_curl -X POST $BASE/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What agents do you have available?"}' | python3 -m json.tool
echo ""

# --- 3. Chat - agent routing (Fenced & Logged) ---
echo -e "${BOLD}[3] Chat: Agent routing (Requires Fencing Token)${NC}"
s_curl -X POST $BASE/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Investigate why the EU-West API is slow."}' | python3 -m json.tool
echo ""

# --- 4. Webhook - system signal (Reliable Queue) ---
echo -e "${BOLD}[4] Webhook: Reliable Signal Ingestion${NC}"
s_curl -X POST $BASE/webhook \
  -H "Content-Type: application/json" \
  -d '{"source": "datadog", "event_type": "alert", "title": "CPU > 95%", "severity": "high"}' | python3 -m json.tool
echo ""

# --- 5. Security Test - Unauthorized Access ---
echo -e "${BOLD}[5] Security Test: Unauthorized Access (Should Fail)${NC}"
curl -s -o /dev/null -w "%{http_code}" -X GET $BASE/status | grep -q "403" && echo -e "${GREEN}✓ Correctly rejected unauthorized request (403)${NC}" || echo -e "${RED}✗ Security failure: request was not rejected${NC}"
echo ""

# --- 6. Check Audit Trail ---
echo -e "${BOLD}[6] Audit Trail (Hardenend Logs)${NC}"
s_curl "$BASE/audit?limit=5" | python3 -m json.tool
echo ""

echo -e "${GREEN}${BOLD}Hardenend tests complete.${NC}"
```

---


## src/config.py

```
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # LLM Providers (defaults to OpenAI; override in .env)
    orchestrator_provider: str = "openai"
    orchestrator_model: str = "gpt-4o-mini"
    agent_provider: str = "openai"
    agent_model: str = "gpt-4o-mini"

    # API Keys
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    google_api_key: Optional[str] = None

    # Security
    stourio_api_key: Optional[str] = None
    cors_origins: str = "http://localhost:3000,http://localhost:8000"

    # Infrastructure passwords (used by docker-compose, declared here so pydantic doesn't reject them)
    postgres_password: str = "changeme"
    redis_password: str = "changeme"

    # DeepSeek
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # Google
    google_model: str = "gemini-2.0-flash"

    # Infrastructure (passwords via env vars, never hardcode)
    database_url: str = "postgresql+asyncpg://stourio:changeme@postgres:5432/stourio"
    redis_url: str = "redis://redis:6379/0"

    # Execution Endpoints
    automation_webhook_url: str = "http://n8n:5678/webhook/stourio"
    mcp_server_url: str = ""
    mcp_shared_secret: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # Guardrails
    max_agent_depth: int = 4
    approval_ttl_seconds: int = 300
    kill_switch_key: str = "stourio:kill_switch"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
```

---


## src/main.py

```
import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router
from src.api.rate_limit import RateLimitMiddleware
from src.persistence.database import init_db
from src.rules.engine import seed_default_rules
from src.config import settings
from src.persistence import redis_store
from src.orchestrator.core import process
from src.models.schemas import OrchestratorInput, SignalSource, WebhookSignal
from src.telemetry import setup_tracing

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s | %(name)-24s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("stourio")


async def signal_consumer_worker():
    """Background worker to dequeue and process signals reliably."""
    logger.info("Signal consumer worker started.")
    while True:
        try:
            # Use reliable consumer group dequeue
            entries = await redis_store.dequeue_signals_reliable(consumer_name="worker-primary")
            if not entries:
                await asyncio.sleep(1)
                continue

            for msg_id, raw_sig in entries:
                sig_model = WebhookSignal(**raw_sig)
                content = f"[{sig_model.source.upper()}] {sig_model.event_type}: {sig_model.title}"
                if sig_model.payload:
                    content += f"\nPayload: {sig_model.payload}"
                
                orchestrator_input = OrchestratorInput(
                    source=SignalSource.SYSTEM,
                    content=content,
                    raw_signal=sig_model,
                )
                
                # Process signal through orchestrator
                await process(orchestrator_input)
                
                # Acknowledge ONLY after successful processing to prevent signal loss
                await redis_store.ack_signal(msg_id)
                
        except asyncio.CancelledError:
            logger.info("Signal consumer worker cancelled.")
            break
        except Exception as e:
            logger.error(f"Consumer worker error: {e}")
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    from src.agents.runtime import list_templates as _list_templates

    logger.info("=" * 60)
    logger.info("STOURIO - Operational Intelligence Engine")
    logger.info(f"Orchestrator: {settings.orchestrator_provider} / {settings.orchestrator_model}")
    logger.info(f"Agent fallback: {settings.agent_provider} / {settings.agent_model}")
    for _t in _list_templates():
        _p = _t.provider_override or settings.agent_provider
        _m = _t.model_override or settings.agent_model
        _src = "override" if _t.provider_override else "fallback"
        logger.info(f"  {_t.id}: {_p} / {_m} ({_src})")
    logger.info("=" * 60)

    if not settings.stourio_api_key:
        logger.warning("!" * 60)
        logger.warning("STOURIO_API_KEY is not set. ALL API requests will be rejected.")
        logger.warning("Run: python3 scripts/generate_key.py")
        logger.warning("!" * 60)


    await init_db()
    await seed_default_rules()
    
    # Initialize reliable messaging infrastructure
    await redis_store.init_consumer_group()

    consumer_task = asyncio.create_task(signal_consumer_worker())

    logger.info("Ready.")
    yield
    logger.info("Shutting down.")
    
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Stourio",
    description="Operational Intelligence Engine - LLM-agnostic orchestration for autonomous operations",
    version="0.1.0",
    lifespan=lifespan,
)
setup_tracing(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(",") if settings.cors_origins else [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-STOURIO-KEY"],
)
app.add_middleware(RateLimitMiddleware)

app.include_router(router, prefix="/api")

from fastapi.staticfiles import StaticFiles
import os

# Create static directory if it doesn't exist
os.makedirs("static", exist_ok=True)

# Mount the static directory to serve the SPA at /admin
app.mount("/admin", StaticFiles(directory="static", html=True), name="admin")

@app.get("/")
async def root():
    return {
        "name": "Stourio",
        "version": "0.1.0",
        "docs": "/docs",
        "status": "/api/status",
    }
```

---


## src/telemetry.py

```
from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

def setup_tracing(app):
    resource = Resource(attributes={
        SERVICE_NAME: "stourio-orchestrator"
    })
    
    provider = TracerProvider(resource=resource)
    
    # Export to console for local debugging
    console_processor = BatchSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(console_processor)
    
    # Export to Jaeger OTLP collector (matches docker-compose service name)
    otlp_processor = BatchSpanProcessor(OTLPSpanExporter(endpoint="http://jaeger:4317"))
    provider.add_span_processor(otlp_processor)
    
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)

tracer = trace.get_tracer("stourio")
```

---


## src/models/schemas.py

```
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
```

---


## src/api/routes.py

```
from __future__ import annotations
import json
import logging
from fastapi import APIRouter, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from src.config import settings
from src.models.schemas import (
    ChatRequest, WebhookSignal, OrchestratorInput, SignalSource,
    ApprovalDecision, Rule, new_id,
)
from src.orchestrator.core import process
from src.persistence import conversations, audit
from src.persistence.redis_store import (
    activate_kill_switch, deactivate_kill_switch, is_killed, enqueue_signal
)
from src.guardrails.approvals import (
    resolve_approval, get_pending_approvals,
)
from src.rules.engine import get_rules, add_rule, remove_rule
from src.agents.runtime import list_templates, execute_agent
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
# CHAT - Human input channel
# =============================================================================

@router.post("/chat")
async def chat(req: ChatRequest):
    conv_id = req.conversation_id or new_id()
    await conversations.save_message(conv_id, "user", req.message)

    signal = OrchestratorInput(
        source=SignalSource.USER,
        content=req.message,
        conversation_id=conv_id,
    )

    result = await process(signal)

    response_text = result.get("message", "")
    await conversations.save_message(conv_id, "assistant", response_text)

    return {
        "conversation_id": conv_id,
        **result,
    }


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

        exec_result = await execute_agent(
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
    }


@router.get("/audit")
async def audit_log(limit: int = 50):
    return await audit.get_recent(limit=limit)
```

---


## src/api/rate_limit.py

```
"""
Per-IP rate limiter middleware using Redis.
Prevents abuse of LLM-backed endpoints and webhook flooding.
"""
from __future__ import annotations
import logging
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from src.config import settings

logger = logging.getLogger("stourio.ratelimit")

# Limits per endpoint prefix (requests per minute)
RATE_LIMITS = {
    "/api/chat": 30,        # LLM cost exposure
    "/api/webhook": 120,    # System signals, higher volume
    "/api/kill": 5,         # Kill switch, low volume
    "/api/resume": 5,
    "/api/approvals": 60,
    "/api/rules": 30,
    "/api/audit": 30,
    "/api/status": 60,
}
DEFAULT_LIMIT = 60  # requests per minute


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for docs and root
        path = request.url.path
        if path in ("/", "/docs", "/openapi.json", "/redoc"):
            return await call_next(request)

        # Determine limit for this path
        limit = DEFAULT_LIMIT
        for prefix, lim in RATE_LIMITS.items():
            if path.startswith(prefix):
                limit = lim
                break

        client_ip = request.client.host if request.client else "unknown"
        window_key = f"stourio:ratelimit:{client_ip}:{path}:{int(time.time()) // 60}"

        try:
            from src.persistence.redis_store import get_redis
            r = await get_redis()
            current = await r.incr(window_key)
            if current == 1:
                await r.expire(window_key, 60)

            if current > limit:
                logger.warning(f"Rate limit exceeded: {client_ip} on {path} ({current}/{limit})")
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": f"Rate limit exceeded. Max {limit} requests/minute for this endpoint.",
                        "retry_after_seconds": 60,
                    },
                    headers={"Retry-After": "60"},
                )
        except Exception as e:
            # If Redis is down, allow the request (fail open for availability)
            logger.error(f"Rate limiter error: {e}. Allowing request.")

        return await call_next(request)

```

---


## src/orchestrator/core.py

```
from __future__ import annotations
import json
import logging
from src.models.schemas import (
    OrchestratorInput, OrchestratorResponse, RoutingDecision,
    RiskLevel, RuleAction, SignalSource, ChatMessage, ToolDefinition, new_id,
)
from src.adapters.registry import get_orchestrator_adapter
from src.rules.engine import get_rules, evaluate
from src.guardrails.approvals import create_approval_request, check_kill_switch
from src.agents.runtime import execute_agent, list_templates
from src.automation.workflows import execute_workflow, list_workflows
from src.persistence import audit
from src.telemetry import tracer

logger = logging.getLogger("stourio.orchestrator")


SYSTEM_PROMPT = """You are Stourio, an AI operations orchestrator. Your job is to analyze incoming
signals (user requests or system events) and decide the best course of action.

You have two types of capabilities:
1. AI AGENTS - for dynamic, novel, or complex situations requiring reasoning
2. AUTOMATION - for known patterns with predefined workflows

Available agent types: {agent_types}
Available automation workflows: {workflow_ids}

For each input, you MUST respond by calling exactly one of these tools:
- route_to_agent: when the situation needs reasoning, diagnosis, or adaptive response
- route_to_automation: when a known workflow matches the situation
- respond_directly: when you can answer the user without taking action
- request_more_info: when the input is ambiguous and you need clarification

Consider the risk level of any action. If an action could affect production systems,
flag it as high-risk so the guardrails layer can request human approval.

Be concise. Prioritize resolution over explanation."""


ROUTING_TOOLS = [
    ToolDefinition(
        name="route_to_agent",
        description="Route to an AI agent for reasoning-heavy, dynamic, or novel tasks",
        parameters={
            "type": "object",
            "properties": {
                "agent_type": {
                    "type": "string",
                    "enum": ["diagnose_repair", "escalate", "take_action"],
                    "description": "Which agent template to use",
                },
                "objective": {
                    "type": "string",
                    "description": "Clear objective for the agent",
                },
                "risk_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why this routing decision was made",
                },
            },
            "required": ["agent_type", "objective", "risk_level", "reasoning"],
        },
    ),
    ToolDefinition(
        name="route_to_automation",
        description="Route to a predefined automation workflow for known patterns",
        parameters={
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "Which automation workflow to trigger",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why this workflow matches",
                },
            },
            "required": ["workflow_id", "reasoning"],
        },
    ),
    ToolDefinition(
        name="respond_directly",
        description="Respond to the user directly without taking any action",
        parameters={
            "type": "object",
            "properties": {
                "response": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["response"],
        },
    ),
    ToolDefinition(
        name="request_more_info",
        description="Ask the user for clarification before proceeding",
        parameters={
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["question"],
        },
    ),
]


async def process(signal: OrchestratorInput) -> dict:
    """
    Main orchestration loop:
    1. Check kill switch
    2. Run rules engine (deterministic, pre-LLM)
    3. If no rule matched, ask the LLM to route
    4. Execute the routing decision
    5. Return result
    """
    span = tracer.start_span("orchestrator_process")
    span.set_attribute("signal.id", signal.id)
    span.set_attribute("signal.source", signal.source.value)

    try:
        return await _process_inner(signal, span)
    except Exception as e:
        span.set_attribute("error", True)
        span.set_attribute("error.message", str(e))
        raise
    finally:
        span.end()


async def _process_inner(signal: OrchestratorInput, span) -> dict:

    # --- Step 0: Kill switch ---
    if await check_kill_switch():
        await audit.log(
            "ORCHESTRATOR_BLOCKED",
            "Signal rejected: kill switch active",
            input_id=signal.id,
        )
        return {
            "status": "halted",
            "message": "System is halted. Kill switch is active. Deactivate before sending commands.",
        }

    await audit.log(
        "SIGNAL_RECEIVED",
        f"Source: {signal.source.value} | Content: {signal.content[:200]}",
        input_id=signal.id,
    )

    # --- Step 1: Deterministic rules engine ---
    rules = await get_rules()
    matched_rule = evaluate(signal.content, rules)

    if matched_rule:
        await audit.log(
            "RULE_MATCHED",
            f"Rule '{matched_rule.name}' matched -> {matched_rule.action.value}",
            input_id=signal.id,
        )

        if matched_rule.action == RuleAction.HARD_REJECT:
            await audit.log(
                "RULE_REJECTED",
                f"Hard reject by rule '{matched_rule.name}'",
                input_id=signal.id,
                risk_level=matched_rule.risk_level,
            )
            return {
                "status": "rejected",
                "message": f"Blocked by rule: {matched_rule.name}. This action is not allowed.",
                "rule": matched_rule.name,
            }

        if matched_rule.action == RuleAction.REQUIRE_APPROVAL:
            approval = await create_approval_request(
                action_description=signal.content,
                risk_level=matched_rule.risk_level,
                blast_radius="Defined by rule: " + matched_rule.name,
                reasoning=f"Intercepted by rule '{matched_rule.name}'",
                input_id=signal.id,
            )
            return {
                "status": "awaiting_approval",
                "message": f"High-risk action detected. Approval required.",
                "approval_id": approval.id,
                "risk_level": matched_rule.risk_level.value,
            }

        if matched_rule.action == RuleAction.TRIGGER_AUTOMATION:
            if matched_rule.automation_id:
                result = await execute_workflow(
                    matched_rule.automation_id,
                    trigger_context=signal.content,
                    input_id=signal.id,
                )
                return {
                    "status": result.status.value,
                    "message": result.result,
                    "execution_id": result.id,
                    "type": "automation",
                }

        if matched_rule.action == RuleAction.FORCE_AGENT:
            # Fall through to agent execution below
            pass

    # --- Step 2: LLM routing ---
    adapter = get_orchestrator_adapter()

    agent_types = ", ".join(t.id for t in list_templates())
    workflow_ids = ", ".join(w.id for w in list_workflows())

    system = SYSTEM_PROMPT.format(agent_types=agent_types, workflow_ids=workflow_ids)
    messages = [ChatMessage(role="user", content=signal.content)]

    try:
        response = await adapter.complete(
            system_prompt=system,
            messages=messages,
            tools=ROUTING_TOOLS,
        )
    except Exception as e:
        logger.exception(f"LLM routing failed: {e}")
        await audit.log(
            "LLM_ROUTING_FAILED",
            f"LLM error: {str(e)}. Falling back to direct response.",
            input_id=signal.id,
        )
        return {
            "status": "error",
            "message": f"Routing failed: {str(e)}. Please try again or check your LLM provider configuration.",
        }

    # --- Step 3: Parse and execute routing decision ---

    if not response.has_tool_call:
        # LLM responded with text directly (no tool call)
        await audit.log(
            "ORCHESTRATOR_DIRECT_RESPONSE",
            "LLM responded without routing",
            input_id=signal.id,
        )
        return {
            "status": "completed",
            "message": response.text or "No response generated.",
            "type": "direct",
        }

    tc = response.first_tool_call
    tool_name = tc["name"]
    args = tc["arguments"]

    await audit.log(
        "ORCHESTRATOR_ROUTED",
        f"Routed to: {tool_name} | Args: {json.dumps(args)[:300]}",
        input_id=signal.id,
    )

    # --- respond_directly ---
    if tool_name == "respond_directly":
        return {
            "status": "completed",
            "message": args.get("response", ""),
            "type": "direct",
            "reasoning": args.get("reasoning", ""),
        }

    # --- request_more_info ---
    if tool_name == "request_more_info":
        return {
            "status": "needs_info",
            "message": args.get("question", "Could you provide more details?"),
            "reasoning": args.get("reasoning", ""),
        }

    # --- route_to_automation ---
    if tool_name == "route_to_automation":
        workflow_id = args.get("workflow_id", "")
        result = await execute_workflow(
            workflow_id,
            trigger_context=signal.content,
            input_id=signal.id,
        )
        return {
            "status": result.status.value,
            "message": result.result,
            "execution_id": result.id,
            "type": "automation",
            "reasoning": args.get("reasoning", ""),
        }

    # --- route_to_agent ---
    if tool_name == "route_to_agent":
        agent_type = args.get("agent_type", "take_action")
        risk_level = RiskLevel(args.get("risk_level", "low"))

        # High/critical risk: require approval first
        if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            approval = await create_approval_request(
                action_description=f"Agent '{agent_type}': {args.get('objective', '')}",
                risk_level=risk_level,
                blast_radius=json.dumps({
                    "agent_type": agent_type,
                    "objective": args.get("objective", ""),
                    "original_content": signal.content,
                }),
                reasoning=args.get("reasoning", ""),
                input_id=signal.id,
            )
            return {
                "status": "awaiting_approval",
                "message": f"Agent action requires approval. Risk: {risk_level.value}",
                "approval_id": approval.id,
                "agent_type": agent_type,
                "objective": args.get("objective", ""),
                "risk_level": risk_level.value,
            }

        # Low/medium risk: execute immediately
        execution = await execute_agent(
            agent_type=agent_type,
            objective=args.get("objective", signal.content),
            context=signal.content,
            input_id=signal.id,
        )
        return {
            "status": execution.status.value,
            "message": execution.result or "Agent completed.",
            "execution_id": execution.id,
            "type": "agent",
            "agent_type": agent_type,
            "steps": execution.steps,
            "reasoning": args.get("reasoning", ""),
        }

    # Unknown tool call
    return {
        "status": "error",
        "message": f"Unknown routing decision: {tool_name}",
    }

```

---


## src/agents/runtime.py

```
from __future__ import annotations
import logging
import asyncio
import httpx
from datetime import datetime
from src.config import settings
from src.models.schemas import (
    AgentTemplate, AgentExecution, ExecutionStatus, ChatMessage, ToolDefinition, new_id,
)
from src.adapters.registry import get_agent_adapter
from src.persistence import redis_store, audit

logger = logging.getLogger("stourio.agents")


# --- Built-in agent templates ---

AGENT_TEMPLATES: dict[str, AgentTemplate] = {
    "diagnose_repair": AgentTemplate(
        id="diagnose_repair",
        name="Diagnose & Repair",
        provider_override="anthropic",
        model_override="claude-3-5-sonnet-latest",
        role="""You are an operations agent specialized in diagnosing system issues and applying fixes.

Your process:
1. Analyze the alert/signal context provided
2. Use available tools to gather more data about the system state
3. Identify the root cause
4. Fetch relevant internal runbooks or documentation if the component is unknown or complex
5. Propose a fix
6. If the fix is safe (low blast radius), apply it
7. If the fix is risky, report back with your analysis and recommendation

Always explain your reasoning step by step. Never execute destructive operations without confirmation.""",
        tools=[
            ToolDefinition(
                name="get_system_metrics",
                description="Get current metrics for a system component (CPU, memory, disk, network)",
                parameters={"type": "object", "properties": {"component": {"type": "string"}, "metric": {"type": "string"}}, "required": ["component"]},
            ),
            ToolDefinition(
                name="get_recent_logs",
                description="Retrieve recent log entries for a service",
                parameters={"type": "object", "properties": {"service": {"type": "string"}, "lines": {"type": "integer", "default": 50}, "severity": {"type": "string"}}, "required": ["service"]},
            ),
            ToolDefinition(
                name="execute_remediation",
                description="Execute a safe remediation action (restart, scale, clear cache)",
                parameters={"type": "object", "properties": {"action": {"type": "string"}, "target": {"type": "string"}, "parameters": {"type": "object"}}, "required": ["action", "target"]},
            ),
            ToolDefinition(
                name="read_internal_runbook",
                description="Fetch internal documentation or troubleshooting guides for a specific service.",
                parameters={
                    "type": "object",
                    "properties": {
                        "service_name": {"type": "string", "description": "The exact name of the service or component to look up."}
                    },
                    "required": ["service_name"],
                },
            ),
        ],
        max_steps=8,
    ),
    "escalate": AgentTemplate(
        id="escalate",
        name="Escalate",
        provider_override="openai",
        model_override="gpt-4o",
        role="""You are an escalation agent. Your job is to:
1. Summarize the situation clearly and concisely
2. Assess severity and business impact
3. Identify who should be notified
4. Draft a clear escalation message
5. Send the notification through the appropriate channel

Be direct. Lead with impact, then cause, then recommended action.""",
        tools=[
            ToolDefinition(
                name="send_notification",
                description="Send a notification to a channel (Slack, email, PagerDuty)",
                parameters={
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string", "enum": ["slack", "email", "pagerduty"]},
                        "target": {"type": "string", "description": "Channel name, email, or service ID"},
                        "message": {"type": "string"},
                        "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                    },
                    "required": ["channel", "target", "message"],
                },
            ),
        ],
        max_steps=4,
    ),
    "take_action": AgentTemplate(
        id="take_action",
        name="Take Action",
        provider_override="google",
        model_override="gemini-3.1-pro-preview",
        role="""You are a general-purpose operations agent. You handle tasks that don't fit
into diagnosis or escalation: data lookups, status checks, report generation,
API calls, and coordination tasks.

Use the available tools to complete the user's request. Report results clearly.""",
        tools=[
            ToolDefinition(
                name="call_api",
                description="Make an API call to an internal or external service",
                parameters={
                    "type": "object",
                    "properties": {
                        "method": {"type": "string", "enum": ["GET", "POST", "PUT"]},
                        "url": {"type": "string"},
                        "body": {"type": "object"},
                    },
                    "required": ["method", "url"],
                },
            ),
            ToolDefinition(
                name="generate_report",
                description="Generate a formatted report from data",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "data": {"type": "object"},
                        "format": {"type": "string", "enum": ["text", "csv", "json"]},
                    },
                    "required": ["title", "data"],
                },
            ),
        ],
        max_steps=6,
    ),
}


def get_template(agent_type: str) -> AgentTemplate | None:
    return AGENT_TEMPLATES.get(agent_type)


def list_templates() -> list[AgentTemplate]:
    return list(AGENT_TEMPLATES.values())


import re

# Build whitelist of valid tool names from all agent templates at import time
_VALID_TOOL_NAMES: set[str] | None = None

def _get_valid_tool_names() -> set[str]:
    global _VALID_TOOL_NAMES
    if _VALID_TOOL_NAMES is None:
        _VALID_TOOL_NAMES = set()
        for template in AGENT_TEMPLATES.values():
            for tool in template.tools:
                _VALID_TOOL_NAMES.add(tool.name)
    return _VALID_TOOL_NAMES

# Strict pattern: alphanumeric, underscores, hyphens only
_SAFE_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_\-]+$")


async def default_tool_executor(tool_name: str, arguments: dict) -> str:
    """
    Production tool executor. Routes LLM tool calls to the MCP gateway's
    single /execute endpoint. The gateway dispatches internally by tool_name.
    """
    # SECURITY: reject tool names not in whitelist
    valid_names = _get_valid_tool_names()
    if tool_name not in valid_names:
        logger.warning(f"SECURITY: LLM requested unknown tool '{tool_name}'. Rejected.")
        return f'{{"error": "Unknown tool: {tool_name}. Not in allowed set."}}'

    # SECURITY: reject path traversal characters even if name passed whitelist
    if not _SAFE_TOOL_NAME.match(tool_name):
        logger.warning(f"SECURITY: Tool name contains illegal characters: '{tool_name}'")
        return '{"error": "Invalid tool name format."}'

    if not settings.mcp_server_url:
        logger.error("MCP_SERVER_URL not configured. Cannot execute tool calls.")
        return '{"error": "MCP gateway not configured. Set MCP_SERVER_URL in .env."}'

    try:
        headers = {}
        if settings.mcp_shared_secret:
            headers["Authorization"] = f"Bearer {settings.mcp_shared_secret}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{settings.mcp_server_url}/execute",
                json={"tool_name": tool_name, "arguments": arguments},
                headers=headers,
            )
            response.raise_for_status()
            return response.text
    except httpx.HTTPStatusError as e:
        logger.error(f"Tool execution HTTP error [{tool_name}]: {e.response.status_code} {e.response.text[:200]}")
        return f'{{"error": "Gateway returned {e.response.status_code} for tool {tool_name}."}}'
    except httpx.HTTPError as e:
        logger.error(f"Tool execution network failure [{tool_name}]: {e}")
        return f'{{"error": "Execution network failure: {str(e)}"}}'
    except Exception as e:
        logger.error(f"Tool execution critical failure [{tool_name}]: {e}")
        return f'{{"error": "Internal error: {str(e)}"}}'


async def execute_agent(
    agent_type: str,
    objective: str,
    context: str,
    input_id: str | None = None,
    tool_executor: callable | None = None,
) -> AgentExecution:
    template = get_template(agent_type)
    if not template:
        raise ValueError(f"Unknown agent type: {agent_type}")

    execution = AgentExecution(
        id=new_id(),
        agent_type=agent_type,
        objective=objective,
        context=context,
        status=ExecutionStatus.RUNNING,
    )

    # Acquire lock with a fencing token to prevent state collisions
    resource_id = f"agent_work:{execution.id}"
    fencing_token = await redis_store.acquire_lock_with_token(resource_id, ttl_seconds=30)
    
    if not fencing_token:
        execution.status = ExecutionStatus.FAILED
        execution.result = "Lock acquisition failed: another agent is already handling this resource."
        return execution

    # Heartbeat to extend the lock/token validity
    async def heartbeat():
        while True:
            await asyncio.sleep(10)
            await redis_store.extend_lock(resource_id, ttl_seconds=30)

    heartbeat_task = asyncio.create_task(heartbeat())

    await audit.log(
        "AGENT_STARTED",
        f"Agent '{template.name}' started: {objective}",
        input_id=input_id,
        execution_id=execution.id,
    )

    try:
        # Dynamically fetch the primary adapter for this template
        current_provider = template.provider_override or settings.agent_provider
        current_model = template.model_override or settings.agent_model
        adapter = get_agent_adapter(current_provider, current_model)
        
        messages = [
            ChatMessage(role="user", content=f"Objective: {objective}\n\nContext:\n{context}")
        ]

        for step in range(template.max_steps):
            # 1. Kill switch check
            if await redis_store.is_killed():
                execution.status = ExecutionStatus.HALTED
                execution.result = "Halted by kill switch"
                await audit.log(
                    "AGENT_HALTED",
                    f"Agent '{template.name}' halted by kill switch at step {step + 1}",
                    execution_id=execution.id,
                )
                break

            # 2. Fencing check
            if not await redis_store.validate_fencing_token(resource_id, fencing_token):
                execution.status = ExecutionStatus.FAILED
                execution.result = "Fencing violation: authorized execution window lost."
                await audit.log(
                    "AGENT_FENCED_OUT",
                    "Process terminated: lock overtaken by a newer process",
                    execution_id=execution.id,
                )
                break

            # 3. LLM Reasoning Call with Execution Failover
            try:
                response = await adapter.complete(
                    system_prompt=template.role,
                    messages=messages,
                    tools=template.tools,
                )
            except Exception as llm_error:
                fallback_provider = settings.agent_provider
                fallback_model = settings.agent_model
                
                # If we are already using the fallback, there is nowhere to fail over to
                if current_provider == fallback_provider and current_model == fallback_model:
                    raise Exception(f"Primary provider {current_provider} failed and no secondary fallback exists: {str(llm_error)}")
                
                logger.warning(
                    f"Provider outage ({current_provider}) for agent {agent_type}. "
                    f"Initiating failover to fallback config ({fallback_provider} / {fallback_model}). Error: {str(llm_error)}"
                )
                
                await audit.log(
                    "AGENT_FAILOVER",
                    f"Provider {current_provider} failed. Failing over to {fallback_provider}.",
                    execution_id=execution.id,
                )
                
                # Swap state to fallback configuration
                current_provider = fallback_provider
                current_model = fallback_model
                adapter = get_agent_adapter(current_provider, current_model)
                
                # Retry the exact same prompt against the fallback provider
                response = await adapter.complete(
                    system_prompt=template.role,
                    messages=messages,
                    tools=template.tools,
                )

            if response.has_tool_call:
                tc = response.first_tool_call
                execution.steps.append({
                    "step": step + 1,
                    "type": "tool_call",
                    "tool": tc["name"],
                    "arguments": tc["arguments"],
                })

                await audit.log(
                    "AGENT_TOOL_CALL",
                    f"Step {step + 1}: {tc['name']}({tc['arguments']})",
                    execution_id=execution.id,
                )

                if tool_executor:
                    tool_result = await tool_executor(tc["name"], tc["arguments"])
                else:
                    tool_result = await default_tool_executor(tc["name"], tc["arguments"])

                messages.append(ChatMessage(
                    role="assistant",
                    content=f"Tool call: {tc['name']}\nArguments: {tc['arguments']}",
                ))
                messages.append(ChatMessage(
                    role="user",
                    content=f"Tool result for {tc['name']}:\n{tool_result}",
                ))

            else:
                execution.steps.append({
                    "step": step + 1,
                    "type": "response",
                    "content": response.text,
                })
                execution.result = response.text
                execution.status = ExecutionStatus.COMPLETED
                execution.completed_at = datetime.utcnow()

                await audit.log(
                    "AGENT_COMPLETED",
                    f"Agent '{template.name}' completed in {step + 1} steps",
                    execution_id=execution.id,
                )
                break
        else:
            execution.status = ExecutionStatus.COMPLETED
            execution.result = f"Agent reached maximum steps ({template.max_steps})."
            execution.completed_at = datetime.utcnow()

    except Exception as e:
        execution.status = ExecutionStatus.FAILED
        execution.result = f"Agent error: {str(e)}"
        logger.exception(f"Agent execution failed: {e}")
        await audit.log(
            "AGENT_FAILED",
            f"Agent '{template.name}' failed: {str(e)}",
            execution_id=execution.id,
        )
    finally:
        heartbeat_task.cancel()
        await redis_store.release_lock(resource_id)

    return execution
```

---


## src/automation/workflows.py

```
from __future__ import annotations
import logging
import httpx
from datetime import datetime
from src.config import settings
from src.models.schemas import AutomationWorkflow, AutomationExecution, ExecutionStatus, new_id
from src.persistence import audit

logger = logging.getLogger("stourio.automation")


# --- Built-in automation workflows ---

WORKFLOWS: dict[str, AutomationWorkflow] = {
    "auto_scale_horizontal": AutomationWorkflow(
        id="auto_scale_horizontal",
        name="Horizontal Auto-Scale",
        description="Scale up instances when CPU exceeds threshold",
        steps=[
            {"action": "get_current_instance_count", "target": "{{service}}"},
            {"action": "scale_to", "target": "{{service}}", "count": "+2"},
            {"action": "verify_health", "target": "{{service}}", "timeout": 60},
        ],
    ),
    "restart_service": AutomationWorkflow(
        id="restart_service",
        name="Rolling Restart",
        description="Perform a rolling restart of a service",
        steps=[
            {"action": "drain_instance", "target": "{{service}}", "instance": "oldest"},
            {"action": "restart_instance", "target": "{{service}}", "instance": "oldest"},
            {"action": "verify_health", "target": "{{service}}", "timeout": 30},
            {"action": "resume_traffic", "target": "{{service}}"},
        ],
    ),
    "flush_cdn_cache": AutomationWorkflow(
        id="flush_cdn_cache",
        name="CDN Cache Flush",
        description="Purge CDN cache for a region or globally",
        steps=[
            {"action": "purge_cdn", "scope": "{{region}}", "confirm": False},
            {"action": "verify_origin_response", "timeout": 15},
        ],
    ),
}


def get_workflow(workflow_id: str) -> AutomationWorkflow | None:
    return WORKFLOWS.get(workflow_id)


def list_workflows() -> list[AutomationWorkflow]:
    return list(WORKFLOWS.values())


async def execute_workflow(
    workflow_id: str,
    trigger_context: str,
    input_id: str | None = None,
) -> AutomationExecution:
    """
    Production execution: Sends workflow payload to the configured external automation engine.
    Enforces a 30-second timeout to prevent API locking.
    """
    workflow = get_workflow(workflow_id)
    if not workflow:
        return AutomationExecution(
            workflow_id=workflow_id,
            trigger_context=trigger_context,
            status=ExecutionStatus.FAILED,
            result=f"Unknown workflow: {workflow_id}",
        )

    execution = AutomationExecution(
        id=new_id(),
        workflow_id=workflow_id,
        trigger_context=trigger_context,
        status=ExecutionStatus.RUNNING,
    )

    await audit.log(
        "AUTOMATION_STARTED",
        f"Workflow '{workflow.name}' triggered via API: {trigger_context}",
        input_id=input_id,
        execution_id=execution.id,
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            payload = {
                "execution_id": execution.id,
                "workflow_id": workflow_id,
                "context": trigger_context,
                "steps": workflow.steps
            }
            response = await client.post(
                settings.automation_webhook_url,
                json=payload
            )
            response.raise_for_status()
            
            try:
                result_data = response.json()
            except ValueError:
                result_data = {"message": response.text}

        execution.status = ExecutionStatus.COMPLETED
        execution.result = f"Engine acknowledged. Response: {result_data.get('message', 'Success')}"

        await audit.log(
            "AUTOMATION_COMPLETED",
            execution.result,
            input_id=input_id,
            execution_id=execution.id,
        )

    except httpx.HTTPError as e:
        execution.status = ExecutionStatus.FAILED
        execution.result = f"Engine unreachable or timeout: {str(e)}"
        logger.error(f"Automation engine network failure for {workflow_id}: {e}")
        await audit.log(
            "AUTOMATION_FAILED",
            execution.result,
            execution_id=execution.id,
        )
    except Exception as e:
        execution.status = ExecutionStatus.FAILED
        execution.result = f"Workflow orchestration error: {str(e)}"
        logger.exception(f"Workflow {workflow_id} failed: {e}")
        await audit.log(
            "AUTOMATION_FAILED",
            execution.result,
            execution_id=execution.id,
        )

    return execution
```

---


## src/rules/engine.py

```
from __future__ import annotations
import re
import json
import logging
from sqlalchemy import select
from src.models.schemas import Rule, RuleAction, RiskLevel, new_id
from src.persistence.database import async_session, RuleRecord

logger = logging.getLogger("stourio.rules")

# In-memory cache, refreshed on changes
_rules_cache: list[Rule] | None = None


async def load_rules() -> list[Rule]:
    """Load active rules from the database."""
    global _rules_cache
    async with async_session() as session:
        result = await session.execute(
            select(RuleRecord).where(RuleRecord.active == True)
        )
        rows = result.scalars().all()
        _rules_cache = [
            Rule(
                id=r.id,
                name=r.name,
                pattern=r.pattern,
                pattern_type=r.pattern_type,
                action=RuleAction(r.action),
                risk_level=RiskLevel(r.risk_level) if r.risk_level else RiskLevel.MEDIUM,
                automation_id=r.automation_id,
                active=r.active,
            )
            for r in rows
        ]
    logger.info(f"Loaded {len(_rules_cache)} active rules")
    return _rules_cache


async def get_rules() -> list[Rule]:
    if _rules_cache is None:
        return await load_rules()
    return _rules_cache


async def add_rule(rule: Rule) -> Rule:
    """Add a new rule and refresh cache."""
    async with async_session() as session:
        record = RuleRecord(
            id=rule.id,
            name=rule.name,
            pattern=rule.pattern,
            pattern_type=rule.pattern_type,
            action=rule.action.value,
            risk_level=rule.risk_level.value,
            automation_id=rule.automation_id,
            active=rule.active,
        )
        session.add(record)
        await session.commit()
    await load_rules()
    logger.info(f"Rule added: {rule.name} ({rule.id})")
    return rule


async def remove_rule(rule_id: str) -> bool:
    async with async_session() as session:
        result = await session.execute(
            select(RuleRecord).where(RuleRecord.id == rule_id)
        )
        record = result.scalar_one_or_none()
        if record:
            await session.delete(record)
            await session.commit()
            await load_rules()
            return True
    return False


def _sanitize_and_normalize(text: str) -> str:
    """
    Strips obfuscation (comments, excessive whitespace) to prevent regex bypasses 
    on destructive commands before they reach the LLM.
    """
    # Remove C-style / SQL block comments
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.DOTALL)
    # Remove SQL line comments
    text = re.sub(r'--.*$', ' ', text, flags=re.MULTILINE)
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def evaluate(content: str, rules: list[Rule]) -> Rule | None:
    """
    Evaluate content against rules. First match wins (priority by order).
    Implements structural sanitization to prevent injection bypasses.
    """
    normalized_content = _sanitize_and_normalize(content)

    # Extract JSON payload if the input is a structured WebhookSignal
    is_json = False
    parsed_payload = {}
    if "Payload: " in content:
        try:
            payload_str = content.split("Payload: ")[1]
            parsed_payload = json.loads(payload_str.replace("'", '"'))
            is_json = True
        except Exception as e:
            logger.debug(f"Failed to parse webhook payload for structural evaluation: {e}")

    for rule in rules:
        if not rule.active:
            continue

        matched = False

        # Structural matching for system events (e.g., pattern="severity:critical")
        if rule.pattern_type == "payload_match" and is_json:
            try:
                k, v = rule.pattern.split(":", 1)
                if str(parsed_payload.get(k, "")).lower() == v.lower():
                    matched = True
            except ValueError:
                logger.warning(f"Invalid payload_match pattern format in rule {rule.id}: {rule.pattern}")

        # Sanitized Regex matching
        elif rule.pattern_type == "regex":
            try:
                # Evaluate against both raw and normalized to catch all vectors
                if re.search(rule.pattern, normalized_content, re.IGNORECASE) or \
                   re.search(rule.pattern, content, re.IGNORECASE):
                    matched = True
            except re.error:
                logger.warning(f"Invalid regex in rule {rule.id}: {rule.pattern}")
                
        elif rule.pattern_type == "keyword":
            if rule.pattern.lower() in normalized_content.lower():
                matched = True
                
        elif rule.pattern_type == "event_type":
            # Event types are consistently formatted in the signal header
            if rule.pattern.lower() in content.lower():
                matched = True

        if matched:
            logger.info(f"Rule matched: {rule.name} ({rule.action.value})")
            return rule

    return None


async def seed_default_rules():
    """Seed initial safety rules if none exist."""
    rules = await get_rules()
    if rules:
        return

    defaults = [
        Rule(
            id=new_id(),
            name="prevent_db_drop",
            pattern=r"DROP\s+(DATABASE|TABLE)",
            pattern_type="regex",
            action=RuleAction.REQUIRE_APPROVAL,
            risk_level=RiskLevel.CRITICAL,
        ),
        Rule(
            id=new_id(),
            name="block_ssh_root",
            pattern=r"ssh\s+root@",
            pattern_type="regex",
            action=RuleAction.HARD_REJECT,
            risk_level=RiskLevel.CRITICAL,
        ),
        Rule(
            id=new_id(),
            name="block_rm_rf",
            pattern=r"rm\s+-rf\s+/",
            pattern_type="regex",
            action=RuleAction.HARD_REJECT,
            risk_level=RiskLevel.CRITICAL,
        ),
        Rule(
            id=new_id(),
            name="auto_scale_cpu",
            pattern=r"CPU\s*>\s*9[0-9]%",
            pattern_type="regex",
            action=RuleAction.TRIGGER_AUTOMATION,
            risk_level=RiskLevel.LOW,
            automation_id="auto_scale_horizontal",
        ),
    ]

    for rule in defaults:
        await add_rule(rule)
    logger.info(f"Seeded {len(defaults)} default rules")
```

---


## src/guardrails/approvals.py

```
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from sqlalchemy import select
from src.config import settings
from src.models.schemas import (
    ApprovalRequest, ApprovalDecision, RiskLevel, ExecutionStatus, new_id,
)
from src.persistence.database import async_session, ApprovalRecord
from src.persistence import redis_store, audit

logger = logging.getLogger("stourio.guardrails")


async def check_kill_switch() -> bool:
    """Returns True if system is halted."""
    killed = await redis_store.is_killed()
    if killed:
        logger.warning("Operation blocked: kill switch is active")
    return killed


async def create_approval_request(
    action_description: str,
    risk_level: RiskLevel,
    blast_radius: str = "",
    reasoning: str = "",
    input_id: str = "",
) -> ApprovalRequest:
    """Create a pending approval and store it."""
    approval = ApprovalRequest(
        id=new_id(),
        action_description=action_description,
        risk_level=risk_level,
        blast_radius=blast_radius,
        reasoning=reasoning,
        original_input_id=input_id,
        status="pending",
        expires_at=datetime.utcnow() + timedelta(seconds=settings.approval_ttl_seconds),
    )

    # Store in DB
    async with async_session() as session:
        record = ApprovalRecord(
            id=approval.id,
            action_description=approval.action_description,
            risk_level=approval.risk_level.value,
            blast_radius=approval.blast_radius,
            reasoning=approval.reasoning,
            original_input_id=approval.original_input_id,
            status="pending",
            expires_at=approval.expires_at,
        )
        session.add(record)
        await session.commit()

    # Cache in Redis with TTL
    await redis_store.cache_approval(approval.id, {
        "id": approval.id,
        "action": approval.action_description,
        "risk_level": approval.risk_level.value,
        "input_id": approval.original_input_id,
    })

    await audit.log(
        "GUARDRAIL_APPROVAL_REQUESTED",
        f"Approval required: {action_description}",
        input_id=input_id,
        risk_level=risk_level,
    )

    return approval


async def resolve_approval(
    approval_id: str, decision: ApprovalDecision
) -> ApprovalRequest | None:
    """Resolve a pending approval. Returns None if expired or not found."""

    # Check if still in Redis (not expired)
    cached = await redis_store.get_cached_approval(approval_id)
    if cached is None:
        # TTL expired - auto-reject
        async with async_session() as session:
            result = await session.execute(
                select(ApprovalRecord).where(ApprovalRecord.id == approval_id)
            )
            record = result.scalar_one_or_none()
            if record and record.status == "pending":
                record.status = "expired"
                record.resolved_at = datetime.utcnow()
                record.resolved_note = "TTL expired before resolution"
                await session.commit()

        await audit.log(
            "GUARDRAIL_APPROVAL_EXPIRED",
            f"Approval {approval_id} expired (TTL exceeded)",
        )
        return None

    # Resolve
    status = "approved" if decision.approved else "rejected"
    async with async_session() as session:
        result = await session.execute(
            select(ApprovalRecord).where(ApprovalRecord.id == approval_id)
        )
        record = result.scalar_one_or_none()
        if record:
            record.status = status
            record.resolved_at = datetime.utcnow()
            record.resolved_note = decision.note or ""
            await session.commit()

    await redis_store.delete_cached_approval(approval_id)

    await audit.log(
        f"GUARDRAIL_APPROVAL_{status.upper()}",
        f"Approval {approval_id}: {status}" + (f" - {decision.note}" if decision.note else ""),
    )

    return ApprovalRequest(
        id=approval_id,
        action_description=cached["action"],
        risk_level=RiskLevel(cached["risk_level"]),
        original_input_id=cached.get("input_id", ""),
        status=status,
    )


async def get_pending_approvals() -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(ApprovalRecord)
            .where(ApprovalRecord.status == "pending")
            .order_by(ApprovalRecord.created_at.desc())
        )
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "action_description": r.action_description,
                "risk_level": r.risk_level,
                "blast_radius": r.blast_radius,
                "reasoning": r.reasoning,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            }
            for r in rows
        ]

```

---


## src/persistence/database.py

```
from __future__ import annotations
import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, String, Text, DateTime, Boolean, JSON, func
from src.config import settings

logger = logging.getLogger("stourio.db")

engine = create_async_engine(settings.database_url, echo=False, pool_size=10)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# --- Tables ---

class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(String, primary_key=True)
    action = Column(String, nullable=False, index=True)
    detail = Column(Text, default="")
    input_id = Column(String, index=True)
    execution_id = Column(String, index=True)
    risk_level = Column(String)
    timestamp = Column(DateTime, server_default=func.now(), index=True)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id = Column(String, primary_key=True)
    conversation_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, server_default=func.now())


class RuleRecord(Base):
    __tablename__ = "rules"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    pattern = Column(String, nullable=False)
    pattern_type = Column(String, default="regex")
    action = Column(String, nullable=False)
    risk_level = Column(String, default="medium")
    automation_id = Column(String, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())


class ApprovalRecord(Base):
    __tablename__ = "approvals"

    id = Column(String, primary_key=True)
    action_description = Column(Text, nullable=False)
    risk_level = Column(String)
    blast_radius = Column(String, default="")
    reasoning = Column(Text, default="")
    original_input_id = Column(String)
    status = Column(String, default="pending", index=True)
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_note = Column(Text, default="")


# --- Init ---

async def init_db():
    """Create all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created")


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session

```

---


## src/persistence/redis_store.py

```
from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Any

import redis.asyncio as aioredis  # Rename alias to avoid conflict
import redis                      # Import top-level redis for exceptions
from src.config import settings

logger = logging.getLogger("stourio.redis")

_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(settings.redis_url, decode_responses=True)
        logger.info("Redis connected")
    return _pool


# --- Kill Switch ---

async def activate_kill_switch() -> None:
    r = await get_redis()
    await r.set(settings.kill_switch_key, "1")
    logger.warning("KILL SWITCH ACTIVATED")


async def deactivate_kill_switch() -> None:
    r = await get_redis()
    await r.delete(settings.kill_switch_key)
    logger.info("Kill switch deactivated")


async def is_killed() -> bool:
    r = await get_redis()
    return await r.exists(settings.kill_switch_key) == 1


# --- Reliable Signal Queue (Consumer Groups) ---

SIGNAL_STREAM = "stourio:signals"
SIGNAL_GROUP = "stourio:consumer_group"


async def init_consumer_group():
    """Ensure the consumer group exists for reliable processing."""
    r = await get_redis()
    try:
        await r.xgroup_create(SIGNAL_STREAM, SIGNAL_GROUP, id="0", mkstream=True)
        logger.info(f"Redis consumer group '{SIGNAL_GROUP}' initialized")
    except redis.exceptions.ResponseError as e:  # Use top-level redis package
        if "already exists" not in str(e).lower():
            raise


async def enqueue_signal(signal: dict[str, Any]) -> str:
    """Push a signal into the Redis stream. Returns the stream entry ID."""
    r = await get_redis()
    entry_id = await r.xadd(SIGNAL_STREAM, {"data": json.dumps(signal)})
    return entry_id


async def dequeue_signals_reliable(consumer_name: str, count: int = 10) -> list[tuple[str, dict]]:
    """Read pending signals using consumer groups. Requires ACK to confirm completion."""
    r = await get_redis()
    # Read messages not yet delivered to other consumers in the group (">")
    entries = await r.xreadgroup(SIGNAL_GROUP, consumer_name, {SIGNAL_STREAM: ">"}, count=count)
    
    results = []
    if entries:
        for _, messages in entries:
            for message_id, data in messages:
                results.append((message_id, json.loads(data["data"])))
    return results


async def ack_signal(message_id: str):
    """Acknowledge and remove processed entries from the stream."""
    r = await get_redis()
    await r.xack(SIGNAL_STREAM, SIGNAL_GROUP, message_id)
    await r.xdel(SIGNAL_STREAM, message_id)

# ... existing code ...

# --- Distributed Locking with Fencing Tokens ---

LOCK_PREFIX = "stourio:lock:" # Added for consistency

async def acquire_lock(resource: str, ttl_seconds: int = 60) -> bool:
    """Standard lock acquisition used for heartbeats."""
    r = await get_redis()
    key = f"{LOCK_PREFIX}{resource}"
    acquired = await r.set(key, "locked", nx=True, ex=ttl_seconds)
    return bool(acquired)

async def extend_lock(resource: str, ttl_seconds: int = 30) -> bool:
    """Extend the TTL of an existing lock."""
    r = await get_redis()
    key = f"{LOCK_PREFIX}{resource}"
    return await r.expire(key, ttl_seconds)

async def acquire_lock_with_token(resource: str, ttl_seconds: int = 60) -> int | None:
    # ... (existing logic) ...
    r = await get_redis()
    key = f"{LOCK_PREFIX}{resource}"
    token = int(datetime.utcnow().timestamp() * 1000)
    acquired = await r.set(key, token, nx=True, ex=ttl_seconds)
    return token if acquired else None

async def validate_fencing_token(resource: str, token: int) -> bool:
    r = await get_redis()
    key = f"{LOCK_PREFIX}{resource}"
    current_token = await r.get(key)
    return current_token == str(token)

async def release_lock(resource: str) -> None:
    r = await get_redis()
    key = f"{LOCK_PREFIX}{resource}"
    await r.delete(key)
    logger.info(f"Lock released: {resource}")


# --- Approval Cache ---

APPROVAL_PREFIX = "stourio:approval:"


async def cache_approval(approval_id: str, data: dict, ttl: int | None = None) -> None:
    r = await get_redis()
    ttl = ttl or settings.approval_ttl_seconds
    await r.set(
        f"{APPROVAL_PREFIX}{approval_id}",
        json.dumps(data),
        ex=ttl,
    )


async def get_cached_approval(approval_id: str) -> dict | None:
    r = await get_redis()
    raw = await r.get(f"{APPROVAL_PREFIX}{approval_id}")
    if raw:
        return json.loads(raw)
    return None


async def delete_cached_approval(approval_id: str) -> None:
    r = await get_redis()
    await r.delete(f"{APPROVAL_PREFIX}{approval_id}")
```

---


## src/persistence/audit.py

```
from __future__ import annotations
import logging
from datetime import datetime
from src.models.schemas import AuditEntry, RiskLevel, new_id
from src.persistence.database import async_session, AuditLog

logger = logging.getLogger("stourio.audit")


async def log(
    action: str,
    detail: str,
    input_id: str | None = None,
    execution_id: str | None = None,
    risk_level: RiskLevel | None = None,
) -> AuditEntry:
    """Append an immutable audit entry."""
    entry = AuditEntry(
        id=new_id(),
        action=action,
        detail=detail,
        input_id=input_id,
        execution_id=execution_id,
        risk_level=risk_level,
    )

    async with async_session() as session:
        record = AuditLog(
            id=entry.id,
            action=entry.action,
            detail=entry.detail,
            input_id=entry.input_id,
            execution_id=entry.execution_id,
            risk_level=entry.risk_level.value if entry.risk_level else None,
            timestamp=entry.timestamp,
        )
        session.add(record)
        await session.commit()

    logger.info(f"AUDIT | {action} | {detail}")
    return entry


async def get_recent(limit: int = 50) -> list[dict]:
    """Get recent audit entries."""
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(AuditLog)
            .order_by(AuditLog.timestamp.desc())
            .limit(limit)
        )
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "action": r.action,
                "detail": r.detail,
                "input_id": r.input_id,
                "execution_id": r.execution_id,
                "risk_level": r.risk_level,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in rows
        ]

```

---


## src/persistence/conversations.py

```
from __future__ import annotations
from sqlalchemy import select
from src.models.schemas import ChatMessage, new_id
from src.persistence.database import async_session, ConversationMessage


async def get_history(conversation_id: str, limit: int = 20) -> list[ChatMessage]:
    """Get conversation history."""
    async with async_session() as session:
        result = await session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.timestamp.desc())
            .limit(limit)
        )
        rows = result.scalars().all()
        return [
            ChatMessage(role=r.role, content=r.content) for r in reversed(rows)
        ]


async def save_message(conversation_id: str, role: str, content: str) -> None:
    """Save a message to conversation history."""
    async with async_session() as session:
        msg = ConversationMessage(
            id=new_id(),
            conversation_id=conversation_id,
            role=role,
            content=content,
        )
        session.add(msg)
        await session.commit()

```

---


## src/adapters/base.py

```
from __future__ import annotations
import json
import logging
from abc import ABC, abstractmethod
from typing import Any
from src.models.schemas import ChatMessage, ToolDefinition
import asyncio
import time

logger = logging.getLogger("stourio.adapters")


class LLMResponse:
    """Normalized response from any LLM provider."""

    def __init__(
        self,
        text: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        raw: Any = None,
    ):
        self.text = text
        self.tool_calls = self._validate_tools(tool_calls or [])
        self.raw = raw

    def _validate_tools(self, calls: list[dict]) -> list[dict]:
        """Parse string arguments and drop malformed tool calls."""
        valid_calls = []
        for call in calls:
            try:
                if isinstance(call.get("arguments"), str):
                    call["arguments"] = json.loads(call["arguments"])
                valid_calls.append(call)
            except json.JSONDecodeError:
                logger.error(f"Adapter dropped malformed tool call: {call}")
                continue
        return valid_calls

    @property
    def has_tool_call(self) -> bool:
        return len(self.tool_calls) > 0

    @property
    def first_tool_call(self) -> dict[str, Any] | None:
        return self.tool_calls[0] if self.tool_calls else None


class BaseLLMAdapter(ABC):
    """
    Abstract interface for all LLM providers.
    Every adapter normalizes provider-specific API differences
    into a single request/response format.
    """

    provider_name: str = "base"

    def __init__(self):
        # Per-instance rate limiter state (not shared across adapters)
        self._tokens: float = 2.0
        self._last_refill: float = time.time()
        self._rate: float = 0.25      # Tokens per second
        self._capacity: float = 2.0   # Max burst
        self._lock = asyncio.Lock()

    async def _acquire_rate_limit_token(self):
        """Simple token bucket rate limiter to prevent 429 errors."""
        async with self._lock:
            now = time.time()
            passed = now - self._last_refill
            self._tokens = min(self._capacity, self._tokens + (passed * self._rate))
            self._last_refill = now

            if self._tokens < 1:
                wait_time = (1 - self._tokens) / self._rate
                logger.warning(f"Rate limit hit for {self.provider_name}. Waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                self._tokens = 0
            else:
                self._tokens -= 1

    @abstractmethod
    async def complete(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.1,
    ) -> LLMResponse:
        """Send a completion request and return a normalized response."""
        ...

    def _format_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        """Override per provider to format tool definitions."""
        return [t.model_dump() for t in tools]

```

---


## src/adapters/registry.py

```
from __future__ import annotations
import logging
from src.adapters.base import BaseLLMAdapter
from src.adapters.openai_adapter import OpenAIAdapter
from src.adapters.anthropic_adapter import AnthropicAdapter
from src.adapters.google_adapter import GoogleAdapter
from src.config import settings

logger = logging.getLogger("stourio.adapters")


def create_adapter(provider: str, model: str) -> BaseLLMAdapter:
    """Factory: create an LLM adapter based on provider name."""

    if provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY not set")
        return OpenAIAdapter(
            api_key=settings.openai_api_key,
            model=model,
        )

    elif provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        return AnthropicAdapter(
            api_key=settings.anthropic_api_key,
            model=model,
        )

    elif provider == "deepseek":
        if not settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY not set")
        return OpenAIAdapter(
            api_key=settings.deepseek_api_key,
            model=model or settings.deepseek_model,
            base_url=settings.deepseek_base_url,
        )

    elif provider == "google":
        if not settings.google_api_key:
            raise ValueError("GOOGLE_API_KEY not set")
        return GoogleAdapter(
            api_key=settings.google_api_key,
            model=model or settings.google_model,
        )

    else:
        raise ValueError(f"Unknown provider: {provider}")


# Orchestrator adapter singleton (handles routing, not tied to a specific agent)
_orchestrator_adapter: BaseLLMAdapter | None = None

# Cache for dynamically instantiated agent adapters
_adapter_cache: dict[tuple[str, str], BaseLLMAdapter] = {}


def get_orchestrator_adapter() -> BaseLLMAdapter:
    global _orchestrator_adapter
    if _orchestrator_adapter is None:
        _orchestrator_adapter = create_adapter(
            settings.orchestrator_provider, settings.orchestrator_model
        )
        logger.info(
            f"Orchestrator adapter: {settings.orchestrator_provider} / {settings.orchestrator_model}"
        )
    return _orchestrator_adapter


def get_agent_adapter(provider: str | None = None, model: str | None = None) -> BaseLLMAdapter:
    """
    Returns an adapter from the cache, instantiating it if it doesn't exist.
    Falls back to environment variables if overrides are not provided.
    """
    p = provider or settings.agent_provider
    m = model or settings.agent_model

    key = (p, m)
    if key not in _adapter_cache:
        _adapter_cache[key] = create_adapter(p, m)
        logger.info(f"Initialized dynamic agent adapter: {p} / {m}")

    return _adapter_cache[key]
```

---


## src/adapters/openai_adapter.py

```
from __future__ import annotations
import json
from openai import AsyncOpenAI
from src.adapters.base import BaseLLMAdapter, LLMResponse
from src.models.schemas import ChatMessage, ToolDefinition


class OpenAIAdapter(BaseLLMAdapter):
    """Adapter for OpenAI and any OpenAI-compatible API (DeepSeek, Ollama, etc.)."""

    provider_name = "openai"

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        super().__init__()
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def complete(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.1,
    ) -> LLMResponse:
        await self._acquire_rate_limit_token()
        formatted_messages = [{"role": "system", "content": system_prompt}]
        for m in messages:
            formatted_messages.append({"role": m.role, "content": m.content})

        kwargs: dict = {
            "model": self.model,
            "messages": formatted_messages,
            "temperature": temperature,
        }

        if tools:
            kwargs["tools"] = self._format_tools(tools)
            kwargs["tool_choice"] = "auto"

        response = await self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        # Extract tool calls if present
        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments),
                })

        return LLMResponse(
            text=choice.message.content,
            tool_calls=tool_calls,
            raw=response,
        )

    def _format_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        """OpenAI function calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]

```

---


## src/adapters/anthropic_adapter.py

```
from __future__ import annotations
import json
from anthropic import AsyncAnthropic
from src.adapters.base import BaseLLMAdapter, LLMResponse
from src.models.schemas import ChatMessage, ToolDefinition


class AnthropicAdapter(BaseLLMAdapter):
    """Adapter for Anthropic Claude models."""

    provider_name = "anthropic"

    def __init__(self, api_key: str, model: str):
        super().__init__()
        self.model = model
        self.client = AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.1,
    ) -> LLMResponse:
        await self._acquire_rate_limit_token()
        formatted_messages = []
        for m in messages:
            formatted_messages.append({"role": m.role, "content": m.content})

        kwargs: dict = {
            "model": self.model,
            "system": system_prompt,
            "messages": formatted_messages,
            "max_tokens": 4096,
            "temperature": temperature,
        }

        if tools:
            kwargs["tools"] = self._format_tools(tools)

        response = await self.client.messages.create(**kwargs)

        # Extract text and tool calls from content blocks
        text_parts = []
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                })

        return LLMResponse(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            raw=response,
        )

    def _format_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        """Anthropic tool format."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters or {"type": "object", "properties": {}},
            }
            for t in tools
        ]

```

---


## src/adapters/google_adapter.py

```
from __future__ import annotations
import json
from google import genai
from google.genai import types
from src.adapters.base import BaseLLMAdapter, LLMResponse
from src.models.schemas import ChatMessage, ToolDefinition


class GoogleAdapter(BaseLLMAdapter):
    """Adapter for Google Gemini models."""

    provider_name = "google"

    def __init__(self, api_key: str, model: str):
        super().__init__()
        self.model = model
        self.client = genai.Client(api_key=api_key)

    async def complete(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.1,
    ) -> LLMResponse:
        # Build contents
        await self._acquire_rate_limit_token()
        contents = []
        for m in messages:
            role = "user" if m.role == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=m.content)]))

        # Build config
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
        )

        if tools:
            config.tools = self._format_tools(tools)

        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )

        # Extract text and tool calls
        text_parts = []
        tool_calls = []

        if response.candidates:
            for part in response.candidates[0].content.parts:
                if part.text:
                    text_parts.append(part.text)
                elif part.function_call:
                    fc = part.function_call
                    tool_calls.append({
                        "id": fc.name,
                        "name": fc.name,
                        "arguments": dict(fc.args) if fc.args else {},
                    })

        return LLMResponse(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            raw=response,
        )

    def _format_tools(self, tools: list[ToolDefinition]) -> list[types.Tool]:
        """Google function declaration format."""
        declarations = []
        for t in tools:
            declarations.append(types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters=t.parameters or {"type": "object", "properties": {}},
            ))
        return [types.Tool(function_declarations=declarations)]

```

---
