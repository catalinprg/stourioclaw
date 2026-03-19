"""Tests for the code review chain."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.chains import execute_code_review_chain, ChainResult, _parse_reviewer_verdict
from src.models.schemas import AgentExecution, ExecutionStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_execution(result: str) -> AgentExecution:
    return AgentExecution(
        id="exec-1",
        agent_type="test",
        objective="test",
        context="test",
        status=ExecutionStatus.COMPLETED,
        result=result,
    )


# ---------------------------------------------------------------------------
# Unit tests for verdict parsing
# ---------------------------------------------------------------------------

def test_parse_reviewer_verdict_json_approved():
    approved, feedback = _parse_reviewer_verdict(
        json.dumps({"verdict": "approved", "feedback": "Looks good"})
    )
    assert approved is True
    assert feedback == "Looks good"


def test_parse_reviewer_verdict_json_rejected():
    approved, feedback = _parse_reviewer_verdict(
        json.dumps({"verdict": "rejected", "feedback": "Missing error handling"})
    )
    assert approved is False
    assert feedback == "Missing error handling"


def test_parse_reviewer_verdict_keyword_fallback():
    approved, feedback = _parse_reviewer_verdict("The code is approved and ready to ship.")
    assert approved is True


def test_parse_reviewer_verdict_no_approval_keyword():
    approved, feedback = _parse_reviewer_verdict("This code has issues with error handling.")
    assert approved is False


# ---------------------------------------------------------------------------
# Chain integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_code_review_approved_on_first_pass():
    """Writer produces code, reviewer approves immediately. 2 execute_agent calls."""
    mock_session = AsyncMock()

    writer_result = _make_execution("def hello(): return 'world'")
    reviewer_result = _make_execution(json.dumps({"verdict": "approved", "feedback": "Clean code"}))

    with patch("src.agents.chains.execute_agent", new_callable=AsyncMock) as mock_exec:
        mock_exec.side_effect = [writer_result, reviewer_result]

        result = await execute_code_review_chain(
            objective="Write a hello function",
            context={"language": "python"},
            session=mock_session,
        )

    assert result.approved is True
    assert result.iterations == 1
    assert result.result == "def hello(): return 'world'"
    assert mock_exec.call_count == 2

    # Verify call args
    writer_call = mock_exec.call_args_list[0]
    assert writer_call.kwargs["agent_name"] == "code_writer"

    reviewer_call = mock_exec.call_args_list[1]
    assert reviewer_call.kwargs["agent_name"] == "code_reviewer"


@pytest.mark.asyncio
async def test_code_review_rejected_then_approved():
    """Reviewer rejects first attempt, approves second. 4 execute_agent calls."""
    mock_session = AsyncMock()

    writer_result_1 = _make_execution("def hello(): pass")
    reviewer_result_1 = _make_execution(
        json.dumps({"verdict": "rejected", "feedback": "Function returns None, should return a value"})
    )
    writer_result_2 = _make_execution("def hello(): return 'world'")
    reviewer_result_2 = _make_execution(
        json.dumps({"verdict": "approved", "feedback": "Now correct"})
    )

    with patch("src.agents.chains.execute_agent", new_callable=AsyncMock) as mock_exec:
        mock_exec.side_effect = [
            writer_result_1, reviewer_result_1,
            writer_result_2, reviewer_result_2,
        ]

        result = await execute_code_review_chain(
            objective="Write a hello function",
            context={"language": "python"},
            session=mock_session,
        )

    assert result.approved is True
    assert result.iterations == 2
    assert result.result == "def hello(): return 'world'"
    assert mock_exec.call_count == 4

    # Second writer call should include feedback
    second_writer_call = mock_exec.call_args_list[2]
    assert "rejected" in second_writer_call.kwargs["objective"].lower() or \
           "feedback" in second_writer_call.kwargs["objective"].lower()


@pytest.mark.asyncio
async def test_code_review_max_iterations():
    """Reviewer never approves. 6 execute_agent calls (3 rounds), approved=False."""
    mock_session = AsyncMock()

    side_effects = []
    for i in range(3):
        side_effects.append(_make_execution(f"def hello(): pass  # attempt {i+1}"))
        side_effects.append(_make_execution(
            json.dumps({"verdict": "rejected", "feedback": f"Still wrong, attempt {i+1}"})
        ))

    with patch("src.agents.chains.execute_agent", new_callable=AsyncMock) as mock_exec:
        mock_exec.side_effect = side_effects

        result = await execute_code_review_chain(
            objective="Write a hello function",
            context={"language": "python"},
            session=mock_session,
            max_iterations=3,
        )

    assert result.approved is False
    assert result.iterations == 3
    assert result.result == "def hello(): pass  # attempt 3"
    assert mock_exec.call_count == 6


@pytest.mark.asyncio
async def test_code_review_keyword_fallback_approval():
    """Reviewer returns non-JSON text containing 'approved' keyword."""
    mock_session = AsyncMock()

    writer_result = _make_execution("def add(a, b): return a + b")
    reviewer_result = _make_execution("This code is approved. Well done.")

    with patch("src.agents.chains.execute_agent", new_callable=AsyncMock) as mock_exec:
        mock_exec.side_effect = [writer_result, reviewer_result]

        result = await execute_code_review_chain(
            objective="Write an add function",
            context={},
            session=mock_session,
        )

    assert result.approved is True
    assert result.iterations == 1
