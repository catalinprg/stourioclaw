"""YamlTool — BaseTool implementation driven by YAML definitions.

YAML definition schema:
  name: str
  description: str
  parameters: dict          # JSON Schema object
  execution_mode: str       # "local" | "gateway" | "sandboxed" (default: "local")
  request:
    method: str             # HTTP method (default: GET)
    url: str                # URL; supports ${ENV_VAR} substitution
    headers: dict           # optional; supports ${ENV_VAR} substitution
    body_template: str      # optional Jinja2 template rendered with arguments
  response:
    extract: str            # optional dot-path with array indexing, e.g. "data.items[0].id"
"""

from __future__ import annotations

import os
import re
import logging

import httpx
from jinja2 import Environment, BaseLoader, StrictUndefined

from src.mcp.legacy.base import BaseTool

logger = logging.getLogger("stourio.plugins.yaml_tool")

_jinja_env = Environment(loader=BaseLoader(), undefined=StrictUndefined)
_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _resolve_env_vars(value: str) -> str:
    """Replace ${ENV_VAR} tokens with values from the environment."""
    def _replace(m: re.Match) -> str:
        var = m.group(1)
        resolved = os.environ.get(var)
        if resolved is None:
            logger.warning("YAML tool: env var '%s' not set, substituting empty string", var)
            return ""
        return resolved
    return _ENV_VAR_RE.sub(_replace, value)


def _extract_path(data: object, path: str) -> object:
    """Traverse data using dot-notation with optional array indexing.

    Example: "data.items[0].id" on {"data": {"items": [{"id": 42}]}} -> 42
    """
    if not path:
        return data

    # Split on dots but keep array index tokens intact
    segments = path.split(".")
    current = data
    for seg in segments:
        # Handle array index: e.g. "items[0]"
        match = re.match(r"^([^\[]+)\[(\d+)\]$", seg)
        if match:
            key, idx = match.group(1), int(match.group(2))
            if isinstance(current, dict):
                current = current[key]
            current = current[idx]
        else:
            if isinstance(current, dict):
                current = current[seg]
            elif isinstance(current, list):
                current = current[int(seg)]
            else:
                raise KeyError(f"Cannot navigate into {type(current)} with key '{seg}'")
    return current


class YamlTool(BaseTool):
    """A tool whose behaviour is fully specified by a parsed YAML definition dict."""

    def __init__(self, definition: dict) -> None:
        self.name: str = definition["name"]
        self.description: str = definition.get("description", "")
        self.parameters: dict = definition.get("parameters", {"type": "object", "properties": {}})
        self.execution_mode: str = definition.get("execution_mode", "local")
        self._request_cfg: dict = definition.get("request", {})
        self._response_cfg: dict = definition.get("response", {})

    async def execute(self, arguments: dict) -> dict:
        cfg = self._request_cfg
        method = cfg.get("method", "GET").upper()
        raw_url = cfg.get("url", "")
        if not raw_url:
            return {"error": f"Tool '{self.name}' has no request.url configured."}

        url = _resolve_env_vars(raw_url)

        # Resolve env vars in headers
        raw_headers: dict = cfg.get("headers", {})
        headers = {k: _resolve_env_vars(str(v)) for k, v in raw_headers.items()}

        # Render body via Jinja2
        body: str | None = None
        body_template_src = cfg.get("body_template")
        if body_template_src:
            try:
                tmpl = _jinja_env.from_string(body_template_src)
                body = tmpl.render(**arguments)
            except Exception as exc:
                logger.error("YAML tool '%s': body_template render error: %s", self.name, exc)
                return {"error": f"Body template render failed: {exc}"}

        logger.info("YAML tool '%s': %s %s", self.name, method, url)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                if body is not None:
                    response = await client.request(
                        method, url, content=body.encode(), headers=headers
                    )
                else:
                    response = await client.request(method, url, headers=headers)

                logger.info(
                    "YAML tool '%s': response status %s", self.name, response.status_code
                )
                response.raise_for_status()

            try:
                data = response.json()
            except Exception:
                data = {"result": response.text}

        except httpx.HTTPStatusError as exc:
            logger.error(
                "YAML tool '%s': HTTP %s from %s",
                self.name, exc.response.status_code, url,
            )
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except httpx.HTTPError as exc:
            logger.error("YAML tool '%s': network error: %s", self.name, exc)
            return {"error": f"Network error: {exc}"}

        # Extract value at response.extract path if configured
        extract_path = self._response_cfg.get("extract")
        if extract_path:
            try:
                data = _extract_path(data, extract_path)
                if not isinstance(data, dict):
                    data = {"result": data}
            except (KeyError, IndexError, TypeError) as exc:
                logger.error(
                    "YAML tool '%s': extract path '%s' failed: %s",
                    self.name, extract_path, exc,
                )
                return {"error": f"Response extraction failed at '{extract_path}': {exc}"}

        return data if isinstance(data, dict) else {"result": data}
