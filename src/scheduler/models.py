"""Cron job Pydantic models."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator
from src.models.schemas import new_id


class CronJob(BaseModel):
    """Schema for a scheduled cron job."""
    id: str = Field(default_factory=new_id)
    name: str = Field(..., min_length=1, max_length=100)
    schedule: str = Field(..., description="Cron expression (5-field: min hour dom mon dow)")
    agent_type: str = Field(..., description="Agent to execute")
    objective: str = Field(..., description="Objective to pass to the agent")
    conversation_id: str | None = None
    active: bool = True

    @field_validator("schedule")
    @classmethod
    def validate_cron_expression(cls, v: str) -> str:
        parts = v.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Cron expression must have 5 fields, got {len(parts)}")
        return v.strip()
