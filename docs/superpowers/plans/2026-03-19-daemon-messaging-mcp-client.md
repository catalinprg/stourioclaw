# Daemon Agents, Async Messaging & MCP Client — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent daemon agents with heartbeat loops, async agent-to-agent messaging via Redis stream inboxes, and MCP client support for agents to consume external tool servers.

**Architecture:** Unified Agent Process Model — extend the existing agent runtime rather than building parallel infrastructure. Daemons are agents that don't stop after one task. Inboxes are Redis streams per agent. MCP client discovers remote tools and merges them into the agent's tool set. All features go through the existing security interceptor, approval workflow, and audit trail.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy (async), Redis streams + pub/sub, MCP SDK (Python client), PostgreSQL, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-03-19-daemon-messaging-mcp-client-design.md`

---

## Review Errata (MUST READ before implementing)

The following fixes from plan review MUST be applied during implementation. Each references the task it affects.

### E1: Orchestrator daemon routing must check if daemon is running (Task 13)
The spec guarantees no dead letters. Task 13 must check if the daemon is actually running before routing to inbox. If not running, fall back to `AgentPool.execute()` as a oneshot. The daemon manager should expose a `is_running(name)` method, and the orchestrator should call it.

### E2: MCP client must hold persistent sessions (Task 9)
The plan's `connect()` opens and closes the MCP session within the method. Sessions must be held open across tool calls. Refactor: `connect()` stores the live `ClientSession` in `self._connections`, `execute_tool()` reuses it, `disconnect()` closes it. For SSE, this means keeping the `sse_client` context open. For stdio, implement actual tool discovery (not a placeholder).

### E3: Inject calling agent_name into messaging tools (Task 5)
`send_message` reads `_from_agent` from arguments, but nothing injects it. Fix: `default_tool_executor` should inject `_agent_name` into `arguments` before calling any tool. Then `send_message` reads `arguments.pop("_agent_name", "unknown")`. Similarly, `read_messages` must only read the caller's own inbox — validate `agent_name == _agent_name`.

### E4: Update API models for new agent columns (Task 8 or new task)
`_agent_to_dict()`, `AgentCreateRequest`, and `AgentUpdateRequest` in `routes.py` must include `execution_mode`, `daemon_config`, `mcp_servers`, `allowed_peers`. Without this, the new columns are invisible to the admin panel and API.

### E5: MCP tool security must use the registry's wired interceptor (Task 10)
Task 10 instantiates a new `SecurityInterceptor()` for MCP calls. This bypasses the Telegram approval workflow (which is wired into the registry's interceptor). Fix: extract the interceptor check into a shared function, or access the registry's `_interceptor` directly.

### E6: Update daemon health monitoring (Task 7)
`_last_heartbeat` must update after each cycle, not just at task start. Add a callback or return value from `run_daemon_loop` that updates the timestamp. The manager should periodically check for stale daemons (`now - last_heartbeat > tick_seconds * 3`) and restart them.

### E7: Import ordering in Task 11
Add `model_validator` to pydantic imports BEFORE writing the endpoint code, not as a separate step after.

---

## File Structure

### Phase 0: Prerequisite Fix
| Action | Path | Responsibility |
|--------|------|---------------|
| Modify | `src/agents/runtime.py` | Thread `agent_name` through `default_tool_executor` |

### Phase 1: Inbox + Messaging
| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `src/daemons/__init__.py` | Package marker |
| Create | `src/daemons/inbox.py` | Inbox enqueue/dequeue/ack, pub/sub notification, message validation |
| Create | `src/mcp/tools/messaging.py` | `send_message`, `read_messages`, `heartbeat_ack` tools |
| Create | `tests/test_inbox.py` | Inbox + messaging tests |
| Modify | `src/persistence/redis_store.py` | Add dedicated pub/sub connection helper |
| Modify | `src/persistence/database.py` | Add `allowed_peers` column to AgentModel |
| Modify | `src/mcp/tools/__init__.py` | Register messaging tools |
| Create | `migrations/versions/003_add_daemons_and_mcp.py` | DB migration (all new columns + tables) |

### Phase 2: Daemon Manager
| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `src/daemons/loop.py` | Single daemon loop — event wait, heartbeat, agent cycle, graceful stop |
| Create | `src/daemons/manager.py` | Daemon manager — spawn/stop tasks, health monitoring, control events |
| Create | `tests/test_daemon_loop.py` | Daemon loop + manager tests |
| Modify | `src/persistence/database.py` | Add `execution_mode`, `daemon_config` to AgentModel |
| Modify | `src/config.py` | Add daemon settings |
| Modify | `src/main.py` | Start daemon manager in lifespan |
| Modify | `src/api/routes.py` | Add daemon control endpoints |

### Phase 3: MCP Client
| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `src/mcp/client.py` | MCP client pool — connect, discover tools, execute remote calls |
| Create | `tests/test_mcp_client.py` | MCP client tests |
| Modify | `src/persistence/database.py` | Add `McpServerRecord`, `mcp_servers` column to AgentModel |
| Modify | `src/agents/runtime.py` | Extend `_resolve_tools()` for MCP tools, MCP fallback in executor |
| Modify | `src/security/interceptor.py` | Handle MCP-sourced tools |
| Modify | `src/config.py` | Add MCP client settings |
| Modify | `src/api/routes.py` | Add MCP server CRUD endpoints |
| Modify | `src/main.py` | Init MCP client pool in lifespan |

### Phase 4: Orchestrator Integration
| Action | Path | Responsibility |
|--------|------|---------------|
| Modify | `src/orchestrator/core.py` | Route to daemon inbox instead of direct execution |
| Modify | `src/api/routes.py` | Extend status endpoint with daemon info |

---

## Task 1: Phase 0 — Thread agent_name Through Tool Executor

**Files:**
- Modify: `src/agents/runtime.py:62-80` (default_tool_executor)
- Modify: `src/agents/runtime.py:190-208` (step loop tool call)
- Test: `tests/test_agent_runtime.py`

- [ ] **Step 1: Write failing test**

Create or append to `tests/test_agent_runtime.py`:

```python
@pytest.mark.asyncio
async def test_tool_executor_passes_agent_name():
    """default_tool_executor should pass agent_name to registry.execute."""
    from src.agents.runtime import default_tool_executor

    with patch("src.agents.runtime.get_registry") as mock_get_registry:
        mock_registry = MagicMock()
        mock_registry.execute = AsyncMock(return_value={"result": "ok"})
        mock_get_registry.return_value = mock_registry

        await default_tool_executor("web_search", {"query": "test"}, agent_name="analyst")

        mock_registry.execute.assert_called_once_with("web_search", {"query": "test"}, agent_name="analyst")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_agent_runtime.py::test_tool_executor_passes_agent_name -v`
Expected: FAIL (default_tool_executor doesn't accept agent_name)

- [ ] **Step 3: Modify default_tool_executor to accept and pass agent_name**

In `src/agents/runtime.py`, change the function signature and call:

```python
async def default_tool_executor(tool_name: str, arguments: dict, agent_name: str = "unknown") -> str:
    """
    Production tool executor. Dispatches LLM tool calls via the ToolRegistry.
    Each tool's execution_mode determines local vs gateway dispatch.
    """
    registry = get_registry()

    if not _SAFE_TOOL_NAME.match(tool_name):
        logger.warning(f"SECURITY: Tool name contains illegal characters: '{tool_name}'")
        return json.dumps({"error": f"Invalid tool name: {tool_name}"})

    try:
        result = await registry.execute(tool_name, arguments, agent_name=agent_name)
        return json.dumps(result) if isinstance(result, dict) else str(result)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        logger.error(f"Tool execution failed: {tool_name}: {e}")
        return json.dumps({"error": f"Tool execution failed: {str(e)}"})
