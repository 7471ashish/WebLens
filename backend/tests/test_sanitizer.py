"""
Unit tests for Secret Sanitizer.
"""

import sys
import os

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from sanitizer import sanitize_text, mask_key, REDACTED_LABEL


def test_sanitize_groq_key():
    fake_key = "gsk_1234567890abcdef1234567890abcdef"
    text = f"Error communicating with LLM using key {fake_key} in subprocess"
    cleaned = sanitize_text(text)
    assert fake_key not in cleaned
    assert REDACTED_LABEL in cleaned


def test_sanitize_bearer_token():
    text = "Authorization: Bearer secret_token_value_abcdef123456"
    cleaned = sanitize_text(text)
    assert "secret_token_value_abcdef123456" not in cleaned
    assert "Bearer [REDACTED_KEY]" in cleaned


def test_sanitize_clean_text():
    text = "Audit finished with 5 findings across 3 crawled pages."
    assert sanitize_text(text) == text


def test_mask_key():
    assert mask_key("gsk_1234567890abcdef") == "gsk_...cdef"
    assert mask_key(None) == "<empty>"
    assert mask_key("short") == "***"
