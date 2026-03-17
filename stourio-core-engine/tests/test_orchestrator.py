import pytest
from unittest.mock import patch, AsyncMock
from src.models.schemas import OrchestratorInput, SignalSource


@pytest.mark.asyncio
async def test_kill_switch_blocks_processing():
    signal = OrchestratorInput(
        source=SignalSource.USER,
        content="Please do something",
    )

    with patch("src.orchestrator.core.check_kill_switch", new_callable=AsyncMock, return_value=True):
        from src.orchestrator.core import process
        result = await process(signal)

    assert result["status"] == "halted"
    assert "kill switch" in result["message"].lower()
