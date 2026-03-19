"""Initial schema — clean install with all tables.

Revision ID: 001
Revises: None
Create Date: 2026-03-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("action", sa.String(), nullable=False, index=True),
        sa.Column("detail", sa.Text(), server_default=""),
        sa.Column("input_id", sa.String(), index=True),
        sa.Column("execution_id", sa.String(), index=True),
        sa.Column("risk_level", sa.String()),
        sa.Column("timestamp", sa.DateTime(), server_default=sa.func.now(), index=True),
        sa.Column("agent_id", sa.String(), nullable=True),
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("conversation_id", sa.String(), nullable=False, index=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("source", sa.String(), server_default="api"),
        sa.Column("agent_id", sa.String(), nullable=True),
    )

    op.create_table(
        "rules",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("pattern", sa.String(), nullable=False),
        sa.Column("pattern_type", sa.String(), server_default="regex"),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("risk_level", sa.String(), server_default="medium"),
        sa.Column("automation_id", sa.String(), nullable=True),
        sa.Column("config", sa.JSON(), server_default="{}"),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("action_description", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String()),
        sa.Column("blast_radius", sa.String(), server_default=""),
        sa.Column("reasoning", sa.Text(), server_default=""),
        sa.Column("original_input_id", sa.String()),
        sa.Column("status", sa.String(), server_default="pending", index=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_note", sa.Text(), server_default=""),
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_path", sa.String(500)),
        sa.Column("title", sa.String(500)),
        sa.Column("section_header", sa.String(500)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), server_default="{}"),
        sa.Column("embedding", postgresql.ARRAY(sa.Float()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "token_usage",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("execution_id", sa.String(100)),
        sa.Column("conversation_id", sa.String(100)),
        sa.Column("agent_template", sa.String(100)),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 6)),
        sa.Column("call_type", sa.String(20)),
        sa.Column("cached_hit", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("units_used", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("openrouter_model", sa.String(), nullable=True),
    )

    op.create_table(
        "agents",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), unique=True, nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("system_prompt", sa.Text(), server_default=""),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("tools", sa.JSON(), server_default="[]"),
        sa.Column("max_steps", sa.Integer(), server_default="8"),
        sa.Column("max_concurrent", sa.Integer(), server_default="3"),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "security_alerts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("alert_type", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("source_agent", sa.String(), server_default=""),
        sa.Column("source_execution_id", sa.String(), server_default=""),
        sa.Column("raw_evidence", sa.JSON(), server_default="{}"),
        sa.Column("status", sa.String(), server_default="OPEN", index=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("security_alerts")
    op.drop_table("agents")
    op.drop_table("token_usage")
    op.drop_table("document_chunks")
    op.drop_table("approvals")
    op.drop_table("rules")
    op.drop_table("conversation_messages")
    op.drop_table("audit_log")
