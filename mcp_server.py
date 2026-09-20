"""MCP server — exposes the same core as tools for external agents.

Transports (mcp v2 API):
  stdio (default, for Claude Desktop / Cursor / `mcp run`):
    PYTHONPATH=. python3 mcp_server.py
  streamable HTTP:
    PYTHONPATH=. python3 mcp_server.py --http --port 8001

Tools:
  route_request(request, slice)   — Jev -> Policy -> Log routing decision
  verify_tool_call(request, tool_call) — firewall verdict for one tool call
  calibration_summary()           — dashboard summary + per-slice suggestions
"""
from __future__ import annotations

import argparse
import json

from mcp.server.mcpserver import MCPServer

from src import dashboard
from src import log as decision_log
from src import calibrator
from src.router import route
from src.slices import Slice
from src.verifier import gate_tool_call
from eval.report import COSTS, MAX_ERROR

mcp = MCPServer(
    name="confidence-gating",
    instructions=(
        "Route requests with route_request before calling an LLM or a tool; "
        "high-confidence 'auto' means run the deterministic handler, anything else "
        "means confirm, call the LLM, or send to a human. "
        "Always gate side-effecting tool calls with verify_tool_call first; "
        "'block' means refuse, 'confirm' means ask the user."
    ),
)


@mcp.tool()
def route_request(request: str, slice: Slice = "default") -> str:
    """Route a user request via Jev confidence gating.

    Returns JSON with source (jev_router | llm | confirm | human | llm_fallback),
    reason, route, confidence, and whether LLM output tokens were saved.
    Call this BEFORE invoking an LLM or tool.
    Pick `slice` from the allowed values by request topic/language.
    """
    return json.dumps(route(request, slice_name=slice))


@mcp.tool()
def verify_tool_call(request: str, tool_call: dict) -> str:
    """Firewall-check one proposed tool call against the user request.

    Returns JSON with verdict (pass | confirm | block | human) plus per-boundary
    scores (injection, tool_match, sensitive, irreversible).
    'block' -> refuse. 'confirm'/'human' -> ask the user. Only 'pass' executes.
    """
    return json.dumps(gate_tool_call(request, tool_call))


@mcp.tool()
def calibration_summary() -> str:
    """Human-feedback summary: automation rate, labeled error, and suggested
    thresholds per slice/action. Use it to decide whether to lower/raise cuts."""
    decisions = decision_log.read_all()
    return json.dumps({
        "summary": dashboard.summarize(decisions),
        "calibration": calibrator.calibrate_all(decisions, COSTS, MAX_ERROR),
    })


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="serve over streamable HTTP instead of stdio")
    ap.add_argument("--port", type=int, default=8001)
    args = ap.parse_args()
    if args.http:
        mcp.run(transport="streamable-http", port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
