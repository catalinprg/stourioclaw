from __future__ import annotations
import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, String, Text, DateTime, Boolean, JSON, func
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


# --- Init ---

async def init_db():
    """Create all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created")


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
