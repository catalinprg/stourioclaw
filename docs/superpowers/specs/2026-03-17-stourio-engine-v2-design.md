# Stourio Engine v2 — Design Spec

**Date**: 2026-03-17
**Status**: Approved
**Scope**: 9 subsystems — plugin tools, notifications, RAG pipeline, agent memory, LLM caching, cost tracking, test suite, multi-agent chaining, agent concurrency & specialization

---

## 1. Plugin-Based Tool System

### Problem
Tools are hardcoded as stub functions inside `agents/runtime.py`. Adding or modifying tools requires source code changes.

### Design
Dual-mode tool system: Python plugins for complex logic, YAML definitions for HTTP-based tools.

```
src/
  plugins/
    registry.py          # Tool registry — discovers & manages tools
    base.py              # BaseTool interface
    loader.py            # Auto-discovers .py plugins + parses YAML configs
  tools/
    yaml/                # YAML tool definitions
    python/              # Python plugin files
```

### Python Plugin Interface
```python
class BaseTool(ABC):
    name: str
    description: str
    parameters: dict          # JSON Schema

    async def execute(self, arguments: dict) -> dict
    async def validate(self, arguments: dict) -> bool
    async def health_check(self) -> bool
```

### YAML Tool Definition
```yaml
name: get_system_metrics
description: Query Prometheus for system metrics
parameters:
  component: {type: string, required: true}
  metric: {type: string, required: true}
endpoint:
  url: "${PROMETHEUS_URL}/api/v1/query"
  method: POST
  headers:
    Authorization: "Bearer ${PROMETHEUS_TOKEN}"
  body_template: |
    {"query": "{{metric}}{component=\"{{component}}\"}"}
response:
  extract: "data.result[0].value[1]"
execution:
  mode: gateway    # local | gateway | sandboxed
```

### Execution Modes (Per-Tool)
```yaml
execution:
  mode: local      # Runs in-process (read-only tools, Python plugins)
  # OR
  mode: gateway    # Routes through MCP gateway (destructive actions)
  # OR
  mode: sandboxed  # Future: container-isolated execution
```

Default policy: read-only tools run local, side-effect tools route through gateway.

### Agent Integration & Security Whitelist Migration
Agent templates reference tools by name. The plugin registry becomes the **single source of truth** for valid tools, replacing the static `_VALID_TOOL_NAMES` set built from `AGENT_TEMPLATES` at import time.

Migration path:
1. Agent templates define which tool names they are allowed to use (as before)
2. At execution time, the registry resolves each tool name to its implementation
3. Validation is two-stage: (a) tool name exists in the agent template's allowed list, (b) tool name exists in the plugin registry
4. If a tool is in the template but not in the registry, the agent receives a structured error: `"Tool '{name}' is declared but not registered. Check plugin configuration."`

The existing `default_tool_executor` in `runtime.py` is replaced by a `PluginToolExecutor` that calls `registry.execute(tool_name, arguments)`. The registry dispatches based on execution mode:
- `local`: calls `tool.execute(arguments)` in-process
- `gateway`: sends `POST {MCP_SERVER_URL}/execute` (existing behavior)

### MCP Gateway — Dynamic Tool Registration
For `mode: gateway` tools, the core engine sends tool definitions to the gateway at startup via a new endpoint:

```
POST /tools/register
Authorization: Bearer {MCP_SHARED_SECRET}
Body: {name, description, parameters, handler_type}
```

The gateway's static `TOOL_REGISTRY` becomes a mutable dict. Registered tools with `handler_type: "proxy"` are dispatched to external endpoints configured in the YAML definition. The existing `@register_tool`-decorated functions remain as built-in gateway tools (runbook reader, health check).

On core engine startup: load all `mode: gateway` tools from YAML → POST each to `/tools/register`. Gateway validates and adds to its registry.

---

## 2. Notification Framework

### Design
Platform-agnostic notification system. Generic webhook as default transport, native adapters for platforms needing richer interaction.

```
src/
  notifications/
    dispatcher.py        # Routes notifications to configured channels
    base.py              # BaseNotifier interface
    webhook.py           # Generic webhook notifier (default)
    adapters/
      slack.py           # Native: threads, blocks, reactions
      pagerduty.py       # Native: incident creation with severity
      email.py           # SMTP/SendGrid
```

