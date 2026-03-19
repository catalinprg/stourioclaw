# Docs Update + Telegram Voice/Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite README for personal AI, remove unnecessary community docs, add voice/image support to Telegram.

**Architecture:** Voice messages downloaded from Telegram, transcribed via OpenAI Whisper. Images downloaded, described via vision model through OpenRouter. Both converted to text before hitting the orchestrator. Docs are straightforward file edits.

**Tech Stack:** OpenAI Whisper API, OpenRouter vision models, Telegram Bot API file downloads, httpx.

**Spec:** `docs/superpowers/specs/2026-03-19-docs-and-telegram-media-design.md`

---

## Task 1: Remove Community Docs + Rewrite README

**Files:**
- Delete: `SECURITY.md`
- Delete: `CONTRIBUTING.md`
- Delete: `CODE_OF_CONDUCT.md`
- Rewrite: `README.md`

- [ ] **Step 1: Delete community docs**

```bash
rm SECURITY.md CONTRIBUTING.md CODE_OF_CONDUCT.md
```

- [ ] **Step 2: Rewrite README.md**

Replace entire contents with personal AI documentation covering:
- Project name: **Stourioclaw** — self-hosted personal AI assistant
- One-line description
- Architecture diagram (text): Telegram → Webhook → Orchestrator → Agents → Tools
- Quick start: prerequisites, .env setup, BotFather, docker compose up
- Agent table (6 agents: Assistant, Analyst, Code Writer, Code Reviewer, CyberSecurity, Intel)
- Admin panel: `http://localhost:8000/admin` with 8 views
- Claude Code MCP integration config snippet
- Environment variables reference
- Project structure tree
- Docker services (4: postgres, redis, jaeger, stourioclaw)

- [ ] **Step 3: Commit**

```bash
git rm SECURITY.md CONTRIBUTING.md CODE_OF_CONDUCT.md
git add README.md
git commit -m "docs: rewrite README for personal AI, remove community docs"
```

---

## Task 2: Telegram Client — File Download Methods

**Files:**
- Modify: `src/telegram/client.py`
- Create: `tests/test_telegram_file_download.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_telegram_file_download.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.telegram.client import TelegramClient


@pytest.mark.asyncio
async def test_get_file_returns_file_path():
    """getFile API returns file_path from response."""
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
    """download_file fetches bytes from Telegram file URL."""
    client = TelegramClient(token="test-token")
    mock_resp = MagicMock()
    mock_resp.content = b"fake-audio-data"
    mock_resp.raise_for_status = MagicMock()
    client._http.get = AsyncMock(return_value=mock_resp)

    data = await client.download_file("voice/file_0.ogg")

    assert data == b"fake-audio-data"
    client._http.get.assert_called_once()
    call_url = client._http.get.call_args[0][0]
    assert "file/bot" in call_url
    assert "voice/file_0.ogg" in call_url


@pytest.mark.asyncio
async def test_download_file_too_large():
    """Files exceeding 20 MB raise ValueError."""
    client = TelegramClient(token="test-token")

    with pytest.raises(ValueError, match="too large"):
        await client.download_file("big.ogg", file_size=25_000_000)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_telegram_file_download.py -v
```

- [ ] **Step 3: Implement get_file and download_file in client.py**

Add to `TelegramClient` class:

```python
TELEGRAM_FILE_BASE = "https://api.telegram.org/file/bot"
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB Telegram limit

async def get_file(self, file_id: str) -> dict:
    """Call getFile API. Returns dict with file_path and file_size."""
    resp = await self._http.post(
        f"{self._base_url}/getFile",
        json={"file_id": file_id},
    )
    resp.raise_for_status()
    return resp.json()["result"]

async def download_file(self, file_path: str, file_size: int = 0) -> bytes:
    """Download file bytes from Telegram. Raises ValueError if > 20 MB."""
    if file_size > MAX_FILE_SIZE:
        raise ValueError(f"File too large ({file_size} bytes, max {MAX_FILE_SIZE})")
    file_url = f"{TELEGRAM_FILE_BASE}{self.token}/{file_path}"
    resp = await self._http.get(file_url)
    resp.raise_for_status()
    return resp.content
```

