"""Slice registry — the server owns the vocabulary, callers only suggest.

Mitigates agent-invented slice names ("Support/EN" vs "support/en" splitting
calibration in two): unknown or malformed names collapse to "default" instead
of creating a new calibration group.
"""
from __future__ import annotations

from typing import Literal

# Closed vocabulary. Add a name here only when it has ~20+ labeled rows
# waiting (otherwise it will never calibrate — see README §2).
KNOWN_SLICES = frozenset({
    "default",
    "billing",
    "support/en",
    "support/pt",
    "dev",
})

# Machine-enforced enum for tool/API schemas (MCP + HTTP). Agents see only
# these values in the parameter description — no AGENTS.md discipline needed.
Slice = Literal["default", "billing", "support/en", "support/pt", "dev"]


def normalize_slice(raw: str | None) -> tuple[str, bool]:
    """Returns (canonical_slice, was_mapped).

    was_mapped=True means the input wasn't in the vocabulary and fell back
    to \"default\" — the caller should log it, not create a group for it.
    """
    if not raw:
        return "default", False
    canon = raw.strip().lower().replace("_", "/").replace(" ", "")
    if canon in KNOWN_SLICES:
        return canon, False
    return "default", True