### BaseNotifier Interface
```python
class BaseNotifier(ABC):
    name: str
    supports_threads: bool = False
    supports_severity: bool = False

    async def send(self, notification: Notification) -> NotificationResult
    async def health_check(self) -> bool
```

### Notification Model
```python
class Notification(BaseModel):
    channel: str              # "slack", "pagerduty", "webhook:oncall-team"
    message: str
    severity: str = "info"    # info, warning, critical
    context: dict = {}        # execution_id, agent, conversation_id
    thread_id: str | None     # For threaded replies
```

### Channel Configuration
```yaml
notification_channels:
  oncall-slack:
    type: slack
    webhook_url: "${SLACK_WEBHOOK_URL}"
    default_channel: "#ops-alerts"
  oncall-pagerduty:
    type: pagerduty
    api_key: "${PAGERDUTY_API_KEY}"
    service_id: "P123ABC"
  generic-webhook:
    type: webhook
    url: "https://your-endpoint.com/notify"
    headers:
      Authorization: "Bearer ${WEBHOOK_TOKEN}"
```

### Approval Lifecycle Notifications
```python
class ApprovalEvent(str, Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ESCALATED = "escalated"       # No response after X% of TTL
```

Configuration:
```yaml
approval_notifications:
  on_requested:
    channel: "oncall-slack"
    severity: "warning"
    template: "Action requires approval: {{action}} (risk: {{risk_level}}). Approve/reject within {{ttl_remaining}}s.\n{{approval_url}}"
  on_expired:
    channel: "oncall-slack"
    severity: "critical"
    template: "Approval EXPIRED for: {{action}}. Auto-rejected after {{ttl}}s with no response."
  on_escalated:
    after_percent: 50
    channel: "oncall-pagerduty"
    severity: "critical"
    template: "Approval stalling: {{action}}. {{ttl_remaining}}s left."
```

### Approval Notification Integration
The notification dispatcher is injected into `guardrails/approvals.py` as a dependency. Approval functions emit notifications at lifecycle points:
- `create_approval()` → fires `REQUESTED` notification
- `resolve_approval()` → fires `APPROVED` or `REJECTED` notification
- Expiration check → fires `EXPIRED` notification

### Escalation Timer Mechanism
Escalation uses a **background asyncio task** started at application boot (in `main.py` alongside the existing signal consumer task). The escalation worker:
1. Polls Redis every 10 seconds for keys matching `stourio:approval_escalation:*`
2. Each key stores `{approval_id, escalation_time, channel, notified}`
3. When `escalation_time` is reached and `notified` is false, fires the escalation notification and sets `notified = true`
4. Keys auto-expire via Redis TTL (set to match the approval TTL)

This avoids Redis keyspace notifications (which require special config) and complex scheduling. The 10-second poll interval is acceptable since escalation is not time-critical to the second.

---

## 3. RAG Pipeline (pgvector + Embeddings + Re-ranker)

### Problem
Runbook retrieval is exact filename lookup. Breaks on fuzzy intent, partial names, multi-service runbooks, and scaling beyond a handful of files.

### Design
Semantic retrieval with pluggable embeddings and re-ranker.

```
src/
  rag/
    embeddings/
      base.py              # BaseEmbedder interface
      openai_embedder.py   # Default: text-embedding-3-small
      voyage_embedder.py   # Optional: voyage-3
    reranker/
      base.py              # BaseReranker interface
      cohere_reranker.py   # Default: rerank-v3.5
      llm_reranker.py      # Uses existing LLM providers
      local_reranker.py    # cross-encoder/ms-marco-MiniLM
    chunker.py             # Document chunking with metadata preservation
    ingestion.py           # Ingest runbooks/docs into pgvector
    retriever.py           # Search + rerank pipeline
```

### Database Schema
The embedding column uses a **configurable dimension** stored in application config. The table is created with the dimension matching the active embedder.

