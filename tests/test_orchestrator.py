import pytest
from unittest.mock import patch, AsyncMock
from src.models.schemas import OrchestratorInput, SignalSource
from src.orchestrator import core as orchestrator_core


@pytest.mark.asyncio
async def test_kill_switch_blocks_processing():
    signal = OrchestratorInput(
        source=SignalSource.USER,
        content="Please do something",
    )

    with patch.object(orchestrator_core, "check_kill_switch", new_callable=AsyncMock, return_value=True), \
         patch.object(orchestrator_core, "audit") as mock_audit:
        mock_audit.log = AsyncMock()
        result = await orchestrator_core.process(signal)

    assert result["status"] == "halted"
    assert "kill switch" in result["message"].lower()
