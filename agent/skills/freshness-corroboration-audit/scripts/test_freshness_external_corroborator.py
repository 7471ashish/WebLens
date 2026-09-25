"""
Unit tests for the independent external corroborator module.

Tests all 26 requirements using mocked HTTP calls without live external network dependencies.
"""

from __future__ import annotations

import io
import json
import socket
import urllib.error
import urllib.response
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from external_corroborator import corroborate


def _make_mock_response(
    status_code: int = 200,
    body: bytes = b"<html><head><title>Test</title></head><body></body></html>",
    headers: dict[str, str] | None = None,
) -> Any:
    """Helper to mock an HTTP response object."""
    hdrs = headers or {"content-type": "text/html"}
    resp = MagicMock()
    resp.status = status_code
    resp.code = status_code
    resp.headers = hdrs
    resp.read.side_effect = [body, b""]
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = None
    return resp


# 1. Valid sameAs URL
@patch("urllib.request.build_opener")
def test_valid_sameas_url(mock_opener: Any) -> None:
    html = "<html><head><title>Adobe Corp</title></head></html>"
    mock_inst = MagicMock()
    mock_inst.open.return_value = _make_mock_response(200, html.encode("utf-8"))
    mock_opener.return_value = mock_inst

    entities = [
        {
            "id": "e1",
            "name": "Adobe Corp",
            "sameAs": ["https://example.org/adobe"],
        }
    ]
    res = corroborate("https://adobe.com", entities, options={"allow_local": True})
    assert res["status"] == "success"
    assert res["summary"]["sources_fetched"] == 1
    assert res["identity_checks"][0]["status"] == "probable_identity_match"


# 2. Invalid URL
def test_invalid_url() -> None:
    entities = [
        {
            "id": "e2",
            "name": "Bad URL",
            "sameAs": ["not-a-url", "ftp://unsupported.com"],
        }
    ]
    res = corroborate("https://example.com", entities)
    assert res["identity_checks"][0]["status"] == "invalid_reference"


# 3. HTTP Success
@patch("urllib.request.build_opener")
def test_http_success(mock_opener: Any) -> None:
    mock_inst = MagicMock()
    mock_inst.open.return_value = _make_mock_response(200, b"<html><title>OK</title></html>")
    mock_opener.return_value = mock_inst

    entities = [{"id": "e3", "sameAs": ["https://example.org/ok"]}]
    res = corroborate("https://example.com", entities, options={"allow_local": True})
    assert res["summary"]["sources_successful"] == 1
    assert res["sources"][0]["fetch_status"] == "success"
    assert res["sources"][0]["status_code"] == 200


# 4. HTTP Failure (e.g. 404)
@patch("urllib.request.build_opener")
def test_http_failure(mock_opener: Any) -> None:
    mock_inst = MagicMock()
    mock_inst.open.side_effect = urllib.error.HTTPError(
        "https://example.org/404", 404, "Not Found", {}, None  # type: ignore
    )
    mock_opener.return_value = mock_inst

    entities = [{"id": "e4", "sameAs": ["https://example.org/404"]}]
    res = corroborate("https://example.com", entities, options={"allow_local": True})
    assert res["summary"]["sources_failed"] == 1
    assert res["sources"][0]["status_code"] == 404


# 5. Timeout handling
@patch("urllib.request.build_opener")
def test_timeout(mock_opener: Any) -> None:
    mock_inst = MagicMock()
    mock_inst.open.side_effect = socket.timeout("Timed out")
    mock_opener.return_value = mock_inst

    entities = [{"id": "e5", "sameAs": ["https://example.org/timeout"]}]
    res = corroborate("https://example.com", entities, options={"allow_local": True})
    assert res["sources"][0]["fetch_status"] == "timeout"


# 6. Redirect followed
@patch("urllib.request.build_opener")
def test_redirect(mock_opener: Any) -> None:
    mock_inst = MagicMock()
    # First returns 301 to /dest, second returns 200
    mock_inst.open.side_effect = [
        urllib.error.HTTPError("https://example.org/src", 301, "Moved", {"location": "https://example.org/dest"}, None),  # type: ignore
        _make_mock_response(200, b"<html><title>Redirected</title></html>"),
    ]
    mock_opener.return_value = mock_inst

    entities = [{"id": "e6", "sameAs": ["https://example.org/src"]}]
    res = corroborate("https://example.com", entities, options={"allow_local": True})
    assert res["sources"][0]["redirect_count"] == 1
    assert res["sources"][0]["final_url"] == "https://example.org/dest"
    assert res["sources"][0]["fetch_status"] == "success"