Note: `TELEGRAM_FILE_BASE` and `MAX_FILE_SIZE` go at module level (alongside existing `TELEGRAM_API_BASE` and `MAX_MESSAGE_LENGTH`). The methods reference them — don't define them inside the methods.

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_telegram_file_download.py -v
```
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/telegram/client.py tests/test_telegram_file_download.py
git commit -m "feat: add Telegram file download methods (getFile + download)"
```

---

## Task 3: Media Processing Module

**Files:**
- Create: `src/telegram/media.py`
- Create: `tests/test_telegram_media.py`
- Modify: `src/config.py` — add `vision_model`
- Modify: `.env.example` — add `VISION_MODEL`

- [ ] **Step 1: Add vision_model to config.py**

Add after the `embedding_dimension` line (~line 16):
```python
# --- Vision (for image analysis via OpenRouter) ---
vision_model: str = "openai/gpt-4o"
```

- [ ] **Step 2: Add VISION_MODEL to .env.example**

Add after EMBEDDING_MODEL line:
```
VISION_MODEL=openai/gpt-4o
```

- [ ] **Step 3: Write failing tests**

```python
# tests/test_telegram_media.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.telegram.media import transcribe_voice, describe_image


@pytest.mark.asyncio
async def test_transcribe_voice_returns_text():
    """Voice bytes are sent to Whisper and transcribed text returned."""
    with patch("openai.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        mock_client.audio.transcriptions.create = AsyncMock(
            return_value=MagicMock(text="Hello, this is a test")
        )
        MockOpenAI.return_value = mock_client

        result = await transcribe_voice(b"fake-ogg-data", api_key="test-key")

    assert result == "Hello, this is a test"


@pytest.mark.asyncio
async def test_transcribe_voice_no_api_key():
    """Returns error message when API key is missing."""
    result = await transcribe_voice(b"data", api_key="")
    assert "not configured" in result.lower()


@pytest.mark.asyncio
async def test_describe_image_returns_description():
    """Image bytes are sent to vision model and description returned."""
    with patch("src.telegram.media.httpx.AsyncClient") as MockClient:
        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "A photo of a cat"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await describe_image(
            b"fake-image-data",
            api_key="test-key",
            model="openai/gpt-4o",
        )

    assert result == "A photo of a cat"


@pytest.mark.asyncio
async def test_describe_image_with_caption():
    """User caption is included in the prompt."""
    with patch("src.telegram.media.httpx.AsyncClient") as MockClient:
        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "It's your car"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await describe_image(
            b"fake-image-data",
            api_key="test-key",
            model="openai/gpt-4o",
            caption="What is this?",
        )

    assert result == "It's your car"
    # Verify caption was in the request
    call_body = mock_http.post.call_args[1]["json"]
    user_msg = call_body["messages"][-1]
    assert any("What is this?" in str(part) for part in user_msg["content"])
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
pytest tests/test_telegram_media.py -v
```

- [ ] **Step 5: Implement media.py**

```python
# src/telegram/media.py
"""Voice transcription and image description for Telegram messages."""

from __future__ import annotations

import base64
import io
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


async def transcribe_voice(audio_bytes: bytes, api_key: str) -> str:
    """Transcribe voice message bytes using OpenAI Whisper.

    Args:
        audio_bytes: Raw OGG/Opus audio data from Telegram.
        api_key: OpenAI API key (same one used for embeddings).

    Returns:
        Transcribed text, or error message if not configured.
    """
    if not api_key:
        return "[Voice transcription not configured — OPENAI_API_KEY required]"

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key, timeout=60.0)
    try:
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "voice.ogg"
        transcript = await client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
        )
        return transcript.text
    except Exception as e:
        logger.error("Whisper transcription failed: %s", e)
        return f"[Voice transcription failed: {e}]"


async def describe_image(
    image_bytes: bytes,
    api_key: str,
    model: str = "openai/gpt-4o",
    caption: Optional[str] = None,
) -> str:
    """Describe an image using a vision model via OpenRouter.

    Args:
        image_bytes: Raw image data (JPEG from Telegram).
        api_key: OpenRouter API key.
        model: Vision-capable model identifier.
        caption: Optional user-provided caption/question about the image.

    Returns:
        Text description of the image.
    """
    if not api_key:
        return "[Image analysis not configured — OPENROUTER_API_KEY required]"

    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    prompt = caption or "Describe this image in detail."

    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64_image}",
                        },
                    },
                ],
            }
        ],
        "max_tokens": 1024,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(OPENROUTER_API_URL, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error("Image description failed: %s", e)
        return f"[Image analysis failed: {e}]"
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_telegram_media.py -v
```
Expected: 4 PASSED

