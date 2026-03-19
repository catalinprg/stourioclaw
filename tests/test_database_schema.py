"""Tests for database schema — verifies all tables and columns exist on Base.metadata.

These tests inspect SQLAlchemy model metadata directly (no DB connection needed).
"""
from __future__ import annotations

import pytest
from src.persistence.database import Base


def _get_table(name: str):
    """Return a Table from Base.metadata by name, or fail."""
    table = Base.metadata.tables.get(name)
    assert table is not None, f"Table '{name}' not found in metadata"
    return table


def _column_names(table_name: str) -> set[str]:
    return {c.name for c in _get_table(table_name).columns}


class TestAgentsTable:
    def test_agents_table_exists(self):
        _get_table("agents")

    def test_agents_table_columns(self):
        expected = {
            "id", "name", "display_name", "description", "system_prompt",
            "model", "tools", "max_steps", "max_concurrent",
            "is_active", "is_system", "created_at", "updated_at",
        }
        actual = _column_names("agents")
        assert expected <= actual, f"Missing columns: {expected - actual}"

    def test_agents_name_is_unique(self):
        table = _get_table("agents")
        name_col = table.c.name
        # unique=True results in a unique constraint
        assert name_col.unique is True, "agents.name must be unique"

    def test_agents_model_not_nullable(self):
        table = _get_table("agents")
        assert table.c.model.nullable is False


class TestSecurityAlertsTable:
    def test_security_alerts_table_exists(self):
        _get_table("security_alerts")

    def test_security_alerts_table_columns(self):
        expected = {
            "id", "severity", "alert_type", "description",
            "source_agent", "source_execution_id", "raw_evidence",
            "status", "created_at", "resolved_at",
        }
        actual = _column_names("security_alerts")
        assert expected <= actual, f"Missing columns: {expected - actual}"

    def test_security_alerts_status_indexed(self):
        table = _get_table("security_alerts")
        assert table.c.status.index is True, "security_alerts.status must be indexed"

    def test_security_alerts_severity_not_nullable(self):
        table = _get_table("security_alerts")
        assert table.c.severity.nullable is False


class TestConversationMessagesNewColumns:
    def test_conversation_messages_has_source_and_agent(self):
        cols = _column_names("conversation_messages")
        assert "source" in cols, "conversation_messages missing 'source' column"
        assert "agent_id" in cols, "conversation_messages missing 'agent_id' column"

    def test_source_default(self):
        table = _get_table("conversation_messages")
        source_col = table.c.source
        assert source_col.default is not None
        assert source_col.default.arg == "api"


class TestAuditLogNewColumns:
    def test_audit_log_has_agent_id(self):
        cols = _column_names("audit_log")
        assert "agent_id" in cols, "audit_log missing 'agent_id' column"

    def test_agent_id_nullable(self):
        table = _get_table("audit_log")
        assert table.c.agent_id.nullable is True


class TestTokenUsageNewColumns:
    def test_token_usage_has_openrouter_model(self):
        cols = _column_names("token_usage")
        assert "openrouter_model" in cols, "token_usage missing 'openrouter_model' column"

    def test_openrouter_model_nullable(self):
        table = _get_table("token_usage")
        assert table.c.openrouter_model.nullable is True
