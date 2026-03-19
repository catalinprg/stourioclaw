from __future__ import annotations
import logging
from sqlalchemy import select
from src.models.schemas import ChatMessage, new_id
from src.persistence.database import async_session, ConversationMessage

logger = logging.getLogger("stourio.conversations")


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


async def summarize_and_compact(
    conversation_id: str,
    keep_recent: int = 10,
    summary_model: str | None = None,
) -> str | None:
    """Summarize old messages and replace them with a single summary message.

    Keeps the most recent `keep_recent` messages verbatim.
    Older messages are summarized via LLM and replaced with one summary message.
    Returns the summary text or None if not enough messages to summarize.
    """
    async with async_session() as session:
        from sqlalchemy import func
        count_result = await session.execute(
            select(func.count(ConversationMessage.id))
            .where(ConversationMessage.conversation_id == conversation_id)
        )
        total = count_result.scalar()

        if total <= keep_recent + 5:  # Not enough to bother summarizing
            return None

        # Get all messages ordered by time
        result = await session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.timestamp.asc())
        )
        all_messages = result.scalars().all()

        # Split: old messages to summarize, recent to keep
        old_messages = all_messages[:-keep_recent]

        if len(old_messages) < 5:
            return None

        # Build text to summarize
        text_to_summarize = "\n".join(
            f"[{m.role}]: {m.content}" for m in old_messages
        )

        # Call LLM to summarize
        from src.adapters.registry import get_orchestrator_adapter
        from src.models.schemas import ChatMessage

        adapter = get_orchestrator_adapter()
        summary_prompt = (
            "Summarize this conversation history into a concise summary. "
            "Preserve key facts, decisions, and context that would be important for future reference. "
            "Be concise but complete."
        )

        response = await adapter.complete(
            system_prompt=summary_prompt,
            messages=[ChatMessage(role="user", content=text_to_summarize)],
        )

        summary_text = response.text or "No summary generated."

        # Delete old messages
        from sqlalchemy import delete
        old_ids = [m.id for m in old_messages]
        await session.execute(
            delete(ConversationMessage)
            .where(ConversationMessage.id.in_(old_ids))
        )

        # Insert summary as first message
        summary_msg = ConversationMessage(
            id=new_id(),
            conversation_id=conversation_id,
            role="system",
            content=f"[Conversation Summary]\n{summary_text}",
            source="summarizer",
        )
        session.add(summary_msg)
        await session.commit()

        logger.info(
            "Summarized %d messages for conversation %s (kept %d recent)",
            len(old_messages), conversation_id, keep_recent,
        )
        return summary_text
