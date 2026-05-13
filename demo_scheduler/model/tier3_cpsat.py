"""Tier-3 CP-SAT sequencing model — second-granularity per (machine, week).

Reads the Tier-2 MILP assignment (x_pack, y_pack, vol_pack, co_pack) and
produces a shift-level schedule that respects:

  * no_overlap per machine (orders + format-change transition)
  * no_overlap globally on the changeover team (transitions across all
    machines compete for one operator team — req #36, catalog C-008)
  * weekly capacity (each interval ends within the shift envelope)

Solves *per week*. The MILP guarantees one format per machine-week (catalog
C-001), so within a week each machine has at most one transition at the
start. We rely on MILP's `co_pack` to know *whether* a transition is
needed — if MILP did not budget a changeover, neither do we (the machine
may have been idle the previous week, in which case MILP-style accounting
considers the changeover free).

Time is modelled in **seconds** so per-interval ceil-rounding error stays
within tolerance — MILP often fills cells to exactly 120 h = 432_000 s,
which leaves no headroom for 60×-coarser minute rounding.

If a week is infeasible, the orchestrator can tighten the MILP team-hours
cap and retry.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from demo_scheduler.data.synthesize import Parameters


@dataclass
class IntervalSpec:
    """One packing or changeover interval emitted by CP-SAT.

    `start_sec` / `end_sec` are seconds since the start of the week (Mon 00:00).
    Downstream consumers (Gantt, JSON, KPIs) convert to minutes/hours as needed.
    """
    machine: str
    week: int
    kind: str           # "pack" or "changeover"
    order_id: int | None
    fmt_from: str | None
    fmt_to: str | None
    start_sec: int
    end_sec: int
    qty: float


@dataclass
class WeekSchedule:
    """CP-SAT output for one week."""
    week: int
    status: str                       # optimal | feasible | infeasible | unknown
    solve_time_s: float
    intervals: list[IntervalSpec]
    objective: int | None = None


@dataclass
class WeekInput:
    """Tier-2 slice for a single week, ready to feed CP-SAT.

    `transitions[m] = (f1, f2, duration_seconds)` for each machine where
    MILP committed to a changeover at the start of this week. CP-SAT only
    adds transition intervals for machines listed here, mirroring exactly
    what MILP budgeted into the per-machine capacity.
    """
    week: int
    avail_seconds: dict[str, int]
    assignments_by_machine: dict[str, list[tuple[int, float, str, float]]]
    transitions: dict[str, tuple[str, str, int]] = field(default_factory=dict)


def build_week_input(
    week: int,
    params: Parameters,
    assignments: list[tuple[int, str, float]],
    transitions_seconds: dict[str, tuple[str, str, int]],
) -> WeekInput:
    """Translate MILP slice into the CP-SAT-friendly per-machine view.

    `transitions_seconds[m] = (f1, f2, duration_seconds)` for each machine
    where MILP committed to a changeover this week.
    """
    by_machine: dict[str, list[tuple[int, float, str, float]]] = {m: [] for m in params.machines}
    for (o, m, qty) in assignments:
        f = params.order_format[o]
        th = params.throughput.get((m, f), 0)
        if th <= 0 or qty <= 0:
            continue
        by_machine.setdefault(m, []).append((o, float(qty), f, float(th)))

    avail_seconds = {
        m: int(round(params.avail_hours[(m, week)] * 3600))
        for m in params.machines
    }

    return WeekInput(
        week=week,
        avail_seconds=avail_seconds,
        assignments_by_machine=by_machine,
        transitions=transitions_seconds,
    )


def solve_week(wi: WeekInput, time_limit_s: float = 10.0) -> WeekSchedule:
    """Build + solve the per-week CP-SAT model in seconds.

    Returns intervals with `start_min`/`end_min` reported in minutes for
    downstream convenience (the internal model is second-precision).
    """
    import time

    model = cp_model.CpModel()
    avail_seconds = wi.avail_seconds
    horizon = max(avail_seconds.values()) if avail_seconds else 0
    if horizon <= 0:
        return WeekSchedule(week=wi.week, status="optimal", solve_time_s=0.0, intervals=[])

    machine_intervals: dict[str, list[tuple[str, cp_model.IntervalVar, dict]]] = {}
    transition_intervals: list[cp_model.IntervalVar] = []
    pack_ends: list[cp_model.IntVar] = []

    for m, packs in wi.assignments_by_machine.items():
        if not packs:
            machine_intervals[m] = []
            continue

        avail = avail_seconds.get(m, 0)
        if avail <= 0:
            return WeekSchedule(week=wi.week, status="infeasible", solve_time_s=0.0, intervals=[])

        machine_intervals[m] = []

        # Changeover at start of week, if MILP committed to one.
        trans = wi.transitions.get(m)
        if trans:
            prev_f, cur_f, dur_sec = trans
            if dur_sec > 0:
                start = model.new_int_var(0, max(0, avail - dur_sec), f"co_start_{m}_{wi.week}")
                end = model.new_int_var(0, avail, f"co_end_{m}_{wi.week}")
                iv = model.new_interval_var(start, dur_sec, end, f"co_{m}_{wi.week}")
                machine_intervals[m].append(("changeover", iv, {
                    "fmt_from": prev_f, "fmt_to": cur_f, "duration": dur_sec,
                }))
                transition_intervals.append(iv)

        for (o, qty, f, th) in packs:
            # qty / throughput(units/h) = hours, * 3600 = seconds
            dur_sec = max(1, int(math.ceil(qty / th * 3600.0)))
            start = model.new_int_var(0, max(0, avail - dur_sec), f"pk_start_{o}_{m}_{wi.week}")
            end = model.new_int_var(0, avail, f"pk_end_{o}_{m}_{wi.week}")
            iv = model.new_interval_var(start, dur_sec, end, f"pk_{o}_{m}_{wi.week}")
            machine_intervals[m].append(("pack", iv, {
                "order_id": o, "qty": qty, "fmt": f, "duration": dur_sec,
            }))
            pack_ends.append(end)

        ivs = [iv for (_kind, iv, _meta) in machine_intervals[m]]
        if len(ivs) > 1:
            model.add_no_overlap(ivs)

    if len(transition_intervals) > 1:
        model.add_no_overlap(transition_intervals)

    if pack_ends:
        model.minimize(sum(pack_ends))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = 4
    t0 = time.time()
    status = solver.solve(model)
    elapsed = time.time() - t0

    status_str = {
        cp_model.OPTIMAL: "optimal",
        cp_model.FEASIBLE: "feasible",
        cp_model.INFEASIBLE: "infeasible",
        cp_model.MODEL_INVALID: "error",
        cp_model.UNKNOWN: "unknown",
    }.get(status, "unknown")

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return WeekSchedule(
            week=wi.week, status=status_str, solve_time_s=elapsed, intervals=[],
        )

    out: list[IntervalSpec] = []
    for m, ivs in machine_intervals.items():
        for kind, iv, meta in ivs:
            s_sec = int(solver.value(iv.start_expr()))
            e_sec = int(solver.value(iv.end_expr()))
            if kind == "changeover":
                out.append(IntervalSpec(
                    machine=m, week=wi.week, kind="changeover",
                    order_id=None,
                    fmt_from=meta["fmt_from"], fmt_to=meta["fmt_to"],
                    start_sec=s_sec, end_sec=e_sec, qty=0.0,
                ))
            else:
                out.append(IntervalSpec(
                    machine=m, week=wi.week, kind="pack",
                    order_id=meta["order_id"],
                    fmt_from=None, fmt_to=meta["fmt"],
                    start_sec=s_sec, end_sec=e_sec, qty=meta["qty"],
                ))
    out.sort(key=lambda x: (x.machine, x.start_sec))

    obj_val = int(solver.objective_value) if pack_ends else None
    return WeekSchedule(
        week=wi.week, status=status_str, solve_time_s=elapsed,
        intervals=out, objective=obj_val,
    )
