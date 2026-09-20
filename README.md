# Adaptive Confidence Gating — Expected-Cost Routing (Jev + LLM)

> Jev decides. Code authorizes. Cost picks the threshold.

This project evolves the [Confidence-Gated Routing](https://docs.typesafe.ai/patterns/confidence-routing) pattern from a fixed threshold (`0.85` global) to **adaptive gating, per action and per slice, optimized by expected cost**.

It is not a `Jev -> LLM` wrapper. It is an **optimizer + audit contract**: given Jev's confidence, the cost of being wrong and the cost of review, the system picks `automate | confirm | escalate` and learns the optimal threshold from your data.

## 1. Why this exists

A single threshold breaks because errors don't cost the same:


| Action                                    | Type                     | C_error           | C_review        | Initial threshold                     |
| ----------------------------------------- | ------------------------ | ----------------- | --------------- | ------------------------------------- |
| `check_balance` / `assign_queue`          | read-only, reversible    | low (e.g. $2)     | $0.5–2          | `~0.60–0.70`                          |
| `draft_llm`                               | recoverable              | medium            | LLM cost        | `~0.75`                               |
| `approve_transfer` / `delete` / `publish` | irreversible / sensitive | high (e.g. $5000) | expensive human | `~0.88–0.95` + mandatory confirmation |


Core rule:

```
Automate when: (1 - p_correct(conf)) * C_error < C_review
```

Where `p_correct(conf)` is **not** raw confidence — it is the measured accuracy on your golden set in that confidence band, for that action, in that slice.

Jev `confidence` (`choice`/`score`) = concentration of the distribution. Useful for routing, not a per-case guarantee. `noul` has no `confidence` — use its own `yes` probability with two cuts.

## 2. Business logic (who decides what)

**Sensitive is defined in code, never by Jev.** Two mechanisms, both in `src/policy.py`:
- `ACTION_POLICY[action]["confirm_always"]` — the hand-written list of sensitive actions
  (`approve_transfer`, `human`). Even at 0.97 confidence these yield at most `confirm`, never `auto`.
- `IRREVERSIBLE_NOUL_CUT = 0.70` — the code-owned cutoff. Jev's only role is scoring the
  specific case via the `irreversible` (`noul`) question; if `P(yes) ≥ 0.70`, the policy
  forces `confirm`/`human`. Confidence is never authorization.

**Slice = the segment you calibrate separately** (e.g. `billing` vs `technical`, a language,
a tenant), passed as `route(prompt, slice_name=...)` and stored in the log's `slice` field.
Accuracy-at-a-given-confidence differs per segment, so each `(slice, action)` pair gets its
own threshold. A global threshold would hide that. The vocabulary is closed
(`src/slices.py::KNOWN_SLICES` + `Slice` enum): MCP/HTTP schemas only offer the known
names, and free-form callers are normalized (`Support/EN` → `support/en`) or collapse
to `default` with `slice_mapped: true` in the response, so a caller can never
fragment calibration by inventing names. Add a name only when it has ~20+ labeled rows.
Consumer repos get a copy-paste starting point in `agents/AGENTS.md.snippet`.

**Confidence comes from Jev, travels through the log.** Each `route` (`choice`) answer carries
`confidence` (0–1, distribution concentration). `router.py` logs it; human labeling
(`eval/label.py`) pairs it with `outcome.correct`. Every labeled row is therefore a
`(confidence, correct)` pair — the raw material for calibration.

**The scan: every candidate threshold `t` is tested.** `cost_model.suggest_threshold()` tries
`0.50, 0.51, … 0.97`. For each `t`, over the labeled rows of that slice/action:

```
auto   = rows with confidence >= t      (the rest go to review)
errors = auto rows labeled correct=false
cost(t)  = errors * C_error + len(review) * C_review
error(t) = errors / len(auto)
```

Candidates breaching the error cap are discarded; the cheapest survivor wins. Small `t` =
automate almost everything (high coverage, many errors); large `t` = automate little
(few errors, much review cost). The winner becomes the suggested `threshold`.

**The cap (`MAX_ERROR`) is policy, set only by humans.** It lives in `eval/report.py`
(e.g. `approve_transfer: 0.02`, `code: 0.08`) and declares how much automated error the
business tolerates per action. Nothing in the system writes to it — the calibrator only
reads it, the controller only moves *thresholds* (toward its own `target_error`). If the
report says `cap_unmet`, no threshold meets your cap: collect more labels, raise the cap,
or automate less. Thresholds are tactics (the system may move them); the cap is policy.

**Below-floor is cost-aware, not a hardcoded `human`.** When confidence falls under
`FLOOR` (0.60), the policy checks `(1 - conf) * C_error < C_review` with that
slice/action's costs (`cost_model.SLICE_COSTS`, else `ACTION_POLICY`). Cheap,
reversible attempts — e.g. slice `dev`, where a wrong suggestion is discarded, not
executed — fall through to `llm` (`below_floor_cheap_attempt`) even at 0.29.
Expensive ones stay `human`. The floor is a trigger for the cost check, not a verdict.

## 3. Architecture

```mermaid
flowchart TD
    REQ["Request"] --> JEV["Jev<br/>route + confidence"]
    JEV --> POL{"Policy<br/>confident enough?"}
    POL -->|yes, reversible| AUTO["auto: run code"]
    POL -->|yes, sensitive| CONF["confirm with user"]
    POL -->|no, or Jev down| LLM["LLM / human"]
    AUTO --> VER{"Verifier<br/>tool safe?"}
    LLM --> VER
    VER -->|pass| EXEC["execute"]
    VER -->|block| REF["refuse"]
    EXEC --> LOG["Log everything"]
    REF --> LOG
    CONF --> LOG
    LOG --> CAL["Calibrator<br/>retune thresholds"]
    CAL --> POL
```



Code never delegates authorization to the model. High confidence only authorizes a branch the code already knows how to run and is permitted to run.

## 4. Decision contract (log)

Each decision saves one JSONL line:

```json
{
  "ts": "2026-09-20T12:00:00Z",
  "state_hash": "sha256:...",
  "state_snapshot": {"request": "...", "tenant": "...", "permissions": [...]},
  "question_version": "v4",
  "model": "jev-1.13.0",
  "answers": {
    "route": {"choice": "billing", "probabilities": {"billing": 0.82, "...": 0.1}, "confidence": 0.78},
    "risk": {"score": 1.0, "confidence": 0.9},
    "irreversible": {"prob_yes": 0.05}
  },
  "slice": "billing",
  "policy": {"action": "billing_lookup", "threshold": 0.71, "decision": "auto"},
  "outcome": {"correct": null, "feedback_by": null, "cost": null}
}
```

Required fields: `state_snapshot`, `question_version`, pinned `model` (never `jev-latest` in prod), full `probabilities`, `policy_version`, `decision + reason`.

Jev failure (timeout, 429, 529, invalid body) never becomes `auto`. It becomes `llm_fallback` or `human` with `reason=jev_unavailable`.

## 5. Calibration

1. Build a golden set: 200–500 real cases with human labels. Include easy, ambiguous, context-free and unroutable (`other`) cases.
2. Run Jev, group by confidence band **per action and per slice**:

```
conf 0.6-0.7: 68% accuracy | 0.7-0.8: 82% | 0.8-0.9: 91% | 0.9-1.0: 97%
```

1. Plot `coverage (% above threshold) x error (%)` and `total_cost = errors*C_error + reviews*C_review`.
2. Pick the cost-minimizing threshold under an error cap (e.g. error <2% on transfers).

Expected output:

```
threshold 0.60 -> automates 92%, errors 11%, cost $1200
threshold 0.80 -> automates 65%, errors 4%,  cost $480
threshold 0.90 -> automates 28%, errors 1.2%, cost $310 <- best for expensive actions
```

Re-run before changing `question_version` or `model_id`. Roll out gradually, comparing `review_rate` and `error_rate`.

## 6. Optimization (online later)

Simple controller, no RL in v1:

```
start conservative (0.90)
every N=100 decisions with feedback:
  if slice_error > target: threshold += 0.05
  if slice_error < target and coverage < goal: threshold -= 0.02
  clamp [0.50, 0.97], per action/slice only, with cooldown
```

Future: contextual bandit using full `probs`, not just `confidence`.

## 7. Repo layout

```
/src
  jev_client.py       # POST /v1/systemone, retry 429/529, timeout
  policy.py           # auto | confirm | llm | human (permissions + cost)
  cost_model.py       # C_error, C_review, E[cost]
  calibrator.py       # coverage x error, suggests thresholds
  controller.py       # online per-slice adjustment
  log.py              # immutable JSONL
  router.py           # Jev -> Policy -> Log wiring
  verifier.py         # agent firewall (noul checks in parallel)
  slices.py           # closed slice vocabulary + normalization
server.py             # HTTP API: POST /route, POST /verify, GET /report
mcp_server.py         # MCP tools: route_request, verify_tool_call, calibration_summary
/eval
  golden_example.jsonl # human labels
  report.py           # accuracy by bin, curves, cost
  label.py            # attach human feedback to the log
/questions
  v4.yaml             # criteria versioned with the code
/agents
  AGENTS.md.snippet   # copy-paste consumer instructions for other repos' AGENTS.md
/tests
  test_smoke.py       # unit tests
README.md
CONFIDENCE_GATING.md  # reference for the previous static pattern
```



## 8. What NOT to do

- Don't use `jev-latest` in prod. Pin `jev-1.x`.
- Don't treat `confidence` as authorization. Permissions stay in code.
- Don't use a global threshold. One per action x risk.
- Don't ask a single generic `is this safe?` question. Check per boundary: input, tool_call, output, citation.
- Don't chain dependent decisions in one call — questions in the same request are independent and parallel. If step 2 depends on step 1, make 2 calls.
- Don't return the classification as the solution.


## 9. Quickstart

Prerequisites: Python 3.10+, a TypeSafe API key ([console.typesafe.ai](https://console.typesafe.ai)).

```bash
git clone <this-repo-url> confidence-gating && cd confidence-gating

make setup   # venv + deps + creates .env — run once
# edit .env: set TYPESAFE_API_KEY=sk-... (keep TYPESAFE_MODEL pinned)

make test    # unit tests — verifies the wiring
make run REQUEST="where is my second invoice copy?"   # first LIVE call
```

That's it. `.env` auto-loads on startup, so there are no exports and no `PYTHONPATH`
to remember — every target below just works. First runs append to `logs/decisions.jsonl`;
inspect the confidence before trusting automation.

Daily use:


| Command                      | What it does                                                     |
| ---------------------------- | ---------------------------------------------------------------- |
| `make run REQUEST="..."`     | one live routing decision, printed as JSON                       |
| `make report`                | calibration (coverage x error) over your labeled log             |
| `make pending`               | list log rows waiting for human feedback                         |
| `make api`                   | HTTP API on :8000 (`POST /route`, `POST /verify`, `GET /report`) |
| `make mcp` / `make mcp-http` | MCP tools for external agents (stdio / streamable HTTP on :8001) |


Config (`.env`; only `TYPESAFE_API_KEY` is required for live calls):


| Var                    | Default                                               |
| ---------------------- | ----------------------------------------------------- |
| `TYPESAFE_API_KEY`     | —                                                     |
| `TYPESAFE_MODEL`       | `jev-1.13.0` (pinned — never `jev-latest` in prod)    |
| `ADAPTIVE_THRESHOLDS`  | `0` (set `1` so `route()` uses controller thresholds) |
| `DECISION_LOG_PATH`    | `logs/decisions.jsonl`                                |
| `THRESHOLD_STATE_PATH` | `logs/thresholds.json`                                |


MCP client example (`claude_desktop_config.json` — adjust paths):

```json
{
  "mcpServers": {
    "confidence-gating": {
      "command": "/abs/path/to/confidence-gating/.venv/bin/python",
      "args": ["/abs/path/to/confidence-gating/mcp_server.py"],
      "env": {
        "PYTHONPATH": "/abs/path/to/confidence-gating",
        "TYPESAFE_API_KEY": "sk-...",
        "TYPESAFE_MODEL": "jev-1.13.0"
      }
    }
  }
}
```

Troubleshooting:

- `jev_unavailable:http_401` → bad/missing key — check `.env`.
- `jev_unavailable:http_429/529` → rate limit/overload; the router retries twice, then falls back to `llm_fallback`/`human` — never `auto`.
- Everything routes to `human` with low confidence → `questions/v4.yaml` criteria are too generic for your domain; label ~50 cases (`make pending`, then `eval/label.py`) and check `make report` bins before lowering thresholds.

References: [Confidence](https://docs.typesafe.ai/confidence) · [Confidence-Gated Routing](https://docs.typesafe.ai/patterns/confidence-routing) · [Models / pinning](https://docs.typesafe.ai/models) · Launch: TypeSafe AI System One + Jev (09/15/2026).