# 7. Redirect loop
@patch("urllib.request.build_opener")
def test_redirect_loop(mock_opener: Any) -> None:
    mock_inst = MagicMock()
    mock_inst.open.side_effect = [
        urllib.error.HTTPError("https://example.org/a", 302, "Found", {"location": "https://example.org/b"}, None),  # type: ignore
        urllib.error.HTTPError("https://example.org/b", 302, "Found", {"location": "https://example.org/a"}, None),  # type: ignore
    ]
    mock_opener.return_value = mock_inst

    entities = [{"id": "e7", "sameAs": ["https://example.org/a"]}]
    res = corroborate("https://example.com", entities, options={"allow_local": True})
    assert res["sources"][0]["fetch_status"] == "redirect_loop"


# 8. Redirect to private address
@patch("urllib.request.build_opener")
def test_redirect_to_private_address(mock_opener: Any) -> None:
    mock_inst = MagicMock()
    mock_inst.open.side_effect = [
        urllib.error.HTTPError("https://example.org/hop", 302, "Found", {"location": "http://127.0.0.1/admin"}, None),  # type: ignore
    ]
    mock_opener.return_value = mock_inst

    entities = [{"id": "e8", "sameAs": ["https://example.org/hop"]}]
    res = corroborate("https://example.com", entities, options={"allow_local": False})
    assert res["sources"][0]["fetch_status"] == "blocked_private_address"


# 9. Localhost blocking
def test_localhost_blocking() -> None:
    entities = [{"id": "e9", "sameAs": ["http://localhost:8080/info"]}]
    res = corroborate("https://example.com", entities, options={"allow_local": False})
    assert res["sources"][0]["fetch_status"] == "blocked_private_address"


# 10. Private IP blocking
def test_private_ip_blocking() -> None:
    entities = [{"id": "e10", "sameAs": ["http://192.168.1.100/router"]}]
    res = corroborate("https://example.com", entities, options={"allow_local": False})
    assert res["sources"][0]["fetch_status"] == "blocked_private_address"


# 11. Oversized response
@patch("urllib.request.build_opener")
def test_oversized_response(mock_opener: Any) -> None:
    resp = MagicMock()
    resp.status = 200
    resp.code = 200
    resp.headers = {"content-type": "text/html"}
    resp.read.side_effect = [b"A" * 1000, b"B" * 1000]
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = None

    mock_inst = MagicMock()
    mock_inst.open.return_value = resp
    mock_opener.return_value = mock_inst

    entities = [{"id": "e11", "sameAs": ["https://example.org/big"]}]
    res = corroborate("https://example.com", entities, options={"max_response_bytes": 500, "allow_local": True})
    assert res["sources"][0]["fetch_status"] == "response_too_large"


# 12. Duplicate URL deduplication
@patch("urllib.request.build_opener")
def test_duplicate_url_deduplication(mock_opener: Any) -> None:
    mock_inst = MagicMock()
    mock_inst.open.return_value = _make_mock_response(200, b"<html><title>Page</title></html>")
    mock_opener.return_value = mock_inst

    entities = [
        {"id": "e12a", "sameAs": ["https://example.org/shared"]},
        {"id": "e12b", "sameAs": ["https://example.org/shared"]},
    ]
    res = corroborate("https://example.com", entities, options={"allow_local": True})
    assert res["summary"]["sources_fetched"] == 1


# 13. Duplicate domain limiting
@patch("urllib.request.build_opener")
def test_duplicate_domain_limiting(mock_opener: Any) -> None:
    mock_inst = MagicMock()
    mock_inst.open.return_value = _make_mock_response(200, b"<html><title>P</title></html>")
    mock_opener.return_value = mock_inst

    entities = [
        {
            "id": "e13",
            "sameAs": [
                "https://domain.com/p1",
                "https://domain.com/p2",
                "https://domain.com/p3",
            ],
        }
    ]
    res = corroborate(
        "https://example.com",
        entities,
        options={"max_sources_per_domain": 2, "max_sameas_urls_per_entity": 5, "allow_local": True},
    )
    # Only 2 from domain.com should be fetched
    assert res["summary"]["sources_fetched"] == 2


# 14. sameAs identity match
@patch("urllib.request.build_opener")
def test_sameas_identity_match(mock_opener: Any) -> None:
    html = """
    <html>
      <head>
        <title>Jane Doe (Profile)</title>
        <link rel="canonical" href="https://example.org/jane">
        <script type="application/ld+json">
        {
          "@type": "Person",
          "name": "Jane Doe"
        }
        </script>
      </head>
    </html>
    """
    mock_inst = MagicMock()
    mock_inst.open.return_value = _make_mock_response(200, html.encode("utf-8"))
    mock_opener.return_value = mock_inst

    entities = [
        {
            "id": "e14",
            "types": ["Person"],
            "name": "Jane Doe",
            "sameAs": ["https://example.org/jane"],
        }
    ]
    res = corroborate("https://example.com", entities, options={"allow_local": True})
    assert res["identity_checks"][0]["status"] == "strong_identity_match"
    assert "name_match" in res["identity_checks"][0]["signals"]
    assert "type_compatible" in res["identity_checks"][0]["signals"]


