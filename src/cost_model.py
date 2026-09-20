"""Expected-cost model — the heart of idea 1.

Automate when: (1 - p_correct) * C_error < C_review
"""
from __future__ import annotations


def should_automate(p_correct: float, c_error: float, c_review: float) -> bool:
    """Returns True when the expected cost of automating < cost of review."""
    return (1.0 - p_correct) * c_error < c_review


def breakeven_p(c_error: float, c_review: float) -> float:
    """Minimum accuracy that justifies automation. E.g. C_error=5000, C_rev=5 -> 0.999."""
    if c_error <= 0:
        return 0.0
    return max(0.0, 1.0 - c_review / c_error)


def total_cost(n_auto: int, n_errors: int, n_review: int, c_error: float, c_review: float) -> float:
    return n_errors * c_error + n_review * c_review


# Per-(slice, action) cost overrides. Dev attempts are cheap because a wrong
# suggestion is reversible (the user discards the diff); billing errors are not.
# Lookup: costs.get((slice, action)) -> (c_error, c_review), else action default.
SLICE_COSTS: dict[tuple[str, str], tuple[float, float]] = {
    ("dev", "code"): (0.2, 1.0),
    ("dev", "llm"): (0.2, 1.0),
    ("dev", "human"): (0.0, 1.0),
}


def costs_for(slice_name: str, action: str, default: tuple[float, float]) -> tuple[float, float]:
    """Slice override if present, else the action default from ACTION_POLICY."""
    return SLICE_COSTS.get((slice_name, action), default)


def suggest_threshold(
    rows: list[dict],
    c_error: float,
    c_review: float,
    max_error_rate: float | None = None,
) -> dict:
    """Scans thresholds 0.50..0.97 over rows=[{confidence, correct:bool}].

    Picks the one minimizing total cost with an optional error cap.
    Used by the nightly calibrator (plan step 3).
    """
    best = None
    candidates = [round(0.50 + i * 0.01, 2) for i in range(48)]
    fallback = None  # best without cap, in case the cap is unreachable
    for t in candidates:
        auto = [r for r in rows if r["confidence"] >= t]
        rev = len(rows) - len(auto)
        err = sum(1 for r in auto if not r["correct"])
        err_rate = (err / len(auto)) if auto else 0.0
        cost = err * c_error + rev * c_review
        cov = len(auto) / len(rows) if rows else 0.0
        entry = {"threshold": t, "cost": cost, "coverage": round(cov, 3),
                 "error_rate": round(err_rate, 4), "n_auto": len(auto)}
        if fallback is None or cost < fallback["cost"]:
            fallback = entry
        if max_error_rate is not None and auto and err_rate > max_error_rate:
            continue
        if best is None or cost < best["cost"]:
            best = entry
    if best is not None:
        return best
    # Unreachable cap: return the lowest-error option with a flag so the
    # operator raises the cap or collects more data
    if fallback is not None:
        return {**fallback, "cap_unmet": True}
    return {"threshold": 0.90, "cost": float("inf"), "coverage": 0.0, "error_rate": 0.0, "n_auto": 0}
