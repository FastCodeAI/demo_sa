"""Compute KPIs from a Plan."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from demo_scheduler.solve.extract import Plan
from demo_scheduler.solve.solver import SolveResult


def compute_kpis(plan: Plan, solve_result: SolveResult) -> dict:
    total_demand = sum(a.qty_demand for a in plan.assignments)
    total_packed = sum(a.qty_packed for a in plan.assignments)
    total_unfilled = sum(a.unfilled for a in plan.assignments)
    otif_pct = (total_packed / total_demand * 100.0) if total_demand else 0.0

    rated_total = sum(a.score * a.qty_demand for a in plan.assignments)
    rated_packed = sum(a.score * a.qty_packed for a in plan.assignments)
    rated_otif_pct = (rated_packed / rated_total * 100.0) if rated_total else 0.0

    vip_assignments = [a for a in plan.assignments if a.is_vip]
    vip_demand = sum(a.qty_demand for a in vip_assignments)
    vip_packed = sum(a.qty_packed for a in vip_assignments)
    vip_otif = (vip_packed / vip_demand * 100.0) if vip_demand else 0.0

    total_changeover_h = sum(
        c["hours"] for c in plan.changeovers
    )
    total_idle_h = sum(mw.idle_hours for mw in plan.machine_weeks)
    total_pack_h = sum(mw.pack_hours for mw in plan.machine_weeks)

    util_by_machine: dict[str, dict] = {}
    avail_by_machine: dict[str, float] = defaultdict(float)
    pack_by_machine: dict[str, float] = defaultdict(float)
    co_by_machine: dict[str, float] = defaultdict(float)
    idle_by_machine: dict[str, float] = defaultdict(float)
    for mw in plan.machine_weeks:
        avail_by_machine[mw.machine] += mw.pack_hours + mw.changeover_hours + mw.idle_hours
        pack_by_machine[mw.machine] += mw.pack_hours
        co_by_machine[mw.machine] += mw.changeover_hours
        idle_by_machine[mw.machine] += mw.idle_hours
    for mm in plan.machines:
        avail = avail_by_machine[mm]
        util_by_machine[mm] = {
            "pack_h": pack_by_machine[mm],
            "changeover_h": co_by_machine[mm],
            "idle_h": idle_by_machine[mm],
            "available_h": avail,
            "utilisation_pct": (pack_by_machine[mm] / avail * 100.0) if avail else 0.0,
        }

    unfilled_by_customer: dict[str, float] = defaultdict(float)
    for a in plan.assignments:
        if a.unfilled > 0:
            unfilled_by_customer[a.customer] += a.unfilled

    return {
        "status": solve_result.status,
        "objective": solve_result.objective,
        "solve_time_s": solve_result.solve_time_s,
        "totals": {
            "demand": total_demand,
            "packed": total_packed,
            "unfilled": total_unfilled,
        },
        "otif_pct": otif_pct,
        "rated_otif_pct": rated_otif_pct,
        "vip_otif_pct": vip_otif,
        "total_changeover_h": total_changeover_h,
        "total_idle_h": total_idle_h,
        "total_pack_h": total_pack_h,
        "piramal_monthly": plan.piramal_monthly,
        "unfilled_by_customer": dict(sorted(unfilled_by_customer.items(), key=lambda kv: -kv[1])[:20]),
        "capacity_utilisation_by_machine": util_by_machine,
        "n_changeovers": len(plan.changeovers),
    }


def write_kpis(kpis: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(kpis, f, indent=2, default=float)


def format_kpis_text(kpis: dict) -> str:
    t = kpis["totals"]
    lines = [
        f"Status:           {kpis['status']}",
        f"Objective:        {kpis['objective']:.1f}" if kpis["objective"] is not None else "Objective:        n/a",
        f"Solve time:       {kpis['solve_time_s']:.1f} s",
        "",
        f"Demand:           {t['demand']:>15,.0f}",
        f"Packed:           {t['packed']:>15,.0f}",
        f"Unfilled:         {t['unfilled']:>15,.0f}",
        f"OTIF:             {kpis['otif_pct']:>14.1f} %",
        f"Rated OTIF:       {kpis['rated_otif_pct']:>14.1f} %",
        f"VIP OTIF:         {kpis['vip_otif_pct']:>14.1f} %",
        "",
        f"Changeover hours: {kpis['total_changeover_h']:>14.1f}",
        f"Idle hours:       {kpis['total_idle_h']:>14.1f}",
        f"Pack hours:       {kpis['total_pack_h']:>14.1f}",
        f"# changeovers:    {kpis['n_changeovers']:>14}",
        "",
        "Capacity utilisation by machine:",
    ]
    for mm, u in kpis["capacity_utilisation_by_machine"].items():
        lines.append(f"  {mm:<14}  util={u['utilisation_pct']:5.1f}%  pack={u['pack_h']:7.1f}h  co={u['changeover_h']:6.1f}h  idle={u['idle_h']:7.1f}h")
    lines.append("")
    lines.append("Piramal monthly band (req #37):")
    for month, info in sorted(kpis["piramal_monthly"].items(), key=lambda kv: int(kv[0])):
        verdict = "OK"
        if info["under_band"] > 0.5:
            verdict = f"UNDER by {info['under_band']:,.0f}"
        elif info["over_band"] > 0.5:
            verdict = f"OVER by {info['over_band']:,.0f}"
        lines.append(f"  M{int(month):02d}  vol={info['volume']:>12,.0f}  band=[{info['band_min']:,.0f}, {info['band_max']:,.0f}]  {verdict}")
    if kpis["unfilled_by_customer"]:
        lines.append("")
        lines.append("Top unfilled by customer:")
        for c, v in list(kpis["unfilled_by_customer"].items())[:10]:
            lines.append(f"  {c:<35}  {v:>12,.0f}")
    return "\n".join(lines)