```

- [ ] **Step 4: Update step loop to pass agent_name**

In `src/agents/runtime.py`, in the step loop (around line 205-208), change:

```python
                if tool_executor:
                    tool_result = await tool_executor(tc["name"], tc["arguments"])
                else:
                    tool_result = await default_tool_executor(tc["name"], tc["arguments"])
```

to:

```python
                if tool_executor:
                    tool_result = await tool_executor(tc["name"], tc["arguments"])
                else:
                    tool_result = await default_tool_executor(tc["name"], tc["arguments"], agent_name=agent.name)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_agent_runtime.py::test_tool_executor_passes_agent_name -v`
Expected: PASS

- [ ] **Step 6: Run full test suite to verify no regressions**

Run: `python3 -m pytest tests/ --ignore=tests/test_embeddings_adapter.py --ignore=tests/test_orchestrator_routing.py -v --tb=short`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/agents/runtime.py tests/test_agent_runtime.py
git commit -m "fix: thread agent_name through default_tool_executor for audit accuracy"
```

---

## Task 2: Phase 1 — DB Migration for All New Columns + Tables

**Files:**
- Modify: `src/persistence/database.py`
- Create: `migrations/versions/003_add_daemons_and_mcp.py`

- [ ] **Step 1: Add new columns to AgentModel and create McpServerRecord**

In `src/persistence/database.py`, add columns to `AgentModel` (after `updated_at`):

```python
    execution_mode = Column(String(20), server_default="oneshot")
    daemon_config = Column(JSON, nullable=True)
    mcp_servers = Column(JSON, default=list)
    allowed_peers = Column(JSON, default=list)
```

Add new `McpServerRecord` class after `CronJobRecord`:

```python
class McpServerRecord(Base):
    __tablename__ = "mcp_servers"

    id = Column(String, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    endpoint_url = Column(String(500), nullable=True)
    endpoint_command = Column(String(500), nullable=True)
    transport = Column(String(20), nullable=False)
    auth_env_var = Column(String(100), nullable=True)
    high_risk_tools = Column(JSON, default=list)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
```

- [ ] **Step 2: Create migration file**

Create `migrations/versions/003_add_daemons_and_mcp.py`:

```python
"""Add daemon mode, messaging, and MCP client support.

Revision ID: 003
Revises: 002
Create Date: 2026-03-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extend agents table
    op.add_column("agents", sa.Column("execution_mode", sa.String(20), server_default="oneshot"))
    op.add_column("agents", sa.Column("daemon_config", sa.JSON(), nullable=True))
    op.add_column("agents", sa.Column("mcp_servers", sa.JSON(), server_default="[]"))
    op.add_column("agents", sa.Column("allowed_peers", sa.JSON(), server_default="[]"))

    # MCP servers table
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("endpoint_url", sa.String(500), nullable=True),
        sa.Column("endpoint_command", sa.String(500), nullable=True),
        sa.Column("transport", sa.String(20), nullable=False),
        sa.Column("auth_env_var", sa.String(100), nullable=True),
        sa.Column("high_risk_tools", sa.JSON(), server_default="[]"),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("mcp_servers")
    op.drop_column("agents", "allowed_peers")
    op.drop_column("agents", "mcp_servers")
    op.drop_column("agents", "daemon_config")
    op.drop_column("agents", "execution_mode")
```

- [ ] **Step 3: Commit**

```bash
git add src/persistence/database.py migrations/versions/003_add_daemons_and_mcp.py
git commit -m "feat: add DB schema for daemon mode, messaging peers, and MCP servers"
```

---

## Task 3: Phase 1 — Redis Pub/Sub Connection Helper

**Files:**
- Modify: `src/persistence/redis_store.py`
- Create: `tests/test_redis_pubsub.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_redis_pubsub.py`:

```python
"""Tests for Redis pub/sub connection helper."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_get_pubsub_connection_returns_pubsub():
    from src.persistence.redis_store import get_pubsub_connection

    with patch("src.persistence.redis_store.get_redis") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_pubsub = MagicMock()
        mock_redis.pubsub.return_value = mock_pubsub
        mock_get_redis.return_value = mock_redis

        result = await get_pubsub_connection()
        assert result is mock_pubsub


@pytest.mark.asyncio
async def test_publish_daemon_event():
    from src.persistence.redis_store import publish_daemon_event

    with patch("src.persistence.redis_store.get_redis") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis

        await publish_daemon_event("start", "my-daemon")
        mock_redis.publish.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_redis_pubsub.py -v`
Expected: FAIL

- [ ] **Step 3: Add pub/sub helpers to redis_store.py**

Append to `src/persistence/redis_store.py`:

```python
# --- Pub/Sub for Daemon Events ---

DAEMON_EVENTS_CHANNEL = "stourio:daemon:events"
INBOX_NOTIFY_PREFIX = "stourio:inbox_notify:"


async def get_pubsub_connection():
    """Get a pub/sub object from the Redis connection.

    Note: The caller is responsible for subscribing to channels
    and managing the pub/sub lifecycle.
    """
    r = await get_redis()
    return r.pubsub()


async def publish_daemon_event(event_type: str, agent_name: str) -> None:
    """Publish a daemon control event (start/stop/restart)."""
    r = await get_redis()
    payload = json.dumps({"event": event_type, "agent": agent_name})
    await r.publish(DAEMON_EVENTS_CHANNEL, payload)
    logger.info("Published daemon event: %s -> %s", event_type, agent_name)


async def notify_inbox(agent_name: str) -> None:
    """Notify a daemon that it has a new inbox message via pub/sub."""
    r = await get_redis()
    await r.publish(f"{INBOX_NOTIFY_PREFIX}{agent_name}", "new_message")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_redis_pubsub.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/persistence/redis_store.py tests/test_redis_pubsub.py
git commit -m "feat: add Redis pub/sub helpers for daemon events and inbox notifications"
```

---

## Task 4: Phase 1 — Inbox Module

**Files:**
- Create: `src/daemons/__init__.py`
- Create: `src/daemons/inbox.py`
- Create: `tests/test_inbox.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_inbox.py`:

```python
"""Tests for agent inbox (Redis stream-based message queue)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_enqueue_message():
    from src.daemons.inbox import enqueue_message

    with patch("src.daemons.inbox.get_redis") as mock_get_redis, \
         patch("src.daemons.inbox.notify_inbox") as mock_notify:
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="1234-0")
        mock_get_redis.return_value = mock_redis
        mock_notify.return_value = None

        result = await enqueue_message("analyst", "hello", from_agent="assistant")
        assert result == "1234-0"
        mock_redis.xadd.assert_called_once()
        mock_notify.assert_called_once_with("analyst")


@pytest.mark.asyncio
async def test_enqueue_rejects_oversized_message():
    from src.daemons.inbox import enqueue_message

    long_msg = "x" * 10001
    result = await enqueue_message("analyst", long_msg, from_agent="assistant")
    assert result is None


@pytest.mark.asyncio
async def test_dequeue_messages():
    from src.daemons.inbox import dequeue_messages

    with patch("src.daemons.inbox.get_redis") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.xreadgroup = AsyncMock(return_value=[
            ("stourio:inbox:analyst", [
                ("msg-1", {"data": '{"from_agent":"assistant","message":"hi"}'})
            ])
        ])
        mock_get_redis.return_value = mock_redis

        messages = await dequeue_messages("analyst", count=10)
        assert len(messages) == 1
        assert messages[0][1]["from_agent"] == "assistant"


@pytest.mark.asyncio
async def test_ack_message():
    from src.daemons.inbox import ack_message

    with patch("src.daemons.inbox.get_redis") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis

        await ack_message("analyst", "msg-1")
        mock_redis.xack.assert_called_once()
        mock_redis.xdel.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_inbox.py -v`