```sql
CREATE EXTENSION vector;

CREATE TABLE document_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type VARCHAR(50) NOT NULL,    -- 'runbook', 'agent_memory', 'incident'
    source_path VARCHAR(500),
    title VARCHAR(500),
    section_header VARCHAR(500),
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',         -- includes 'embedding_model' for tracking
    embedding vector({dimension}),       -- dimension from config: 1536 (OpenAI), 1024 (Voyage), etc.
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_chunks_embedding ON document_chunks
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_chunks_source ON document_chunks (source_type);
CREATE INDEX idx_chunks_metadata ON document_chunks USING gin (metadata);
```

**Dimension management**:
- `EMBEDDING_DIMENSION` config value must match the active embedder's output dimension
- Each chunk's `metadata` stores `embedding_model` for provenance tracking
- **Switching embedders requires re-ingestion**: drop all chunks, update dimension config, recreate table column via migration, re-embed all documents. A `POST /api/documents/reindex` endpoint triggers this process.
- Startup validation: the configured dimension is checked against the embedder's declared `dimension` property. Mismatch → fatal error with clear message.

### Chunking Strategy
- Split markdown by headers (h1, h2, h3) — preserves logical sections
- Sections exceeding 512 tokens split by paragraphs with 50-token overlap
- Each chunk carries: `source_path`, `section_header`, `service_tags` (from frontmatter or directory)

### Retrieval Pipeline
```
Query → Embed → pgvector ANN (top-20, cosine) → Metadata filter → Rerank (top-3) → Return
```

### Ingestion Modes
1. **Startup scan**: Hash each runbook file, ingest only if changed (content hash stored in metadata)
2. **API trigger**: `POST /api/documents/ingest` for manual re-ingestion

### Pluggable Interfaces
```python
class BaseEmbedder(ABC):
    dimension: int
    model_name: str
    async def embed(self, texts: list[str]) -> list[list[float]]

class BaseReranker(ABC):
    async def rerank(self, query: str, documents: list[str], top_k: int = 3) -> list[RankedDocument]
```

### Tool Replacement
`read_internal_runbook(service_name)` replaced by `search_knowledge(query: str, source_type: str = None)`.

**Agent template update**: The `diagnose_repair` template in `runtime.py` currently lists `read_internal_runbook` in its tools array (line 52-63). This tool definition is removed and replaced with:
```python
{
    "name": "search_knowledge",
    "description": "Search internal documentation, runbooks, and past agent experiences",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural language search query"},
            "source_type": {
                "type": "string",
                "enum": ["runbook", "agent_memory", "incident"],
                "description": "Optional: filter by source type"
            }
        },
        "required": ["query"]
    }
}
```

The `search_knowledge` tool is implemented as a Python plugin in `tools/python/knowledge_search.py` that calls the RAG retriever. Execution mode: `local` (read-only, in-process).

The MCP gateway's `tool_read_internal_runbook()` function is retained as a fallback for direct file access but is no longer the primary retrieval path.

---

## 4. Agent Session Memory

### Design
Two-layer memory system built on the RAG pipeline.

### Layer 1: Conversation History
Load recent messages from `conversation_messages` table for the active `conversation_id`. Prepend to agent's message history before the tool loop starts. Data already exists — just needs loading.

**Signature change**: `execute_agent()` in `runtime.py` gains a new parameter `conversation_id: str | None = None`. When provided, the function queries `conversation_messages` for the last N messages (configurable, default 20) and prepends them as context.

**Orchestrator threading**: The `process()` method in `orchestrator/core.py` already receives `signal: OrchestratorInput` which contains `conversation_id` (set by the API route at `routes.py` line 61). Currently, `execute_agent()` is called at line 327 without passing it. The change:

```python
# core.py — in the agent execution block (approx line 327)
result = await execute_agent(
    agent_type=agent_type,
    objective=objective,
    context=context,
    input_id=signal.id,
    conversation_id=signal.conversation_id,   # <-- add this
    tool_executor=tool_executor,
)
```

This applies to all `execute_agent()` call sites in `core.py`: the LLM-routed path, the `FORCE_AGENT` rule path, and any chain-dispatched agent calls.

### Layer 2: Semantic Memory
After each agent execution completes, persist a memory entry.

