"""CP-SAT solver wrapper — per-week sequencing pass.

Wraps `model.tier3_cpsat.solve_week` over all weeks in a horizon and
collects the per-week schedules. The orchestrator calls this after the
Tier-2 MILP commits an assignment.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pyomo.environ as pyo

from demo_scheduler.data.synthesize import Parameters
from demo_scheduler.model.tier3_cpsat import (
    IntervalSpec,
    WeekSchedule,
    build_week_input,
    solve_week,
)


@dataclass
class SequenceResult:
    schedules: list[WeekSchedule] = field(default_factory=list)
    infeasible_weeks: list[int] = field(default_factory=list)
    total_solve_time_s: float = 0.0

    @property
    def intervals(self) -> list[IntervalSpec]:
        return [iv for ws in self.schedules for iv in ws.intervals]


def _read_milp(
    model: pyo.ConcreteModel, params: Parameters
) -> tuple[
    dict[int, list[tuple[int, str, float]]],
    dict[int, dict[str, tuple[str, str, int]]],
]:
    """Pull (week → packing assignments) and (week → per-machine MILP-committed
    transition) from the solved MILP.

    Transitions are sourced from `co_pack` so CP-SAT only adds intervals
    that MILP actually budgeted into the per-machine capacity. This avoids
    phantom transitions across idle weeks (catalog C-008 semantics).
    """
    eps = 1e-6
    by_week: dict[int, list[tuple[int, str, float]]] = {w: [] for w in params.weeks}
    transitions: dict[int, dict[str, tuple[str, str, int]]] = {w: {} for w in params.weeks}

    machine_formats = getattr(model, "machine_formats", {m: set(params.formats) for m in params.machines})

    for o in params.orders:
        for m in params.eligible[o]:
            for w in params.weeks:
                if (o, m, w) not in model.vol_pack:
                    continue
                v = pyo.value(model.vol_pack[o, m, w])
                if v is None or v <= eps:
                    continue
                by_week[w].append((o, m, float(v)))

    for m in params.machines:
        formats = list(machine_formats.get(m, []))
        for f1 in formats:
            for f2 in formats:
                if f1 == f2:
                    continue
                for w in params.weeks:
                    if (m, f1, f2, w) not in model.co_pack:
                        continue
                    v = pyo.value(model.co_pack[m, f1, f2, w])
                    if v is None or v <= 0.5:
                        continue
                    dur_sec = int(round(params.changeover_h[(m, f1, f2)] * 3600))
                    transitions[w][m] = (f1, f2, dur_sec)

    return by_week, transitions


def solve_sequence(
    model: pyo.ConcreteModel,
    params: Parameters,
    time_limit_s_per_week: float = 10.0,
    headroom_seconds: int = 300,
) -> SequenceResult:
    """Run CP-SAT once per week.

    `headroom_seconds` is a small buffer (default 5 min) added to each
    per-machine envelope to absorb per-interval ceil-rounding when MILP
    fills a cell to exactly its capacity. Without it, ~60-100 ceiling
    roundings can push the cumulative interval length one second past
    the envelope and trip a spurious infeasibility.
    """
    by_week, transitions_by_week = _read_milp(model, params)
    weeks_sorted = sorted(params.weeks)

    result = SequenceResult()
    for w in weeks_sorted:
        wi = build_week_input(
            week=w,
            params=params,
            assignments=by_week[w],
            transitions_seconds=transitions_by_week.get(w, {}),
        )
        # apply headroom
        wi.avail_seconds = {m: v + headroom_seconds for m, v in wi.avail_seconds.items()}
        ws = solve_week(wi, time_limit_s=time_limit_s_per_week)
        result.schedules.append(ws)
        result.total_solve_time_s += ws.solve_time_s
        if ws.status == "infeasible":
            result.infeasible_weeks.append(w)

    return result