Expected: FAIL

- [ ] **Step 3: Implement inbox module**

Create `src/daemons/__init__.py` (empty).

Create `src/daemons/inbox.py`:

```python
"""Agent inbox — Redis stream-based message queue.

Each agent has an inbox stream: stourio:inbox:{agent_name}
Messages are enqueued by other agents (send_message tool),
the orchestrator (routing to daemons), or cron/webhooks.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from src.persistence.redis_store import get_redis, notify_inbox
import redis

logger = logging.getLogger("stourio.daemons.inbox")

INBOX_PREFIX = "stourio:inbox:"
INBOX_GROUP_PREFIX = "stourio:inbox_group:"
MAX_MESSAGE_SIZE = 10000


async def init_inbox_group(agent_name: str) -> None:
    """Ensure consumer group exists for an agent's inbox stream."""
    r = await get_redis()
    stream = f"{INBOX_PREFIX}{agent_name}"
    group = f"{INBOX_GROUP_PREFIX}{agent_name}"
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except redis.exceptions.ResponseError as e:
        if "already exists" not in str(e).lower():
            raise


async def enqueue_message(
    target_agent: str,
    message: str,
    from_agent: str = "system",
    context: str = "",
    conversation_id: str | None = None,
) -> str | None:
    """Add a message to an agent's inbox. Returns stream entry ID or None if rejected."""
    if len(message) > MAX_MESSAGE_SIZE:
        logger.warning("Message to '%s' rejected: %d chars exceeds max %d", target_agent, len(message), MAX_MESSAGE_SIZE)
        return None

    r = await get_redis()
    stream = f"{INBOX_PREFIX}{target_agent}"

    payload = json.dumps({
        "from_agent": from_agent,
        "message": message,
        "context": context,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "conversation_id": conversation_id,
    })

    entry_id = await r.xadd(stream, {"data": payload})
    logger.info("Inbox message enqueued: %s -> %s (id=%s)", from_agent, target_agent, entry_id)

    # Wake daemon if running
    await notify_inbox(target_agent)

    return entry_id


async def dequeue_messages(
    agent_name: str,
    count: int = 10,
    consumer_name: str = "daemon",
) -> list[tuple[str, dict]]:
    """Read pending messages from an agent's inbox."""
    r = await get_redis()
    stream = f"{INBOX_PREFIX}{agent_name}"
    group = f"{INBOX_GROUP_PREFIX}{agent_name}"

    entries = await r.xreadgroup(group, consumer_name, {stream: ">"}, count=count)

    results = []
    if entries:
        for _, messages in entries:
            for message_id, data in messages:
                results.append((message_id, json.loads(data["data"])))
    return results


async def ack_message(agent_name: str, message_id: str) -> None:
    """Acknowledge a processed inbox message."""
    r = await get_redis()
    stream = f"{INBOX_PREFIX}{agent_name}"
    group = f"{INBOX_GROUP_PREFIX}{agent_name}"
    await r.xack(stream, group, message_id)
    await r.xdel(stream, message_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_inbox.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/daemons/ tests/test_inbox.py
git commit -m "feat: add agent inbox with Redis stream queue and pub/sub notification"
```

---

## Task 5: Phase 1 — Messaging Tools (send_message, read_messages, heartbeat_ack)

**Files:**
- Create: `src/mcp/tools/messaging.py`
- Modify: `src/mcp/tools/__init__.py`
- Test: `tests/test_inbox.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_inbox.py`:

```python
@pytest.mark.asyncio
async def test_send_message_tool_rejects_missing_target():
    from src.mcp.tools.messaging import send_message

    result = await send_message({"message": "hello"})
    assert "error" in result


@pytest.mark.asyncio
async def test_send_message_tool_rejects_disallowed_peer():
    from src.mcp.tools.messaging import send_message

    with patch("src.mcp.tools.messaging._check_peer_allowed") as mock_check:
        mock_check.return_value = False
        result = await send_message({
            "target_agent": "analyst",
            "message": "hello",
        })
        assert "error" in result
        assert "not allowed" in result["error"].lower()


@pytest.mark.asyncio
async def test_heartbeat_ack_tool():
    from src.mcp.tools.messaging import heartbeat_ack

    result = await heartbeat_ack({})
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_read_messages_tool():
    from src.mcp.tools.messaging import read_messages

    with patch("src.mcp.tools.messaging.dequeue_messages") as mock_dequeue:
        mock_dequeue.return_value = [("msg-1", {"from_agent": "assistant", "message": "hi"})]
        result = await read_messages({"agent_name": "analyst"})
        assert result["count"] == 1
        assert len(result["messages"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_inbox.py::test_send_message_tool_rejects_missing_target -v`
Expected: FAIL

- [ ] **Step 3: Implement messaging tools**

Create `src/mcp/tools/messaging.py`:

```python
"""Messaging tools for inter-agent communication.

- send_message: fire-and-forget message to another agent's inbox
- read_messages: check your own inbox for pending messages
- heartbeat_ack: daemon signals "nothing to report" (suppresses output)
"""
from __future__ import annotations

import logging

from src.daemons.inbox import enqueue_message, dequeue_messages
from src.persistence.database import async_session, AgentModel
from sqlalchemy import select

logger = logging.getLogger("stourio.tools.messaging")


async def _check_peer_allowed(from_agent: str, target_agent: str) -> bool:
    """Check if from_agent is in target_agent's allowed_peers list."""
    async with async_session() as session:
        result = await session.execute(
            select(AgentModel).where(AgentModel.name == target_agent)
        )
        agent = result.scalars().first()
        if not agent:
            return False
        peers = agent.allowed_peers or []
        return from_agent in peers


async def send_message(arguments: dict) -> dict:
    """Send a message to another agent's inbox.

    Args:
        target_agent: Name of the receiving agent
        message: Message content (max 10,000 chars)
        context: Optional additional context
    """
    target = arguments.get("target_agent")
    message = arguments.get("message")
    context = arguments.get("context", "")
    from_agent = arguments.get("_from_agent", "unknown")

    if not target:
        return {"error": "Missing required parameter: target_agent"}
    if not message:
        return {"error": "Missing required parameter: message"}

    # Check peer allowlist
    if not await _check_peer_allowed(from_agent, target):
        return {"error": f"Agent '{from_agent}' is not allowed to message '{target}'. Update allowed_peers on the target agent."}

    entry_id = await enqueue_message(
        target_agent=target,
        message=message,
        from_agent=from_agent,
        context=context,
    )

    if entry_id is None:
        return {"error": "Message rejected: exceeds maximum size (10,000 characters)"}

    return {"status": "delivered", "target_agent": target, "entry_id": entry_id}


async def read_messages(arguments: dict) -> dict:
    """Check inbox for pending messages.

    Args:
        agent_name: Your agent name
        limit: Max messages to return (default 10)
    """
    agent_name = arguments.get("agent_name")
    limit = arguments.get("limit", 10)

    if not agent_name:
        return {"error": "Missing required parameter: agent_name"}

    messages = await dequeue_messages(agent_name, count=limit)

    return {
        "count": len(messages),
        "messages": [
            {"id": msg_id, **data}
            for msg_id, data in messages
        ],
    }


async def heartbeat_ack(arguments: dict) -> dict:
    """Daemon signals nothing needs attention this cycle.

    Call this when your heartbeat check finds nothing to act on.
    The daemon manager will suppress output delivery for this cycle.
    """
    return {"status": "ok", "action": "heartbeat_ack"}
```

- [ ] **Step 4: Register tools in __init__.py**

In `src/mcp/tools/__init__.py`, add import:

```python
    from src.mcp.tools.messaging import send_message, read_messages, heartbeat_ack
```

Add registration blocks before the final `logger.info` line:

