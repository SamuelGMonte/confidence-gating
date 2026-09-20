"""HTTP API — thin layer over the core (router + verifier + report).

Run:
  uvicorn server:app --port 8000
  curl -X POST localhost:8000/route -H 'Content-Type: application/json' \
    -d '{"request": "where is my second invoice copy?", "slice": "billing"}'
"""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from src import dashboard
from src import log as decision_log
from src import calibrator
from src.router import route
from src.slices import Slice
from src.verifier import gate_tool_call
from eval.report import COSTS, MAX_ERROR

app = FastAPI(title="confidence-gating", version="0.5.0")


class RouteIn(BaseModel):
    request: str = Field(min_length=1)
    slice: Slice = "default"


class VerifyIn(BaseModel):
    request: str = Field(min_length=1)
    tool_call: dict


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/route")
def route_endpoint(body: RouteIn) -> dict:
    """Jev -> Policy -> Log. Returns auto | confirm | llm | human + reason."""
    return route(body.request, slice_name=body.slice)


@app.post("/verify")
def verify_endpoint(body: VerifyIn) -> dict:
    """Firewall for one proposed tool call. Returns block | confirm | pass | human."""
    return gate_tool_call(body.request, body.tool_call)


@app.get("/report")
def report_endpoint() -> dict:
    """Dashboard summary + per-slice/action calibration over labeled log rows."""
    decisions = decision_log.read_all()
    return {
        "summary": dashboard.summarize(decisions),
        "calibration": calibrator.calibrate_all(decisions, COSTS, MAX_ERROR),
    }
