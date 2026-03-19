"""FastAPI route that receives Telegram updates and routes through the orchestrator."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.config import settings
from src.models.schemas import OrchestratorInput, SignalSource
from src.telegram.formatter import to_telegram_markdown

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
    if secret != settings.telegram_webhook_secret:
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

    text = message.get("text", "")
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
    if hasattr(result, "text_response") and result.text_response:
        response_text = result.text_response
    elif hasattr(result, "result") and result.result:
        response_text = result.result
    elif isinstance(result, str):
        response_text = result

    if response_text and _telegram_client:
        formatted = to_telegram_markdown(response_text)
        await _telegram_client.send_message(chat_id=chat_id, text=formatted)

    return response_text


async def _handle_callback_query(callback_query: dict, chat_id: int) -> Optional[str]:
    """Handle inline keyboard callbacks (approve/reject)."""
    callback_data = callback_query.get("data", "")
    callback_query_id = callback_query.get("id")

    if not callback_data or not _orchestrator:
        return None

    parts = callback_data.split(":", 1)
    if len(parts) != 2:
        return None

    action, approval_id = parts

    if action not in ("approve", "reject"):
        return None

    # Answer the callback to remove the loading state
    if _telegram_client and callback_query_id:
        try:
            await _telegram_client.answer_callback_query(
                callback_query_id=callback_query_id,
                text=f"{'Approved' if action == 'approve' else 'Rejected'}",
            )
        except Exception:
            logger.debug("Failed to answer callback query")

    # Process the approval decision
    approved = action == "approve"
    response_text = f"Decision recorded: {'approved' if approved else 'rejected'} for {approval_id}"

    if _telegram_client:
        await _telegram_client.send_message(chat_id=chat_id, text=response_text)

    return response_text
