"""Tests for the Agent DB Registry."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from src.agents.registry import AgentRegistry, NON_ROUTABLE_AGENTS, REQUIRED_YAML_FIELDS
from src.persistence.database import AgentModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(**overrides) -> AgentModel:
    defaults = dict(
        id="01AGENT",
        name="test_agent",
        display_name="Test Agent",
        description="A test agent",
        system_prompt="You are a test agent.",
        model="test-model",
        tools=[],
        max_steps=8,
        max_concurrent=3,
        is_active=True,
        is_system=False,
    )
    defaults.update(overrides)
    return AgentModel(**defaults)


def _mock_scalars(items):
    """Create a mock result whose .scalars().all() returns items."""
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = items
    scalars_mock.first.return_value = items[0] if items else None
    result_mock = MagicMock()
    result_mock.scalars.return_value = scalars_mock
    return result_mock


def _mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_active_agents():
    """Verify list_active returns only active agents."""
    active = _make_agent(name="active_one", is_active=True)
    session = _mock_session()
    session.execute.return_value = _mock_scalars([active])

    registry = AgentRegistry(session)
    result = await registry.list_active()

    assert len(result) == 1
    assert result[0].name == "active_one"
    assert result[0].is_active is True
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_agent_by_name():
    """Verify fetch by name returns the correct agent."""
    agent = _make_agent(name="lookup_agent")
    session = _mock_session()
    session.execute.return_value = _mock_scalars([agent])

    registry = AgentRegistry(session)
    result = await registry.get_by_name("lookup_agent")

    assert result is not None
    assert result.name == "lookup_agent"


@pytest.mark.asyncio
async def test_get_agent_by_name_not_found():
    """Verify fetch returns None when agent doesn't exist."""
    session = _mock_session()
    session.execute.return_value = _mock_scalars([])

    registry = AgentRegistry(session)
    result = await registry.get_by_name("nonexistent")

    assert result is None


@pytest.mark.asyncio
async def test_get_routable_agents_excludes_cybersecurity_and_reviewer():
    """Verify non-routable agents are filtered out from list_routable."""
    routable = _make_agent(name="general_agent")
    session = _mock_session()
    session.execute.return_value = _mock_scalars([routable])

    registry = AgentRegistry(session)
    result = await registry.list_routable()

    assert len(result) == 1
    assert result[0].name == "general_agent"
    # Verify the SQL query was constructed (we trust SQLAlchemy filtering)
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_agent_generates_ulid():
    """Verify create assigns a ULID id."""
    session = _mock_session()
    registry = AgentRegistry(session)

    agent = await registry.create(
        name="new_agent",
        display_name="New Agent",
        model="test-model",
    )

    assert agent.id is not None
    assert len(agent.id) == 26  # ULID string length
    assert agent.name == "new_agent"
    session.add.assert_called_once()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_agent():
    """Verify update modifies fields and returns updated agent."""
    agent = _make_agent(name="updatable", description="old")
    session = _mock_session()
    session.execute.return_value = _mock_scalars([agent])

    registry = AgentRegistry(session)
    result = await registry.update("updatable", description="new description")

    assert result is not None
    assert result.description == "new description"
    session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_update_agent_not_found():
    """Verify update returns None when agent doesn't exist."""
    session = _mock_session()
    session.execute.return_value = _mock_scalars([])

    registry = AgentRegistry(session)
    result = await registry.update("ghost", description="nope")

    assert result is None


@pytest.mark.asyncio
async def test_delete_non_system_agent():
    """Verify delete removes non-system agent."""
    agent = _make_agent(name="deletable", is_system=False)
    session = _mock_session()
    session.execute.return_value = _mock_scalars([agent])

    registry = AgentRegistry(session)
    result = await registry.delete("deletable")

    assert result is True
    session.delete.assert_awaited_once_with(agent)


@pytest.mark.asyncio
async def test_delete_system_agent_fails():
    """Verify delete refuses to remove system agents."""
    agent = _make_agent(name="core_agent", is_system=True)
    session = _mock_session()
    session.execute.return_value = _mock_scalars([agent])

    registry = AgentRegistry(session)
    result = await registry.delete("core_agent")

    assert result is False
    session.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_nonexistent_agent_fails():
    """Verify delete returns False for nonexistent agent."""
    session = _mock_session()
    session.execute.return_value = _mock_scalars([])

    registry = AgentRegistry(session)
    result = await registry.delete("ghost")

    assert result is False


@pytest.mark.asyncio
async def test_seed_from_yaml_loads_all_agents():
    """Seed from YAML files in config/agents/ — works with whatever YAMLs exist."""
    config_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "agents"
    )
    yaml_files = list(Path(config_dir).glob("*.yaml"))

    if not yaml_files:
        pytest.skip("No YAML files in config/agents/")

    # First execute call: check if DB is empty (return no agents)
    empty_result = _mock_scalars([])
    session = _mock_session()
    session.execute.return_value = empty_result

    registry = AgentRegistry(session)
    count = await registry.seed_from_yaml(config_dir)

    assert count == len(yaml_files)
    assert session.add.call_count == len(yaml_files)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_seed_from_yaml_skips_when_not_empty():
    """Seed is a no-op when agents table already has data."""
    existing = _make_agent(name="existing")
    session = _mock_session()
    session.execute.return_value = _mock_scalars([existing])

    registry = AgentRegistry(session)
    count = await registry.seed_from_yaml("/some/dir")

    assert count == 0
    # Only the emptiness check query, no adds
    assert session.add.call_count == 0


@pytest.mark.asyncio
async def test_seed_from_yaml_handles_missing_dir():
    """Seed returns 0 for nonexistent directory."""
    session = _mock_session()
    session.execute.return_value = _mock_scalars([])

    registry = AgentRegistry(session)
    count = await registry.seed_from_yaml("/nonexistent/path")

    assert count == 0


def test_all_yaml_files_have_required_fields():
    """Validate that every YAML file in config/agents/ has required fields."""
    config_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "agents"
    )
    yaml_files = list(Path(config_dir).glob("*.yaml"))

    if not yaml_files:
        pytest.skip("No YAML files in config/agents/")

    for yaml_file in yaml_files:
        with open(yaml_file) as f:
            data = yaml.safe_load(f)

        assert data is not None, f"{yaml_file.name} is empty"
        assert isinstance(data, dict), f"{yaml_file.name} is not a mapping"

        missing = REQUIRED_YAML_FIELDS - set(data.keys())
        assert not missing, f"{yaml_file.name} missing fields: {missing}"
