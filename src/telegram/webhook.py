"""FastAPI route that receives Telegram updates and routes through the orchestrator."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.config import settings
from src.models.schemas import OrchestratorInput, SignalSource
from src.telegram.formatter import to_telegram_markdown
from src.telegram.media import transcribe_voice, describe_image

logger = logging.getLogger(__name__)

telegram_router = APIRouter(prefix="/api/telegram", tags=["telegram"])

# Module-level globals, set during startup
_orchestrator = None
_telegram_client = None


def init_telegram_handler(orchestrator, telegram_client):
    """Wire up the orchestrator and Telegram client at app startup."""
    global _orchestrator, _telegram_client
    _orchestrator = orchestrator
    _telegram_client = telegram_client


@telegram_router.post("/webhook")
async def telegram_webhook(request: Request):
    """Receive Telegram update, verify secret, dispatch to processing."""
    # 1. Verify X-Telegram-Bot-Api-Secret-Token header
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not settings.telegram_webhook_secret or secret != settings.telegram_webhook_secret:
        return JSONResponse(status_code=403, content={"error": "forbidden"})

    # 2. Parse update JSON
    update = await request.json()

    # 3. Process in background-safe manner (fire and forget would lose errors)
    try:
        await process_telegram_update(update)
    except Exception:
        logger.exception("Failed to process Telegram update")

    # 4. Always return 200 to Telegram so it doesn't retry
    return {"ok": True}


async def process_telegram_update(update: dict) -> Optional[str]:
    """Extract message from update, authorize user, route through orchestrator."""
    # 1. Extract message from update (message or callback_query.message)
    callback_query = update.get("callback_query")
    message = update.get("message") or (
        callback_query.get("message") if callback_query else None
    )

    if not message:
        logger.debug("Update has no message, skipping: %s", update.get("update_id"))
        return None

    # 2. Check user_id against allowed list
    # For callback queries, the sender is on the callback_query, not the message
    if callback_query:
        from_user = callback_query.get("from", {})
    else:
        from_user = message.get("from", {})
    user_id = from_user.get("id") if from_user else None

    if not user_id or (
        settings.telegram_allowed_user_ids
        and user_id not in settings.telegram_allowed_user_ids
    ):
        logger.warning("Unauthorized Telegram user: %s", user_id)
        return None

    # 3. Extract chat_id and text
    chat_id = message.get("chat", {}).get("id")
    if not chat_id:
        return None

    # 4. Handle callback queries (approval buttons) separately
    if callback_query:
        return await _handle_callback_query(callback_query, chat_id)

    # 4b. Extract content — voice, photo, or text
    text = await _extract_message_content(message)
    if not text:
        return None

    # 5. Send typing indicator
    if _telegram_client:
        try:
            await _telegram_client.send_typing(chat_id)
        except Exception:
            logger.debug("Failed to send typing indicator")

    # 6. Build OrchestratorInput
    orch_input = OrchestratorInput(
        source=SignalSource.USER,
        content=text,
        conversation_id=str(chat_id),
    )

    # 7. Process through orchestrator
    if not _orchestrator:
        logger.error("Orchestrator not initialized")
        return None

    result = await _orchestrator.process(orch_input)

    # 8. Send response back via Telegram
    response_text = None
    if isinstance(result, dict):
        response_text = result.get("message")
    elif isinstance(result, str):
        response_text = result

    if response_text and _telegram_client:
        formatted = to_telegram_markdown(response_text)
        await _telegram_client.send_message(chat_id=chat_id, text=formatted)

    return response_text


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


async def _handle_callback_query(callback_query: dict, chat_id: int) -> Optional[str]:
    """Handle inline keyboard callbacks — approvals now happen via admin panel."""
    callback_query_id = callback_query.get("id")
    if _telegram_client and callback_query_id:
        try:
            await _telegram_client.answer_callback_query(
                callback_query_id=callback_query_id,
                text="Approvals are managed via the admin panel.",
            )
        except Exception:
            pass
    return None
