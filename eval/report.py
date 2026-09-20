"""CLI: python -m eval.report [--log logs/decisions.jsonl]

Reads the log, filters rows with outcome.correct and prints
bins + coverage x error curve + suggested threshold per slice/action.
Only labeled rows (see eval/label.py) are used.
"""
from __future__ import annotations

import argparse
from src import log as decision_log
from src import calibrator
from src import dashboard

COSTS = {
    "code": (5.0, 1.0),
    "billing_lookup": (5.0, 1.0),
    "llm": (2.0, 2.0),
    "approve_transfer": (5000.0, 5.0),
    "human": (0.0, 5.0),
}
MAX_ERROR = {"approve_transfer": 0.02, "code": 0.08, "billing_lookup": 0.08}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="logs/decisions.jsonl")
    args = ap.parse_args()

    decisions = decision_log.read_all(args.log)
    if not decisions:
        print("No decisions yet. Run `make run REQUEST=\"...\"` a few times, "
              "label them with `make pending` + eval/label.py, then re-run this report.")
        return
    print(dashboard.summarize(decisions))
    print()
    rep = calibrator.calibrate_all(decisions, COSTS, MAX_ERROR)
    if not rep:
        print("Nothing to calibrate: set outcome.correct on the log rows.")
        return
    for key, r in rep.items():
        print(f"### {key} (n={r['n']}, breakeven_p={r['breakeven_p']})")
        print(f"  bins: {r['bins']}")
        print("  thr | cover | error | n_auto")
        for c in r["curve"]:
            print(f"  {c['threshold']:.2f} | {c['coverage']:.0%} | {c['error_rate']:.1%} | {c['n_auto']}")
        s = r["suggested"]
        flag = " ⚠️ error cap unreachable — collect more data or raise the cap" if s.get("cap_unmet") else ""
        print(f"  => suggested: thr={s['threshold']} cost={s['cost']:.0f} "
              f"cov={s['coverage']:.0%} err={s['error_rate']:.1%}{flag}")
        print()


if __name__ == "__main__":
    main()
