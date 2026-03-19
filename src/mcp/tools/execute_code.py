"""Execute Python or bash code in a sandboxed Docker container.

Each execution spawns a disposable container with:
- No network access (--network=none)
- No environment variables (secrets cannot leak)
- Read-only filesystem (except /tmp)
- Memory and CPU limits
- Automatic cleanup after execution

Falls back to subprocess execution if Docker is unavailable
(e.g., local development without Docker).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile

logger = logging.getLogger("stourio.tools.execute_code")

def _get_sandbox_config():
    from src.config import settings
    return {
        "image": settings.code_sandbox_image,
        "memory": settings.code_sandbox_memory,
        "cpus": settings.code_sandbox_cpus,
        "enabled": settings.code_sandbox_enabled,
    }

# Fallback: minimal environment for subprocess (no secrets)
_SAFE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "LANG": "en_US.UTF-8",
}

# Lazy-checked: is Docker daemon running?
_docker_available: bool | None = None


def _check_docker() -> bool:
    """Check if Docker CLI is available and the daemon is running."""
    global _docker_available
    if _docker_available is None:
        if shutil.which("docker") is None:
            _docker_available = False
        else:
            # Verify daemon is actually running (use inherited env for PATH)
            import subprocess
            try:
                result = subprocess.run(
                    ["docker", "info"],
                    capture_output=True, timeout=5,
                )
                _docker_available = result.returncode == 0
            except Exception:
                _docker_available = False
        if _docker_available:
            logger.info("Docker sandbox available — code execution will be containerized")
        else:
            logger.warning("Docker sandbox not available — code execution will use subprocess fallback")
    return _docker_available


async def _run_in_docker(code: str, language: str, timeout: int) -> dict:
    """Execute code in a disposable Docker container with full isolation."""
    cfg = _get_sandbox_config()
    base_cmd = [
        "docker", "run", "--rm",
        "--network=none",
        f"--memory={cfg['memory']}",
        f"--cpus={cfg['cpus']}",
        "--read-only",
        "--tmpfs", "/tmp:size=64m",
        "--security-opt=no-new-privileges:true",
        cfg["image"],
    ]

    if language == "python":
        cmd = base_cmd + ["python3", "-c", code]
    else:
        cmd = base_cmd + ["bash", "-c", code]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_SAFE_ENV,  # Don't pass host env to docker CLI either
    )

    stdout, stderr = await asyncio.wait_for(
        proc.communicate(), timeout=timeout + 5  # Extra 5s for container startup
    )

    return {
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "exit_code": proc.returncode,
        "sandboxed": True,
    }


async def _run_in_subprocess(code: str, language: str, timeout: int) -> dict:
    """Fallback: execute code in a subprocess with stripped environment."""
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

        return {
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "exit_code": proc.returncode,
            "sandboxed": False,
        }
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


async def execute_code(arguments: dict) -> dict:
    """Run code in a sandboxed Docker container (or subprocess fallback).

    Returns stdout, stderr, exit_code, and whether sandboxing was used.
    """
    language = arguments.get("language", "python")
    code = arguments["code"]
    timeout = arguments.get("timeout", 30)

    if language not in ("python", "bash"):
        return {"error": f"Unsupported language: {language}", "exit_code": -1}

    try:
        cfg = _get_sandbox_config()
        if cfg["enabled"] and _check_docker():
            result = await _run_in_docker(code, language, timeout)
            logger.info(
                "execute_code [%s] (sandboxed): exit=%d, stdout=%d bytes",
                language, result["exit_code"], len(result["stdout"]),
            )
        else:
            logger.warning("Docker not available — falling back to subprocess execution (NOT sandboxed)")
            result = await _run_in_subprocess(code, language, timeout)
            logger.info(
                "execute_code [%s] (subprocess): exit=%d, stdout=%d bytes",
                language, result["exit_code"], len(result["stdout"]),
            )

        return result

    except asyncio.TimeoutError:
        logger.warning("execute_code: timeout after %ds", timeout)
        return {"error": f"Execution timed out after {timeout}s", "exit_code": -1}
    except Exception as exc:
        logger.exception("execute_code failed")
        return {"error": str(exc), "exit_code": -1}
