"""Agent-curated memory — extract and store key facts from interactions.

After each execution, the LLM extracts important facts, preferences, and
context that should be remembered for future interactions.
"""
from __future__ import annotations

import logging

from src.models.schemas import new_id, ChatMessage
from src.persistence.database import DocumentChunk, async_session
from src.rag.embeddings.base import BaseEmbedder

logger = logging.getLogger("stourio.rag.memory")

EXTRACT_PROMPT = """You are a memory curator. Extract key facts from this agent interaction that should be remembered for future reference.

Focus on:
- User preferences and patterns
- Important decisions made
- Technical context (systems, configurations, conventions)
- Action items or follow-ups
- Corrections or clarifications

Return ONLY a bullet list of facts. Each fact should be a single, self-contained statement.
If there's nothing worth remembering, respond with "NONE".

Example output:
- User prefers concise reports without charts
- Production database is PostgreSQL on AWS eu-west-1
- Deploy freeze starts 2026-03-20 for mobile release"""

async def extract_and_store_memories(
    agent_name: str,
    objective: str,
    result: str,
    steps: list[dict],
    embedder: BaseEmbedder,
    conversation_id: str | None = None,
) -> int:
    """Extract key facts from an agent execution and store as curated memories.

    Returns the number of memories stored.
    """
    if not result:
        return 0

    # Build interaction summary for the LLM
    step_summary = ""
    for s in steps[:10]:  # Cap to prevent token explosion
        if s.get("type") == "tool_call":
            step_summary += f"\n- Called {s.get('tool', 'unknown')}"
        elif s.get("type") == "response":
            step_summary += f"\n- Responded: {s.get('content', '')[:200]}"

    interaction_text = (
        f"Agent: {agent_name}\n"
        f"Objective: {objective}\n"
        f"Steps taken:{step_summary}\n"
        f"Result: {result[:1000]}"
    )

    try:
        from src.adapters.registry import get_orchestrator_adapter

        adapter = get_orchestrator_adapter()
        response = await adapter.complete(
            system_prompt=EXTRACT_PROMPT,
            messages=[ChatMessage(role="user", content=interaction_text)],
        )

        facts_text = response.text or ""
        if "NONE" in facts_text.upper() or not facts_text.strip():
            return 0

        # Parse bullet points
        facts = [
            line.strip().lstrip("- ").strip()
            for line in facts_text.strip().split("\n")
            if line.strip() and line.strip().startswith("-")
        ]

        if not facts:
            return 0

        # Embed and store each fact
        embeddings = await embedder.embed(facts)

        async with async_session() as session:
            for fact, embedding in zip(facts, embeddings):
                record = DocumentChunk(
                    id=new_id(),
                    source_type="curated_memory",
                    source_path=f"agent/{agent_name}",
                    title=f"Memory from {agent_name}",
                    section_header=objective[:200],
                    content=fact,
                    metadata_={
                        "agent": agent_name,
                        "conversation_id": conversation_id or "",
                        "type": "curated_fact",
                    },
                    embedding=embedding,
                )
                session.add(record)
            await session.commit()

        logger.info("Stored %d curated memories from %s", len(facts), agent_name)
        return len(facts)

    except Exception as e:
        logger.warning("Memory extraction failed: %s", e)
        return 0
