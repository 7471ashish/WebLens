"""
Unit tests for crawler.py using pytest and httpx.MockTransport.

Tests all 12 scenarios specified in the project requirements without external network dependencies.
"""

from __future__ import annotations

import asyncio
import pytest
import httpx

from crawl_crawler import (
    crawl_url,
    validate_and_normalize_url,
    is_safe_target,
    DEFAULT_OPTIONS,
)


@pytest.mark.asyncio
async def test_valid_200_html_page():
    """1. Test valid 200 OK HTML page."""
    html_content = "<!DOCTYPE html><html><head><title>Test Page</title></head><body><h1>Hello World</h1></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8", "Server": "mock-server"},
            content=html_content.encode("utf-8"),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await crawl_url("https://example.com/test", client=client)

    assert res["success"] is True
    assert res["status_code"] == 200
    assert res["requested_url"] == "https://example.com/test"
    assert res["final_url"] == "https://example.com/test"
    assert "text/html" in res["content_type"]
    assert res["body_available"] is True
    assert res["body"] == html_content
    assert res["redirect_count"] == 0
    assert res["headers"].get("server") == "mock-server"


@pytest.mark.asyncio
async def test_404_page_response():
    """2. Test HTTP 404 response (transport succeeds, status is 404)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=404,
            headers={"Content-Type": "text/html"},
            content=b"<html><body><h1>Page Not Found</h1></body></html>",
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await crawl_url("https://example.com/missing", client=client)

    assert res["success"] is True
    assert res["status_code"] == 404
    assert res["body_available"] is True
    assert "Page Not Found" in (res["body"] or "")


@pytest.mark.asyncio
async def test_single_redirect():
    """3. Test single HTTP 301 redirect."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old-path":
            return httpx.Response(
                status_code=301,
                headers={"Location": "https://example.com/new-path"},
            )
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "text/html"},
            content=b"<html><body>New Page</body></html>",
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await crawl_url("https://example.com/old-path", client=client)

    assert res["success"] is True
    assert res["status_code"] == 200
    assert res["final_url"] == "https://example.com/new-path"
    assert res["redirect_count"] == 1
    assert len(res["redirects"]) == 1
    assert res["redirects"][0]["status"] == 301
    assert res["redirects"][0]["to"] == "https://example.com/new-path"


@pytest.mark.asyncio
async def test_multiple_redirects():
    """4. Test multi-hop redirect chain (e.g. 301 -> 302 -> 200)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/step1":
            return httpx.Response(status_code=301, headers={"Location": "/step2"})
        if request.url.path == "/step2":
            return httpx.Response(status_code=302, headers={"Location": "/final"})
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "text/html"},
            content=b"<html><body>Final Destination</body></html>",
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await crawl_url("https://example.com/step1", client=client)

    assert res["success"] is True
    assert res["status_code"] == 200
    assert res["final_url"] == "https://example.com/final"
    assert res["redirect_count"] == 2
    assert len(res["redirects"]) == 2


@pytest.mark.asyncio
async def test_redirect_loop_detection():
    """5. Test redirect loop detection (A -> B -> A)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/loop-a":
            return httpx.Response(status_code=302, headers={"Location": "https://example.com/loop-b"})
        if request.url.path == "/loop-b":
            return httpx.Response(status_code=302, headers={"Location": "https://example.com/loop-a"})
        return httpx.Response(status_code=200)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await crawl_url("https://example.com/loop-a", client=client)

    assert res["success"] is False
    assert res["error"]["type"] == "redirect_error"
    assert "loop" in res["error"]["message"].lower()


@pytest.mark.asyncio
async def test_timeout_handling():
    """6. Test request timeout handling."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Mocked read timeout")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await crawl_url("https://example.com/slow", options={"timeout_ms": 2000}, client=client)

    assert res["success"] is False
    assert res["error"]["type"] == "timeout"
    assert "timed out" in res["error"]["message"].lower()


def test_invalid_url_syntax():
    """7. Test invalid URL handling."""
    is_valid, norm, err = validate_and_normalize_url("ftp://example.com/file.txt")
    assert is_valid is False
    assert "Unsupported scheme" in (err or "")

    is_valid2, norm2, err2 = validate_and_normalize_url("javascript:alert(1)")
    assert is_valid2 is False

    is_valid3, norm3, err3 = validate_and_normalize_url("")
    assert is_valid3 is False


@pytest.mark.asyncio
async def test_https_page():
    """8. Test HTTPS scheme handling."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "https"
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "text/html"},
            content=b"<html><body>Secure</body></html>",
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await crawl_url("https://secure.example.com", client=client)

    assert res["success"] is True
    assert res["status_code"] == 200
    assert res["requested_url"].startswith("https://")


@pytest.mark.asyncio
async def test_non_html_binary_response():
    """9. Test non-HTML binary response (e.g. image/png)."""
    fake_png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR..."

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "image/png"},
            content=fake_png_bytes,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await crawl_url("https://example.com/logo.png", client=client)

    assert res["success"] is True
    assert res["status_code"] == 200
    assert res["content_type"] == "image/png"
    assert res["body_available"] is False
    assert res["body"] is None
    assert res["content_length_bytes"] == len(fake_png_bytes)


@pytest.mark.asyncio
async def test_large_response_truncation():
    """10. Test large response byte truncation."""
    huge_text = "A" * 5000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "text/plain"},
            content=huge_text.encode("utf-8"),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await crawl_url(
            "https://example.com/huge",
            options={"max_response_bytes": 1000},
            client=client,
        )

    assert res["success"] is True
    assert res["body_truncated"] is True
    assert res["content_length_bytes"] == 5000
    assert len(res["body"]) == 1000


@pytest.mark.asyncio
async def test_connection_failure():
    """11. Test connection failure exception handling."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Failed to connect to host")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await crawl_url("https://unreachable.example.com", client=client)

    assert res["success"] is False
    assert res["error"]["type"] in ("connection_error", "dns_error")


def test_unsafe_ssrf_targets():
    """12. Test SSRF protection rejects localhost and private IPs."""
    # Localhost
    safe, err = is_safe_target("localhost")
    assert safe is False
    assert "prohibited" in (err or "").lower()

    # 127.0.0.1
    safe, err = is_safe_target("127.0.0.1")
    assert safe is False

    # Cloud metadata / link-local 169.254.169.254
    safe, err = is_safe_target("169.254.169.254")
    assert safe is False
    assert "restricted" in (err or "").lower() or "protected" in (err or "").lower()

    # Private 192.168.1.1
    safe, err = is_safe_target("192.168.1.1")
    assert safe is False

    # Public domain should be allowed
    safe_pub, _ = is_safe_target("adobe.com")
    assert safe_pub is True
