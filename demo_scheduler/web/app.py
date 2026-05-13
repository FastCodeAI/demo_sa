"""FastAPI viewer for the constraint catalog, variables, and latest plan.

This is the read-only first cut of the web frontend. Endpoints:

  GET /                   → serve static/index.html
  GET /api/constraints    → catalog rows (JSON)
  GET /api/variables      → model-variable directory (static metadata)
  GET /api/plan           → most recent plan.json (q1_catalog > q1)
  GET /api/kpis           → matching kpis.json next to the plan

Spawn with `demo_scheduler serve`, which runs uvicorn on localhost:8000.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
CATALOG_ROOT = PROJECT_ROOT / "catalog"
OUTPUTS_ROOT = PROJECT_ROOT / "outputs"


VARIABLES: list[dict] = [
    {"name": "x_pack",       "domain": "Binary",            "index": "(order, machine, week)",      "purpose": "1 iff order o is placed on machine m in week w."},
    {"name": "vol_pack",     "domain": "Non-neg real",      "index": "(order, machine, week)",      "purpose": "Volume of order o packed on (m, w) — units."},
    {"name": "y_pack",       "domain": "Binary",            "index": "(machine, format, week)",     "purpose": "1 iff machine m is set up for format f in week w."},
    {"name": "co_pack",      "domain": "[0, 1] real",       "index": "(machine, f_prev, f_next, w)", "purpose": "Changeover indicator (linearised from y_pack adjacency)."},
    {"name": "unfilled",     "domain": "Non-neg real",      "index": "(order,)",                    "purpose": "Slack: demand left unmet at horizon end."},
    {"name": "late",         "domain": "Non-neg real",      "index": "(order,)",                    "purpose": "Days late past order due date (soft penalty)."},
    {"name": "idle_hours",   "domain": "Non-neg real",      "index": "(machine, week)",             "purpose": "Capacity not booked by packing or changeover."},
    {"name": "qty_prod",     "domain": "Non-neg real",      "index": "(material, week)",            "purpose": "Production volume of SKU s in week w (Stage-1 output)."},
    {"name": "piramal_under","domain": "Non-neg real",      "index": "(month,)",                    "purpose": "Shortfall against Piramal monthly_min (penalised in objective)."},
    {"name": "piramal_over", "domain": "Non-neg real",      "index": "(month,)",                    "purpose": "Excess above Piramal monthly_max (penalised in objective)."},
]


app = FastAPI(title="DEMO Pharma Scheduler — Viewer", version="0.1.0")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/constraints")
def get_constraints() -> JSONResponse:
    from demo_scheduler.catalog.load import load_catalog

    rows = load_catalog(CATALOG_ROOT)
    payload = []
    for r in rows:
        payload.append({
            "id": r.id,
            "name": r.name,
            "category": r.category,
            "severity": r.severity.value,
            "solver_layer": r.solver_layer.value,
            "enabled": r.enabled,
            "pattern": r.formal_expr.type.value,
            "parameters": r.parameters,
            "units": r.units,
            "business_rules": r.business_rules,
            "owner": r.owner,
            "version": r.version,
            "description": r.description,
        })
    return JSONResponse(payload)


@app.get("/api/variables")
def get_variables() -> JSONResponse:
    return JSONResponse(VARIABLES)


def _latest_plan_dir() -> Path | None:
    for sub in ("q1_catalog", "q1"):
        p = OUTPUTS_ROOT / sub / "plan.json"
        if p.exists():
            return p.parent
    return None


@app.get("/api/plan")
def get_plan() -> JSONResponse:
    d = _latest_plan_dir()
    if d is None:
        raise HTTPException(404, "no plan.json found — run `demo_scheduler solve` first")
    data = json.loads((d / "plan.json").read_text())
    data["_source"] = d.name
    return JSONResponse(data)


@app.get("/api/kpis")
def get_kpis() -> JSONResponse:
    d = _latest_plan_dir()
    if d is None or not (d / "kpis.json").exists():
        raise HTTPException(404, "no kpis.json found")
    return JSONResponse(json.loads((d / "kpis.json").read_text()))