**Extracting memory fields**: The agent's `result` string is structured by an LLM summarization call post-execution. A lightweight prompt asks the agent's LLM to extract from the conversation:
```
Given this agent execution transcript, extract:
- trigger_summary: one-line description of what triggered this
- conclusion: the final outcome or recommendation
- services_involved: list of service/component names mentioned
- resolution_status: one of "resolved", "escalated", "failed"
```

This adds one cheap LLM call per execution (using `gpt-4o-mini` or equivalent small model). The extracted fields populate:

```python
class AgentMemoryEntry(BaseModel):
    conversation_id: str
    agent_template: str
    trigger_summary: str
    actions_taken: list[str]       # Derived from steps[].tool_name
    conclusion: str
    services_involved: list[str]
    resolution_status: str         # "resolved", "escalated", "failed"
    timestamp: datetime
```

Entry is chunked, embedded, stored in `document_chunks` with `source_type='agent_memory'`.

### Recall at Execution Time
Before agent starts, retriever searches agent_memory chunks with the incoming signal:
```
Signal → Search agent_memory → Rerank → Top-3 injected as "Relevant past experience:" in system prompt
```

### Retention Policy
Configurable TTL on agent memory entries. Default 90 days. Pruned on schedule via background task.

---

## 5. LLM Response Caching

### Design
Redis-based deterministic cache at the adapter layer.

### Cache Key
```python
def build_cache_key(
    provider: str, model: str, system_prompt: str,
    messages: list[ChatMessage], tools: list[ToolDefinition] | None
) -> str:
    payload = {
        "provider": provider, "model": model,
        "system_prompt": system_prompt,
        "messages": [m.model_dump() for m in messages],
        "tools": [t.model_dump() for t in sorted(tools, key=lambda t: t.name)] if tools else None,
    }
    content_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return f"stourio:llm_cache:{content_hash}"
```

### Cache Wrapper
The wrapper matches `BaseLLMAdapter.complete()` signature exactly: `(system_prompt: str, messages: list[ChatMessage], tools: list[ToolDefinition] | None = None, temperature: float = 0.1)`.

```python
class CachedLLMAdapter:
    """Decorator around any LLM adapter. Transparent to callers."""

    def __init__(self, adapter: BaseLLMAdapter, redis: Redis, ttl: int = 300):
        self.adapter = adapter
        self.redis = redis
        self.ttl = ttl

    async def complete(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.1,
    ):
        if self.ttl <= 0:
            return await self.adapter.complete(system_prompt, messages, tools, temperature)
        key = build_cache_key(
            self.adapter.provider_name, self.adapter.model,
            system_prompt, messages, tools
        )
        cached = await self.redis.get(key)
        if cached:
            return LLMResponse.model_validate_json(cached)
        result = await self.adapter.complete(system_prompt, messages, tools, temperature)
        await self.redis.setex(key, self.ttl, result.model_dump_json())
        return result
```

**Cache key includes `system_prompt`** to prevent cross-contamination between orchestrator routing prompts and agent execution prompts. `temperature` is excluded from the key (same prompt at different temps is rare and not worth the cache fragmentation).

### Caching Policy
| Context | Cached | TTL |
|---------|--------|-----|
| Orchestrator routing | Yes | 5 min |
| Agent tool-calling steps | No | — |
| Direct informational responses | Yes | 5 min |

### Configuration
```yaml
cache_config:
  orchestrator_ttl: 300
  agent_ttl: 0
  enabled: true
```

Kill switch activation flushes `stourio:llm_cache:*`.

---

## 6. Token/Cost Tracking

### Database Schema
```sql
CREATE TABLE token_usage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_id VARCHAR(100),
    conversation_id VARCHAR(100),
    agent_template VARCHAR(100),
    provider VARCHAR(50) NOT NULL,
    model VARCHAR(100) NOT NULL,
    input_tokens INT NOT NULL,
    output_tokens INT NOT NULL,
    total_tokens INT NOT NULL,
    estimated_cost_usd NUMERIC(10, 6),
    call_type VARCHAR(20),           -- 'orchestrator', 'agent', 'embedding', 'rerank'
    cached_hit BOOLEAN DEFAULT FALSE,
    units_used INT DEFAULT 0,        -- For non-token APIs (reranker search units)
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_usage_conversation ON token_usage (conversation_id);
CREATE INDEX idx_usage_agent ON token_usage (agent_template);
CREATE INDEX idx_usage_created ON token_usage (created_at);
```

