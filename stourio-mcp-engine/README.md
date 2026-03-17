# Stourio MCP Gateway

**The Zero-Trust Tool Execution Layer**

The MCP Gateway is a standalone service that executes privileged actions on behalf of the Stourio orchestrator. It runs on a separate server from the core framework, ensuring the orchestrator never holds infrastructure credentials or direct access to internal systems.

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

Apache License 2.0 — see [LICENSE](../LICENSE).
