"""Web search tool via Tavily API."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger("stourio.tools.web_search")

TAVILY_URL = "https://api.tavily.com/search"


async def web_search(arguments: dict) -> dict:
    """Search the web using Tavily API."""
    from src.config import settings

    api_key = settings.search_api_key or os.getenv("TAVILY_API_KEY", "")
    if not api_key:
        return {"error": "Search API key not configured"}

    query = arguments["query"]
    max_results = arguments.get("max_results", 5)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                TAVILY_URL,
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
            }
            for r in data.get("results", [])
        ]
        logger.info("web_search: query=%r, results=%d", query, len(results))
        return {"results": results}
    except Exception as exc:
        logger.exception("web_search failed")
        return {"error": str(exc)}
