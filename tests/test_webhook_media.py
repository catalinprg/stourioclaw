"""Tests for voice/image media processing wired into the webhook handler."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.telegram import webhook
from src.telegram.webhook import process_telegram_update, init_telegram_handler


@pytest.fixture
def mock_orchestrator():
    orch = AsyncMock()
    result = MagicMock()
    result.text_response = "Got it"
    result.result = None
    orch.process.return_value = result
    return orch


@pytest.fixture
def mock_telegram_client():
    tc = AsyncMock()
    tc.send_message.return_value = [{"ok": True}]
    tc.send_typing.return_value = None
    tc.get_file.return_value = {"file_path": "voice/file_0.ogg"}
    tc.download_file.return_value = b"fake-audio-bytes"
    return tc


@pytest.fixture(autouse=True)
def _wire_globals(mock_orchestrator, mock_telegram_client):
    init_telegram_handler(mock_orchestrator, mock_telegram_client)
    yield
    init_telegram_handler(None, None)


def _make_voice_update(user_id=111, chat_id=999):
    return {
        "update_id": 10,
        "message": {
            "message_id": 10,
            "from": {"id": user_id, "first_name": "Test"},
            "chat": {"id": chat_id, "type": "private"},
            "voice": {
                "file_id": "voice_file_123",
                "file_unique_id": "vu123",
                "duration": 3,
                "file_size": 9000,
            },
        },
    }


def _make_photo_update(user_id=111, chat_id=999, caption=None):
    msg = {
        "message_id": 11,
        "from": {"id": user_id, "first_name": "Test"},
        "chat": {"id": chat_id, "type": "private"},
        "photo": [
            {"file_id": "photo_small", "file_unique_id": "ps", "file_size": 1000, "width": 90, "height": 90},
            {"file_id": "photo_medium", "file_unique_id": "pm", "file_size": 5000, "width": 320, "height": 320},
            {"file_id": "photo_large", "file_unique_id": "pl", "file_size": 20000, "width": 800, "height": 800},
        ],
    }
    if caption:
        msg["caption"] = caption
    return {"update_id": 11, "message": msg}


def _make_text_update(text="hello", user_id=111, chat_id=999):
    return {
        "update_id": 12,
        "message": {
            "message_id": 12,
            "from": {"id": user_id, "first_name": "Test"},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


class TestWebhookMedia:
    @pytest.mark.asyncio
    async def test_voice_message_transcribed(self, mock_orchestrator, mock_telegram_client):
        """Voice message is downloaded, transcribed, and sent to orchestrator."""
        with patch("src.telegram.webhook.settings") as mock_settings, \
             patch("src.telegram.webhook.transcribe_voice", new_callable=AsyncMock) as mock_transcribe:
            mock_settings.telegram_allowed_user_ids = []
            mock_settings.openai_api_key = "sk-test"
            mock_transcribe.return_value = "Hello world"

            result = await process_telegram_update(_make_voice_update())

            # Verify file was fetched and downloaded
            mock_telegram_client.get_file.assert_called_once_with("voice_file_123")
            mock_telegram_client.download_file.assert_called_once_with(
                "voice/file_0.ogg", file_size=9000
            )
            # Verify transcription called
            mock_transcribe.assert_called_once_with(b"fake-audio-bytes", api_key="sk-test")
            # Verify orchestrator received transcribed text
            call_args = mock_orchestrator.process.call_args[0][0]
            assert "Hello world" in call_args.content

    @pytest.mark.asyncio
    async def test_photo_message_described(self, mock_orchestrator, mock_telegram_client):
        """Photo message picks largest size, describes it, sends to orchestrator."""
        mock_telegram_client.get_file.return_value = {"file_path": "photos/file_0.jpg"}
        mock_telegram_client.download_file.return_value = b"fake-image-bytes"

        with patch("src.telegram.webhook.settings") as mock_settings, \
             patch("src.telegram.webhook.describe_image", new_callable=AsyncMock) as mock_describe:
            mock_settings.telegram_allowed_user_ids = []
            mock_settings.openrouter_api_key = "or-test"
            mock_settings.vision_model = "openai/gpt-4o"
            mock_describe.return_value = "A photo of a cat"

            result = await process_telegram_update(_make_photo_update())

            # Verify largest photo was picked (file_size=20000)
            mock_telegram_client.get_file.assert_called_once_with("photo_large")
            mock_telegram_client.download_file.assert_called_once_with(
                "photos/file_0.jpg", file_size=20000
            )
            # Verify describe_image called
            mock_describe.assert_called_once_with(
                b"fake-image-bytes",
                api_key="or-test",
                model="openai/gpt-4o",
                caption=None,
            )
            # Verify orchestrator received description with prefix
            call_args = mock_orchestrator.process.call_args[0][0]
            assert "[Image analysis]" in call_args.content
            assert "A photo of a cat" in call_args.content

    @pytest.mark.asyncio
    async def test_text_message_still_works(self, mock_orchestrator, mock_telegram_client):
        """Regular text message still goes through the orchestrator."""
        with patch("src.telegram.webhook.settings") as mock_settings:
            mock_settings.telegram_allowed_user_ids = []

            result = await process_telegram_update(_make_text_update(text="ping"))

            call_args = mock_orchestrator.process.call_args[0][0]
            assert call_args.content == "ping"
            assert result == "Got it"
