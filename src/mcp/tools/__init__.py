"""Register all MCP tools with the global tool_registry."""

from __future__ import annotations

import logging

from src.mcp.registry import register_tool, tool_registry

logger = logging.getLogger("stourio.tools")


def register_all_tools() -> None:
    """Register every tool with the global tool_registry singleton."""

    from src.mcp.tools.web_search import web_search
    from src.mcp.tools.file_ops import read_file, write_file
    from src.mcp.tools.execute_code import execute_code
    from src.mcp.tools.query_data import query_data
    from src.mcp.tools.knowledge import search_knowledge
    from src.mcp.tools.audit import read_audit_log
    from src.mcp.tools.api import call_api
    from src.mcp.tools.notification import send_notification
    from src.mcp.tools.report import generate_report

    # --- web_search ---
    register_tool(
        registry=tool_registry,
        name="web_search",
        description="Search the web using Tavily API",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {
                    "type": "integer",
                    "description": "Max results to return",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    )(web_search)

    # --- read_file ---
    register_tool(
        registry=tool_registry,
        name="read_file",
        description="Read a file from the workspace directory",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within workspace",
                },
            },
            "required": ["path"],
        },
    )(read_file)

    # --- write_file ---
    register_tool(
        registry=tool_registry,
        name="write_file",
        description="Write content to a file in the workspace directory",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within workspace",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write",
                },
            },
            "required": ["path", "content"],
        },
    )(write_file)

    # --- execute_code ---
    register_tool(
        registry=tool_registry,
        name="execute_code",
        description="Execute Python or bash code in a sandboxed subprocess",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Code to execute"},
                "language": {
                    "type": "string",
                    "enum": ["python", "bash"],
                    "default": "python",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds",
                    "default": 30,
                },
            },
            "required": ["code"],
        },
    )(execute_code)

    # --- query_data ---
    register_tool(
        registry=tool_registry,
        name="query_data",
        description="Parse and query CSV or JSON structured data",
        parameters={
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "Raw CSV or JSON string"},
                "format": {
                    "type": "string",
                    "enum": ["csv", "json"],
                    "default": "json",
                },
                "filter_field": {
                    "type": "string",
                    "description": "Field name to filter on",
                },
                "filter_value": {
                    "type": "string",
                    "description": "Value to match",
                },
            },
            "required": ["data"],
        },
    )(query_data)

    # --- search_knowledge ---
    register_tool(
        registry=tool_registry,
        name="search_knowledge",
        description="Semantic search over internal knowledge base (runbooks, docs) via pgvector",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return",
                    "default": 5,
                },
                "source_type": {
                    "type": "string",
                    "enum": ["runbook", "agent_memory", "incident"],
                    "description": "Optional: filter by source type",
                },
            },
            "required": ["query"],
        },
    )(search_knowledge)

    # --- read_audit_log ---
    register_tool(
        registry=tool_registry,
        name="read_audit_log",
        description="Query the audit log with optional filters (agent, action, time range)",
        parameters={
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Filter by agent ID",
                },
                "action": {
                    "type": "string",
                    "description": "Filter by action type",
                },
                "hours": {
                    "type": "integer",
                    "description": "Look back N hours (default 24)",
                    "default": 24,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return",
                    "default": 20,
                },
            },
        },
    )(read_audit_log)

    # --- call_api ---
    register_tool(
        registry=tool_registry,
        name="call_api",
        description="Make HTTP requests to external APIs",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Target URL"},
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    "default": "GET",
                },
                "headers": {
                    "type": "object",
                    "description": "HTTP headers",
                },
                "body": {
                    "type": "object",
                    "description": "JSON body for POST/PUT/PATCH",
                },
                "timeout": {"type": "integer", "default": 30},
            },
            "required": ["url"],
        },
    )(call_api)

    # --- send_notification ---
    register_tool(
        registry=tool_registry,
        name="send_notification",
        description="Send a notification via Telegram to allowed recipients",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Notification message"},
                "channel": {
                    "type": "string",
                    "description": "Notification channel",
                    "default": "default",
                },
                "severity": {
                    "type": "string",
                    "enum": ["info", "warning", "critical"],
                    "default": "info",
                },
            },
            "required": ["message"],
        },
    )(send_notification)

    # --- generate_report ---
    register_tool(
        registry=tool_registry,
        name="generate_report",
        description="Generate a markdown report from structured sections",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Report title"},
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string"},
                            "content": {"type": "string"},
                        },
                    },
                    "description": "Report sections",
                },
                "include_timestamp": {
                    "type": "boolean",
                    "default": True,
                },
            },
        },
    )(generate_report)

    logger.info("Registered %d MCP tools", len(tool_registry.list_tools()))