# 15. sameAs identity mismatch
@patch("urllib.request.build_opener")
def test_sameas_identity_mismatch(mock_opener: Any) -> None:
    html = "<html><head><title>Totally Unrelated Organization</title></head></html>"
    mock_inst = MagicMock()
    mock_inst.open.return_value = _make_mock_response(200, html.encode("utf-8"))
    mock_opener.return_value = mock_inst

    entities = [
        {
            "id": "e15",
            "name": "John Doe",
            "sameAs": ["https://example.org/unrelated"],
        }
    ]
    res = corroborate("https://example.com", entities, options={"allow_local": True})
    assert res["identity_checks"][0]["status"] == "weak_identity_match"


# 16. Reachable source with insufficient evidence
@patch("urllib.request.build_opener")
def test_reachable_source_with_insufficient_evidence(mock_opener: Any) -> None:
    html = "<html><body>No metadata here</body></html>"
    mock_inst = MagicMock()
    mock_inst.open.return_value = _make_mock_response(200, html.encode("utf-8"))
    mock_opener.return_value = mock_inst

    entities = [
        {
            "id": "e16",
            "name": "Entity Name",
            "sameAs": ["https://example.org/empty"],
        }
    ]
    res = corroborate("https://example.com", entities, options={"allow_local": True})
    assert res["identity_checks"][0]["status"] == "reachable_no_identity_evidence"


# 17. Date corroboration (supported)
@patch("urllib.request.build_opener")
def test_date_corroboration_supported(mock_opener: Any) -> None:
    html = """
    <html>
      <head>
        <meta property="article:modified_time" content="2025-05-01T12:00:00Z">
      </head>
    </html>
    """
    mock_inst = MagicMock()
    mock_inst.open.return_value = _make_mock_response(200, html.encode("utf-8"))
    mock_opener.return_value = mock_inst

    entities = [
        {
            "id": "e17",
            "dateModified": "2025-05-01",
            "sameAs": ["https://example.org/doc"],
        }
    ]
    res = corroborate("https://example.com", entities, options={"allow_local": True})
    assert res["summary"]["claims_supported"] == 1
    assert res["claim_checks"][0]["result"] == "supported"


# 18. Date disagreement
@patch("urllib.request.build_opener")
def test_date_disagreement(mock_opener: Any) -> None:
    html = """
    <html>
      <head>
        <meta property="article:modified_time" content="2025-08-10T00:00:00Z">
      </head>
    </html>
    """
    mock_inst = MagicMock()
    mock_inst.open.return_value = _make_mock_response(200, html.encode("utf-8"))
    mock_opener.return_value = mock_inst

    entities = [
        {
            "id": "e18",
            "dateModified": "2025-01-01",
            "sameAs": ["https://example.org/doc2"],
        }
    ]
    res = corroborate("https://example.com", entities, options={"allow_local": True})
    assert res["summary"]["claims_disagreed"] == 1
    assert res["claim_checks"][0]["result"] == "disagreement"
    # Verify NO severity generated
    serialized = json.dumps(res)
    assert '"severity"' not in serialized


# 19. Multiple sources
@patch("urllib.request.build_opener")
def test_multiple_sources(mock_opener: Any) -> None:
    mock_inst = MagicMock()
    mock_inst.open.side_effect = [
        _make_mock_response(200, b"<html><title>S1</title></html>"),
        _make_mock_response(200, b"<html><title>S2</title></html>"),
    ]
    mock_opener.return_value = mock_inst

    entities = [
        {"id": "e19a", "sameAs": ["https://s1.org/page"]},
        {"id": "e19b", "sameAs": ["https://s2.org/page"]},
    ]
    res = corroborate("https://example.com", entities, options={"allow_local": True})
    assert res["summary"]["sources_fetched"] == 2


# 20. Source failure without aborting entire run
@patch("urllib.request.build_opener")
def test_source_failure_continues(mock_opener: Any) -> None:
    mock_inst = MagicMock()
    mock_inst.open.side_effect = [
        socket.timeout("Timeout S1"),
        _make_mock_response(200, b"<html><title>S2 Success</title></html>"),
    ]
    mock_opener.return_value = mock_inst

    entities = [
        {"id": "e20a", "sameAs": ["https://fail.org/bad"]},
        {"id": "e20b", "sameAs": ["https://good.org/ok"]},
    ]
    res = corroborate("https://example.com", entities, options={"allow_local": True})
    assert res["summary"]["sources_fetched"] == 2
    assert res["summary"]["sources_failed"] == 1
    assert res["summary"]["sources_successful"] == 1


