"""Helper: attach human feedback to the log — python -m eval.label --ts <ts> --correct 1/0.

List pending: python -m eval.label --pending
"""
from __future__ import annotations

import argparse
import json
from src import log as decision_log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="logs/decisions.jsonl")
    ap.add_argument("--pending", action="store_true")
    ap.add_argument("--ts", default=None)
    ap.add_argument("--correct", choices=["0", "1"], default=None)
    ap.add_argument("--by", default="human")
    args = ap.parse_args()

    rows = decision_log.read_all(args.log)
    if args.pending:
        pend = [r for r in rows if (r.get("outcome") or {}).get("correct") is None]
        print(f"{len(pend)} pending out of {len(rows)}")
        for r in pend[:20]:
            pol = r.get("policy") or {}
            print(f"- {r.get('ts')} [{r.get('slice')}] {pol.get('decision')}/{pol.get('action')} "
                  f"| {(r.get('state_snapshot') or {}).get('request', '')[:80]}")
        return
    if not args.ts or args.correct is None:
        ap.error("--ts and --correct are required (or use --pending)")
    updated = 0
    out_lines = []
    with open(args.log, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("ts") == args.ts:
                r.setdefault("outcome", {})["correct"] = args.correct == "1"
                r["outcome"]["feedback_by"] = args.by
                updated += 1
            out_lines.append(json.dumps(r, ensure_ascii=False))
    with open(args.log, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")
    print(f"updated: {updated}")


if __name__ == "__main__":
    main()
