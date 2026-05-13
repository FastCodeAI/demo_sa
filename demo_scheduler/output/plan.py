"""Serialise the Plan to JSON + CSV."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from demo_scheduler.solve.extract import Plan


def write_plan_json(plan: Plan, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "quarter": plan.quarter,
        "weeks": plan.weeks,
        "months": plan.months,
        "machines": plan.machines,
        "assignments": [asdict(a) for a in plan.assignments],
        "machine_weeks": [asdict(mw) for mw in plan.machine_weeks],
        "changeovers": plan.changeovers,
        "piramal_monthly": plan.piramal_monthly,
        "production": plan.production,
        "sequence": plan.sequence,
    }
    with path.open("w") as f:
        json.dump(data, f, indent=2, default=float)


def write_plan_csv(plan: Plan, path: Path) -> None:
    rows = []
    for a in plan.assignments:
        if not a.placements:
            rows.append({
                "order_id": a.order_id,
                "customer": a.customer,
                "material": a.material,
                "format": a.fmt,
                "qty_demand": a.qty_demand,
                "qty_packed": a.qty_packed,
                "unfilled": a.unfilled,
                "vip": a.is_vip,
                "is_org": a.is_org,
                "score": a.score,
                "machine": None,
                "pack_week": None,
                "qty_at_placement": 0,
            })
        else:
            for p in a.placements:
                rows.append({
                    "order_id": a.order_id,
                    "customer": a.customer,
                    "material": a.material,
                    "format": a.fmt,
                    "qty_demand": a.qty_demand,
                    "qty_packed": a.qty_packed,
                    "unfilled": a.unfilled,
                    "vip": a.is_vip,
                    "is_org": a.is_org,
                    "score": a.score,
                    "machine": p["machine"],
                    "pack_week": p["week"],
                    "qty_at_placement": p["qty"],
                })
    df = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
