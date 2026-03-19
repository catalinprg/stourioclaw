from __future__ import annotations
from sqlalchemy import select
from src.models.schemas import ChatMessage, new_id
from src.persistence.database import async_session, ConversationMessage


async def get_history(conversation_id: str, limit: int = 20) -> list[ChatMessage]:
    """Get conversation history."""
    async with async_session() as session:
        result = await session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.timestamp.desc())
            .limit(limit)
        )
        rows = result.scalars().all()
        return [
            ChatMessage(role=r.role, content=r.content) for r in reversed(rows)
        ]


async def save_message(conversation_id: str, role: str, content: str) -> None:
    """Save a message to conversation history."""
    async with async_session() as session:
        msg = ConversationMessage(
            id=new_id(),
            conversation_id=conversation_id,
            role=role,
            content=content,
        )
        session.add(msg)
        await session.commit()
