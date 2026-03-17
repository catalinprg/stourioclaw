# Stourio Engine v2 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform Stourio from a stub-based orchestrator into a fully pluggable, production-ready autonomous operations engine with RAG retrieval, notifications, cost tracking, caching, multi-agent chaining, and concurrent agent execution.

**Architecture:** 9 subsystems built in dependency order. Each phase produces working, testable software. Foundation layers (plugin system, config, DB schema) first, then subsystems that build on them (RAG, notifications, caching, tracking), then orchestration features (chains, concurrency), then tests that validate the whole stack.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy (async), pgvector, Redis, Pydantic, Jinja2, Cohere SDK, OpenAI SDK, pytest + pytest-asyncio, Chart.js (CDN)

**Spec:** `docs/superpowers/specs/2026-03-17-stourio-engine-v2-design.md`

---

## Phase Dependency Order

```
Phase 1: Config & DB Foundation (all phases depend on this)
Phase 2: Plugin-Based Tool System (phases 3,8,9 depend on this)
Phase 3: RAG Pipeline (phases 4 depend on this)
Phase 4: Agent Session Memory (depends on 3)
Phase 5: Notification Framework (depends on 1)
Phase 6: LLM Response Caching (depends on 1)
Phase 7: Token/Cost Tracking (depends on 1,5)
Phase 8: Agent Concurrency & Specialization (depends on 2) — MUST come before chaining
Phase 9: Multi-Agent Chaining (depends on 2,8)
Phase 10: Admin Panel Cost Dashboard (depends on 7)
Phase 11: Integration Tests (depends on all)
```

---

## Phase 1: Config & DB Foundation

### Task 1.1: Update Dependencies

**Files:**
- Modify: `stourio-core-engine/requirements.txt`
- Modify: `stourio-core-engine/docker-compose.yml`

- [ ] **Step 1: Add new dependencies to requirements.txt**

Append to `stourio-core-engine/requirements.txt`:
```
cohere==5.13.0
voyageai==0.3.2
jinja2==3.1.4
pgvector==0.3.6
pytest==8.3.4
pytest-asyncio==0.24.0
pyyaml==6.0.2
```

- [ ] **Step 2: Switch Postgres image to pgvector**

In `stourio-core-engine/docker-compose.yml`, change the postgres service image:
```yaml
# FROM:
image: postgres:16-alpine
# TO:
image: pgvector/pgvector:pg16
```

- [ ] **Step 3: Commit**

```bash
cd /Users/catalinstour/Documents/Intelligence/stourio-engine
git add stourio-core-engine/requirements.txt stourio-core-engine/docker-compose.yml
git commit -m "deps: add pgvector, cohere, jinja2, pytest, pyyaml"
```

---

### Task 1.2: Extend Config with New Settings

**Files:**
- Modify: `stourio-core-engine/src/config.py`

- [ ] **Step 1: Read current config.py**

Read `stourio-core-engine/src/config.py` to see current Settings class.

- [ ] **Step 2: Add all new config fields**

Add these fields to the `Settings` class in `stourio-core-engine/src/config.py`, after the existing fields:

```python
    # --- RAG ---
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
    reranker_provider: str = "cohere"
    cohere_api_key: str = ""
    runbooks_dir: str = "/app/docs"

    # --- Notifications ---
    notification_config_path: str = "config/notifications.yaml"

    # --- Caching ---
    cache_enabled: bool = True
    cache_orchestrator_ttl: int = 300
    cache_agent_ttl: int = 0

    # --- Cost tracking ---
    cost_alert_daily_threshold: float = 0.0
    cost_alert_channel: str = ""

    # --- Agent memory ---
    agent_memory_ttl_days: int = 90
    agent_memory_recall_count: int = 3
    conversation_history_limit: int = 20

    # --- Plugins ---
    tools_yaml_dir: str = "tools/yaml"
    tools_python_dir: str = "tools/python"

    # --- Chains ---
    chains_config_path: str = "config/chains.yaml"

    # --- Agent concurrency & templates ---
    agent_templates_dir: str = "config/agents"
    agent_concurrency_default: int = 3
    agent_concurrency_config: dict = {}
```

- [ ] **Step 3: Commit**

```bash
git add stourio-core-engine/src/config.py
git commit -m "config: add settings for RAG, notifications, caching, tracking, concurrency"
```

---

### Task 1.3: Add New DB Tables (document_chunks, token_usage)

**Files:**
- Modify: `stourio-core-engine/src/persistence/database.py`

- [ ] **Step 1: Read current database.py**

Read `stourio-core-engine/src/persistence/database.py` to see current models and init_db().

- [ ] **Step 2: Add pgvector import and new models**

Add to imports at top of `stourio-core-engine/src/persistence/database.py`:
```python
from sqlalchemy import text
from pgvector.sqlalchemy import Vector
from src.config import settings
```

Add these two new SQLAlchemy models after the existing `ApprovalRecord` class:

```python
class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(String, primary_key=True)
    source_type = Column(String(50), nullable=False)      # 'runbook', 'agent_memory', 'incident'
    source_path = Column(String(500))
    title = Column(String(500))
    section_header = Column(String(500))
    content = Column(Text, nullable=False)
    metadata_ = Column("metadata", JSON, default={})
    embedding = Column(Vector(settings.embedding_dimension))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class TokenUsageRecord(Base):
    __tablename__ = "token_usage"

    id = Column(String, primary_key=True)
    execution_id = Column(String(100))
    conversation_id = Column(String(100))
    agent_template = Column(String(100))
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=False)
    input_tokens = Column(Integer, nullable=False)
    output_tokens = Column(Integer, nullable=False)
    total_tokens = Column(Integer, nullable=False)
    estimated_cost_usd = Column(Numeric(10, 6))
    call_type = Column(String(20))        # 'orchestrator', 'agent', 'embedding', 'rerank'
    cached_hit = Column(Boolean, default=False)
    units_used = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 3: Update init_db() to create pgvector extension first**

Replace the existing `init_db()` function:

```python
async def init_db():
    """Create pgvector extension and all tables."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created (pgvector enabled)")
```

- [ ] **Step 4: Commit**

```bash
git add stourio-core-engine/src/persistence/database.py
git commit -m "db: add document_chunks and token_usage tables with pgvector"
```

---

### Task 1.4: Add LLMResponse as Pydantic Model with TokenUsage

**Files:**
- Modify: `stourio-core-engine/src/models/schemas.py`

- [ ] **Step 1: Read current schemas.py**

Read `stourio-core-engine/src/models/schemas.py`.

- [ ] **Step 2: Add TokenUsage and update-related models**

Add to `stourio-core-engine/src/models/schemas.py`:

```python
class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class AgentMemoryEntry(BaseModel):
    conversation_id: str
    agent_template: str
    trigger_summary: str
    actions_taken: list[str] = []
    conclusion: str
    services_involved: list[str] = []
    resolution_status: str          # "resolved", "escalated", "failed"
    timestamp: datetime


class Notification(BaseModel):
    channel: str
    message: str
    severity: str = "info"
    context: dict = {}
    thread_id: str | None = None


class NotificationResult(BaseModel):
    success: bool
    channel: str
    error: str | None = None


class ApprovalEvent(str, Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ESCALATED = "escalated"
```

Also add `FORCE_CHAIN` to the existing `RuleAction` enum (keep lowercase values to match existing DB data):
```python
class RuleAction(str, Enum):
    REQUIRE_APPROVAL = "require_approval"
    HARD_REJECT = "hard_reject"
    TRIGGER_AUTOMATION = "trigger_automation"
    FORCE_AGENT = "force_agent"
    FORCE_CHAIN = "force_chain"
    ALLOW = "allow"
```

Add `config` field to `Rule` model:
```python
class Rule(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    pattern: str = Field(..., description="Regex or keyword pattern to match")
    pattern_type: str = "regex"
    action: RuleAction
    risk_level: RiskLevel = RiskLevel.MEDIUM
    automation_id: Optional[str] = None
    config: dict = Field(default_factory=dict)   # <-- NEW: holds agent_type, chain_name, etc.
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

Add `config` column to `RuleRecord` in `database.py`:
```python
config = Column(JSON, default={})
```

Add `description` field to `AgentTemplate` and rename `role` to `system_prompt`:
```python
class AgentTemplate(BaseModel):
    id: str
    name: str
    description: str = ""
    system_prompt: str = Field(default="", alias="role", description="System prompt describing the agent's role")
    tools: list[ToolDefinition] = Field(default_factory=list)
    max_steps: int = 10
    provider_override: Optional[str] = None
    model_override: Optional[str] = None
```
Note: The `alias="role"` allows backward compatibility with existing code that sets `role=`. New code uses `system_prompt`.

Update `AgentExecution.context` to accept both str and dict:
```python
class AgentExecution(BaseModel):
    id: str = Field(default_factory=new_id)
    agent_type: str
    objective: str
    context: str | dict = ""        # <-- changed from str to str | dict
    status: ExecutionStatus = ExecutionStatus.PENDING
    steps: list[dict[str, Any]] = Field(default_factory=list)
    result: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
```

Convert `LLMResponse` from plain class to Pydantic BaseModel with token usage:
In `adapters/base.py`, change `LLMResponse`:
```python
class LLMResponse(BaseModel):
    text: str | None = None
    tool_calls: list[dict] | None = None
    raw: dict = {}
    usage: TokenUsage = Field(default_factory=TokenUsage)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def has_tool_call(self) -> bool:
        return bool(self.tool_calls)

    @property
    def first_tool_call(self) -> dict | None:
        if self.tool_calls:
            return self.tool_calls[0]
        return None
```

Add import: `from src.models.schemas import TokenUsage` and `from pydantic import BaseModel, ConfigDict`.

Each adapter (`openai_adapter.py`, `anthropic_adapter.py`, `google_adapter.py`) must populate `usage` from the provider response. Example for OpenAI:
```python
return LLMResponse(
    text=msg.content,
    tool_calls=tool_calls,
    raw=response.model_dump(),
    usage=TokenUsage(
        input_tokens=response.usage.prompt_tokens,
        output_tokens=response.usage.completion_tokens,
        total_tokens=response.usage.total_tokens,
    ),
)
```

- [ ] **Step 3: Commit**

```bash
git add stourio-core-engine/src/models/schemas.py
git commit -m "schemas: add TokenUsage, AgentMemoryEntry, Notification, FORCE_CHAIN"
```

---

## Phase 2: Plugin-Based Tool System

### Task 2.1: Create BaseTool Interface

**Files:**
- Create: `stourio-core-engine/src/plugins/__init__.py`
- Create: `stourio-core-engine/src/plugins/base.py`

- [ ] **Step 1: Write failing test for BaseTool**

Create `stourio-core-engine/tests/test_plugin_registry.py`:
```python
import pytest
from src.plugins.base import BaseTool


class DummyTool(BaseTool):
    name = "dummy"
    description = "A test tool"
    parameters = {"type": "object", "properties": {"x": {"type": "string"}}}

    async def execute(self, arguments: dict) -> dict:
        return {"result": arguments.get("x", "none")}


@pytest.mark.asyncio
async def test_base_tool_execute():
    tool = DummyTool()
    result = await tool.execute({"x": "hello"})
    assert result == {"result": "hello"}


@pytest.mark.asyncio
async def test_base_tool_validate_default():
    tool = DummyTool()
    assert await tool.validate({"x": "hello"}) is True


@pytest.mark.asyncio
async def test_base_tool_health_check_default():
    tool = DummyTool()
    assert await tool.health_check() is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/catalinstour/Documents/Intelligence/stourio-engine/stourio-core-engine
python -m pytest tests/test_plugin_registry.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'src.plugins'`

- [ ] **Step 3: Implement BaseTool**

Create `stourio-core-engine/src/plugins/__init__.py` (empty).

Create `stourio-core-engine/src/plugins/base.py`:
```python
from abc import ABC, abstractmethod


class BaseTool(ABC):
    """Interface for all tools — Python plugins and YAML-derived tools."""

    name: str
    description: str
    parameters: dict          # JSON Schema
    execution_mode: str = "local"   # "local" | "gateway" | "sandboxed"

    @abstractmethod
    async def execute(self, arguments: dict) -> dict:
        """Execute the tool with given arguments. Returns result dict."""
        ...

    async def validate(self, arguments: dict) -> bool:
        """Validate arguments before execution. Override for custom validation."""
        return True

    async def health_check(self) -> bool:
        """Check if the tool is operational. Override for custom checks."""
        return True

    def to_tool_definition(self) -> dict:
        """Convert to the ToolDefinition format used by agent templates."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/catalinstour/Documents/Intelligence/stourio-engine/stourio-core-engine
python -m pytest tests/test_plugin_registry.py -v
```
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add stourio-core-engine/src/plugins/ stourio-core-engine/tests/test_plugin_registry.py
git commit -m "feat: add BaseTool interface for plugin system"
```

---

### Task 2.2: Create YAML Tool Loader

