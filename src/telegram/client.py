"""Thin wrapper around the Telegram Bot API using httpx."""

from __future__ import annotations

import httpx

TELEGRAM_API_BASE = "https://api.telegram.org/bot"
TELEGRAM_FILE_BASE = "https://api.telegram.org/file/bot"
MAX_MESSAGE_LENGTH = 4096
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB Telegram Bot API limit


class TelegramClient:
    """Async Telegram Bot API client. No SDK — raw HTTP calls."""

    def __init__(self, token: str):
        self.token = token
        self._base_url = f"{TELEGRAM_API_BASE}{token}"
        self._http = httpx.AsyncClient(timeout=30.0)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "Markdown",
        reply_markup: dict | None = None,
    ) -> list[dict]:
        """Send message, splitting if > 4096 chars. Returns list of API responses."""
        chunks = self._split_text(text)
        results: list[dict] = []
        for chunk in chunks:
            payload: dict = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
            }
            if reply_markup is not None:
                payload["reply_markup"] = reply_markup
            resp = await self._http.post(
                f"{self._base_url}/sendMessage", json=payload
            )
            resp.raise_for_status()
            results.append(resp.json())
        return results

    async def send_typing(self, chat_id: int) -> None:
        """Send typing indicator via sendChatAction."""
        resp = await self._http.post(
            f"{self._base_url}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
        )
        resp.raise_for_status()

    async def set_webhook(self, url: str, secret_token: str) -> dict:
        """Register webhook URL with Telegram."""
        resp = await self._http.post(
            f"{self._base_url}/setWebhook",
            json={"url": url, "secret_token": secret_token},
        )
        resp.raise_for_status()
        return resp.json()

    async def answer_callback_query(
        self, callback_query_id: str, text: str = ""
    ) -> None:
        """Answer inline keyboard callback."""
        payload: dict = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        resp = await self._http.post(
            f"{self._base_url}/answerCallbackQuery", json=payload
        )
        resp.raise_for_status()

    def _split_text(self, text: str) -> list[str]:
        """Split text into chunks <= MAX_MESSAGE_LENGTH, preferring newline boundaries."""
        if len(text) <= MAX_MESSAGE_LENGTH:
            return [text]

        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= MAX_MESSAGE_LENGTH:
                chunks.append(remaining)
                break

            # Find the last newline within the limit
            split_at = remaining.rfind("\n", 0, MAX_MESSAGE_LENGTH)
            if split_at == -1:
                # No newline found — hard split at limit
                split_at = MAX_MESSAGE_LENGTH

            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip("\n")

        return chunks

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

    async def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict]:
        """Long-poll for updates from Telegram."""
        payload: dict = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        resp = await self._http.post(
            f"{self._base_url}/getUpdates",
            json=payload,
            timeout=timeout + 10,  # HTTP timeout > long-poll timeout
        )
        resp.raise_for_status()
        return resp.json().get("result", [])

    async def delete_webhook(self) -> None:
        """Remove any existing webhook so polling works."""
        resp = await self._http.post(
            f"{self._base_url}/deleteWebhook",
        )
        resp.raise_for_status()

    async def close(self) -> None:
        await self._http.aclose()