### LLM Response Model
```python
class LLMResponse(BaseModel):
    content: str | None
    tool_calls: list | None
    usage: TokenUsage              # input_tokens, output_tokens
    model: str
    provider: str
```

### Reranker Cost Tracking
Cohere reranker returns search units, not tokens. For `call_type='rerank'`:
- `input_tokens` = number of documents scored
- `output_tokens` = 0
- `units_used` = Cohere search units consumed
- `estimated_cost_usd` = `units_used * price_per_search_unit`

The `units_used` column handles non-token-based APIs generically.

### Cost Calculation
Configurable pricing per model in `src/tracking/pricing.py`. Overridable via config file.

### API Endpoints
```
GET /api/usage?from=2026-03-01&to=2026-03-17
GET /api/usage/summary?group_by=agent_template
```

### Admin Panel — Cost Dashboard Tab
- Total cost, tokens, calls (24h / 7d / 30d toggle)
- Cost by provider (bar chart)
- Cost by agent (table)
- Cache hit rate and estimated savings
- Recent calls table (time, agent, model, tokens, cost)
- Chart.js via CDN — no build step

### Budget Alerts
```yaml
cost_alerts:
  daily_threshold_usd: 50.00
  channel: "oncall-slack"
  severity: "warning"
```

Uses notification framework for delivery.

---

## 7. Test Suite

### Structure
```
tests/
  conftest.py                    # Fixtures: async DB, Redis mock, LLM mock
  test_rules_engine.py
  test_orchestrator.py
  test_agent_runtime.py
  test_approvals.py
  test_plugin_registry.py
  test_notification_dispatcher.py
  test_rag_pipeline.py
  test_cache.py
  test_cost_tracking.py
  test_api_routes.py
```

### Strategy
- **Unit tests**: Rules engine, chunker, cost calculator, plugin loader — no I/O
- **Integration tests**: Orchestrator, agent runtime, RAG pipeline — real Postgres, mocked LLM
- **LLM mocking**: Deterministic fixture returning predetermined tool calls and responses

### Key Scenarios
- Rules: regex match, keyword match, priority ordering, no-match passthrough
- Orchestrator: rule → agent, rule → approval, no rule → LLM routing, kill switch blocks
- Agent: tool loop, max steps, fencing token expiry, provider failover
- Approvals: create → approve, create → TTL expire, notification on each event
- RAG: chunk markdown, retrieve relevant chunks, reranker reorders

### Run Command
```bash
pytest tests/ -v --asyncio-mode=auto
```

---

## 8. Multi-Agent Chaining

### Design
Sequential pipelines (default) with optional DAG support for parallel execution.

```
src/
  orchestrator/
    chains.py            # Pipeline/DAG definitions and execution
    chain_registry.py    # Registered chain templates
```

### Models
```python
class AgentStep(BaseModel):
    agent_template: str
    input_mapping: dict[str, str] = {}
    condition: str | None = None

class Pipeline(BaseModel):
    name: str
    description: str
    steps: list[AgentStep]

class DAG(BaseModel):
    name: str
    description: str
    nodes: dict[str, AgentStep]
    edges: list[tuple[str, str]]
```

### Expression Language for Conditions and Input Mapping
Conditions and input mappings use **Jinja2 expressions** evaluated against a `ChainContext` dict:

```python
class ChainContext:
    """Accumulated state across chain execution."""
    steps: dict[str, StepResult]     # step_index or node_id → result
    previous: StepResult | None      # Last completed step (pipelines only)
    original_input: dict             # The signal that triggered the chain

class StepResult(BaseModel):
    conclusion: str
    resolution_status: str
    raw_output: str
    agent_template: str
```

**Condition evaluation**: Jinja2 expression returning truthy/falsy:
```yaml
condition: "{{ previous.resolution_status == 'escalated' }}"
```

