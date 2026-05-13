"""Two-stage orchestrator: Tier-2 MILP → Tier-3 CP-SAT.

Phase 2a entry point. Replaces the bare `solve()` call from Phase 1.

Flow:
  1. Build + solve the Tier-2 MILP (assignment, balance, capacity, bands).
  2. Run Tier-3 CP-SAT once per week to produce minute-level schedules.
  3. If any week is sequencing-infeasible, tighten the MILP team-hours cap
     for that week and re-solve. Up to `max_retries` iterations.
  4. Return the combined `Plan` (assignments + minute-level intervals) and
     a `RunInfo` with per-tier timings.

The MILP is the source of truth for *what* gets packed and *where*; CP-SAT
sequences within each week. Catalog wiring + LangGraph agents come in 2b/2c.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pyomo.environ as pyo

from demo_scheduler.data.synthesize import Parameters
from demo_scheduler.model.build import build_model
from demo_scheduler.solve.extract import Plan, extract_plan
from demo_scheduler.solve.solver import SolveResult, solve as solve_milp
from demo_scheduler.solve.solver_cpsat import SequenceResult, solve_sequence


@dataclass
class RunInfo:
    milp_result: SolveResult
    sequence_result: SequenceResult
    retries: int = 0
    cuts_applied: list[dict] = field(default_factory=list)

    @property
    def status(self) -> str:
        return self.milp_result.status

    @property
    def objective(self) -> float | None:
        return self.milp_result.objective

    @property
    def solve_time_s(self) -> float:
        return self.milp_result.solve_time_s + self.sequence_result.total_solve_time_s

    @property
    def infeasible_weeks(self) -> list[int]:
        return list(self.sequence_result.infeasible_weeks)


def _add_team_hours_cut(
    model: pyo.ConcreteModel,
    params: Parameters,
    week: int,
    factor: float,
) -> dict:
    """Tighten the team-hours cap for one week so the next MILP solve gives
    CP-SAT a more sequenceable assignment.

    Returns a dict describing the cut for the audit log.
    """
    machine_formats = getattr(model, "machine_formats", {})
    current_cap = float(params.team_hours_per_week) * factor
    expr = sum(
        model.co_pack[mm, f1, f2, week] * params.changeover_h[(mm, f1, f2)]
        for mm in params.machines
        for f1 in machine_formats.get(mm, [])
        for f2 in machine_formats.get(mm, []) if f1 != f2
        if (mm, f1, f2, week) in model.co_pack
    )
    cut_name = f"co_team_cut_w{week}_x{int(factor * 100)}"
    model.add_component(cut_name, pyo.Constraint(expr=expr <= current_cap))
    return {"name": cut_name, "week": week, "cap_hours": current_cap}


def solve_orchestrated(
    params: Parameters,
    time_limit_milp_s: int = 600,
    mip_gap: float = 0.01,
    time_limit_cpsat_per_week_s: float = 10.0,
    max_retries: int = 2,
    catalog_root=None,
) -> tuple[Plan, RunInfo]:
    """Run the full Tier-2 + Tier-3 pipeline with a tightening cut loop.

    If `catalog_root` is given, the Tier-2 model is assembled from the
    catalog at that path instead of from the hardcoded build_model().
    """
    if catalog_root is not None:
        from demo_scheduler.model.compile import build_from_catalog
        model, _ = build_from_catalog(params, catalog_root)
    else:
        model = build_model(params)
    milp_result = solve_milp(model, time_limit_milp_s, mip_gap)

    if milp_result.status not in ("optimal", "feasible", "time_limit"):
        # MILP itself is unusable; skip CP-SAT.
        return extract_plan(model, params), RunInfo(
            milp_result=milp_result,
            sequence_result=SequenceResult(),
        )

    seq = solve_sequence(model, params, time_limit_s_per_week=time_limit_cpsat_per_week_s)
    retries = 0
    cuts_applied: list[dict] = []

    # Gentler ratchet: shave 10% of the team-hours cap per retry on the
    # specific weeks that fell through CP-SAT. Aggressive halving was
    # observed to wreck OTIF (Q1 retry experiment: 100% → 45.6%) because
    # it strangles the MILP across most of the horizon.
    while seq.infeasible_weeks and retries < max_retries:
        factor = max(0.5, 1.0 - 0.1 * (retries + 1))
        for w in seq.infeasible_weeks:
            cuts_applied.append(_add_team_hours_cut(model, params, w, factor))
        milp_result = solve_milp(model, time_limit_milp_s, mip_gap)
        if milp_result.status not in ("optimal", "feasible", "time_limit"):
            break
        seq = solve_sequence(model, params, time_limit_s_per_week=time_limit_cpsat_per_week_s)
        retries += 1

    plan = extract_plan(model, params)
    # Attach the minute-level sequence to the Plan (always present, possibly empty).
    plan.sequence = [
        {
            "machine": iv.machine,
            "week": iv.week,
            "kind": iv.kind,
            "order_id": iv.order_id,
            "fmt_from": iv.fmt_from,
            "fmt_to": iv.fmt_to,
            "start_sec": iv.start_sec,
            "end_sec": iv.end_sec,
            "qty": iv.qty,
        }
        for iv in seq.intervals
    ]

    return plan, RunInfo(
        milp_result=milp_result,
        sequence_result=seq,
        retries=retries,
        cuts_applied=cuts_applied,
    )
