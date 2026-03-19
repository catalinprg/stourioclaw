# Docs Update + Telegram Voice/Image Support

**Date:** 2026-03-19
**Status:** Approved

## Overview

Two independent changes:
1. Rewrite README.md for personal AI. Remove SECURITY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md.
2. Add voice message and image support to Telegram integration.

---

## Part 1: Documentation

### Remove
- `SECURITY.md` — not needed for personal self-hosted tool
- `CONTRIBUTING.md` — not an open-source community project
- `CODE_OF_CONDUCT.md` — not an open-source community project

### Rewrite: README.md
Cover:
- Project name and one-line description (personal AI assistant)
- What it does (6 agents, Telegram input, MCP tools, CyberSecurity monitoring)
- Architecture overview (FastAPI + PostgreSQL + Redis + OpenRouter)
- Quick start (docker compose up, BotFather setup, .env config)
- Agent descriptions (table of 6 agents with roles)
- Admin panel (URL, what views are available)
- Claude Code MCP integration (config snippet)
- Environment variables reference (from .env.example)
- Project structure (directory tree)

No badges, no marketing fluff. Functional documentation for the operator (you).

---

## Part 2: Telegram Voice/Image Support

### Telegram File Download Pattern

Telegram file downloads use a two-step process with a **different base URL** than the method API:
1. Call `getFile(file_id)` → returns `file_path` string
2. Download from `https://api.telegram.org/file/bot{token}/{file_path}`

The current `_base_url` in client.py only covers the method API. A second `_file_url` must be constructed: `https://api.telegram.org/file/bot{token}/`.

**File size limit:** Telegram Bot API limits downloads to 20 MB. Check `file_size` from `getFile` response before downloading. If exceeded, send user a message: "File too large (max 20 MB)."

### Webhook Handler Refactoring

Current webhook.py lines 88-89 extract `text = message.get("text", "")` and return `None` if empty. Voice and photo messages don't have a `text` field. The extraction logic must be restructured to check for voice/photo **before** the text-empty guard:

```
1. Check message.voice → transcribe → set text
2. Check message.photo → describe → set text
3. Check message.text → use directly
4. If none of the above → return None
```

### Voice Messages

**Flow:**
1. Telegram sends update with `message.voice` field (OGG/Opus format)
2. Webhook handler detects voice message type
3. Download voice file via `getFile` + file download URL (see pattern above)
4. Check file size < 20 MB
5. Send audio bytes to OpenAI Whisper API (`POST /v1/audio/transcriptions`, model `whisper-1`, timeout 60s)
6. Get transcribed text back
7. Feed transcribed text into orchestrator as a normal user message
8. Response goes back as text

**Dependencies:** OpenAI API key (already retained for embeddings). Whisper uses the same key. If `openai_api_key` is not set, voice messages return an error: "Voice transcription not configured (OPENAI_API_KEY required)."

### Image Messages

**Flow:**
1. Telegram sends update with `message.photo` field (array of PhotoSize, pick largest)
2. Webhook handler detects photo message type
3. Download image via Telegram Bot API: `getFile` → file URL → download bytes
4. Send image to vision-capable model via OpenRouter with prompt: user's caption if provided, otherwise "Describe this image in detail"
5. Get description text back
6. Feed description into orchestrator as a normal user message (prefixed with "[Image analysis]")
7. Response goes back as text

**Config:** `vision_model` setting in config.py (default: `openai/gpt-4o`)

### Files Changed

| File | Change |
|------|--------|
| `src/telegram/webhook.py` | Detect voice/photo types, call media handlers before orchestrator |
| `src/telegram/client.py` | Add `get_file(file_id)` and `download_file(file_path)` methods |
| `src/telegram/media.py` (new) | `transcribe_voice(audio_bytes)` and `describe_image(image_bytes, caption)` |
| `src/config.py` | Add `vision_model: str = "openai/gpt-4o"` |
| `.env.example` | Add `VISION_MODEL=openai/gpt-4o` |
| `tests/test_telegram_media.py` (new) | Tests for voice transcription and image description |

### Out of Scope
- Images sent as documents (`message.document` with image MIME type) — only `message.photo` handled
- Voice responses (text-to-speech) — responses stay text
- Video messages — not supported

### No Changes To
- Database schema
- Admin panel
- Agent system
- MCP tools
- Orchestrator (it receives text regardless of input type)