**Input mapping**: Jinja2 templates resolving to strings:
```yaml
input_mapping:
  diagnosis: "{{ previous.conclusion }}"
  db_findings: "{{ steps['check_db'].conclusion }}"
```

**Error handling**: If a referenced step/field doesn't exist, the expression returns empty string for mappings and `false` for conditions (step is skipped). Errors logged to audit trail.

### Pipeline Example — Incident Response
```yaml
chains:
  incident_response:
    type: pipeline
    steps:
      - agent: diagnose_repair
        input_mapping: {}
      - agent: escalate
        input_mapping:
          diagnosis: "{{ previous.conclusion }}"
        condition: "{{ previous.resolution_status == 'escalated' }}"
      - agent: take_action
        input_mapping:
          action_plan: "{{ steps['0'].conclusion }}"
        condition: "{{ steps['0'].resolution_status == 'resolved' }}"
```

### DAG Example — Parallel Investigation
```yaml
chains:
  multi_service_diagnosis:
    type: dag
    nodes:
      check_db:
        agent: diagnose_repair
        input_mapping: { focus: "database" }
      check_cache:
        agent: diagnose_repair
        input_mapping: { focus: "cache" }
      synthesize:
        agent: take_action
        input_mapping:
          db_findings: "{{ steps['check_db'].conclusion }}"
          cache_findings: "{{ steps['check_cache'].conclusion }}"
    edges:
      - [check_db, synthesize]
      - [check_cache, synthesize]
```

### Execution
- **Pipeline**: Sequential, output forwarding, condition evaluation, skip if not met
- **DAG**: Topological sort, `asyncio.gather()` for independent nodes, wait for dependencies
- Each node: full agent execution with own fencing token and kill switch checks
- Chain-level fencing token wraps entire execution

### Orchestrator Integration

**New routing tool definition**:
```python
{
    "type": "function",
    "function": {
        "name": "route_to_chain",
        "description": "Route to a multi-agent chain for complex workflows requiring multiple agents",
        "parameters": {
            "type": "object",
            "properties": {
                "chain_name": {"type": "string", "description": "Name of the chain to execute"},
                "context": {"type": "string", "description": "Additional context for the chain"}
            },
            "required": ["chain_name"]
        }
    }
}
```

Added to `ROUTING_TOOLS` list in `orchestrator/core.py` (5th tool alongside `route_to_agent`, `route_to_automation`, `respond_directly`, `request_more_info`).

**Rules engine**: New `FORCE_CHAIN` value added to the `RuleAction` enum in `models/schemas.py`.

**Fix required for existing FORCE_AGENT**: The current `FORCE_AGENT` handler in `core.py` (line 210-212) is a no-op `pass` that falls through to LLM routing — it doesn't actually force the agent. Both `FORCE_AGENT` and `FORCE_CHAIN` need direct dispatch logic that **bypasses LLM routing entirely**.

New handler block in `orchestrator/core.py`, replacing lines 210-212:
```python
if matched_rule.action == RuleAction.FORCE_AGENT:
    # Direct dispatch — bypass LLM routing
    agent_type = matched_rule.config.get("agent_type", "diagnose_repair")
    result = await execute_agent(
        agent_type=agent_type,
        objective=signal.content,
        context={"rule_id": matched_rule.id, "source": "forced"},
        input_id=signal.id,
        conversation_id=signal.conversation_id,
    )
    return {"status": "ok", "message": result.result, "execution_id": result.id, "type": "agent"}

if matched_rule.action == RuleAction.FORCE_CHAIN:
    chain_name = matched_rule.config.get("chain_name")
    result = await execute_chain(
        chain_name=chain_name,
        context={"signal": signal.content, "rule_id": matched_rule.id},
        input_id=signal.id,
        conversation_id=signal.conversation_id,
    )
    return {"status": "ok", "message": result.summary, "execution_id": result.id, "type": "chain"}
```

Rules with `FORCE_AGENT` or `FORCE_CHAIN` action must include a `config` dict with `agent_type` or `chain_name` respectively.

### Audit
Parent `execution_id` for chain, child IDs per step. All linked in audit trail.

---

## 9. Agent Concurrency & Specialization

