"""Starter dashboard — text/markdown summary over the log. No dependencies."""
from __future__ import annotations

from collections import Counter


def summarize(decisions: list[dict]) -> str:
    if not decisions:
        return "No decisions in the log yet."
    by_dec = Counter((d.get("policy") or {}).get("decision", "?") for d in decisions)
    by_slice = Counter(d.get("slice", "default") for d in decisions)
    labeled = [d for d in decisions if (d.get("outcome") or {}).get("correct") is not None]
    total = len(decisions)
    auto = by_dec.get("auto", 0)
    lines = [
        f"## Summary ({total} decisions)",
        f"- automation: {auto}/{total} = {auto / total:.1%}",
        f"- by decision: {dict(by_dec)}",
        f"- by slice: {dict(by_slice)}",
    ]
    if labeled:
        err = sum(1 for d in labeled if not d["outcome"]["correct"]) / len(labeled)
        lines.append(f"- labeled error: {err:.1%} over {len(labeled)} cases with feedback")
        # error restricted to what was automated
        auto_lab = [d for d in labeled if (d.get("policy") or {}).get("decision") == "auto"]
        if auto_lab:
            e2 = sum(1 for d in auto_lab if not d["outcome"]["correct"]) / len(auto_lab)
            lines.append(f"- error on auto: {e2:.1%} over {len(auto_lab)} labeled automated")
    else:
        lines.append("- no human feedback yet: set outcome.correct to calibrate")
    lines.append("\nNext: run `python -m eval.report` for the coverage x error curve per slice/action.")
    return "\n".join(lines)


def print_summary(decisions: list[dict]):
    print(summarize(decisions))
