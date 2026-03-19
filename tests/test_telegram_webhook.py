import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.telegram.webhook import (
    telegram_router,
    init_telegram_handler,
    process_telegram_update,
    _handle_callback_query,
)
from src.telegram.formatter import (
    to_telegram_markdown,
    format_approval_request,
    format_security_alert,
)


@pytest.fixture
def app():
    """Create a FastAPI app with the telegram router."""
    app = FastAPI()
    app.include_router(telegram_router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def mock_orchestrator():
    orch = AsyncMock()
    result = MagicMock()
    result.text_response = "Orchestrator says hello"
    result.result = None
    orch.process.return_value = result
    return orch


@pytest.fixture
def mock_telegram_client():
    tc = AsyncMock()
    tc.send_message.return_value = [{"ok": True}]
    tc.send_typing.return_value = None
    tc.answer_callback_query.return_value = None
    return tc


@pytest.fixture(autouse=True)
def _wire_globals(mock_orchestrator, mock_telegram_client):
    """Set and tear down module-level globals for each test."""
    init_telegram_handler(mock_orchestrator, mock_telegram_client)
    yield
    init_telegram_handler(None, None)


def _make_update(text="hello", user_id=111, chat_id=999):
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": user_id, "first_name": "Test"},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def _make_callback_update(data="approve:abc123", user_id=111, chat_id=999):
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cbq_1",
            "from": {"id": user_id, "first_name": "Test"},
            "message": {
                "message_id": 2,
                "from": {"id": 0},
                "chat": {"id": chat_id, "type": "private"},
                "text": "Approval request",
            },
            "data": data,
        },
    }


# --- Webhook endpoint tests ---


class TestWebhookEndpoint:
    def test_webhook_rejects_wrong_secret(self, client):
        """Wrong header -> 403."""
        with patch("src.telegram.webhook.settings") as mock_settings:
            mock_settings.telegram_webhook_secret = "correct-secret"
            mock_settings.telegram_allowed_user_ids = []

            resp = client.post(
                "/api/telegram/webhook",
                json=_make_update(),
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
            )
            assert resp.status_code == 403
            assert resp.json() == {"error": "forbidden"}

    def test_webhook_rejects_missing_secret(self, client):
        """No header -> 403."""
        with patch("src.telegram.webhook.settings") as mock_settings:
            mock_settings.telegram_webhook_secret = "correct-secret"
            mock_settings.telegram_allowed_user_ids = []

            resp = client.post(
                "/api/telegram/webhook",
                json=_make_update(),
            )
            assert resp.status_code == 403

    def test_webhook_accepts_correct_secret(self, client):
        """Correct header -> 200."""
        with patch("src.telegram.webhook.settings") as mock_settings:
            mock_settings.telegram_webhook_secret = "correct-secret"
            mock_settings.telegram_allowed_user_ids = []

            resp = client.post(
                "/api/telegram/webhook",
                json=_make_update(),
                headers={"X-Telegram-Bot-Api-Secret-Token": "correct-secret"},
            )
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}


# --- process_telegram_update tests ---


class TestProcessTelegramUpdate:
    @pytest.mark.asyncio
    async def test_webhook_rejects_unauthorized_user(
        self, mock_orchestrator, mock_telegram_client
    ):
        """Non-allowed user -> silently dropped, orchestrator never called."""
        with patch("src.telegram.webhook.settings") as mock_settings:
            mock_settings.telegram_allowed_user_ids = [999]

            result = await process_telegram_update(_make_update(user_id=111))
            assert result is None
            mock_orchestrator.process.assert_not_called()
            mock_telegram_client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_authorized_user_processed(
        self, mock_orchestrator, mock_telegram_client
    ):
        """Allowed user -> orchestrator called, response sent."""
        with patch("src.telegram.webhook.settings") as mock_settings:
            mock_settings.telegram_allowed_user_ids = [111]

            result = await process_telegram_update(_make_update(user_id=111))
            assert result == "Orchestrator says hello"
            mock_orchestrator.process.assert_called_once()
            mock_telegram_client.send_typing.assert_called_once_with(999)
            mock_telegram_client.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_allowed_list_permits_all(
        self, mock_orchestrator, mock_telegram_client
    ):
        """Empty allowed list -> all users permitted."""
        with patch("src.telegram.webhook.settings") as mock_settings:
            mock_settings.telegram_allowed_user_ids = []

            result = await process_telegram_update(_make_update(user_id=42))
            assert result == "Orchestrator says hello"
            mock_orchestrator.process.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_with_no_message_skipped(self):
        """Update without message field -> None."""
        result = await process_telegram_update({"update_id": 1})
        assert result is None

    @pytest.mark.asyncio
    async def test_update_with_empty_text_skipped(
        self, mock_orchestrator, mock_telegram_client
    ):
        """Message with no text -> None."""
        with patch("src.telegram.webhook.settings") as mock_settings:
            mock_settings.telegram_allowed_user_ids = []

            update = _make_update(text="")
            # Remove text key entirely
            update["message"]["text"] = ""
            result = await process_telegram_update(update)
            assert result is None
            mock_orchestrator.process.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_query_approve(
        self, mock_orchestrator, mock_telegram_client
    ):
        """Callback query with approve data -> acknowledged."""
        with patch("src.telegram.webhook.settings") as mock_settings:
            mock_settings.telegram_allowed_user_ids = [111]

            result = await process_telegram_update(
                _make_callback_update(data="approve:req_123", user_id=111)
            )
            assert result is not None
            assert "approved" in result
            mock_telegram_client.answer_callback_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_callback_query_reject(
        self, mock_orchestrator, mock_telegram_client
    ):
        """Callback query with reject data -> acknowledged."""
        with patch("src.telegram.webhook.settings") as mock_settings:
            mock_settings.telegram_allowed_user_ids = [111]

            result = await process_telegram_update(
                _make_callback_update(data="reject:req_456", user_id=111)
            )
            assert result is not None
            assert "rejected" in result


# --- Formatter tests ---


class TestFormatter:
    def test_to_telegram_markdown_passthrough(self):
        """Pass-through returns input unchanged."""
        text = "*bold* and _italic_"
        assert to_telegram_markdown(text) == text

    def test_format_approval_request(self):
        """Returns text and inline keyboard markup."""
        text, markup = format_approval_request(
            approval_id="ap_1",
            action_description="Delete database",
            risk_level="critical",
            reasoning="User requested full wipe",
        )
        assert "Approval Required" in text
        assert "CRITICAL" in text
        assert "Delete database" in text
        assert markup["inline_keyboard"][0][0]["callback_data"] == "approve:ap_1"
        assert markup["inline_keyboard"][0][1]["callback_data"] == "reject:ap_1"

    def test_format_security_alert(self):
        """Returns formatted security alert string."""
        result = format_security_alert(
            severity="high",
            alert_type="prompt_injection",
            description="Suspicious input detected",
            source_agent="security_auditor",
        )
        assert "Security Alert" in result
        assert "HIGH" in result
        assert "prompt_injection" in result
        assert "security_auditor" in result
