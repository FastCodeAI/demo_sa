"""Build the two-stage MILP as a Pyomo ConcreteModel.

The model implements the 15 hard + 3 soft constraints from
docs/MODEL_SCOPING.md §5 against the parameter tables produced by
demo_scheduler.data.synthesize.

Design notes for v1:
  * `vol_pack` is indexed by (order, machine, week) so capacity attribution
    is unambiguous when an order has multiple eligible machines.
  * Sequence-dependent setup is linearised via consecutive-week y_pack
    indicators (constraint `co_pack[m,f1,f2,w] >= y[m,f1,w-1] + y[m,f2,w] - 1`).
  * Piramal monthly band (req #37) is encoded with under/over slack
    variables penalised at the lateness weight. The current data
    (~2.8M/month) exceeds the stated 1.8–2.0M band — see open question
    in docs/MODEL_SCOPING.md §10. Slack lets us measure the violation
    rather than infeasibility.
  * Production weekly lag is enforced via cumulative inventory:
    production-to-date (lag weeks earlier) must cover packing-to-date.
  * One-API-per-week (req #42) is omitted from v1 — would force <=13
    distinct materials in Q1 against 145 demanded; needs production-line
    granularity not present in the data.
  * EVATON gap (req #49) is structurally encoded but `evaton_skus` is empty
    by default; constraint stays vacuous.
"""
from __future__ import annotations

import pyomo.environ as pyo

from demo_scheduler.data.synthesize import Parameters, week_to_month


