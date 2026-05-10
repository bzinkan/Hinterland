#!/usr/bin/env python
"""Author-time tool for drafting new expeditions with LLM assistance.

Phase 10 ships this as a stub so the file exists at the path
docs/expedition-authoring.md references and AGENTS.md Phase 10
deliverables list. The full implementation is intentionally deferred
-- it requires an Anthropic / OpenAI API key (Brian's expense, not the
project's), and ADR 0007 ("multi-agent AI is internal-only / never on
a kid-facing path") needs the per-author setup documented before this
hits production hands.

Spec for the eventual implementation:
- Reads a one-line prompt from argv ("a city-park expedition focused
  on insects you can find without flipping rocks")
- Calls Claude (or OpenAI) with the docs/expedition-authoring.md
  voice/tone rules in the system prompt
- Returns a Pydantic-validated Expedition JSON blob to stdout
- The author reviews + edits + drops the file under
  content/expeditions/ + commits

This is author-time only -- there is NO runtime path for the kid-
facing API to call an LLM. ADRs 0002 and 0007 are explicit about this.

Run:
    python scripts/draft_expedition.py "your prompt here"
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "draft_expedition.py: stub. Full LLM-assisted authoring tool is a "
        "post-Phase-10 follow-up; see docs/risks/0004-expedition-authoring-tooling.md."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
