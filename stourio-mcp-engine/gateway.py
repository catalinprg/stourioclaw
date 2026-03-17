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

import httpx
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


# ---------------------------------------------------------------------------
# 9. Dynamic Tool Registration
# ---------------------------------------------------------------------------

class RegisterToolRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_\-]+$")
    description: str = Field(default="")
    parameters: dict = Field(default_factory=dict)
    handler_type: str = Field(
        default="proxy",
        description="Type of handler to create. Currently only 'proxy' is supported.",
    )
    # proxy-specific fields
    proxy_url: str = Field(
        default="",
        description="URL to forward execution requests to (required when handler_type='proxy').",
    )
    proxy_headers: dict = Field(
        default_factory=dict,
        description="Extra headers to include in proxy requests.",
    )


def _create_proxy_handler(definition: RegisterToolRequest) -> ToolHandler:
    """Return an async handler that proxies execute calls to definition.proxy_url."""

    async def _proxy_handler(arguments: dict) -> dict:
        url = definition.proxy_url
        if not url:
            return {"error": f"Proxy tool '{definition.name}' has no proxy_url configured."}

        headers = dict(definition.proxy_headers)
        logger.info("Proxy tool '%s': POST %s", definition.name, url)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    url,
                    json={"tool_name": definition.name, "arguments": arguments},
                    headers=headers,
                )
                logger.info(
                    "Proxy tool '%s': response status %s", definition.name, response.status_code
                )
                response.raise_for_status()
                try:
                    return response.json()
                except Exception:
                    return {"result": response.text}
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Proxy tool '%s': HTTP %s from %s",
                definition.name, exc.response.status_code, url,
            )
            return {"error": f"Proxy HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except httpx.HTTPError as exc:
            logger.error("Proxy tool '%s': network error: %s", definition.name, exc)
            return {"error": f"Proxy network failure: {exc}"}

    return _proxy_handler


@app.post("/tools/register", dependencies=[Depends(verify_token)], status_code=200)
async def register_dynamic_tool(req: RegisterToolRequest):
    """
    Dynamically register a tool on the gateway at runtime.
    Accepts {name, description, parameters, handler_type, proxy_url, proxy_headers}.
    Currently supports handler_type='proxy', which forwards execution to proxy_url.
    """
    if req.handler_type != "proxy":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported handler_type '{req.handler_type}'. Only 'proxy' is supported.",
        )

    if not req.proxy_url:
        raise HTTPException(
            status_code=400,
            detail="proxy_url is required when handler_type is 'proxy'.",
        )

    handler = _create_proxy_handler(req)
    TOOL_REGISTRY[req.name] = handler

    logger.info(
        "Dynamic tool registered: '%s' -> proxy -> %s", req.name, req.proxy_url
    )
    return {
        "registered": True,
        "name": req.name,
        "handler_type": req.handler_type,
        "proxy_url": req.proxy_url,
    }
