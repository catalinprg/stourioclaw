"""Audit log query tool — stub, wired to real DB in Task 12B."""

from __future__ import annotations

import logging

logger = logging.getLogger("stourio.tools.audit")

_audit_store = None


def set_audit_store(store):
    """Wire the audit persistence layer. Called during app startup."""
    global _audit_store
    _audit_store = store
    logger.info("Audit store wired: %s", type(store).__name__)


async def query_audit_log(arguments: dict) -> dict:
    """Query the audit log with optional filters."""
    if _audit_store is None:
        return {"error": "Audit store not initialized"}

    try:
        filters = {
            k: v
            for k, v in {
                "agent_name": arguments.get("agent_name"),
                "tool_name": arguments.get("tool_name"),
                "status": arguments.get("status"),
                "limit": arguments.get("limit", 50),
            }.items()
            if v is not None
        }
        entries = await _audit_store.query(**filters)
        return {"entries": entries, "count": len(entries)}
    except Exception as exc:
        logger.exception("query_audit_log failed")
        return {"error": str(exc)}
