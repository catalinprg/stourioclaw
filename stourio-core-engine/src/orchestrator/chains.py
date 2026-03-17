from __future__ import annotations
import asyncio
import logging
from typing import Optional, Any
import yaml
from jinja2 import Template
from pydantic import BaseModel
from src.models.schemas import new_id
from src.persistence import audit

logger = logging.getLogger("stourio.orchestrator.chains")


# --- Models ---

class StepResult(BaseModel):
    conclusion: str = ""
    resolution_status: str = ""
    raw_output: Any = None
    agent_template: str = ""


class ChainContext:
    def __init__(self, original_input: dict):
        self.steps: dict[str, StepResult] = {}
        self.previous: Optional[StepResult] = None
        self.original_input: dict = original_input

    def to_dict(self) -> dict:
        return {
            "steps": {k: v.model_dump() for k, v in self.steps.items()},
            "previous": self.previous.model_dump() if self.previous else {},
            "original_input": self.original_input,
        }


class AgentStep(BaseModel):
    agent_template: str
    input_mapping: dict[str, str] = {}
    condition: Optional[str] = None


class ChainDefinition(BaseModel):
    name: str
    description: str = ""
    type: str = "pipeline"  # pipeline | dag
    steps: list[AgentStep] = []
    nodes: dict[str, AgentStep] = {}
    edges: list[list[str]] = []


# --- Chain registry ---

_chains: dict[str, ChainDefinition] = {}


def load_chains(config_path: str) -> None:
    global _chains
    try:
        with open(config_path, "r") as f:
            raw = yaml.safe_load(f) or {}
        chains_raw = raw.get("chains", {})
        _chains = {}
        for name, data in chains_raw.items():
            chain_type = data.get("type", "pipeline")
            steps = []
            nodes = {}
            edges = data.get("edges", [])

            if chain_type == "pipeline":
                for s in data.get("steps", []):
                    steps.append(AgentStep(
                        agent_template=s["agent"],
                        input_mapping=s.get("input_mapping", {}),
                        condition=s.get("condition"),
                    ))
            else:
                # DAG: nodes dict
                for node_name, node_data in data.get("nodes", {}).items():
                    nodes[node_name] = AgentStep(
                        agent_template=node_data["agent"],
                        input_mapping=node_data.get("input_mapping", {}),
                        condition=node_data.get("condition"),
                    )

            _chains[name] = ChainDefinition(
                name=name,
                description=data.get("description", ""),
                type=chain_type,
                steps=steps,
                nodes=nodes,
                edges=edges,
            )
        logger.info(f"Loaded {len(_chains)} chain(s) from {config_path}")
    except FileNotFoundError:
        logger.warning(f"Chain config not found at {config_path}. No chains loaded.")
    except Exception as e:
        logger.error(f"Failed to load chains from {config_path}: {e}")
        raise


def get_chain(name: str) -> ChainDefinition:
    if name not in _chains:
        raise KeyError(f"Chain '{name}' not found. Available: {list(_chains.keys())}")
    return _chains[name]


def list_chains() -> list[ChainDefinition]:
    return list(_chains.values())


# --- Jinja2 helpers ---

def _evaluate_condition(condition: Optional[str], ctx: ChainContext) -> bool:
    if not condition:
        return True
    stripped = condition.strip()
    if stripped.lower() in ("false", "none", "0", ""):
        return False
    try:
        rendered = Template(stripped).render(**ctx.to_dict())
        rendered = rendered.strip()
        if rendered.lower() in ("false", "none", "0", ""):
            return False
        return bool(rendered)
    except Exception as e:
        logger.warning(f"Condition evaluation failed: {condition!r} -> {e}")
        return False


def _resolve_input_mapping(mapping: dict[str, str], ctx: ChainContext) -> dict[str, str]:
    resolved = {}
    ctx_dict = ctx.to_dict()
    for key, template_str in mapping.items():
        try:
            resolved[key] = Template(template_str).render(**ctx_dict)
        except Exception as e:
            logger.warning(f"Input mapping resolution failed for key '{key}': {e}")
            resolved[key] = template_str
    return resolved


# --- Execution helpers ---

async def _run_agent_step(
    step: AgentStep,
    ctx: ChainContext,
    input_id: str,
    conversation_id: Optional[str],
) -> StepResult:
    from src.orchestrator.concurrency import get_pool

    resolved = _resolve_input_mapping(step.input_mapping, ctx)
    objective = resolved.get("objective") or ctx.original_input.get("signal", "")
    context_payload: dict[str, Any] = {
        **ctx.original_input,
        **resolved,
        "chain_context": ctx.to_dict(),
    }

    execution = await get_pool().execute(
        agent_type=step.agent_template,
        objective=objective,
        context=context_payload,
        input_id=input_id,
        conversation_id=conversation_id,
    )

    return StepResult(
        conclusion=execution.result or "",
        resolution_status=execution.status.value,
        raw_output=execution.model_dump(),
        agent_template=step.agent_template,
    )


# --- Pipeline execution ---

