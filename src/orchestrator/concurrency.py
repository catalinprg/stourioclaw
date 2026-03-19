import asyncio
import logging
from collections import defaultdict
from src.agents.runtime import execute_agent
from src.persistence import audit
from src.persistence.database import async_session

logger = logging.getLogger("stourio.orchestrator.concurrency")


class AgentPool:
    def __init__(self, config: dict[str, int], default_limit: int = 3):
        self._semaphores: dict[str, asyncio.Semaphore] = {
            agent_type: asyncio.Semaphore(limit) for agent_type, limit in config.items()
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
                # Create session if not provided
                if "session" not in kwargs:
                    async with async_session() as session:
                        return await execute_agent(agent_name=agent_type, session=session, **kwargs)
                return await execute_agent(agent_name=agent_type, **kwargs)
        except Exception:
            self._queued[agent_type] = max(0, self._queued[agent_type] - 1)
            raise

    def status(self) -> dict:
        return {
            agent_type: {"capacity": sem._value, "queued": self._queued.get(agent_type, 0)}
            for agent_type, sem in self._semaphores.items()
        }


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
