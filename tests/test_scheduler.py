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
