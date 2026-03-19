"""Add daemon mode, messaging, and MCP client support.

Revision ID: 003
Revises: 002
Create Date: 2026-03-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("execution_mode", sa.String(20), server_default="oneshot"))
    op.add_column("agents", sa.Column("daemon_config", sa.JSON(), nullable=True))
    op.add_column("agents", sa.Column("mcp_servers", sa.JSON(), server_default="[]"))
    op.add_column("agents", sa.Column("allowed_peers", sa.JSON(), server_default="[]"))

    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("endpoint_url", sa.String(500), nullable=True),
        sa.Column("endpoint_command", sa.String(500), nullable=True),
        sa.Column("transport", sa.String(20), nullable=False),
        sa.Column("auth_env_var", sa.String(100), nullable=True),
        sa.Column("high_risk_tools", sa.JSON(), server_default="[]"),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("mcp_servers")
    op.drop_column("agents", "allowed_peers")
    op.drop_column("agents", "mcp_servers")
    op.drop_column("agents", "daemon_config")
    op.drop_column("agents", "execution_mode")
