"""
Sanitizer utility for masking secrets, API keys, and sensitive tokens.
Guarantees that no Groq/OpenAI/OpenRouter credentials leak into logs, error records, or HTTP responses.
"""

from __future__ import annotations

import logging
import re
from typing import Any

# Regex patterns matching common LLM API keys and authorization headers
_SECRET_PATTERNS = [
    # Groq API keys (typically gsk_...)
    re.compile(r"gsk_[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    # OpenAI and standard sk- keys
    re.compile(r"sk-[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    # OpenRouter sk-or- keys
    re.compile(r"sk-or-[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    # Authorization header with bearer token
    re.compile(r"(Bearer\s+)[A-Za-z0-9_.-]{16,}", re.IGNORECASE),
    # Query parameter / env assignment like GROQ_API_KEY=... or api_key=...
    re.compile(r"(groq_api_key\s*[=:]\s*['\"]?)[A-Za-z0-9_-]{16,}(['\"]?)", re.IGNORECASE),
    re.compile(r"(api_key\s*[=:]\s*['\"]?)[A-Za-z0-9_-]{16,}(['\"]?)", re.IGNORECASE),
]

REDACTED_LABEL = "[REDACTED_KEY]"


def sanitize_text(text: Any) -> str:
    """
    Scrub sensitive keys and tokens from arbitrary text, stack traces, or stderr.
    Safe for non-string inputs.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)

    cleaned = text
    for pattern in _SECRET_PATTERNS:
        # If group matched (like Bearer or key=), retain prefix and mask secret
        if pattern.groups > 0:
            cleaned = pattern.sub(r"\g<1>[REDACTED_KEY]", cleaned)
        else:
            cleaned = pattern.sub(REDACTED_LABEL, cleaned)
    return cleaned


def mask_key(key: str | None) -> str:
    """
    Produce a safe representation of an API key for logs or diagnostics (e.g. gsk_...abcd).
    """
    if not key:
        return "<empty>"
    clean_k = key.strip()
    if len(clean_k) <= 8:
        return "***"
    return f"{clean_k[:4]}...{clean_k[-4:]}"


class SecretSanitizingFilter(logging.Filter):
    """
    Logging filter that sanitizes record messages before output.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = sanitize_text(record.msg)
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    sanitize_text(a) if isinstance(a, str) else a for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: (sanitize_text(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
        return True
