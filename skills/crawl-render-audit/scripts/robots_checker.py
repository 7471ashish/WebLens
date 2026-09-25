"""
Robots.txt Checker Module (robots_checker.py)
---------------------------------------------
Deterministic, read-only robots.txt fetcher and directive parser for the `crawl-render-audit` skill.

Architecture Role:
    AUDIT ORCHESTRATOR
            |
            v
    crawl-render-audit
            |
            +----------------+
            |                |
            v                v
     crawler.py      robots_checker.py  <-- (THIS MODULE)
            |                |
            |                v
            |         robots.txt evidence
            |                |
            +---------> FINDING BUILDER
                             |
                             v
                      structured findings

This module evaluates robots.txt compliance per standard RFC 9309 semantics, isolating
individual user-agent rules (including AI bots), sitemaps, and crawl-delays without
assigning audit severity.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

# Import low-level crawler utilities
try:
    from crawl_crawler import crawl_url, validate_and_normalize_url
except ImportError:
    from .crawl_crawler import crawl_url, validate_and_normalize_url

# Configure module-level logger
logger = logging.getLogger("crawl_render_audit.robots_checker")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Default user agents to evaluate against robots.txt directives
DEFAULT_EVAL_USER_AGENTS: list[str] = [
    "AgentAuditBot",
    "GPTBot",
    "ClaudeBot",
    "Googlebot",
    "Bingbot",
    "*",
]

DEFAULT_OPTIONS: dict[str, Any] = {
    "user_agent": "Mozilla/5.0 (compatible; AgentAuditBot/1.0)",
    "timeout_ms": 15000,
    "user_agents": DEFAULT_EVAL_USER_AGENTS,
}



# Data Structures


@dataclass
class RobotsRule:
    """Individual Allow or Disallow rule with line number evidence."""
    directive: str  # "Allow" or "Disallow"
    value: str
    line_number: int
    pattern_regex: re.Pattern[str] | None = None

    def matches(self, path: str) -> bool:
        """Check if path matches this rule."""
        if not self.value:
            # Empty value (e.g. "Disallow:") matches nothing
            return False
        if self.pattern_regex is not None:
            return bool(self.pattern_regex.search(path))
        return path.startswith(self.value)


@dataclass
class RobotsGroup:
    """A group of rules applicable to one or more user agents."""
    user_agents: list[str] = field(default_factory=list)
    rules: list[RobotsRule] = field(default_factory=list)
    crawl_delay: float | None = None


@dataclass
class ParsedRobotsTxt:
    """Parsed representation of a robots.txt file."""
    groups: list[RobotsGroup] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)
    directive_count: int = 0
    raw_text: str = ""



# Path Pattern Compiler & Robots.txt Parser


def _compile_path_pattern(pattern: str) -> re.Pattern[str]:
    """
    Compile a robots.txt path pattern to a regular expression.
    Supports wildcards (*) and end-of-path anchors ($).
    """
    if not pattern:
        return re.compile(r"^$")

    escaped = []
    i = 0
    length = len(pattern)

    while i < length:
        char = pattern[i]
        if char == "*":
            escaped.append(".*")
        elif char == "$" and i == length - 1:
            escaped.append("$")
        elif char in r"\.+?{}[]()|^":
            escaped.append("\\" + char)
        else:
            escaped.append(char)
        i += 1

    pattern_str = "".join(escaped)
    if not pattern_str.endswith("$"):
        # Match as prefix
        pattern_str = "^" + pattern_str
    else:
        pattern_str = "^" + pattern_str

    try:
        return re.compile(pattern_str)
    except re.error:
        # Fallback to exact prefix match
        return re.compile("^" + re.escape(pattern))


def parse_robots_txt(content: str) -> ParsedRobotsTxt:
    """
    Parse the contents of a robots.txt file into structured groups and directives.

    Complies with RFC 9309 robots.txt specification.
    """
    parsed = ParsedRobotsTxt(raw_text=content)
    if not content or not isinstance(content, str):
        return parsed

    lines = content.splitlines()
    current_group: RobotsGroup | None = None
    in_user_agent_block = False
    directive_count = 0

    for line_idx, raw_line in enumerate(lines, start=1):
        # Strip comments
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        if ":" not in line:
            continue

        directive, _, value = line.partition(":")
        directive_clean = directive.strip().lower()
        value_clean = value.strip()
        directive_count += 1

        if directive_clean == "user-agent":
            agent_name = value_clean
            if not in_user_agent_block or current_group is None:
                # Start a new group
                if current_group is not None and (current_group.rules or current_group.crawl_delay is not None):
                    parsed.groups.append(current_group)
                current_group = RobotsGroup()
                in_user_agent_block = True

            if agent_name:
                current_group.user_agents.append(agent_name)

        elif directive_clean in ("allow", "disallow"):
            in_user_agent_block = False
            if current_group is None:
                # Rule before any User-agent line is ignored per RFC 9309
                continue

            rule_type = "Allow" if directive_clean == "allow" else "Disallow"
            compiled_regex = _compile_path_pattern(value_clean) if value_clean else None
            rule = RobotsRule(
                directive=rule_type,
                value=value_clean,
                line_number=line_idx,
                pattern_regex=compiled_regex,
            )
            current_group.rules.append(rule)

        elif directive_clean == "crawl-delay":
            in_user_agent_block = False
            if current_group is not None:
                try:
                    current_group.crawl_delay = float(value_clean)
                except ValueError:
                    pass

        elif directive_clean == "sitemap":
            # Sitemap is a global directive
            if value_clean and value_clean not in parsed.sitemaps:
                parsed.sitemaps.append(value_clean)

    # Append trailing group
    if current_group is not None and (current_group.user_agents or current_group.rules or current_group.crawl_delay is not None):
        parsed.groups.append(current_group)

    parsed.directive_count = directive_count
    return parsed



# Rule Matching Logic (RFC 9309 Precedence)


def _find_matching_group(parsed: ParsedRobotsTxt, target_bot: str) -> RobotsGroup | None:
    """
    Find the most specific RobotsGroup for a given bot name.
    1. Exact match (case-insensitive) on User-agent.
    2. Substring/prefix match (e.g. 'GPTBot' matches 'GPTBot/1.0' or 'gptbot').
    3. Fallback to '*' wildcard group.
    """
    bot_lower = target_bot.lower().strip()
    wildcard_group: RobotsGroup | None = None

    for group in parsed.groups:
        for ua in group.user_agents:
            ua_lower = ua.lower().strip()
            if ua_lower == "*":
                wildcard_group = group
            elif ua_lower == bot_lower or ua_lower in bot_lower or bot_lower in ua_lower:
                return group

    if bot_lower == "*" and wildcard_group:
        return wildcard_group

    return wildcard_group


def evaluate_url_permission(
    parsed: ParsedRobotsTxt,
    target_path: str,
    target_bot: str,
) -> dict[str, Any]:
    """
    Evaluate whether `target_path` is allowed for `target_bot`.

    According to RFC 9309:
    - The rule with the longest matching pattern (character length) takes precedence.
    - If Allow and Disallow rules match with equal length, Allow takes precedence.
    - If no rule matches, crawling is allowed by default.
    """
    group = _find_matching_group(parsed, target_bot)
    if not group:
        return {
            "allowed": True,
            "matching_rule": None,
            "overridden_rule": None,
            "crawl_delay": None,
            "group_user_agent": None,
        }

    crawl_delay = group.crawl_delay
    group_ua = ", ".join(group.user_agents) if group.user_agents else "*"

    best_match_rule: RobotsRule | None = None
    best_match_length = -1
    matching_disallows: list[RobotsRule] = []
    matching_allows: list[RobotsRule] = []

    for rule in group.rules:
        if not rule.value:
            # Empty Disallow / Allow has no blocking effect
            continue

        if rule.matches(target_path):
            # Compute match specificity (length of rule pattern)
            rule_len = len(rule.value)

            if rule.directive == "Disallow":
                matching_disallows.append(rule)
            else:
                matching_allows.append(rule)

            if rule_len > best_match_length:
                best_match_length = rule_len
                best_match_rule = rule
            elif rule_len == best_match_length:
                # Tie-breaker: Allow overrides Disallow at equal specificity
                if rule.directive == "Allow" and best_match_rule and best_match_rule.directive == "Disallow":
                    best_match_rule = rule

    if best_match_rule is None:
        # Default allow if no rules matched
        return {
            "allowed": True,
            "matching_rule": None,
            "overridden_rule": None,
            "crawl_delay": crawl_delay,
            "group_user_agent": group_ua,
        }

    is_allowed = (best_match_rule.directive == "Allow")
    matching_rule_info = {
        "directive": best_match_rule.directive,
        "value": best_match_rule.value,
        "line_number": best_match_rule.line_number,
    }

    overridden_info = None
    if is_allowed and matching_disallows:
        # Find the most specific Disallow that was overridden by this Allow
        most_specific_disallow = max(matching_disallows, key=lambda r: len(r.value))
        overridden_info = {
            "directive": most_specific_disallow.directive,
            "value": most_specific_disallow.value,
            "line_number": most_specific_disallow.line_number,
        }

    return {
        "allowed": is_allowed,
        "matching_rule": matching_rule_info,
        "overridden_rule": overridden_info,
        "crawl_delay": crawl_delay,
        "group_user_agent": group_ua,
    }



# Main Robots Checker Interface


def get_robots_url(target_url: str) -> str:
    """Extract `<scheme>://<netloc>/robots.txt` from any target URL."""
    parsed = urlparse(target_url)
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), "/robots.txt", "", "", ""))


async def check_robots(
    target_url: str,
    options: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """
    Fetch and evaluate robots.txt for a given target URL.

    Args:
        target_url: The full target URL to audit (e.g. 'https://example.com/products/item').
        options: Optional settings dictionary (user_agents list, timeout_ms, etc.).
        client: Optional custom httpx.AsyncClient (e.g. MockTransport for testing).

    Returns:
        Structured JSON-serializable dictionary with robots.txt status, per-bot permissions,
        sitemaps, crawl-delays, and objective evidence.
    """
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    eval_user_agents: list[str] = opts.get("user_agents", DEFAULT_EVAL_USER_AGENTS)
    timeout_ms = int(opts.get("timeout_ms", DEFAULT_OPTIONS["timeout_ms"]))

    # Step 1: Validate input URL
    is_valid, norm_url, val_err = validate_and_normalize_url(target_url)
    if not is_valid or norm_url is None:
        logger.warning("Invalid URL passed to check_robots: %s (%s)", target_url, val_err)
        return {
            "success": False,
            "target_url": target_url,
            "robots_txt": None,
            "error": {
                "type": "invalid_url",
                "message": val_err or "Invalid URL supplied",
            },
        }

    robots_url = get_robots_url(norm_url)
    parsed_target = urlparse(norm_url)
    target_path = parsed_target.path if parsed_target.path else "/"
    if parsed_target.query:
        target_path += f"?{parsed_target.query}"

    logger.info("Checking robots.txt for %s -> %s", norm_url, robots_url)

    # Step 2: Fetch robots.txt via crawler HTTP layer
    crawl_opts = {
        "user_agent": opts.get("user_agent", DEFAULT_OPTIONS["user_agent"]),
        "timeout_ms": timeout_ms,
        "max_redirects": 3,
        "max_response_bytes": 500_000,  # Robots.txt is typically < 500 KB
        "allow_private_ips": opts.get("allow_private_ips", False),
        "verify_ssl": opts.get("verify_ssl", True),
    }

    crawl_res = await crawl_url(robots_url, options=crawl_opts, client=client)

    # Step 3: Handle Network / Connection Errors
    if not crawl_res.get("success"):
        err = crawl_res.get("error", {})
        logger.warning("Failed to fetch robots.txt for %s: %s", robots_url, err)
        return {
            "success": False,
            "target_url": norm_url,
            "robots_txt": {
                "url": robots_url,
                "found": False,
                "status": None,
                "resource_state": "unavailable",
                "error": err,
            },
            "error": err,
            "resource_state": "unavailable",
        }

    status_code = crawl_res.get("status_code", 0)

    # Step 4: Handle 404 (Not Found) - Robots.txt absent -> All crawlers allowed by default
    if status_code == 404:
        logger.info("robots.txt returned 404 Not Found at %s (crawling is permitted)", robots_url)
        user_agent_results = {}
        for bot in eval_user_agents:
            user_agent_results[bot] = {
                "allowed": True,
                "matching_rule": None,
                "overridden_rule": None,
                "crawl_delay": None,
                "note": "robots.txt not found (404); unrestricted access assumed",
            }

        return {
            "success": True,
            "target_url": norm_url,
            "robots_txt": {
                "url": robots_url,
                "found": False,
                "status": 404,
                "resource_state": "missing",
                "content_type": crawl_res.get("content_type"),
                "response_time_ms": crawl_res.get("response_time_ms"),
            },
            "user_agent_results": user_agent_results,
            "sitemaps": [],
            "parser_metadata": {
                "directive_count": 0,
                "user_agent_groups": 0,
            },
            "resource_state": "missing",
        }

    # Step 5: Handle 403 or 5xx Retrieval Failures
    if status_code != 200:
        logger.warning("robots.txt returned non-200 HTTP status %d at %s", status_code, robots_url)
        res_state = "forbidden" if status_code in (401, 403) else ("unavailable" if status_code in (408, 502, 503, 504) else "error")
        return {
            "success": True,  # Network operation succeeded, but HTTP returned non-200
            "target_url": norm_url,
            "robots_txt": {
                "url": robots_url,
                "found": False,
                "status": status_code,
                "resource_state": res_state,
                "content_type": crawl_res.get("content_type"),
                "response_time_ms": crawl_res.get("response_time_ms"),
            },
            "user_agent_results": {
                bot: {
                    "allowed": None,
                    "matching_rule": None,
                    "crawl_delay": None,
                    "note": f"robots.txt could not be retrieved (HTTP {status_code})",
                }
                for bot in eval_user_agents
            },
            "sitemaps": [],
            "parser_metadata": {
                "directive_count": 0,
                "user_agent_groups": 0,
            },
            "resource_state": res_state,
        }

    # Step 6: Parse 200 OK Robots.txt Content
    body_content = crawl_res.get("body") or ""
    parsed_robots = parse_robots_txt(body_content)

    # Step 7: Evaluate permissions for all requested user agents
    user_agent_results = {}
    for bot in eval_user_agents:
        eval_res = evaluate_url_permission(parsed_robots, target_path, bot)
        user_agent_results[bot] = eval_res

    logger.info(
        "Robots.txt parsed successfully for %s: %d groups, %d sitemaps",
        robots_url,
        len(parsed_robots.groups),
        len(parsed_robots.sitemaps),
    )

    # Step 8: Assemble Return Object
    return {
        "success": True,
        "target_url": norm_url,
        "robots_txt": {
            "url": robots_url,
            "found": True,
            "status": 200,
            "resource_state": "present",
            "content_type": crawl_res.get("content_type"),
            "response_time_ms": crawl_res.get("response_time_ms"),
        },
        "user_agent_results": user_agent_results,
        "sitemaps": parsed_robots.sitemaps,
        "parser_metadata": {
            "directive_count": parsed_robots.directive_count,
            "user_agent_groups": len(parsed_robots.groups),
        },
        "resource_state": "present",
    }


def check_robots_sync(
    target_url: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Synchronous convenience wrapper around `check_robots`."""
    return asyncio.run(check_robots(target_url, options))



# CLI Testing Interface


def _cli_entrypoint() -> None:
    """CLI runner for direct command-line verification."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python robots_checker.py <TARGET_URL>")
        print("Example: python robots_checker.py https://example.com/products/item")
        sys.exit(0)

    url = sys.argv[1]
    result = check_robots_sync(url)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _cli_entrypoint()
