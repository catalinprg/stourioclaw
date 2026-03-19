import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.security.auditor import (
    SecurityAuditor,
    SecurityAlert,
    FREQUENCY_THRESHOLD,
    ERROR_THRESHOLD,
)


def _make_entry(agent_id: str, action: str = "tool_call", execution_id: str = "exec-1", risk_level: str = "low"):
    return {
        "agent_id": agent_id,
        "action": action,
        "execution_id": execution_id,
        "risk_level": risk_level,
    }


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def auditor(mock_session):
    return SecurityAuditor(session=mock_session, interval_seconds=60)


# --- analyze_recent_activity ---


@pytest.mark.asyncio
async def test_auditor_detects_high_frequency(auditor):
    """50 actions from one agent should trigger a HIGH_FREQUENCY alert."""
    entries = [_make_entry("agent-spam") for _ in range(50)]

    alerts = await auditor.analyze_recent_activity(entries)

    assert len(alerts) >= 1
    freq_alerts = [a for a in alerts if a.alert_type == "HIGH_FREQUENCY"]
    assert len(freq_alerts) == 1
    assert freq_alerts[0].severity == "HIGH"
    assert freq_alerts[0].source_agent == "agent-spam"
    assert freq_alerts[0].raw_evidence["action_count"] == 50


@pytest.mark.asyncio
async def test_auditor_no_alert_below_threshold(auditor):
    """Actions at or below threshold should not trigger alerts."""
    entries = [_make_entry("agent-normal") for _ in range(FREQUENCY_THRESHOLD)]

    alerts = await auditor.analyze_recent_activity(entries)

    freq_alerts = [a for a in alerts if a.alert_type == "HIGH_FREQUENCY"]
    assert len(freq_alerts) == 0


@pytest.mark.asyncio
async def test_auditor_detects_repeated_failures(auditor):
    """More than ERROR_THRESHOLD error actions should trigger REPEATED_FAILURES alert."""
    entries = [_make_entry("agent-broken", action="error_occurred") for _ in range(15)]

    alerts = await auditor.analyze_recent_activity(entries)

    failure_alerts = [a for a in alerts if a.alert_type == "REPEATED_FAILURES"]
    assert len(failure_alerts) == 1
    assert failure_alerts[0].severity == "MEDIUM"
    assert failure_alerts[0].source_agent == "agent-broken"
    assert failure_alerts[0].raw_evidence["error_count"] == 15


@pytest.mark.asyncio
async def test_auditor_detects_failures_via_risk_level(auditor):
    """High/critical risk_level entries count as errors."""
    entries = [_make_entry("agent-risky", action="tool_call", risk_level="high") for _ in range(12)]

    alerts = await auditor.analyze_recent_activity(entries)

    failure_alerts = [a for a in alerts if a.alert_type == "REPEATED_FAILURES"]
    assert len(failure_alerts) == 1


@pytest.mark.asyncio
async def test_auditor_no_failure_alert_below_threshold(auditor):
    """Errors at or below threshold should not trigger failure alerts."""
    entries = [_make_entry("agent-ok", action="error_occurred") for _ in range(ERROR_THRESHOLD)]

    alerts = await auditor.analyze_recent_activity(entries)

    failure_alerts = [a for a in alerts if a.alert_type == "REPEATED_FAILURES"]
    assert len(failure_alerts) == 0


@pytest.mark.asyncio
async def test_auditor_multiple_agents_independent(auditor):
    """Each agent is evaluated independently."""
    entries = (
        [_make_entry("agent-a") for _ in range(50)]
        + [_make_entry("agent-b") for _ in range(10)]
    )

    alerts = await auditor.analyze_recent_activity(entries)

    freq_alerts = [a for a in alerts if a.alert_type == "HIGH_FREQUENCY"]
    assert len(freq_alerts) == 1
    assert freq_alerts[0].source_agent == "agent-a"


@pytest.mark.asyncio
async def test_auditor_empty_entries(auditor):
    """Empty input produces no alerts."""
    alerts = await auditor.analyze_recent_activity([])
    assert alerts == []


@pytest.mark.asyncio
async def test_auditor_both_alerts_simultaneously(auditor):
    """An agent can trigger both HIGH_FREQUENCY and REPEATED_FAILURES."""
    entries = [_make_entry("agent-chaos", action="fail_something") for _ in range(50)]

    alerts = await auditor.analyze_recent_activity(entries)

    alert_types = {a.alert_type for a in alerts}
    assert "HIGH_FREQUENCY" in alert_types
    assert "REPEATED_FAILURES" in alert_types


# --- save_alerts ---


@pytest.mark.asyncio
async def test_save_alerts_persists_to_session(auditor, mock_session):
    """save_alerts should add SecurityAlertModel records and commit."""
    alerts = [
        SecurityAlert(
            severity="HIGH",
            alert_type="HIGH_FREQUENCY",
            description="test",
            source_agent="agent-x",
            source_execution_id="exec-1",
            raw_evidence={"count": 50},
        ),
    ]

    await auditor.save_alerts(alerts)

    assert mock_session.add.call_count == 1
    mock_session.commit.assert_awaited_once()

    saved_record = mock_session.add.call_args[0][0]
    assert saved_record.severity == "HIGH"
    assert saved_record.alert_type == "HIGH_FREQUENCY"
    assert saved_record.source_agent == "agent-x"


@pytest.mark.asyncio
async def test_save_alerts_empty_list(auditor, mock_session):
    """Saving empty alerts list should still commit but add nothing."""
    await auditor.save_alerts([])

    assert mock_session.add.call_count == 0
    mock_session.commit.assert_awaited_once()
