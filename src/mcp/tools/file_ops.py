"""File operations tool — read/write files sandboxed to WORKSPACE_DIR."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("stourio.tools.file_ops")

WORKSPACE_DIR: str | None = None  # Set lazily


def _get_workspace_dir() -> str:
    from src.config import settings

    return getattr(settings, "workspace_dir", "/app/workspace")


def _safe_path(path: str) -> str:
    """Resolve *path* inside the workspace, blocking traversal and symlinks."""
    workspace = WORKSPACE_DIR or _get_workspace_dir()
    resolved = os.path.realpath(os.path.join(workspace, path))
    if not resolved.startswith(os.path.realpath(workspace)):
        raise ValueError(f"Path traversal blocked: {path}")
    return resolved


async def read_file(arguments: dict) -> dict:
    """Read a file from the workspace."""
    try:
        full = _safe_path(arguments["path"])
        with open(full, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info("read_file: %s (%d bytes)", full, len(content))
        return {"content": content, "path": full}
    except ValueError as exc:
        return {"error": str(exc)}
    except FileNotFoundError:
        return {"error": f"File not found: {arguments['path']}"}
    except Exception as exc:
        return {"error": str(exc)}


async def write_file(arguments: dict) -> dict:
    """Write content to a file in the workspace."""
    try:
        full = _safe_path(arguments["path"])
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(arguments["content"])
        logger.info("write_file: %s (%d bytes)", full, len(arguments["content"]))
        return {"status": "ok", "path": full, "bytes_written": len(arguments["content"])}
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": str(exc)}