**Files:**
- Create: `stourio-core-engine/src/plugins/yaml_tool.py`
- Create: `stourio-core-engine/src/plugins/loader.py`
- Create: `stourio-core-engine/src/tools/yaml/.gitkeep`
- Create: `stourio-core-engine/src/tools/python/.gitkeep`

- [ ] **Step 1: Write failing test for YAML tool loading**

Append to `stourio-core-engine/tests/test_plugin_registry.py`:
```python
import os
import tempfile
import yaml
from src.plugins.yaml_tool import YamlTool
from src.plugins.loader import load_yaml_tools, load_python_tools


def test_yaml_tool_parsing():
    definition = {
        "name": "test_api",
        "description": "Test API tool",
        "parameters": {
            "query": {"type": "string", "required": True},
        },
        "endpoint": {
            "url": "https://example.com/api",
            "method": "GET",
        },
        "execution": {"mode": "local"},
    }
    tool = YamlTool(definition)
    assert tool.name == "test_api"
    assert tool.description == "Test API tool"
    assert tool.execution_mode == "local"


def test_load_yaml_tools_from_directory():
    with tempfile.TemporaryDirectory() as tmpdir:
        definition = {
            "name": "metrics_query",
            "description": "Query metrics",
            "parameters": {"metric": {"type": "string"}},
            "endpoint": {"url": "http://prom:9090/query", "method": "GET"},
            "execution": {"mode": "local"},
        }
        filepath = os.path.join(tmpdir, "metrics.yaml")
        with open(filepath, "w") as f:
            yaml.dump(definition, f)

        tools = load_yaml_tools(tmpdir)
        assert len(tools) == 1
        assert tools[0].name == "metrics_query"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_plugin_registry.py::test_yaml_tool_parsing -v
```
Expected: FAIL

- [ ] **Step 3: Implement YamlTool and loader**

Create `stourio-core-engine/src/plugins/yaml_tool.py`:
```python
import os
import re
import logging
import httpx
from jinja2 import Template
from src.plugins.base import BaseTool

logger = logging.getLogger("stourio.plugins.yaml_tool")

ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env_vars(value: str) -> str:
    """Replace ${VAR_NAME} with environment variable values."""
    def replacer(match):
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))
    if isinstance(value, str):
        return ENV_VAR_PATTERN.sub(replacer, value)
    return value


class YamlTool(BaseTool):
    """Tool defined by a YAML configuration file."""

    def __init__(self, definition: dict):
        self.name = definition["name"]
        self.description = definition.get("description", "")
        self.parameters = definition.get("parameters", {})
        self.execution_mode = definition.get("execution", {}).get("mode", "local")
        self._endpoint = definition.get("endpoint", {})
        self._response_config = definition.get("response", {})

    async def execute(self, arguments: dict) -> dict:
        url = _resolve_env_vars(self._endpoint.get("url", ""))
        method = self._endpoint.get("method", "GET").upper()
        headers = {
            k: _resolve_env_vars(v)
            for k, v in self._endpoint.get("headers", {}).items()
        }

        body = None
        body_template = self._endpoint.get("body_template")
        if body_template:
            template = Template(body_template)
            body = template.render(**arguments)

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                content=body if body else None,
            )
            response.raise_for_status()
            result = response.json()

        # Extract nested value if configured
        extract_path = self._response_config.get("extract")
        if extract_path:
            for key in extract_path.split("."):
                if "[" in key:
                    field, idx = key.rstrip("]").split("[")
                    result = result[field][int(idx)]
                else:
                    result = result[key]

        return {"result": result}
```

Create `stourio-core-engine/src/plugins/loader.py`:
```python
import os
import importlib
import importlib.util
import logging
import yaml
from src.plugins.base import BaseTool
from src.plugins.yaml_tool import YamlTool

logger = logging.getLogger("stourio.plugins.loader")


def load_yaml_tools(directory: str) -> list[BaseTool]:
    """Load all YAML tool definitions from a directory."""
    tools = []
    if not os.path.isdir(directory):
        logger.warning(f"YAML tools directory not found: {directory}")
        return tools

    for filename in sorted(os.listdir(directory)):
        if not filename.endswith((".yaml", ".yml")):
            continue
        filepath = os.path.join(directory, filename)
        try:
            with open(filepath, "r") as f:
                definition = yaml.safe_load(f)
            if not definition or "name" not in definition:
                logger.warning(f"Skipping invalid YAML tool: {filepath}")
                continue
            tool = YamlTool(definition)
            tools.append(tool)
            logger.info(f"Loaded YAML tool: {tool.name} from {filepath}")
        except Exception as e:
            logger.error(f"Failed to load YAML tool {filepath}: {e}")
    return tools


def load_python_tools(directory: str) -> list[BaseTool]:
    """Auto-discover Python plugin files. Each must export a class inheriting BaseTool."""
    tools = []
    if not os.path.isdir(directory):
        logger.warning(f"Python tools directory not found: {directory}")
        return tools

    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue
        filepath = os.path.join(directory, filename)
        module_name = f"tools.python.{filename[:-3]}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseTool)
                    and attr is not BaseTool
                ):
                    tool = attr()
                    tools.append(tool)
                    logger.info(f"Loaded Python tool: {tool.name} from {filepath}")
        except Exception as e:
            logger.error(f"Failed to load Python tool {filepath}: {e}")
    return tools
```

Create dirs:
```bash
mkdir -p stourio-core-engine/src/tools/yaml stourio-core-engine/src/tools/python
touch stourio-core-engine/src/tools/yaml/.gitkeep stourio-core-engine/src/tools/python/.gitkeep
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_plugin_registry.py -v
```
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add stourio-core-engine/src/plugins/ stourio-core-engine/src/tools/ stourio-core-engine/tests/test_plugin_registry.py
git commit -m "feat: add YAML tool loader and Python plugin discovery"
```

---

### Task 2.3: Create Tool Registry

**Files:**
- Create: `stourio-core-engine/src/plugins/registry.py`

- [ ] **Step 1: Write failing test for registry**

Append to `stourio-core-engine/tests/test_plugin_registry.py`:
```python
from src.plugins.registry import ToolRegistry


@pytest.mark.asyncio
async def test_registry_register_and_get():
    registry = ToolRegistry()
    tool = DummyTool()
    registry.register(tool)
    assert registry.get("dummy") is tool
    assert registry.get("nonexistent") is None


@pytest.mark.asyncio
async def test_registry_execute():
    registry = ToolRegistry()
    registry.register(DummyTool())
    result = await registry.execute("dummy", {"x": "world"})
    assert result == {"result": "world"}


@pytest.mark.asyncio
async def test_registry_execute_unknown_tool():
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="not registered"):
        await registry.execute("unknown", {})


def test_registry_list_tools():
    registry = ToolRegistry()
    registry.register(DummyTool())
    tools = registry.list_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "dummy"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_plugin_registry.py::test_registry_register_and_get -v
```
Expected: FAIL

- [ ] **Step 3: Implement ToolRegistry**

Create `stourio-core-engine/src/plugins/registry.py`:
```python
import logging
import httpx
from src.plugins.base import BaseTool
from src.config import settings

logger = logging.getLogger("stourio.plugins.registry")


class ToolRegistry:
    """Central registry for all tools. Single source of truth for tool resolution."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            logger.warning(f"Overwriting existing tool: {tool.name}")
        self._tools[tool.name] = tool
        logger.info(f"Registered tool: {tool.name} (mode={tool.execution_mode})")

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> list[dict]:
        return [tool.to_tool_definition() for tool in self._tools.values()]

    async def execute(self, tool_name: str, arguments: dict) -> dict:
        """Execute a tool by name. Dispatches based on execution mode."""
        tool = self._tools.get(tool_name)
        if not tool:
            raise ValueError(
                f"Tool '{tool_name}' not registered. Check plugin configuration."
            )

        if tool.execution_mode == "gateway":
            return await self._execute_via_gateway(tool_name, arguments)

        # local execution
        if not await tool.validate(arguments):
            return {"error": f"Validation failed for tool '{tool_name}'"}
        return await tool.execute(arguments)

    async def _execute_via_gateway(self, tool_name: str, arguments: dict) -> dict:
        """Route tool execution through MCP gateway."""
        if not settings.mcp_server_url:
            return {"error": "MCP gateway URL not configured"}

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{settings.mcp_server_url}/execute",
                json={"tool_name": tool_name, "arguments": arguments},
                headers={"Authorization": f"Bearer {settings.mcp_shared_secret}"},
            )
            resp.raise_for_status()
            return resp.json()


# Global registry instance
_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def init_registry() -> ToolRegistry:
    """Initialize registry by loading all tools from configured directories."""
    from src.plugins.loader import load_yaml_tools, load_python_tools

    global _registry
    _registry = ToolRegistry()

    yaml_tools = load_yaml_tools(settings.tools_yaml_dir)
    for tool in yaml_tools:
        _registry.register(tool)

    python_tools = load_python_tools(settings.tools_python_dir)
    for tool in python_tools:
        _registry.register(tool)

    logger.info(f"Registry initialized with {len(_registry._tools)} tools")
    return _registry
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_plugin_registry.py -v
```
Expected: 9 PASSED

- [ ] **Step 5: Commit**

```bash
git add stourio-core-engine/src/plugins/registry.py stourio-core-engine/tests/test_plugin_registry.py
git commit -m "feat: add ToolRegistry with local/gateway dispatch"
```

---

### Task 2.4: Replace default_tool_executor with PluginToolExecutor

**Files:**
- Modify: `stourio-core-engine/src/agents/runtime.py`

- [ ] **Step 1: Read current runtime.py**

Read `stourio-core-engine/src/agents/runtime.py` to understand the current `default_tool_executor` and `_get_valid_tool_names`.

- [ ] **Step 2: Replace default_tool_executor**

In `stourio-core-engine/src/agents/runtime.py`:

Add imports at top:
```python
import json
from src.plugins.registry import get_registry
```

Replace the `default_tool_executor` function with:
```python
async def default_tool_executor(tool_name: str, arguments: dict) -> str:
    """Execute tool via plugin registry. Falls back to MCP gateway for unregistered tools."""
    registry = get_registry()

    # Two-stage validation: (1) tool name is safe, (2) tool exists in registry
    if not _SAFE_TOOL_NAME.match(tool_name):
        return json.dumps({"error": f"Invalid tool name: {tool_name}"})

    try:
        result = await registry.execute(tool_name, arguments)
        return json.dumps(result) if isinstance(result, dict) else str(result)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        logger.error(f"Tool execution failed: {tool_name}: {e}")
        return json.dumps({"error": f"Tool execution failed: {str(e)}"})
```

Remove `_get_valid_tool_names()` function and `_VALID_TOOL_NAMES` global (no longer needed — registry is source of truth).

Keep `_SAFE_TOOL_NAME` regex for name validation.

- [ ] **Step 3: Run existing tests to check for regressions**

```bash
python -m pytest tests/ -v
```

- [ ] **Step 4: Commit**

```bash
git add stourio-core-engine/src/agents/runtime.py
git commit -m "feat: replace hardcoded tool executor with plugin registry dispatch"
```

---

### Task 2.5: Add Gateway Dynamic Tool Registration Endpoint

**Files:**
- Modify: `stourio-mcp-engine/gateway.py`

- [ ] **Step 1: Read current gateway.py**

Read `stourio-mcp-engine/gateway.py`.

- [ ] **Step 2: Add /tools/register endpoint**

Add to `stourio-mcp-engine/gateway.py`, after the existing `/execute` endpoint:

```python
@app.post("/tools/register")
async def register_tool_endpoint(request: Request):
    """Dynamically register a tool from the core engine."""
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {SHARED_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Tool name required")

    handler_type = body.get("handler_type", "proxy")
    TOOL_REGISTRY[name] = {
        "handler": _create_proxy_handler(body) if handler_type == "proxy" else None,
        "description": body.get("description", ""),
        "parameters": body.get("parameters", {}),
        "handler_type": handler_type,
    }
    logger.info(f"Dynamically registered tool: {name} (type={handler_type})")
    return {"status": "registered", "tool": name}


def _create_proxy_handler(definition: dict):
    """Create an async handler that proxies to an external endpoint."""
    endpoint = definition.get("endpoint", {})
    url = endpoint.get("url", "")
    method = endpoint.get("method", "GET")

    async def proxy_handler(**kwargs):
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(method=method, url=url, json=kwargs)
            resp.raise_for_status()
            return resp.json()

    return proxy_handler
```

Add `import httpx` to gateway imports if not present.

- [ ] **Step 3: Commit**

```bash
git add stourio-mcp-engine/gateway.py
git commit -m "feat: add /tools/register endpoint for dynamic tool registration"
```

---

### Task 2.6: Initialize Plugin Registry at Startup

**Files:**
- Modify: `stourio-core-engine/src/main.py`

- [ ] **Step 1: Read current main.py**

Read `stourio-core-engine/src/main.py`.

- [ ] **Step 2: Add registry init to lifespan**

Add import:
```python
from src.plugins.registry import init_registry
```

In the `lifespan()` function, after `await seed_default_rules()` and before `yield`, add:
```python
    # Initialize plugin registry
    init_registry()
    logger.info("Plugin registry initialized")
```

- [ ] **Step 3: Commit**

```bash
git add stourio-core-engine/src/main.py
git commit -m "feat: initialize plugin registry at application startup"
```

---

## Phase 3: RAG Pipeline

### Task 3.1: Create Embedder Interface and OpenAI Implementation

**Files:**
- Create: `stourio-core-engine/src/rag/__init__.py`
- Create: `stourio-core-engine/src/rag/embeddings/__init__.py`
- Create: `stourio-core-engine/src/rag/embeddings/base.py`
- Create: `stourio-core-engine/src/rag/embeddings/openai_embedder.py`

- [ ] **Step 1: Write failing test**

Create `stourio-core-engine/tests/test_rag_pipeline.py`:
```python
import pytest
from unittest.mock import AsyncMock, patch
from src.rag.embeddings.base import BaseEmbedder
from src.rag.embeddings.openai_embedder import OpenAIEmbedder


def test_openai_embedder_dimension():
    embedder = OpenAIEmbedder(api_key="test", model="text-embedding-3-small")
    assert embedder.dimension == 1536
    assert embedder.model_name == "text-embedding-3-small"


@pytest.mark.asyncio
async def test_openai_embedder_embed():
    embedder = OpenAIEmbedder(api_key="test", model="text-embedding-3-small")
    mock_response = AsyncMock()
    mock_response.data = [
        type("Obj", (), {"embedding": [0.1] * 1536})(),
        type("Obj", (), {"embedding": [0.2] * 1536})(),
    ]
    with patch.object(embedder._client.embeddings, "create", return_value=mock_response):
        result = await embedder.embed(["hello", "world"])
        assert len(result) == 2
        assert len(result[0]) == 1536
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_rag_pipeline.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement**

Create `stourio-core-engine/src/rag/__init__.py` (empty).
Create `stourio-core-engine/src/rag/embeddings/__init__.py` (empty).

Create `stourio-core-engine/src/rag/embeddings/base.py`:
```python
from abc import ABC, abstractmethod


class BaseEmbedder(ABC):
    dimension: int
    model_name: str

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts. Returns list of embedding vectors."""
        ...
