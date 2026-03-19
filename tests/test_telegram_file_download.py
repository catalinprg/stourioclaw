import pytest
from unittest.mock import AsyncMock, MagicMock
from src.telegram.client import TelegramClient


@pytest.mark.asyncio
async def test_get_file_returns_file_path():
    client = TelegramClient(token="test-token")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "ok": True,
        "result": {"file_id": "abc", "file_path": "voice/file_0.ogg", "file_size": 12345},
    }
    mock_resp.raise_for_status = MagicMock()
    client._http.post = AsyncMock(return_value=mock_resp)
    result = await client.get_file("abc")
    assert result["file_path"] == "voice/file_0.ogg"
    assert result["file_size"] == 12345


@pytest.mark.asyncio
async def test_download_file_returns_bytes():
    client = TelegramClient(token="test-token")
    mock_resp = MagicMock()
    mock_resp.content = b"fake-audio-data"
    mock_resp.raise_for_status = MagicMock()
    client._http.get = AsyncMock(return_value=mock_resp)
    data = await client.download_file("voice/file_0.ogg")
    assert data == b"fake-audio-data"
    call_url = client._http.get.call_args[0][0]
    assert "file/bot" in call_url


@pytest.mark.asyncio
async def test_download_file_too_large():
    client = TelegramClient(token="test-token")
    with pytest.raises(ValueError, match="too large"):
        await client.download_file("big.ogg", file_size=25_000_000)
