"""
Regression tests for HTML <title> parsing.
Validates:
1. valid title
2. missing title
3. malformed/unclosed title
4. title followed by body content
5. multiple title tags
"""

from __future__ import annotations

import os
import sys
import pytest
from bs4 import BeautifulSoup

WORKSPACE_ROOT = os.path.abspath(os.path.dirname(__file__))
SCRIPTS_DIR = os.path.join(WORKSPACE_ROOT, "skills", "crawl-render-audit", "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from raw_html_analyzer import analyze_raw_html, extract_clean_title


def test_1_valid_title():
    """1. Valid standard title extraction."""
    html = "<!DOCTYPE html><html><head><title>Adobe University Hackathon 2026</title></head><body><h1>Welcome</h1></body></html>"
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["title"]["exists"] is True
    assert res["title"]["value"] == "Adobe University Hackathon 2026"
    assert res["title"]["duplicate_count"] == 0


def test_2_missing_title():
    """2. Missing title in document."""
    html = "<!DOCTYPE html><html><head><meta name='description' content='test'></head><body><h1>No Title</h1></body></html>"
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["title"]["exists"] is False
    assert res["title"]["value"] is None
    assert res["title"]["duplicate_count"] == 0


def test_3_malformed_unclosed_title():
    """3. Malformed / unclosed title does not swallow subsequent body or child elements."""
    html = """<html>
<head>
<title>Malformed Document
<body>
<h1>Broken structure</h1>
<p>text"""
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["title"]["exists"] is True
    assert res["title"]["value"] == "Malformed Document"
    assert "Broken structure" not in res["title"]["value"]
    assert "<body>" not in res["title"]["value"]


def test_4_title_followed_by_body_content():
    """4. Unclosed title followed immediately by body content."""
    html = "<html><head><title>Page Title<body><p>Body text here</p></body></html>"
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["title"]["exists"] is True
    assert res["title"]["value"] == "Page Title"
    assert "Body text here" not in res["title"]["value"]


def test_5_multiple_title_tags():
    """5. Multiple title tags handled deterministically."""
    html = """<html>
<head>
    <title>First Canonical Title</title>
    <title>Second Ignored Title</title>
    <title>Third Ignored Title</title>
</head>
<body></body>
</html>"""
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["title"]["exists"] is True
    assert res["title"]["value"] == "First Canonical Title"
    assert res["title"]["duplicate_count"] == 2
    assert res["structural_quality"]["duplicate_title_count"] == 2


def test_title_whitespace_stripping():
    """Title with leading/trailing whitespace and newlines is cleanly stripped."""
    html = "<html><head><title>   \n\t  Spaced Out Title  \r\n </title></head></html>"
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["title"]["exists"] is True
    assert res["title"]["value"] == "Spaced Out Title"


def test_title_with_entities():
    """Title with valid HTML entities (&amp;, &lt;, &gt;) preserved correctly."""
    html = "<html><head><title>Brand &amp; Co &lt;Global&gt;</title></head></html>"
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["title"]["exists"] is True
    assert res["title"]["value"] == "Brand & Co <Global>"
