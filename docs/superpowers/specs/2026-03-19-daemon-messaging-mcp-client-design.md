# Daemon Agents, Async Messaging & MCP Client — Design Spec

**Goal:** Transform stourioclaw from a request-response system into an agent operating system with persistent daemons, inter-agent async messaging, and dynamic external tool discovery via MCP client support.

**Approach:** Unified Agent Process Model (Approach 1). Extend the existing agent runtime with new execution modes rather than building parallel infrastructure. Aligned with OpenClaw's architecture — same runtime handles both oneshot and persistent agents, heartbeat-based proactive execution, inbox-based message routing.

---

## 0. Prerequisite Fix: Thread agent_name Through Tool Executor

`default_tool_executor()` in `runtime.py:74` calls `registry.execute(tool_name, arguments)` without passing `agent_name`. It defaults to `"unknown"`. This means security interceptor approvals and audit logs show `agent_name="unknown"` for all tool calls. This must be fixed before building any new features.

**Fix:** Add `agent_name` parameter to `default_tool_executor()` and pass it from the step loop where `agent.name` is available. The daemon loop and MCP client both rely on this for proper audit trails.

---

## 1. Agent Execution Modes

The `agents` table gets new columns:

- `execution_mode`: `oneshot` (default, current behavior) or `daemon`
- `daemon_config`: JSON blob, only used when mode is `daemon`:

```json
{
  "tick_seconds": 300,
  "heartbeat_prompt": "Check audit logs for anomalies. Check inbox for messages. If nothing needs attention, call the heartbeat_ack tool.",
  "active_hours": {"start": "08:00", "end": "22:00"},
  "event_sources": ["inbox"],
  "max_messages_per_cycle": 10
}
```

- `tick_seconds`: Heartbeat interval. Daemon wakes every N seconds even without events.
- `heartbeat_prompt`: The objective passed to the agent on each heartbeat cycle. Equivalent to OpenClaw's HEARTBEAT.md.
- `active_hours`: Optional. If set, daemon only executes within this window. Outside the window, the daemon process stays alive but skips execution. Inbox messages queue up and get processed when the window opens. If null, daemon runs 24/7. Configurable per daemon via API/admin panel.
- `event_sources`: What wakes the daemon besides tick. Currently only `["inbox"]` (new messages trigger immediate wake). Extensible for future sources.
- `max_messages_per_cycle`: Maximum inbox messages processed per cycle (default 10). Remaining messages stay queued in FIFO order for the next cycle. Prevents token explosion from flooded inboxes.

**Oneshot agents are unchanged.** The existing `execute_agent()` step loop stays exactly as-is for `execution_mode: oneshot`.

**Daemons restart fresh.** On container restart, daemons re-launch with their original config. No checkpointing. Input queues (Redis streams) are durable — unacked messages survive restarts, so no messages are lost.

**Persistent conversation context per daemon.** Each daemon gets a stable `conversation_id` of `daemon:{agent_name}`. This is passed to `execute_agent()` on every cycle so conversation history accumulates across cycles via the existing `get_history()` mechanism.

---

## 2. Agent Inbox (Message Queue)

Every agent gets an inbox — a Redis stream keyed by agent name:

```
stourio:inbox:{agent_name}
```

### Three ways messages arrive in an inbox:

1. **Agent-to-agent** — via the `send_message` tool (fire-and-forget, non-blocking). The sender doesn't wait for a response.
2. **Orchestrator routing** — when the orchestrator routes a user message to a daemon agent, it drops the message in the inbox instead of calling `execute_agent()` directly. The daemon picks it up on its next cycle or wakes immediately.
3. **Cron/webhooks** — the scheduler and webhook ingestion can target a specific agent's inbox.

### What happens when a message lands:

- **Target is a running daemon:** Redis pub/sub notifies the daemon's event loop, waking it immediately (no waiting for tick).
- **Target is not running (oneshot or inactive daemon):** The system spins up a oneshot execution with the message as the objective via `AgentPool.execute()`. Messages always trigger execution — no dead letters.

### Inbox tools:

