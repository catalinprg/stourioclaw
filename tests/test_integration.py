"""Integration tests: end-to-end flows through the FastAPI app via ASGI transport.

These tests build a minimal FastAPI app with mocked dependencies so no real
DB, Redis, or external service is required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import router, get_api_key
from src.persistence.database import AgentModel, get_session

# ---------------------------------------------------------------------------
# Shared helpers (mirror the pattern from test_agent_api.py)
# ---------------------------------------------------------------------------

_TEST_KEY = "test-integration-key"


def _make_agent(**overrides) -> AgentModel:
    defaults = dict(
        id="01INTEG",
        name="integ_agent",
        display_name="Integration Agent",
        description="Used in integration tests",
        system_prompt="You are an integration test agent.",
        model="gpt-4",
        tools=[],
        max_steps=8,
        max_concurrent=2,
        is_active=True,
        is_system=False,
    )
    defaults.update(overrides)
    return AgentModel(**defaults)


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


def _build_app(session) -> FastAPI:
    """Build a test FastAPI app that bypasses auth and DB."""
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_api_key] = lambda: _TEST_KEY
    app.dependency_overrides[get_session] = lambda: session
    return app


# ---------------------------------------------------------------------------
# Test 1: POST /api/webhook returns 202
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("src.api.routes.enqueue_signal", new_callable=AsyncMock)
async def test_webhook_to_orchestrator_flow(mock_enqueue):
    """
    Flow: POST /api/webhook with a valid WebhookSignal body.
    Expected: 202 Accepted, signal enqueued to Redis stream.
    """
    mock_enqueue.return_value = "msg-id-123"

    session = _mock_session()
    app = _build_app(session)

    payload = {
        "source": "github",
        "event_type": "push",
        "title": "New commit on main",
        "severity": "low",
        "payload": {"repo": "stourioclaw", "branch": "main"},
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/webhook",
            json=payload,
            headers={"X-STOURIO-KEY": _TEST_KEY},
        )

    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["status"] == "queued"
    mock_enqueue.assert_awaited_once()
    enqueued = mock_enqueue.call_args[0][0]
    assert enqueued["source"] == "github"
    assert enqueued["event_type"] == "push"


# ---------------------------------------------------------------------------
# Test 2: Create → List → Delete agent flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("src.persistence.audit.log", new_callable=AsyncMock)
async def test_agent_crud_flow(mock_audit):
    """
    Flow: POST /api/agents → GET /api/agents → DELETE /api/agents/{name}

    Step 1 – Create: returns 201 with id and name.
    Step 2 – List:   returns 200 with the new agent visible.
    Step 3 – Delete: returns 200 with status=deleted.
    """
    session = _mock_session()
    app = _build_app(session)
    transport = ASGITransport(app=app)

    created_agent = _make_agent(name="crud_flow_agent")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1 – Create
        # get_by_name returns empty (no conflict), then the newly created agent
        session.execute.return_value = _mock_scalars([])

        create_resp = await client.post(
            "/api/agents",
            json={
                "name": "crud_flow_agent",
                "display_name": "CRUD Flow Agent",
                "description": "Created during integration test",
                "system_prompt": "You assist with integration testing.",
                "model": "gpt-4",
                "tools": [],
                "max_steps": 5,
                "max_concurrent": 1,
            },
            headers={"X-STOURIO-KEY": _TEST_KEY},
        )

        assert create_resp.status_code == 201, (
            f"Expected 201, got {create_resp.status_code}: {create_resp.text}"
        )
        create_body = create_resp.json()
        assert create_body["name"] == "crud_flow_agent"
        assert "id" in create_body
        session.commit.assert_awaited()

        # Step 2 – List (agent now exists)
        session.execute.return_value = _mock_scalars([created_agent])
        session.commit.reset_mock()

        list_resp = await client.get(
            "/api/agents",
            headers={"X-STOURIO-KEY": _TEST_KEY},
        )

        assert list_resp.status_code == 200, (
            f"Expected 200, got {list_resp.status_code}: {list_resp.text}"
        )
        agents = list_resp.json()
        assert any(a["name"] == "crud_flow_agent" for a in agents), (
            f"Agent not found in list response: {agents}"
        )

        # Step 3 – Delete
        session.execute.return_value = _mock_scalars([created_agent])
        session.commit.reset_mock()

        delete_resp = await client.delete(
            "/api/agents/crud_flow_agent",
            headers={"X-STOURIO-KEY": _TEST_KEY},
        )

        assert delete_resp.status_code == 200, (
            f"Expected 200, got {delete_resp.status_code}: {delete_resp.text}"
        )
        delete_body = delete_resp.json()
        assert delete_body["status"] == "deleted"
        session.commit.assert_awaited()

    # audit.log should have been called for create and delete
    assert mock_audit.await_count >= 2
