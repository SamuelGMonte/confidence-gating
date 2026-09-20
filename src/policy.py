"""Policy engine — code decides, the model only advises.

Decisions: auto | confirm | llm | human
Never delegates authorization to the model. Threshold per action x slice.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Initial thresholds (starting point — the calibrator adjusts them later).
# Read-only tolerates errors; irreversible always requires confirmation.
ACTION_POLICY = {
    "code":        {"threshold": 0.70, "c_error": 5,    "c_review": 1.0, "confirm_always": False},
    "llm":           {"threshold": 0.75, "c_error": 2,    "c_review": 2.0, "confirm_always": False},
    "human":        {"threshold": 1.01, "c_error": 0,    "c_review": 5.0, "confirm_always": True},
    "billing_lookup":{"threshold": 0.71, "c_error": 5,    "c_review": 1.0, "confirm_always": False},
    "approve_transfer": {"threshold": 0.90, "c_error": 5000, "c_review": 5.0, "confirm_always": True},
}

FLOOR = 0.60  # below this: nobody automates, goes to human
IRREVERSIBLE_NOUL_CUT = 0.70  # P(irreversible=yes) above this -> confirm/human


@dataclass
class Decision:
    decision: str  # auto | confirm | llm | human | llm_fallback
    action: str | None
    confidence: float
    threshold: float
    reason: str
    extra: dict = field(default_factory=dict)


def _choice(answers: dict, key: str) -> tuple[str | None, float, dict]:
    item = answers.get(key) or {}
    try:
        conf = float(item.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    ch = item.get("choice")
    return (str(ch) if ch is not None else None), max(0.0, min(1.0, conf)), item


def _noul_prob(answers: dict, key: str) -> float | None:
    item = answers.get(key) or {}
    # Jev noul returns the yes-probability under varying fields; cover the common ones
    for k in ("prob_yes", "probability", "prob", "p_yes", "score"):
        if item.get(k) is not None:
            try:
                return max(0.0, min(1.0, float(item[k])))
            except (TypeError, ValueError):
                pass
    # fallback: direct boolean
    if isinstance(item.get("value"), bool):
        return 1.0 if item["value"] else 0.0
    return None


def decide(answers: dict, slice_name: str = "default", action_override: str | None = None,
           thresholds: dict | None = None) -> Decision:
    route, conf, raw = _choice(answers, "route")
    if action_override:
        route = action_override

    if route is None or not (0.0 <= conf <= 1.0):
        return Decision("human", route, conf, 1.0, "invalid_response")

    if conf < FLOOR:
        return Decision("human", route, conf, FLOOR, "below_floor")

    if route in ("human", "other"):
        return Decision("human", route, conf, 1.0, "human_or_other_route")

    cfg = ACTION_POLICY.get(route, ACTION_POLICY["code"])
    thr = cfg["threshold"]
    if thresholds and f"{slice_name}/{route}" in thresholds:
        thr = float(thresholds[f"{slice_name}/{route}"])

    # Irreversibility lock: noul takes precedence over a confident choice
    p_irr = _noul_prob(answers, "irreversible")
    if p_irr is not None and p_irr >= IRREVERSIBLE_NOUL_CUT:
        return Decision("confirm" if conf >= thr else "human", route, conf, thr,
                        f"irreversible_p={p_irr:.2f}", {"p_irreversible": p_irr})

    if route == "llm":
        return Decision("llm", route, conf, thr, "jev_requested_generation")

    if cfg.get("confirm_always"):
        # Sensitive action: even at high confidence, at most confirm — never pure auto
        if conf >= thr:
            return Decision("confirm", route, conf, thr, "sensitive_requires_confirmation")
        return Decision("human", route, conf, thr, "sensitive_low_confidence")

    if conf >= thr:
        return Decision("auto", route, conf, thr, "high_confidence_code")
    return Decision("llm", route, conf, thr, "low_confidence_for_action")


def decide_with_fallback(answers: dict | None, jev_error: str | None = None, **kw) -> Decision:
    """Wrapper handling Jev outages: never becomes auto."""
    if jev_error or answers is None:
        return Decision("llm_fallback", None, 0.0, 1.0, f"jev_unavailable:{jev_error or 'unknown'}")
    return decide(answers, **kw)
