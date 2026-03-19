"""Per-execution sandbox session.

Manages an isolated workspace and execution context for each agent execution.
- Workspace: a temp directory under /app/workspace/sessions/{execution_id}
- Code execution: Docker container (already implemented)
- Cleanup: workspace deleted when session ends

Future: full session container with shared filesystem.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from src.config import settings

logger = logging.getLogger("stourio.sandbox")

SESSIONS_DIR = "sessions"


class SessionSandbox:
    """Isolated execution context for an agent session."""

    def __init__(self, execution_id: str):
        self.execution_id = execution_id
        self.workspace = os.path.join(
            settings.workspace_dir, SESSIONS_DIR, execution_id
        )
        self._created = False

    def ensure_workspace(self) -> str:
        """Create the session workspace directory."""
        if not self._created:
            os.makedirs(self.workspace, exist_ok=True)
            self._created = True
            logger.debug("Session workspace created: %s", self.workspace)
        return self.workspace

    def cleanup(self) -> None:
        """Delete the session workspace and all its contents."""
        if self._created and os.path.exists(self.workspace):
            shutil.rmtree(self.workspace, ignore_errors=True)
            logger.debug("Session workspace cleaned: %s", self.workspace)

    @staticmethod
    def cleanup_stale_sessions(max_age_hours: int = 24) -> int:
        """Remove session workspaces older than max_age_hours."""
        import time

        sessions_path = os.path.join(settings.workspace_dir, SESSIONS_DIR)
        if not os.path.exists(sessions_path):
            return 0

        now = time.time()
        max_age_seconds = max_age_hours * 3600
        cleaned = 0

        for entry in os.listdir(sessions_path):
            full_path = os.path.join(sessions_path, entry)
            if os.path.isdir(full_path):
                try:
                    age = now - os.path.getmtime(full_path)
                    if age > max_age_seconds:
                        shutil.rmtree(full_path, ignore_errors=True)
                        cleaned += 1
                except Exception:
                    pass

        if cleaned:
            logger.info("Cleaned %d stale session workspaces", cleaned)
        return cleaned
