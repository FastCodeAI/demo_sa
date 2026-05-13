"""Build the parameter tables the MILP consumes, blending Excel + config.

For each missing input (gap log in MODEL_SCOPING.md §9), this module produces
a deterministic placeholder so the optimiser can run end-to-end. Replace the
placeholder by editing configs/defaults.yaml (or passing --config).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from demo_scheduler.config.schema import Config
from demo_scheduler.data.canonicalize import (
    FORMAT_VOLUME_ML,
    add_canonical_columns,
    is_org_no_split,
    is_piramal,
)
from demo_scheduler.data.load_excel import (
    BO_COL,
    QUARTER_TO_MONTHS,
    QUARTER_VOL_COLS,
    TOTAL_POINTS_COL,
    RawData,
)


@dataclass
class Parameters:
    # Index sets
    orders: list[int]                 # order ids
    materials: list[int]              # unique material numbers (semi-fin proxy)
    customers: list[str]
    machines: list[str]
    formats: list[str]
    weeks: list[int]
    months: list[int]
    apis: list[int]                   # placeholder: 1 API per material (no BoM)

    # Order-level data
    order_qty: dict[int, float]                # total_qty[o]
    order_format: dict[int, str]               # format[o]
    order_material: dict[int, int]             # semi-fin / material[o]
    order_customer: dict[int, str]
    order_score: dict[int, float]              # rating_score[o]
    order_is_vip: dict[int, bool]
    order_is_org: dict[int, bool]
    order_due_week: dict[int, int]             # latest acceptable pack week
    order_pref_machine: dict[int, str | None]  # manual choice from Excel

    # Capacity & eligibility
    throughput: dict[tuple[str, str], float]   # units/hour per (machine, format)
    avail_hours: dict[tuple[str, str], float]  # hours/week per (machine, week)
    eligible: dict[int, list[str]]             # eligible machines per order
    changeover_h: dict[tuple[str, str, str], float]   # (machine, f1, f2)

    # Customer bands
    piramal_orders: list[int]
    piramal_band_min: float
    piramal_band_max: float
    orgs_no_split: list[int]                  # order ids that may not be split

    # Production
    shelf_life_weeks: dict[int, int]
    scrap_rate: dict[int, float]
    evaton_skus: list[int]
    evaton_min_gap_weeks: int

    # Objective weights
    w_fulfilment: float
    w_changeover: float
    w_idle: float
    w_tie_split: float
    w_late: float

    # Lags
    prod_to_pack_min_weeks: int                # converted from days for weekly model

    # Shared changeover-team capacity (hours/week available to the operator team)
    team_hours_per_week: float

    # Metadata
    quarter: str
    horizon_weeks: int
    backorder_included: bool


def _months_for_quarter(q: str) -> list[int]:
    return list(QUARTER_TO_MONTHS[q])


def _weeks_for_quarter(q: str) -> list[int]:
    # Q1 = wk 1..13, Q2 = wk 14..26, etc.
    base = {"Q1": 1, "Q2": 14, "Q3": 27, "Q4": 40}[q]
    return list(range(base, base + 13))


def _week_to_month(w: int, q: str) -> int:
    """Map an absolute week (1-52) to a calendar month within the quarter."""
    weeks = _weeks_for_quarter(q)
    months = _months_for_quarter(q)
    # First ~4-5 wks = first month, next 4-5 = second, last 4-5 = third
    idx = weeks.index(w)
    if idx < 4:
        return months[0]
    if idx < 9:
        return months[1]
    return months[2]


def _changeover_hours(cfg: Config, machine: str, f1: str, f2: str) -> float:
    if f1 == f2:
        return cfg.changeover_hours.default.same_format
    overrides = cfg.changeover_hours.per_machine_overrides.get(machine, {})
    v1 = FORMAT_VOLUME_ML.get(f1)
    v2 = FORMAT_VOLUME_ML.get(f2)
    if v1 is None or v2 is None:
        return overrides.get("major_volume_change", cfg.changeover_hours.default.major_volume_change)
    # ladder distance — neighbouring volumes = minor
    ladder = sorted(FORMAT_VOLUME_ML.values())
    i1, i2 = ladder.index(v1), ladder.index(v2)
    if abs(i1 - i2) == 1:
        return overrides.get("minor_volume_change", cfg.changeover_hours.default.minor_volume_change)
    return overrides.get("major_volume_change", cfg.changeover_hours.default.major_volume_change)


def _machine_eligible_for_order(
    cfg: Config, machine: str, fmt: str, vol: float
) -> bool:
    m = cfg.machines.get(machine)
    if m is None:
        return False
    if fmt not in m.eligible_formats:
        return False
    th = cfg.glass_volume_thresholds.get(machine)
    if th is None:
        return True
    if th.min_vol is not None and vol < th.min_vol:
        return False
    if th.max_vol is not None and vol > th.max_vol:
        return False
    return True


def synthesize(raw: RawData, cfg: Config) -> Parameters:
    quarter = cfg.horizon.quarter
    weeks = _weeks_for_quarter(quarter)
    months = _months_for_quarter(quarter)

    orders_df = add_canonical_columns(raw.orders)
    vol_col = QUARTER_VOL_COLS[quarter]

    # Total qty per order: quarter demand + (optional) BO carry for Q1.
    qty = orders_df[vol_col].fillna(0).astype(float).copy()
    if cfg.horizon.include_backorder and quarter == "Q1":
        qty = qty + orders_df[BO_COL].fillna(0).astype(float)
    orders_df["_qty"] = qty
    # Drop rows with zero demand after the combination, missing material, or missing format
    orders_df = orders_df[
        (orders_df["_qty"] > 0)
        & orders_df["Material Number"].notna()
        & orders_df["format"].notna()
    ].reset_index(drop=True)
    orders_df["Material Number"] = orders_df["Material Number"].astype(int)
    orders_df.index.name = "order_id"

    order_ids = list(orders_df.index)
    order_qty = orders_df["_qty"].to_dict()
    order_format = orders_df["format"].to_dict()
    order_material = orders_df["Material Number"].astype(int).to_dict()
    order_customer = orders_df["customer"].to_dict()
    order_score = orders_df[TOTAL_POINTS_COL].fillna(0).astype(float).to_dict()
    order_is_vip = (orders_df["VIP"].fillna(0) > 0).to_dict()
    order_is_org = {
        o: is_org_no_split(c, cfg.orgs_no_split) for o, c in order_customer.items()
    }
    order_pref_machine = orders_df["machine_pref"].to_dict()
    last_week = weeks[-1]
    order_due_week = {o: last_week for o in order_ids}

    machines = list(cfg.machines.keys())
    formats = sorted({order_format[o] for o in order_ids if order_format[o]},
                     key=lambda f: FORMAT_VOLUME_ML.get(f, 0))

    # throughput[m,f]
    throughput: dict[tuple[str, str], float] = {}
    for m, fm in cfg.throughput_units_per_hour.items():
        for f, t in fm.items():
            throughput[(m, f)] = float(t)

    # avail_hours[m,w]: start from calendar hours/week, subtract maintenance
    avail_hours: dict[tuple[str, str], float] = {}
    base_hpw = float(cfg.calendar.hours_per_week)
    for m in machines:
        for w in weeks:
            avail_hours[(m, w)] = base_hpw
    for mb in cfg.calendar.maintenance_blocks:
        key = (mb.machine, mb.week)
        if key in avail_hours:
            avail_hours[key] = max(0.0, avail_hours[key] - mb.hours_lost)
    for h in cfg.calendar.holidays:
        for m in machines:
            key = (m, h.week)
            if key in avail_hours:
                avail_hours[key] = max(0.0, avail_hours[key] - h.hours_lost)

    # eligibility per order
    eligible: dict[int, list[str]] = {}
    for o in order_ids:
        f = order_format[o]
        v = order_qty[o]
        elig = [m for m in machines if _machine_eligible_for_order(cfg, m, f, v)]
        eligible[o] = elig

    # changeover matrix per machine
    changeover_h: dict[tuple[str, str, str], float] = {}
    for m in machines:
        for f1 in formats:
            for f2 in formats:
                changeover_h[(m, f1, f2)] = _changeover_hours(cfg, m, f1, f2)

    piramal_orders = [o for o in order_ids if is_piramal(order_customer[o])]
    piramal_band = cfg.bands.get("Piramal")
    band_min = piramal_band.monthly_min if piramal_band else 0.0
    band_max = piramal_band.monthly_max if piramal_band else float("inf")

    orgs_no_split = [o for o in order_ids if order_is_org[o]]

    customers = sorted({order_customer[o] for o in order_ids})
    materials = sorted({order_material[o] for o in order_ids})

    # Production-side params — placeholders.
    shelf_life_weeks = {
        s: cfg.production.shelf_life_months_default * 4
        for s in materials
    }
    for s in cfg.production.long_sl_skus:
        if s in shelf_life_weeks:
            shelf_life_weeks[s] = cfg.production.shelf_life_months_long * 4

    scrap_rate = {s: cfg.production.scrap_rate_default for s in materials}
    evaton_skus = [s for s in cfg.production.evaton_skus if s in materials]

    # APIs = materials (one-to-one until BoM is supplied)
    apis = list(materials)

    # prod-to-pack lag in weeks (round up days/7)
    lag_days = cfg.lags.prod_to_pack_min_days
    prod_to_pack_min_weeks = (lag_days + 6) // 7

    return Parameters(
        orders=order_ids,
        materials=materials,
        customers=customers,
        machines=machines,
        formats=formats,
        weeks=weeks,
        months=months,
        apis=apis,
        order_qty=order_qty,
        order_format=order_format,
        order_material=order_material,
        order_customer=order_customer,
        order_score=order_score,
        order_is_vip=order_is_vip,
        order_is_org=order_is_org,
        order_due_week=order_due_week,
        order_pref_machine=order_pref_machine,
        throughput=throughput,
        avail_hours=avail_hours,
        eligible=eligible,
        changeover_h=changeover_h,
        piramal_orders=piramal_orders,
        piramal_band_min=band_min,
        piramal_band_max=band_max,
        orgs_no_split=orgs_no_split,
        shelf_life_weeks=shelf_life_weeks,
        scrap_rate=scrap_rate,
        evaton_skus=evaton_skus,
        evaton_min_gap_weeks=cfg.production.evaton_min_gap_weeks,
        w_fulfilment=cfg.objective_weights.fulfilment,
        w_changeover=cfg.objective_weights.changeover,
        w_idle=cfg.objective_weights.idle,
        w_tie_split=cfg.objective_weights.tie_split,
        w_late=cfg.objective_weights.late,
        prod_to_pack_min_weeks=prod_to_pack_min_weeks,
        team_hours_per_week=float(cfg.calendar.hours_per_week),
        quarter=quarter,
        horizon_weeks=len(weeks),
        backorder_included=cfg.horizon.include_backorder,
    )


def week_to_month(w: int, params: Parameters) -> int:
    return _week_to_month(w, params.quarter)
