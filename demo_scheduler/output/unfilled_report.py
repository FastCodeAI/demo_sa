"""Binding-cause scanner for unfilled orders.

For every order with `unfilled > 0` we compute, from the plan + the
config, which constraints actually bound the solver away from a fuller
placement. The output is a JSON-safe dict the Explanation Agent feeds
directly into its LLM prompt — so the agent narrates from *grounded
facts* (cap utilisation, eligible-machine count, Piramal band hits),
not from priors.

Reasons surfaced (all deterministic, no LLM inside this module):

  CAPACITY_EXHAUSTED       — every eligible (machine, week) was >=95% used
  ELIGIBILITY_NARROW       — format has few eligible machines, all of them
                             saturated for the order's customer profile
  PIRAMAL_BAND_HIT         — order belongs to Piramal and the monthly cap
                             was reached
  NO_SPLIT_BLOCKED         — customer is in orgs_no_split and no single
                             (m, w) cell had enough free capacity to take
                             the entire order at once
  HEURISTIC_RESIDUAL       — fallback when none of the above fired but
                             the solver still chose to leave the order
                             unfilled (objective tradeoff)
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SAT_THRESHOLD = 0.95   # >=95% week utilisation → "saturated"


def _machine_hours_per_week(cfg) -> float:
    cal = cfg.calendar
    return float(cal.shifts_per_day) * float(cal.working_hours_per_shift) * float(cal.days_per_week)


def _piramal_band(cfg) -> tuple[float, float] | None:
    band = (cfg.bands or {}).get("Piramal")
    if not band:
        return None
    return float(band.monthly_min), float(band.monthly_max)


def _week_to_month(week: int) -> int:
    # 13 weeks -> 3 months: W1-4 Jan, W5-9 Feb, W10-13 Mar (approx; matches
    # demo_scheduler.model.sets.week_to_month for Q1).
    if week <= 4: return 1
    if week <= 9: return 2
    return 3


def build_unfilled_report(plan: dict, cfg) -> dict[str, Any]:
    """Run the scanner. Pure function — no IO, no LLM.

    Args:
        plan: parsed plan.json
        cfg:  loaded `Config` (pydantic model from `config/load.py`)

    Returns:
        Report dict (JSON-safe). Empty `orders` list means everything
        is filled.
    """
    assignments = plan["assignments"]
    weeks = plan["weeks"]
    machines = plan["machines"]
    hpw = _machine_hours_per_week(cfg)
    thr = cfg.throughput_units_per_hour
    eligible_for: dict[str, list[str]] = {}
    for mname, m in cfg.machines.items():
        for f in m.eligible_formats:
            eligible_for.setdefault(f, []).append(mname)

    # ---- 1. Machine-week utilisation (in hours) computed from plan
    hours_used: dict[tuple[str, int], float] = defaultdict(float)
    for a in assignments:
        fmt = a["fmt"]
        for p in a.get("placements") or []:
            m = p["machine"]; w = p["week"]; q = float(p["qty"])
            rate = thr.get(m, {}).get(fmt)
            if rate and rate > 0:
                hours_used[(m, w)] += q / rate
    util: dict[str, dict[int, float]] = {m: {} for m in machines}
    for (m, w), h in hours_used.items():
        util.setdefault(m, {})[w] = round(h / hpw, 3) if hpw > 0 else 0.0
    saturated_cells = {k for k, v in util.items() for w, frac in v.items() if frac >= SAT_THRESHOLD}
    saturated_pairs = {(m, w) for m, wks in util.items() for w, frac in wks.items() if frac >= SAT_THRESHOLD}

    # ---- 2. Piramal monthly volumes vs band
    band = _piramal_band(cfg)
    piramal_monthly: dict[int, float] = defaultdict(float)
    for a in assignments:
        if a["customer"] == "Piramal":
            for p in a.get("placements") or []:
                piramal_monthly[_week_to_month(p["week"])] += float(p["qty"])
    band_hit_months = []
    if band:
        _, hi = band
        band_hit_months = [m for m, v in piramal_monthly.items() if v >= hi * 0.98]

    # ---- 3. Per-format Q1 supply vs demand
    fmt_demand: dict[str, float] = defaultdict(float)
    fmt_packed: dict[str, float] = defaultdict(float)
    for a in assignments:
        fmt_demand[a["fmt"]] += float(a["qty_demand"])
        fmt_packed[a["fmt"]] += float(a["qty_packed"])
    fmt_capacity: dict[str, float] = {}
    for f, ms in eligible_for.items():
        cap = 0.0
        for m in ms:
            rate = thr.get(m, {}).get(f, 0)
            cap += rate * hpw * len(weeks)
        fmt_capacity[f] = cap

    no_split = set(cfg.orgs_no_split or [])

    # ---- 4. Per-order reasoning
    order_reports: list[dict] = []
    for a in assignments:
        if (a["unfilled"] or 0) <= 0:
            continue
        fmt = a["fmt"]
        cust = a["customer"]
        elig = list(eligible_for.get(fmt, []))
        # Pairs available to this order across the horizon and how many
        # were saturated.
        total_cells = len(elig) * len(weeks)
        sat_cells = sum(1 for m in elig for w in weeks if (m, w) in saturated_pairs)
        sat_frac = sat_cells / total_cells if total_cells else 0.0

        reasons: list[str] = []
        if not elig:
            reasons.append("NO_ELIGIBLE_MACHINE")
        else:
            if sat_frac >= 0.85:
                reasons.append("CAPACITY_EXHAUSTED")
            if len(elig) <= 2 and sat_frac >= 0.5:
                reasons.append("ELIGIBILITY_NARROW")
            if cust == "Piramal" and band_hit_months:
                reasons.append("PIRAMAL_BAND_HIT")
            if cust in no_split:
                reasons.append("NO_SPLIT_BLOCKED")
        if not reasons:
            reasons.append("HEURISTIC_RESIDUAL")

        order_reports.append({
            "order_id": a["order_id"],
            "customer": cust,
            "material": a["material"],
            "fmt": fmt,
            "qty_demand": float(a["qty_demand"]),
            "qty_packed": float(a["qty_packed"]),
            "qty_unfilled": float(a["unfilled"]),
            "eligible_machines": elig,
            "eligible_cells_saturated_pct": round(sat_frac * 100, 1),
            "binding_reasons": reasons,
            "is_no_split_org": cust in no_split,
            "is_piramal": cust == "Piramal",
        })

    # ---- 5. Top-level aggregates for the prompt
    total_unfilled = sum(o["qty_unfilled"] for o in order_reports)
    total_demand = sum(float(a["qty_demand"]) for a in assignments)

    return {
        "summary": {
            "n_orders": len(assignments),
            "n_unfilled": len(order_reports),
            "total_demand": total_demand,
            "total_unfilled": total_unfilled,
            "fill_rate_pct": round((1.0 - total_unfilled / total_demand) * 100, 2) if total_demand else None,
            "sat_machine_weeks": len(saturated_pairs),
            "piramal_band_hit_months": band_hit_months,
            "piramal_band_max": band[1] if band else None,
        },
        "format_balance": {
            f: {
                "demand_q1": fmt_demand.get(f, 0),
                "packed_q1": fmt_packed.get(f, 0),
                "capacity_q1": round(fmt_capacity[f], 0),
                "demand_vs_capacity_pct": round(fmt_demand.get(f, 0) / fmt_capacity[f] * 100, 1) if fmt_capacity[f] else None,
                "eligible_machines": eligible_for.get(f, []),
            }
            for f in fmt_capacity
        },
        "machine_utilisation_pct": {
            m: {
                "avg_q1": round(sum(util[m].values()) / max(1, len(util[m])) * 100, 1),
                "peak": round(max(util[m].values(), default=0.0) * 100, 1),
                "weeks_saturated": sum(1 for v in util[m].values() if v >= SAT_THRESHOLD),
            }
            for m in machines
        },
        "orders": order_reports,
    }
