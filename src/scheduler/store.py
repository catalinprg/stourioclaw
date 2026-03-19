"""CRUD operations for cron jobs."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.persistence.database import CronJobRecord
from src.scheduler.models import CronJob

logger = logging.getLogger("stourio.scheduler.store")


class CronStore:
    """Database operations for cron jobs."""

    def __init__(self, session: AsyncSession):
        self._session = session

    def _compute_next_run(self, schedule: str) -> datetime:
        """Compute the next run time from a cron expression."""
        now = datetime.utcnow()
        cron = croniter(schedule, now)
        return cron.get_next(datetime)

    async def create(self, job: CronJob) -> CronJobRecord:
        next_run = self._compute_next_run(job.schedule)
        record = CronJobRecord(
            id=job.id,
            name=job.name,
            schedule=job.schedule,
            agent_type=job.agent_type,
            objective=job.objective,
            conversation_id=job.conversation_id,
            active=job.active,
            next_run_at=next_run,
        )
        self._session.add(record)
        await self._session.commit()
        logger.info("Created cron job '%s' (next_run=%s)", job.name, next_run)
        return record

    async def list_active(self) -> list[CronJobRecord]:
        result = await self._session.execute(
            select(CronJobRecord).where(CronJobRecord.active == True)
        )
        return result.scalars().all()

    async def list_all(self) -> list[CronJobRecord]:
        result = await self._session.execute(
            select(CronJobRecord).order_by(CronJobRecord.created_at.desc())
        )
        return result.scalars().all()

    async def get_by_name(self, name: str) -> CronJobRecord | None:
        result = await self._session.execute(
            select(CronJobRecord).where(CronJobRecord.name == name)
        )
        return result.scalars().first()

    async def get_due_jobs(self) -> list[CronJobRecord]:
        """Return active jobs whose next_run_at is in the past."""
        now = datetime.utcnow()  # Naive UTC to match naive DB column
        result = await self._session.execute(
            select(CronJobRecord)
            .where(CronJobRecord.active == True)
            .where(CronJobRecord.next_run_at <= now)
        )
        return result.scalars().all()

    async def mark_executed(self, job: CronJobRecord) -> None:
        """Update last_run_at and compute next_run_at."""
        now = datetime.utcnow()
        job.last_run_at = now
        job.next_run_at = self._compute_next_run(job.schedule)
        await self._session.commit()

    async def delete(self, name: str) -> bool:
        result = await self._session.execute(
            select(CronJobRecord).where(CronJobRecord.name == name)
        )
        record = result.scalars().first()
        if not record:
            return False
        await self._session.delete(record)
        await self._session.commit()
        logger.info("Deleted cron job '%s'", name)
        return True

    async def toggle(self, name: str, active: bool) -> CronJobRecord | None:
        record = await self.get_by_name(name)
        if not record:
            return None
        record.active = active
        if active:
            record.next_run_at = self._compute_next_run(record.schedule)
        await self._session.commit()
        return record
