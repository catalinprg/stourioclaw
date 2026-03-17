from __future__ import annotations
import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, String, Text, DateTime, Boolean, JSON, func, text, Integer, Numeric
from pgvector.sqlalchemy import Vector
from src.config import settings

logger = logging.getLogger("stourio.db")

engine = create_async_engine(settings.database_url, echo=False, pool_size=10)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# --- Tables ---

class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(String, primary_key=True)
    action = Column(String, nullable=False, index=True)
    detail = Column(Text, default="")
    input_id = Column(String, index=True)
    execution_id = Column(String, index=True)
    risk_level = Column(String)
    timestamp = Column(DateTime, server_default=func.now(), index=True)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id = Column(String, primary_key=True)
    conversation_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, server_default=func.now())


class RuleRecord(Base):
    __tablename__ = "rules"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    pattern = Column(String, nullable=False)
    pattern_type = Column(String, default="regex")
    action = Column(String, nullable=False)
    risk_level = Column(String, default="medium")
    automation_id = Column(String, nullable=True)
    config = Column(JSON, default={})
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())


class ApprovalRecord(Base):
    __tablename__ = "approvals"

    id = Column(String, primary_key=True)
    action_description = Column(Text, nullable=False)
    risk_level = Column(String)
    blast_radius = Column(String, default="")
    reasoning = Column(Text, default="")
    original_input_id = Column(String)
    status = Column(String, default="pending", index=True)
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_note = Column(Text, default="")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id = Column(String, primary_key=True)
    source_type = Column(String(50), nullable=False)
    source_path = Column(String(500))
    title = Column(String(500))
    section_header = Column(String(500))
    content = Column(Text, nullable=False)
    metadata_ = Column("metadata", JSON, default={})
    embedding = Column(Vector(settings.embedding_dimension))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class TokenUsageRecord(Base):
    __tablename__ = "token_usage"
    id = Column(String, primary_key=True)
    execution_id = Column(String(100))
    conversation_id = Column(String(100))
    agent_template = Column(String(100))
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=False)
    input_tokens = Column(Integer, nullable=False)
    output_tokens = Column(Integer, nullable=False)
    total_tokens = Column(Integer, nullable=False)
    estimated_cost_usd = Column(Numeric(10, 6))
    call_type = Column(String(20))
    cached_hit = Column(Boolean, default=False)
    units_used = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# --- Init ---

async def init_db():
    """Create pgvector extension and all tables."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created (pgvector enabled)")


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
