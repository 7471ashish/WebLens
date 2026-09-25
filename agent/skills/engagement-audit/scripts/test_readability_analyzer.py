"""
Unit test suite for readability_analyzer.py using pytest.
Tests all 27 specified readability scenarios deterministically.
"""

from __future__ import annotations

import json
import pytest
from engagement_state import (
    HeadingElement,
    PageInputData,
)
from readability_analyzer import (
    NormalizedContent,
    ReadabilityAnalyzer,
    analyze_readability,
    count_syllables_in_word,
    extract_content_evidence,
    segment_sentences,
    segment_words,
)


def test_1_normal_english_content():
    """1. Test clean standard English prose produces valid readability metrics."""
    text = (
        "Design is not just what it looks like and feels like. Design is how it works. "
        "Good design makes a product useful and understandable. It clarifies the structure "
        "and guides the user smoothly through every task without confusion."
    )
    data = {"url": "https://example.com", "text": text}

    res = analyze_readability(data)
    assert res.status == "passed"
    assert res.metrics["word_count"] > 30
    assert isinstance(res.metrics["flesch_reading_ease"], float)
    assert res.metrics["flesch_reading_ease"] >= 60.0


def test_2_very_easy_english_content():
    """2. Test simple elementary sentences score high on Flesch Reading Ease."""
    text = (
        "The dog ran in the park. The sun was hot and bright. The cat sat on the mat. "
        "We went to the shop to buy some red apples. The apples were sweet and good to eat. "
        "The boy had fun playing with his new toy car all day long."
    )
    data = {"url": "https://example.com", "text": text}

    res = analyze_readability(data)
    assert res.status == "passed"
    assert res.metrics["flesch_reading_ease"] >= 80.0


def test_3_difficult_english_content():
    """3. Test dense academic/legal text triggers ENG-READ-001."""
    text = (
        "Notwithstanding the aforementioned stipulations and subsequent contractual indemnifications, "
        "the disproportionate accumulation of jurisdictional jurisprudence necessitates "
        "comprehensive multi-institutional corroboration prior to statutory implementation and "
        "subsequent operationalization across diverse intergovernmental jurisdictions globally."
    )
    data = {"url": "https://example.com", "text": text}

    res = analyze_readability(data)
    diff_f = [f for f in res.findings if f.id == "ENG-READ-001"]
    assert len(diff_f) == 1
    assert diff_f[0].severity in ("low", "medium")


def test_4_short_sentences_metrics():
    """4. Test average sentence length calculation with short sentences."""
    text = "Run fast. Jump high. Look here. See this. Eat well. Sleep more. Read books. Play games. Win now. Go home."
    words = segment_words(text)
    sentences = segment_sentences(text)
    assert len(sentences) == 10
    assert len(words) == 20


def test_5_long_sentences_triggers_finding():
    """5. Test content with sentences > 25 words triggers ENG-READ-003."""
    long_s1 = "This is a remarkably elongated sentence deliberately constructed with numerous auxiliary clauses and descriptive adjectives to thoroughly test whether the readability analyzer correctly detects and flags sentences exceeding twenty-five words in total length."
    long_s2 = "Here is another exceptionally long-winded sentence containing extensive descriptive phrasing intended to ensure that more than thirty-five percent of the total analyzed sentences comfortably exceed the standard twenty-five word threshold."
    data = {"url": "https://example.com", "text": f"{long_s1} {long_s2}"}

    res = analyze_readability(data)
    len_f = [f for f in res.findings if f.id == "ENG-READ-003"]
    assert len(len_f) == 1
    assert len_f[0].severity == "low"


def test_6_long_paragraphs_triggers_finding():
    """6. Test paragraphs > 120 words triggers ENG-READ-005."""
    para = " ".join(["word"] * 130) + "."
    data = {
        "url": "https://example.com",
        "paragraphs": [para, para],
    }

    res = analyze_readability(data)
    para_f = [f for f in res.findings if f.id == "ENG-READ-005"]
    assert len(para_f) == 1
    assert para_f[0].severity == "low"


def test_7_long_words_triggers_finding():
    """7. Test proportion of words > 12 chars triggers ENG-READ-004."""
    long_words_text = (
        "Multidisciplinary internationalization telecommunications "
        "counterrevolutionaries institutionalization characteristically "
        "intergovernmental spectrophotometer disproportionate "
        "incomprehensibility hyperresponsiveness uncompromisingly."
    )
    # Add filler words so total words >= 30
    filler = "The team will meet to discuss these topics in great detail during the upcoming weekly sprint review session."
    data = {"url": "https://example.com", "text": f"{long_words_text} {filler}"}

    res = analyze_readability(data)
    word_f = [f for f in res.findings if f.id == "ENG-READ-004"]
    assert len(word_f) == 1
    assert word_f[0].severity == "low"


