"""Coverage x error calibration per action/slice.

Computes over labeled log rows (outcome.correct set via eval/label.py).
Pure computation over the log.
"""
from __future__ import annotations

from collections import defaultdict
from src import cost_model

BIN_EDGES = [0.0, 0.6, 0.7, 0.8, 0.9, 1.01]


def _rows_from_decisions(decisions: list[dict]) -> list[dict]:
    """Extracts [{action, slice, confidence, correct}] from labeled rows only."""
    rows = []
    for d in decisions:
        out = d.get("outcome") or {}
        if out.get("correct") is None:
            continue
        pol = d.get("policy") or {}
        ans = (d.get("answers") or {}).get("route") or {}
        try:
            conf = float(ans.get("confidence") or 0.0)
        except (TypeError, ValueError):
            continue
        rows.append({
            "action": pol.get("action") or ans.get("choice") or "unknown",
            "slice": d.get("slice", "default"),
            "confidence": max(0.0, min(1.0, conf)),
            "correct": bool(out["correct"]),
        })
    return rows


def bin_stats(rows: list[dict], edges: list[float] = BIN_EDGES) -> list[dict]:
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        b = [r for r in rows if lo <= r["confidence"] < hi]
        if not b:
            continue
        acc = sum(1 for r in b if r["correct"]) / len(b)
        out.append({"bin": f"{lo:.1f}-{min(hi, 1.0):.1f}", "n": len(b),
                    "accuracy": round(acc, 3)})
    return out


def coverage_curve(rows: list[dict], thresholds: list[float] | None = None) -> list[dict]:
    ths = thresholds or [0.60, 0.70, 0.80, 0.85, 0.90, 0.95]
    out = []
    for t in ths:
        auto = [r for r in rows if r["confidence"] >= t]
        cov = len(auto) / len(rows) if rows else 0.0
        err = (sum(1 for r in auto if not r["correct"]) / len(auto)) if auto else 0.0
        out.append({"threshold": t, "coverage": round(cov, 3),
                    "error_rate": round(err, 4), "n_auto": len(auto)})
    return out


def calibrate_all(decisions: list[dict], costs: dict, max_error: dict | None = None) -> dict:
    """Groups by (slice, action) and suggests the cost-minimizing threshold.

    costs: {action: (c_error, c_review)} — fallback (5, 1).
    max_error: {action: error_cap} — e.g. approve_transfer: 0.02.
    """
    max_error = max_error or {}
    rows = _rows_from_decisions(decisions)
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        groups[(r["slice"], r["action"])].append(r)

    report = {}
    for (sl, act), g in sorted(groups.items()):
        c_err, c_rev = costs.get(act, (5.0, 1.0))
        cap = max_error.get(act)
        best = cost_model.suggest_threshold(g, c_err, c_rev, cap)
        report[f"{sl}/{act}"] = {
            "n": len(g),
            "bins": bin_stats(g),
            "curve": coverage_curve(g),
            "costs": {"c_error": c_err, "c_review": c_rev, "max_error": cap},
            "suggested": best,
            "breakeven_p": round(cost_model.breakeven_p(c_err, c_rev), 4),
        }
    return report
