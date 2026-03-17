# Stourio MCP Gateway — Complete Source

Last consolidated: 2026-02-25

---


## .env.example

```
# Stourio MCP Gateway Configuration
# Copy to .env: cp .env.example .env
# Generate secret: python3 setup_gateway.py

# Auth (MANDATORY) - must match the value in Stourio Core .env
MCP_SHARED_SECRET=

# Rate limiting (requests per minute per IP)
MCP_RATE_LIMIT=60

# Runbook directory (override only if not using default volume mount)
# MCP_DOCS_DIR=/app/docs

```

---

## Dockerfile

```
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gateway.py .
COPY ./runbooks /app/docs

EXPOSE 8080

CMD ["uvicorn", "gateway:app", "--host", "0.0.0.0", "--port", "8080"]

```

---

## README.md

```
# Stourio MCP Gateway

**The Zero-Trust Tool Execution Layer**

The MCP Gateway is a standalone service that executes privileged actions on behalf of the Stourio orchestrator. It runs on a separate server from the core engine, ensuring the orchestrator never holds infrastructure credentials or direct access to internal systems.

## Architecture

```
[Stourio Core]                    [MCP Gateway]
  Agent Runtime                     /execute endpoint
       |                                 |
  POST /execute  ──── Bearer Token ────> Tool Registry
  { tool_name,                           |
    arguments }                    Handler dispatch
                                         |
                                  [Your Infrastructure]
                                  Monitoring, Logs, APIs,
                                  Runbooks, Notifications
```

All tool calls go through a single `/execute` endpoint. The gateway dispatches to the correct handler via an internal registry. Adding a new tool = adding one function with the `@register_tool` decorator.

## Registered Tools

| Tool | Status | Purpose |
|---|---|---|
| `read_internal_runbook` | Live | Fetch internal docs from mounted `/app/docs` directory |
| `get_system_metrics` | Stub | Connect to Prometheus, CloudWatch, or Datadog |
| `get_recent_logs` | Stub | Connect to Loki, CloudWatch Logs, or ELK |
| `execute_remediation` | Stub | Connect to AWS SSM, Ansible, or Rundeck |
| `send_notification` | Stub | Connect to Slack webhook, SendGrid, or PagerDuty |
| `call_api` | Stub | HTTP dispatch with URL allowlist |
| `generate_report` | Stub | Report formatting and export |

Stub tools return structured JSON explaining they are not yet connected. The agent LLM receives this and reports it cleanly to the user.

## Security

- **Bearer Token Auth**: Every request requires `Authorization: Bearer <MCP_SHARED_SECRET>`. Enforced at the FastAPI dependency level (all endpoints protected by default).
- **Rate Limiting**: In-memory sliding window, 60 requests/minute per IP. Configurable via `MCP_RATE_LIMIT` env var.
- **Path Traversal Protection**: `read_internal_runbook` sanitizes input with `os.path.basename()` before file access.
- **Tool Name Validation**: The `/execute` endpoint rejects tool names that don't match `^[a-zA-Z0-9_\-]+$`.

## Setup

### 1. Generate the shared secret

```
python3 setup_gateway.py
```

This creates or updates your `.env` with a cryptographically secure `MCP_SHARED_SECRET`. Copy this same secret to your Stourio Core `.env`.

### 2. Add your runbooks

Create Markdown files in the `runbooks/` directory:

```
runbooks/
  redis-cache.md
  api-errors.md
  deployment-process.md
```

These are copied into the Docker image at build time. To update runbooks, rebuild the image.

### 3. Build and run

```
docker build -t mcp-gateway .

docker run -d \
  -p 8080:8080 \
  --name mcp-gateway \
  --env-file .env \
  mcp-gateway
```

### 4. Verify

Health check (no auth required):
```
curl http://localhost:8080/health
```

Test a tool call (auth required):
```
curl -X POST http://localhost:8080/execute \
  -H "Authorization: Bearer YOUR_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"tool_name": "read_internal_runbook", "arguments": {"service_name": "redis-cache"}}'
```

Swagger UI: `http://localhost:8080/docs`

### 5. Link to Stourio Core

In the Stourio Core `.env`:
```
MCP_SERVER_URL=http://<GATEWAY_IP>:8080
MCP_SHARED_SECRET=<SAME_SECRET_AS_ABOVE>
```

## Firewall Rules

The gateway should only accept traffic from the Stourio Core server. On the gateway host:

```
# Allow only the orchestrator IP on port 8080
ufw allow from <ORCHESTRATOR_IP> to any port 8080
ufw deny 8080
```

## Adding a New Tool

1. Write an async handler function
2. Decorate it with `@register_tool("your_tool_name")`
3. Restart the gateway

```python
@register_tool("check_disk_usage")
async def tool_check_disk_usage(arguments: dict) -> dict:
    host = arguments.get("host", "localhost")
    # ... your implementation ...
    return {"usage_percent": 73.2, "mount": "/"}
```

