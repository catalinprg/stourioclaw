"""Parse and query structured data (CSV/JSON)."""

from __future__ import annotations

import csv
import io
import json
import logging

logger = logging.getLogger("stourio.tools.query_data")


async def query_data(arguments: dict) -> dict:
    """Parse CSV or JSON data and optionally filter rows."""
    data_str = arguments["data"]
    fmt = arguments.get("format", "json")
    filter_field = arguments.get("filter_field")
    filter_value = arguments.get("filter_value")

    try:
        if fmt == "csv":
            reader = csv.DictReader(io.StringIO(data_str))
            rows = list(reader)
        elif fmt == "json":
            rows = json.loads(data_str)
            if isinstance(rows, dict):
                rows = [rows]
        else:
            return {"error": f"Unsupported format: {fmt}"}

        if filter_field and filter_value is not None:
            rows = [r for r in rows if str(r.get(filter_field, "")) == str(filter_value)]

        logger.info("query_data: format=%s, rows=%d", fmt, len(rows))
        return {"rows": rows, "count": len(rows)}
    except Exception as exc:
        logger.exception("query_data failed")
        return {"error": str(exc)}
