import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from src.telegram.client import TelegramClient, MAX_MESSAGE_LENGTH


@pytest.fixture
def client():
    return TelegramClient(token="test-token-123")


@pytest.fixture
def mock_response():
    """Factory for fake httpx responses."""
    def _make(json_data=None):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json.return_value = json_data or {"ok": True, "result": {}}
        resp.raise_for_status = MagicMock()
        return resp
    return _make


@pytest.mark.asyncio
async def test_send_message(client, mock_response):
    """Verify API call made with correct params."""
    client._http.post = AsyncMock(return_value=mock_response({"ok": True, "result": {"message_id": 1}}))

    results = await client.send_message(chat_id=12345, text="Hello")

    assert len(results) == 1
    assert results[0]["ok"] is True

    client._http.post.assert_called_once()
    call_args = client._http.post.call_args
    assert call_args[0][0].endswith("/sendMessage")
    payload = call_args[1]["json"]
    assert payload["chat_id"] == 12345
    assert payload["text"] == "Hello"
    assert payload["parse_mode"] == "Markdown"
    assert "reply_markup" not in payload


@pytest.mark.asyncio
async def test_send_message_with_reply_markup(client, mock_response):
    """Verify reply_markup is included when provided."""
    client._http.post = AsyncMock(return_value=mock_response())

    markup = {"inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]]}
    await client.send_message(chat_id=1, text="Pick", reply_markup=markup)

    payload = client._http.post.call_args[1]["json"]
    assert payload["reply_markup"] == markup


@pytest.mark.asyncio
async def test_send_message_splits_long_text(client, mock_response):
    """5000 char message should produce 2 API calls."""
    client._http.post = AsyncMock(return_value=mock_response())

    long_text = "a" * 5000
    results = await client.send_message(chat_id=1, text=long_text)

    assert len(results) == 2
    assert client._http.post.call_count == 2

    # Verify all text was sent
    sent_texts = [call[1]["json"]["text"] for call in client._http.post.call_args_list]
    assert sum(len(t) for t in sent_texts) == 5000
    for t in sent_texts:
        assert len(t) <= MAX_MESSAGE_LENGTH


@pytest.mark.asyncio
async def test_send_typing_action(client, mock_response):
    """Verify sendChatAction call with typing action."""
    client._http.post = AsyncMock(return_value=mock_response())

    await client.send_typing(chat_id=42)

    client._http.post.assert_called_once()
    call_args = client._http.post.call_args
    assert call_args[0][0].endswith("/sendChatAction")
    payload = call_args[1]["json"]
    assert payload["chat_id"] == 42
    assert payload["action"] == "typing"


@pytest.mark.asyncio
async def test_set_webhook(client, mock_response):
    """Verify setWebhook call."""
    client._http.post = AsyncMock(return_value=mock_response({"ok": True, "description": "Webhook set"}))

    result = await client.set_webhook(url="https://example.com/hook", secret_token="s3cret")

    call_args = client._http.post.call_args
    assert call_args[0][0].endswith("/setWebhook")
    payload = call_args[1]["json"]
    assert payload["url"] == "https://example.com/hook"
    assert payload["secret_token"] == "s3cret"
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_answer_callback_query(client, mock_response):
    """Verify answerCallbackQuery call."""
    client._http.post = AsyncMock(return_value=mock_response())

    await client.answer_callback_query(callback_query_id="abc123", text="Done")

    call_args = client._http.post.call_args
    assert call_args[0][0].endswith("/answerCallbackQuery")
    payload = call_args[1]["json"]
    assert payload["callback_query_id"] == "abc123"
    assert payload["text"] == "Done"


def test_split_text_short(client):
    """Text under limit returns single chunk."""
    chunks = client._split_text("short")
    assert chunks == ["short"]


def test_split_text_prefers_newline(client):
    """Splitting prefers newline boundaries."""
    # Build text: 4000 chars + newline + 200 chars
    text = "a" * 4000 + "\n" + "b" * 200
    chunks = client._split_text(text)
    assert len(chunks) == 2
    assert chunks[0] == "a" * 4000
    assert chunks[1] == "b" * 200


def test_split_text_hard_split_no_newlines(client):
    """Without newlines, hard-splits at MAX_MESSAGE_LENGTH."""
    text = "x" * 5000
    chunks = client._split_text(text)
    assert len(chunks) == 2
    assert len(chunks[0]) == MAX_MESSAGE_LENGTH
    assert len(chunks[1]) == 5000 - MAX_MESSAGE_LENGTH
