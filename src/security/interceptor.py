"""Security interceptor for pre-execution tool call validation."""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

HIGH_RISK_TOOLS = {"write_file", "execute_code"}
EXTERNAL_RISK_TOOLS = {"call_api", "send_notification"}
SENSITIVE_PATTERNS = [
    re.compile(r"api[_-]?key", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"credential", re.IGNORECASE),
    re.compile(r"sk-[a-zA-Z0-9]+"),
    re.compile(r"ghp_[a-zA-Z0-9]+"),
]


@dataclass
class InterceptResult:
    intercepted: bool
    reason: Optional[str] = None
    severity: str = "LOW"


def _contains_sensitive(arguments: Dict[str, Any]) -> Optional[str]:
    """Check if any argument value matches a sensitive pattern. Returns the matched pattern or None."""
    text = str(arguments)
    for pattern in SENSITIVE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group()
    return None


class SecurityInterceptor:
    """Inline interceptor that checks tool calls before execution."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    async def check_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        agent_name: str,
    ) -> InterceptResult:
        if not self.enabled:
            return InterceptResult(intercepted=False)

        # High-risk tools are always intercepted
        if tool_name in HIGH_RISK_TOOLS:
            return InterceptResult(
                intercepted=True,
                reason=f"Tool '{tool_name}' is classified as high-risk",
                severity="HIGH",
            )

        # External-risk tools: check for sensitive data
        if tool_name in EXTERNAL_RISK_TOOLS:
            matched = _contains_sensitive(arguments)
            if matched:
                return InterceptResult(
                    intercepted=True,
                    reason=f"Sensitive pattern '{matched}' detected in arguments for external tool '{tool_name}'",
                    severity="CRITICAL",
                )
            return InterceptResult(
                intercepted=True,
                reason=f"Tool '{tool_name}' makes external calls",
                severity="MEDIUM",
            )

        # All other tools: check for sensitive patterns
        matched = _contains_sensitive(arguments)
        if matched:
            return InterceptResult(
                intercepted=True,
                reason=f"Sensitive pattern '{matched}' detected in arguments",
                severity="HIGH",
            )

        return InterceptResult(intercepted=False)
