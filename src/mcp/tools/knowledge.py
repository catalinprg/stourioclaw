"""Semantic search tool — stub, wired to real retriever in Task 12B."""

from __future__ import annotations

import logging

logger = logging.getLogger("stourio.tools.knowledge")

_retriever = None


def set_retriever(retriever):
    """Wire a RAG retriever. Called during app startup."""
    global _retriever
    _retriever = retriever
    logger.info("Knowledge retriever wired: %s", type(retriever).__name__)


async def search_knowledge(arguments: dict) -> dict:
    """Semantic search over internal knowledge base."""
    if _retriever is None:
        return {"error": "Knowledge retriever not initialized"}

    query = arguments["query"]
    source_type = arguments.get("source_type")

    try:
        results = await _retriever.search(query=query, source_type=source_type)
        return {
            "results": [
                {
                    "content": r.content,
                    "score": round(r.score, 3),
                    "source": r.source_path,
                    "section": r.section_header,
                }
                for r in results
            ]
        }
    except Exception as exc:
        logger.exception("search_knowledge failed")
        return {"error": str(exc)}
