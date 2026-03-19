"""Execute Python or bash code in a subprocess with timeout."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile

logger = logging.getLogger("stourio.tools.execute_code")

# Minimal environment for subprocess — no secrets leaked
_SAFE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "LANG": "en_US.UTF-8",
}


async def execute_code(arguments: dict) -> dict:
    """Run code in a subprocess. Returns stdout, stderr, exit_code."""
    language = arguments.get("language", "python")
    code = arguments["code"]
    timeout = arguments.get("timeout", 30)

    if language not in ("python", "bash"):
        return {"error": f"Unsupported language: {language}", "exit_code": -1}

    tmp_path = None
    try:
        if language == "python":
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False
            ) as tmp:
                tmp.write(code)
                tmp_path = tmp.name
            proc = await asyncio.create_subprocess_exec(
                "python3", tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_SAFE_ENV,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_SAFE_ENV,
            )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        logger.info(
            "execute_code [%s]: exit=%d, stdout=%d bytes",
            language,
            proc.returncode,
            len(stdout),
        )
        return {
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "exit_code": proc.returncode,
        }
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        logger.warning("execute_code: timeout after %ds", timeout)
        return {"error": f"Execution timed out after {timeout}s", "exit_code": -1}
    except Exception as exc:
        logger.exception("execute_code failed")
        return {"error": str(exc), "exit_code": -1}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