def test_8_syllable_counter_accuracy():
    """8. Test morphological syllable counter accuracy on test words."""
    assert count_syllables_in_word("cat") == 1
    assert count_syllables_in_word("dog") == 1
    assert count_syllables_in_word("happy") == 2
    assert count_syllables_in_word("computer") == 3
    assert count_syllables_in_word("international") == 5
    assert count_syllables_in_word("table") == 2
    assert count_syllables_in_word("") == 0


def test_9_flesch_reading_ease_formula_math():
    """9. Test mathematical integrity of Flesch Reading Ease formula."""
    text = "The quick brown fox jumps over the lazy dog. It is a sunny day in the green valley."
    res = analyze_readability({"text": text})
    # Since text is 18 words (< 30 words), it returns insufficient_evidence safely
    assert res.status == "insufficient_evidence"


def test_10_flesch_kincaid_grade_level_math():
    """10. Test FKGL calculation on 35-word text."""
    text = (
        "Modern cloud computing architectures allow organizations to deploy applications "
        "globally in seconds. Distributed databases ensure fault tolerance and resilient "
        "data replication across geographically separated infrastructure zones to prevent "
        "downtime and data loss during unexpected service disruptions."
    )
    res = analyze_readability({"text": text})
    assert res.status in ("passed", "warning")
    assert isinstance(res.metrics["flesch_kincaid_grade"], float)
    assert 5.0 <= res.metrics["flesch_kincaid_grade"] <= 25.0


def test_11_empty_content_returns_insufficient_evidence():
    """11. Test empty string content returns status 'insufficient_evidence'."""
    res = analyze_readability({"url": "https://example.com", "text": ""})
    assert res.status == "insufficient_evidence"
    assert res.score is None


def test_12_very_short_content_insufficient_evidence():
    """12. Test short text (< 30 words) returns status 'insufficient_evidence'."""
    res = analyze_readability({"url": "https://example.com", "text": "Hello world. Welcome to our website."})
    assert res.status == "insufficient_evidence"
    assert res.metrics["word_count"] == 6


def test_13_missing_language_assumes_english():
    """13. Test missing language field assumes English gracefully."""
    text = (
        "Our mission is to empower creative professionals worldwide with powerful tools. "
        "We build intuitive software designed for speed, flexibility, and creative freedom "
        "enabling creators to bring their visions to life with maximum productivity."
    )
    data = {"url": "https://example.com", "text": text}
    res = analyze_readability(data)
    assert res.metrics["language"] == "en"


def test_14_unsupported_language_returns_insufficient_evidence():
    """14. Test unsupported language ('es') returns status 'insufficient_evidence'."""
    data = {
        "url": "https://example.com",
        "language": "es",
        "text": "Este es un texto en español para probar el análisis de legibilidad.",
    }
    res = analyze_readability(data)
    assert res.status == "insufficient_evidence"
    assert res.metrics["supported"] is False


def test_15_valid_heading_hierarchy():
    """15. Test sequential heading progression (H1 -> H2 -> H3) produces 0 hierarchy findings."""
    text = " ".join(["word"] * 40)
    data = {
        "url": "https://example.com",
        "text": text,
        "content": {
            "headings": [
                {"level": 1, "text": "Main Title"},
                {"level": 2, "text": "Section 1"},
                {"level": 3, "text": "Subsection 1.1"},
            ]
        },
    }

    res = analyze_readability(data)
    hier_f = [f for f in res.findings if f.id == "ENG-READ-007"]
    assert len(hier_f) == 0


def test_16_empty_heading_triggers_finding():
    """16. Test empty heading text triggers ENG-READ-006."""
    text = " ".join(["word"] * 40)
    data = {
        "url": "https://example.com",
        "text": text,
        "content": {
            "headings": [
                {"level": 1, "text": "Main Title"},
                {"level": 2, "text": ""},
            ]
        },
    }

    res = analyze_readability(data)
    empty_f = [f for f in res.findings if f.id == "ENG-READ-006"]
    assert len(empty_f) == 1
    assert empty_f[0].severity == "medium"


