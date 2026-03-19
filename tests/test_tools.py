"""Tests for MCP tools: file_ops and execute_code."""

from __future__ import annotations

import os
import tempfile

import pytest

from src.mcp.tools import file_ops
from src.mcp.tools.execute_code import execute_code


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path):
    """Override WORKSPACE_DIR to a temp directory for sandboxed tests."""
    original = file_ops.WORKSPACE_DIR
    file_ops.WORKSPACE_DIR = str(tmp_path)
    yield tmp_path
    file_ops.WORKSPACE_DIR = original


# ── file_ops: read_file ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_file_within_workspace(workspace):
    """Create a temp file in workspace, read it back."""
    test_content = "hello from test"
    test_file = workspace / "notes.txt"
    test_file.write_text(test_content)

    result = await file_ops.read_file({"path": "notes.txt"})
    assert result["content"] == test_content
    assert "error" not in result


@pytest.mark.asyncio
async def test_read_file_path_traversal_blocked(workspace):
    """../etc/passwd must be rejected."""
    result = await file_ops.read_file({"path": "../etc/passwd"})
    assert "error" in result
    assert "Path traversal blocked" in result["error"]


@pytest.mark.asyncio
async def test_read_file_not_found(workspace):
    """Non-existent file returns error, not exception."""
    result = await file_ops.read_file({"path": "does_not_exist.txt"})
    assert "error" in result
    assert "not found" in result["error"].lower()


# ── file_ops: write_file ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_file_creates_file(workspace):
    """Write a file and verify it exists with correct content."""
    result = await file_ops.write_file(
        {"path": "output/result.txt", "content": "test output"}
    )
    assert result["status"] == "ok"

    written = (workspace / "output" / "result.txt").read_text()
    assert written == "test output"


@pytest.mark.asyncio
async def test_write_file_path_traversal_blocked(workspace):
    """Write to path outside workspace must be rejected."""
    result = await file_ops.write_file(
        {"path": "../../evil.txt", "content": "pwned"}
    )
    assert "error" in result
    assert "Path traversal blocked" in result["error"]


# ── execute_code ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_code_python():
    """Simple Python print, check stdout."""
    result = await execute_code({"code": "print('hello world')", "language": "python"})
    assert result["exit_code"] == 0
    assert "hello world" in result["stdout"]


@pytest.mark.asyncio
async def test_execute_code_bash():
    """Simple bash echo."""
    result = await execute_code({"code": "echo 42", "language": "bash"})
    assert result["exit_code"] == 0
    assert "42" in result["stdout"]


@pytest.mark.asyncio
async def test_execute_code_timeout():
    """Long sleep with short timeout returns error and exit_code=-1."""
    result = await execute_code(
        {"code": "import time; time.sleep(60)", "language": "python", "timeout": 2}
    )
    assert result["exit_code"] == -1
    assert "timed out" in result["error"].lower()


@pytest.mark.asyncio
async def test_execute_code_unsupported_language():
    """Unsupported language returns error."""
    result = await execute_code({"code": "puts 'hi'", "language": "ruby"})
    assert result["exit_code"] == -1
    assert "Unsupported language" in result["error"]


@pytest.mark.asyncio
async def test_execute_code_syntax_error():
    """Python syntax error returns non-zero exit code."""
    result = await execute_code({"code": "def broken(", "language": "python"})
    assert result["exit_code"] != 0
    assert result["stderr"]  # should have traceback


# ── register_all_tools ────────────────────────────────────────────


def test_register_all_tools():
    """register_all_tools populates the global tool_registry."""
    from unittest.mock import patch
    from src.mcp.registry import ToolRegistry

    fresh = ToolRegistry()

    # Patch the singleton in both the registry module and the tools __init__
    # so register_tool sees the fresh instance.
    with patch("src.mcp.registry.tool_registry", fresh), \
         patch("src.mcp.tools.tool_registry", fresh):
        # Force re-import of register_all_tools to pick up patched registry
        import importlib
        import src.mcp.tools as tools_mod
        importlib.reload(tools_mod)
        tools_mod.register_all_tools()

    names = {t.name for t in fresh.list_tools()}

    expected = {
        "web_search",
        "read_file",
        "write_file",
        "execute_code",
        "query_data",
        "search_knowledge",
        "read_audit_log",
        "call_api",
        "send_notification",
        "generate_report",
    }
    assert expected.issubset(names), f"Missing tools: {expected - names}"
