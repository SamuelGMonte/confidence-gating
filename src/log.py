"""Immutable decision log — JSONL, one line per decision.

README §4 contract. Never overwrites, append-only.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

LOG_PATH = os.getenv("DECISION_LOG_PATH", "logs/decisions.jsonl")


def state_hash(state: dict) -> str:
    blob = json.dumps(state, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:16]


def append(record: dict, path: str = LOG_PATH) -> dict:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        **record,
    }
    rec.setdefault("state_hash", state_hash(rec.get("state_snapshot", {})))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read_all(path: str = LOG_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