async def execute_pipeline(
    chain: ChainDefinition,
    context: ChainContext,
    input_id: str,
    conversation_id: Optional[str],
) -> dict:
    execution_id = new_id()
    await audit.log(
        "CHAIN_PIPELINE_START",
        f"Chain '{chain.name}' pipeline started with {len(chain.steps)} steps",
        input_id=input_id,
        execution_id=execution_id,
    )

    for i, step in enumerate(chain.steps):
        if not _evaluate_condition(step.condition, context):
            await audit.log(
                "CHAIN_STEP_SKIPPED",
                f"Step {i} ({step.agent_template}) skipped — condition false",
                input_id=input_id,
                execution_id=execution_id,
            )
            continue

        await audit.log(
            "CHAIN_STEP_START",
            f"Step {i} ({step.agent_template}) starting",
            input_id=input_id,
            execution_id=execution_id,
        )

        result = await _run_agent_step(step, context, input_id, conversation_id)
        context.steps[f"step_{i}"] = result
        context.previous = result

        await audit.log(
            "CHAIN_STEP_DONE",
            f"Step {i} ({step.agent_template}) completed: {result.resolution_status}",
            input_id=input_id,
            execution_id=execution_id,
        )

    summary = context.previous.conclusion if context.previous else ""
    await audit.log(
        "CHAIN_PIPELINE_DONE",
        f"Chain '{chain.name}' pipeline completed",
        input_id=input_id,
        execution_id=execution_id,
    )

    return {
        "id": execution_id,
        "chain": chain.name,
        "type": "pipeline",
        "summary": summary,
        "steps": {k: v.model_dump() for k, v in context.steps.items()},
    }


# --- DAG execution ---

def _topological_sort(nodes: list[str], edges: list[list[str]]) -> list[list[str]]:
    """Return nodes grouped into execution levels via Kahn's algorithm."""
    from collections import deque, defaultdict

    in_degree: dict[str, int] = {n: 0 for n in nodes}
    adjacency: dict[str, list[str]] = defaultdict(list)

    for edge in edges:
        if len(edge) == 2:
            src, dst = edge
            adjacency[src].append(dst)
            in_degree[dst] = in_degree.get(dst, 0) + 1

    queue: deque[str] = deque(n for n in nodes if in_degree[n] == 0)
    levels: list[list[str]] = []

    while queue:
        level = list(queue)
        levels.append(level)
        next_queue: deque[str] = deque()
        for node in level:
            for neighbor in adjacency[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    next_queue.append(neighbor)
        queue = next_queue

    return levels


async def execute_dag(
    chain: ChainDefinition,
    context: ChainContext,
    input_id: str,
    conversation_id: Optional[str],
) -> dict:
    execution_id = new_id()
    await audit.log(
        "CHAIN_DAG_START",
        f"Chain '{chain.name}' DAG started with {len(chain.nodes)} nodes",
        input_id=input_id,
        execution_id=execution_id,
    )

    node_names = list(chain.nodes.keys())
    levels = _topological_sort(node_names, chain.edges)

    for level in levels:
        tasks = []
        active_nodes = []
        for node_name in level:
            step = chain.nodes[node_name]
            if not _evaluate_condition(step.condition, context):
                await audit.log(
                    "CHAIN_NODE_SKIPPED",
                    f"Node '{node_name}' skipped — condition false",
                    input_id=input_id,
                    execution_id=execution_id,
                )
                continue
            active_nodes.append(node_name)
            tasks.append(_run_agent_step(step, context, input_id, conversation_id))

        if tasks:
            await audit.log(
                "CHAIN_DAG_LEVEL",
                f"Executing {len(tasks)} node(s) in parallel: {active_nodes}",
                input_id=input_id,
                execution_id=execution_id,
            )
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for node_name, result in zip(active_nodes, results):
                if isinstance(result, Exception):
                    logger.error(f"DAG node '{node_name}' failed: {result}")
                    context.steps[node_name] = StepResult(
                        conclusion=str(result),
                        resolution_status="failed",
                        agent_template=chain.nodes[node_name].agent_template,
                    )
                else:
                    context.steps[node_name] = result
                    context.previous = result

    summary = context.previous.conclusion if context.previous else ""
    await audit.log(
        "CHAIN_DAG_DONE",
        f"Chain '{chain.name}' DAG completed",
        input_id=input_id,
        execution_id=execution_id,
    )

    return {
        "id": execution_id,
        "chain": chain.name,
        "type": "dag",
        "summary": summary,
        "steps": {k: v.model_dump() for k, v in context.steps.items()},
    }


# --- Top-level dispatcher ---

async def execute_chain(
    chain_name: str,
    context: dict,
    input_id: str,
    conversation_id: Optional[str] = None,
) -> dict:
    from src.config import settings

    # Lazy-load chains if registry is empty
    if not _chains:
        load_chains(settings.chains_config_path)

    chain = get_chain(chain_name)
    chain_ctx = ChainContext(original_input=context)

    await audit.log(
        "CHAIN_EXECUTE",
        f"Executing chain '{chain_name}' (type={chain.type})",
        input_id=input_id,
    )

    if chain.type == "dag":
        return await execute_dag(chain, chain_ctx, input_id, conversation_id)
    else:
        return await execute_pipeline(chain, chain_ctx, input_id, conversation_id)
