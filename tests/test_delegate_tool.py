"""Tests for inter-agent delegation tool."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_delegate_rejects_missing_agent_type():
    from src.mcp.tools.delegate import delegate_to_agent

    result = await delegate_to_agent({"objective": "do something"})
    assert "error" in result


@pytest.mark.asyncio
async def test_delegate_rejects_missing_objective():
    from src.mcp.tools.delegate import delegate_to_agent

    result = await delegate_to_agent({"agent_type": "analyst"})
    assert "error" in result


@pytest.mark.asyncio
async def test_delegate_enforces_max_depth():
    from src.mcp.tools.delegate import delegate_to_agent, _delegation_depth

    token = _delegation_depth.set(5)
    try:
        result = await delegate_to_agent({
            "agent_type": "analyst",
            "objective": "deep analysis",
        })
        assert "error" in result
        assert "depth" in result["error"].lower()
    finally:
        _delegation_depth.reset(token)
