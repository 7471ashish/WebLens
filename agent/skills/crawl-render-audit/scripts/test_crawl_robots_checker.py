"""
Unit tests for robots_checker.py using pytest and httpx.MockTransport.

Tests all 20 scenarios specified in the requirements without external network dependencies.
"""

from __future__ import annotations

import pytest
import httpx

from robots_checker import (
    check_robots,
    parse_robots_txt,
    evaluate_url_permission,
    get_robots_url,
)


@pytest.mark.asyncio
async def test_1_robots_txt_missing_404():
    """1. Test robots.txt missing (HTTP 404) -> allowed by default."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=404, headers={"Content-Type": "text/html"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_robots("https://example.com/products/item", client=client)

    assert res["success"] is True
    assert res["robots_txt"]["found"] is False
    assert res["robots_txt"]["status"] == 404
    assert res["user_agent_results"]["GPTBot"]["allowed"] is True
    assert res["user_agent_results"]["AgentAuditBot"]["allowed"] is True


@pytest.mark.asyncio
async def test_2_robots_txt_accessible_200():
    """2. Test standard 200 OK robots.txt retrieval and parsing."""
    body = "User-agent: *\nDisallow: /admin/\nSitemap: https://example.com/sitemap.xml"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, headers={"Content-Type": "text/plain"}, content=body.encode("utf-8"))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_robots("https://example.com/products/item", client=client)

    assert res["success"] is True
    assert res["robots_txt"]["found"] is True
    assert res["robots_txt"]["status"] == 200
    assert res["user_agent_results"]["*"]["allowed"] is True
    assert "https://example.com/sitemap.xml" in res["sitemaps"]


def test_3_wildcard_user_agent():
    """3. Test wildcard User-agent rule application."""
    content = "User-agent: *\nDisallow: /secret/"
    parsed = parse_robots_txt(content)

    res_blocked = evaluate_url_permission(parsed, "/secret/page", "AnyBot")
    assert res_blocked["allowed"] is False
    assert res_blocked["matching_rule"]["directive"] == "Disallow"

    res_allowed = evaluate_url_permission(parsed, "/public/page", "AnyBot")
    assert res_allowed["allowed"] is True


def test_4_specific_user_agent():
    """4. Test specific User-agent group taking precedence over wildcard."""
    content = """
User-agent: *
Disallow: /

User-agent: Googlebot
Allow: /public/
Disallow: /admin/
"""
    parsed = parse_robots_txt(content)

    res_google = evaluate_url_permission(parsed, "/public/article", "Googlebot")
    assert res_google["allowed"] is True
    assert res_google["matching_rule"]["directive"] == "Allow"

    res_other = evaluate_url_permission(parsed, "/public/article", "SomeOtherBot")
    assert res_other["allowed"] is False


def test_5_gptbot_blocked():
    """5. Test GPTBot explicitly blocked."""
    content = """
User-agent: GPTBot
Disallow: /
"""
    parsed = parse_robots_txt(content)
    res = evaluate_url_permission(parsed, "/any-page", "GPTBot")
    assert res["allowed"] is False
    assert res["matching_rule"]["directive"] == "Disallow"
    assert res["matching_rule"]["value"] == "/"


def test_6_gptbot_allowed():
    """6. Test GPTBot explicitly allowed."""
    content = """
User-agent: GPTBot
Allow: /articles/
Disallow: /private/
"""
    parsed = parse_robots_txt(content)
    res = evaluate_url_permission(parsed, "/articles/ai-news", "GPTBot")
    assert res["allowed"] is True
    assert res["matching_rule"]["directive"] == "Allow"


def test_7_wildcard_blocked_but_specific_bot_allowed():
    """7. Test wildcard blocked for all bots, but specific bot allowed."""
    content = """
User-agent: *
Disallow: /

User-agent: AgentAuditBot
Allow: /
"""
    parsed = parse_robots_txt(content)

    audit_bot = evaluate_url_permission(parsed, "/catalog", "AgentAuditBot")
    assert audit_bot["allowed"] is True

    ai_bot = evaluate_url_permission(parsed, "/catalog", "ClaudeBot")
    assert ai_bot["allowed"] is False


def test_8_allow_overriding_disallow():
    """8. Test longer Allow rule overriding shorter Disallow rule."""
    content = """
User-agent: *
Disallow: /products/
Allow: /products/public/
"""
    parsed = parse_robots_txt(content)

    # Longer Allow rule matches
    res_public = evaluate_url_permission(parsed, "/products/public/item-123", "AgentAuditBot")
    assert res_public["allowed"] is True
    assert res_public["matching_rule"]["directive"] == "Allow"
    assert res_public["overridden_rule"]["directive"] == "Disallow"

    # Shorter Disallow rule matches
    res_private = evaluate_url_permission(parsed, "/products/private/item-123", "AgentAuditBot")
    assert res_private["allowed"] is False


def test_9_sitemap_extraction():
    """9. Test single sitemap extraction."""
    content = "User-agent: *\nDisallow: /private\nSitemap: https://example.com/sitemap.xml"
    parsed = parse_robots_txt(content)
    assert parsed.sitemaps == ["https://example.com/sitemap.xml"]


def test_10_multiple_sitemaps_extraction():
    """10. Test multiple sitemap directives."""
    content = """