- [ ] **Step 7: Commit**

```bash
git add src/telegram/media.py tests/test_telegram_media.py src/config.py .env.example
git commit -m "feat: add voice transcription and image description for Telegram

Whisper for voice-to-text, OpenRouter vision model for images.
Both convert to text before hitting the orchestrator."
```

---

## Task 4: Wire Media into Webhook Handler

**Files:**
- Modify: `src/telegram/webhook.py`
- Create: `tests/test_webhook_media.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_webhook_media.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_voice_message_transcribed():
    """Voice message is downloaded, transcribed, and sent to orchestrator."""
    from src.telegram import webhook

    # Set up module globals
    mock_client = AsyncMock()
    mock_client.get_file = AsyncMock(return_value={"file_path": "voice/f.ogg", "file_size": 5000})
    mock_client.download_file = AsyncMock(return_value=b"fake-audio")
    mock_client.send_typing = AsyncMock()
    mock_client.send_message = AsyncMock(return_value=[{}])

    mock_orchestrator = MagicMock()
    mock_orchestrator.process = AsyncMock(return_value=MagicMock(
        text_response="Got it", result="Got it"
    ))

    webhook._telegram_client = mock_client
    webhook._orchestrator = mock_orchestrator

    update = {
        "message": {
            "voice": {"file_id": "voice123", "file_size": 5000},
            "chat": {"id": 111},
            "from": {"id": 42},
        }
    }

    with patch("src.telegram.webhook.settings") as mock_settings, \
         patch("src.telegram.webhook.transcribe_voice", return_value="Hello world") as mock_transcribe:
        mock_settings.telegram_allowed_user_ids = [42]
        mock_settings.telegram_webhook_secret = ""
        mock_settings.openai_api_key = "test-key"
        mock_settings.openrouter_api_key = "test-key"
        mock_settings.vision_model = "openai/gpt-4o"

        result = await webhook.process_telegram_update(update)

    mock_transcribe.assert_called_once()
    mock_orchestrator.process.assert_called_once()
    assert "Hello world" in mock_orchestrator.process.call_args[0][0].content


@pytest.mark.asyncio
async def test_photo_message_described():
    """Photo is downloaded, described, and sent to orchestrator."""
    from src.telegram import webhook

    mock_client = AsyncMock()
    mock_client.get_file = AsyncMock(return_value={"file_path": "photos/f.jpg", "file_size": 10000})
    mock_client.download_file = AsyncMock(return_value=b"fake-image")
    mock_client.send_typing = AsyncMock()
    mock_client.send_message = AsyncMock(return_value=[{}])

    mock_orchestrator = MagicMock()
    mock_orchestrator.process = AsyncMock(return_value=MagicMock(
        text_response="Nice cat", result="Nice cat"
    ))

    webhook._telegram_client = mock_client
    webhook._orchestrator = mock_orchestrator

    update = {
        "message": {
            "photo": [
                {"file_id": "small", "width": 100, "height": 100, "file_size": 1000},
                {"file_id": "large", "width": 800, "height": 600, "file_size": 10000},
            ],
            "caption": "What animal is this?",
            "chat": {"id": 111},
            "from": {"id": 42},
        }
    }

    with patch("src.telegram.webhook.settings") as mock_settings, \
         patch("src.telegram.webhook.describe_image", return_value="A photo of a cat") as mock_describe:
        mock_settings.telegram_allowed_user_ids = [42]
        mock_settings.telegram_webhook_secret = ""
        mock_settings.openai_api_key = "test-key"
        mock_settings.openrouter_api_key = "test-key"
        mock_settings.vision_model = "openai/gpt-4o"

        result = await webhook.process_telegram_update(update)

    # Should use largest photo
    mock_client.get_file.assert_called_with("large")
    mock_describe.assert_called_once()
    mock_orchestrator.process.assert_called_once()
    assert "[Image analysis]" in mock_orchestrator.process.call_args[0][0].content


@pytest.mark.asyncio
async def test_text_message_still_works():
    """Regular text messages continue to work."""
    from src.telegram import webhook

    mock_client = AsyncMock()
    mock_client.send_typing = AsyncMock()
    mock_client.send_message = AsyncMock(return_value=[{}])

    mock_orchestrator = MagicMock()
    mock_orchestrator.process = AsyncMock(return_value=MagicMock(
        text_response="Hi!", result="Hi!"
    ))

    webhook._telegram_client = mock_client
    webhook._orchestrator = mock_orchestrator

    update = {
        "message": {
            "text": "Hello",
            "chat": {"id": 111},
            "from": {"id": 42},
        }
    }

    with patch("src.telegram.webhook.settings") as mock_settings:
        mock_settings.telegram_allowed_user_ids = [42]
        mock_settings.telegram_webhook_secret = ""

        result = await webhook.process_telegram_update(update)

    mock_orchestrator.process.assert_called_once()
    assert mock_orchestrator.process.call_args[0][0].content == "Hello"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_webhook_media.py -v
```

