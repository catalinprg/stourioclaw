"""Generate markdown reports."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("stourio.tools.report")


async def generate_report(arguments: dict) -> dict:
    """Generate a markdown report from structured data."""
    title = arguments.get("title", "Report")
    sections = arguments.get("sections", [])
    include_timestamp = arguments.get("include_timestamp", True)

    try:
        lines = [f"# {title}", ""]

        if include_timestamp:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            lines.append(f"*Generated: {ts}*")
            lines.append("")

        for section in sections:
            heading = section.get("heading", "Section")
            content = section.get("content", "")
            lines.append(f"## {heading}")
            lines.append("")
            lines.append(content)
            lines.append("")

        markdown = "\n".join(lines)
        logger.info("generate_report: title=%r, sections=%d", title, len(sections))
        return {"markdown": markdown, "length": len(markdown)}
    except Exception as exc:
        logger.exception("generate_report failed")
        return {"error": str(exc)}