User-agent: *
Disallow: /admin
Sitemap: https://example.com/sitemap1.xml
Sitemap: https://example.com/sitemap2.xml
Sitemap: https://example.com/news-sitemap.xml
"""
    parsed = parse_robots_txt(content)
    assert len(parsed.sitemaps) == 3
    assert "https://example.com/news-sitemap.xml" in parsed.sitemaps


def test_11_crawl_delay_extraction():
    """11. Test Crawl-delay directive extraction."""
    content = """
User-agent: Bingbot
Crawl-delay: 2.5
Disallow: /temp/

User-agent: *
Crawl-delay: 10
"""
    parsed = parse_robots_txt(content)

    bing = evaluate_url_permission(parsed, "/page", "Bingbot")
    assert bing["crawl_delay"] == 2.5

    other = evaluate_url_permission(parsed, "/page", "OtherBot")
    assert other["crawl_delay"] == 10.0


def test_12_empty_disallow():
    """12. Test empty Disallow means nothing is blocked."""
    content = """
User-agent: *
Disallow:
"""
    parsed = parse_robots_txt(content)
    res = evaluate_url_permission(parsed, "/anything", "AgentAuditBot")
    assert res["allowed"] is True
    assert res["matching_rule"] is None


def test_13_comments_handling():
    """13. Test inline and full-line comments."""
    content = """
# Top level comment
User-agent: * # Wildcard bot
Disallow: /secret/ # Inline secret
# Another comment
Allow: /secret/open/
"""
    parsed = parse_robots_txt(content)
    res_open = evaluate_url_permission(parsed, "/secret/open/doc", "AgentAuditBot")
    assert res_open["allowed"] is True
    assert res_open["matching_rule"]["value"] == "/secret/open/"


def test_14_multiple_user_agents_in_single_group():
    """14. Test multiple User-agent headers grouped together."""
    content = """
User-agent: GPTBot
User-agent: ClaudeBot
User-agent: CCBot
Disallow: /ai-restricted/
"""
    parsed = parse_robots_txt(content)

    gpt = evaluate_url_permission(parsed, "/ai-restricted/doc", "GPTBot")
    assert gpt["allowed"] is False

    claude = evaluate_url_permission(parsed, "/ai-restricted/doc", "ClaudeBot")
    assert claude["allowed"] is False

    google = evaluate_url_permission(parsed, "/ai-restricted/doc", "Googlebot")
    assert google["allowed"] is True  # Not in group, no wildcard group


def test_15_wildcard_path_matching():
    """15. Test wildcard (*) within URL path rules."""
    content = """
User-agent: *
Disallow: /catalog/*/preview
"""
    parsed = parse_robots_txt(content)

    res1 = evaluate_url_permission(parsed, "/catalog/shoes/preview", "AgentAuditBot")
    assert res1["allowed"] is False

    res2 = evaluate_url_permission(parsed, "/catalog/shoes/detail", "AgentAuditBot")
    assert res2["allowed"] is True


def test_16_end_of_path_anchor_matching():
    """16. Test end-of-path ($) rule matching."""
    content = """
User-agent: *
Disallow: /*.pdf$
"""
    parsed = parse_robots_txt(content)

    res_pdf = evaluate_url_permission(parsed, "/documents/annual_report.pdf", "AgentAuditBot")
    assert res_pdf["allowed"] is False

    res_pdf_param = evaluate_url_permission(parsed, "/documents/annual_report.pdf?preview=true", "AgentAuditBot")
    assert res_pdf_param["allowed"] is True  # Does not end with .pdf


def test_17_resilience_to_malformed_syntax():
    """17. Test resilience to garbage/invalid robots.txt lines."""
    content = """
<!DOCTYPE html>
<html>
This is not a real robots file
UnknownDirective: value
User-agent: *
Disallow: /valid-blocked/
Random garbage text
"""
    parsed = parse_robots_txt(content)
    assert len(parsed.groups) == 1
    res = evaluate_url_permission(parsed, "/valid-blocked/page", "AgentAuditBot")
    assert res["allowed"] is False


@pytest.mark.asyncio
async def test_18_timeout_handling():
    """18. Test timeout handling during robots.txt retrieval."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Connection timed out")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_robots("https://example.com/page", client=client)

    assert res["success"] is False
    assert res["error"]["type"] == "timeout"


@pytest.mark.asyncio
async def test_19_connection_error():
    """19. Test connection/DNS error handling."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("getaddrinfo failed")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_robots("https://broken-domain.example", client=client)

    assert res["success"] is False
    assert res["error"]["type"] in ("dns_error", "connection_error")


@pytest.mark.asyncio
async def test_20_invalid_target_url():
    """20. Test invalid target URL rejected safely."""
    res = await check_robots("not_a_valid_url")
    assert res["success"] is False
    assert res["error"]["type"] == "invalid_url"
