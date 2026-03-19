"""FastAPI routes for MCP protocol via SSE transport.

Endpoints:
    GET  /mcp/sse         — SSE stream for Claude Code / MCP clients
    POST /mcp/messages/   — POST endpoint for SSE message handling
    GET  /mcp/tools       — REST endpoint listing tools (admin panel)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from mcp.server.sse import SseServerTransport
from starlette.responses import Response

from src.mcp.registry import tool_registry
from src.mcp.server import mcp_server

logger = logging.getLogger("stourio.mcp.router")

mcp_router = APIRouter(prefix="/mcp", tags=["mcp"])
sse_transport = SseServerTransport("/mcp/messages/")


@mcp_router.get("/sse")
async def sse_endpoint(request: Request) -> Response:
    """SSE endpoint that Claude Code connects to via @anthropic-ai/mcp-proxy."""
    logger.info("MCP SSE client connected")
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await mcp_server.run(
            streams[0],
            streams[1],
            mcp_server.create_initialization_options(),
        )
    return Response()


@mcp_router.post("/messages/")
async def messages_endpoint(request: Request) -> Response:
    """Handle POST messages from the SSE transport."""
    logger.debug("MCP POST /messages/")
    await sse_transport.handle_post_message(
        request.scope, request.receive, request._send
    )
    return Response()


@mcp_router.get("/tools")
async def list_tools_rest() -> JSONResponse:
    """REST endpoint to list available tools (for admin panel / debugging)."""
    tools = [
        {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        }
        for t in tool_registry.list_tools()
    ]
    return JSONResponse(content={"tools": tools, "count": len(tools)})
