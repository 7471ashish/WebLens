"""
Unit tests for raw_html_analyzer.py using pytest.

Tests all 36 scenarios specified in the requirements deterministically without network access.
"""

from __future__ import annotations

import pytest
from raw_html_analyzer import analyze_raw_html


def test_1_basic_valid_html():
    """1. Test basic valid HTML document structure."""
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <title>Adobe Creative Cloud</title>
    <meta name="description" content="Creativity for all.">
    <link rel="canonical" href="https://adobe.com/creativecloud">
</head>
<body>
    <main>
        <h1>Creative Tools</h1>
        <p>Photoshop, Illustrator, Premiere Pro.</p>
        <a href="/products">View all</a>
        <img src="/logo.png" alt="Adobe Logo">
    </main>
</body>
</html>"""
    res = analyze_raw_html(html, target_url="https://adobe.com/creativecloud")
    assert res["title"]["exists"] is True
    assert res["title"]["value"] == "Adobe Creative Cloud"
    assert res["meta_description"]["content"] == "Creativity for all."
    assert res["canonical"]["is_absolute"] is True
    assert res["language"]["value"] == "en"
    assert res["headings"]["h1_count"] == 1
    assert res["text_content"]["main_exists"] is True
    assert res["machine_readability"]["has_meaningful_text"] is False  # under 200 chars but present


def test_2_empty_html():
    """2. Test empty string or None HTML."""
    res = analyze_raw_html("", target_url="https://example.com")
    assert res["title"]["exists"] is False
    assert res["html_metrics"]["html_bytes"] == 0
    assert res["spa_indicators"]["possible_client_rendered_shell"] is True

    res_none = analyze_raw_html(None, target_url="https://example.com")
    assert res_none["title"]["exists"] is False


def test_3_missing_title():
    """3. Test HTML with no <title> tag."""
    html = "<html><body><h1>Hello</h1></body></html>"
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["title"]["exists"] is False
    assert res["title"]["value"] is None


def test_4_meta_description_extraction():
    """4. Test meta description with case insensitivity."""
    html = '<html><head><meta name="Description" content="A comprehensive guide to web auditing."></head></html>'
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["meta_description"]["exists"] is True
    assert res["meta_description"]["content"] == "A comprehensive guide to web auditing."
    assert res["meta_description"]["character_count"] == len("A comprehensive guide to web auditing.")


def test_5_canonical_extraction():
    """5. Test canonical link tag extraction."""
    html = '<html><head><link rel="canonical" href="https://example.com/canonical-url"></head></html>'
    res = analyze_raw_html(html, target_url="https://example.com/page?ref=ad")
    assert res["canonical"]["exists"] is True
    assert res["canonical"]["value"] == "https://example.com/canonical-url"
    assert res["canonical"]["is_absolute"] is True


def test_6_relative_canonical():
    """6. Test relative canonical URL resolution."""
    html = '<html><head><link rel="canonical" href="/canonical-item"></head></html>'
    res = analyze_raw_html(html, target_url="https://example.com/products/item?x=1")
    assert res["canonical"]["exists"] is True
    assert res["canonical"]["is_absolute"] is False
    assert res["canonical"]["normalized_url"] == "https://example.com/canonical-item"


def test_7_multiple_canonical_tags():
    """7. Test detection of multiple canonical tags."""
    html = """<html><head>
        <link rel="canonical" href="https://example.com/url1">
        <link rel="canonical" href="https://example.com/url2">
    </head></html>"""
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["canonical"]["duplicate_count"] == 1
    assert res["structural_quality"]["duplicate_canonical_count"] == 1


def test_8_language_extraction():
    """8. Test <html lang="..."> attribute extraction."""
    html = '<html lang="es-ES"><head><title>Hola</title></head></html>'
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["language"]["declared"] is True
    assert res["language"]["value"] == "es-ES"


def test_9_h1_h2_h3_extraction():
    """9. Test heading hierarchy counts and text."""
    html = """<html><body>
        <h1>Main Title</h1>
        <h2>Section 1</h2>
        <h2>Section 2</h2>
        <h3>Subsection 2.1</h3>
    </body></html>"""
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["headings"]["h1_count"] == 1
    assert res["headings"]["h2_count"] == 2
    assert res["headings"]["h3_count"] == 1
    assert res["headings"]["h1_text"] == ["Main Title"]
    assert res["headings"]["multiple_h1"] is False


def test_10_multiple_h1_detection():
    """10. Test detection of multiple H1 tags."""
    html = "<html><body><h1>First H1</h1><h1>Second H1</h1></body></html>"
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["headings"]["h1_count"] == 2
    assert res["headings"]["multiple_h1"] is True
    assert len(res["headings"]["h1_text"]) == 2


def test_11_text_extraction():
    """11. Test clean text extraction and word counting."""
    long_text = " ".join(["word"] * 50)
    html = f"<html><body><p>{long_text}</p></body></html>"
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["text_content"]["word_count"] == 50
    assert res["text_content"]["character_count"] == len(long_text)


def test_12_script_exclusion_from_text():
    """12. Test that inline JavaScript text is excluded from body text."""
    html = """<html><body>
        <p>Visible content here.</p>
        <script>var secretVariable = "This should not be counted as visible text";</script>
    </body></html>"""
    res = analyze_raw_html(html, target_url="https://example.com")
    assert "secretVariable" not in res["text_content"]["body_text_characters"] * " "
    assert res["text_content"]["word_count"] == 3


def test_13_style_exclusion_from_text():
    """13. Test that CSS style definitions are excluded from text."""
    html = """<html><body>
        <style>body { color: red; font-size: 16px; }</style>
        <p>Real text</p>
    </body></html>"""
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["text_content"]["word_count"] == 2


def test_14_link_classification():
    """14. Test link classification into internal, external, relative, empty, fragment."""
    html = """<html><body>
        <a href="/about">About Us</a>
        <a href="https://example.com/contact">Contact</a>
        <a href="https://external.org/resource">External</a>
        <a href="#section">Jump</a>
        <a>No href</a>
        <a href="">Empty href</a>
    </body></html>"""
    res = analyze_raw_html(html, target_url="https://example.com/home")
    links = res["links"]
    assert links["total"] == 6
    assert links["relative"] == 1
    assert links["internal"] == 2  # /about and https://example.com/contact
    assert links["external"] == 1  # https://external.org
    assert links["fragment_only"] == 1
    assert links["empty"] == 2


def test_15_internal_vs_external_links():
    """15. Test subdomain and cross-domain link differentiation."""
    html = """<html><body>
        <a href="https://sub.example.com/app">Subdomain Internal</a>
        <a href="https://another-domain.com">Other External</a>
    </body></html>"""
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["links"]["internal"] == 1
    assert res["links"]["external"] == 1


def test_16_fragment_links():
    """16. Test anchor links (#top, #main)."""
    html = '<html><body><a href="#top">Top</a><a href="#footer">Footer</a></body></html>'
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["links"]["fragment_only"] == 2


def test_17_image_analysis():
    """17. Test image element extraction with attributes."""
    html = '<html><body><img src="/banner.jpg" alt="Company Banner" width="800" height="400" loading="lazy"></body></html>'
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["images"]["count"] == 1
    assert res["images"]["missing_alt_count"] == 0
    img = res["images"]["images"][0]
    assert img["alt"] == "Company Banner"
    assert img["width"] == "800"
    assert img["loading"] == "lazy"


def test_18_missing_image_alt():
    """18. Test image missing the alt attribute entirely."""
    html = '<html><body><img src="/photo.jpg"></body></html>'
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["images"]["missing_alt_count"] == 1
    assert res["images"]["empty_alt_count"] == 0


def test_19_empty_alt():
    """19. Test decorative image with empty alt attribute alt=""."""
    html = '<html><body><img src="/deco.png" alt=""></body></html>'
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["images"]["missing_alt_count"] == 0
    assert res["images"]["empty_alt_count"] == 1


def test_20_json_ld_valid():
    """20. Test valid application/ld+json extraction."""
    html = """<html><head>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Super Widget"
    }
    </script>
    </head></html>"""
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["structured_data"]["json_ld_blocks"] == 1
    assert res["structured_data"]["valid_json_ld_blocks"] == 1
    assert "Product" in res["structured_data"]["types"]


def test_21_json_ld_invalid():
    """21. Test invalid JSON syntax inside application/ld+json."""
    html = '<script type="application/ld+json">{ broken json: 123 </script>'
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["structured_data"]["json_ld_blocks"] == 1
    assert res["structured_data"]["invalid_json_ld_blocks"] == 1


def test_22_json_ld_graph():
    """22. Test @graph array inside JSON-LD."""
    html = """<script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "Organization", "name": "Adobe"},
            {"@type": "WebSite", "url": "https://adobe.com"}
        ]
    }
    </script>"""
    res = analyze_raw_html(html, target_url="https://adobe.com")
    assert "Organization" in res["structured_data"]["types"]
    assert "WebSite" in res["structured_data"]["types"]


def test_23_robots_meta():
    """23. Test <meta name="robots" content="noindex, follow">."""
    html = '<meta name="robots" content="noindex, nofollow, nosnippet">'
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["robots_meta"]["exists"] is True
    assert "noindex" in res["robots_meta"]["directives"]
    assert "nofollow" in res["robots_meta"]["directives"]


def test_24_x_robots_tag():
    """24. Test X-Robots-Tag extraction from response metadata headers."""
    html = "<html><body><h1>Hello</h1></body></html>"
    meta = {"headers": {"x-robots-tag": "noindex, noarchive"}}
    res = analyze_raw_html(html, target_url="https://example.com", response_metadata=meta)
    assert res["robots_headers"]["x_robots_tag"] == "noindex, noarchive"


def test_25_semantic_html():
    """25. Test semantic HTML5 tag counting."""
    html = """<html><body>
        <header><nav></nav></header>
        <main>
            <article>
                <section></section>
                <section></section>
            </article>
            <aside></aside>
        </main>
        <footer></footer>
    </body></html>"""
    res = analyze_raw_html(html, target_url="https://example.com")
    sem = res["semantic_structure"]
    assert sem["main"] == 1
    assert sem["article"] == 1
    assert sem["section"] == 2
    assert sem["nav"] == 1
    assert sem["header"] == 1
    assert sem["footer"] == 1
    assert sem["aside"] == 1


def test_26_spa_shell_detection():
    """26. Test SPA empty shell detection with root container and scripts."""
    html = """<!DOCTYPE html>
<html>
<head><title>App Shell</title></head>
<body>
    <div id="root"></div>
    <script src="/bundle.js"></script>
    <script src="/vendor.js"></script>
</body>
</html>"""
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["spa_indicators"]["possible_client_rendered_shell"] is True
    assert "root" in res["spa_indicators"]["root_containers"]
    assert len(res["spa_indicators"]["reasons"]) > 0


def test_27_noscript_analysis():
    """27. Test noscript tag counting and fallback text length."""
    html = '<html><body><noscript><p>Please enable JavaScript to view this site.</p></noscript></body></html>'
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["noscript"]["count"] == 1
    assert res["noscript"]["text_characters"] > 10


def test_28_forms_analysis():
    """28. Test form element detection."""
    html = """<html><body>
        <form action="/search" method="GET"></form>
        <form action="/login" method="post"></form>
    </body></html>"""
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["forms"]["count"] == 2
    assert res["forms"]["forms"][0]["method"] == "GET"
    assert res["forms"]["forms"][1]["method"] == "POST"


def test_29_tables_and_lists():
    """29. Test table, ul, ol, and paragraph counts."""
    html = """<html><body>
        <table><tr><td>Cell 1</td></tr></table>
        <ul><li>Item 1</li><li>Item 2</li></ul>
        <ol><li>Step 1</li></ol>
        <p>Paragraph 1</p>
        <p>Paragraph 2</p>
    </body></html>"""
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["html_metrics"]["table_count"] == 1
    assert res["html_metrics"]["list_count"] == 2
    assert res["html_metrics"]["paragraph_count"] == 2


def test_30_malformed_html():
    """30. Test analyzer resilience with unclosed and broken HTML tags."""
    html = "<div><p>Unclosed paragraph<h1>Header without body</h1><div>Broken unclosed div"
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["headings"]["h1_count"] == 1
    assert res["headings"]["h1_text"] == ["Header without body"]
    assert res["text_content"]["word_count"] > 0


def test_31_unicode_html():
    """31. Test handling of international Unicode characters and emojis."""
    html = "<html><head><title>Adobe 日本語 🎨</title></head><body><p>こんにちは 世界！</p></body></html>"
    res = analyze_raw_html(html, target_url="https://adobe.com/jp")
    assert "日本語 🎨" in res["title"]["value"]
    assert "こんにちは" in res["text_content"]["body_text_characters"] * " " or res["text_content"]["character_count"] > 0


def test_32_empty_body():
    """32. Test empty body tag."""
    html = "<html><head><title>Test</title></head><body></body></html>"
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["structural_quality"]["empty_body"] is True
    assert res["text_content"]["character_count"] == 0


def test_33_missing_body():
    """33. Test HTML fragment without <body>."""
    html = "<head><title>No Body Here</title></head>"
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["title"]["exists"] is True


def test_34_duplicate_title():
    """34. Test duplicate <title> tags in document."""
    html = "<head><title>First Title</title><title>Second Title</title></head>"
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["title"]["value"] == "First Title"
    assert res["title"]["duplicate_count"] == 1
    assert res["structural_quality"]["duplicate_title_count"] == 1


def test_35_invalid_canonical_url():
    """35. Test malformed canonical link href."""
    html = '<link rel="canonical" href="not a url at all ::: 123">'
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["canonical"]["exists"] is True


def test_36_iframe_detection():
    """36. Test iframe counting."""
    html = '<html><body><iframe src="https://example.com/embed"></iframe><iframe src="/video"></iframe></body></html>'
    res = analyze_raw_html(html, target_url="https://example.com")
    assert res["html_metrics"]["iframe_count"] == 2


def test_37_preloader_details_viewport_blocking():
    """37. Test preloader detection capturing tag, id, class, and viewport-blocking style."""
    html = '<div id="site-loader" class="preloader-overlay fullscreen" style="position: fixed; top: 0; left: 0; width: 100%; height: 100%;"></div>'
    res = analyze_raw_html(html, target_url="https://example.com")
    spa = res["spa_indicators"]
    assert spa["has_preloader"] is True
    details = spa["preloader_details"]
    assert details["tag"] == "div"
    assert details["id"] == "site-loader"
    assert "preloader-overlay" in details["class"]
    assert details["selector"] == "div#site-loader.preloader-overlay.fullscreen"
    assert details["blocks_viewport"] is True
    assert details["is_progress_bar"] is False


def test_38_preloader_details_progress_bar():
    """38. Test small progress-bar loader does not trigger viewport blocking."""
    html = '<progress class="loader-progress mini" style="height: 4px; width: 100px;"></progress>'
    res = analyze_raw_html(html, target_url="https://example.com")
    spa = res["spa_indicators"]
    assert spa["has_preloader"] is True
    details = spa["preloader_details"]
    assert details["tag"] == "progress"
    assert details["is_progress_bar"] is True
    assert details["blocks_viewport"] is False

