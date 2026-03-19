"""Text-to-Speech tool — generates audio via ElevenLabs and sends via Telegram.

Agents can use this to speak responses back to the user as voice messages.
"""
from __future__ import annotations

import io
import logging

import httpx

from src.config import settings

logger = logging.getLogger("stourio.tools.tts")

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/text-to-speech"
TELEGRAM_API_URL = "https://api.telegram.org"


async def text_to_speech(arguments: dict) -> dict:
    """Convert text to speech and send as Telegram voice message.

    Args:
        text: Text to speak (max 5000 characters)
        voice_id: ElevenLabs voice ID (optional, uses default)
    """
    text = arguments.get("text")
    if not text:
        return {"error": "Missing required parameter: text"}

    if len(text) > 5000:
        return {"error": "Text too long for TTS (max 5000 characters)"}

    if not settings.elevenlabs_api_key:
        return {"error": "ELEVENLABS_API_KEY not configured. Set it in .env to enable TTS."}

    if not settings.telegram_bot_token:
        return {"error": "TELEGRAM_BOT_TOKEN not configured. Cannot deliver voice message."}

    from src.mcp.tools.notification import get_allowed_user_ids

    chat_ids = get_allowed_user_ids()
    if not chat_ids:
        return {"error": "No allowed Telegram user IDs configured. Cannot deliver voice message."}

    voice_id = arguments.get("voice_id", settings.elevenlabs_voice_id)

    try:
        # Generate audio via ElevenLabs
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{ELEVENLABS_API_URL}/{voice_id}",
                headers={
                    "xi-api-key": settings.elevenlabs_api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json={
                    "text": text,
                    "model_id": settings.elevenlabs_model,
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                    },
                },
            )
            logger.info(
                "ElevenLabs API: POST %s/%s -> %d",
                ELEVENLABS_API_URL,
                voice_id,
                resp.status_code,
            )

            if resp.status_code != 200:
                return {"error": f"ElevenLabs API error: {resp.status_code} {resp.text[:200]}"}

            audio_bytes = resp.content

        if not audio_bytes:
            return {"error": "ElevenLabs returned empty audio"}

        # Send as voice message via Telegram Bot API
        send_voice_url = f"{TELEGRAM_API_URL}/bot{settings.telegram_bot_token}/sendVoice"

        async with httpx.AsyncClient(timeout=30.0) as client:
            for chat_id in chat_ids:
                tg_resp = await client.post(
                    send_voice_url,
                    data={"chat_id": str(chat_id)},
                    files={"voice": ("speech.ogg", io.BytesIO(audio_bytes), "audio/ogg")},
                )
                logger.info(
                    "Telegram sendVoice: POST -> chat_id=%d status=%d",
                    chat_id,
                    tg_resp.status_code,
                )
                tg_resp.raise_for_status()

        logger.info("TTS delivered: %d chars -> %d bytes audio", len(text), len(audio_bytes))
        return {
            "status": "delivered",
            "text_length": len(text),
            "audio_bytes": len(audio_bytes),
            "voice_id": voice_id,
        }

    except Exception as e:
        logger.error("TTS failed: %s", e)
        return {"error": f"TTS failed: {str(e)}"}
