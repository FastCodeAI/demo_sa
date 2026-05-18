"""Run the full pipeline across Q1→Q4, carrying unfilled orders forward.

For each quarter:
  1. Load Q_n demand (Q1 includes BO 2024).
  2. Inject carry-forward demand from Q_{n-1} unfilled: bumps an
     existing matching row, or appends a synthetic row using the
     unfilled order's format/material profile.
  3. Solve MILP + CP-SAT via the orchestrator (catalog-built model).
  4. Record OTIF + unfilled volume, propagate to the next quarter.

Outputs a single JSON summary at /tmp/quarters_summary.json.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from demo_scheduler.config.load import load_config  # noqa: E402
from demo_scheduler.data.load_excel import (  # noqa: E402
    BO_COL,
    QUARTER_VOL_COLS,
    load_orders,
)
from demo_scheduler.data.synthesize import synthesize  # noqa: E402
from demo_scheduler.output.kpis import compute_kpis  # noqa: E402
from demo_scheduler.output.plan import write_plan_json  # noqa: E402
from demo_scheduler.solve.orchestrator import solve_orchestrated  # noqa: E402
from demo_scheduler.solve.solver import SolveResult  # noqa: E402

CATALOG = PROJECT_ROOT / "catalog"


def inject_carry_forward(raw, quarter: str, carry: dict[tuple[int, str], float]):
    """Add `carry` qty into the Q_n volume column.

    carry key: (material_number, country).
    If a row already exists for that pair, bump its quarter qty.
    Otherwise synthesise a new row from the full demand sheet
    (taking the row's format/material profile).
    """
    if not carry:
        return raw
    vol_col = QUARTER_VOL_COLS[quarter]
    orders = raw.orders.copy()
    full = raw.full_demand
    appended_rows = []

    for (material, country), qty in carry.items():
        mask = (orders["Material Number"] == material) & (orders["Country"] == country)
        if mask.any():
            orders.loc[mask, vol_col] = orders.loc[mask, vol_col].fillna(0).astype(float) + qty
            continue
        # Find any reference row in the full sheet for this material
        ref_mask = (full["Material Number"] == material) & (full["Country"] == country)
        if not ref_mask.any():
            ref_mask = full["Material Number"] == material
        if not ref_mask.any():
            print(f"  WARN: cannot place carry ({material}, {country}) — no ref row")
            continue
        row = full[ref_mask].iloc[0].copy()
        row["Country"] = country
        for q_col in QUARTER_VOL_COLS.values():
            row[q_col] = qty if q_col == vol_col else 0
        row[BO_COL] = 0
        appended_rows.append(row)

    if appended_rows:
        orders = pd.concat([orders, pd.DataFrame(appended_rows)], ignore_index=True)
    orders.index.name = "order_id"
    raw.orders = orders
    return raw


def run_quarter(quarter: str, carry: dict[tuple[int, str], float]):
    cfg = load_config(None)
    cfg.horizon.quarter = quarter
    cfg.horizon.include_backorder = (quarter == "Q1")
    cfg.solver.time_limit_seconds = 600

    print(f"\n=== {quarter} ===")
    raw = load_orders(quarter=quarter, include_backorder=cfg.horizon.include_backorder)
    print(f"  loaded {len(raw.orders)} rows before carry-forward")

    raw = inject_carry_forward(raw, quarter, carry)
    print(f"  {len(raw.orders)} rows after carry-forward (+{sum(carry.values()):,.0f} units carried)")

    params = synthesize(raw, cfg)
    print(f"  |O|={len(params.orders)}  |S|={len(params.materials)}  weeks={len(params.weeks)}")

    t0 = time.time()
    plan, info = solve_orchestrated(
        params,
        time_limit_milp_s=cfg.solver.time_limit_seconds,
        mip_gap=cfg.solver.mip_gap,
        time_limit_cpsat_per_week_s=10.0,
        max_retries=2,
        catalog_root=CATALOG,
    )
    wall = time.time() - t0

    composite = SolveResult(
        status=info.milp_result.status,
        objective=info.milp_result.objective,
        solve_time_s=info.solve_time_s,
    )
    kpis = compute_kpis(plan, composite)

    out_dir = PROJECT_ROOT / "outputs" / f"{quarter.lower()}_chain"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_plan_json(plan, out_dir / "plan.json")
    (out_dir / "kpis.json").write_text(json.dumps(kpis, indent=2, default=str))

    next_carry: dict[tuple[int, str], float] = defaultdict(float)
    for a in plan.assignments:
        if (a.unfilled or 0) > 0:
            next_carry[(a.material, a.customer)] += a.unfilled

    total_demand = sum(a.qty_demand for a in plan.assignments)
    total_packed = sum(a.qty_packed for a in plan.assignments)
    total_unfilled = sum(a.unfilled for a in plan.assignments)
    otif = (total_packed / total_demand) * 100 if total_demand else 0.0

    print(f"  status={info.milp_result.status}  OTIF={otif:.2f}%  "
          f"demand={total_demand:,.0f}  packed={total_packed:,.0f}  unfilled={total_unfilled:,.0f}  "
          f"wall={wall:.0f}s")

    return {
        "quarter": quarter,
        "status": info.milp_result.status,
        "n_orders": len(plan.assignments),
        "total_demand": total_demand,
        "total_packed": total_packed,
        "total_unfilled": total_unfilled,
        "otif_pct": round(otif, 2),
        "rated_otif_pct": kpis.get("rated_otif_pct"),
        "objective": info.milp_result.objective,
        "wall_time_s": round(wall, 1),
        "solve_time_s": info.solve_time_s,
        "carry_to_next": sum(next_carry.values()),
        "carry_to_next_orders": len(next_carry),
    }, next_carry


def main() -> None:
    summary = []
    carry: dict[tuple[int, str], float] = {}
    for q in ("Q1", "Q2", "Q3", "Q4"):
        result, carry = run_quarter(q, carry)
        summary.append(result)

    out_path = Path("/tmp/quarters_summary.json")
    out_path.write_text(json.dumps(summary, indent=2, default=str))
    print("\n=== SUMMARY ===")
    for s in summary:
        print(
            f"  {s['quarter']}: OTIF={s['otif_pct']:.2f}%   "
            f"demand={s['total_demand']:>13,.0f}   "
            f"unfilled={s['total_unfilled']:>10,.0f}   "
            f"wall={s['wall_time_s']:.0f}s"
        )
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
