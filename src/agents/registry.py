"""DB-backed agent registry with CRUD operations."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.persistence.database import AgentModel

logger = logging.getLogger("stourio.agent_registry")

NON_ROUTABLE_AGENTS = {"cybersecurity", "code_reviewer"}

# Required fields in agent YAML files
REQUIRED_YAML_FIELDS = {"name", "description", "system_prompt", "tools"}


class AgentRegistry:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_active(self) -> list[AgentModel]:
        """List all active agents."""
        result = await self.session.execute(
            select(AgentModel).where(AgentModel.is_active.is_(True))
        )
        return list(result.scalars().all())

    async def list_routable(self) -> list[AgentModel]:
        """List agents the orchestrator can route to (excludes cybersecurity, code_reviewer)."""
        result = await self.session.execute(
            select(AgentModel).where(
                AgentModel.is_active.is_(True),
                AgentModel.name.notin_(NON_ROUTABLE_AGENTS),
            )
        )
        return list(result.scalars().all())

    async def get_by_name(self, name: str) -> Optional[AgentModel]:
        """Get agent by unique name."""
        result = await self.session.execute(
            select(AgentModel).where(AgentModel.name == name)
        )
        return result.scalars().first()

    async def create(self, **kwargs) -> AgentModel:
        """Create a new agent with ULID id."""
        if "id" not in kwargs:
            kwargs["id"] = str(ULID())
        agent = AgentModel(**kwargs)
        self.session.add(agent)
        await self.session.flush()
        return agent

    async def update(self, name: str, **kwargs) -> Optional[AgentModel]:
        """Update agent by name. Returns None if not found."""
        agent = await self.get_by_name(name)
        if agent is None:
            return None
        for key, value in kwargs.items():
            if hasattr(agent, key):
                setattr(agent, key, value)
        await self.session.flush()
        return agent

    async def delete(self, name: str) -> bool:
        """Delete non-system agent. Returns False if not found or is_system=True."""
        agent = await self.get_by_name(name)
        if agent is None or agent.is_system:
            return False
        await self.session.delete(agent)
        await self.session.flush()
        return True

    async def list_daemons(self) -> list[AgentModel]:
        """Return all active daemon agents."""
        result = await self.session.execute(
            select(AgentModel)
            .where(AgentModel.is_active == True)
            .where(AgentModel.execution_mode == "daemon")
        )
        return result.scalars().all()

    async def seed_from_yaml(self, config_dir: str) -> int:
        """Seed agents from YAML files if DB is empty. Returns count seeded."""
        # Check if any agents exist already
        result = await self.session.execute(select(AgentModel).limit(1))
        if result.scalars().first() is not None:
            logger.info("Agents table not empty, skipping seed")
            return 0

        config_path = Path(config_dir)
        if not config_path.is_dir():
            logger.warning("Config directory not found: %s", config_dir)
            return 0

        count = 0
        for yaml_file in sorted(config_path.glob("*.yaml")):
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)
                if not data or not isinstance(data, dict):
                    logger.warning("Skipping invalid YAML: %s", yaml_file)
                    continue

                agent = AgentModel(
                    id=str(ULID()),
                    name=data["name"],
                    display_name=data.get("display_name", data["name"].replace("_", " ").title()),
                    description=data.get("description", ""),
                    system_prompt=data.get("system_prompt", ""),
                    model=data.get("model", data.get("provider_override", "default")),
                    tools=data.get("tools", []),
                    max_steps=data.get("max_steps", 8),
                    max_concurrent=data.get("max_concurrent", 3),
                    is_active=data.get("is_active", True),
                    is_system=data.get("is_system", False),
                )
                self.session.add(agent)
                count += 1
                logger.info("Seeded agent: %s", data["name"])
            except Exception:
                logger.exception("Failed to seed from %s", yaml_file)

        if count:
            await self.session.flush()
        return count