```

Create `stourio-core-engine/src/rag/embeddings/openai_embedder.py`:
```python
from openai import AsyncOpenAI
from src.rag.embeddings.base import BaseEmbedder

DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class OpenAIEmbedder(BaseEmbedder):
    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self.model_name = model
        self.dimension = DIMENSIONS.get(model, 1536)
        self._client = AsyncOpenAI(api_key=api_key)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(
            model=self.model_name,
            input=texts,
        )
        return [item.embedding for item in response.data]
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_rag_pipeline.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add stourio-core-engine/src/rag/ stourio-core-engine/tests/test_rag_pipeline.py
git commit -m "feat: add embedder interface with OpenAI implementation"
```

---

### Task 3.2: Create Re-ranker Interface and Cohere Implementation

**Files:**
- Create: `stourio-core-engine/src/rag/reranker/__init__.py`
- Create: `stourio-core-engine/src/rag/reranker/base.py`
- Create: `stourio-core-engine/src/rag/reranker/cohere_reranker.py`

- [ ] **Step 1: Write failing test**

Append to `stourio-core-engine/tests/test_rag_pipeline.py`:
```python
from src.rag.reranker.base import BaseReranker, RankedDocument
from src.rag.reranker.cohere_reranker import CohereReranker


def test_ranked_document_model():
    doc = RankedDocument(content="test", score=0.95, index=0)
    assert doc.score == 0.95


@pytest.mark.asyncio
async def test_cohere_reranker():
    reranker = CohereReranker(api_key="test")
    mock_result = type("Obj", (), {
        "results": [
            type("R", (), {"index": 1, "relevance_score": 0.9})(),
            type("R", (), {"index": 0, "relevance_score": 0.3})(),
        ]
    })()
    with patch.object(reranker._client, "rerank", return_value=mock_result):
        results = await reranker.rerank("query", ["doc1", "doc2"], top_k=2)
        assert len(results) == 2
        assert results[0].score == 0.9
        assert results[0].content == "doc2"  # index 1
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement**

Create `stourio-core-engine/src/rag/reranker/__init__.py` (empty).

Create `stourio-core-engine/src/rag/reranker/base.py`:
```python
from abc import ABC, abstractmethod
from pydantic import BaseModel


class RankedDocument(BaseModel):
    content: str
    score: float
    index: int
    metadata: dict = {}


class BaseReranker(ABC):
    @abstractmethod
    async def rerank(
        self, query: str, documents: list[str], top_k: int = 3
    ) -> list[RankedDocument]:
        """Re-rank documents by relevance to query. Returns top_k results."""
        ...
```

Create `stourio-core-engine/src/rag/reranker/cohere_reranker.py`:
```python
import cohere
from src.rag.reranker.base import BaseReranker, RankedDocument


class CohereReranker(BaseReranker):
    def __init__(self, api_key: str, model: str = "rerank-v3.5"):
        self._client = cohere.Client(api_key=api_key)
        self._model = model

    async def rerank(
        self, query: str, documents: list[str], top_k: int = 3
    ) -> list[RankedDocument]:
        response = self._client.rerank(
            model=self._model,
            query=query,
            documents=documents,
            top_n=top_k,
        )
        return [
            RankedDocument(
                content=documents[r.index],
                score=r.relevance_score,
                index=r.index,
            )
            for r in response.results
        ]
```

- [ ] **Step 4: Run tests**

- [ ] **Step 5: Commit**

```bash
git add stourio-core-engine/src/rag/reranker/ stourio-core-engine/tests/test_rag_pipeline.py
git commit -m "feat: add reranker interface with Cohere implementation"
```

---

### Task 3.3: Create Document Chunker

**Files:**
- Create: `stourio-core-engine/src/rag/chunker.py`

- [ ] **Step 1: Write failing test**

Append to `stourio-core-engine/tests/test_rag_pipeline.py`:
```python
from src.rag.chunker import chunk_markdown


def test_chunk_by_headers():
    md = """# Service A
Overview of service A.

## Troubleshooting
Step 1: Check logs.
Step 2: Restart.

## Monitoring
Check Grafana dashboard.
"""
    chunks = chunk_markdown(md, source_path="runbooks/service-a.md")
    assert len(chunks) >= 2
    assert chunks[0]["section_header"] in ("Service A", "Troubleshooting", "Monitoring")
    assert all("content" in c for c in chunks)
    assert all("source_path" in c for c in chunks)


def test_chunk_preserves_metadata():
    md = """---
service: redis
domain: cache
---
# Redis Runbook
Content here.
"""
    chunks = chunk_markdown(md, source_path="runbooks/redis.md")
    assert len(chunks) >= 1
    assert chunks[0].get("metadata", {}).get("service") == "redis"


def test_chunk_splits_long_sections():
    # Create a section with many paragraphs
    paragraphs = "\n\n".join([f"Paragraph {i} with enough words to count." * 20 for i in range(20)])
    md = f"# Long Section\n{paragraphs}"
    chunks = chunk_markdown(md, source_path="test.md", max_tokens=512)
    assert len(chunks) > 1
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement chunker**

Create `stourio-core-engine/src/rag/chunker.py`:
```python
import re
import hashlib
import logging

logger = logging.getLogger("stourio.rag.chunker")

