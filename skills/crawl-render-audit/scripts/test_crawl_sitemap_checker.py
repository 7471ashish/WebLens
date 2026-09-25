"""
Unit tests for sitemap_checker.py using pytest and httpx.MockTransport.

Tests all 30 scenarios specified in the requirements without external network dependencies.
"""

from __future__ import annotations

import gzip
import pytest
import httpx

from sitemap_checker import (
    check_sitemap,
    discover_sitemap_urls,
    get_origin,
    parse_sitemap_xml,
)


@pytest.mark.asyncio
async def test_1_valid_sitemap_xml():
    """1. Test valid standard sitemap.xml with <urlset>."""
    xml_data = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>https://example.com/page1</loc>
        <lastmod>2026-08-20</lastmod>
        <changefreq>weekly</changefreq>
        <priority>0.8</priority>
    </url>
    <url>
        <loc>https://example.com/page2</loc>
        <lastmod>2026-08-21</lastmod>
    </url>
</urlset>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "application/xml"},
            content=xml_data.encode("utf-8"),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/page", client=client)

    assert res["summary"]["sitemap_found"] is True
    assert res["summary"]["sitemaps_successful"] == 1
    assert res["summary"]["total_urls_discovered"] == 2
    assert res["sitemaps"][0]["status"] == "ok"
    assert res["sitemaps"][0]["type"] == "urlset"


@pytest.mark.asyncio
async def test_2_sitemap_not_found_404():
    """2. Test conventional sitemap returning HTTP 404."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=404, headers={"Content-Type": "text/html"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/item", client=client)

    assert res["summary"]["sitemap_found"] is False
    assert res["summary"]["sitemaps_failed"] == 1
    assert res["sitemaps"][0]["status"] == "not_found"
    assert res["sitemaps"][0]["status_code"] == 404


@pytest.mark.asyncio
async def test_3_robots_txt_provided_sitemap():
    """3. Test sitemap discovered via robots.txt result."""
    robots_result = {
        "sitemaps": ["https://example.com/custom-location-sitemap.xml"]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/custom-location-sitemap.xml":
            return httpx.Response(
                status_code=200,
                headers={"Content-Type": "application/xml"},
                content=b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/p1</loc></url></urlset>',
            )
        return httpx.Response(status_code=404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/home", robots_result=robots_result, client=client)

    assert res["summary"]["sitemap_found"] is True
    assert "https://example.com/custom-location-sitemap.xml" in res["discovery"]["robots_sitemaps"]
    assert res["sitemaps"][0]["requested_url"] == "https://example.com/custom-location-sitemap.xml"


@pytest.mark.asyncio
async def test_4_sitemap_index():
    """4. Test sitemap index pointing to child sitemaps."""
    index_xml = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <sitemap>
        <loc>https://example.com/sitemap-products.xml</loc>
        <lastmod>2026-08-20</lastmod>
    </sitemap>
</sitemapindex>"""

    child_xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>https://example.com/product/1</loc></url>
    <url><loc>https://example.com/product/2</loc></url>