- `send_message` — send a message to another agent's inbox. Parameters: `target_agent`, `message`, `context`. Max message size: 10,000 characters. Returns immediately with delivery confirmation.
- `read_messages` — check your own inbox. Parameters: `limit` (default 10). Returns pending messages. Primarily used by daemons in their heartbeat cycle, but any agent can call it.
- `heartbeat_ack` — daemon calls this to explicitly signal "nothing to report." No parameters. Returns `{"status": "ok"}`. The daemon loop checks whether this tool was called during the cycle — if yes, suppress output (don't deliver to user). This replaces fragile substring matching on "HEARTBEAT_OK".

### Access control:

**`allowed_peers` enforcement locations:**

- **`send_message` tool** — enforces `allowed_peers` for agent-to-agent messaging. If agent A is not in agent B's `allowed_peers`, the tool returns an error.
- **Orchestrator/cron/webhook routing** — bypasses `allowed_peers` (system-level trust). The system can always route to any agent's inbox.

The `agents` table gets a new `allowed_peers` JSON column (list of agent names this agent can message). Empty list = can't message anyone. Follows OpenClaw's pattern — disabled by default, explicitly enabled per pair.

### Message schema in Redis stream:

```json
{
  "from_agent": "analyst",
  "message": "Found 3 anomalies in today's logs",
  "context": "...",
  "timestamp": "2026-03-19T15:00:00Z",
  "conversation_id": "daemon:analyst"
}
```

Messages are acked after the agent processes them. Unacked messages survive restarts (Redis stream durability).

---

## 3. MCP Client — Agents Consume External Tools

### New `mcp_servers` table:

| Column | Type | Description |
|--------|------|-------------|
| `id` | String (PK) | ULID |
| `name` | String (unique) | Human-readable name, e.g. "notion" |
| `endpoint_url` | String (nullable) | URL for SSE transport, e.g. `http://localhost:3456/sse` |
| `endpoint_command` | String (nullable) | Shell command for stdio transport, e.g. `npx @notion/mcp-server` |
| `transport` | String | `sse` or `stdio` |
| `auth_env_var` | String (nullable) | Env var name holding the secret, e.g. `NOTION_MCP_TOKEN` |
| `active` | Boolean | Enabled/disabled |
| `created_at` | DateTime | |

`endpoint_url` and `endpoint_command` are mutually exclusive — validated on create. SSE transport requires `endpoint_url`, stdio requires `endpoint_command`. This eliminates the command injection risk of storing arbitrary strings in a single `endpoint` column. For stdio, commands are validated against a configurable allowlist (`mcp_stdio_allowed_commands` in settings).

### How it works:

1. **Connection pool** — `src/mcp/client.py` manages connections to external MCP servers. On first use (lazy init), connects to the server, calls `list_tools()` to discover available tools, caches the connection + tool definitions. Auth resolved at connection time via `os.environ.get(auth_env_var)` — no restart needed when adding new secrets.

2. **Agent-to-server assignment** — the `agents` table gets a new `mcp_servers` JSON column (list of server names this agent can access). When an agent executes, `_resolve_tools()` in `runtime.py` is extended to union:
   - Local tools from the tool registry (web_search, execute_code, etc.) — resolved from `agent.tools`
   - Remote tools discovered from assigned MCP servers — resolved from `agent.mcp_servers` via the MCP client pool

   Both are passed as `ToolDefinition` objects to `adapter.complete()`. The LLM sees all tools uniformly.

3. **Remote tool execution** — MCP fallback lives in `default_tool_executor()` (not in `ToolRegistry.execute()`). When `registry.execute()` raises `ValueError` (tool not found locally), the executor catches it and checks the MCP client pool. If found there, dispatches via MCP client with security interceptor check. This is a single fallback point — the registry itself is not modified.

4. **Security** — remote tool calls go through the same security interceptor before execution. All MCP-sourced tools classified as `EXTERNAL_RISK_TOOLS` by default. The `mcp_servers` table can optionally include a `high_risk_tools` JSON list to elevate specific tools to `HIGH_RISK`.

5. **Env var reloading** — auth env vars resolved at connection time, not cached at startup. Adding a new secret to the environment and registering the server via API works without restart.

6. **If agent needs a tool that doesn't exist** — the agent sees only the tools it has access to. If it can't accomplish a task, it responds telling you what it needs. You then connect the MCP server and assign it.

### API endpoints:

- `GET /api/mcp-servers` — list connected servers + discovered tools
- `POST /api/mcp-servers` — register a new server (validates endpoint/transport, checks env var exists)
- `DELETE /api/mcp-servers/{name}` — remove + close connection
- `POST /api/mcp-servers/{name}/refresh` — re-discover tools (if server added new ones)

---

## 4. Daemon Manager Worker

New background worker in `src/main.py` that manages all daemon lifecycles.

### Startup flow:

1. Load all agents where `execution_mode = 'daemon'` and `is_active = True`
2. Spawn an asyncio task per daemon running the daemon loop
3. Create a dedicated Redis connection for pub/sub (separate from the shared pool — Redis requires a dedicated connection for SUBSCRIBE operations)
4. Subscribe to Redis pub/sub channel `stourio:daemon:events` for runtime control

### Daemon loop (per daemon):

```
while not stopping:
    1. Wait for event OR tick_seconds timeout (whichever first)
       - Event: Redis pub/sub notification that inbox has new message
       - Timeout: tick_seconds elapsed (heartbeat)

    2. Check active_hours — if outside window, skip and sleep until window opens

    3. Read inbox messages (up to max_messages_per_cycle)

    4. Build objective:
       - If inbox has messages: "You have {n} new messages:\n{messages}\n\n{heartbeat_prompt}"
       - If tick (no messages): heartbeat_prompt only

    5. Create async session, execute one agent cycle:
       - async with async_session() as session:
           await AgentPool.execute(agent_type=name, objective=..., session=session,
                                   conversation_id=f"daemon:{agent_name}")
       - Goes through concurrency pool (preserves per-agent semaphore limits)
       - Same runtime as oneshot: LLM loop, tools, security, audit
       - Fencing token acquired per cycle (not held across cycles)

    6. Check response:
       - If agent called `heartbeat_ack` tool during cycle → suppress, audit as DAEMON_HEARTBEAT
       - Otherwise → deliver response (Telegram, audit)

    7. Ack processed inbox messages

    8. Record last_heartbeat timestamp for health monitoring
```

### Graceful shutdown:

On stop request (API or app shutdown), the daemon loop sets a `stopping` flag and finishes the current cycle before exiting. No hard cancellation mid-execution. This prevents abandoned tool calls (browser actions, API requests).

### Health monitoring:

Each daemon records `last_heartbeat` timestamp after every cycle. The daemon manager checks: if `now - last_heartbeat > tick_seconds * 3`, the daemon task has likely crashed. The manager logs an alert, restarts the daemon task, and sends a Telegram notification.

### Runtime control via API:

- `POST /api/daemons/{name}/start` — start a daemon (spawn its task)
- `POST /api/daemons/{name}/stop` — stop a daemon (graceful, finishes current cycle)
- `POST /api/daemons/{name}/restart` — stop + start

These publish events to Redis pub/sub so the daemon manager picks them up without app restart.

### Monitoring:

`GET /api/status` extended to include daemon status — which daemons are running, last heartbeat time, messages processed count, current state (active/sleeping/outside_hours).

### Resource management:

No hard token budgets. The existing cost tracking system monitors spend per agent. Telegram alert if a daemon exceeds a configurable threshold. User kills manually via API or admin panel if needed.

---

## 5. Changes to Existing Components

### Prerequisite: `default_tool_executor` in `src/agents/runtime.py`:
- Add `agent_name` parameter, thread it from the step loop where `agent.name` is available
- Add MCP client fallback in the `except ValueError` branch (tool not found locally → check MCP client pool)

### `_resolve_tools()` in `src/agents/runtime.py`:
- Extended to query MCP client pool for tool definitions from servers listed in `agent.mcp_servers`
- Returns union of local tools + remote MCP tools as `ToolDefinition` objects
- Both passed to `adapter.complete()` so the LLM sees all available tools

### Tool registry (`src/mcp/registry.py`):
- **No changes.** MCP fallback lives in `default_tool_executor`, not in the registry. `get_strict()` continues to raise `ValueError` for unknown tools.

### Security interceptor (`src/security/interceptor.py`):
- MCP-sourced tools classified as `EXTERNAL_RISK_TOOLS` by default
- `check_tool_call()` accepts optional `is_mcp_tool: bool` flag to apply MCP-specific classification

### Orchestrator (`src/orchestrator/core.py`):
- When routing to a daemon agent (`execution_mode == 'daemon'`), drop message in agent's inbox via `inbox.enqueue()` instead of calling `AgentPool.execute()`. The daemon picks it up on next cycle or wakes immediately via pub/sub.
- When routing to a daemon agent that is not running, fall back to `AgentPool.execute()` (oneshot execution with the message).

### Config (`src/config.py`):
- `daemon_manager_enabled: bool = True`
- `daemon_default_tick_seconds: int = 300`
- `mcp_client_timeout: int = 30`
- `mcp_stdio_allowed_commands: list[str] = []` — allowlist for stdio MCP server commands

### Database:
- `agents` table: add `execution_mode` (default "oneshot"), `daemon_config` (JSON, nullable), `mcp_servers` (JSON list, nullable), `allowed_peers` (JSON list, nullable)
- New table: `mcp_servers` (with split `endpoint_url`/`endpoint_command` columns)
- Migration: `003_add_daemons_and_mcp.py`

### Redis (`src/persistence/redis_store.py`):
- Add inbox stream operations: `enqueue_inbox()`, `dequeue_inbox()`, `ack_inbox()`
- Add pub/sub helper: `publish_daemon_event()`, `get_pubsub_connection()` (dedicated connection for SUBSCRIBE)
- Consumer group per agent inbox: `stourio:inbox_group:{agent_name}`

### Main lifespan (`src/main.py`):
- Daemon manager worker started after scheduler
- MCP client pool initialized on startup (connects to active servers)
- Both cleaned up on shutdown (daemon manager graceful stop, MCP connections closed)

---

## 6. New File Structure

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `src/daemons/__init__.py` | Package marker |
| Create | `src/daemons/manager.py` | Daemon manager — spawns/stops daemon tasks, health monitoring, control events |
| Create | `src/daemons/loop.py` | Single daemon loop — event wait, heartbeat, inbox read, agent cycle, graceful stop |
| Create | `src/daemons/inbox.py` | Inbox operations — enqueue, dequeue, ack, pub/sub notification, message size validation |
| Create | `src/mcp/client.py` | MCP client pool — connect, discover tools, execute remote calls, env var resolution |
| Create | `src/mcp/tools/messaging.py` | `send_message`, `read_messages`, and `heartbeat_ack` tools |
| Create | `tests/test_daemon_loop.py` | Daemon loop tests |
| Create | `tests/test_inbox.py` | Inbox messaging tests |
| Create | `tests/test_mcp_client.py` | MCP client tests |
| Create | `migrations/versions/003_add_daemons_and_mcp.py` | DB migration |
| Modify | `src/persistence/database.py` | Add `McpServerRecord`, extend `AgentModel` |
| Modify | `src/persistence/redis_store.py` | Add inbox stream ops + dedicated pub/sub connection |
| Modify | `src/agents/runtime.py` | Thread `agent_name`, extend `_resolve_tools()`, MCP fallback in executor |
| Modify | `src/mcp/tools/__init__.py` | Register `send_message`, `read_messages`, `heartbeat_ack` |
| Modify | `src/security/interceptor.py` | Handle MCP-sourced tools classification |
| Modify | `src/orchestrator/core.py` | Route to daemon inbox |
| Modify | `src/api/routes.py` | MCP server CRUD + daemon control endpoints |
| Modify | `src/config.py` | Daemon + MCP client settings |
| Modify | `src/main.py` | Start daemon manager + MCP client pool |

### Untouched:
Scheduler, browser automation, cron jobs, approval workflow, kill switch, audit trail, Telegram integration, RAG pipeline. All existing features work as-is.

---

## 7. Implementation Phases

All phases are strictly sequential (1 → 2 → 3 → 4) to avoid merge conflicts on shared files.

**Phase 0: Prerequisite Fix** — Thread `agent_name` through `default_tool_executor()`. Small, isolated change.

**Phase 1: Inbox + Messaging** — Redis inbox streams, pub/sub helpers, dedicated pub/sub connection, `send_message`/`read_messages`/`heartbeat_ack` tools, `allowed_peers` access control, message size validation. Foundation for everything else.

**Phase 2: Daemon Manager** — daemon loop with graceful shutdown, heartbeat system, active_hours, pub/sub event waking, health monitoring with auto-restart, persistent conversation_id per daemon, runtime control API, status endpoint extension. Depends on Phase 1 (daemons read from inbox).

**Phase 3: MCP Client** — connection pool with lazy init, tool discovery, `_resolve_tools()` extension, remote execution via `default_tool_executor` fallback, MCP server CRUD API, split endpoint columns, stdio command allowlist, security interceptor integration. Depends on Phase 0 (agent_name threading).

**Phase 4: Orchestrator Integration** — modify routing to use inbox for daemon agents, fallback to oneshot for non-running daemons, update admin panel views.