The tool is immediately available to any Stourio agent whose template includes a tool definition with the matching name.

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `MCP_SHARED_SECRET` | Yes | - | Bearer token for auth |
| `MCP_RATE_LIMIT` | No | 60 | Max requests/minute per IP |
| `MCP_DOCS_DIR` | No | /app/docs | Runbook directory path |

## License

Private. Internal use only.

```

---

## setup_gateway.py

```
import secrets
import os

def setup():
    env_path = ".env"
    # Generate a cryptographically secure 32-character string
    new_secret = secrets.token_urlsafe(32)
    
    if not os.path.exists(env_path):
        # Create a new .env if it doesn't exist
        with open(env_path, "w") as f:
            f.write(f"MCP_SHARED_SECRET={new_secret}\n")
            f.write("MCP_RATE_LIMIT=60\n")
        print(f"Created new .env file.")
    else:
        # Update existing .env
        with open(env_path, "r") as f:
            lines = f.readlines()
        
        with open(env_path, "w") as f:
            found = False
            for line in lines:
                if line.startswith("MCP_SHARED_SECRET="):
                    f.write(f"MCP_SHARED_SECRET={new_secret}\n")
                    found = True
                else:
                    f.write(line)
            if not found:
                f.write(f"MCP_SHARED_SECRET={new_secret}\n")
        print(f"Updated existing .env file.")

    print(f"\nIMPORTANT: Copy this secret to your Stourio Orchestrator .env later:")
    print(f"MCP_SHARED_SECRET={new_secret}")

if __name__ == "__main__":
    setup()
```

---

## gateway.py

```
"""
Stourio MCP Gateway — Secure Tool Execution Layer

Single /execute dispatch endpoint. All tool calls from the orchestrator
route through here. Tools are registered in TOOL_REGISTRY.

Security:
  - Bearer token auth on every request (MCP_SHARED_SECRET)
  - In-memory sliding-window rate limiter (no Redis dependency)
  - Path traversal protection on file-reading tools
  - Unknown tools rejected with structured error
"""

from __future__ import annotations

import os
import time
import logging
from collections import defaultdict
from typing import Callable, Awaitable

from fastapi import FastAPI, HTTPException, Security, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp.gateway")

# ---------------------------------------------------------------------------
# 1. Configuration
# ---------------------------------------------------------------------------

SHARED_SECRET = os.getenv("MCP_SHARED_SECRET")
if not SHARED_SECRET:
    raise RuntimeError("CRITICAL: MCP_SHARED_SECRET is not set.")

# Rate limit: max requests per minute from any single IP
RATE_LIMIT_PER_MINUTE = int(os.getenv("MCP_RATE_LIMIT", "60"))

# Directory where runbooks / internal docs are mounted
DOCS_DIR = os.getenv("MCP_DOCS_DIR", "/app/docs")


# ---------------------------------------------------------------------------
# 2. Rate Limiter (in-memory, no Redis dependency)
# ---------------------------------------------------------------------------

class _SlidingWindow:
    """Per-IP sliding window counter. Stores timestamps of recent requests."""

    def __init__(self, window_seconds: int = 60):
        self.window = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_ip: str, limit: int) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - self.window
        bucket = self._buckets[client_ip]

        # Evict expired entries
        self._buckets[client_ip] = [t for t in bucket if t > cutoff]
        bucket = self._buckets[client_ip]

        if len(bucket) >= limit:
            return False, len(bucket)

        bucket.append(now)
        return True, len(bucket)


_rate_limiter = _SlidingWindow()


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in ("/", "/docs", "/openapi.json", "/redoc", "/health"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        allowed, count = _rate_limiter.is_allowed(client_ip, RATE_LIMIT_PER_MINUTE)

        if not allowed:
            logger.warning(f"Rate limit exceeded: {client_ip} ({count}/{RATE_LIMIT_PER_MINUTE}/min)")
            return JSONResponse(
                status_code=429,
                content={
                    "error": f"Rate limit exceeded. Max {RATE_LIMIT_PER_MINUTE} requests/minute.",
                    "retry_after_seconds": 60,
                },
                headers={"Retry-After": "60"},
            )

        return await call_next(request)


# ---------------------------------------------------------------------------
# 3. Auth
# ---------------------------------------------------------------------------

security = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    if credentials.credentials != SHARED_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid shared secret.")
    return credentials.credentials


# ---------------------------------------------------------------------------
# 4. App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Stourio MCP Gateway",
    description="Secure tool execution layer for the Stourio orchestrator.",
)
app.add_middleware(RateLimitMiddleware)


# ---------------------------------------------------------------------------
# 5. Tool Registry
# ---------------------------------------------------------------------------

ToolHandler = Callable[[dict], Awaitable[dict]]

TOOL_REGISTRY: dict[str, ToolHandler] = {}


def register_tool(name: str):
    """Decorator to register a tool handler."""
    def decorator(fn: ToolHandler):
        TOOL_REGISTRY[name] = fn
        return fn
    return decorator


# ---------------------------------------------------------------------------
# 6. Tool Implementations
# ---------------------------------------------------------------------------

