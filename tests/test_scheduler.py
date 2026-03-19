"""Tests for the cron job scheduler subsystem."""
from __future__ import annotations

import pytest
from src.scheduler.models import CronJob


def test_cron_job_schema_defaults():
    job = CronJob(
        name="daily-report",
        schedule="0 9 * * *",
        agent_type="analyst",
        objective="Generate daily summary report",
    )
    assert job.name == "daily-report"
    assert job.schedule == "0 9 * * *"
    assert job.active is True
    assert job.conversation_id is None


def test_cron_job_schema_rejects_empty_name():
    with pytest.raises(Exception):
        CronJob(name="", schedule="* * * * *", agent_type="analyst", objective="x")


from unittest.mock import AsyncMock, MagicMock, patch
from src.scheduler.store import CronStore


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_create_cron_job(mock_session):
    store = CronStore(mock_session)
    job = CronJob(
        name="test-job",
        schedule="*/5 * * * *",
        agent_type="assistant",
        objective="Say hello",
    )
    with patch.object(store, "_compute_next_run", return_value=None):
        await store.create(job)
    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_list_active_jobs(mock_session):
    store = CronStore(mock_session)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)
    jobs = await store.list_active()
    assert jobs == []


@pytest.mark.asyncio
async def test_delete_cron_job_not_found(mock_session):
    store = CronStore(mock_session)
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)
    result = await store.delete("nonexistent")
    assert result is False
