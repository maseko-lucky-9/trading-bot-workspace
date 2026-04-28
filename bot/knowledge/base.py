"""
KnowledgeBase (US-008).

Lightweight markdown KB indexer over research/knowledge-base.md.
Sections are parsed by heading level (# / ## / ###).
"""
from __future__ import annotations

import re
from pathlib import Path


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


class KnowledgeBase:
    DEFAULT_PATH = (
        Path(__file__).resolve().parents[1] / "research" / "knowledge-base.md"
    )

    def __init__(self, kb_path: Path | None = None) -> None:
        self.kb_path = Path(kb_path) if kb_path else self.DEFAULT_PATH
        self.sections: list[dict] = []  # {level, title, body, lines}
        self._load()

    # ------------------------------------------------------------------ #
    # Parsing                                                            #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        self.sections = []
        if not self.kb_path.exists():
            return
        text = self.kb_path.read_text(encoding="utf-8")
        cur: dict | None = None
        for line in text.splitlines():
            m = _HEADING_RE.match(line)
            if m:
                if cur is not None:
                    self.sections.append(cur)
                cur = {
                    "level": len(m.group(1)),
                    "title": m.group(2).strip(),
                    "body": [],
                }
                continue
            if cur is not None:
                cur["body"].append(line)
        if cur is not None:
            self.sections.append(cur)
        # Materialise body text
        for s in self.sections:
            s["text"] = "\n".join(s["body"]).strip()

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def get_strategy(self, name: str) -> dict | None:
        """Return the first section whose title contains ``name`` (CI)."""
        n = name.lower()
        for s in self.sections:
            if n in s["title"].lower():
                return {"title": s["title"], "level": s["level"], "text": s["text"]}
        return None

    def get_risk_rules(self) -> list[str]:
        """Return bullet lines from any section whose title mentions risk."""
        rules: list[str] = []
        for s in self.sections:
            if "risk" in s["title"].lower() or "position siz" in s["title"].lower():
                for ln in s["body"]:
                    stripped = ln.strip()
                    if stripped.startswith(("-", "*", "1.", "2.", "3.")):
                        rules.append(stripped)
        return rules

    def query(self, topic: str) -> list[str]:
        """Return all lines (across all sections) that mention ``topic``."""
        n = topic.lower()
        hits: list[str] = []
        for s in self.sections:
            for ln in s["body"]:
                if n in ln.lower():
                    hits.append(ln.strip())
        return hits

    def section_titles(self) -> list[str]:
        return [s["title"] for s in self.sections]
