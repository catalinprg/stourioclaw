"""Encrypt/decrypt MCP auth tokens using Fernet symmetric encryption."""
from __future__ import annotations

import logging
import secrets
import base64

from cryptography.fernet import Fernet

from src.config import settings

logger = logging.getLogger("stourio.mcp.crypto")


def _get_fernet() -> Fernet:
    """Get or auto-generate Fernet encryption key."""
    key = settings.mcp_encryption_key
    if not key:
        # Auto-generate and warn
        key = Fernet.generate_key().decode()
        try:
            import os
            object.__setattr__(settings, 'mcp_encryption_key', key)
            os.environ["MCP_ENCRYPTION_KEY"] = key
        except Exception:
            pass
        logger.warning("MCP_ENCRYPTION_KEY not set — auto-generated (tokens won't survive container restart without setting this in .env)")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(token: str) -> str:
    """Encrypt a plaintext auth token. Returns base64-encoded ciphertext."""
    f = _get_fernet()
    return f.encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """Decrypt an encrypted auth token. Returns plaintext."""
    f = _get_fernet()
    return f.decrypt(encrypted.encode()).decode()
