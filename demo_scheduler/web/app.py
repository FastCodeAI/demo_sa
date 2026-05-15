"""FastAPI viewer + agent control plane.

Read endpoints:
  GET  /                  → static/index.html
  GET  /api/constraints
  GET  /api/variables
  GET  /api/plan
  GET  /api/kpis

Agent + solver endpoints:
  POST /api/chat          → Supervisor.run_turn → intent + patch + verify
  POST /api/apply         → write a patch to catalog/ (verifier-cleared only)
  POST /api/solve         → kick off solve_orchestrated in a thread, return job_id
  GET  /api/solve/{id}    → poll job status (running|done|error)

Solves take 5-13 min. Jobs are tracked in-memory; restarting uvicorn drops them.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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


# ---------- in-memory job manager ----------

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


def _set_job(job_id: str, **kw: Any) -> None:
    with JOBS_LOCK:
        JOBS[job_id].update(kw)


def _solve_worker(job_id: str, use_catalog: bool, time_limit: int) -> None:
    """Run the full MILP+CP-SAT pipeline and write outputs/<dir>/."""
    try:
        _set_job(job_id, status="running", phase="loading", started_at=time.time())
        from demo_scheduler.config.load import load_config
        from demo_scheduler.data.load_excel import load_orders
        from demo_scheduler.data.synthesize import synthesize
        from demo_scheduler.solve.orchestrator import solve_orchestrated
        from demo_scheduler.output.plan import write_plan_csv, write_plan_json
        from demo_scheduler.output.gantt import write_gantt
        from demo_scheduler.output.kpis import compute_kpis, write_kpis
        from demo_scheduler.solve.solver import SolveResult

        cfg = load_config(None)
        if time_limit:
            cfg.solver.time_limit_seconds = time_limit
        raw = load_orders(quarter=cfg.horizon.quarter, include_backorder=cfg.horizon.include_backorder)
        params = synthesize(raw, cfg)

        _set_job(job_id, phase="solving_milp_cpsat", orders=len(params.orders))
        plan, info = solve_orchestrated(
            params,
            time_limit_milp_s=cfg.solver.time_limit_seconds,
            mip_gap=cfg.solver.mip_gap,
            time_limit_cpsat_per_week_s=10.0,
            max_retries=2,
            catalog_root=(CATALOG_ROOT if use_catalog else None),
        )

        _set_job(job_id, phase="writing_outputs")
        out = OUTPUTS_ROOT / ("q1_catalog" if use_catalog else "q1")
        out.mkdir(parents=True, exist_ok=True)
        write_plan_json(plan, out / "plan.json")
        write_plan_csv(plan, out / "plan.csv")
        try:
            write_gantt(plan, out / "gantt.png")
        except Exception:
            pass  # headless / matplotlib backend issues should not fail the job

        composite = SolveResult(
            status=info.milp_result.status,
            objective=info.milp_result.objective,
            solve_time_s=info.solve_time_s,
        )
        kpis = compute_kpis(plan, composite)
        kpis["cpsat"] = {
            "total_time_s": info.sequence_result.total_solve_time_s,
            "retries": info.retries,
            "infeasible_weeks": info.infeasible_weeks,
            "n_intervals": len(plan.sequence),
        }
        write_kpis(kpis, out / "kpis.json")

        _set_job(
            job_id,
            status="done",
            phase="done",
            finished_at=time.time(),
            output_dir=str(out.relative_to(PROJECT_ROOT)),
            kpis={"status": kpis.get("status"),
                  "otif_pct": kpis.get("otif_pct"),
                  "objective": kpis.get("objective"),
                  "total_idle_h": kpis.get("total_idle_h"),
                  "total_changeover_h": kpis.get("total_changeover_h"),
                  "solve_time_s": kpis.get("solve_time_s")},
        )
    except Exception as e:
        _set_job(
            job_id,
            status="error",
            error=f"{type(e).__name__}: {e}",
            traceback=traceback.format_exc(),
            finished_at=time.time(),
        )


# ---------- request models ----------

class ChatRequest(BaseModel):
    message: str
    persona: str | None = None
    use_mock: bool = False
    llm_backend: str = "auto"  # auto | openai | anthropic


class ApplyRequest(BaseModel):
    id: str
    parameter_changes: dict[str, Any]
    version_to: str
    rationale: str = ""
    actor: str = "ui"


class SolveRequest(BaseModel):
    use_catalog: bool = True
    time_limit: int = 600


# ---------- app ----------

app = FastAPI(title="DEMO Pharma Scheduler — Viewer", version="0.2.0")

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


def _verify_to_dict(v) -> dict | None:
    if v is None:
        return None
    return {
        "passed": v.passed,
        "needs_approval": v.needs_approval,
        "approval_gates": v.approval_gates,
        "outcomes": [{"name": o.name, "passed": o.passed, "detail": o.detail} for o in v.outcomes],
    }


@app.post("/api/chat")
def post_chat(req: ChatRequest) -> JSONResponse:
    """Send a message to the Supervisor; return the parsed intent + agent output."""
    from demo_scheduler.agents.llm import MockLLM, default_llm
    from demo_scheduler.agents.supervisor import run_turn

    if req.use_mock:
        llm = MockLLM(responses=[json.dumps({
            "id": "C-037",
            "parameter_changes": {"monthly_max": 2_500_000},
            "version_to": "2026-05-12.v2",
            "rationale": f"(mock) {req.message}",
        })])
        llm_name = "MockLLM"
    else:
        prefer = None if req.llm_backend == "auto" else req.llm_backend
        try:
            llm = default_llm(prefer=prefer)
        except RuntimeError as e:
            raise HTTPException(400, str(e))
        llm_name = f"{type(llm).__name__} {getattr(llm, 'model', '')}".strip()

    turn = run_turn(req.message, llm, CATALOG_ROOT, persona=req.persona)
    out: dict[str, Any] = {"intent": turn.intent, "llm": llm_name}

    if turn.intent == "edit_constraint":
        elic = turn.output.get("elicitation")
        if elic and elic.patch:
            out["patch"] = {
                "id": elic.patch.id,
                "parameter_changes": elic.patch.parameter_changes,
                "version_to": elic.patch.version_to,
                "rationale": elic.patch.rationale,
                "actor": elic.patch.actor,
            }
            out["verify"] = _verify_to_dict(elic.verify)
        elif elic and elic.error:
            out["error"] = elic.error
    elif turn.intent == "explain":
        exp = turn.output.get("explanation")
        if exp:
            out["text"] = getattr(exp, "text", str(exp))
    elif turn.intent == "unknown":
        out["text"] = turn.output.get("message", "I didn't recognise that intent.")
    else:
        # whatif / infeasibility / disruption / run_scheduler — pass through generically
        out["payload"] = _jsonable(turn.output)

    return JSONResponse(out)


def _jsonable(obj: Any) -> Any:
    """Best-effort JSON-safe conversion (dataclasses, paths, sets)."""
    if is_dataclass(obj):
        return _jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj


@app.post("/api/apply")
def post_apply(req: ApplyRequest) -> JSONResponse:
    """Write a verifier-cleared patch back to catalog/constraints/<id>-*.yaml.

    Mirrors `cli._apply_patch_to_disk` but also re-runs the Verifier as a
    safety check so a manipulated frontend can't bypass it.
    """
    from demo_scheduler.catalog.load import audit_append
    from demo_scheduler.verifier.verify import CatalogPatch, verify_patch

    patch = CatalogPatch(
        id=req.id,
        parameter_changes=req.parameter_changes,
        version_to=req.version_to,
        actor=req.actor,
        rationale=req.rationale,
    )
    verify = verify_patch(patch, CATALOG_ROOT, params=None, run_feasibility=False)

    if not verify.passed:
        return JSONResponse(
            {"applied": False, "reason": "verifier rejected", "verify": _verify_to_dict(verify)},
            status_code=409,
        )
    if verify.needs_approval:
        return JSONResponse(
            {"applied": False, "reason": "approval gate triggered", "verify": _verify_to_dict(verify)},
            status_code=409,
        )

    cat_dir = CATALOG_ROOT / "constraints"
    target: Path | None = None
    for p in cat_dir.glob(f"{patch.id}-*.yaml"):
        target = p
        break
    if target is None:
        target = cat_dir / f"{patch.id}.yaml"
    if not target.exists():
        raise HTTPException(404, f"catalog yaml not found for {patch.id}")

    with target.open() as f:
        data = yaml.safe_load(f)
    data.setdefault("parameters", {}).update(patch.parameter_changes)
    data["version"] = patch.version_to
    with target.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False)

    audit_append(CATALOG_ROOT, {
        "id": patch.id,
        "actor": patch.actor,
        "rationale": patch.rationale,
        "parameter_changes": patch.parameter_changes,
        "version_to": patch.version_to,
        "source": "web_ui",
    })

    return JSONResponse({"applied": True, "file": str(target.relative_to(PROJECT_ROOT))})


@app.post("/api/solve")
def post_solve(req: SolveRequest) -> JSONResponse:
    """Kick off a background solve. Returns job_id; poll /api/solve/{job_id}."""
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "status": "pending",
            "phase": "queued",
            "use_catalog": req.use_catalog,
            "time_limit": req.time_limit,
            "created_at": time.time(),
        }
    t = threading.Thread(
        target=_solve_worker,
        args=(job_id, req.use_catalog, req.time_limit),
        daemon=True,
    )
    t.start()
    return JSONResponse({"job_id": job_id, "status": "pending"})


@app.get("/api/solve/{job_id}")
def get_solve(job_id: str) -> JSONResponse:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job_id")
    out = dict(job)
    if out.get("started_at") and out.get("finished_at"):
        out["elapsed_s"] = out["finished_at"] - out["started_at"]
    elif out.get("started_at"):
        out["elapsed_s"] = time.time() - out["started_at"]
    return JSONResponse(out)


@app.get("/api/solve")
def list_solves() -> JSONResponse:
    with JOBS_LOCK:
        return JSONResponse(list(JOBS.values()))