### Problem
The engine has 3 agent templates (`diagnose_repair`, `escalate`, `take_action`). If multiple incidents arrive simultaneously requiring the same agent type, they execute sequentially — incident #2 blocks behind #1. The fencing token mechanism amplifies this by acquiring a lock per execution.

### Design
Two complementary layers: concurrent instance pooling (infrastructure) and domain-specific agent templates (configuration).

### Layer 1: Agent Instance Pooling

Agent templates are blueprints, not singletons. Multiple concurrent instances of the same template can run in parallel, each with its own execution context, fencing token, and message history.

**Concurrency control** — `asyncio.Semaphore` per agent type:

```python
# orchestrator/concurrency.py
class AgentPool:
    """Manages concurrent agent instances per template type."""

    def __init__(self, config: dict[str, int]):
        # config: {"diagnose_repair": 5, "escalate": 3, ...}
        self._semaphores: dict[str, asyncio.Semaphore] = {
            agent_type: asyncio.Semaphore(limit)
            for agent_type, limit in config.items()
        }
        self._default_limit: int = 3
        self._queued: dict[str, int] = defaultdict(int)  # queue depth per type

    async def execute(self, agent_type: str, **kwargs) -> AgentExecution:
        sem = self._semaphores.get(
            agent_type,
            asyncio.Semaphore(self._default_limit)
        )
        if sem.locked():
            self._queued[agent_type] += 1
            await audit.log("AGENT_QUEUED", f"{agent_type} at capacity, queued")
        try:
            async with sem:
                self._queued[agent_type] = max(0, self._queued[agent_type] - 1)
                return await execute_agent(agent_type=agent_type, **kwargs)
        except Exception:
            self._queued[agent_type] = max(0, self._queued[agent_type] - 1)
            raise

    def status(self) -> dict:
        """Returns current utilization per agent type."""
        return {
            agent_type: {
                "capacity": sem._value + (sem._value == 0),
                "active": sem._value == 0,
                "queued": self._queued.get(agent_type, 0),
            }
            for agent_type, sem in self._semaphores.items()
        }
```

**Configuration**:
```yaml
agent_concurrency:
  diagnose_repair: 5
  diagnose_database: 5
  diagnose_cache: 3
  escalate: 3
  take_action: 3
  default: 3
```

**Orchestrator integration**: All `execute_agent()` calls in `core.py` go through `AgentPool.execute()` instead of calling `execute_agent()` directly. The pool is initialized at startup and injected into the orchestrator.

**Status endpoint**: `/api/status` extended to show agent pool utilization (active instances, queue depth per type).

**Backpressure behavior**: When an agent type hits its concurrency limit:
- New signals are queued (asyncio semaphore handles this natively)
- Audit trail records `AGENT_QUEUED` event with queue position
- API response is immediate with `"status": "queued"` — the caller doesn't block
- When a slot opens, the next queued signal proceeds automatically

### Layer 2: Domain-Specific Agent Templates

Domain specialization is a **configuration concern** — create focused templates with domain-specific system prompts, tool sets, and knowledge filters. The plugin-based tool system (Section 1) and RAG pipeline (Section 3) already support this.

**Example — specialized diagnosis agents**:

```yaml
agent_templates:
  diagnose_database:
    system_prompt: |
      You are a database operations specialist. You diagnose PostgreSQL, MySQL,
      and Redis issues. You understand replication lag, connection pooling,
      query performance, vacuum operations, WAL sizing, and failover procedures.
    tools:
      - search_knowledge        # RAG retrieval, auto-filters to DB runbooks
      - get_system_metrics      # Prometheus queries for DB metrics
      - get_recent_logs         # Log retrieval filtered to DB services
      - execute_remediation     # DB-specific remediations
    provider_override: anthropic/claude-3-5-sonnet-latest
    max_steps: 8
    knowledge_filter:
      source_type: runbook
      metadata_filter: {"domain": "database"}  # Pre-filter RAG results

  diagnose_network:
    system_prompt: |
      You are a network operations specialist. You diagnose DNS, load balancer,
      TLS, latency, and connectivity issues.
    tools:
      - search_knowledge
      - get_system_metrics
      - get_recent_logs
      - call_api               # For health check endpoints
    provider_override: openai/gpt-4o
    max_steps: 6
    knowledge_filter:
      source_type: runbook
      metadata_filter: {"domain": "network"}
```

