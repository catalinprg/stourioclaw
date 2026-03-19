"""Tests for Agent CRUD and Security Alerts API endpoints."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from src.api.routes import router, get_api_key
from src.persistence.database import AgentModel, SecurityAlertModel, get_session


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


def _make_alert(**overrides) -> SecurityAlertModel:
    defaults = dict(
        id="01ALERT",
        severity="HIGH",
        alert_type="HIGH_FREQUENCY",
        description="Test alert",
        source_agent="test_agent",
        source_execution_id="exec_1",
        raw_evidence={},
        status="OPEN",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        resolved_at=None,
    )
    defaults.update(overrides)
    return SecurityAlertModel(**defaults)


def _mock_scalars(items):
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
    session.commit = AsyncMock()
    return session


def _build_app(session):
    """Build a test FastAPI app with mocked dependencies."""
    app = FastAPI()
    app.include_router(router, prefix="/api")

    # Override API key check
    app.dependency_overrides[get_api_key] = lambda: "test-key"
    # Override DB session
    app.dependency_overrides[get_session] = lambda: session

    return app


# ---------------------------------------------------------------------------
# Agent CRUD Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_agents():
    """GET /api/agents returns 200 with list of active agents."""
    agent = _make_agent(name="agent_one")
    session = _mock_session()
    session.execute.return_value = _mock_scalars([agent])

    app = _build_app(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/agents")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "agent_one"
    assert data[0]["is_active"] is True


@pytest.mark.asyncio
@patch("src.persistence.audit.log", new_callable=AsyncMock)
async def test_create_agent(mock_audit):
    """POST /api/agents creates agent and returns id + name."""
    session = _mock_session()
    # First call: get_by_name check (not found)
    # Second call: after create (flush)
    session.execute.return_value = _mock_scalars([])

    app = _build_app(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/agents", json={
            "name": "new_agent",
            "display_name": "New Agent",
            "description": "A new agent",
            "system_prompt": "You are new.",
            "model": "gpt-4",
            "tools": ["search_knowledge"],
            "max_steps": 10,
            "max_concurrent": 2,
        })

    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "new_agent"
    assert "id" in data
    session.add.assert_called_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.persistence.audit.log", new_callable=AsyncMock)
async def test_create_agent_conflict(mock_audit):
    """POST /api/agents returns 409 when agent name already exists."""
    existing = _make_agent(name="existing_agent")
    session = _mock_session()
    session.execute.return_value = _mock_scalars([existing])

    app = _build_app(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/agents", json={
            "name": "existing_agent",
            "display_name": "Existing",
            "model": "gpt-4",
        })

    assert resp.status_code == 409


@pytest.mark.asyncio
@patch("src.persistence.audit.log", new_callable=AsyncMock)
async def test_update_agent(mock_audit):
    """PUT /api/agents/{name} updates agent fields."""
    agent = _make_agent(name="updatable", description="old")
    session = _mock_session()
    session.execute.return_value = _mock_scalars([agent])

    app = _build_app(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put("/api/agents/updatable", json={
            "description": "new description",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["description"] == "new description"


@pytest.mark.asyncio
async def test_update_agent_not_found():
    """PUT /api/agents/{name} returns 404 when agent doesn't exist."""
    session = _mock_session()
    session.execute.return_value = _mock_scalars([])

    app = _build_app(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put("/api/agents/ghost", json={
            "description": "nope",
        })

    assert resp.status_code == 404


@pytest.mark.asyncio
@patch("src.persistence.audit.log", new_callable=AsyncMock)
async def test_delete_agent(mock_audit):
    """DELETE /api/agents/{name} deletes non-system agent."""
    agent = _make_agent(name="deletable", is_system=False)
    session = _mock_session()
    session.execute.return_value = _mock_scalars([agent])

    app = _build_app(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete("/api/agents/deletable")

    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_delete_system_agent_blocked():
    """DELETE /api/agents/{name} returns 400 for system agents."""
    agent = _make_agent(name="core", is_system=True)
    session = _mock_session()
    session.execute.return_value = _mock_scalars([agent])

    app = _build_app(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete("/api/agents/core")

    assert resp.status_code == 400
    assert "system agent" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_agent_not_found():
    """DELETE /api/agents/{name} returns 400 when not found."""
    session = _mock_session()
    session.execute.return_value = _mock_scalars([])

    app = _build_app(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete("/api/agents/ghost")

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Security Alerts Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_security_alerts():
    """GET /api/security/alerts returns open alerts."""
    alert = _make_alert()
    session = _mock_session()
    session.execute.return_value = _mock_scalars([alert])

    app = _build_app(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/security/alerts")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["severity"] == "HIGH"
    assert data[0]["status"] == "OPEN"


@pytest.mark.asyncio
@patch("src.persistence.audit.log", new_callable=AsyncMock)
async def test_update_alert_status(mock_audit):
    """POST /api/security/alerts/{id} updates alert status."""
    alert = _make_alert(id="alert_123")
    session = _mock_session()
    session.execute.return_value = _mock_scalars([alert])

    app = _build_app(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/security/alerts/alert_123", json={
            "status": "resolved",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "RESOLVED"
    assert alert.resolved_at is not None


@pytest.mark.asyncio
async def test_update_alert_not_found():
    """POST /api/security/alerts/{id} returns 404 when alert doesn't exist."""
    session = _mock_session()
    session.execute.return_value = _mock_scalars([])

    app = _build_app(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/security/alerts/ghost", json={
            "status": "acknowledged",
        })

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_alert_invalid_status():
    """POST /api/security/alerts/{id} rejects invalid status values."""
    session = _mock_session()

    app = _build_app(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/security/alerts/alert_123", json={
            "status": "invalid_status",
        })

    assert resp.status_code == 422
