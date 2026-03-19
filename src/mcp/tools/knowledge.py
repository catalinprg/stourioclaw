"""Semantic search tool — wired to pgvector retriever."""

from __future__ import annotations

import logging

logger = logging.getLogger("stourio.tools.knowledge")

_retriever = None


def set_retriever(retriever):
    """Wire a RAG retriever (src.rag.retriever.Retriever). Called during app startup."""
    global _retriever
    _retriever = retriever
    logger.info("Knowledge retriever wired: %s", type(retriever).__name__)


async def search_knowledge(arguments: dict) -> dict:
    """Semantic search over internal knowledge base via pgvector."""
    if _retriever is None:
        return {"error": "Knowledge retriever not initialized", "results": []}

    query = arguments["query"]
    top_k = arguments.get("top_k", 5)
    source_type = arguments.get("source_type")

    try:
        results = await _retriever.search(
            query=query,
            source_type=source_type,
            top_k_final=top_k,
        )
        return {
            "query": query,
            "results": [
                {
                    "content": r.content,
                    "score": round(r.score, 3),
                    "source": r.source_path,
                    "section": r.section_header,
                }
                for r in results
            ],
        }
    except Exception as exc:
        logger.exception("search_knowledge failed")
        return {"error": str(exc), "results": []}