```python
    # --- send_message ---
    register_tool(
        registry=tool_registry,
        name="send_message",
        description="Send a message to another agent's inbox. Fire-and-forget, non-blocking. Use for inter-agent communication.",
        parameters={
            "type": "object",
            "properties": {
                "target_agent": {
                    "type": "string",
                    "description": "Name of the agent to send the message to",
                },
                "message": {
                    "type": "string",
                    "description": "Message content (max 10,000 characters)",
                },
                "context": {
                    "type": "string",
                    "description": "Additional context for the receiving agent",
                },
            },
            "required": ["target_agent", "message"],
        },
    )(send_message)

    # --- read_messages ---
    register_tool(
        registry=tool_registry,
        name="read_messages",
        description="Check your inbox for pending messages from other agents.",
        parameters={
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Your agent name",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max messages to return (default 10)",
                    "default": 10,
                },
            },
            "required": ["agent_name"],
        },
    )(read_messages)

    # --- heartbeat_ack ---
    register_tool(
        registry=tool_registry,
        name="heartbeat_ack",
        description="Signal that your heartbeat check found nothing requiring action. Call this when everything looks normal.",
        parameters={
            "type": "object",
            "properties": {},
        },
    )(heartbeat_ack)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_inbox.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/mcp/tools/messaging.py src/mcp/tools/__init__.py tests/test_inbox.py
git commit -m "feat: add send_message, read_messages, heartbeat_ack tools for inter-agent messaging"
```

---

## Task 6: Phase 2 — Daemon Loop

**Files:**
- Create: `src/daemons/loop.py`
- Create: `tests/test_daemon_loop.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_daemon_loop.py`:

```python
"""Tests for daemon loop."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_daemon_cycle_heartbeat_no_messages():
    """Daemon cycle with no inbox messages runs heartbeat prompt."""
    from src.daemons.loop import run_daemon_cycle

    mock_execution = MagicMock()
    mock_execution.status.value = "completed"
    mock_execution.result = "Everything looks normal."
    mock_execution.steps = []

    with patch("src.daemons.loop.dequeue_messages", new_callable=AsyncMock, return_value=[]), \
         patch("src.daemons.loop.get_pool") as mock_pool, \
         patch("src.daemons.loop.async_session") as mock_session_factory, \
         patch("src.daemons.loop.audit") as mock_audit:
        mock_audit.log = AsyncMock()
        mock_pool.return_value.execute = AsyncMock(return_value=mock_execution)
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await run_daemon_cycle("test-daemon", "Check things.", max_messages=10)

    assert result["suppressed"] is False
    assert result["result"] == "Everything looks normal."


@pytest.mark.asyncio
async def test_daemon_cycle_suppressed_on_heartbeat_ack():
    """Daemon cycle is suppressed when agent calls heartbeat_ack tool."""
    from src.daemons.loop import run_daemon_cycle

    mock_execution = MagicMock()
    mock_execution.status.value = "completed"
    mock_execution.result = "All clear."
    mock_execution.steps = [{"tool": "heartbeat_ack", "type": "tool_call"}]

    with patch("src.daemons.loop.dequeue_messages", new_callable=AsyncMock, return_value=[]), \
         patch("src.daemons.loop.get_pool") as mock_pool, \
         patch("src.daemons.loop.async_session") as mock_session_factory, \
         patch("src.daemons.loop.audit") as mock_audit:
        mock_audit.log = AsyncMock()
        mock_pool.return_value.execute = AsyncMock(return_value=mock_execution)
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await run_daemon_cycle("test-daemon", "Check things.", max_messages=10)

    assert result["suppressed"] is True


@pytest.mark.asyncio
async def test_is_in_active_hours():
    from src.daemons.loop import is_in_active_hours
    from datetime import time

    # Always active if no window set
    assert is_in_active_hours(None) is True

    # Within window
    config = {"start": "00:00", "end": "23:59"}
    assert is_in_active_hours(config) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_daemon_loop.py -v`
Expected: FAIL

- [ ] **Step 3: Implement daemon loop**

Create `src/daemons/loop.py`:

```python
"""Single daemon loop — one cycle of heartbeat check + inbox processing.

The daemon manager (manager.py) calls run_daemon_loop() which loops forever.
Each iteration calls run_daemon_cycle() which is one heartbeat tick.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timezone

from src.daemons.inbox import dequeue_messages, ack_message, init_inbox_group
from src.orchestrator.concurrency import get_pool
from src.persistence import audit
from src.persistence.database import async_session
from src.persistence.redis_store import get_pubsub_connection, INBOX_NOTIFY_PREFIX

logger = logging.getLogger("stourio.daemons.loop")


def is_in_active_hours(active_hours: dict | None) -> bool:
    """Check if current time is within active hours window."""
    if active_hours is None:
        return True

    now = datetime.now(timezone.utc).time()
    start = time.fromisoformat(active_hours["start"])
    end = time.fromisoformat(active_hours["end"])

    if start <= end:
        return start <= now <= end
    else:
        # Overnight window (e.g., 22:00 - 06:00)
        return now >= start or now <= end


async def run_daemon_cycle(
    agent_name: str,
    heartbeat_prompt: str,
    max_messages: int = 10,
    inbox_messages: list | None = None,
) -> dict:
    """Execute one daemon cycle.

    1. Read inbox (or use provided messages)
    2. Build objective from heartbeat prompt + messages
    3. Execute agent cycle
    4. Check if heartbeat_ack was called (suppress if so)
    5. Ack processed messages

    Returns dict with: result, suppressed, messages_processed, execution_id
    """
    # Read inbox if not provided
    if inbox_messages is None:
        inbox_messages = await dequeue_messages(agent_name, count=max_messages)

    # Build objective
    if inbox_messages:
        msg_texts = []
        for msg_id, data in inbox_messages:
            msg_texts.append(f"[From {data.get('from_agent', 'unknown')}]: {data.get('message', '')}")
        objective = f"You have {len(inbox_messages)} new message(s):\n" + "\n".join(msg_texts) + f"\n\n{heartbeat_prompt}"
    else:
        objective = heartbeat_prompt

    # Execute one agent cycle
    conversation_id = f"daemon:{agent_name}"

    async with async_session() as session:
        execution = await get_pool().execute(
            agent_type=agent_name,
            objective=objective,
            context=f"Daemon cycle for '{agent_name}'",
            session=session,
            conversation_id=conversation_id,
        )

    # Check if heartbeat_ack was called
    suppressed = any(
        step.get("tool") == "heartbeat_ack"
        for step in (execution.steps or [])
        if step.get("type") == "tool_call"
    )

    if suppressed:
        await audit.log("DAEMON_HEARTBEAT", f"Daemon '{agent_name}' heartbeat OK (suppressed)")
    else:
        await audit.log("DAEMON_CYCLE", f"Daemon '{agent_name}' cycle completed: {(execution.result or '')[:200]}")

    # Ack processed inbox messages
    for msg_id, _ in inbox_messages:
        await ack_message(agent_name, msg_id)

    return {
        "result": execution.result,
        "suppressed": suppressed,
        "messages_processed": len(inbox_messages),
        "execution_id": execution.id,
        "status": execution.status.value,
    }


async def run_daemon_loop(
    agent_name: str,
    daemon_config: dict,
    stopping: asyncio.Event,
) -> None:
    """Run the daemon loop indefinitely until stopping is set.

    Wakes on: inbox pub/sub notification OR tick_seconds timeout.
    """
    tick_seconds = daemon_config.get("tick_seconds", 300)
    heartbeat_prompt = daemon_config.get("heartbeat_prompt", "Check inbox. If nothing needs attention, call heartbeat_ack.")
    active_hours = daemon_config.get("active_hours")
    max_messages = daemon_config.get("max_messages_per_cycle", 10)

    logger.info("Daemon loop started: %s (tick=%ds)", agent_name, tick_seconds)

    # Init inbox consumer group
    await init_inbox_group(agent_name)

    # Subscribe to inbox notifications
    pubsub = await get_pubsub_connection()
    channel = f"{INBOX_NOTIFY_PREFIX}{agent_name}"
    await pubsub.subscribe(channel)

    try:
        while not stopping.is_set():
            # Wait for event or tick timeout
            try:
                msg = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=tick_seconds,
                )
            except asyncio.TimeoutError:
                msg = None  # Tick timeout — run heartbeat

            if stopping.is_set():
                break

            # Check active hours
            if not is_in_active_hours(active_hours):
                await asyncio.sleep(60)  # Sleep 1 min and re-check
                continue

            # Run one cycle
            try:
                result = await run_daemon_cycle(
                    agent_name=agent_name,
                    heartbeat_prompt=heartbeat_prompt,
                    max_messages=max_messages,
                )

                if not result["suppressed"] and result["result"]:
                    # Deliver to user via Telegram
                    try:
                        from src.mcp.tools.notification import _send_telegram
                        await _send_telegram(
                            f"[Daemon: {agent_name}]\n\n{result['result']}",
                            severity="info",
                        )
                    except Exception as e:
                        logger.warning("Failed to deliver daemon output: %s", e)

            except Exception as e:
                logger.error("Daemon '%s' cycle failed: %s", agent_name, e)
                await audit.log("DAEMON_ERROR", f"Daemon '{agent_name}' cycle error: {e}")

    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        logger.info("Daemon loop stopped: %s", agent_name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_daemon_loop.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/daemons/loop.py tests/test_daemon_loop.py
git commit -m "feat: add daemon loop with heartbeat, inbox processing, and active_hours"
```

