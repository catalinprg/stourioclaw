"""Add tsvector column for BM25 full-text search.

Revision ID: 004
Revises: 003
"""
from __future__ import annotations
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS tsv tsvector")
    op.execute("UPDATE document_chunks SET tsv = to_tsvector('english', coalesce(content, ''))")
    op.execute("CREATE INDEX IF NOT EXISTS idx_document_chunks_tsv ON document_chunks USING GIN(tsv)")
    op.execute("""
        CREATE OR REPLACE FUNCTION update_tsv() RETURNS trigger AS $$
        BEGIN
            NEW.tsv := to_tsvector('english', coalesce(NEW.content, ''));
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS tsv_update ON document_chunks;
        CREATE TRIGGER tsv_update BEFORE INSERT OR UPDATE ON document_chunks
        FOR EACH ROW EXECUTE FUNCTION update_tsv();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tsv_update ON document_chunks")
    op.execute("DROP FUNCTION IF EXISTS update_tsv()")
    op.execute("DROP INDEX IF EXISTS idx_document_chunks_tsv")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS tsv")
