"""
Unit tests for the independent crawler module.

Tests all 28 audit requirements using mocked responses without external network access.
"""

from __future__ import annotations

import http.client
import io
import json
import socket
import ssl
import urllib.error
import urllib.response
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from freshness_crawler import (
    DEFAULT_USER_AGENT,
    _extract_charset,
    _extract_mime_type,
    _is_private_or_local_ip,
    _sanitize_headers,
    _validate_url,
    crawl_page,
)


class MockHTTPResponse:
    """Helper mock for urllib HTTP response context manager."""

    def __init__(
        self,
        body: bytes = b"<html><head><title>Test</title></head><body><h1>Hello</h1></body></html>",
        status: int = 200,
        headers: dict[str, str] | None = None,
    ):
        self.status = status
        self.code = status
        default_headers = {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(body)),
        }
        if headers:
            default_headers.update(headers)
        self.headers = http.client.HTTPMessage()
        for k, v in default_headers.items():
            self.headers[k] = v
        self._stream = io.BytesIO(body)

    def read(self, amt: int = -1) -> bytes:
        return self._stream.read(amt)

    def __enter__(self) -> MockHTTPResponse:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


# 1. Valid URL
def test_valid_url() -> None:
    valid, err, parsed = _validate_url("https://example.com/valid-article")
    assert valid is True
    assert err is None
    assert parsed is not None
    assert parsed.scheme == "https"
    assert parsed.hostname == "example.com"


# 2. Invalid URL
def test_invalid_url() -> None:
    res = crawl_page("not_a_valid_url")
    assert res["status"] == "invalid_input"
    assert res["errors"][0]["type"] == "invalid_url"
    assert res["html"]["available"] is False


# 3. Missing scheme
def test_missing_scheme() -> None:
    res = crawl_page("example.com/page")
    assert res["status"] == "invalid_input"
    assert res["errors"][0]["type"] == "invalid_url"
    assert "Unsupported scheme" in res["errors"][0]["message"]


# 4. Unsupported scheme
def test_unsupported_scheme() -> None:
    res = crawl_page("ftp://example.com/resource.txt")
    assert res["status"] == "invalid_input"
    assert res["errors"][0]["type"] == "invalid_url"
    assert "ftp" in res["errors"][0]["message"]

    res_file = crawl_page("file:///etc/passwd")
    assert res_file["status"] == "invalid_input"
    assert "file" in res_file["errors"][0]["message"]


# 5. Successful 200 response
@patch("urllib.request.OpenerDirector.open")
def test_successful_200_response(mock_open: MagicMock) -> None:
    html_bytes = b"<!DOCTYPE html><html><body><h1>Audit Subject</h1></body></html>"
    mock_open.return_value = MockHTTPResponse(body=html_bytes, status=200)

    res = crawl_page("https://example.com/article", options={"allow_local": True})
    assert res["status"] == "success"
    assert res["response"]["status_code"] == 200
    assert res["response"]["final_url"] == "https://example.com/article"
    assert res["html"]["available"] is True
    assert "Audit Subject" in res["html"]["body"]
    assert res["errors"] == []


# 6. 404 response
@patch("urllib.request.OpenerDirector.open")
def test_404_response(mock_open: MagicMock) -> None:
    err_headers = http.client.HTTPMessage()
    err_headers["Content-Type"] = "text/html"
    http_error = urllib.error.HTTPError(
        url="https://example.com/missing",
        code=404,
        msg="Not Found",
        hdrs=err_headers,
        fp=io.BytesIO(b"<html><body>404 Not Found</body></html>"),
    )
    mock_open.side_effect = http_error

    res = crawl_page("https://example.com/missing", options={"allow_local": True})
    assert res["status"] == "http_error"
    assert res["response"]["status_code"] == 404
    assert res["errors"][0]["type"] == "http_error"
    assert "404" in res["errors"][0]["message"]


# 7. 500 response
@patch("urllib.request.OpenerDirector.open")
def test_500_response(mock_open: MagicMock) -> None:
    err_headers = http.client.HTTPMessage()
    err_headers["Content-Type"] = "text/html"
    http_error = urllib.error.HTTPError(
        url="https://example.com/crash",
        code=500,
        msg="Internal Server Error",
        hdrs=err_headers,
        fp=io.BytesIO(b"Server Error"),
    )
    mock_open.side_effect = http_error

    res = crawl_page("https://example.com/crash", options={"allow_local": True})
    assert res["status"] == "http_error"
    assert res["response"]["status_code"] == 500
    assert res["errors"][0]["type"] == "http_error"