**Benefits of specialization**:
1. **Better tool selection** — agent knows which metrics matter for its domain
2. **Better RAG retrieval** — `knowledge_filter` pre-filters vector search to relevant runbooks
3. **Better semantic memory** — past incidents from the same domain surface more relevantly
4. **Fewer wasted LLM steps** — no time ruling out unrelated domains
5. **Better orchestrator routing** — LLM has clearer, more descriptive agent options to choose from

**Agent template loading**: Templates are loaded from YAML config files at startup (consistent with chain definitions). The existing hardcoded `AGENT_TEMPLATES` dict in `runtime.py` is replaced by config-driven template loading. Built-in templates (`diagnose_repair`, `escalate`, `take_action`) ship as default YAML files that can be overridden or extended.

**Composition with concurrency**:
```
Signal: "Redis replication lag on prod-cache-03"
  → Orchestrator routes to diagnose_cache (domain-specific)
  → AgentPool checks: 2/3 slots used → spawns instance-3
  → Runs concurrently with other diagnose_cache instances

Signal: "Postgres connection pool exhausted"
  → Orchestrator routes to diagnose_database (different type)
  → AgentPool checks: 0/5 slots used → spawns instance-1
  → Runs in parallel with Redis investigation (different semaphore)
```

---

## Cross-Cutting Concerns

### Configuration
All new subsystems configured via environment variables and/or YAML config files, loaded through `pydantic-settings`. Consistent with existing `src/config.py` pattern.

New config fields:
```python
# RAG
embedding_provider: str = "openai"         # openai, voyage
embedding_model: str = "text-embedding-3-small"
embedding_dimension: int = 1536
reranker_provider: str = "cohere"          # cohere, llm, local
cohere_api_key: str = ""
runbooks_dir: str = "/app/docs"

# Notifications
notification_config_path: str = "config/notifications.yaml"

# Caching
cache_enabled: bool = True
cache_orchestrator_ttl: int = 300
cache_agent_ttl: int = 0

# Cost tracking
cost_alert_daily_threshold: float = 0.0    # 0 = disabled
cost_alert_channel: str = ""

# Memory
agent_memory_ttl_days: int = 90
agent_memory_recall_count: int = 3
conversation_history_limit: int = 20

# Plugins
tools_yaml_dir: str = "tools/yaml"
tools_python_dir: str = "tools/python"

# Chains
chains_config_path: str = "config/chains.yaml"

# Agent Concurrency & Templates
agent_templates_dir: str = "config/agents"         # YAML template definitions
agent_concurrency_default: int = 3
agent_concurrency_config: dict = {}                 # {"diagnose_database": 5, ...}
```

### New Dependencies
- `pgvector` — Postgres extension (Docker image change)
- `cohere` — Re-ranker SDK
- `voyageai` — Optional embeddings
- `jinja2` — Template engine for chain expressions and notification templates
- `Chart.js` — CDN include for admin panel (no npm)
- `pytest`, `pytest-asyncio` — Test framework

### Database Schema Management
The current codebase uses `Base.metadata.create_all()` in `database.py` for table creation. This approach is maintained for v2 — no Alembic introduction at this stage to avoid unnecessary infrastructure change.

Changes to `database.py`:
1. Add `document_chunks` and `token_usage` SQLAlchemy models
2. pgvector extension must exist before `create_all()` (the `vector` column type requires it)
3. Modified `init_db()`:

```python
async def init_db():
    """Create pgvector extension and all tables."""
    async with engine.begin() as conn:
        # pgvector extension must exist before create_all() — vector column type requires it
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created (pgvector enabled)")
```

Requires adding `from sqlalchemy import text` to imports.

**Note**: If the project grows to need migration management, Alembic can be introduced later. For now, `create_all()` is idempotent and sufficient.

### Docker Compose Changes
- Postgres image: switch to `pgvector/pgvector:pg16` (pgvector pre-installed)
- No new services required
