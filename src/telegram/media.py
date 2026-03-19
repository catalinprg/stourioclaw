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
    """Transcribe voice message bytes using OpenAI Whisper."""
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
    """Describe an image using a vision model via OpenRouter."""
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
                        "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
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