HEADER_PATTERN = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Rough token estimate: 1 token ≈ 4 chars
def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter metadata and return (metadata, remaining_text)."""
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        return {}, text
    import yaml
    try:
        metadata = yaml.safe_load(match.group(1)) or {}
    except Exception:
        metadata = {}
    remaining = text[match.end():]
    return metadata, remaining


def _split_by_paragraphs(text: str, max_tokens: int, overlap_tokens: int = 50) -> list[str]:
    """Split text into chunks by paragraph boundaries with overlap."""
    paragraphs = text.split("\n\n")
    chunks = []
    current = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = _estimate_tokens(para)
        if current_tokens + para_tokens > max_tokens and current:
            chunks.append("\n\n".join(current))
            # Overlap: keep last paragraph(s) up to overlap_tokens
            overlap = []
            overlap_count = 0
            for p in reversed(current):
                t = _estimate_tokens(p)
                if overlap_count + t > overlap_tokens:
                    break
                overlap.insert(0, p)
                overlap_count += t
            current = overlap
            current_tokens = overlap_count
        current.append(para)
        current_tokens += para_tokens

    if current:
        chunks.append("\n\n".join(current))
    return chunks


def chunk_markdown(
    text: str,
    source_path: str = "",
    max_tokens: int = 512,
    overlap_tokens: int = 50,
) -> list[dict]:
    """Split markdown into chunks by headers, then by paragraphs if too long."""
    metadata, text = _parse_frontmatter(text)

    # Split by headers
    sections = []
    parts = HEADER_PATTERN.split(text)

    # parts[0] is text before first header (if any)
    if parts[0].strip():
        sections.append({"header": "", "content": parts[0].strip()})

    # After that, groups of 3: (level_hashes, header_text, content_until_next_header)
    i = 1
    while i < len(parts) - 1:
        header = parts[i + 1].strip()
        content = parts[i + 2].strip() if i + 2 < len(parts) else ""
        sections.append({"header": header, "content": content})
        i += 3

    # Chunk each section
    chunks = []
    for section in sections:
        content = section["content"]
        if not content:
            continue

        if _estimate_tokens(content) <= max_tokens:
            text_chunks = [content]
        else:
            text_chunks = _split_by_paragraphs(content, max_tokens, overlap_tokens)

        for chunk_text in text_chunks:
            content_hash = hashlib.sha256(chunk_text.encode()).hexdigest()[:16]
            chunks.append({
                "section_header": section["header"],
                "content": chunk_text,
                "source_path": source_path,
                "metadata": {
                    **metadata,
                    "content_hash": content_hash,
                },
            })

    return chunks
```

- [ ] **Step 4: Run tests**

- [ ] **Step 5: Commit**

```bash
git add stourio-core-engine/src/rag/chunker.py stourio-core-engine/tests/test_rag_pipeline.py
git commit -m "feat: add markdown chunker with header splitting and overlap"
```

---

### Task 3.4: Create Ingestion Pipeline

**Files:**
- Create: `stourio-core-engine/src/rag/ingestion.py`

- [ ] **Step 1: Write failing test**

Append to `stourio-core-engine/tests/test_rag_pipeline.py`:
```python
from src.rag.ingestion import _hash_file, _should_reingest


def test_file_hash():
    h = _hash_file("test content")
    assert isinstance(h, str)
    assert len(h) == 64  # sha256 hex


def test_should_reingest_new_file():
    assert _should_reingest("abc123", {}) is True


def test_should_reingest_unchanged():
    assert _should_reingest("abc123", {"content_hash": "abc123"}) is False


def test_should_reingest_changed():
    assert _should_reingest("abc123", {"content_hash": "def456"}) is True
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement ingestion**

Create `stourio-core-engine/src/rag/ingestion.py`:
```python
import os
import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.schemas import new_id
from src.persistence.database import DocumentChunk, async_session
from src.rag.chunker import chunk_markdown
from src.rag.embeddings.base import BaseEmbedder
from src.config import settings

logger = logging.getLogger("stourio.rag.ingestion")


def _hash_file(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _should_reingest(file_hash: str, existing_metadata: dict) -> bool:
    return existing_metadata.get("content_hash") != file_hash


async def ingest_runbooks(embedder: BaseEmbedder, directory: str | None = None) -> int:
    """Scan runbooks directory, chunk and embed changed files. Returns count of new chunks."""
    directory = directory or settings.runbooks_dir
    if not os.path.isdir(directory):
        logger.warning(f"Runbooks directory not found: {directory}")
        return 0

    total_chunks = 0

    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".md"):
            continue
        filepath = os.path.join(directory, filename)
        with open(filepath, "r") as f:
            content = f.read()

        file_hash = _hash_file(content)

        # Check if already ingested with same hash
        async with async_session() as session:
            existing = await session.execute(
                select(DocumentChunk.metadata_)
                .where(DocumentChunk.source_path == filepath)
                .where(DocumentChunk.source_type == "runbook")
                .limit(1)
            )
            row = existing.scalar_one_or_none()
            if row and not _should_reingest(file_hash, row or {}):
                logger.debug(f"Skipping unchanged: {filepath}")
                continue

            # Delete old chunks for this file
            await session.execute(
                delete(DocumentChunk).where(
                    DocumentChunk.source_path == filepath
                )
            )

            # Chunk the document
            chunks = chunk_markdown(content, source_path=filepath)
            if not chunks:
                continue

            # Embed all chunk contents
            texts = [c["content"] for c in chunks]
            embeddings = await embedder.embed(texts)

            # Store chunks
            for chunk_data, embedding in zip(chunks, embeddings):
                chunk_data["metadata"]["content_hash"] = file_hash
                chunk_data["metadata"]["embedding_model"] = embedder.model_name
                record = DocumentChunk(
                    id=new_id(),
                    source_type="runbook",
                    source_path=filepath,
                    title=filename.replace(".md", ""),
                    section_header=chunk_data.get("section_header", ""),
                    content=chunk_data["content"],
                    metadata_=chunk_data.get("metadata", {}),
                    embedding=embedding,
                )
                session.add(record)

            await session.commit()
            total_chunks += len(chunks)
            logger.info(f"Ingested {len(chunks)} chunks from {filepath}")

    return total_chunks


async def ingest_text(
    embedder: BaseEmbedder,
    content: str,
    source_type: str,
    source_path: str = "",
    title: str = "",
    extra_metadata: dict | None = None,
) -> int:
    """Ingest arbitrary text content (used for agent memory). Returns chunk count."""
    chunks = chunk_markdown(content, source_path=source_path)
    if not chunks:
        return 0

    texts = [c["content"] for c in chunks]
    embeddings = await embedder.embed(texts)

    async with async_session() as session:
        for chunk_data, embedding in zip(chunks, embeddings):
            meta = chunk_data.get("metadata", {})
            if extra_metadata:
                meta.update(extra_metadata)
            meta["embedding_model"] = embedder.model_name

            record = DocumentChunk(
                id=new_id(),
                source_type=source_type,
                source_path=source_path,
                title=title,
                section_header=chunk_data.get("section_header", ""),
                content=chunk_data["content"],
                metadata_=meta,
                embedding=embedding,
            )
            session.add(record)
        await session.commit()

    return len(chunks)
```

- [ ] **Step 4: Run tests**

- [ ] **Step 5: Commit**

```bash
git add stourio-core-engine/src/rag/ingestion.py stourio-core-engine/tests/test_rag_pipeline.py
git commit -m "feat: add runbook ingestion pipeline with change detection"
```

---

### Task 3.5: Create Retriever (Search + Rerank)

**Files:**
- Create: `stourio-core-engine/src/rag/retriever.py`

- [ ] **Step 1: Write failing test**

Append to `stourio-core-engine/tests/test_rag_pipeline.py`:
```python
from src.rag.retriever import Retriever


def test_retriever_init():
    mock_embedder = AsyncMock()
    mock_embedder.dimension = 1536
    mock_reranker = AsyncMock()
    retriever = Retriever(embedder=mock_embedder, reranker=mock_reranker)
    assert retriever is not None
```

- [ ] **Step 2: Implement retriever**

Create `stourio-core-engine/src/rag/retriever.py`:
```python
import logging
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.persistence.database import DocumentChunk, async_session
from src.rag.embeddings.base import BaseEmbedder
from src.rag.reranker.base import BaseReranker, RankedDocument

logger = logging.getLogger("stourio.rag.retriever")


class RetrievalResult:
    def __init__(self, content: str, score: float, metadata: dict, source_path: str, section_header: str):
        self.content = content
        self.score = score
        self.metadata = metadata
        self.source_path = source_path
        self.section_header = section_header

    def __repr__(self):
        return f"RetrievalResult(score={self.score:.3f}, section='{self.section_header}')"


class Retriever:
    """Search + rerank pipeline for document retrieval."""

    def __init__(self, embedder: BaseEmbedder, reranker: BaseReranker | None = None):
        self._embedder = embedder
        self._reranker = reranker

    async def search(
        self,
        query: str,
        source_type: str | None = None,
        metadata_filter: dict | None = None,
        top_k_vector: int = 20,
        top_k_final: int = 3,
    ) -> list[RetrievalResult]:
        """Embed query → vector search → optional rerank → return top results."""
        # Embed query
        query_embedding = (await self._embedder.embed([query]))[0]

        # Vector search
        async with async_session() as session:
            # Build query with cosine distance
            stmt = (
                select(
                    DocumentChunk.content,
                    DocumentChunk.metadata_,
                    DocumentChunk.source_path,
                    DocumentChunk.section_header,
                    DocumentChunk.embedding.cosine_distance(query_embedding).label("distance"),
                )
                .order_by("distance")
                .limit(top_k_vector)
            )

            if source_type:
                stmt = stmt.where(DocumentChunk.source_type == source_type)

            if metadata_filter:
                for key, value in metadata_filter.items():
                    stmt = stmt.where(
                        DocumentChunk.metadata_[key].astext == str(value)
                    )

            result = await session.execute(stmt)
            rows = result.all()

        if not rows:
            return []

        # Rerank if available
        if self._reranker and len(rows) > top_k_final:
            documents = [row.content for row in rows]
            reranked = await self._reranker.rerank(query, documents, top_k=top_k_final)
            return [
                RetrievalResult(
                    content=r.content,
                    score=r.score,
                    metadata=rows[r.index].metadata_ or {},
                    source_path=rows[r.index].source_path or "",
                    section_header=rows[r.index].section_header or "",
                )
                for r in reranked
            ]

        # No reranker — return top_k_final by vector distance
        return [
            RetrievalResult(
                content=row.content,
                score=1.0 - row.distance,  # Convert distance to similarity
                metadata=row.metadata_ or {},
                source_path=row.source_path or "",
                section_header=row.section_header or "",
            )
            for row in rows[:top_k_final]
        ]
```

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

```bash
git add stourio-core-engine/src/rag/retriever.py stourio-core-engine/tests/test_rag_pipeline.py
git commit -m "feat: add retriever with vector search and optional reranking"
```

---

### Task 3.6: Create search_knowledge Plugin Tool

**Files:**
- Create: `stourio-core-engine/src/tools/python/knowledge_search.py`

- [ ] **Step 1: Implement knowledge_search tool**

Create `stourio-core-engine/src/tools/python/knowledge_search.py`:
```python
from src.plugins.base import BaseTool
from src.rag.retriever import Retriever


# This will be set during app initialization
_retriever: Retriever | None = None


def set_retriever(retriever: Retriever):
    global _retriever
    _retriever = retriever


class KnowledgeSearchTool(BaseTool):
    name = "search_knowledge"
    description = "Search internal documentation, runbooks, and past agent experiences"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query",
            },
            "source_type": {
                "type": "string",
                "enum": ["runbook", "agent_memory", "incident"],
                "description": "Optional: filter by source type",
            },
        },
        "required": ["query"],
    }
    execution_mode = "local"

    async def execute(self, arguments: dict) -> dict:
        if not _retriever:
            return {"error": "RAG retriever not initialized"}

        query = arguments.get("query", "")
        source_type = arguments.get("source_type")

        results = await _retriever.search(
            query=query,
            source_type=source_type,
        )

        return {
            "results": [
                {
                    "content": r.content,
                    "score": round(r.score, 3),
                    "source": r.source_path,
                    "section": r.section_header,
                }
                for r in results
            ]
        }
```

- [ ] **Step 2: Commit**

```bash
git add stourio-core-engine/src/tools/python/knowledge_search.py
git commit -m "feat: add search_knowledge plugin tool for RAG retrieval"
```

---

### Task 3.7: Add RAG Initialization to Startup + API Endpoints

**Files:**
- Modify: `stourio-core-engine/src/main.py`
- Modify: `stourio-core-engine/src/api/routes.py`

- [ ] **Step 1: Add RAG init to main.py lifespan**

Add imports to `stourio-core-engine/src/main.py`:
```python
from src.rag.embeddings.openai_embedder import OpenAIEmbedder
from src.rag.reranker.cohere_reranker import CohereReranker
from src.rag.retriever import Retriever
from src.rag.ingestion import ingest_runbooks
from src.tools.python.knowledge_search import set_retriever
```

Add to lifespan after plugin registry init:
```python
    # Initialize RAG pipeline
    embedder = OpenAIEmbedder(
        api_key=settings.openai_api_key,
        model=settings.embedding_model,
    )
    assert embedder.dimension == settings.embedding_dimension, (
        f"Embedder dimension {embedder.dimension} != config {settings.embedding_dimension}"
    )

    reranker = None
    if settings.reranker_provider == "cohere" and settings.cohere_api_key:
        reranker = CohereReranker(api_key=settings.cohere_api_key)

    retriever = Retriever(embedder=embedder, reranker=reranker)
    set_retriever(retriever)

    # Ingest runbooks on startup
    count = await ingest_runbooks(embedder)
    logger.info(f"Ingested {count} runbook chunks")
```

- [ ] **Step 2: Add ingestion API endpoint to routes.py**

Add to `stourio-core-engine/src/api/routes.py`:
```python
@router.post("/documents/ingest")
async def ingest_documents(api_key: str = Depends(get_api_key)):
    """Manually trigger runbook re-ingestion."""
    from src.rag.embeddings.openai_embedder import OpenAIEmbedder
    from src.rag.ingestion import ingest_runbooks

    embedder = OpenAIEmbedder(
        api_key=settings.openai_api_key,
        model=settings.embedding_model,
    )
    count = await ingest_runbooks(embedder)
    return {"status": "ok", "chunks_ingested": count}
```

- [ ] **Step 3: Commit**

```bash
git add stourio-core-engine/src/main.py stourio-core-engine/src/api/routes.py
git commit -m "feat: add RAG initialization at startup and ingestion API endpoint"
```

---

## Phase 4: Agent Session Memory

### Task 4.1: Add conversation_id to execute_agent and Load History

**Files:**
- Modify: `stourio-core-engine/src/agents/runtime.py`

- [ ] **Step 1: Read runtime.py**

Read `stourio-core-engine/src/agents/runtime.py`.

- [ ] **Step 2: Add conversation_id parameter and history loading**

In `execute_agent()` function signature, add `conversation_id: str | None = None` parameter.

Add import:
```python
from src.persistence.conversations import get_history
```

After the fencing token acquisition, before the agent loop, add:
```python
    # Load conversation history if available
    if conversation_id:
        history = await get_history(conversation_id, limit=settings.conversation_history_limit)
        if history:
            history_context = "\n".join(
                f"[{m.role}]: {m.content}" for m in history
            )
            messages.insert(0, {"role": "user", "content": f"Previous conversation context:\n{history_context}"})
```

- [ ] **Step 3: Add semantic memory recall**

Before the agent loop, after history loading, add:
```python
    # Recall relevant past experiences
    from src.tools.python.knowledge_search import _retriever
    if _retriever:
        try:
            memories = await _retriever.search(
                query=objective,
                source_type="agent_memory",
                top_k_final=settings.agent_memory_recall_count,
            )
            if memories:
                memory_text = "\n\n".join(
                    f"- {m.content} (score: {m.score:.2f})" for m in memories
                )
                system_content = template.role + f"\n\nRelevant past experience:\n{memory_text}"
                # Update the system message
                messages[0] = {"role": "system", "content": system_content}
        except Exception as e:
            logger.warning(f"Memory recall failed: {e}")
```

- [ ] **Step 4: Commit**

```bash
git add stourio-core-engine/src/agents/runtime.py
git commit -m "feat: load conversation history and semantic memory into agent context"
```

---

### Task 4.2: Persist Agent Memory After Execution

**Files:**
- Modify: `stourio-core-engine/src/agents/runtime.py`

- [ ] **Step 1: Add memory persistence after agent completes**

At the end of `execute_agent()`, after the agent loop completes and before returning `execution`, add:

```python
    # Persist agent memory for semantic recall
    try:
        from src.rag.ingestion import ingest_text
        from src.tools.python.knowledge_search import _retriever

        if _retriever and execution.result:
            # Build memory entry text
            actions = [s.get("tool_name", "unknown") for s in execution.steps if s.get("tool_name")]
            memory_text = (
                f"# Agent Execution: {agent_type}\n"
                f"## Trigger\n{objective}\n"
                f"## Actions Taken\n{', '.join(actions)}\n"
                f"## Conclusion\n{execution.result}\n"
            )
            await ingest_text(
                embedder=_retriever._embedder,
                content=memory_text,
                source_type="agent_memory",
                source_path=f"agent/{execution.id}",
                title=f"{agent_type} - {objective[:100]}",
                extra_metadata={
                    "agent_template": agent_type,
                    "execution_id": execution.id,
                    "conversation_id": conversation_id or "",
                },
            )
    except Exception as e:
        logger.warning(f"Failed to persist agent memory: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add stourio-core-engine/src/agents/runtime.py
git commit -m "feat: persist agent execution memory for semantic recall"
```

---

### Task 4.3: Thread conversation_id Through Orchestrator

**Files:**
- Modify: `stourio-core-engine/src/orchestrator/core.py`

- [ ] **Step 1: Read core.py**

Read `stourio-core-engine/src/orchestrator/core.py` to find all `execute_agent()` call sites.

- [ ] **Step 2: Add conversation_id to all execute_agent calls**

Find every `execute_agent(` call in `core.py` and add `conversation_id=signal.conversation_id`. There should be at least 2 call sites: the LLM-routed agent path and the approval-resumed path.

- [ ] **Step 3: Commit**

```bash
git add stourio-core-engine/src/orchestrator/core.py
git commit -m "feat: thread conversation_id through orchestrator to agent execution"
```

---

## Phase 5: Notification Framework

### Task 5.1: Create BaseNotifier and Webhook Notifier

**Files:**
- Create: `stourio-core-engine/src/notifications/__init__.py`
- Create: `stourio-core-engine/src/notifications/base.py`
- Create: `stourio-core-engine/src/notifications/webhook.py`

- [ ] **Step 1: Write failing test**

Create `stourio-core-engine/tests/test_notification_dispatcher.py`:
```python
import pytest
from unittest.mock import AsyncMock, patch
from src.notifications.webhook import WebhookNotifier
from src.models.schemas import Notification, NotificationResult


@pytest.mark.asyncio
async def test_webhook_notifier_send():
    notifier = WebhookNotifier(
        name="test-webhook",
        url="https://example.com/hook",
        headers={"Authorization": "Bearer test"},
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = type("R", (), {"status_code": 200, "raise_for_status": lambda: None})()
        result = await notifier.send(Notification(channel="test", message="hello"))
        assert result.success is True
```

- [ ] **Step 2: Implement**

Create `stourio-core-engine/src/notifications/__init__.py` (empty).

Create `stourio-core-engine/src/notifications/base.py`:
```python
from abc import ABC, abstractmethod
from src.models.schemas import Notification, NotificationResult


class BaseNotifier(ABC):
    name: str
    supports_threads: bool = False
    supports_severity: bool = False

    @abstractmethod
    async def send(self, notification: Notification) -> NotificationResult:
        ...

    async def health_check(self) -> bool:
        return True
```

Create `stourio-core-engine/src/notifications/webhook.py`:
```python
import logging
import httpx
from src.notifications.base import BaseNotifier
from src.models.schemas import Notification, NotificationResult

logger = logging.getLogger("stourio.notifications.webhook")


class WebhookNotifier(BaseNotifier):
    supports_threads = False
    supports_severity = False

    def __init__(self, name: str, url: str, headers: dict | None = None):
        self.name = name
        self._url = url
        self._headers = headers or {}

    async def send(self, notification: Notification) -> NotificationResult:
        payload = {
            "message": notification.message,
            "severity": notification.severity,
            "context": notification.context,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(self._url, json=payload, headers=self._headers)
                resp.raise_for_status()
            return NotificationResult(success=True, channel=self.name)
        except Exception as e:
            logger.error(f"Webhook notification failed ({self.name}): {e}")
            return NotificationResult(success=False, channel=self.name, error=str(e))
```

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

```bash
git add stourio-core-engine/src/notifications/ stourio-core-engine/tests/test_notification_dispatcher.py
git commit -m "feat: add BaseNotifier interface and webhook notifier"
```

---

### Task 5.2: Create Slack and PagerDuty Adapters

**Files:**
- Create: `stourio-core-engine/src/notifications/adapters/__init__.py`
- Create: `stourio-core-engine/src/notifications/adapters/slack.py`
- Create: `stourio-core-engine/src/notifications/adapters/pagerduty.py`
- Create: `stourio-core-engine/src/notifications/adapters/email.py`

- [ ] **Step 1: Implement Slack adapter**

Create `stourio-core-engine/src/notifications/adapters/__init__.py` (empty).

Create `stourio-core-engine/src/notifications/adapters/slack.py`:
```python
import logging
import httpx
from src.notifications.base import BaseNotifier
from src.models.schemas import Notification, NotificationResult

logger = logging.getLogger("stourio.notifications.slack")

SEVERITY_EMOJI = {"info": ":information_source:", "warning": ":warning:", "critical": ":rotating_light:"}


class SlackNotifier(BaseNotifier):
    supports_threads = True
    supports_severity = True

    def __init__(self, name: str, webhook_url: str, default_channel: str = ""):
        self.name = name
        self._webhook_url = webhook_url
        self._default_channel = default_channel

    async def send(self, notification: Notification) -> NotificationResult:
        emoji = SEVERITY_EMOJI.get(notification.severity, "")
        payload = {"text": f"{emoji} {notification.message}"}
        if notification.thread_id:
            payload["thread_ts"] = notification.thread_id

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(self._webhook_url, json=payload)
                resp.raise_for_status()
            return NotificationResult(success=True, channel=self.name)
        except Exception as e:
            logger.error(f"Slack notification failed ({self.name}): {e}")
            return NotificationResult(success=False, channel=self.name, error=str(e))
```

Create `stourio-core-engine/src/notifications/adapters/pagerduty.py`:
```python
import logging
import httpx
from src.notifications.base import BaseNotifier
from src.models.schemas import Notification, NotificationResult

logger = logging.getLogger("stourio.notifications.pagerduty")

SEVERITY_MAP = {"info": "info", "warning": "warning", "critical": "critical"}


class PagerDutyNotifier(BaseNotifier):
    supports_threads = False
    supports_severity = True

    def __init__(self, name: str, api_key: str, service_id: str):
        self.name = name
        self._api_key = api_key
        self._service_id = service_id

    async def send(self, notification: Notification) -> NotificationResult:
        payload = {
            "routing_key": self._api_key,
            "event_action": "trigger",
            "payload": {
                "summary": notification.message[:1024],
                "severity": SEVERITY_MAP.get(notification.severity, "info"),
                "source": "stourio-engine",
                "custom_details": notification.context,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://events.pagerduty.com/v2/enqueue",
                    json=payload,
                )
                resp.raise_for_status()
            return NotificationResult(success=True, channel=self.name)
        except Exception as e:
            logger.error(f"PagerDuty notification failed ({self.name}): {e}")
            return NotificationResult(success=False, channel=self.name, error=str(e))
```

Create `stourio-core-engine/src/notifications/adapters/email.py`:
```python
import logging
import httpx
from src.notifications.base import BaseNotifier
from src.models.schemas import Notification, NotificationResult

logger = logging.getLogger("stourio.notifications.email")


class EmailNotifier(BaseNotifier):
    supports_threads = False
    supports_severity = False

    def __init__(self, name: str, api_key: str, from_email: str, to_email: str):
        self.name = name
        self._api_key = api_key
        self._from_email = from_email
        self._to_email = to_email

    async def send(self, notification: Notification) -> NotificationResult:
        payload = {
            "personalizations": [{"to": [{"email": self._to_email}]}],
            "from": {"email": self._from_email},
            "subject": f"[Stourio {notification.severity.upper()}] {notification.message[:100]}",
            "content": [{"type": "text/plain", "value": notification.message}],
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                resp.raise_for_status()
            return NotificationResult(success=True, channel=self.name)
        except Exception as e:
            logger.error(f"Email notification failed ({self.name}): {e}")
            return NotificationResult(success=False, channel=self.name, error=str(e))
```

- [ ] **Step 2: Commit**

```bash
git add stourio-core-engine/src/notifications/adapters/
git commit -m "feat: add Slack, PagerDuty, and email notification adapters"
```

---

### Task 5.3: Create Notification Dispatcher

**Files:**
- Create: `stourio-core-engine/src/notifications/dispatcher.py`

- [ ] **Step 1: Write failing test**

Append to `stourio-core-engine/tests/test_notification_dispatcher.py`:
```python
from src.notifications.dispatcher import NotificationDispatcher


@pytest.mark.asyncio
async def test_dispatcher_routes_to_channel():
    mock_notifier = AsyncMock()
    mock_notifier.name = "test-slack"
    mock_notifier.send.return_value = NotificationResult(success=True, channel="test-slack")

    dispatcher = NotificationDispatcher()
    dispatcher.register_channel("oncall-slack", mock_notifier)

    result = await dispatcher.send(Notification(channel="oncall-slack", message="test"))
    assert result.success is True
    mock_notifier.send.assert_called_once()


@pytest.mark.asyncio
async def test_dispatcher_unknown_channel():
    dispatcher = NotificationDispatcher()
    result = await dispatcher.send(Notification(channel="nonexistent", message="test"))
    assert result.success is False
    assert "not configured" in result.error
```

- [ ] **Step 2: Implement dispatcher**

Create `stourio-core-engine/src/notifications/dispatcher.py`:
```python
import os
import logging
import yaml
from jinja2 import Template
from src.notifications.base import BaseNotifier
from src.notifications.webhook import WebhookNotifier
from src.notifications.adapters.slack import SlackNotifier
from src.notifications.adapters.pagerduty import PagerDutyNotifier
from src.notifications.adapters.email import EmailNotifier
from src.models.schemas import Notification, NotificationResult

logger = logging.getLogger("stourio.notifications.dispatcher")

NOTIFIER_TYPES = {
    "webhook": WebhookNotifier,
    "slack": SlackNotifier,
    "pagerduty": PagerDutyNotifier,
    "email": EmailNotifier,
}


def _resolve_env(value: str) -> str:
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], value)
    return value


class NotificationDispatcher:
    """Routes notifications to configured channels."""

    def __init__(self):
        self._channels: dict[str, BaseNotifier] = {}

    def register_channel(self, channel_name: str, notifier: BaseNotifier) -> None:
        self._channels[channel_name] = notifier
        logger.info(f"Registered notification channel: {channel_name} ({notifier.name})")

    async def send(self, notification: Notification) -> NotificationResult:
        notifier = self._channels.get(notification.channel)
        if not notifier:
            return NotificationResult(
                success=False,
                channel=notification.channel,
                error=f"Channel '{notification.channel}' not configured",
            )
        return await notifier.send(notification)

    async def send_templated(
        self, channel: str, template_str: str, context: dict, severity: str = "info"
    ) -> NotificationResult:
        """Render a Jinja2 template and send."""
        rendered = Template(template_str).render(**context)
        return await self.send(Notification(
            channel=channel, message=rendered, severity=severity, context=context,
        ))

    @classmethod
    def from_config(cls, config_path: str) -> "NotificationDispatcher":
        """Load dispatcher from YAML config file."""
        dispatcher = cls()
        if not os.path.isfile(config_path):
            logger.warning(f"Notification config not found: {config_path}")
            return dispatcher

        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}

        channels = config.get("notification_channels", {})
        for name, channel_config in channels.items():
            channel_type = channel_config.get("type", "webhook")
            try:
                if channel_type == "slack":
                    notifier = SlackNotifier(
                        name=name,
                        webhook_url=_resolve_env(channel_config.get("webhook_url", "")),
                        default_channel=channel_config.get("default_channel", ""),
                    )
                elif channel_type == "pagerduty":
                    notifier = PagerDutyNotifier(
                        name=name,
                        api_key=_resolve_env(channel_config.get("api_key", "")),
                        service_id=channel_config.get("service_id", ""),
                    )
                elif channel_type == "email":
                    notifier = EmailNotifier(
                        name=name,
                        api_key=_resolve_env(channel_config.get("api_key", "")),
                        from_email=channel_config.get("from_email", ""),
                        to_email=channel_config.get("to_email", ""),
                    )
                else:
                    notifier = WebhookNotifier(
                        name=name,
                        url=_resolve_env(channel_config.get("url", "")),
                        headers={
                            k: _resolve_env(v)
                            for k, v in channel_config.get("headers", {}).items()
                        },
                    )
                dispatcher.register_channel(name, notifier)
            except Exception as e:
                logger.error(f"Failed to create notifier '{name}': {e}")

        return dispatcher


# Global dispatcher
_dispatcher: NotificationDispatcher | None = None


def get_dispatcher() -> NotificationDispatcher:
    global _dispatcher
    if _dispatcher is None:
        from src.config import settings
        _dispatcher = NotificationDispatcher.from_config(settings.notification_config_path)
    return _dispatcher
```

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

```bash
git add stourio-core-engine/src/notifications/dispatcher.py stourio-core-engine/tests/test_notification_dispatcher.py
git commit -m "feat: add notification dispatcher with YAML config loading"
```

---

### Task 5.4: Integrate Notifications into Approval Lifecycle

**Files:**
- Modify: `stourio-core-engine/src/guardrails/approvals.py`
- Modify: `stourio-core-engine/src/main.py`

- [ ] **Step 1: Read current approvals.py**

Read `stourio-core-engine/src/guardrails/approvals.py`.

- [ ] **Step 2: Add notification dispatch to approval functions**

Add import to `stourio-core-engine/src/guardrails/approvals.py`:
```python
from src.notifications.dispatcher import get_dispatcher
from src.models.schemas import Notification, ApprovalEvent
```

In `create_approval_request()`, after the approval is created and cached, add:
```python
    # Fire notification
    try:
        dispatcher = get_dispatcher()
        await dispatcher.send(Notification(
            channel=settings.cost_alert_channel or "oncall-slack",
            message=f"Action requires approval: {action_description} (risk: {risk_level}). "
                    f"Approve/reject within {settings.approval_ttl_seconds}s.",
            severity="warning" if risk_level == "HIGH" else "critical",
            context={"approval_id": approval.id, "risk_level": risk_level},
        ))
    except Exception as e:
        logger.warning(f"Approval notification failed: {e}")
```

In `resolve_approval()`, after resolution, add:
```python
    # Fire notification
    try:
        dispatcher = get_dispatcher()
        event = "approved" if decision.approved else "rejected"
        await dispatcher.send(Notification(
            channel=settings.cost_alert_channel or "oncall-slack",
            message=f"Approval {event}: {record.action_description}",
            severity="info",
            context={"approval_id": approval_id, "decision": event},
        ))
    except Exception as e:
        logger.warning(f"Approval resolution notification failed: {e}")
```

- [ ] **Step 3: Add escalation worker to main.py**

Add to `stourio-core-engine/src/main.py`:

```python
async def approval_escalation_worker():
    """Background task to check for stalling approvals and escalate."""
    import json
    from src.persistence.redis_store import get_redis
    from src.notifications.dispatcher import get_dispatcher
    from src.models.schemas import Notification

    redis = await get_redis()
    while True:
        try:
            keys = []
            async for key in redis.scan_iter("stourio:approval_escalation:*"):
                keys.append(key)

            for key in keys:
                data = await redis.get(key)
                if not data:
                    continue
                info = json.loads(data)
                if info.get("notified"):
                    continue

                import time
                if time.time() >= info.get("escalation_time", 0):
                    dispatcher = get_dispatcher()
                    await dispatcher.send(Notification(
                        channel=info.get("channel", "oncall-slack"),
                        message=f"Approval stalling: {info.get('action', 'unknown')}. Respond urgently.",
                        severity="critical",
                        context={"approval_id": info.get("approval_id")},
                    ))
                    info["notified"] = True
                    ttl = await redis.ttl(key)
                    if ttl > 0:
                        await redis.setex(key, ttl, json.dumps(info))
        except Exception as e:
            logger.error(f"Escalation worker error: {e}")

        await asyncio.sleep(10)
```

Start this worker in the lifespan alongside the signal consumer:
```python
    escalation_task = asyncio.create_task(approval_escalation_worker())
```

And cancel it on shutdown alongside the signal consumer.

- [ ] **Step 4: Commit**

```bash
git add stourio-core-engine/src/guardrails/approvals.py stourio-core-engine/src/main.py
git commit -m "feat: integrate notifications into approval lifecycle with escalation worker"
```

---

## Phase 6: LLM Response Caching

### Task 6.1: Create CachedLLMAdapter

**Files:**
- Create: `stourio-core-engine/src/adapters/cache.py`

- [ ] **Step 1: Write failing test**

Create `stourio-core-engine/tests/test_cache.py`:
```python
import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from src.adapters.cache import CachedLLMAdapter, build_cache_key


def test_build_cache_key_deterministic():
    k1 = build_cache_key("openai", "gpt-4o", "sys", [], None)
    k2 = build_cache_key("openai", "gpt-4o", "sys", [], None)
    assert k1 == k2


def test_build_cache_key_different_prompts():
    k1 = build_cache_key("openai", "gpt-4o", "sys1", [], None)
    k2 = build_cache_key("openai", "gpt-4o", "sys2", [], None)
    assert k1 != k2


@pytest.mark.asyncio
async def test_cached_adapter_miss_then_hit():
    mock_adapter = AsyncMock()
    mock_adapter.provider_name = "openai"
    mock_adapter.model = "gpt-4o"
    mock_response = MagicMock()
    mock_response.model_dump_json.return_value = '{"text": "hello"}'
    mock_adapter.complete.return_value = mock_response

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # Cache miss

    cached = CachedLLMAdapter(adapter=mock_adapter, redis=mock_redis, ttl=300)
    await cached.complete("sys", [])

    mock_adapter.complete.assert_called_once()
    mock_redis.setex.assert_called_once()


@pytest.mark.asyncio
async def test_cached_adapter_ttl_zero_skips_cache():
    mock_adapter = AsyncMock()
    mock_redis = AsyncMock()

    cached = CachedLLMAdapter(adapter=mock_adapter, redis=mock_redis, ttl=0)
    await cached.complete("sys", [])

    mock_adapter.complete.assert_called_once()
    mock_redis.get.assert_not_called()
```

- [ ] **Step 2: Implement**

Create `stourio-core-engine/src/adapters/cache.py`:
```python
import hashlib
import json
import logging
from redis.asyncio import Redis

from src.adapters.base import BaseLLMAdapter, LLMResponse

logger = logging.getLogger("stourio.adapters.cache")


def build_cache_key(
    provider: str, model: str, system_prompt: str,
    messages: list, tools: list | None,
) -> str:
    payload = {
        "provider": provider,
        "model": model,
        "system_prompt": system_prompt,
        "messages": [m.model_dump() if hasattr(m, "model_dump") else m for m in messages],
        "tools": [t.model_dump() if hasattr(t, "model_dump") else t for t in sorted(tools, key=lambda t: getattr(t, "name", str(t)))] if tools else None,
    }
    content_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()
    return f"stourio:llm_cache:{content_hash}"


class CachedLLMAdapter:
    """Decorator around any LLM adapter. Transparent caching layer."""

    def __init__(self, adapter: BaseLLMAdapter, redis: Redis, ttl: int = 300):
        self.adapter = adapter
        self.redis = redis
        self.ttl = ttl
        # Proxy adapter attributes
        self.provider_name = adapter.provider_name

    async def complete(self, system_prompt, messages, tools=None, temperature=0.1):
        if self.ttl <= 0:
            return await self.adapter.complete(system_prompt, messages, tools, temperature)

        key = build_cache_key(
            self.adapter.provider_name,
            getattr(self.adapter, "model", "unknown"),
            system_prompt, messages, tools,
        )

        cached = await self.redis.get(key)
        if cached:
            logger.debug(f"Cache HIT: {key[:40]}...")
            data = json.loads(cached)
            return LLMResponse(
                text=data.get("text"),
                tool_calls=data.get("tool_calls"),
                raw=data.get("raw"),
            )

        result = await self.adapter.complete(system_prompt, messages, tools, temperature)
        try:
            cache_data = json.dumps({
                "text": result.text,
                "tool_calls": result.tool_calls,
                "raw": result.raw,
            }, default=str)
            await self.redis.setex(key, self.ttl, cache_data)
        except Exception as e:
            logger.warning(f"Cache write failed: {e}")

        return result
```

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

```bash
git add stourio-core-engine/src/adapters/cache.py stourio-core-engine/tests/test_cache.py
git commit -m "feat: add CachedLLMAdapter with Redis-backed response caching"
```

---

### Task 6.2: Wire Caching into Adapter Registry

**Files:**
- Modify: `stourio-core-engine/src/adapters/registry.py`

- [ ] **Step 1: Read current registry.py**

Read `stourio-core-engine/src/adapters/registry.py`.

- [ ] **Step 2: Wrap adapters with cache when enabled**

Add import:
```python
from src.adapters.cache import CachedLLMAdapter
```

Modify `get_orchestrator_adapter()` to wrap with cache if `settings.cache_enabled` and `settings.cache_orchestrator_ttl > 0`:
```python
def get_orchestrator_adapter():
    global _orchestrator_adapter
    if _orchestrator_adapter is None:
        adapter = create_adapter(settings.orchestrator_provider, settings.orchestrator_model)
        if settings.cache_enabled and settings.cache_orchestrator_ttl > 0:
            import asyncio
            from src.persistence.redis_store import get_redis
            # Cache wrapper will be applied on first use
            _orchestrator_adapter = _CachePendingAdapter(adapter, settings.cache_orchestrator_ttl)
        else:
            _orchestrator_adapter = adapter
    return _orchestrator_adapter
```

Note: Since Redis requires async init, the cache wrapper initialization should happen during app lifespan. Add a function `init_cached_adapters()` that wraps the orchestrator adapter with `CachedLLMAdapter` after Redis is available.

- [ ] **Step 3: Commit**

```bash
git add stourio-core-engine/src/adapters/registry.py
git commit -m "feat: wire LLM response caching into adapter registry"
```

---

### Task 6.3: Flush Cache on Kill Switch

**Files:**
- Modify: `stourio-core-engine/src/persistence/redis_store.py`

- [ ] **Step 1: Add cache flush to kill switch activation**

In `activate_kill_switch()` in `redis_store.py`, after setting the kill switch key, add:
```python
    # Flush LLM response cache
    keys = []
    async for key in redis.scan_iter("stourio:llm_cache:*"):
        keys.append(key)
    if keys:
        await redis.delete(*keys)
        logger.info(f"Flushed {len(keys)} cached LLM responses on kill switch activation")
```

- [ ] **Step 2: Commit**

```bash
git add stourio-core-engine/src/persistence/redis_store.py
git commit -m "feat: flush LLM cache on kill switch activation"
```

---

## Phase 7: Token/Cost Tracking

### Task 7.1: Create Cost Tracker

**Files:**
- Create: `stourio-core-engine/src/tracking/__init__.py`
- Create: `stourio-core-engine/src/tracking/pricing.py`
- Create: `stourio-core-engine/src/tracking/tracker.py`

- [ ] **Step 1: Write failing test**

Create `stourio-core-engine/tests/test_cost_tracking.py`:
```python
import pytest
from src.tracking.pricing import estimate_cost
from src.tracking.tracker import UsageTracker


def test_estimate_cost_gpt4o():
    cost = estimate_cost("gpt-4o", input_tokens=1000, output_tokens=500)
    assert cost > 0
    assert isinstance(cost, float)


def test_estimate_cost_unknown_model():
    cost = estimate_cost("unknown-model", input_tokens=1000, output_tokens=500)
    assert cost == 0.0
```

- [ ] **Step 2: Implement pricing and tracker**

Create `stourio-core-engine/src/tracking/__init__.py` (empty).

Create `stourio-core-engine/src/tracking/pricing.py`:
```python
# Pricing per 1M tokens (USD)
MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "claude-3-5-sonnet-latest": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "gemini-2.0-flash": {"input": 0.075, "output": 0.30},
    "gemini-3.1-pro-preview": {"input": 1.25, "output": 5.00},
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
    "text-embedding-3-large": {"input": 0.13, "output": 0.0},
    "rerank-v3.5": {"input": 0.0, "output": 0.0, "per_search": 0.001},
    "deepseek-chat": {"input": 0.14, "output": 0.28},
}


def estimate_cost(model: str, input_tokens: int = 0, output_tokens: int = 0, units: int = 0) -> float:
    pricing = MODEL_PRICING.get(model)
    if not pricing:
        return 0.0

    cost = (input_tokens * pricing.get("input", 0) / 1_000_000) + \
           (output_tokens * pricing.get("output", 0) / 1_000_000) + \
           (units * pricing.get("per_search", 0))
    return round(cost, 6)
```

Create `stourio-core-engine/src/tracking/tracker.py`:
```python
import logging
from src.models.schemas import new_id
from src.persistence.database import TokenUsageRecord, async_session
from src.tracking.pricing import estimate_cost

logger = logging.getLogger("stourio.tracking")


async def track_usage(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    call_type: str = "agent",
    execution_id: str = "",
    conversation_id: str = "",
    agent_template: str = "",
    cached_hit: bool = False,
    units_used: int = 0,
) -> None:
    """Record token usage and estimated cost."""
    total = input_tokens + output_tokens
    cost = estimate_cost(model, input_tokens, output_tokens, units_used)

    record = TokenUsageRecord(
        id=new_id(),
        execution_id=execution_id,
        conversation_id=conversation_id,
        agent_template=agent_template,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total,
        estimated_cost_usd=cost,
        call_type=call_type,
        cached_hit=cached_hit,
        units_used=units_used,
    )

    try:
        async with async_session() as session:
            session.add(record)
            await session.commit()
    except Exception as e:
        logger.error(f"Failed to track usage: {e}")


async def get_usage_summary(
    group_by: str = "agent_template",
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict]:
    """Query aggregated usage stats."""
    from sqlalchemy import func, select
    from datetime import datetime

    async with async_session() as session:
        group_col = getattr(TokenUsageRecord, group_by, TokenUsageRecord.agent_template)
        stmt = select(
            group_col,
            func.sum(TokenUsageRecord.input_tokens).label("total_input"),
            func.sum(TokenUsageRecord.output_tokens).label("total_output"),
            func.sum(TokenUsageRecord.total_tokens).label("total_tokens"),
            func.sum(TokenUsageRecord.estimated_cost_usd).label("total_cost"),
            func.count().label("call_count"),
        ).group_by(group_col)

        if from_date:
            stmt = stmt.where(TokenUsageRecord.created_at >= datetime.fromisoformat(from_date))
        if to_date:
            stmt = stmt.where(TokenUsageRecord.created_at <= datetime.fromisoformat(to_date))

        result = await session.execute(stmt)
        return [
            {
                "group": row[0],
                "input_tokens": row[1] or 0,
                "output_tokens": row[2] or 0,
                "total_tokens": row[3] or 0,
                "total_cost_usd": float(row[4] or 0),
                "call_count": row[5],
            }
            for row in result.all()
        ]
```

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

```bash
git add stourio-core-engine/src/tracking/ stourio-core-engine/tests/test_cost_tracking.py
git commit -m "feat: add cost tracking with model pricing and usage aggregation"
```

---

### Task 7.2: Add Usage API Endpoints

**Files:**
- Modify: `stourio-core-engine/src/api/routes.py`

- [ ] **Step 1: Add usage endpoints**

Add to `stourio-core-engine/src/api/routes.py`:
```python
@router.get("/usage")
async def get_usage(
    api_key: str = Depends(get_api_key),
    from_date: str | None = None,
    to_date: str | None = None,
):
    """Get token usage within date range."""
    from src.tracking.tracker import get_usage_summary
    summary = await get_usage_summary(from_date=from_date, to_date=to_date)
    return {"usage": summary}


@router.get("/usage/summary")
async def get_usage_summary_endpoint(
    api_key: str = Depends(get_api_key),
    group_by: str = "agent_template",
):
    """Get aggregated usage grouped by field."""
    from src.tracking.tracker import get_usage_summary
    summary = await get_usage_summary(group_by=group_by)
    return {"summary": summary}
```

- [ ] **Step 2: Commit**

```bash
git add stourio-core-engine/src/api/routes.py
git commit -m "feat: add /api/usage and /api/usage/summary endpoints"
```

---

## Phase 8: Agent Concurrency & Specialization

### Task 8.1: Create AgentPool

**Files:**
- Create: `stourio-core-engine/src/orchestrator/concurrency.py`

- [ ] **Step 1: Implement AgentPool**

Create `stourio-core-engine/src/orchestrator/concurrency.py`:
```python
import asyncio
import logging
from collections import defaultdict

from src.agents.runtime import execute_agent
from src.persistence import audit

logger = logging.getLogger("stourio.orchestrator.concurrency")


class StepResult(BaseModel):
    conclusion: str = ""
    resolution_status: str = ""
    raw_output: str = ""
    agent_template: str = ""


class ChainContext:
    def __init__(self, original_input: dict):
        self.steps: dict[str, StepResult] = {}
        self.previous: StepResult | None = None
        self.original_input = original_input

    def to_dict(self) -> dict:
        return {
            "steps": {k: v.model_dump() for k, v in self.steps.items()},
            "previous": self.previous.model_dump() if self.previous else None,
            "original_input": self.original_input,
        }


class AgentStep(BaseModel):
    agent_template: str
    input_mapping: dict[str, str] = {}
    condition: str | None = None


class ChainDefinition(BaseModel):
    name: str
    description: str = ""
    type: str = "pipeline"          # "pipeline" or "dag"
    steps: list[AgentStep] = []     # For pipelines
    nodes: dict[str, AgentStep] = {}   # For DAGs
    edges: list[tuple[str, str]] = []  # For DAGs


def _evaluate_condition(condition: str, ctx: ChainContext) -> bool:
    """Evaluate a Jinja2 condition against chain context."""
    if not condition:
        return True
    try:
        rendered = Template(condition).render(**ctx.to_dict())
        return rendered.strip().lower() not in ("", "false", "none", "0")
    except Exception as e:
        logger.warning(f"Condition evaluation failed: {condition} -> {e}")
        return False


def _resolve_input_mapping(mapping: dict[str, str], ctx: ChainContext) -> dict:
    """Resolve Jinja2 input mappings."""
    resolved = {}
    for key, template_str in mapping.items():
        try:
            resolved[key] = Template(template_str).render(**ctx.to_dict())
        except Exception as e:
            logger.warning(f"Input mapping failed for '{key}': {e}")
            resolved[key] = ""
    return resolved


async def execute_pipeline(
    chain: ChainDefinition,
    context: dict,
    input_id: str = "",
    conversation_id: str = "",
) -> dict:
    """Execute a sequential pipeline of agents."""
    chain_id = new_id()
    ctx = ChainContext(original_input=context)

    await audit.log("CHAIN_STARTED", f"Pipeline '{chain.name}' started", input_id=input_id, execution_id=chain_id)

    for i, step in enumerate(chain.steps):
        step_key = str(i)

        # Check condition
        if not _evaluate_condition(step.condition, ctx):
            logger.info(f"Step {i} ({step.agent_template}) skipped: condition not met")
            await audit.log("CHAIN_STEP_SKIPPED", f"Step {i} skipped", execution_id=chain_id)
            continue

        # Resolve input mapping
        extra_context = _resolve_input_mapping(step.input_mapping, ctx)
        objective = context.get("signal", "") + "\n" + "\n".join(
            f"{k}: {v}" for k, v in extra_context.items()
        )

        # Execute agent
        result = await execute_agent(
            agent_type=step.agent_template,
            objective=objective.strip(),
            context={**context, **extra_context, "chain_id": chain_id, "step": i},
            input_id=input_id,
            conversation_id=conversation_id,
        )

        step_result = StepResult(
            conclusion=result.result or "",
            resolution_status=result.status.value,
            raw_output=result.result or "",
            agent_template=step.agent_template,
        )
        ctx.steps[step_key] = step_result
        ctx.previous = step_result

    await audit.log("CHAIN_COMPLETED", f"Pipeline '{chain.name}' completed", execution_id=chain_id)
    return {"id": chain_id, "summary": ctx.previous.conclusion if ctx.previous else "", "steps": ctx.steps}


async def execute_dag(
    chain: ChainDefinition,
    context: dict,
    input_id: str = "",
    conversation_id: str = "",
) -> dict:
    """Execute a DAG of agents with parallel independent nodes."""
    chain_id = new_id()
    ctx = ChainContext(original_input=context)

    await audit.log("CHAIN_STARTED", f"DAG '{chain.name}' started", input_id=input_id, execution_id=chain_id)

    # Build adjacency and in-degree
    in_degree: dict[str, int] = {node_id: 0 for node_id in chain.nodes}
    children: dict[str, list[str]] = {node_id: [] for node_id in chain.nodes}
    for src, dst in chain.edges:
        in_degree[dst] = in_degree.get(dst, 0) + 1
        children[src].append(dst)

    # Topological execution
    completed: set[str] = set()
    pending = {nid for nid, deg in in_degree.items() if deg == 0}

    while pending:
        # Run all pending nodes in parallel
        tasks = {}
        for node_id in pending:
            step = chain.nodes[node_id]
            extra_context = _resolve_input_mapping(step.input_mapping, ctx)
            objective = context.get("signal", "") + "\n" + "\n".join(
                f"{k}: {v}" for k, v in extra_context.items()
            )
            tasks[node_id] = asyncio.create_task(
                execute_agent(
                    agent_type=step.agent_template,
                    objective=objective.strip(),
                    context={**context, **extra_context, "chain_id": chain_id, "node": node_id},
                    input_id=input_id,
                    conversation_id=conversation_id,
                )
            )

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        new_pending: set[str] = set()
        for node_id, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.error(f"DAG node {node_id} failed: {result}")
                ctx.steps[node_id] = StepResult(resolution_status="failed", agent_template=chain.nodes[node_id].agent_template)
            else:
                ctx.steps[node_id] = StepResult(
                    conclusion=result.result or "",
                    resolution_status=result.status.value,
                    raw_output=result.result or "",
                    agent_template=chain.nodes[node_id].agent_template,
                )
            completed.add(node_id)

            for child in children.get(node_id, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    new_pending.add(child)

        pending = new_pending

    await audit.log("CHAIN_COMPLETED", f"DAG '{chain.name}' completed", execution_id=chain_id)
    last = list(ctx.steps.values())[-1] if ctx.steps else StepResult()
    return {"id": chain_id, "summary": last.conclusion, "steps": ctx.steps}


async def execute_chain(chain_name: str, context: dict, input_id: str = "", conversation_id: str = "") -> dict:
    """Load and execute a named chain."""
    chain = get_chain(chain_name)
    if not chain:
        return {"error": f"Chain '{chain_name}' not found"}

    if chain.type == "dag":
        return await execute_dag(chain, context, input_id, conversation_id)
    return await execute_pipeline(chain, context, input_id, conversation_id)


# --- Chain Registry ---
_chains: dict[str, ChainDefinition] = {}


def load_chains(config_path: str | None = None) -> dict[str, ChainDefinition]:
    """Load chain definitions from YAML config."""
    global _chains
    path = config_path or settings.chains_config_path
    if not os.path.isfile(path):
        logger.warning(f"Chains config not found: {path}")
        return _chains

    with open(path, "r") as f:
        config = yaml.safe_load(f) or {}

    for name, defn in config.get("chains", {}).items():
        steps = [AgentStep(**s) for s in defn.get("steps", [])]
        nodes = {k: AgentStep(**v) for k, v in defn.get("nodes", {}).items()}
        edges = [tuple(e) for e in defn.get("edges", [])]
        _chains[name] = ChainDefinition(
            name=name,
            description=defn.get("description", ""),
            type=defn.get("type", "pipeline"),
            steps=steps,
            nodes=nodes,
            edges=edges,
        )
    logger.info(f"Loaded {len(_chains)} chain definitions")
    return _chains


def get_chain(name: str) -> ChainDefinition | None:
    return _chains.get(name)


def list_chains() -> list[str]:
    return list(_chains.keys())
```

- [ ] **Step 2: Commit**

```bash
git add stourio-core-engine/src/orchestrator/chains.py
git commit -m "feat: add multi-agent chaining with pipeline and DAG execution"
```

---

### Task 8.2: Add route_to_chain and FORCE_CHAIN to Orchestrator

**Files:**
- Modify: `stourio-core-engine/src/orchestrator/core.py`

- [ ] **Step 1: Read core.py**

Read `stourio-core-engine/src/orchestrator/core.py`.

- [ ] **Step 2: Add route_to_chain tool and FORCE_CHAIN handler**

Add import:
```python
from src.orchestrator.chains import execute_chain, list_chains
```

Add the 5th routing tool to `ROUTING_TOOLS`:
```python
ToolDefinition(
    name="route_to_chain",
    description="Route to a multi-agent chain for complex workflows requiring multiple agents",
    parameters={
        "type": "object",
        "properties": {
            "chain_name": {"type": "string", "description": "Name of the chain to execute"},
            "context": {"type": "string", "description": "Additional context for the chain"},
        },
        "required": ["chain_name"],
    },
)
```

Replace the `FORCE_AGENT` pass-through (lines 210-212) with direct dispatch:
```python
        if matched_rule.action == RuleAction.FORCE_AGENT:
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
            return {"status": "ok", "message": result.get("summary", ""), "execution_id": result.get("id", ""), "type": "chain"}
```

In the routing decision handler (where tool calls are parsed), add handling for `route_to_chain`:
```python
    elif tool_name == "route_to_chain":
        chain_name = tool_args.get("chain_name")
        result = await execute_chain(
            chain_name=chain_name,
            context={"signal": signal.content, "extra": tool_args.get("context", "")},
            input_id=signal.id,
            conversation_id=signal.conversation_id,
        )
        return {"status": "ok", "message": result.get("summary", ""), "execution_id": result.get("id", ""), "type": "chain"}
```

Update the system prompt to include available chains:
```python
chain_names = ", ".join(list_chains())
```
And append to the system prompt format string.

- [ ] **Step 3: Commit**

```bash
git add stourio-core-engine/src/orchestrator/core.py
git commit -m "feat: add route_to_chain tool and FORCE_CHAIN/FORCE_AGENT direct dispatch"
```

---

## Phase 9: Multi-Agent Chaining

### Task 9.1: Create Chain Models and Context

**Files:**
- Create: `stourio-core-engine/src/orchestrator/chains.py`

- [ ] **Step 1: Implement chain models and execution**

Create `stourio-core-engine/src/orchestrator/chains.py`:
```python
import os
import asyncio
import logging
from datetime import datetime, timezone

import yaml
from jinja2 import Template
from pydantic import BaseModel

from src.models.schemas import new_id
from src.orchestrator.concurrency import get_pool
from src.persistence import audit
from src.config import settings

logger = logging.getLogger("stourio.orchestrator.chains")


class AgentPool:
    """Manages concurrent agent instances per template type."""

    def __init__(self, config: dict[str, int], default_limit: int = 3):
        self._semaphores: dict[str, asyncio.Semaphore] = {
            agent_type: asyncio.Semaphore(limit)
            for agent_type, limit in config.items()
        }
        self._default_limit = default_limit
        self._queued: dict[str, int] = defaultdict(int)

    def _get_semaphore(self, agent_type: str) -> asyncio.Semaphore:
        if agent_type not in self._semaphores:
            self._semaphores[agent_type] = asyncio.Semaphore(self._default_limit)
        return self._semaphores[agent_type]

    async def execute(self, agent_type: str, **kwargs):
        sem = self._get_semaphore(agent_type)

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
        return {
            agent_type: {
                "capacity": sem._value,
                "queued": self._queued.get(agent_type, 0),
            }
            for agent_type, sem in self._semaphores.items()
        }


# Global pool instance
_pool: AgentPool | None = None


def get_pool() -> AgentPool:
    global _pool
    if _pool is None:
        from src.config import settings
        _pool = AgentPool(
            config=settings.agent_concurrency_config,
            default_limit=settings.agent_concurrency_default,
        )
    return _pool
```

- [ ] **Step 2: Commit**

```bash
git add stourio-core-engine/src/orchestrator/concurrency.py
git commit -m "feat: add AgentPool with per-type semaphore concurrency control"
```

---

### Task 9.2: Route Agent Calls Through Pool

**Files:**
- Modify: `stourio-core-engine/src/orchestrator/core.py`

- [ ] **Step 1: Replace direct execute_agent calls with pool.execute**

In `stourio-core-engine/src/orchestrator/core.py`:

Add import:
```python
from src.orchestrator.concurrency import get_pool
```

Replace all `execute_agent(` calls with `get_pool().execute(`:
- The LLM-routed agent path
- The FORCE_AGENT path
- Any approval-resumed path

The `execute_agent` signature matches `pool.execute` (pool passes `agent_type` as first arg, everything else as kwargs).

- [ ] **Step 2: Add pool status to /api/status endpoint**

In `stourio-core-engine/src/api/routes.py`, in the `/status` endpoint, add:
```python
from src.orchestrator.concurrency import get_pool
# ... in the response dict:
"agent_pool": get_pool().status(),
```

- [ ] **Step 3: Commit**

```bash
git add stourio-core-engine/src/orchestrator/core.py stourio-core-engine/src/api/routes.py
git commit -m "feat: route all agent executions through concurrency pool"
```

---

### Task 9.3: Config-Driven Agent Templates

**Files:**
- Modify: `stourio-core-engine/src/agents/runtime.py`
- Create: `stourio-core-engine/config/agents/diagnose_repair.yaml`
- Create: `stourio-core-engine/config/agents/escalate.yaml`
- Create: `stourio-core-engine/config/agents/take_action.yaml`

- [ ] **Step 1: Create default agent template YAML files**

Create `stourio-core-engine/config/agents/diagnose_repair.yaml`:
```yaml
id: diagnose_repair
description: Diagnose system issues and apply fixes
system_prompt: |
  You are an expert SRE agent. Your job is to diagnose system issues and apply fixes.
  Use available tools to gather data, identify root causes, and execute remediation.
  Always check runbooks before taking action.
tools:
  - search_knowledge
  - get_system_metrics
  - get_recent_logs
  - execute_remediation
provider_override: anthropic/claude-3-5-sonnet-latest
max_steps: 8
```

Create `stourio-core-engine/config/agents/escalate.yaml`:
```yaml
id: escalate
description: Summarize and escalate to human operators
system_prompt: |
  You are an escalation agent. Summarize the situation clearly and notify the appropriate team.
tools:
  - send_notification
provider_override: openai/gpt-4o
max_steps: 4
```

Create `stourio-core-engine/config/agents/take_action.yaml`:
```yaml
id: take_action
description: General-purpose operations agent
system_prompt: |
  You are a general operations agent. Execute lookups, generate reports, and call APIs as needed.
tools:
  - call_api
  - generate_report
  - search_knowledge
provider_override: google/gemini-3.1-pro-preview
max_steps: 6
```

- [ ] **Step 2: Add YAML template loading to runtime.py**

Add a function to `stourio-core-engine/src/agents/runtime.py`:
```python
import yaml as yaml_lib

def load_agent_templates(directory: str | None = None) -> dict[str, AgentTemplate]:
    """Load agent templates from YAML files. Merges with built-in defaults."""
    dir_path = directory or settings.agent_templates_dir
    templates = dict(AGENT_TEMPLATES)   # Start with built-in defaults

    if not os.path.isdir(dir_path):
        return templates

    for filename in sorted(os.listdir(dir_path)):
        if not filename.endswith((".yaml", ".yml")):
            continue
        filepath = os.path.join(dir_path, filename)
        try:
            with open(filepath, "r") as f:
                defn = yaml_lib.safe_load(f)
            if not defn or "id" not in defn:
                continue

            # Parse provider override
            provider_override = None
            model_override = None
            if "provider_override" in defn:
                parts = defn["provider_override"].split("/", 1)
                provider_override = parts[0]
                model_override = parts[1] if len(parts) > 1 else None

            # Build tool definitions from tool names
            tool_defs = []
            registry = get_registry()
            for tool_name in defn.get("tools", []):
                tool = registry.get(tool_name)
                if tool:
                    td = tool.to_tool_definition()
                    tool_defs.append(ToolDefinition(
                        name=td["name"],
                        description=td["description"],
                        parameters=td["parameters"],
                    ))

            template = AgentTemplate(
                id=defn["id"],
                description=defn.get("description", ""),
                system_prompt=defn.get("system_prompt", ""),
                tools=tool_defs,
                max_steps=defn.get("max_steps", 8),
                provider_override=provider_override,
                model_override=model_override,
            )
            templates[defn["id"]] = template
            logger.info(f"Loaded agent template: {defn['id']} from {filepath}")
        except Exception as e:
            logger.error(f"Failed to load agent template {filepath}: {e}")

    return templates
```

Add import for `os` and `get_registry`:
```python
import os
from src.plugins.registry import get_registry
```

- [ ] **Step 3: Commit**

```bash
git add stourio-core-engine/config/agents/ stourio-core-engine/src/agents/runtime.py
git commit -m "feat: add config-driven agent templates loaded from YAML"
```

---

## Phase 10: Admin Panel Cost Dashboard

### Task 10.1: Add Cost Dashboard Tab

**Files:**
- Modify: `stourio-core-engine/static/index.html` (or equivalent admin panel file)

- [ ] **Step 1: Read current admin panel**

Read the admin panel static files to understand current structure.

- [ ] **Step 2: Add cost dashboard tab**

Add a new tab to the admin panel HTML with:
- Chart.js CDN: `<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>`
- Time range toggle buttons (24h, 7d, 30d)
- Bar chart for cost by provider
- Table for cost by agent
- Cache hit rate display
- Recent calls table

The tab fetches from `/api/usage` and `/api/usage/summary` endpoints.

- [ ] **Step 3: Commit**

```bash
git add stourio-core-engine/static/
git commit -m "feat: add cost dashboard tab to admin panel"
```

---

## Phase 11: Integration Tests

### Task 11.1: Create Test Fixtures

**Files:**
- Create: `stourio-core-engine/tests/conftest.py`

- [ ] **Step 1: Create shared test fixtures**

Create `stourio-core-engine/tests/conftest.py`:
```python
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.adapters.base import LLMResponse


@pytest.fixture
def mock_llm_adapter():
    """Returns an adapter with predetermined responses."""
    adapter = AsyncMock()
    adapter.provider_name = "test"
    adapter.model = "test-model"
    adapter.complete.return_value = LLMResponse(
        text="Test response",
        tool_calls=None,
        raw={},
    )
    return adapter


@pytest.fixture
def mock_llm_with_tool_call():
    """Returns adapter that makes a tool call then responds."""
    adapter = AsyncMock()
    adapter.provider_name = "test"
    adapter.complete.side_effect = [
        LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_1",
                "name": "search_knowledge",
                "arguments": {"query": "redis troubleshooting"},
            }],
            raw={},
        ),
        LLMResponse(text="Issue resolved.", tool_calls=None, raw={}),
    ]
    return adapter


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.get.return_value = None
    redis.setex.return_value = True
    redis.delete.return_value = True
    return redis
```

- [ ] **Step 2: Create test for rules engine**

Create `stourio-core-engine/tests/test_rules_engine.py`:
```python
import pytest
from src.rules.engine import evaluate, _sanitize_and_normalize
from src.models.schemas import Rule, RuleAction


def _make_rule(pattern: str, action: RuleAction = RuleAction.HARD_REJECT) -> Rule:
    return Rule(
        id="test-rule",
        name="test",
        pattern=pattern,
        action=action,
        risk_level="HIGH",
    )


def test_evaluate_regex_match():
    rules = [_make_rule(r"DROP\s+TABLE")]
    result = evaluate("Please DROP TABLE users", rules)
    assert result is not None
    assert result.action == RuleAction.HARD_REJECT


def test_evaluate_no_match():
    rules = [_make_rule(r"DROP\s+TABLE")]
    result = evaluate("Select all users from database", rules)
    assert result is None


def test_evaluate_keyword_match():
    rules = [_make_rule("rm -rf")]
    result = evaluate("Run rm -rf /tmp/cache", rules)
    assert result is not None


def test_sanitize_strips_comments():
    result = _sanitize_and_normalize("hello /* comment */ world")
    assert "comment" not in result
```

- [ ] **Step 3: Commit**

```bash
git add stourio-core-engine/tests/
git commit -m "test: add conftest fixtures and rules engine tests"
```

---

### Task 11.2: Add Remaining Test Files

**Files:**
- Create: `stourio-core-engine/tests/test_orchestrator.py`
- Create: `stourio-core-engine/tests/test_approvals.py`

- [ ] **Step 1: Create orchestrator test**

Create `stourio-core-engine/tests/test_orchestrator.py`:
```python
import pytest
from unittest.mock import AsyncMock, patch
from src.models.schemas import OrchestratorInput


@pytest.mark.asyncio
async def test_kill_switch_blocks_processing():
    with patch("src.orchestrator.core.check_kill_switch", return_value=True):
        from src.orchestrator.core import process
        signal = OrchestratorInput(content="test", source="USER")
        result = await process(signal)
        assert result["status"] == "halted"
```

- [ ] **Step 2: Create approvals test**

Create `stourio-core-engine/tests/test_approvals.py`:
```python
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_notification_dispatched_on_approval():
    """Test that the notification dispatcher routes correctly."""
    from src.notifications.dispatcher import NotificationDispatcher
    from src.models.schemas import Notification, NotificationResult

    dispatcher = NotificationDispatcher()
    mock_notifier = AsyncMock()
    mock_notifier.name = "test"
    mock_notifier.send.return_value = NotificationResult(success=True, channel="test")
    dispatcher.register_channel("test-channel", mock_notifier)

    result = await dispatcher.send(Notification(
        channel="test-channel",
        message="Approval required: restart prod-db",
        severity="warning",
    ))
    assert result.success is True
    mock_notifier.send.assert_called_once()
```

- [ ] **Step 3: Commit**

```bash
git add stourio-core-engine/tests/
git commit -m "test: add orchestrator and approval test stubs"
```

---

## Phase 12: Final Wiring & Startup

### Task 12.1: Wire Everything into main.py Lifespan

**Files:**
- Modify: `stourio-core-engine/src/main.py`

- [ ] **Step 1: Read current main.py**

- [ ] **Step 2: Add all initialization to lifespan**

Ensure the lifespan function in `main.py` initializes everything in order:
1. `await init_db()` (creates pgvector extension + tables)
2. `await seed_default_rules()`
3. `init_registry()` (plugin system)
4. RAG pipeline (embedder, reranker, retriever, runbook ingestion)
5. `load_chains()` (multi-agent chains)
6. `load_agent_templates()` (YAML agent templates)
7. Start signal consumer worker
8. Start escalation worker

- [ ] **Step 3: Create sample config files**

Create `stourio-core-engine/config/notifications.yaml`:
```yaml
notification_channels:
  default-webhook:
    type: webhook
    url: "${NOTIFICATION_WEBHOOK_URL}"
```

Create `stourio-core-engine/config/chains.yaml`:
```yaml
chains:
  incident_response:
    type: pipeline
    description: Standard incident response pipeline
    steps:
      - agent: diagnose_repair
        input_mapping: {}
      - agent: escalate
        input_mapping:
          diagnosis: "{{ previous.conclusion }}"
        condition: "{{ previous.resolution_status == 'escalated' }}"
```

- [ ] **Step 4: Commit**

```bash
git add stourio-core-engine/src/main.py stourio-core-engine/config/
git commit -m "feat: wire all v2 subsystems into application startup"
```

---

### Task 12.2: Verify Full Stack

- [ ] **Step 1: Run all tests**

```bash
cd /Users/catalinstour/Documents/Intelligence/stourio-engine/stourio-core-engine
python -m pytest tests/ -v --asyncio-mode=auto
```

- [ ] **Step 2: Docker build test**

```bash
docker-compose build stourio
```

- [ ] **Step 3: Review all new files match spec**

Verify file structure matches the spec's directory layout. Check that all 9 subsystems are wired correctly.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: stourio engine v2 — complete implementation of all 9 subsystems"
```

---

## Deferred Items (Not in this plan — implement later)

These are mentioned in the spec but intentionally deferred to keep scope focused:

- **`POST /api/documents/reindex`** — full re-ingestion endpoint for embedder switching (drop all chunks, recreate column, re-embed). Add when actually switching embedders.
- **`voyage_embedder.py`** — Voyage AI embedder implementation. The `voyageai` dependency is included but the adapter is deferred until needed. Remove `voyageai` from requirements.txt until implemented.
- **`llm_reranker.py`** — Re-ranker using existing LLM providers. Deferred; Cohere reranker is the default.
- **`local_reranker.py`** — Local cross-encoder re-ranker. Deferred; adds ~500MB model dependency.
- **`track_usage()` calls from adapters** — Each adapter's `complete()` method should call `track_usage()` after every LLM call. This must be added to `openai_adapter.py`, `anthropic_adapter.py`, and `google_adapter.py` after `LLMResponse` is converted to include `usage: TokenUsage`. Also add tracking in the orchestrator routing path and agent tool loop.
