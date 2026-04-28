"""Tests for KnowledgeBase (US-008)."""
from __future__ import annotations

from pathlib import Path

import pytest

from knowledge.base import KnowledgeBase


_SAMPLE_MD = """\
# EMA Crossover Strategy

Uses 9/21 EMA crossover for entry signals.
Trend following approach.

## Risk Management

- Max 1% risk per trade
- Stop loss mandatory
* Position sizing via ATR

## Mean Reversion

Bollinger Band + RSI signals.
Enter on oversold/overbought extremes.

### Entry Rules

Price must touch the lower band.
RSI below 30 for confirmation.
"""


@pytest.fixture
def kb(tmp_path) -> KnowledgeBase:
    md = tmp_path / "kb.md"
    md.write_text(_SAMPLE_MD)
    return KnowledgeBase(kb_path=md)


# ------------------------------------------------------------------ #
# Parsing                                                            #
# ------------------------------------------------------------------ #

def test_sections_parsed(kb):
    assert len(kb.sections) >= 4


def test_section_titles_returns_all(kb):
    titles = kb.section_titles()
    assert "EMA Crossover Strategy" in titles
    assert "Risk Management" in titles
    assert "Mean Reversion" in titles
    assert "Entry Rules" in titles


def test_section_levels_correct(kb):
    levels = {s["title"]: s["level"] for s in kb.sections}
    assert levels["EMA Crossover Strategy"] == 1
    assert levels["Risk Management"] == 2
    assert levels["Entry Rules"] == 3


def test_section_body_text_populated(kb):
    for s in kb.sections:
        assert "text" in s


# ------------------------------------------------------------------ #
# get_strategy                                                        #
# ------------------------------------------------------------------ #

def test_get_strategy_finds_by_partial_name(kb):
    result = kb.get_strategy("ema")
    assert result is not None
    assert "EMA" in result["title"]


def test_get_strategy_case_insensitive(kb):
    assert kb.get_strategy("MEAN REVERSION") is not None
    assert kb.get_strategy("mean reversion") is not None


def test_get_strategy_returns_none_when_missing(kb):
    assert kb.get_strategy("nonexistent_strategy_xyz") is None


# ------------------------------------------------------------------ #
# get_risk_rules                                                      #
# ------------------------------------------------------------------ #

def test_get_risk_rules_returns_bullets(kb):
    rules = kb.get_risk_rules()
    assert len(rules) >= 2
    assert any("1%" in r for r in rules)
    assert any("Stop loss" in r or "ATR" in r for r in rules)


# ------------------------------------------------------------------ #
# query                                                               #
# ------------------------------------------------------------------ #

def test_query_finds_lines_with_topic(kb):
    hits = kb.query("RSI")
    assert len(hits) >= 1
    assert all("rsi" in h.lower() for h in hits)


def test_query_returns_empty_when_no_match(kb):
    assert kb.query("xyzzy_no_match_term") == []


def test_query_case_insensitive(kb):
    assert kb.query("atr") == kb.query("ATR")


# ------------------------------------------------------------------ #
# Edge cases                                                         #
# ------------------------------------------------------------------ #

def test_missing_file_gives_empty_sections(tmp_path):
    kb = KnowledgeBase(kb_path=tmp_path / "nonexistent.md")
    assert kb.sections == []
    assert kb.section_titles() == []
    assert kb.get_strategy("ema") is None
    assert kb.get_risk_rules() == []
    assert kb.query("anything") == []


def test_empty_file_gives_empty_sections(tmp_path):
    md = tmp_path / "empty.md"
    md.write_text("")
    kb = KnowledgeBase(kb_path=md)
    assert kb.sections == []


# ------------------------------------------------------------------ #
# Default path — verifies the real research/knowledge-base.md loads  #
# ------------------------------------------------------------------ #

def test_default_path_resolves_and_loads():
    """DEFAULT_PATH must point to the real knowledge-base.md file."""
    assert KnowledgeBase.DEFAULT_PATH.exists(), (
        f"knowledge-base.md not found at {KnowledgeBase.DEFAULT_PATH}"
    )
    kb = KnowledgeBase()
    assert len(kb.sections) >= 5, "Expected ≥5 sections in the real knowledge base"


def test_default_kb_has_expected_content():
    """Spot-check known sections in the real knowledge base."""
    kb = KnowledgeBase()
    assert kb.get_strategy("EMA") is not None
    assert kb.get_strategy("Mean Reversion") is not None
    rules = kb.get_risk_rules()
    assert len(rules) >= 1
    hits = kb.query("ATR")
    assert len(hits) >= 1
