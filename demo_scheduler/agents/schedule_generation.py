"""Schedule-Generation Agent — catalog → solver.

Doesn't talk to the LLM. Given a (params, catalog_root) pair, fires the
Tier-2 + Tier-3 orchestrator and returns the Plan + RunInfo.

Kept as an agent (rather than a function call) so the Supervisor can
route a "run scheduler" intent through it uniformly with the other six.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from demo_scheduler.data.synthesize import Parameters
from demo_scheduler.solve.extract import Plan
from demo_scheduler.solve.orchestrator import RunInfo, solve_orchestrated


@dataclass
class ScheduleResult:
    plan: Plan
    info: RunInfo


def generate_schedule(
    params: Parameters,
    catalog_root: Path | None,
    time_limit_milp_s: int = 600,
    time_limit_cpsat_per_week_s: float = 10.0,
) -> ScheduleResult:
    plan, info = solve_orchestrated(
        params,
        time_limit_milp_s=time_limit_milp_s,
        mip_gap=0.05,
        time_limit_cpsat_per_week_s=time_limit_cpsat_per_week_s,
        max_retries=2,
        catalog_root=catalog_root,
    )
    return ScheduleResult(plan=plan, info=info)
