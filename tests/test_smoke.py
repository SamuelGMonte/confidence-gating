"""Unit tests — run with `make test` (or python3 -m pytest tests/ -q)."""
from src import policy, cost_model, calibrator, controller, verifier
from src.slices import normalize_slice


def test_slice_allowlist():
    assert normalize_slice("billing") == ("billing", False)
    assert normalize_slice("Support/EN") == ("support/en", False)
    assert normalize_slice("support_en") == ("support/en", False)
    assert normalize_slice("Support/DE") == ("default", True)
    assert normalize_slice(None) == ("default", False)


def test_policy_basics():
    assert policy.decide({"route": {"choice": "code", "confidence": 0.85}}).decision == "auto"
    assert policy.decide({"route": {"choice": "code", "confidence": 0.40}}).decision == "human"
    assert policy.decide({"route": {"choice": "approve_transfer", "confidence": 0.97}}).decision == "confirm"
    # dynamic override: high threshold blocks auto
    d = policy.decide({"route": {"choice": "code", "confidence": 0.85}},
                      slice_name="billing", thresholds={"billing/code": 0.95})
    assert d.decision == "llm" and d.threshold == 0.95


def test_dev_task_override():
    # coding task with wrong slice + low route confidence -> llm anyway
    d = policy.decide({"route": {"choice": "code", "confidence": 0.26},
                       "dev_task": {"noul": 0.9}},
                      slice_name="support/pt")
    assert d.decision == "llm" and d.reason.startswith("dev_task_p="), d
    # irreversible still wins over dev_task
    d = policy.decide({"route": {"choice": "code", "confidence": 0.26},
                       "dev_task": {"noul": 0.9},
                       "irreversible": {"noul": 0.85}},
                      slice_name="support/pt")
    assert d.decision in ("confirm", "human"), d
    # non-dev low-conf on expensive slice stays human
    d = policy.decide({"route": {"choice": "code", "confidence": 0.26},
                       "dev_task": {"noul": 0.1}},
                      slice_name="support/pt")
    assert d.decision == "human", d
    # dev task with whole-codebase scope (0-indexed: 2.0 = top) -> confirm
    d = policy.decide({"route": {"choice": "code", "confidence": 0.26},
                       "dev_task": {"noul": 0.9},
                       "scope": {"score": 2.0, "confidence": 0.85}},
                      slice_name="dev")
    assert d.decision == "confirm" and d.reason.startswith("dev_big_scope"), d
    # dev task, small scope, low confidence -> llm attempt
    d = policy.decide({"route": {"choice": "code", "confidence": 0.26},
                       "dev_task": {"noul": 0.9},
                       "scope": {"score": 1.0, "confidence": 0.85}},
                      slice_name="dev")
    assert d.decision == "llm", d


def test_noul_live_shape():
    # live shape (jev-1.13.0): {"type": "noul", "noul": 0.46}
    assert policy._noul_prob({"irreversible": {"type": "noul", "noul": 0.46}},
                             "irreversible") == 0.46


def test_verifier_mapping():
    # inject answers directly by monkeypatching evaluate
    import src.verifier as v
    from src import jev_client
    orig = jev_client.evaluate
    jev_client.evaluate = lambda state, q, model=None: {
        "answers": {"injection": {"prob_yes": 0.9}, "tool_match": {"prob_yes": 0.1},
                    "sensitive": {"prob_yes": 0.1}, "irreversible": {"prob_yes": 0.1}}}
    try:
        assert v.verify({"request": "x"})["verdict"] == "block"
    finally:
        jev_client.evaluate = orig
    jev_client.evaluate = lambda state, q, model=None: (_ for _ in ()).throw(
        jev_client.JevUnavailable("http_529"))
    try:
        assert v.verify({"request": "x"})["verdict"] == "human"
    finally:
        jev_client.evaluate = orig
    # faithful tool call (tool_match yes = good news) must NOT block
    jev_client.evaluate = lambda state, q, model=None: {
        "answers": {"injection": {"prob_yes": 0.05}, "tool_match": {"noul": 0.95},
                    "sensitive": {"prob_yes": 0.05}, "irreversible": {"noul": 0.05}}}
    try:
        assert v.verify({"request": "x"})["verdict"] == "pass"
    finally:
        jev_client.evaluate = orig


def test_below_floor_cost_aware():
    # default slice: error expensive -> human
    d = policy.decide({"route": {"choice": "code", "confidence": 0.40}})
    assert d.decision == "human" and d.reason == "below_floor", d
    # dev slice: attempt cheap -> llm even at 0.29
    d = policy.decide({"route": {"choice": "code", "confidence": 0.29}},
                      slice_name="dev")
    assert d.decision == "llm" and d.reason == "below_floor_cheap_attempt", d
    # dev slice, sensitive action: still human below floor
    d = policy.decide({"route": {"choice": "approve_transfer", "confidence": 0.29}},
                      slice_name="dev")
    assert d.decision == "human", d


def test_calibrator_suggest():
    rows = [{"confidence": c, "correct": ok}
            for c, ok in [(0.9, True), (0.85, True), (0.6, False), (0.55, False)]]
    best = cost_model.suggest_threshold(rows, 10, 1)
    assert best["threshold"] >= 0.5 and best["n_auto"] >= 1


if __name__ == "__main__":
    test_slice_allowlist()
    test_policy_basics()
    test_below_floor_cost_aware()
    test_dev_task_override()
    test_noul_live_shape()
    test_verifier_mapping()
    test_calibrator_suggest()
    print("SMOKE PASS")
