"""Adaptive per-slice controller — v1, simple, no RL.

Rule: start conservative; on every feedback batch,
step up fast when the error target is breached, step down slowly when safe.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

STATE_PATH = os.getenv("THRESHOLD_STATE_PATH", "logs/thresholds.json")

MIN_T, MAX_T = 0.50, 0.97
UP_STEP, DOWN_STEP = 0.05, 0.02


class ThresholdController:
    def __init__(self, initial: dict | None = None, path: str = STATE_PATH):
        self.path = path
        self.thresholds: dict[str, float] = dict(initial or {})
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.thresholds.update(json.load(f))
            except Exception:
                pass

    def get(self, action: str, slice_name: str = "default", default: float = 0.85) -> float:
        return self.thresholds.get(f"{slice_name}/{action}", default)

    def update(self, decisions: list[dict], target_error: dict | None = None,
               min_batch: int = 20) -> dict:
        """decisions: log rows with outcome.correct. Returns applied changes."""
        target_error = target_error or {}
        groups: dict[str, list] = defaultdict(list)
        for d in decisions:
            out = d.get("outcome") or {}
            if out.get("correct") is None:
                continue
            pol = d.get("policy") or {}
            key = f"{d.get('slice', 'default')}/{pol.get('action', 'unknown')}"
            groups[key].append(bool(out["correct"]))

        changes = {}
        for key, labels in groups.items():
            if len(labels) < min_batch:
                continue
            act = key.split("/", 1)[1]
            target = target_error.get(act, 0.05)
            err = 1.0 - sum(labels) / len(labels)
            cur = self.thresholds.get(key, 0.85)
            if err > target:
                new = min(MAX_T, round(cur + UP_STEP, 2))
            elif err < target / 2:
                new = max(MIN_T, round(cur - DOWN_STEP, 2))
            else:
                continue
            if new != cur:
                self.thresholds[key] = new
                changes[key] = {"old": cur, "new": new, "err": round(err, 4), "n": len(labels)}
        self.save()
        return changes

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.thresholds, f, indent=2, sort_keys=True)