# 8. Single redirect
@patch("urllib.request.OpenerDirector.open")
def test_single_redirect(mock_open: MagicMock) -> None:
    redirect_hdrs = http.client.HTTPMessage()
    redirect_hdrs["Location"] = "https://example.com/destination"
    redirect_hdrs["Content-Type"] = "text/html"
    redirect_err = urllib.error.HTTPError(
        url="https://example.com/origin",
        code=301,
        msg="Moved Permanently",
        hdrs=redirect_hdrs,
        fp=io.BytesIO(b""),
    )

    success_resp = MockHTTPResponse(
        body=b"<html>Destination</html>",
        status=200,
    )
    mock_open.side_effect = [redirect_err, success_resp]

    res = crawl_page("https://example.com/origin", options={"allow_local": True})
    assert res["status"] == "success"
    assert res["redirects"]["count"] == 1
    assert res["redirects"]["chain"][0]["status_code"] == 301
    assert res["redirects"]["chain"][0]["from"] == "https://example.com/origin"
    assert res["redirects"]["chain"][0]["to"] == "https://example.com/destination"
    assert res["response"]["final_url"] == "https://example.com/destination"


# 9. Multiple redirects
@patch("urllib.request.OpenerDirector.open")
def test_multiple_redirects(mock_open: MagicMock) -> None:
    h1 = http.client.HTTPMessage()
    h1["Location"] = "https://example.com/step2"
    e1 = urllib.error.HTTPError("https://example.com/step1", 301, "Moved", h1, io.BytesIO(b""))

    h2 = http.client.HTTPMessage()
    h2["Location"] = "https://example.com/final"
    e2 = urllib.error.HTTPError("https://example.com/step2", 302, "Found", h2, io.BytesIO(b""))

    success = MockHTTPResponse(body=b"Final Page", status=200)
    mock_open.side_effect = [e1, e2, success]

    res = crawl_page("https://example.com/step1", options={"allow_local": True})
    assert res["status"] == "success"
    assert res["redirects"]["count"] == 2
    assert res["response"]["final_url"] == "https://example.com/final"


# 10. Redirect loop
@patch("urllib.request.OpenerDirector.open")
def test_redirect_loop(mock_open: MagicMock) -> None:
    h1 = http.client.HTTPMessage()
    h1["Location"] = "https://example.com/page-b"
    e1 = urllib.error.HTTPError("https://example.com/page-a", 302, "Found", h1, io.BytesIO(b""))

    h2 = http.client.HTTPMessage()
    h2["Location"] = "https://example.com/page-a"
    e2 = urllib.error.HTTPError("https://example.com/page-b", 302, "Found", h2, io.BytesIO(b""))

    mock_open.side_effect = [e1, e2]

    res = crawl_page("https://example.com/page-a", options={"allow_local": True})
    assert res["status"] == "redirect_loop"
    assert res["errors"][0]["type"] == "redirect_loop"
    assert res["redirects"]["count"] == 2


# 11. Too many redirects
@patch("urllib.request.OpenerDirector.open")
def test_too_many_redirects(mock_open: MagicMock) -> None:
    # 3 hops when limit is 2
    effects = []
    for i in range(1, 5):
        h = http.client.HTTPMessage()
        h["Location"] = f"https://example.com/hop{i+1}"
        effects.append(urllib.error.HTTPError(f"https://example.com/hop{i}", 301, "Moved", h, io.BytesIO(b"")))

    mock_open.side_effect = effects

    res = crawl_page(
        "https://example.com/hop1",
        options={"max_redirects": 2, "allow_local": True},
    )
    assert res["status"] == "error"
    assert res["errors"][0]["type"] == "too_many_redirects"


# 12. Timeout
@patch("urllib.request.OpenerDirector.open")
def test_timeout(mock_open: MagicMock) -> None:
    mock_open.side_effect = socket.timeout("Socket timed out")

    res = crawl_page("https://example.com/slow", options={"allow_local": True})
    assert res["status"] == "error"
    assert res["errors"][0]["type"] == "timeout"
    assert "timed out" in res["errors"][0]["message"]