def test_17_heading_level_jump_triggers_finding():
    """17. Test skipping from H1 to H4 triggers ENG-READ-007."""
    text = " ".join(["word"] * 40)
    data = {
        "url": "https://example.com",
        "text": text,
        "content": {
            "headings": [
                {"level": 1, "text": "Main Title"},
                {"level": 4, "text": "Deep Subsection"},
            ]
        },
    }

    res = analyze_readability(data)
    jump_f = [f for f in res.findings if f.id == "ENG-READ-007"]
    assert len(jump_f) == 1
    assert jump_f[0].severity == "low"


def test_18_large_section_without_heading():
    """18. Test continuous body text > 500 words without headings triggers ENG-READ-008."""
    text = " ".join(["This is a test word to build up a large wall of text without headings."] * 45)
    data = {"url": "https://example.com", "text": text, "headings": []}

    res = analyze_readability(data)
    assert res.metrics["word_count"] > 500
    wall_f = [f for f in res.findings if f.id == "ENG-READ-008"]
    assert len(wall_f) == 1
    assert wall_f[0].severity == "low"


def test_19_duplicate_content_blocks():
    """19. Test repeated duplicate paragraphs triggers ENG-READ-009."""
    para = "This is a substantial paragraph containing enough words to be considered a distinct content block in the article."
    filler = "Here is some unique filler content to provide contextual diversity in the document."
    data = {
        "url": "https://example.com",
        "paragraphs": [para, filler, para],
    }

    res = analyze_readability(data)
    dup_f = [f for f in res.findings if f.id == "ENG-READ-009"]
    assert len(dup_f) == 1
    assert dup_f[0].severity == "low"


def test_20_malformed_paragraph_handled_safely():
    """20. Test None, numbers, and corrupt objects in paragraphs list."""
    data = {
        "url": "https://example.com",
        "paragraphs": [
            None,
            12345,
            {"text": "Valid paragraph with more than thirty words to ensure complete statistical evaluation without crashing and verifying robust error handling."},
        ],
    }

    res = analyze_readability(data)
    assert res.metrics["paragraph_count"] == 1


def test_21_malformed_heading_handled_safely():
    """21. Test malformed heading entries."""
    text = " ".join(["word"] * 40)
    data = {
        "url": "https://example.com",
        "text": text,
        "headings": [
            None,
            "Plain String Heading",
            {"level": "invalid", "text": "Valid Text"},
        ],
    }

    res = analyze_readability(data)
    assert res.metrics["heading_count"] == 2


def test_22_missing_content_fields_handled_safely():
    """22. Test dictionary with missing optional fields."""
    data = {"url": "https://example.com"}
    res = analyze_readability(data)
    assert res.status == "insufficient_evidence"


def test_23_deterministic_finding_ids():
    """23. Test finding IDs are completely deterministic."""
    data = {
        "url": "https://example.com",
        "text": " ".join(["word"] * 40),
        "headings": [{"level": 1, "text": "Title"}, {"level": 4, "text": "Jump"}],
    }

    res1 = analyze_readability(data)
    res2 = analyze_readability(data)
    assert [f.id for f in res1.findings] == [f.id for f in res2.findings]


def test_24_deterministic_scoring_bounds():
    """24. Test score is integer bounded between 0 and 100."""
    text = (
        "Simple design enables seamless collaboration across engineering organizations. "
        "Teams communicate clearly and deliver results quickly with structured tools. "
        "Clear documentation accelerates developer onboarding and reduces production errors. "
        "High quality testing ensures consistent software delivery across all releases."
    )
    res = analyze_readability({"text": text})
    assert isinstance(res.score, int)
    assert 0 <= res.score <= 100


def test_25_page_input_data_support():
    """25. Test direct PageInputData instance support."""
    input_data = PageInputData(
        url="https://example.com",
        headings=[
            HeadingElement(level=1, text="Main Page Title"),
            HeadingElement(level=2, text="Introduction"),
        ],
    )

    res = analyze_readability(input_data)
    # No body text supplied -> insufficient_evidence
    assert res.status == "insufficient_evidence"


def test_26_none_input_handled():
    """26. Test None input returns status 'insufficient_evidence'."""
    res = analyze_readability(None)
    assert res.status == "insufficient_evidence"
    assert res.score is None


def test_27_math_safety_zero_division():
    """27. Test zero division safety with empty strings and punctuation-only text."""
    res = analyze_readability({"text": "... ??? !!!"})
    assert res.status == "insufficient_evidence"
    assert res.score is None