---

## Task 7: Phase 2 — Daemon Manager

**Files:**
- Create: `src/daemons/manager.py`
- Modify: `src/config.py`
- Modify: `src/main.py`
- Test: `tests/test_daemon_loop.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `tests/test_daemon_loop.py`:

```python
@pytest.mark.asyncio
async def test_daemon_manager_starts_active_daemons():
    from src.daemons.manager import DaemonManager

    mock_agent = MagicMock()
    mock_agent.name = "test-daemon"
    mock_agent.daemon_config = {"tick_seconds": 60, "heartbeat_prompt": "check"}

    with patch("src.daemons.manager.async_session") as mock_sf, \
         patch("src.daemons.manager.AgentRegistry") as mock_reg_cls, \
         patch("src.daemons.manager.run_daemon_loop", new_callable=AsyncMock):
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_reg = AsyncMock()
        mock_reg.list_daemons = AsyncMock(return_value=[mock_agent])
        mock_reg_cls.return_value = mock_reg

        manager = DaemonManager()
        await manager.start()

        assert "test-daemon" in manager._tasks
        await manager.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_daemon_loop.py::test_daemon_manager_starts_active_daemons -v`
Expected: FAIL

- [ ] **Step 3: Implement daemon manager**

Create `src/daemons/manager.py`:

```python
"""Daemon manager — spawns, monitors, and controls daemon agent tasks."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from src.agents.registry import AgentRegistry
from src.daemons.loop import run_daemon_loop
from src.persistence import audit
from src.persistence.database import async_session
from src.persistence.redis_store import get_pubsub_connection, DAEMON_EVENTS_CHANNEL

logger = logging.getLogger("stourio.daemons.manager")


class DaemonManager:
    """Manages lifecycle of all daemon agents."""

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._stop_events: dict[str, asyncio.Event] = {}
        self._last_heartbeat: dict[str, datetime] = {}
        self._control_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Load and start all active daemon agents."""
        async with async_session() as session:
            registry = AgentRegistry(session)
            daemons = await registry.list_daemons()

        for agent in daemons:
            await self._start_daemon(agent.name, agent.daemon_config or {})

        # Start control event listener
        self._control_task = asyncio.create_task(self._listen_control_events())

        logger.info("Daemon manager started: %d daemon(s)", len(self._tasks))

    async def stop(self) -> None:
        """Gracefully stop all daemons."""
        if self._control_task:
            self._control_task.cancel()
            try:
                await self._control_task
            except asyncio.CancelledError:
                pass

        for name in list(self._tasks.keys()):
            await self._stop_daemon(name)

        logger.info("Daemon manager stopped")

    async def _start_daemon(self, name: str, config: dict) -> None:
        """Start a single daemon."""
        if name in self._tasks:
            logger.warning("Daemon '%s' already running", name)
            return

        stop_event = asyncio.Event()
        self._stop_events[name] = stop_event

        task = asyncio.create_task(
            self._run_with_health_check(name, config, stop_event)
        )
        self._tasks[name] = task

        await audit.log("DAEMON_STARTED", f"Daemon '{name}' started")
        logger.info("Started daemon: %s", name)

    async def _stop_daemon(self, name: str) -> None:
        """Gracefully stop a daemon (finishes current cycle)."""
        if name not in self._tasks:
            return

        self._stop_events[name].set()

        try:
            await asyncio.wait_for(self._tasks[name], timeout=60)
        except asyncio.TimeoutError:
            self._tasks[name].cancel()
            try:
                await self._tasks[name]
            except asyncio.CancelledError:
                pass

        del self._tasks[name]
        del self._stop_events[name]
        self._last_heartbeat.pop(name, None)

        await audit.log("DAEMON_STOPPED", f"Daemon '{name}' stopped")
        logger.info("Stopped daemon: %s", name)

    async def _run_with_health_check(self, name: str, config: dict, stop_event: asyncio.Event) -> None:
        """Run daemon loop and restart on crash."""
        while not stop_event.is_set():
            try:
                self._last_heartbeat[name] = datetime.now(timezone.utc)
                await run_daemon_loop(name, config, stop_event)
            except Exception as e:
                logger.error("Daemon '%s' crashed: %s. Restarting in 10s...", name, e)
                await audit.log("DAEMON_CRASHED", f"Daemon '{name}' crashed: {e}. Auto-restarting.")
                await asyncio.sleep(10)

    async def _listen_control_events(self) -> None:
        """Listen for daemon control events (start/stop/restart) via Redis pub/sub."""
        pubsub = await get_pubsub_connection()
        await pubsub.subscribe(DAEMON_EVENTS_CHANNEL)

        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    try:
                        data = json.loads(msg["data"])
                        event = data.get("event")
                        agent = data.get("agent")

                        if event == "start":
                            async with async_session() as session:
                                reg = AgentRegistry(session)
                                agent_model = await reg.get_by_name(agent)
                            if agent_model:
                                await self._start_daemon(agent, agent_model.daemon_config or {})
                        elif event == "stop":
                            await self._stop_daemon(agent)
                        elif event == "restart":
                            await self._stop_daemon(agent)
                            async with async_session() as session:
                                reg = AgentRegistry(session)
                                agent_model = await reg.get_by_name(agent)
                            if agent_model:
                                await self._start_daemon(agent, agent_model.daemon_config or {})
                    except Exception as e:
                        logger.error("Error handling daemon event: %s", e)
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(DAEMON_EVENTS_CHANNEL)
            await pubsub.close()

    def status(self) -> dict:
        """Return status of all daemons."""
        return {
            name: {
                "running": not task.done(),
                "last_heartbeat": self._last_heartbeat.get(name, "").isoformat() if self._last_heartbeat.get(name) else None,
            }
            for name, task in self._tasks.items()
        }
```

- [ ] **Step 4: Add list_daemons to AgentRegistry**

In `src/agents/registry.py`, add method to `AgentRegistry`:

```python
    async def list_daemons(self) -> list[AgentModel]:
        """Return all active daemon agents."""
        result = await self._session.execute(
            select(AgentModel)
            .where(AgentModel.is_active == True)
            .where(AgentModel.execution_mode == "daemon")
        )
        return result.scalars().all()