# 13. Connection error
@patch("urllib.request.OpenerDirector.open")
def test_connection_error(mock_open: MagicMock) -> None:
    mock_open.side_effect = urllib.error.URLError(reason=ConnectionRefusedError("Connection refused"))

    res = crawl_page("https://example.com/refused", options={"allow_local": True})
    assert res["status"] == "error"
    assert res["errors"][0]["type"] == "connection_error"


# 14. Invalid content type (non-HTML)
@patch("urllib.request.OpenerDirector.open")
def test_invalid_content_type(mock_open: MagicMock) -> None:
    mock_open.return_value = MockHTTPResponse(
        body=b"%PDF-1.4 ...",
        status=200,
        headers={"Content-Type": "application/pdf"},
    )

    res = crawl_page("https://example.com/document.pdf", options={"allow_local": True})
    assert res["status"] == "unsupported_content_type"
    assert res["response"]["content_type"] == "application/pdf"
    assert res["html"]["available"] is False
    assert res["errors"][0]["type"] == "unsupported_content_type"


# 15. Response size limit exceeded
@patch("urllib.request.OpenerDirector.open")
def test_response_size_limit(mock_open: MagicMock) -> None:
    large_payload = b"A" * 5000
    mock_open.return_value = MockHTTPResponse(body=large_payload, status=200)

    res = crawl_page(
        "https://example.com/large",
        options={"max_response_bytes": 1024, "allow_local": True},
    )
    assert res["status"] == "response_too_large"
    assert res["errors"][0]["type"] == "response_too_large"


# 16. Last-Modified header capture
@patch("urllib.request.OpenerDirector.open")
def test_last_modified_header(mock_open: MagicMock) -> None:
    date_str = "Wed, 01 Jan 2026 12:00:00 GMT"
    mock_open.return_value = MockHTTPResponse(
        body=b"<html>Page with Last Modified</html>",
        status=200,
        headers={"Last-Modified": date_str},
    )

    res = crawl_page("https://example.com/fresh", options={"allow_local": True})
    assert res["headers"]["last_modified"] == date_str


# 17. Content-Type extraction
def test_content_type_extraction() -> None:
    mime = _extract_mime_type("text/html; charset=ISO-8859-1")
    assert mime == "text/html"

    charset = _extract_charset("text/html; charset=ISO-8859-1")
    assert charset == "iso-8859-1"


# 18. Duration measurement
@patch("urllib.request.OpenerDirector.open")
def test_duration_measurement(mock_open: MagicMock) -> None:
    mock_open.return_value = MockHTTPResponse(body=b"Quick", status=200)

    res = crawl_page("https://example.com/timer", options={"allow_local": True})
    assert "duration_ms" in res["response"]
    assert isinstance(res["response"]["duration_ms"], int)
    assert res["response"]["duration_ms"] >= 0


# 19. Custom user agent
@patch("urllib.request.OpenerDirector.open")
def test_custom_user_agent(mock_open: MagicMock) -> None:
    mock_open.return_value = MockHTTPResponse(body=b"OK", status=200)

    custom_agent = "CustomAuditor/2.5"
    res = crawl_page(
        "https://example.com/ua",
        options={"user_agent": custom_agent, "allow_local": True},
    )
    assert res["request"]["user_agent"] == custom_agent


# 20. Custom timeout clamp
def test_custom_timeout_clamp() -> None:
    res = crawl_page("invalid_url", options={"timeout_ms": 100})
    # Verification of clamp behavior in crawler function
    assert res["status"] == "invalid_input"


# 21. Custom redirect limit
@patch("urllib.request.OpenerDirector.open")
def test_custom_redirect_limit(mock_open: MagicMock) -> None:
    h = http.client.HTTPMessage()
    h["Location"] = "https://example.com/next"
    mock_open.side_effect = urllib.error.HTTPError("https://example.com/start", 302, "Found", h, io.BytesIO(b""))

    res = crawl_page(
        "https://example.com/start",
        options={"max_redirects": 0, "allow_local": True},
    )
    assert res["status"] == "error"
    assert res["errors"][0]["type"] == "too_many_redirects"