</urlset>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(status_code=200, headers={"Content-Type": "application/xml"}, content=index_xml.encode("utf-8"))
        if request.url.path == "/sitemap-products.xml":
            return httpx.Response(status_code=200, headers={"Content-Type": "application/xml"}, content=child_xml.encode("utf-8"))
        return httpx.Response(status_code=404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/", client=client)

    assert res["summary"]["sitemaps_successful"] == 2
    assert res["summary"]["total_urls_discovered"] == 2
    assert res["sitemaps"][0]["type"] == "sitemapindex"
    assert res["sitemaps"][1]["type"] == "urlset"


@pytest.mark.asyncio
async def test_5_nested_sitemap_index():
    """5. Test multi-level sitemap index recursion."""
    top_index = '<sitemapindex><sitemap><loc>https://example.com/sub-index.xml</loc></sitemap></sitemapindex>'
    sub_index = '<sitemapindex><sitemap><loc>https://example.com/leaf.xml</loc></sitemap></sitemapindex>'
    leaf_urlset = '<urlset><url><loc>https://example.com/leaf-page</loc></url></urlset>'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(status_code=200, headers={"Content-Type": "application/xml"}, content=top_index.encode("utf-8"))
        if request.url.path == "/sub-index.xml":
            return httpx.Response(status_code=200, headers={"Content-Type": "application/xml"}, content=sub_index.encode("utf-8"))
        if request.url.path == "/leaf.xml":
            return httpx.Response(status_code=200, headers={"Content-Type": "application/xml"}, content=leaf_urlset.encode("utf-8"))
        return httpx.Response(status_code=404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/", client=client)

    assert res["summary"]["sitemaps_successful"] == 3
    assert res["summary"]["total_urls_discovered"] == 1


@pytest.mark.asyncio
async def test_6_circular_sitemap_references():
    """6. Test circular sitemap index references (A -> B -> A) avoid infinite recursion."""
    sm_a = '<sitemapindex><sitemap><loc>https://example.com/sitemap-b.xml</loc></sitemap></sitemapindex>'
    sm_b = '<sitemapindex><sitemap><loc>https://example.com/sitemap.xml</loc></sitemap></sitemapindex>'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(status_code=200, headers={"Content-Type": "application/xml"}, content=sm_a.encode("utf-8"))
        if request.url.path == "/sitemap-b.xml":
            return httpx.Response(status_code=200, headers={"Content-Type": "application/xml"}, content=sm_b.encode("utf-8"))
        return httpx.Response(status_code=404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/", client=client)

    # Both A and B fetched exactly once, recursion halts cleanly
    assert res["summary"]["sitemaps_checked"] == 2
    assert res["summary"]["sitemaps_successful"] == 2


def test_7_duplicate_urls_detection():
    """7. Test duplicate <loc> entries in a sitemap."""
    xml = """<urlset>
        <url><loc>https://example.com/page1</loc></url>
        <url><loc>https://example.com/page2</loc></url>
        <url><loc>https://example.com/page1</loc></url>
    </urlset>"""
    parsed = parse_sitemap_xml(xml.encode("utf-8"))
    assert parsed["url_count"] == 3
    assert parsed["duplicate_url_count"] == 1


def test_8_invalid_url_in_loc():
    """8. Test invalid URL schemes in <loc> elements."""
    xml = """<urlset>
        <url><loc>https://example.com/valid</loc></url>
        <url><loc>javascript:alert(1)</loc></url>
        <url><loc>ftp://example.com/file</loc></url>
    </urlset>"""
    parsed = parse_sitemap_xml(xml.encode("utf-8"))
    assert parsed["url_count"] == 3
    assert parsed["invalid_url_count"] == 2


def test_9_malformed_xml():
    """9. Test malformed XML content handling."""
    xml = b"<urlset><url><loc>https://example.com/unclosed"
    parsed = parse_sitemap_xml(xml)
    assert parsed["valid_xml"] is False
    assert parsed["recognized_sitemap_type"] is False
    assert "XML parse error" in parsed["error"]


def test_10_empty_sitemap():
    """10. Test empty XML string handling."""
    parsed = parse_sitemap_xml(b"")
    assert parsed["valid_xml"] is False
    assert parsed["type"] == "empty"


def test_11_unexpected_xml_root():
    """11. Test unrecognized root element (e.g. <html> or <feed>)."""
    xml = b"<html><head><title>Not a sitemap</title></head><body>Hello</body></html>"
    parsed = parse_sitemap_xml(xml)
    assert parsed["valid_xml"] is True
    assert parsed["recognized_sitemap_type"] is False
    assert parsed["type"] == "unexpected_root"


@pytest.mark.asyncio
async def test_12_timeout_handling():
    """12. Test HTTP timeout on sitemap request."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Request timed out")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/", client=client)

    assert res["summary"]["sitemap_found"] is False
    assert res["summary"]["sitemaps_failed"] == 1
    assert res["errors"][0]["type"] == "timeout"


@pytest.mark.asyncio
async def test_13_connection_error():
    """13. Test DNS or network connection failure."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("getaddrinfo failed")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://invalid-non-existent.example", client=client)

    assert res["summary"]["sitemaps_failed"] == 1
    assert res["errors"][0]["type"] in ("dns_error", "connection_error")


@pytest.mark.asyncio
async def test_14_http_403_forbidden():
    """14. Test HTTP 403 Forbidden on sitemap endpoint."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=403)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/", client=client)

    assert res["sitemaps"][0]["status"] == "http_error"
    assert res["sitemaps"][0]["status_code"] == 403


@pytest.mark.asyncio
async def test_15_http_500_server_error():
    """15. Test HTTP 500 Internal Server Error."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=500)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/", client=client)

    assert res["sitemaps"][0]["status_code"] == 500


@pytest.mark.asyncio
async def test_16_redirect_following():
    """16. Test single HTTP 301 redirect to another sitemap location."""
    xml = '<urlset><url><loc>https://example.com/p</loc></url></urlset>'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(status_code=301, headers={"Location": "https://example.com/real-sitemap.xml"})
        return httpx.Response(status_code=200, headers={"Content-Type": "application/xml"}, content=xml.encode("utf-8"))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/", client=client)

    assert res["summary"]["sitemaps_successful"] == 1
    assert res["sitemaps"][0]["final_url"] == "https://example.com/real-sitemap.xml"


@pytest.mark.asyncio
async def test_17_redirect_loop():
    """17. Test redirect loop detection on sitemap."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(status_code=302, headers={"Location": "https://example.com/sm-loop.xml"})
        return httpx.Response(status_code=302, headers={"Location": "https://example.com/sitemap.xml"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/", client=client)

    assert res["summary"]["sitemaps_failed"] == 1
    assert "loop" in res["errors"][0]["message"].lower()


@pytest.mark.asyncio
async def test_18_oversized_xml():
    """18. Test oversized sitemap exceeding max_xml_size_bytes."""
    huge_xml = b"<urlset>" + b"<url><loc>https://example.com/p</loc></url>" * 2000 + b"</urlset>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, headers={"Content-Type": "application/xml"}, content=huge_xml)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/", options={"max_xml_size_bytes": 1000}, client=client)

    assert res["sitemaps"][0]["status"] == "too_large"
    assert res["sitemaps"][0]["max_size_bytes"] == 1000


@pytest.mark.asyncio
async def test_19_gzip_sitemap():
    """19. Test compressed gzip (.xml.gz) sitemap."""
    raw_xml = b'<urlset><url><loc>https://example.com/compressed-page</loc></url></urlset>'
    compressed_bytes = gzip.compress(raw_xml)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "application/x-gzip"},
            content=compressed_bytes,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/sitemap.xml.gz", client=client)

    assert res["summary"]["sitemaps_successful"] == 1
    assert res["sitemaps"][0]["url_count"] == 1


@pytest.mark.asyncio
async def test_20_invalid_gzip():
    """20. Test invalid/corrupt gzip payload handling."""
    corrupt_gzip = b"\x1f\x8b\x08\x00corrupted_payload"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, headers={"Content-Type": "application/x-gzip"}, content=corrupt_gzip)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/", client=client)

    assert res["sitemaps"][0]["status"] == "invalid_gzip"


@pytest.mark.asyncio
async def test_21_missing_content_type():
    """21. Test sitemap without Content-Type header still parsed if valid XML."""
    xml = b'<urlset><url><loc>https://example.com/p1</loc></url></urlset>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, content=xml)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/", client=client)

    assert res["summary"]["sitemaps_successful"] == 1
    assert res["sitemaps"][0]["url_count"] == 1


@pytest.mark.asyncio
async def test_22_incorrect_content_type_but_valid_xml():
    """22. Test server returning text/html for valid sitemap XML."""
    xml = b'<urlset><url><loc>https://example.com/p1</loc></url></urlset>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, headers={"Content-Type": "text/html"}, content=xml)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/", client=client)

    assert res["summary"]["sitemaps_successful"] == 1
    assert res["sitemaps"][0]["content_type_valid"] is False  # Recorded observation


def test_23_xml_namespaces():
    """23. Test XML parsing with arbitrary or standard namespaces."""
    xml = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
    <url>
        <loc>https://example.com/page-with-image</loc>
        <image:image><image:loc>https://example.com/img.jpg</image:loc></image:image>
    </url>
</urlset>"""
    parsed = parse_sitemap_xml(xml.encode("utf-8"))
    assert parsed["valid_xml"] is True
    assert parsed["url_count"] == 1
    assert parsed["urls"][0]["loc"] == "https://example.com/page-with-image"


def test_24_missing_optional_lastmod():
    """24. Test sitemap without lastmod, changefreq, priority."""
    xml = """<urlset>
        <url><loc>https://example.com/minimal</loc></url>
    </urlset>"""
    parsed = parse_sitemap_xml(xml.encode("utf-8"))
    assert parsed["url_count"] == 1
    assert "lastmod" not in parsed["urls"][0]


def test_25_missing_loc():
    """25. Test URL element without <loc>."""
    xml = """<urlset>
        <url><lastmod>2026-08-20</lastmod></url>
        <url><loc>https://example.com/has-loc</loc></url>
    </urlset>"""
    parsed = parse_sitemap_xml(xml.encode("utf-8"))
    assert parsed["url_count"] == 2
    assert parsed["has_loc"] is True


@pytest.mark.asyncio
async def test_26_sitemap_url_invalid_scheme():
    """26. Test candidate sitemap with unsupported scheme."""
    robots_result = {"sitemaps": ["ftp://example.com/sitemap.xml"]}
    res = await check_sitemap("https://example.com/", robots_result=robots_result)
    assert "ftp://example.com/sitemap.xml" not in res["discovery"]["robots_sitemaps"]


@pytest.mark.asyncio
async def test_27_ssrf_private_ip_protection():
    """27. Test SSRF protection rejects localhost sitemap targets."""
    robots_result = {"sitemaps": ["http://127.0.0.1/sitemap.xml"]}
    res = await check_sitemap("https://example.com/", robots_result=robots_result)
    assert res["summary"]["sitemaps_failed"] == 1
    assert res["errors"][0]["type"] == "security_error"


@pytest.mark.asyncio
async def test_28_max_sitemaps_limit():
    """28. Test max_sitemaps limiting index traversal."""
    sitemaps_elements = "".join(f"<sitemap><loc>https://example.com/sm-{i}.xml</loc></sitemap>" for i in range(10))
    index_xml = f"<sitemapindex>{sitemaps_elements}</sitemapindex>"
    child_xml = "<urlset><url><loc>https://example.com/p</loc></url></urlset>"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(status_code=200, headers={"Content-Type": "application/xml"}, content=index_xml.encode("utf-8"))
        return httpx.Response(status_code=200, headers={"Content-Type": "application/xml"}, content=child_xml.encode("utf-8"))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/", options={"max_sitemaps": 3}, client=client)

    assert len(res["sitemaps"]) == 3


def test_29_max_urls_limit():
    """29. Test max_urls truncation within single sitemap."""
    url_elements = "".join(f"<url><loc>https://example.com/p-{i}</loc></url>" for i in range(100))
    xml = f"<urlset>{url_elements}</urlset>"
    parsed = parse_sitemap_xml(xml.encode("utf-8"), max_urls=15)
    assert parsed["url_count"] == 15
    assert parsed["truncated"] is True


def test_30_duplicate_sitemap_references():
    """30. Test duplicate sitemap references in <sitemapindex>."""
    xml = """<sitemapindex>
        <sitemap><loc>https://example.com/sm-products.xml</loc></sitemap>
        <sitemap><loc>https://example.com/sm-blog.xml</loc></sitemap>
        <sitemap><loc>https://example.com/sm-products.xml</loc></sitemap>
    </sitemapindex>"""
    parsed = parse_sitemap_xml(xml.encode("utf-8"))
    assert parsed["sitemap_count"] == 3
    assert parsed["duplicate_sitemap_count"] == 1


@pytest.mark.asyncio
async def test_31_sitemap_200_html_invalid_resource_state():
    """31. Test HTTP 200 returning HTML page sets resource_state to 'invalid'."""
    html_content = "<!DOCTYPE html><html><body><h1>Welcome to My SPA</h1></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=html_content.encode("utf-8"),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/", client=client)

    assert res["resource_state"] == "invalid"
    assert res["summary"]["sitemap_found"] is False
    assert res["summary"]["sitemaps_failed"] == 1
    assert res["sitemaps"][0]["status"] == "invalid_xml"
    assert res["sitemaps"][0]["resource_state"] == "invalid"


@pytest.mark.asyncio
async def test_32_sitemap_200_empty_xml_resource_state():
    """32. Test HTTP 200 returning valid XML urlset with 0 URLs sets resource_state to 'empty'."""
    empty_xml = """<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "application/xml"},
            content=empty_xml.encode("utf-8"),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_sitemap("https://example.com/", client=client)

    assert res["resource_state"] == "empty"
    assert res["summary"]["total_urls_discovered"] == 0

