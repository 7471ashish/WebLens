"""
Content Readability & Text Structure Analyzer (readability_analyzer.py)
----------------------------------------------------------------------
Specialized deterministic analyzer within the independent Engagement Audit subagent.

This module evaluates textual complexity, reading ease, sentence/paragraph structure,
and heading organization from structured page content evidence.

Key Evaluations:
1. Flesch Reading Ease (FRE) formula calculation
2. Flesch-Kincaid Grade Level (FKGL) estimation
3. Sentence length distribution and percentage of overly long sentences (> 25 words)
4. Word length distribution and percentage of complex/polysyllabic words (> 12 chars)
5. Syllable counting using deterministic English morphological heuristics
6. Paragraph length and density (> 120 words per paragraph)
7. Heading hierarchy progression, empty headings, and heading-to-content ratios
8. Repetitive duplicate content blocks and wall-of-text section segmentation
9. Language gating (English supported; unsupported languages marked insufficient evidence)

Architectural Constraint:
    Operates strictly in-memory on structured page evidence. Does not spawn browsers,
    make network calls, or import from crawl-render-audit.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from engagement_state import (
    CategoryResult,
    CategoryStatus,
    EngagementFinding,
    HeadingElement,
    PageInputData,
    SeverityLevel,
)

logger = logging.getLogger("engagement_audit.readability")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Configurable default analysis thresholds
MIN_WORDS_FOR_READABILITY: int = 30
DEFAULT_LONG_SENTENCE_WORDS: int = 25
DEFAULT_LONG_PARAGRAPH_WORDS: int = 120
DEFAULT_LONG_WORD_LENGTH: int = 12
DEFAULT_SECTION_WORD_LIMIT: int = 500

SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "en-us", "en-gb", "en-ca", "en-au", "english")



# Syllable Counter & Text Segmentation Helpers


def count_syllables_in_word(word: str) -> int:
    """
    Deterministic English syllable counter using morphological rules.
    Guaranteed to return >= 1 for any alphabetic token.
    """
    clean = re.sub(r"[^a-zA-Z]", "", word).lower()
    if not clean:
        return 0
    if len(clean) <= 3:
        return 1

    # Exception endings
    if clean.endswith("ed") and not clean.endswith("ted") and not clean.endswith("ded"):
        clean = clean[:-2]
    elif clean.endswith("e") and not clean.endswith("le") and not clean.endswith("ee") and not clean.endswith("oe"):
        clean = clean[:-1]

    # Count vowel groups
    vowels = "aeiouy"
    count = 0
    in_vowel = False
    for char in clean:
        if char in vowels:
            if not in_vowel:
                count += 1
                in_vowel = True
        else:
            in_vowel = False

    return max(1, count)


def segment_sentences(text: str) -> list[str]:
    """Split raw text into clean, non-empty sentences."""
    if not text:
        return []
    # Match sentence terminators (. ! ?) followed by whitespace or end of string
    raw_sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in raw_sentences if s.strip()]


def segment_words(text: str) -> list[str]:
    """Extract alphabetic/alphanumeric words from text."""
    if not text:
        return []
    return [w for w in re.findall(r"\b[a-zA-Z0-9'-]+\b", text) if any(c.isalpha() for c in w)]



# Normalized Content Models & Extraction


@dataclass
class NormalizedContent:
    """Structured extraction of main textual content, headings, and paragraphs."""
    language: str | None = None
    main_text: str = ""
    paragraphs: list[str] = field(default_factory=list)
    headings: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(self.main_text.strip() or self.paragraphs)


def extract_content_evidence(raw_data: dict[str, Any]) -> NormalizedContent:
    """
    Extract and normalize textual content, headings, paragraphs, and sections.
    """
    lang = raw_data.get("language")
    if lang:
        lang = str(lang).strip().lower()

    content_obj = raw_data.get("content")
    paragraphs: list[str] = []
    headings: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    text_chunks: list[str] = []

    # 1. Check top-level main_text or text
    if raw_data.get("main_text"):
        text_chunks.append(str(raw_data["main_text"]))
    elif raw_data.get("text"):
        text_chunks.append(str(raw_data["text"]))

    # 2. Extract from content container dict
    if isinstance(content_obj, dict):
        if not lang and content_obj.get("language"):
            lang = str(content_obj["language"]).strip().lower()

        if content_obj.get("main_text"):
            text_chunks.append(str(content_obj["main_text"]))
        elif content_obj.get("text"):
            text_chunks.append(str(content_obj["text"]))

        raw_paras = content_obj.get("paragraphs") or []
        if isinstance(raw_paras, list):
            for p in raw_paras:
                if not p:
                    continue
                p_text = ""
                if isinstance(p, dict):
                    p_text = str(p.get("text") or "")
                elif isinstance(p, str):
                    p_text = p
                if p_text and p_text.strip():
                    paragraphs.append(p_text.strip())

        raw_heads = content_obj.get("headings") or []
        if isinstance(raw_heads, list):
            for h in raw_heads:
                if not h:
                    continue
                if isinstance(h, dict):
                    lvl = 2
                    try:
                        lvl = int(h.get("level", 2))
                    except (ValueError, TypeError):
                        lvl = 2
                    headings.append({
                        "level": lvl,
                        "text": str(h.get("text", "")).strip(),
                    })
                elif isinstance(h, str):
                    headings.append({"level": 2, "text": h.strip()})

        raw_secs = content_obj.get("sections") or []
        if isinstance(raw_secs, list):
            for s in raw_secs:
                if isinstance(s, dict):
                    sections.append(s)

    # 3. Top-level paragraphs / headings / sections fallbacks
    if not paragraphs:
        top_paras = raw_data.get("paragraphs") or []
        if isinstance(top_paras, list):
            for p in top_paras:
                if not p:
                    continue
                p_text = ""
                if isinstance(p, dict):
                    p_text = str(p.get("text") or "")
                elif isinstance(p, str):
                    p_text = p
                if p_text and p_text.strip():
                    paragraphs.append(p_text.strip())

    if not headings:
        top_heads = raw_data.get("headings") or []
        if isinstance(top_heads, list):
            for h in top_heads:
                if not h:
                    continue
                if isinstance(h, HeadingElement):
                    headings.append({"level": h.level, "text": h.text.strip()})
                elif isinstance(h, dict):
                    lvl = 2
                    try:
                        lvl = int(h.get("level", 2))
                    except (ValueError, TypeError):
                        lvl = 2
                    headings.append({
                        "level": lvl,
                        "text": str(h.get("text", "")).strip(),
                    })
                elif isinstance(h, str):
                    headings.append({"level": 2, "text": h.strip()})

    if not sections:
        top_secs = raw_data.get("sections") or []
        if isinstance(top_secs, list):
            for s in top_secs:
                if isinstance(s, dict):
                    sections.append(s)

    # Build consolidated main_text if empty but paragraphs exist
    if not text_chunks and paragraphs:
        consolidated = "\n\n".join(paragraphs)
    else:
        consolidated = " ".join(text_chunks).strip()

    return NormalizedContent(
        language=lang,
        main_text=consolidated,
        paragraphs=paragraphs,
        headings=headings,
        sections=sections,
    )



# Readability Analyzer Core Logic


class ReadabilityAnalyzer:
    """
    Main evaluation engine for content readability and textual architecture.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.min_words = int(self.config.get("min_words_for_readability", MIN_WORDS_FOR_READABILITY))
        self.long_sentence_words = int(self.config.get("long_sentence_words", DEFAULT_LONG_SENTENCE_WORDS))
        self.long_paragraph_words = int(self.config.get("long_paragraph_words", DEFAULT_LONG_PARAGRAPH_WORDS))
        self.long_word_length = int(self.config.get("long_word_length", DEFAULT_LONG_WORD_LENGTH))
        self.section_word_limit = int(self.config.get("section_word_limit", DEFAULT_SECTION_WORD_LIMIT))

    def analyze(
        self,
        page_data: dict[str, Any] | PageInputData | None,
        options: dict[str, Any] | None = None,
    ) -> CategoryResult:
        """
        Execute comprehensive readability and text structure audit.
        """
        opts = {**self.config, **(options or {})}

        if isinstance(page_data, PageInputData):
            raw_dict = page_data.to_dict()
        elif isinstance(page_data, dict):
            raw_dict = page_data
        else:
            raw_dict = {}

        target_url = str(raw_dict.get("url") or raw_dict.get("target_url") or "https://example.com")
        logger.info("Starting readability analysis for %s", target_url)

        content = extract_content_evidence(raw_dict)

        # ----------------------------------------------------------------------
        # Check 1: Language Gating
        # ----------------------------------------------------------------------
        if content.language and content.language not in SUPPORTED_LANGUAGES:
            return CategoryResult(
                category="readability",
                status="insufficient_evidence",
                score=None,
                findings=[],
                metrics={
                    "language": content.language,
                    "supported": False,
                    "message": f"Readability calculation for the supplied language '{content.language}' is not supported.",
                },
                confidence=0.5,
                messages=[f"Supplied language '{content.language}' is not supported by English readability formulas."],
            )

        # ----------------------------------------------------------------------
        # Check 2: Content Presence & Minimum Word Threshold
        # ----------------------------------------------------------------------
        words = segment_words(content.main_text)
        sentences = segment_sentences(content.main_text)
        word_count = len(words)
        sentence_count = len(sentences)
        paragraph_count = len(content.paragraphs)

        if word_count < self.min_words:
            return CategoryResult(
                category="readability",
                status="insufficient_evidence",
                score=None,
                findings=[],
                metrics={
                    "word_count": word_count,
                    "sentence_count": sentence_count,
                    "paragraph_count": paragraph_count,
                    "min_words_required": self.min_words,
                    "message": (
                        f"Content contains only {word_count} words (minimum {self.min_words} required for "
                        "reliable statistical readability formulas)."
                    ),
                },
                confidence=0.5,
                messages=["Insufficient text content to calculate reliable readability metrics."],
            )

        # ----------------------------------------------------------------------
        # Statistical Computations
        # ----------------------------------------------------------------------
        total_syllables = sum(count_syllables_in_word(w) for w in words)
        total_word_chars = sum(len(w) for w in words)

        # Division-safe metrics
        safe_sentences = max(1, sentence_count)
        safe_words = max(1, word_count)

        avg_sentence_len = round(word_count / safe_sentences, 1)
        avg_word_len = round(total_word_chars / safe_words, 1)
        avg_syllables_per_word = round(total_syllables / safe_words, 2)

        # Flesch Reading Ease Formula
        # 206.835 - 1.015 * (words/sentences) - 84.6 * (syllables/words)
        fre = round(
            206.835 - (1.015 * (word_count / safe_sentences)) - (84.6 * (total_syllables / safe_words)),
            1,
        )
        fre_clamped = max(0.0, min(100.0, fre))

        # Flesch-Kincaid Grade Level Formula
        # 0.39 * (words/sentences) + 11.8 * (syllables/words) - 15.59
        fkgl = round(
            (0.39 * (word_count / safe_sentences)) + (11.8 * (total_syllables / safe_words)) - 15.59,
            1,
        )
        fkgl_clamped = max(1.0, fkgl)

        # Long sentence analysis
        long_sentences = [s for s in sentences if len(segment_words(s)) > self.long_sentence_words]
        long_sentence_pct = round((len(long_sentences) / safe_sentences) * 100, 1)

        # Long word analysis (> 12 characters)
        long_words = [w for w in words if len(w) > self.long_word_length]
        long_word_pct = round((len(long_words) / safe_words) * 100, 1)

        # Paragraph analysis
        long_paragraphs = []
        if content.paragraphs:
            for p in content.paragraphs:
                p_words = segment_words(p)
                if len(p_words) > self.long_paragraph_words:
                    long_paragraphs.append(p)
            long_para_pct = round((len(long_paragraphs) / len(content.paragraphs)) * 100, 1)
        else:
            long_para_pct = None

        findings: list[EngagementFinding] = []

        # ----------------------------------------------------------------------
        # Check 3: Flesch Reading Ease Interpretation
        # ----------------------------------------------------------------------
        if fre_clamped < 30.0:
            findings.append(EngagementFinding(
                id="ENG-READ-001",
                category="readability",
                title=f"Very difficult reading complexity (Flesch Score: {fre_clamped:.1f})",
                description=(
                    f"Content readability score of {fre_clamped:.1f}/100 indicates academic/legal complexity "
                    f"with average sentence length of {avg_sentence_len} words and {avg_syllables_per_word} syllables/word."
                ),
                severity="medium",
                confidence=0.95,
                evidence={
                    "flesch_reading_ease": fre_clamped,
                    "flesch_kincaid_grade": fkgl_clamped,
                    "avg_sentence_length": avg_sentence_len,
                    "avg_syllables_per_word": avg_syllables_per_word,
                },
                recommendation="Simplify complex phrasing, break multi-clause sentences into shorter statements, and use common terminology.",
                url=target_url,
            ))
        elif fre_clamped < 50.0:
            findings.append(EngagementFinding(
                id="ENG-READ-001",
                category="readability",
                title=f"Difficult reading ease (Flesch Score: {fre_clamped:.1f})",
                description=(
                    f"Flesch Reading Ease score of {fre_clamped:.1f}/100 indicates relatively dense reading "
                    f"(estimated grade level: {fkgl_clamped:.1f})."
                ),
                severity="low",
                confidence=0.9,
                evidence={
                    "flesch_reading_ease": fre_clamped,
                    "flesch_kincaid_grade": fkgl_clamped,
                    "avg_sentence_length": avg_sentence_len,
                },
                recommendation="Aim for a Flesch Reading Ease score between 60-70 for general audience comprehension.",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Check 4: Sentence Length Distribution
        # ----------------------------------------------------------------------
        if long_sentence_pct >= 35.0 or avg_sentence_len > 28.0:
            findings.append(EngagementFinding(
                id="ENG-READ-003",
                category="readability",
                title=f"Excessive sentence length ({long_sentence_pct:.0f}% sentences > {self.long_sentence_words} words)",
                description=(
                    f"{long_sentence_pct:.0f}% of analyzed sentences exceed {self.long_sentence_words} words "
                    f"(average sentence length is {avg_sentence_len} words)."
                ),
                severity="low",
                confidence=0.9,
                evidence={
                    "long_sentence_pct": long_sentence_pct,
                    "long_sentence_count": len(long_sentences),
                    "total_sentences": sentence_count,
                    "avg_sentence_length": avg_sentence_len,
                },
                recommendation="Break up complex compound sentences into concise statements under 20 words.",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Check 5: Word Complexity
        # ----------------------------------------------------------------------
        if long_word_pct >= 15.0:
            findings.append(EngagementFinding(
                id="ENG-READ-004",
                category="readability",
                title=f"High proportion of complex terminology ({long_word_pct:.0f}% long words)",
                description=(
                    f"{long_word_pct:.0f}% of words exceed {self.long_word_length} characters, "
                    "which may elevate cognitive reading load."
                ),
                severity="low",
                confidence=0.85,
                evidence={"long_word_pct": long_word_pct, "sample_long_words": long_words[:4]},
                recommendation="Substitute specialized jargon with plain English terminology where possible.",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Check 6: Paragraph Length
        # ----------------------------------------------------------------------
        if long_para_pct and long_para_pct >= 40.0:
            findings.append(EngagementFinding(
                id="ENG-READ-005",
                category="readability",
                title=f"Dense paragraph blocks ({long_para_pct:.0f}% paragraphs > {self.long_paragraph_words} words)",
                description=(
                    f"{long_para_pct:.0f}% of paragraphs contain more than {self.long_paragraph_words} words, "
                    "creating visual 'wall of text' fatigue."
                ),
                severity="low",
                confidence=0.9,
                evidence={
                    "long_paragraph_pct": long_para_pct,
                    "total_paragraphs": len(content.paragraphs),
                },
                recommendation="Divide lengthy paragraphs into 2-3 sentence blocks and incorporate bulleted lists.",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Check 7 & 8: Heading Structure & Empty Headings
        # ----------------------------------------------------------------------
        empty_headings: list[int] = []
        heading_levels: list[int] = []
        skipped_hierarchy = False

        for h in content.headings:
            lvl = h.get("level", 2)
            htext = h.get("text", "")
            heading_levels.append(lvl)

            if not htext:
                empty_headings.append(lvl)

        if empty_headings:
            findings.append(EngagementFinding(
                id="ENG-READ-006",
                category="readability",
                title=f"Empty or whitespace-only heading tags ({len(empty_headings)} headings)",
                description=f"Found {len(empty_headings)} heading element(s) with empty text labels.",
                severity="medium",
                confidence=0.95,
                evidence={"empty_heading_levels": empty_headings[:4]},
                recommendation="Remove empty heading tags or provide descriptive title text.",
                url=target_url,
            ))

        for idx in range(len(heading_levels) - 1):
            curr_lvl = heading_levels[idx]
            next_lvl = heading_levels[idx + 1]
            if next_lvl > curr_lvl + 1:
                skipped_hierarchy = True
                break

        if skipped_hierarchy:
            findings.append(EngagementFinding(
                id="ENG-READ-007",
                category="readability",
                title="Inconsistent heading hierarchy progression",
                description="Heading structure skips levels (e.g., H1 directly followed by H3 or H4 without intermediate H2).",
                severity="low",
                confidence=0.9,
                evidence={"heading_levels_sequence": heading_levels[:8]},
                recommendation="Structure content hierarchically with sequential heading levels (H1 -> H2 -> H3).",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Check 9: Large Sections Lacking Headings
        # ----------------------------------------------------------------------
        if word_count > self.section_word_limit and len(content.headings) == 0:
            findings.append(EngagementFinding(
                id="ENG-READ-008",
                category="readability",
                title=f"Large content body ({word_count} words) lacks structural headings",
                description=(
                    f"Page contains {word_count} words of continuous text without any structural headings "
                    "(H2/H3) to assist scanning."
                ),
                severity="low",
                confidence=0.9,
                evidence={"word_count": word_count, "heading_count": 0},
                recommendation="Introduce subheadings every 200-300 words to break content into scannable chunks.",
                url=target_url,
            ))

        # ----------------------------------------------------------------------
        # Check 11: Repeated Content Blocks
        # ----------------------------------------------------------------------
        if content.paragraphs:
            para_counts: dict[str, int] = {}
            for p in content.paragraphs:
                p_norm = p.strip().lower()
                if len(segment_words(p_norm)) >= 10:
                    para_counts[p_norm] = para_counts.get(p_norm, 0) + 1

            dup_paras = [k for k, count in para_counts.items() if count > 1]
            if dup_paras:
                findings.append(EngagementFinding(
                    id="ENG-READ-009",
                    category="readability",
                    title=f"Duplicate paragraph content detected ({len(dup_paras)} repeated blocks)",
                    description=f"Identical text blocks ({len(dup_paras)} occurrences) are repeated within main content.",
                    severity="low",
                    confidence=0.95,
                    evidence={"duplicate_paragraphs_count": len(dup_paras)},
                    recommendation="Remove redundant duplicate paragraphs to improve conciseness.",
                    url=target_url,
                ))

        # ----------------------------------------------------------------------
        # Deterministic Scoring Calculation (0 to 100)
        # ----------------------------------------------------------------------
        # Base score derived from Flesch Reading Ease (normalized to 0-100)
        base_score = fre_clamped
        for f in findings:
            if f.severity == "critical":
                base_score -= 25.0 * f.confidence
            elif f.severity == "high":
                base_score -= 15.0 * f.confidence
            elif f.severity == "medium":
                base_score -= 8.0 * f.confidence
            elif f.severity == "low":
                base_score -= 3.0 * f.confidence

        score_clamped = max(0, min(100, int(round(base_score))))

        status: CategoryStatus = "passed"
        if any(f.severity in ("critical", "high") for f in findings):
            status = "failed"
        elif fre_clamped < 30.0 or findings:
            status = "warning"

        metrics = {
            "language": content.language or "en",
            "word_count": word_count,
            "sentence_count": sentence_count,
            "paragraph_count": paragraph_count,
            "heading_count": len(content.headings),
            "average_sentence_length": avg_sentence_len,
            "average_word_length": avg_word_len,
            "average_syllables_per_word": avg_syllables_per_word,
            "flesch_reading_ease": fre_clamped,
            "flesch_kincaid_grade": fkgl_clamped,
            "long_sentence_percentage": long_sentence_pct,
            "long_word_percentage": long_word_pct,
            "long_paragraph_percentage": long_para_pct,
        }

        return CategoryResult(
            category="readability",
            status=status,
            score=score_clamped,
            findings=findings,
            metrics=metrics,
            confidence=0.95,
            messages=[
                f"Readability analysis completed: {word_count} words, {sentence_count} sentences.",
                f"Flesch Reading Ease: {fre_clamped:.1f} (Grade Level: {fkgl_clamped:.1f}, Score: {score_clamped}/100).",
            ],
        )



# Public API Function


def analyze_readability(
    page_data: dict[str, Any] | PageInputData | None,
    options: dict[str, Any] | None = None,
) -> CategoryResult:
    """
    Public entry point for Content Readability and Text Structure analysis.

    Args:
        page_data: Structured page input dictionary or PageInputData instance.
        options: Optional threshold overrides.

    Returns:
        CategoryResult containing status, score, metrics, and readability findings.
    """
    analyzer = ReadabilityAnalyzer(config=options)
    return analyzer.analyze(page_data, options=options)