# 21. max_external_sources limit
@patch("urllib.request.build_opener")
def test_max_external_sources(mock_opener: Any) -> None:
    mock_inst = MagicMock()
    mock_inst.open.return_value = _make_mock_response(200, b"<html><title>P</title></html>")
    mock_opener.return_value = mock_inst

    entities = [{"id": f"e21_{i}", "sameAs": [f"https://source{i}.org/"]} for i in range(10)]
    res = corroborate("https://example.com", entities, options={"max_external_sources": 3, "allow_local": True})
    assert res["summary"]["sources_fetched"] == 3


# 22. max_claims limit
@patch("urllib.request.build_opener")
def test_max_claims(mock_opener: Any) -> None:
    html = '<html><head><meta property="article:published_time" content="2025-01-01"></head></html>'
    mock_inst = MagicMock()
    mock_inst.open.return_value = _make_mock_response(200, html.encode("utf-8"))
    mock_opener.return_value = mock_inst

    entities = [
        {"id": f"e22_{i}", "datePublished": "2025-01-01", "sameAs": [f"https://s{i}.org/"]}
        for i in range(10)
    ]
    res = corroborate("https://example.com", entities, options={"max_claims": 2, "allow_local": True})
    assert len(res["claim_checks"]) <= 2


# 23. Total budget exhaustion
@patch("time.monotonic")
@patch("urllib.request.build_opener")
def test_total_budget_exhaustion(mock_opener: Any, mock_time: Any) -> None:
    current_t = [0.0]

    def advancing_time() -> float:
        current_t[0] += 30.0
        return current_t[0]

    mock_time.side_effect = advancing_time
    mock_inst = MagicMock()
    mock_inst.open.side_effect = lambda *args, **kwargs: _make_mock_response(200, b"<html><title>P1</title></html>")
    mock_opener.return_value = mock_inst

    entities = [
        {"id": "e23a", "sameAs": ["https://s1.org/"]},
        {"id": "e23b", "sameAs": ["https://s2.org/"]},
    ]
    res = corroborate("https://example.com", entities, options={"total_budget_ms": 50000, "allow_local": True})
    assert any(obs["code"] == "corroboration_budget_exhausted" for obs in res["observations"])


# 24. Deterministic output
@patch("urllib.request.build_opener")
def test_deterministic_output(mock_opener: Any) -> None:
    mock_inst = MagicMock()
    mock_inst.open.side_effect = lambda *args, **kwargs: _make_mock_response(200, b"<html><title>Page</title></html>")
    mock_opener.return_value = mock_inst

    entities = [{"id": "e24", "sameAs": ["https://example.org/stable"]}]
    res1 = corroborate("https://example.com", entities, options={"allow_local": True})
    res2 = corroborate("https://example.com", entities, options={"allow_local": True})
    assert res1["summary"] == res2["summary"]
    assert res1["identity_checks"] == res2["identity_checks"]


# 25. No credentials/cookies sent
@patch("urllib.request.Request")
@patch("urllib.request.build_opener")
def test_no_credentials_sent(mock_opener: Any, mock_request_cls: Any) -> None:
    mock_inst = MagicMock()
    mock_inst.open.return_value = _make_mock_response(200, b"<html><title>Safe</title></html>")
    mock_opener.return_value = mock_inst

    entities = [{"id": "e25", "sameAs": ["https://example.org/safe"]}]
    corroborate("https://example.com", entities, options={"allow_local": True})

    # Inspect headers passed to urllib.request.Request
    for call in mock_request_cls.call_args_list:
        headers = call[1].get("headers", {})
        assert "cookie" not in [k.lower() for k in headers.keys()]
        assert "authorization" not in [k.lower() for k in headers.keys()]


# 26. Unsupported content type
@patch("urllib.request.build_opener")
def test_unsupported_content_type(mock_opener: Any) -> None:
    mock_inst = MagicMock()
    mock_inst.open.return_value = _make_mock_response(
        200, b"\x00\x01\x02\x03", headers={"content-type": "application/octet-stream"}
    )
    mock_opener.return_value = mock_inst

    entities = [{"id": "e26", "sameAs": ["https://example.org/binary"]}]
    res = corroborate("https://example.com", entities, options={"allow_local": True})
    assert res["sources"][0]["fetch_status"] == "unsupported_content_type"