```

- [ ] **Step 5: Add daemon config settings**

In `src/config.py`, add to Settings class:

```python
    # Daemon agents
    daemon_manager_enabled: bool = True
    daemon_default_tick_seconds: int = 300
```

- [ ] **Step 6: Wire daemon manager into main.py lifespan**

In `src/main.py`, add import:

```python
from src.daemons.manager import DaemonManager
```

In the lifespan, after the scheduler_task creation, add:

```python
    # 10. Daemon manager
    daemon_manager = None
    if settings.daemon_manager_enabled:
        daemon_manager = DaemonManager()
        await daemon_manager.start()
```

In the shutdown section, before browser pool cleanup:

```python
    # Stop daemons gracefully
    if daemon_manager:
        await daemon_manager.stop()
```

- [ ] **Step 7: Run tests**

Run: `python3 -m pytest tests/test_daemon_loop.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/daemons/manager.py src/agents/registry.py src/config.py src/main.py tests/test_daemon_loop.py
git commit -m "feat: add daemon manager with lifecycle control, health monitoring, and auto-restart"
```

---

## Task 8: Phase 2 — Daemon Control API Endpoints

**Files:**
- Modify: `src/api/routes.py`

- [ ] **Step 1: Add daemon control endpoints**

In `src/api/routes.py`, add imports:

```python
from src.persistence.redis_store import publish_daemon_event
```

Add section before the SECURITY ALERTS section:

```python
# =============================================================================
# DAEMONS - Runtime control
# =============================================================================