- [ ] **Step 3: Update webhook.py**

Refactor `process_telegram_update()` lines 84-90. Replace:

```python
    text = message.get("text", "")
    if not text:
        return None
```

With:

```python
    # 4b. Extract content — voice, photo, or text
    text = await _extract_message_content(message)
    if not text:
        return None
```

Add new function and import:

```python
from src.telegram.media import transcribe_voice, describe_image

async def _extract_message_content(message: dict) -> Optional[str]:
    """Extract text content from message. Handles voice, photo, and plain text."""
    # Voice message
    voice = message.get("voice")
    if voice and _telegram_client:
        file_id = voice["file_id"]
        file_size = voice.get("file_size", 0)
        try:
            file_info = await _telegram_client.get_file(file_id)
            audio_bytes = await _telegram_client.download_file(
                file_info["file_path"], file_size=file_size
            )
            return await transcribe_voice(audio_bytes, api_key=settings.openai_api_key or "")
        except ValueError as e:
            return f"[Error: {e}]"
        except Exception as e:
            logger.error("Voice processing failed: %s", e)
            return "[Voice processing failed]"

    # Photo message
    photos = message.get("photo")
    if photos and _telegram_client:
        # Pick largest photo (last in array)
        largest = max(photos, key=lambda p: p.get("file_size", 0))
        file_id = largest["file_id"]
        file_size = largest.get("file_size", 0)
        caption = message.get("caption")
        try:
            file_info = await _telegram_client.get_file(file_id)
            image_bytes = await _telegram_client.download_file(
                file_info["file_path"], file_size=file_size
            )
            description = await describe_image(
                image_bytes,
                api_key=settings.openrouter_api_key,
                model=settings.vision_model,
                caption=caption,
            )
            return f"[Image analysis] {description}"
        except ValueError as e:
            return f"[Error: {e}]"
        except Exception as e:
            logger.error("Image processing failed: %s", e)
            return "[Image processing failed]"

    # Plain text
    return message.get("text", "")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_webhook_media.py -v
```
Expected: 3 PASSED

- [ ] **Step 5: Run full test suite to check no regressions**

```bash
pytest tests/test_telegram_webhook.py tests/test_webhook_media.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/telegram/webhook.py tests/test_webhook_media.py
git commit -m "feat: wire voice/image processing into Telegram webhook

Voice messages transcribed via Whisper, images described via
vision model. Both converted to text before orchestrator routing."
```

---

## Summary

| Task | What | Files |
|------|------|-------|
| 1 | Docs cleanup + README rewrite | README.md, delete 3 files |
| 2 | Telegram file download | client.py, test |
| 3 | Media processing module | media.py, config.py, .env.example, test |
| 4 | Wire media into webhook | webhook.py, test |

**Total: 4 tasks, ~12 steps**
