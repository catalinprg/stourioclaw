import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.telegram.media import transcribe_voice, describe_image


@pytest.mark.asyncio
async def test_transcribe_voice_returns_text():
    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = AsyncMock(
        return_value=MagicMock(text="Hello, this is a test")
    )
    mock_openai_module = MagicMock()
    mock_openai_module.AsyncOpenAI.return_value = mock_client

    with patch.dict("sys.modules", {"openai": mock_openai_module}):
        result = await transcribe_voice(b"fake-ogg-data", api_key="test-key")
    assert result == "Hello, this is a test"


@pytest.mark.asyncio
async def test_transcribe_voice_no_api_key():
    result = await transcribe_voice(b"data", api_key="")
    assert "not configured" in result.lower()


@pytest.mark.asyncio
async def test_describe_image_returns_description():
    with patch("src.telegram.media.httpx.AsyncClient") as MockClient:
        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "A photo of a cat"}}],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await describe_image(b"fake-image", api_key="test-key", model="openai/gpt-4o")
    assert result == "A photo of a cat"


@pytest.mark.asyncio
async def test_describe_image_with_caption():
    with patch("src.telegram.media.httpx.AsyncClient") as MockClient:
        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "It's your car"}}],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await describe_image(b"fake-image", api_key="test-key", model="openai/gpt-4o", caption="What is this?")
    assert result == "It's your car"
    call_body = mock_http.post.call_args[1]["json"]
    user_msg = call_body["messages"][-1]
    assert any("What is this?" in str(part) for part in user_msg["content"])