@router.post("/daemons/{name}/start")
async def start_daemon(name: str, session: AsyncSession = Depends(get_session)):
    registry = AgentRegistry(session)
    agent = await registry.get_by_name(name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found.")
    if agent.execution_mode != "daemon":
        raise HTTPException(status_code=400, detail=f"Agent '{name}' is not a daemon.")
    await publish_daemon_event("start", name)
    await audit.log("DAEMON_CONTROL", f"Daemon '{name}' start requested via API")
    return {"status": "starting", "name": name}


@router.post("/daemons/{name}/stop")
async def stop_daemon(name: str):
    await publish_daemon_event("stop", name)
    await audit.log("DAEMON_CONTROL", f"Daemon '{name}' stop requested via API")
    return {"status": "stopping", "name": name}


@router.post("/daemons/{name}/restart")
async def restart_daemon(name: str, session: AsyncSession = Depends(get_session)):
    registry = AgentRegistry(session)
    agent = await registry.get_by_name(name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found.")
    if agent.execution_mode != "daemon":
        raise HTTPException(status_code=400, detail=f"Agent '{name}' is not a daemon.")
    await publish_daemon_event("restart", name)
    await audit.log("DAEMON_CONTROL", f"Daemon '{name}' restart requested via API")
    return {"status": "restarting", "name": name}
```

- [ ] **Step 2: Commit**

```bash
git add src/api/routes.py
git commit -m "feat: add daemon control API endpoints (start/stop/restart)"
```

---

## Task 9: Phase 3 — MCP Client Pool

**Files:**
- Create: `src/mcp/client.py`
- Create: `tests/test_mcp_client.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_mcp_client.py`:

```python
"""Tests for MCP client pool."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_mcp_client_pool_connect():
    from src.mcp.client import McpClientPool

    pool = McpClientPool()
    assert pool.is_connected("notion") is False


@pytest.mark.asyncio
async def test_mcp_client_pool_get_tools_unknown_server():
    from src.mcp.client import McpClientPool

    pool = McpClientPool()
    tools = pool.get_tools("nonexistent")
    assert tools == []


@pytest.mark.asyncio
async def test_mcp_client_pool_resolve_auth():
    from src.mcp.client import McpClientPool
    import os

    pool = McpClientPool()
    os.environ["TEST_MCP_TOKEN"] = "secret123"
    try:
        result = pool._resolve_auth("TEST_MCP_TOKEN")
        assert result == "secret123"
    finally:
        del os.environ["TEST_MCP_TOKEN"]


@pytest.mark.asyncio
async def test_mcp_client_pool_resolve_auth_missing():
    from src.mcp.client import McpClientPool

    pool = McpClientPool()
    result = pool._resolve_auth("NONEXISTENT_VAR")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_mcp_client.py -v`
Expected: FAIL

- [ ] **Step 3: Implement MCP client pool**

Create `src/mcp/client.py`:

```python
"""MCP client pool — manages connections to external MCP servers.

Agents use this to discover and call tools on external MCP servers
(Notion, GitHub, Slack, etc.). Tools are discovered on first connect
and cached. Auth is resolved from environment variables at connection time.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from src.models.schemas import ToolDefinition

logger = logging.getLogger("stourio.mcp.client")


class McpClientPool:
    """Manages connections to external MCP servers."""

    def __init__(self):
        self._connections: dict[str, Any] = {}  # server_name -> client session
        self._tools: dict[str, list[ToolDefinition]] = {}  # server_name -> tool defs
        self._server_configs: dict[str, dict] = {}  # server_name -> config

    def _resolve_auth(self, env_var: str | None) -> str | None:
        """Resolve auth token from environment variable at call time."""
        if not env_var:
            return None
        return os.environ.get(env_var)

    def is_connected(self, server_name: str) -> bool:
        return server_name in self._connections

    def get_tools(self, server_name: str) -> list[ToolDefinition]:
        """Get cached tool definitions for a server."""
        return self._tools.get(server_name, [])

    def get_all_tools_for_agent(self, server_names: list[str]) -> list[ToolDefinition]:
        """Get all tool definitions from multiple MCP servers."""
        tools = []
        for name in server_names:
            tools.extend(self.get_tools(name))
        return tools

    async def connect(self, server_name: str, config: dict) -> bool:
        """Connect to an MCP server and discover its tools.

        Config should have: transport, endpoint_url or endpoint_command, auth_env_var
        """
        if server_name in self._connections:
            logger.info("Already connected to '%s'", server_name)
            return True

        transport = config.get("transport")
        auth_token = self._resolve_auth(config.get("auth_env_var"))

        try:
            if transport == "sse":
                endpoint = config.get("endpoint_url")
                if not endpoint:
                    logger.error("MCP server '%s' missing endpoint_url", server_name)
                    return False

                from mcp.client.sse import sse_client
                # Store config for lazy reconnection
                self._server_configs[server_name] = config
                # Connect via SSE
                # Note: actual MCP SDK connection is done here
                # For now, store a placeholder and discover tools
                logger.info("Connecting to MCP server '%s' via SSE: %s", server_name, endpoint)

                async with sse_client(endpoint) as (read_stream, write_stream):
                    from mcp import ClientSession
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        tools_result = await session.list_tools()

                        tool_defs = []
                        for tool in tools_result.tools:
                            tool_defs.append(ToolDefinition(
                                name=f"{server_name}__{tool.name}",
                                description=f"[{server_name}] {tool.description or ''}",
                                parameters=tool.inputSchema if hasattr(tool, 'inputSchema') else {},
                            ))

                        self._tools[server_name] = tool_defs
                        self._connections[server_name] = {"transport": "sse", "endpoint": endpoint}
                        logger.info("Connected to '%s': %d tools discovered", server_name, len(tool_defs))
                        return True

            elif transport == "stdio":
                endpoint_cmd = config.get("endpoint_command")
                if not endpoint_cmd:
                    logger.error("MCP server '%s' missing endpoint_command", server_name)
                    return False

                from src.config import settings
                if endpoint_cmd not in (settings.mcp_stdio_allowed_commands or []):
                    logger.error("MCP stdio command '%s' not in allowlist", endpoint_cmd)
                    return False

                self._server_configs[server_name] = config
                logger.info("Connecting to MCP server '%s' via stdio: %s", server_name, endpoint_cmd)
                # stdio connections are long-lived processes — implemented similarly
                # For now, mark as connected
                self._connections[server_name] = {"transport": "stdio", "command": endpoint_cmd}
                return True

            else:
                logger.error("Unknown transport '%s' for MCP server '%s'", transport, server_name)
                return False

        except Exception as e:
            logger.error("Failed to connect to MCP server '%s': %s", server_name, e)
            return False

    async def execute_tool(self, server_name: str, tool_name: str, arguments: dict) -> dict:
        """Execute a tool on a remote MCP server."""
        if server_name not in self._connections:
            return {"error": f"MCP server '{server_name}' not connected"}

        config = self._server_configs.get(server_name, {})

        try:
            if config.get("transport") == "sse":
                endpoint = config.get("endpoint_url")
                from mcp.client.sse import sse_client
                from mcp import ClientSession

                async with sse_client(endpoint) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.call_tool(tool_name, arguments)
                        # Extract text content
                        if result.content:
                            texts = [c.text for c in result.content if hasattr(c, 'text')]
                            return {"result": "\n".join(texts)}
                        return {"result": "Tool completed with no output"}

            return {"error": f"Transport '{config.get('transport')}' execution not implemented"}

        except Exception as e:
            logger.error("MCP tool execution failed: %s/%s: %s", server_name, tool_name, e)
            return {"error": f"MCP tool execution failed: {str(e)}"}

    async def disconnect(self, server_name: str) -> None:
        """Disconnect from an MCP server."""
        self._connections.pop(server_name, None)
        self._tools.pop(server_name, None)
        self._server_configs.pop(server_name, None)
        logger.info("Disconnected from MCP server: %s", server_name)

    async def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        for name in list(self._connections.keys()):
            await self.disconnect(name)


# Global singleton
_pool: McpClientPool | None = None


def get_mcp_client_pool() -> McpClientPool:
    global _pool
    if _pool is None:
        _pool = McpClientPool()
    return _pool
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_mcp_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mcp/client.py tests/test_mcp_client.py
git commit -m "feat: add MCP client pool with SSE/stdio transport and tool discovery"
```

---

## Task 10: Phase 3 — Extend Agent Runtime for MCP Tools

**Files:**
- Modify: `src/agents/runtime.py` (_resolve_tools and default_tool_executor)
- Modify: `src/config.py`

- [ ] **Step 1: Extend _resolve_tools to include MCP tools**

In `src/agents/runtime.py`, modify `_resolve_tools()`:

```python
def _resolve_tools(agent: AgentModel) -> list[ToolDefinition]:
    """Resolve tool name strings from DB agent into ToolDefinition objects.

    Returns union of:
    - Local tools from the tool registry (agent.tools)
    - Remote tools from assigned MCP servers (agent.mcp_servers)
    """
    registry = get_registry()
    tool_defs = []

    # Local tools
    for tool_name in (agent.tools or []):
        tool = registry.get(tool_name)
        if tool:
            tool_defs.append(ToolDefinition(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
            ))
        else:
            logger.warning("Tool '%s' referenced by agent '%s' not found in registry", tool_name, agent.name)

    # MCP server tools
    mcp_server_names = agent.mcp_servers or []
    if mcp_server_names:
        from src.mcp.client import get_mcp_client_pool
        pool = get_mcp_client_pool()
        mcp_tools = pool.get_all_tools_for_agent(mcp_server_names)
        tool_defs.extend(mcp_tools)

    return tool_defs
```

- [ ] **Step 2: Extend default_tool_executor with MCP fallback**

In `src/agents/runtime.py`, update `default_tool_executor()` to add MCP fallback in the `except ValueError` branch:

```python
async def default_tool_executor(tool_name: str, arguments: dict, agent_name: str = "unknown") -> str:
    """
    Production tool executor. Dispatches LLM tool calls via the ToolRegistry.
    Falls back to MCP client pool for remote tools not in the local registry.
    """
    registry = get_registry()

    if not _SAFE_TOOL_NAME.match(tool_name):
        logger.warning(f"SECURITY: Tool name contains illegal characters: '{tool_name}'")
        return json.dumps({"error": f"Invalid tool name: {tool_name}"})

    try:
        result = await registry.execute(tool_name, arguments, agent_name=agent_name)
        return json.dumps(result) if isinstance(result, dict) else str(result)
    except ValueError:
        # Tool not in local registry — check MCP client pool
        if "__" in tool_name:
            server_name, remote_tool_name = tool_name.split("__", 1)
            from src.mcp.client import get_mcp_client_pool
            pool = get_mcp_client_pool()
            if pool.is_connected(server_name):
                # Security interceptor check for MCP tools
                from src.security.interceptor import SecurityInterceptor
                interceptor = SecurityInterceptor()
                check = await interceptor.check_tool_call(tool_name, arguments, agent_name)
                if check.intercepted:
                    return json.dumps({"blocked": True, "reason": check.reason})

                result = await pool.execute_tool(server_name, remote_tool_name, arguments)
                return json.dumps(result)
        return json.dumps({"error": f"Tool '{tool_name}' not found in local registry or MCP servers"})
    except Exception as e:
        logger.error(f"Tool execution failed: {tool_name}: {e}")
        return json.dumps({"error": f"Tool execution failed: {str(e)}"})
```

- [ ] **Step 3: Add MCP config settings**

In `src/config.py`, add to Settings:

```python
    # MCP client
    mcp_client_timeout: int = 30
    mcp_stdio_allowed_commands: list[str] = []
```

- [ ] **Step 4: Run full test suite**

Run: `python3 -m pytest tests/ --ignore=tests/test_embeddings_adapter.py --ignore=tests/test_orchestrator_routing.py -v --tb=short`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/agents/runtime.py src/config.py
git commit -m "feat: extend agent runtime with MCP tool resolution and execution fallback"
```

---

## Task 11: Phase 3 — MCP Server CRUD API

**Files:**
- Modify: `src/api/routes.py`

- [ ] **Step 1: Add MCP server CRUD endpoints**

Add imports:

```python
from src.persistence.database import McpServerRecord
from src.models.schemas import new_id
from src.mcp.client import get_mcp_client_pool
```

Add section:

```python
# =============================================================================
# MCP SERVERS - External tool server connections
# =============================================================================

class McpServerCreateRequest(BaseModel):
    name: str = Field(..., max_length=100)
    endpoint_url: Optional[str] = None
    endpoint_command: Optional[str] = None
    transport: str = Field(..., pattern="^(sse|stdio)$")
    auth_env_var: Optional[str] = None

    @model_validator(mode="after")
    def validate_endpoint(self):
        if self.transport == "sse" and not self.endpoint_url:
            raise ValueError("SSE transport requires endpoint_url")
        if self.transport == "stdio" and not self.endpoint_command:
            raise ValueError("stdio transport requires endpoint_command")
        if self.endpoint_url and self.endpoint_command:
            raise ValueError("Provide endpoint_url OR endpoint_command, not both")
        return self


@router.get("/mcp-servers")
async def list_mcp_servers(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(McpServerRecord).order_by(McpServerRecord.created_at.desc()))
    servers = result.scalars().all()
    pool = get_mcp_client_pool()
    return [
        {
            "id": s.id,
            "name": s.name,
            "transport": s.transport,
            "endpoint_url": s.endpoint_url,
            "endpoint_command": s.endpoint_command,
            "auth_env_var": s.auth_env_var,
            "active": s.active,
            "connected": pool.is_connected(s.name),
            "tools": [t.name for t in pool.get_tools(s.name)],
        }
        for s in servers
    ]


@router.post("/mcp-servers", status_code=201)
async def create_mcp_server(req: McpServerCreateRequest, session: AsyncSession = Depends(get_session)):
    # Check uniqueness
    existing = await session.execute(select(McpServerRecord).where(McpServerRecord.name == req.name))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail=f"MCP server '{req.name}' already exists.")

    # Check auth env var exists
    if req.auth_env_var:
        import os
        if not os.environ.get(req.auth_env_var):
            raise HTTPException(status_code=400, detail=f"Environment variable '{req.auth_env_var}' is not set.")

    record = McpServerRecord(
        id=new_id(),
        name=req.name,
        endpoint_url=req.endpoint_url,
        endpoint_command=req.endpoint_command,
        transport=req.transport,
        auth_env_var=req.auth_env_var,
    )
    session.add(record)
    await session.commit()

    # Connect immediately
    pool = get_mcp_client_pool()
    connected = await pool.connect(req.name, {
        "transport": req.transport,
        "endpoint_url": req.endpoint_url,
        "endpoint_command": req.endpoint_command,
        "auth_env_var": req.auth_env_var,
    })

    await audit.log("MCP_SERVER_CREATED", f"MCP server '{req.name}' registered (connected={connected})")
    return {"id": record.id, "name": req.name, "connected": connected}


@router.delete("/mcp-servers/{name}")
async def delete_mcp_server(name: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(McpServerRecord).where(McpServerRecord.name == name))
    record = result.scalars().first()
    if not record:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found.")

    pool = get_mcp_client_pool()
    await pool.disconnect(name)

    await session.delete(record)
    await session.commit()
    await audit.log("MCP_SERVER_DELETED", f"MCP server '{name}' removed")
    return {"status": "deleted", "name": name}


@router.post("/mcp-servers/{name}/refresh")
async def refresh_mcp_server(name: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(McpServerRecord).where(McpServerRecord.name == name))
    record = result.scalars().first()
    if not record:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found.")

    pool = get_mcp_client_pool()
    await pool.disconnect(name)
    connected = await pool.connect(name, {
        "transport": record.transport,
        "endpoint_url": record.endpoint_url,
        "endpoint_command": record.endpoint_command,
        "auth_env_var": record.auth_env_var,
    })

    tools = [t.name for t in pool.get_tools(name)]
    await audit.log("MCP_SERVER_REFRESHED", f"MCP server '{name}' refreshed: {len(tools)} tools")
    return {"name": name, "connected": connected, "tools": tools}
```

- [ ] **Step 2: Add model_validator import**

Add to imports in `src/api/routes.py`:

```python
from pydantic import BaseModel, Field, model_validator
```

- [ ] **Step 3: Commit**

```bash
git add src/api/routes.py
git commit -m "feat: add MCP server CRUD API with auto-connect and tool discovery"
```

---

## Task 12: Phase 3 — Wire MCP Client Pool Into Lifespan

**Files:**
- Modify: `src/main.py`

- [ ] **Step 1: Init MCP client pool on startup**

In `src/main.py`, add import:

```python
from src.mcp.client import get_mcp_client_pool
```

In the lifespan, after daemon manager start, add:

```python
    # 11. MCP client pool — connect to registered servers
    mcp_pool = get_mcp_client_pool()
    async with async_session() as session:
        from src.persistence.database import McpServerRecord
        result = await session.execute(
            select(McpServerRecord).where(McpServerRecord.active == True)
        )
        for server in result.scalars().all():
            await mcp_pool.connect(server.name, {
                "transport": server.transport,
                "endpoint_url": server.endpoint_url,
                "endpoint_command": server.endpoint_command,
                "auth_env_var": server.auth_env_var,
            })
```

Add `select` import if not already present.

In the shutdown section, before browser pool cleanup:

```python
    # Disconnect MCP clients
    await get_mcp_client_pool().disconnect_all()
```

- [ ] **Step 2: Commit**

```bash
git add src/main.py
git commit -m "feat: wire MCP client pool into lifespan with auto-connect on startup"
```

---

## Task 13: Phase 4 — Orchestrator Integration

**Files:**
- Modify: `src/orchestrator/core.py`

- [ ] **Step 1: Modify routing for daemon agents**

In `src/orchestrator/core.py`, in the `route_to_agent` handling (around line 273-315), after determining the `agent_type`, add a check for daemon mode:

Before the existing `execution = await get_pool().execute(...)` block, add:

```python
        # Check if target agent is a daemon — route to inbox
        async with get_session() as sess:
            _reg = AgentRegistry(sess)
            _target = await _reg.get_by_name(agent_type)

        if _target and _target.execution_mode == "daemon":
            from src.daemons.inbox import enqueue_message
            entry_id = await enqueue_message(
                target_agent=agent_type,
                message=args.get("objective", signal.content),
                from_agent="orchestrator",
                context=signal.content,
            )
            return {
                "status": "routed_to_daemon",
                "message": f"Message delivered to daemon '{agent_type}' inbox.",
                "agent_type": agent_type,
                "entry_id": entry_id,
            }
```

- [ ] **Step 2: Run full test suite**

Run: `python3 -m pytest tests/ --ignore=tests/test_embeddings_adapter.py --ignore=tests/test_orchestrator_routing.py -v --tb=short`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add src/orchestrator/core.py
git commit -m "feat: route messages to daemon agent inbox instead of direct execution"
```

---

## Task 14: Phase 4 — Extend Status Endpoint

**Files:**
- Modify: `src/api/routes.py`

- [ ] **Step 1: Extend /api/status with daemon and MCP info**

In the `status()` endpoint in `src/api/routes.py`, add daemon manager and MCP pool status:

```python
@router.get("/status")
async def status():
    killed = await is_killed()
    approvals = await get_pending_approvals()

    # Daemon status
    daemon_status = {}
    try:
        from src.daemons.manager import DaemonManager
        # Access the manager instance — this needs to be made accessible
        # For now, return basic info from DB
        async with async_session() as session:
            registry = AgentRegistry(session)
            daemons = await registry.list_daemons()
            daemon_status = {d.name: {"execution_mode": "daemon", "active": d.is_active} for d in daemons}
    except Exception:
        pass

    # MCP server status
    mcp_status = {}
    try:
        pool = get_mcp_client_pool()
        for name in pool._connections:
            mcp_status[name] = {"connected": True, "tools_count": len(pool.get_tools(name))}
    except Exception:
        pass

    return {
        "status": "killed" if killed else "operational",
        "kill_switch": killed,
        "pending_approvals": len(approvals),
        "agents": [t.model_dump() for t in list_templates()],
        "workflows": [w.model_dump() for w in list_workflows()],
        "agent_pool": get_pool().status(),
        "daemons": daemon_status,
        "mcp_servers": mcp_status,
    }
```

- [ ] **Step 2: Commit**

```bash
git add src/api/routes.py
git commit -m "feat: extend status endpoint with daemon and MCP server info"
```

---

## Task 15: Full Integration Test

- [ ] **Step 1: Run complete test suite**

Run: `python3 -m pytest tests/ --ignore=tests/test_embeddings_adapter.py --ignore=tests/test_orchestrator_routing.py -v --tb=short`
Expected: All pass

- [ ] **Step 2: Verify all imports**

Run: `python3 -c "from src.daemons.inbox import enqueue_message, dequeue_messages; from src.daemons.loop import run_daemon_cycle, run_daemon_loop; from src.daemons.manager import DaemonManager; from src.mcp.client import McpClientPool, get_mcp_client_pool; from src.mcp.tools.messaging import send_message, read_messages, heartbeat_ack; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 3: Commit**

```bash
git commit --allow-empty -m "chore: verify full integration of daemon, messaging, and MCP client features"
```