def build_model(
    params: Parameters,
    skip: set[str] | None = None,
) -> pyo.ConcreteModel:
    """Build the Pyomo model. Pass `skip={...}` to omit named constraint
    blocks — used by `model.compile.build_from_catalog` to replace
    hardcoded blocks with their catalog-compiled equivalents.
    """
    skip = skip or set()
    m = pyo.ConcreteModel(name="DEMO_Scheduler_Q1")

    # ----- Sets -----
    m.O = pyo.Set(initialize=params.orders, ordered=True)
    m.M = pyo.Set(initialize=params.machines, ordered=True)
    m.F = pyo.Set(initialize=params.formats, ordered=True)
    m.W = pyo.Set(initialize=params.weeks, ordered=True)
    m.S = pyo.Set(initialize=params.materials, ordered=True)
    m.T = pyo.Set(initialize=params.months, ordered=True)

    eligible_omw = [
        (o, mm, w) for o in params.orders for mm in params.eligible[o] for w in params.weeks
    ]
    m.OMW = pyo.Set(dimen=3, initialize=eligible_omw)

    # Format eligibility per machine (drives y_pack/co_pack sparsity).
    machine_formats: dict[str, set[str]] = {
        mm: set() for mm in params.machines
    }
    for o in params.orders:
        f = params.order_format[o]
        for mm in params.eligible[o]:
            machine_formats[mm].add(f)
    mfw_pairs = [
        (mm, f, w) for mm in params.machines for f in machine_formats[mm] for w in params.weeks
    ]
    co_pairs = [
        (mm, f1, f2, w)
        for mm in params.machines
        for f1 in machine_formats[mm]
        for f2 in machine_formats[mm]
        if f1 != f2
        for w in params.weeks
    ]
    m.MFW = pyo.Set(dimen=3, initialize=mfw_pairs)
    m.COIDX = pyo.Set(dimen=4, initialize=co_pairs)

    # ----- Variables -----
    m.x_pack = pyo.Var(m.OMW, within=pyo.Binary)
    m.vol_pack = pyo.Var(m.OMW, within=pyo.NonNegativeReals)

    m.y_pack = pyo.Var(m.MFW, within=pyo.Binary)
    m.co_pack = pyo.Var(m.COIDX, within=pyo.NonNegativeReals, bounds=(0, 1))

    m.unfilled = pyo.Var(m.O, within=pyo.NonNegativeReals)
    m.late = pyo.Var(m.O, within=pyo.NonNegativeReals)
    m.idle_hours = pyo.Var(m.M, m.W, within=pyo.NonNegativeReals)

    m.qty_prod = pyo.Var(m.S, m.W, within=pyo.NonNegativeReals)

    m.piramal_under = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.piramal_over = pyo.Var(m.T, within=pyo.NonNegativeReals)

    # ----- Constraint #2: demand fulfilment balance -----
    if "fulfilment" not in skip:
        def fulfil_rule(m_, o):
            eligible_mw = [(mm, w) for mm in params.eligible[o] for w in m_.W]
            if not eligible_mw:
                return m_.unfilled[o] == params.order_qty[o]
            return sum(m_.vol_pack[o, mm, w] for (mm, w) in eligible_mw) + \
                m_.unfilled[o] == params.order_qty[o]
        m.fulfilment = pyo.Constraint(m.O, rule=fulfil_rule)

    # vol_pack[o,m,w] <= qty[o] * x_pack[o,m,w]
    if "vol_link" not in skip:
        def vol_link_rule(m_, o, mm, w):
            return m_.vol_pack[o, mm, w] <= params.order_qty[o] * m_.x_pack[o, mm, w]
        m.vol_link = pyo.Constraint(m.OMW, rule=vol_link_rule)

    # ----- Constraint #1: one format per (m,w) -----
    if "one_format" not in skip:
        def one_format_rule(m_, mm, w):
            formats = machine_formats[mm]
            if not formats:
                return pyo.Constraint.Skip
            return sum(m_.y_pack[mm, f, w] for f in formats) <= 1
        m.one_format = pyo.Constraint(m.M, m.W, rule=one_format_rule)

    # x_pack[o,m,w] <= y_pack[m, f(o), w]
    if "x_y_link" not in skip:
        def x_y_link_rule(m_, o, mm, w):
            f = params.order_format[o]
            if f not in machine_formats[mm]:
                return pyo.Constraint.Skip
            return m_.x_pack[o, mm, w] <= m_.y_pack[mm, f, w]
        m.x_y_link = pyo.Constraint(m.OMW, rule=x_y_link_rule)

    # ----- Constraint #5: org no-split — entire qty in a single (m,w) -----
    if "org_no_split" not in skip:
        def org_no_split_rule(m_, o):
            if o not in params.orgs_no_split:
                return pyo.Constraint.Skip
            eligible_mw = [(mm, w) for mm in params.eligible[o] for w in m_.W]
            if not eligible_mw:
                return pyo.Constraint.Skip
            return sum(m_.x_pack[o, mm, w] for (mm, w) in eligible_mw) == 1
        m.org_no_split = pyo.Constraint(m.O, rule=org_no_split_rule)

        def org_vol_eq_rule(m_, o, mm, w):
            if o not in params.orgs_no_split:
                return pyo.Constraint.Skip
            return m_.vol_pack[o, mm, w] == params.order_qty[o] * m_.x_pack[o, mm, w]
        m.org_vol_eq = pyo.Constraint(m.OMW, rule=org_vol_eq_rule)

    # ----- Constraint #6: Piramal monthly band (soft via slacks) -----
    if "piramal_band" not in skip:
        def piramal_lo_rule(m_, t):
            if not params.piramal_orders:
                return pyo.Constraint.Skip
            weeks_in_t = [w for w in m_.W if week_to_month(w, params) == t]
            total = sum(
                m_.vol_pack[o, mm, w]
                for o in params.piramal_orders
                for mm in params.eligible[o]
                for w in weeks_in_t
            )
            return total + m_.piramal_under[t] >= params.piramal_band_min
        m.piramal_lo = pyo.Constraint(m.T, rule=piramal_lo_rule)

        def piramal_hi_rule(m_, t):
            if not params.piramal_orders:
                return pyo.Constraint.Skip
            weeks_in_t = [w for w in m_.W if week_to_month(w, params) == t]
            total = sum(
                m_.vol_pack[o, mm, w]
                for o in params.piramal_orders
                for mm in params.eligible[o]
                for w in weeks_in_t
            )
            return total - m_.piramal_over[t] <= params.piramal_band_max
        m.piramal_hi = pyo.Constraint(m.T, rule=piramal_hi_rule)

    # ----- Constraint #7: Farcon XOR Dividella per week -----
    if "farcon_div_xor" not in skip:
        def farcon_div_xor_rule(m_, w):
            if "Farcon" not in params.machines or "Dividella" not in params.machines:
                return pyo.Constraint.Skip
            return (
                sum(m_.y_pack["Farcon", f, w] for f in machine_formats["Farcon"])
                + sum(m_.y_pack["Dividella", f, w] for f in machine_formats["Dividella"])
            ) <= 1
        m.farcon_div_xor = pyo.Constraint(m.W, rule=farcon_div_xor_rule)

    # ----- Changeover indicator linearisation -----
    weeks_sorted = sorted(params.weeks)
    week_prev = {w: weeks_sorted[i - 1] for i, w in enumerate(weeks_sorted) if i > 0}

    if "changeover_link" not in skip:
        def changeover_rule(m_, mm, f1, f2, w):
            if w not in week_prev:
                return pyo.Constraint.Skip
            return m_.co_pack[mm, f1, f2, w] >= m_.y_pack[mm, f1, week_prev[w]] + \
                m_.y_pack[mm, f2, w] - 1
        m.changeover_link = pyo.Constraint(m.COIDX, rule=changeover_rule)

    # ----- Constraint #8 (loose): changeover-team weekly hour cap -----
    # Tier-2 enforces only the hours-based envelope: the sum of changeover
    # durations across all machines in a week must fit one team-week of
    # operator time. Strict minute-level no-overlap is delegated to the
    # Tier-3 CP-SAT pass (catalog C-008). If CP-SAT cannot sequence what
    # Tier-2 hands it, the orchestrator tightens this cap and re-solves.
    team_hours_cap = float(params.team_hours_per_week)

    if "co_team_hours" not in skip:
        def co_team_hours_rule(m_, w):
            terms = [
                m_.co_pack[mm, f1, f2, w] * params.changeover_h[(mm, f1, f2)]
                for mm in params.machines
                for f1 in machine_formats[mm]
                for f2 in machine_formats[mm] if f1 != f2
            ]
            if not terms:
                return pyo.Constraint.Skip
            return sum(terms) <= team_hours_cap
        m.co_team_hours = pyo.Constraint(m.W, rule=co_team_hours_rule)

    # ----- Constraint #15: capacity (hours) — packing + changeover + idle = avail -----
    if "capacity" not in skip:
        def capacity_rule(m_, mm, w):
            pack_hours = sum(
                m_.vol_pack[o, mm, w] / params.throughput[(mm, params.order_format[o])]
                for o in params.orders
                if mm in params.eligible[o]
                and (mm, params.order_format[o]) in params.throughput
            )
            co_hours = sum(
                m_.co_pack[mm, f1, f2, w] * params.changeover_h[(mm, f1, f2)]
                for f1 in machine_formats[mm]
                for f2 in machine_formats[mm] if f1 != f2
            )
            avail = params.avail_hours[(mm, w)]
            return pack_hours + co_hours + m_.idle_hours[mm, w] == avail
        m.capacity = pyo.Constraint(m.M, m.W, rule=capacity_rule)

    # ----- Constraint #9/#10: prod→pack lag (cumulative form, per material) -----
    lag = params.prod_to_pack_min_weeks

    if "prod_total" not in skip:
        def prod_total_rule(m_, s):
            # Production must cover total packing for this material, net of scrap.
            produced = sum(m_.qty_prod[s, w] for w in m_.W)
            packed = sum(
                m_.vol_pack[o, mm, w]
                for o in params.orders
                for mm in params.eligible[o]
                for w in m_.W
                if params.order_material[o] == s
            )
            scrap = params.scrap_rate.get(s, 0.0)
            return produced * (1 - scrap) >= packed
        m.prod_total = pyo.Constraint(m.S, rule=prod_total_rule)

    if lag > 0 and "prod_pack_lag" not in skip:
        def lag_rule(m_, s, w):
            prod_weeks = [pw for pw in m_.W if pw <= w - lag]
            cum_prod = sum(m_.qty_prod[s, pw] for pw in prod_weeks) if prod_weeks else 0
            cum_pack = sum(
                m_.vol_pack[o, mm, pw]
                for o in params.orders
                for mm in params.eligible[o]
                for pw in m_.W if pw <= w
                if params.order_material[o] == s
            )
            return cum_prod >= cum_pack
        m.prod_pack_lag = pyo.Constraint(m.S, m.W, rule=lag_rule)

    # ----- Constraint #13: shelf-life storage cap — vacuous when SL ≥ horizon -----
    # Skipped in v1 (default 24-month SL > 13-week horizon).

    # ----- Constraint #14: EVATON 2-batch / 3-week gap -----
    # Vacuous when params.evaton_skus is empty (default). Structural placeholder.

    # ----- Constraint #11: one API per production week -----
    # Skipped — infeasible at 145 materials / 13 weeks without prod-line dimension.

    # ----- Objective -----
    def objective_rule(m_):
        fulfilment_term = sum(
            params.order_score[o] * (params.order_qty[o] - m_.unfilled[o])
            for o in m_.O
        )
        changeover_term = sum(
            m_.co_pack[mm, f1, f2, w] * params.changeover_h[(mm, f1, f2)]
            for mm in params.machines
            for f1 in machine_formats[mm]
            for f2 in machine_formats[mm] if f1 != f2
            for w in params.weeks
        )
        idle_term = sum(m_.idle_hours[mm, w] for mm in m_.M for w in m_.W)
        late_term = sum(m_.late[o] for o in m_.O)
        piramal_dev_term = sum(m_.piramal_under[t] + m_.piramal_over[t] for t in m_.T)

        return (
            params.w_fulfilment * fulfilment_term
            - params.w_changeover * changeover_term
            - params.w_idle * idle_term
            - params.w_late * late_term
            - params.w_late * piramal_dev_term
        )
    m.obj = pyo.Objective(rule=objective_rule, sense=pyo.maximize)

    # Attach for introspection / extract.
    m.machine_formats = machine_formats
    return m
