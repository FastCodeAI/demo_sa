"""HiGHS solver wrapper (Pyomo appsi backend)."""
from __future__ import annotations

import time
from dataclasses import dataclass

import pyomo.environ as pyo
from pyomo.contrib.appsi.base import TerminationCondition as AppsiTC
from pyomo.contrib.appsi.solvers.highs import Highs


@dataclass
class SolveResult:
    status: str
    objective: float | None
    solve_time_s: float
    mip_gap: float | None = None


def solve(model: pyo.ConcreteModel, time_limit_s: int = 300, mip_gap: float = 0.01) -> SolveResult:
    s = Highs()
    s.highs_options = {
        "time_limit": float(time_limit_s),
        "mip_rel_gap": float(mip_gap),
        "presolve": "on",
        "parallel": "on",
    }
    t0 = time.time()
    res = s.solve(model)
    elapsed = time.time() - t0

    cond = res.termination_condition
    status_map = {
        AppsiTC.optimal: "optimal",
        AppsiTC.infeasible: "infeasible",
        AppsiTC.unbounded: "unbounded",
        AppsiTC.maxTimeLimit: "time_limit",
        AppsiTC.maxIterations: "iter_limit",
        AppsiTC.interrupted: "interrupted",
        AppsiTC.error: "error",
        AppsiTC.unknown: "unknown",
    }
    status = status_map.get(cond, str(cond))

    obj_val = None
    try:
        obj_val = float(pyo.value(model.obj))
    except Exception:
        obj_val = None

    return SolveResult(
        status=status,
        objective=obj_val,
        solve_time_s=elapsed,
    )