@register_tool("read_internal_runbook")
async def tool_read_internal_runbook(arguments: dict) -> dict:
    """Fetch internal documentation from the mounted docs directory."""
    service_name = arguments.get("service_name")
    if not service_name:
        return {"error": "Missing required argument: service_name"}

    safe_name = os.path.basename(service_name)
    file_path = os.path.join(DOCS_DIR, f"{safe_name}.md")

    if not os.path.isfile(file_path):
        return {"error": f"Runbook not found: {safe_name}"}

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        return {"error": f"Runbook '{safe_name}' exists but is empty."}

    return {"result": content}


@register_tool("get_system_metrics")
async def tool_get_system_metrics(arguments: dict) -> dict:
    """
    Retrieve system metrics for a component.
    STUB: Replace with real monitoring integration (Prometheus, CloudWatch, Datadog).
    """
    component = arguments.get("component", "unknown")
    metric = arguments.get("metric")
    return {
        "status": "stub",
        "component": component,
        "metric": metric,
        "message": f"Metrics endpoint not yet connected. Configure your monitoring integration for '{component}'.",
    }


@register_tool("get_recent_logs")
async def tool_get_recent_logs(arguments: dict) -> dict:
    """
    Retrieve recent logs for a service.
    STUB: Replace with real log aggregator integration (Loki, CloudWatch Logs, ELK).
    """
    service = arguments.get("service", "unknown")
    lines = arguments.get("lines", 50)
    severity = arguments.get("severity")
    return {
        "status": "stub",
        "service": service,
        "lines_requested": lines,
        "severity_filter": severity,
        "message": f"Log retrieval not yet connected. Configure your log aggregator for '{service}'.",
    }


@register_tool("execute_remediation")
async def tool_execute_remediation(arguments: dict) -> dict:
    """
    Execute a remediation action on infrastructure.
    STUB: Replace with real infrastructure automation (AWS SSM, Ansible, Rundeck).
    """
    action = arguments.get("action", "unknown")
    target = arguments.get("target", "unknown")
    params = arguments.get("parameters", {})
    return {
        "status": "stub",
        "action": action,
        "target": target,
        "parameters": params,
        "message": f"Remediation '{action}' on '{target}' not yet connected. Configure your infrastructure automation.",
    }


@register_tool("send_notification")
async def tool_send_notification(arguments: dict) -> dict:
    """
    Send a notification to a channel.
    STUB: Replace with real integrations (Slack webhook, SendGrid, PagerDuty API).
    """
    channel = arguments.get("channel", "unknown")
    target = arguments.get("target", "unknown")
    message = arguments.get("message", "")
    severity = arguments.get("severity", "info")
    return {
        "status": "stub",
        "channel": channel,
        "target": target,
        "severity": severity,
        "message": f"Notification to {channel}:{target} not yet connected. Configure your notification provider.",
    }


@register_tool("call_api")
async def tool_call_api(arguments: dict) -> dict:
    """
    Make an API call to an internal or external service.
    STUB: Replace with real HTTP dispatch (with URL allowlist validation).
    """
    method = arguments.get("method", "GET")
    url = arguments.get("url", "")
    body = arguments.get("body")
    return {
        "status": "stub",
        "method": method,
        "url": url,
        "body_provided": body is not None,
        "message": f"API dispatch not yet connected. Configure URL allowlist and implement {method} {url}.",
    }


@register_tool("generate_report")
async def tool_generate_report(arguments: dict) -> dict:
    """
    Generate a formatted report from data.
    STUB: Replace with real report generation logic.
    """
    title = arguments.get("title", "Untitled")
    data = arguments.get("data", {})
    fmt = arguments.get("format", "text")
    return {
        "status": "stub",
        "title": title,
        "format": fmt,
        "data_keys": list(data.keys()) if isinstance(data, dict) else "non-dict",
        "message": f"Report generation not yet connected. Implement formatter for '{fmt}' output.",
    }


# ---------------------------------------------------------------------------
# 7. Dispatch Endpoint
# ---------------------------------------------------------------------------

class ExecuteRequest(BaseModel):
    tool_name: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_\-]+$")
    arguments: dict = Field(default_factory=dict)


@app.post("/execute", dependencies=[Depends(verify_token)])
async def execute_tool(req: ExecuteRequest):
    """
    Single dispatch endpoint. The orchestrator sends the tool name and arguments;
    the gateway looks up the handler and executes it.
    """
    handler = TOOL_REGISTRY.get(req.tool_name)
    if handler is None:
        logger.warning(f"Unknown tool requested: '{req.tool_name}'")
        raise HTTPException(
            status_code=404,
            detail=f"Tool '{req.tool_name}' is not registered on this gateway.",
        )

    try:
        result = await handler(req.arguments)
        return result
    except Exception as e:
        logger.exception(f"Tool '{req.tool_name}' execution failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Tool execution failed: {str(e)}",
        )


# ---------------------------------------------------------------------------
# 8. Health Check (unauthenticated, for load balancer / orchestrator probes)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "registered_tools": list(TOOL_REGISTRY.keys()),
    }

```

---

## requirements.txt

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
pydantic==2.10.4
```

---
