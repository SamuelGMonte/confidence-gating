"""HTTP client for TypeSafe Jev — POST /v1/systemone.

No illustrative SDK. Retries only on 429/529 + timeout.
Any failure becomes JevUnavailable — the caller never treats it as `auto`.
"""
from __future__ import annotations

import os
import time

import httpx

TYPESAFE_BASE_URL = os.getenv("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
if not TYPESAFE_BASE_URL.endswith("/v1/systemone"):
    TYPESAFE_BASE_URL = f"{TYPESAFE_BASE_URL}/v1/systemone"

DEFAULT_MODEL = (os.getenv("TYPESAFE_MODEL", "jev-1.13.0").strip() or "jev-1.13.0")
DEFAULT_TIMEOUT = float(os.getenv("TYPESAFE_TIMEOUT_S", "15.0"))

RETRYABLE = {429, 529}
MAX_RETRIES = 2


class JevUnavailable(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}"[:500])
        self.reason = reason
        self.detail = detail


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('TYPESAFE_API_KEY', '').strip()}",
        "Content-Type": "application/json",
    }


def _validate_answers(body: dict) -> dict:
    answers = body.get("answers")
    if not isinstance(answers, dict) or not answers:
        raise JevUnavailable("typesafe_missing_answers", str(body)[:300])
    return answers


def evaluate(state: dict, questions: dict, model: str | None = None) -> dict:
    """Synchronous call. Returns {'answers': ..., 'model': ...}."""
    payload = {"model": model or DEFAULT_MODEL, "state": state, "questions": questions}
    last_err = ""
    for attempt in range(MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                r = client.post(TYPESAFE_BASE_URL, json=payload, headers=_headers())
            if r.status_code in RETRYABLE and attempt < MAX_RETRIES:
                time.sleep(0.5 * (2**attempt))
                last_err = f"http_{r.status_code}"
                continue
            r.raise_for_status()
            body = r.json()
            return {"answers": _validate_answers(body), "model": body.get("model", payload["model"])}
        except JevUnavailable:
            raise
        except Exception as e:  # timeout, connection, non-retryable 4xx/5xx
            last_err = f"{type(e).__name__}:{e}"[:200]
            if attempt < MAX_RETRIES and ("Timeout" in type(e).__name__ or "Connect" in type(e).__name__):
                time.sleep(0.5 * (2**attempt))
                continue
            raise JevUnavailable("jev_request_failed", last_err) from e
    raise JevUnavailable("jev_retries_exhausted", last_err)


async def aevaluate(state: dict, questions: dict, model: str | None = None) -> dict:
    """Async variant."""
    import asyncio

    payload = {"model": model or DEFAULT_MODEL, "state": state, "questions": questions}
    last_err = ""
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                r = await client.post(TYPESAFE_BASE_URL, json=payload, headers=_headers())
            if r.status_code in RETRYABLE and attempt < MAX_RETRIES:
                await asyncio.sleep(0.5 * (2**attempt))
                last_err = f"http_{r.status_code}"
                continue
            r.raise_for_status()
            body = r.json()
            return {"answers": _validate_answers(body), "model": body.get("model", payload["model"])}
        except JevUnavailable:
            raise
        except Exception as e:
            last_err = f"{type(e).__name__}:{e}"[:200]
            if attempt < MAX_RETRIES and ("Timeout" in type(e).__name__ or "Connect" in type(e).__name__):
                await asyncio.sleep(0.5 * (2**attempt))
                continue
            raise JevUnavailable("jev_request_failed", last_err) from e
    raise JevUnavailable("jev_retries_exhausted", last_err)
