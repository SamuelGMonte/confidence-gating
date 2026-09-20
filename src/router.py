"""Router: Jev -> Policy -> Log. LLM only on fallback/generation (injectable)."""
from __future__ import annotations

import os
from src import jev_client, log, policy
from src.controller import ThresholdController
from src.slices import normalize_slice

QUESTION_VERSION = "v6"

QUESTIONS = {
    "route": {
        "type": "choice",
        "instructions": "Which handler fits this request? Choose by capability, not vendor.",
        "criteria": {
            "code": "Lookup, local config, or deterministic procedure with no text generation",
            "llm": "Open question, troubleshooting, or text that must be written",
            "human": "Ambiguous, irreversible, or out of scope",
        },
    },
    "irreversible": {
        "type": "noul",
        "instructions": "Does this request cause an irreversible or sensitive effect?",
    },
    "dev_task": {
        "type": "noul",
        "instructions": "Is this a coding/build/fix task an AI coding agent can attempt (write or edit code, fix UI, refactor, debug)?",
    },
    "scope": {
        "type": "score",
        "instructions": "How large is the blast radius of attempting this task?",
        "criteria": [
            "Single file or snippet",
            "Multiple files in a bounded area",
            "Whole codebase, migration, or production-wide change",
        ],
    },
}

_controller: ThresholdController | None = None


def _thresholds() -> dict:
    global _controller
    if os.getenv("ADAPTIVE_THRESHOLDS", "0") == "1":
        if _controller is None:
            _controller = ThresholdController()
        return _controller.thresholds
    return {}


def route(prompt: str, llm_fn=None, slice_name: str = "default") -> dict:
    slice_name, slice_mapped = normalize_slice(slice_name)
    state = {"request": prompt}
    try:
        res = jev_client.evaluate(state, QUESTIONS)
    except jev_client.JevUnavailable as e:
        d = policy.decide_with_fallback(None, jev_error=e.reason)
        data = llm_fn(prompt) if llm_fn else {"action": "human_queue", "request": prompt}
        rec = log.append({"state_snapshot": state, "question_version": QUESTION_VERSION,
                          "model": os.getenv("TYPESAFE_MODEL", "jev-1.13.0"),
                          "answers": {}, "slice": slice_name,
                          "policy": {"decision": d.decision, "reason": d.reason},
                          "outcome": {}})
        return {"source": d.decision, "reason": d.reason, "data": data,
                "slice": slice_name, "slice_mapped": slice_mapped, "log": rec["ts"]}

    d = policy.decide(res["answers"], slice_name=slice_name, thresholds=_thresholds())
    if d.decision in ("llm", "llm_fallback"):
        data = llm_fn(prompt) if llm_fn else {"action": "call_llm", "request": prompt}
        source, saved = d.decision, False
    elif d.decision in ("human", "confirm"):
        data = {"action": "confirm_or_human_queue", "request": prompt, "reason": d.reason}
        source, saved = d.decision, False
    else:
        data = {"action": "run_deterministic_handler", "request": prompt, "route": d.action}
        source, saved = "jev_router", True

    rec = log.append({"state_snapshot": state, "question_version": QUESTION_VERSION,
                      "model": res.get("model"), "answers": res["answers"], "slice": slice_name,
                      "policy": {"action": d.action, "decision": d.decision,
                                 "threshold": d.threshold, "reason": d.reason},
                      "outcome": {}})
    return {"source": source, "reason": d.reason, "route": d.action,
            "confidence": d.confidence, "data": data,
            "slice": slice_name, "slice_mapped": slice_mapped,
            "output_tokens_saved": saved, "log": rec["ts"]}
