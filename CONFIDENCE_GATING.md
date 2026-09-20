# Hybrid Pipeline: Confidence Gating (Jev + LLM)

This document describes the **Confidence-Gated Routing** architecture using **Jev (TypeSafe AI)** as the decision layer and an autoregressive LLM (OpenAI/Anthropic) only when the decision is uncertain or the task requires text generation.

The pattern is documented by TypeSafe: [Confidence-Gated Routing](https://docs.typesafe.ai/patterns/confidence-routing). This is an implementation of it, not a new product.

**Core correction:** Jev does not solve the task and does not generate prose. It evaluates `state` against typed questions (`choice`, `score`, `noul`/`boolean`) and returns closed answers with probabilities plus, for `choice`/`score`, `confidence`. Code picks the branch. The LLM only steps in to write, reason open-endedly, or cover low confidence.


```
┌───────────────────────────┐
│ Request / Task            │
└─────────────┬─────────────┘
               │
               ▼
┌────────────────────────┐
│ Jev: choice / score    │
│ (no output tokens)     │
└────────────┬───────────┘
              │
              ▼
     /───────────────────\
     < Confidence ≥ cutoff >
     \───────────────────/
              │
      Yes ────┴───── No
       │              │
       ▼              ▼
┌──────────────┐  ┌────────────────────────┐
│ Code acts    │  │ LLM (GPT/Claude)       │
│ on Jev route │  │ or human review        │
│ (lookup,     │  │ (writes text / judges  │
│  tool,       │  │  the ambiguous case)   │
│  refuse)     │  └────────────────────────┘
└──────────────┘
```

Jev **never** replaces LLM output. High confidence only authorizes a branch your code already knows how to run.

## 1. Prerequisites

```bash
pip install httpx openai
```

Official API: `POST https://api.typesafe.ai/v1/systemone`
Env vars: `TYPESAFE_API_KEY`, `OPENAI_API_KEY`
Model: `jev-latest` (or pinned, e.g. `jev-1.13.0`)

Official SDKs exist (`typesafe` / `@typesafe-ai/sdk`) and, in TypeScript, `experimental_evaluate` from the AI SDK (`typesafe-ai/jev`). The example below uses direct HTTP to avoid depending on an illustrative SDK.

## 2. Primitives (what Jev returns)

| Type | Question | Answer | Separate confidence? |
| :--- | :--- | :--- | :--- |
| `choice` | One option from a closed set (up to 255) | `choice` + `probabilities` | Yes (`0`–`1`, distribution concentration) |
| `score` | Grade on an ordered rubric (2–10 levels) | `score` + `probabilities` | Yes |
| `noul` / `boolean` | Is the statement true? | probability of true | No. Threshold on the value itself |

`confidence` ≠ winning-option probability. Low confidence = spread distribution = don't automate.

Thresholds are not universal. Calibrate on your data. Numbers below are starting points.

## 3. Code layout (Python)

```python
import os
import asyncio
import httpx
from openai import OpenAI

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

TYPESAFE_URL = os.getenv("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
if not TYPESAFE_URL.endswith("/v1/systemone"):
    TYPESAFE_URL = f"{TYPESAFE_URL}/v1/systemone"

CONFIDENCE_THRESHOLD = 0.85
COMPLEXITY_LLM = 1.0

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
    "complexity": {
        "type": "score",
        "instructions": "How hard is this to solve without a generative model?",
        "criteria": [
            "One known step, no judgment",
            "Multiple steps, but still routable by code",
            "Edge case; needs reasoning or writing",
        ],
    },
}


async def jev_evaluate(prompt: str) -> dict:
    payload = {
        "model": os.getenv("TYPESAFE_MODEL", "jev-latest").strip() or "jev-latest",
        "state": {"request": prompt},
        "questions": QUESTIONS,
    }
    headers = {
        "Authorization": f"Bearer {os.getenv('TYPESAFE_API_KEY', '').strip()}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(TYPESAFE_URL, json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("typesafe_missing_answers")
    return answers


async def run_llm(prompt: str) -> str:
    completion = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
    )
    return completion.choices[0].message.content or ""


def _choice(answers: dict, key: str) -> tuple[str | None, float]:
    item = answers.get(key) or {}
    choice = item.get("choice")
    try:
        confidence = float(item.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return (str(choice) if choice is not None else None), confidence


def _score(answers: dict, key: str) -> tuple[float | None, float]:
    item = answers.get(key) or {}
    try:
        score = float(item["score"]) if item.get("score") is not None else None
    except (TypeError, ValueError):
        score = None
    try:
        confidence = float(item.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return score, confidence


async def confidence_gated_router(user_prompt: str) -> dict:
    try:
        answers = await jev_evaluate(user_prompt)
    except Exception:
        return {
            "source": "llm_fallback",
            "reason": "jev_unavailable",
            "data": await run_llm(user_prompt),
            "output_tokens_saved": False,
        }

    route, route_conf = _choice(answers, "route")
    complexity, cx_conf = _score(answers, "complexity")

    if (
        route is None
        or not (0.0 <= route_conf <= 1.0)
        or route_conf < CONFIDENCE_THRESHOLD
        or route == "human"
    ):
        return {
            "source": "llm_fallback",
            "reason": "low_confidence_or_human",
            "route": route,
            "confidence": route_conf,
            "data": await run_llm(user_prompt),
            "output_tokens_saved": False,
        }

    if route == "llm" or (
        complexity is not None
        and complexity >= COMPLEXITY_LLM
        and cx_conf >= CONFIDENCE_THRESHOLD
    ):
        return {
            "source": "llm_jev_route",
            "reason": "jev_requested_generation",
            "route": route,
            "confidence": route_conf,
            "data": await run_llm(user_prompt),
            "output_tokens_saved": False,
        }

    return {
        "source": "jev_router",
        "reason": "high_confidence_code",
        "route": route,
        "confidence": route_conf,
        "data": {"action": "run_deterministic_handler", "request": user_prompt},
        "output_tokens_saved": True,
    }


async def main():
    simple = "I need to update the local database connection string in the .env file"
    complex = (
        "The system shows an intermittent race condition "
        "in the checkout microservice under high load."
    )
    print(await confidence_gated_router(simple))
    print(await confidence_gated_router(complex))


if __name__ == "__main__":
    asyncio.run(main())
```

Jev failure (timeout, 429, 529, invalid body) **never** becomes automatic action. It falls through to the LLM or a human queue.

## 4. Metrics (what actually changes)

Jev bills **input only**. There are no output tokens because there is no generation.

| Metric | LLM only | Jev decides + LLM when needed |
| :--- | :--- | :--- |
| Output tokens on Jev branch | ~150+ per call | 0 (Jev doesn't write the answer) |
| Jev branch latency | 1–3 s typical | tens of ms, if the handler is code |
| Cost | always the LLM | cheap Jev on the hot path; LLM only on fallback / generation |
| Savings | — | only on routes that **code** can close; not on text-heavy ones |

Tables claiming "−100% / 95% faster / 60–80% cheaper" are marketing. Measure above-threshold coverage on your traffic.

## 5. Threshold tuning

- **Conservative (`≈ 0.90`):** action with high error cost. Below it: LLM or human.
- **Economic (`≈ 0.75`):** triage, labeling, reversible routing.
- One threshold **per action**, not one global. Read-only tolerates more error than writes.
- `noul`: two cuts (`yes` high, `no` low); the middle band is uncertain — don't round it.
- Pin the model after calibrating. `jev-latest` moves the behavior you measured.

## 6. What not to do

- Don't treat Jev as a cheap LLM with Pydantic (`response_model=TaskAnalysis`). That is not the API.
- Don't return Jev's classification as if it solved the task.
- Don't use `confidence` from `noul` — the field doesn't exist.
- Don't treat high confidence as authorization. Permissions stay in code.
- Don't invent SDKs (`TypeSafeClient.evaluate`). Use `/v1/systemone` or the official SDK.

There is currently no drop-in project where "Jev answers and the LLM only steps in on low confidence". What exists is this pattern + SDKs. This file is the flow contract.
