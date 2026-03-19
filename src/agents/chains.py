"""Code review chain: orchestrates code_writer -> code_reviewer with feedback loop."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.runtime import execute_agent

logger = logging.getLogger("stourio.chains")


@dataclass
class ChainResult:
    result: str
    iterations: int
    approved: bool


def _parse_reviewer_verdict(text: str) -> tuple[bool, str]:
    """Parse reviewer response. Tries JSON first, falls back to keyword match.

    Returns (approved: bool, feedback: str).
    """
    # Try JSON parse
    try:
        data = json.loads(text)
        verdict = data.get("verdict", "").lower()
        feedback = data.get("feedback", "")
        return verdict == "approved", feedback
    except (json.JSONDecodeError, AttributeError):
        pass

    # Fallback: keyword check
    approved = "approved" in text.lower()
    return approved, text


async def execute_code_review_chain(
    objective: str,
    context: dict[str, Any],
    session: AsyncSession,
    max_iterations: int = 3,
) -> ChainResult:
    """Run code_writer -> code_reviewer chain with feedback loop.

    Flow:
    1. Send objective to code_writer agent
    2. Send code_writer output to code_reviewer
    3. If reviewer approves -> return result
    4. If reviewer rejects -> feed back to code_writer
    5. Repeat up to max_iterations
    6. If max_iterations exhausted -> return last result with approved=False
    """
    context_str = json.dumps(context) if isinstance(context, dict) else str(context)
    writer_output = ""
    feedback = ""

    for iteration in range(1, max_iterations + 1):
        # Build writer objective with feedback from previous iteration
        if feedback:
            writer_objective = (
                f"{objective}\n\nPrevious code was rejected. Reviewer feedback:\n{feedback}"
            )
        else:
            writer_objective = objective

        # Step 1: code_writer produces code
        writer_exec = await execute_agent(
            agent_name="code_writer",
            objective=writer_objective,
            context=context_str,
            session=session,
        )
        writer_output = writer_exec.result or ""

        # Step 2: code_reviewer evaluates the code
        review_context = json.dumps({
            "objective": objective,
            "code": writer_output,
            "iteration": iteration,
        })
        reviewer_exec = await execute_agent(
            agent_name="code_reviewer",
            objective=f"Review the following code for correctness and quality:\n\n{writer_output}",
            context=review_context,
            session=session,
        )
        reviewer_response = reviewer_exec.result or ""

        approved, feedback = _parse_reviewer_verdict(reviewer_response)

        if approved:
            return ChainResult(
                result=writer_output,
                iterations=iteration,
                approved=True,
            )

        logger.info(
            "Code review iteration %d/%d rejected. Feedback: %s",
            iteration, max_iterations, feedback[:200],
        )

    # Max iterations exhausted without approval
    return ChainResult(
        result=writer_output,
        iterations=max_iterations,
        approved=False,
    )
