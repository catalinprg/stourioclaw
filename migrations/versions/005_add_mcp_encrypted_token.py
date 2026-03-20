"""Add encrypted auth token column to mcp_servers.

Revision ID: 005
Revises: 004
Create Date: 2026-03-20
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("mcp_servers", sa.Column("auth_token_encrypted", sa.Text(), nullable=True))

def downgrade() -> None:
    op.drop_column("mcp_servers", "auth_token_encrypted")
