"""Heading-parser harness for docs/trading/*.md (T09–T15)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_BOT_ROOT = Path(__file__).resolve().parents[2]
_DOCS = _BOT_ROOT / "docs" / "trading"


# ------------------------------------------------------------------ #
# All Wave-2 doc files must exist                                    #
# ------------------------------------------------------------------ #

_REQUIRED_DOCS = [
    "daily-routine.md",
    "weekly-routine.md",
    "daily-prep-checklist.md",
    "journal-template.md",
    "trade-review-process.md",
    "getting-started.md",
    "growth-roadmap.md",
    "success-timeline.md",
    "scaling-strategies.md",
    "weekly-reflection.md",
    "volatility-playbook.md",
    "simulated-trade-walkthrough.md",
    "drawdown-protocol.md",
    "risk-rules.md",
    "README.md",
]


@pytest.mark.parametrize("filename", _REQUIRED_DOCS)
def test_doc_exists(filename: str):
    assert (_DOCS / filename).exists(), f"docs/trading/{filename} missing"


# ------------------------------------------------------------------ #
# Frontmatter check                                                  #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("filename", _REQUIRED_DOCS)
def test_doc_has_frontmatter(filename: str):
    text = (_DOCS / filename).read_text()
    assert text.startswith("---\n"), f"{filename} missing YAML frontmatter"
    fm = text.split("---", 2)[1]
    assert "title:" in fm, f"{filename} frontmatter missing title"
    assert "last_updated:" in fm, f"{filename} frontmatter missing last_updated"
    assert "source: FX GOAT Mastery Compendium" in fm, (
        f"{filename} frontmatter must cite the source"
    )


# ------------------------------------------------------------------ #
# Compendium gap-fills (G1–G7) — required content                    #
# ------------------------------------------------------------------ #

_GAP_FILLS = [
    # G1: getting-started.md must list the three actionable steps from §1
    ("getting-started.md", "Construct a Market Structure Map"),
    ("getting-started.md", "Standardise Technical Confluences"),
    ("getting-started.md", "Baseline Psychological Audit"),
    # G2: drawdown-protocol.md must reference the 24-hour cooling-off rule
    ("drawdown-protocol.md", "24-hour cooling-off"),
    # G3: weekly-reflection.md must lead with "Did I follow my rules?"
    ("weekly-reflection.md", "Did I follow my rules?"),
    # G4: risk-rules.md must contain the Three Vital Rules
    ("risk-rules.md", "Structure is Absolute"),
    ("risk-rules.md", "Capital is Life"),
    ("risk-rules.md", "Discipline is the Edge"),
    # G5: success-timeline.md must state the 1:2 R:R × 20-consecutive milestone
    ("success-timeline.md", "20 consecutive"),
    # G6: journal-template.md must require all three fields
    ("journal-template.md", "technical reason"),
    ("journal-template.md", "emotional state"),
    ("journal-template.md", "Lesson Learned"),
    # G7: daily-routine.md must adopt the "Kill Zones" terminology
    ("daily-routine.md", "Kill Zones"),
]


@pytest.mark.parametrize("filename, required_phrase", _GAP_FILLS)
def test_doc_contains_required_gap_fill(filename: str, required_phrase: str):
    text = (_DOCS / filename).read_text()
    assert required_phrase.lower() in text.lower(), (
        f"docs/trading/{filename} missing required compendium phrase {required_phrase!r}"
    )


# ------------------------------------------------------------------ #
# Code-vs-Compendium delta callouts                                  #
# ------------------------------------------------------------------ #

_REQUIRED_CALLOUTS = [
    # The literal "Current bot behaviour" callout for the simulated walkthrough
    (
        "simulated-trade-walkthrough.md",
        "v1 closes the full position at the 1:2 marker; partial-fill, BE-trail, and HTF-target are roadmap items",
    ),
    # risk-rules.md must call out the liquidity-sweep / Fib-proxy delta
    ("risk-rules.md", "liquidity sweep"),
    ("risk-rules.md", "Fibonacci"),
]


@pytest.mark.parametrize("filename, required_phrase", _REQUIRED_CALLOUTS)
def test_doc_contains_code_delta_callout(filename: str, required_phrase: str):
    text = (_DOCS / filename).read_text()
    assert required_phrase.lower() in text.lower(), (
        f"docs/trading/{filename} missing code-vs-compendium delta callout: {required_phrase!r}"
    )


# ------------------------------------------------------------------ #
# Body-paragraph check — heading must not be a stub                  #
# ------------------------------------------------------------------ #

_H2_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


@pytest.mark.parametrize("filename", _REQUIRED_DOCS)
def test_doc_h2_sections_have_body(filename: str):
    text = (_DOCS / filename).read_text()
    # Strip frontmatter
    if text.startswith("---\n"):
        text = text.split("---", 2)[2]
    headings = list(_H2_RE.finditer(text))
    if not headings:
        pytest.skip(f"{filename} has no H2 sections (acceptable for top-level index docs)")
    for i, m in enumerate(headings):
        section_start = m.end()
        section_end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        body = text[section_start:section_end].strip()
        assert len(body) >= 60, (
            f"{filename}: section {m.group(1)!r} has fewer than 60 chars of body — "
            "stub headings are not acceptable; full prose required."
        )
