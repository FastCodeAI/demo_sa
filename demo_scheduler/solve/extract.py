"""Pull the solved plan out of a Pyomo model."""
from __future__ import annotations

from dataclasses import dataclass, field  # noqa: F401 (field used by Plan)

import pyomo.environ as pyo

from demo_scheduler.data.synthesize import Parameters


@dataclass
class OrderAssignment:
    order_id: int
    customer: str
    material: int
    fmt: str
    qty_demand: float
    qty_packed: float
    unfilled: float
    is_vip: bool
    is_org: bool
    score: float
    # one row per (machine, week) with positive volume
    placements: list[dict] = field(default_factory=list)


@dataclass
class MachineWeek:
    machine: str
    week: int
    active_format: str | None
    pack_hours: float
    changeover_hours: float
    idle_hours: float


@dataclass
class Plan:
    quarter: str
    weeks: list[int]
    months: list[int]
    machines: list[str]
    assignments: list[OrderAssignment]
    machine_weeks: list[MachineWeek]
    changeovers: list[dict]
    piramal_monthly: dict[int, dict]
    production: list[dict]
    # Minute-level intervals from Tier-3 CP-SAT (Phase 2a). Empty when CP-SAT
    # is skipped or every week was sequencing-infeasible.
    sequence: list[dict] = field(default_factory=list)


def extract_plan(model: pyo.ConcreteModel, params: Parameters) -> Plan:
    eps = 1e-6

    assignments: list[OrderAssignment] = []
    for o in params.orders:
        placements: list[dict] = []
        total_packed = 0.0
        for mm in params.eligible[o]:
            for w in params.weeks:
                v = pyo.value(model.vol_pack[o, mm, w])
                if v is None or v <= eps:
                    continue
                placements.append({"machine": mm, "week": int(w), "qty": float(v)})
                total_packed += v
        unfilled = float(pyo.value(model.unfilled[o]) or 0)
        assignments.append(OrderAssignment(
            order_id=int(o),
            customer=params.order_customer[o],
            material=int(params.order_material[o]),
            fmt=params.order_format[o],
            qty_demand=float(params.order_qty[o]),
            qty_packed=total_packed,
            unfilled=unfilled,
            is_vip=bool(params.order_is_vip[o]),
            is_org=o in params.orgs_no_split,
            score=float(params.order_score[o]),
            placements=placements,
        ))

    machine_formats = getattr(model, "machine_formats", {mm: params.formats for mm in params.machines})
    machine_weeks: list[MachineWeek] = []
    changeovers: list[dict] = []
    for mm in params.machines:
        formats_here = list(machine_formats.get(mm, []))
        for w in params.weeks:
            active_f: str | None = None
            for f in formats_here:
                v = pyo.value(model.y_pack[mm, f, w])
                if v and v > 0.5:
                    active_f = f
                    break
            pack_hours = sum(
                (pyo.value(model.vol_pack[o, mm, w]) or 0.0)
                / params.throughput[(mm, params.order_format[o])]
                for o in params.orders
                if mm in params.eligible[o]
                and (mm, params.order_format[o]) in params.throughput
            )
            co_hours = sum(
                (pyo.value(model.co_pack[mm, f1, f2, w]) or 0.0) * params.changeover_h[(mm, f1, f2)]
                for f1 in formats_here for f2 in formats_here if f1 != f2
            )
            idle = float(pyo.value(model.idle_hours[mm, w]) or 0)
            machine_weeks.append(MachineWeek(
                machine=mm, week=int(w),
                active_format=active_f,
                pack_hours=float(pack_hours),
                changeover_hours=float(co_hours),
                idle_hours=idle,
            ))
            for f1 in formats_here:
                for f2 in formats_here:
                    if f1 == f2:
                        continue
                    v = pyo.value(model.co_pack[mm, f1, f2, w]) or 0.0
                    if v > 0.5:
                        changeovers.append({
                            "machine": mm, "week": int(w),
                            "from": f1, "to": f2,
                            "hours": float(params.changeover_h[(mm, f1, f2)]),
                        })

    from demo_scheduler.data.synthesize import week_to_month
    piramal_monthly: dict[int, dict] = {}
    for t in params.months:
        weeks_in_t = [w for w in params.weeks if week_to_month(w, params) == t]
        total = sum(
            (pyo.value(model.vol_pack[o, mm, w]) or 0.0)
            for o in params.piramal_orders
            for mm in params.eligible[o]
            for w in weeks_in_t
        )
        under = float(pyo.value(model.piramal_under[t]) or 0)
        over = float(pyo.value(model.piramal_over[t]) or 0)
        piramal_monthly[int(t)] = {
            "month": int(t),
            "volume": float(total),
            "band_min": params.piramal_band_min,
            "band_max": params.piramal_band_max,
            "under_band": under,
            "over_band": over,
        }

    production: list[dict] = []
    for s in params.materials:
        for w in params.weeks:
            qty = float(pyo.value(model.qty_prod[s, w]) or 0)
            if qty > eps:
                production.append({"material": int(s), "week": int(w), "qty": qty})

    return Plan(
        quarter=params.quarter,
        weeks=list(params.weeks),
        months=list(params.months),
        machines=list(params.machines),
        assignments=assignments,
        machine_weeks=machine_weeks,
        changeovers=changeovers,
        piramal_monthly=piramal_monthly,
        production=production,
    )