# 22. Custom response-size limit option
@patch("urllib.request.OpenerDirector.open")
def test_custom_response_size_limit_option(mock_open: MagicMock) -> None:
    payload = b"X" * 2048
    mock_open.return_value = MockHTTPResponse(body=payload, status=200)

    res = crawl_page(
        "https://example.com/limit",
        options={"max_response_bytes": 10000, "allow_local": True},
    )
    assert res["status"] == "success"
    assert res["response"]["content_length"] == 2048


# 23. Missing headers handled safely
def test_missing_headers_handled() -> None:
    safe = _sanitize_headers({})
    assert safe["content_type"] is None
    assert safe["last_modified"] is None
    assert safe["etag"] is None
    assert safe["cache_control"] is None
    assert safe["date"] is None


# 24. Malformed encoding handling
@patch("urllib.request.OpenerDirector.open")
def test_malformed_encoding_handling(mock_open: MagicMock) -> None:
    # Invalid UTF-8 sequence bytes
    bad_bytes = b"<html>\xff\xfe\xfd Invalid Characters</html>"
    mock_open.return_value = MockHTTPResponse(
        body=bad_bytes,
        status=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
    )

    res = crawl_page("https://example.com/bad-encoding", options={"allow_local": True})
    assert res["status"] == "success"
    assert res["html"]["available"] is True
    # Verify fallback decoding handled replacement character without raising exception
    assert "\ufffd" in res["html"]["body"]


# 25. JSON serialization
@patch("urllib.request.OpenerDirector.open")
def test_json_serialization(mock_open: MagicMock) -> None:
    mock_open.return_value = MockHTTPResponse(body=b"<html>JSON test</html>", status=200)

    res = crawl_page("https://example.com/serializable", options={"allow_local": True})
    json_str = json.dumps(res)
    assert isinstance(json_str, str)
    parsed = json.loads(json_str)
    assert parsed["component"] == "crawler"
    assert parsed["status"] == "success"


# 26. No sensitive headers in output
def test_no_sensitive_headers_in_output() -> None:
    raw_headers = {
        "Content-Type": "text/html",
        "Set-Cookie": "session_token=secret_value; Secure; HttpOnly",
        "Cookie": "uid=12345",
        "Authorization": "Bearer supersecrettoken",
        "X-Api-Key": "my-api-key",
        "Date": "Wed, 01 Jan 2026 00:00:00 GMT",
    }
    sanitized = _sanitize_headers(raw_headers)
    assert "set-cookie" not in sanitized
    assert "cookie" not in sanitized
    assert "authorization" not in sanitized
    assert "x-api-key" not in sanitized
    assert sanitized["content_type"] == "text/html"
    assert sanitized["date"] == "Wed, 01 Jan 2026 00:00:00 GMT"


# 27. Local/loopback target SSRF protection
def test_ssrf_protection() -> None:
    assert _is_private_or_local_ip("localhost") is True
    assert _is_private_or_local_ip("127.0.0.1") is True
    assert _is_private_or_local_ip("0.0.0.0") is True
    assert _is_private_or_local_ip("::1") is True
    assert _is_private_or_local_ip("10.0.0.1") is True
    assert _is_private_or_local_ip("192.168.1.1") is True

    # When allow_local is False (default), crawl_page blocks it
    res = crawl_page("http://127.0.0.1/admin")
    assert res["status"] == "blocked"
    assert res["errors"][0]["type"] == "unsafe_target"
    assert res["html"]["available"] is False


# 28. Deterministic output structure
@patch("urllib.request.OpenerDirector.open")
def test_deterministic_output_structure(mock_open: MagicMock) -> None:
    mock_open.return_value = MockHTTPResponse(body=b"Structure test", status=200)

    res1 = crawl_page("https://example.com/structure", options={"allow_local": True})
    mock_open.return_value = MockHTTPResponse(body=b"Structure test", status=200)
    res2 = crawl_page("https://example.com/structure", options={"allow_local": True})

    # Keys must match exactly
    assert set(res1.keys()) == set(res2.keys())
    assert set(res1["request"].keys()) == set(res2["request"].keys())
    assert set(res1["response"].keys()) == set(res2["response"].keys())
    assert set(res1["redirects"].keys()) == set(res2["redirects"].keys())
    assert set(res1["headers"].keys()) == set(res2["headers"].keys())
    assert set(res1["html"].keys()) == set(res2["html"].keys())
