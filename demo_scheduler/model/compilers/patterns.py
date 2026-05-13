"""Pattern compilers.

Each function turns one `formal_expr.type` into Pyomo constraint blocks
attached to the model. The variable schema (x_pack, y_pack, vol_pack,
unfilled, qty_prod, piramal_under, piramal_over, idle_hours, co_pack)
is fixed; the catalog only chooses which constraints to install and
how they're parameterised.

Phase 2b ships compilers for the 5 patterns needed by the four migrated
constraints (C-001, C-005, C-007, C-037). Remaining patterns are
stubbed with explicit NotImplementedError so the dispatcher fails loud
when an un-migrated rule lands in the catalog.
"""
from __future__ import annotations

import pyomo.environ as pyo

from demo_scheduler.catalog.schema import CatalogRow
from demo_scheduler.data.synthesize import Parameters, week_to_month
from demo_scheduler.model.compilers import register


def _machine_formats(model: pyo.ConcreteModel, params: Parameters) -> dict[str, set[str]]:
    return getattr(
        model, "machine_formats",
        {m: set(params.formats) for m in params.machines},
    )


@register("sum_le")
def compile_sum_le(row: CatalogRow, model: pyo.ConcreteModel, params: Parameters) -> None:
    """Generic sum-le: sum_{i in index_over} var[i,...] <= rhs.

    Currently handles the one-format-per-cycle case (`y_pack`, indexed
    over (machine, week), summed over formats, rhs=1).
    """
    expr = row.formal_expr
    var_name = getattr(expr, "variable", None) or expr.model_extra.get("variable")
    rhs = getattr(expr, "rhs", None) if hasattr(expr, "rhs") else expr.model_extra.get("rhs")
    if var_name != "y_pack":
        raise NotImplementedError(f"sum_le not implemented for variable {var_name}")
    mf = _machine_formats(model, params)

    def _rule(m_, mm, w):
        formats = mf.get(mm, set())
        if not formats:
            return pyo.Constraint.Skip
        return sum(m_.y_pack[mm, f, w] for f in formats if (mm, f, w) in m_.y_pack) <= int(rhs)

    model.add_component(
        f"cat_{row.id.lower().replace('-', '_')}",
        pyo.Constraint(model.M, model.W, rule=_rule),
    )


@register("mutex")
def compile_mutex(row: CatalogRow, model: pyo.ConcreteModel, params: Parameters) -> None:
    """sum of `y_pack` flags across the listed machines, per week, <= 1."""
    machines = row.formal_expr.model_extra.get("machines", [])
    machines = [m for m in machines if m in params.machines]
    if not machines:
        return
    mf = _machine_formats(model, params)

    def _rule(m_, w):
        terms = [
            m_.y_pack[mm, f, w]
            for mm in machines for f in mf.get(mm, set())
            if (mm, f, w) in m_.y_pack
        ]
        if not terms:
            return pyo.Constraint.Skip
        return sum(terms) <= 1

    model.add_component(
        f"cat_{row.id.lower().replace('-', '_')}",
        pyo.Constraint(model.W, rule=_rule),
    )


@register("single_placement")
def compile_single_placement(row: CatalogRow, model: pyo.ConcreteModel, params: Parameters) -> None:
    """Orders in the named scope must be placed in exactly one (m,w) cell."""
    org_orders = list(params.orgs_no_split)
    if not org_orders:
        return

    def _placement_rule(m_, o):
        if o not in org_orders:
            return pyo.Constraint.Skip
        eligible_mw = [(mm, w) for mm in params.eligible[o] for w in m_.W]
        if not eligible_mw:
            return pyo.Constraint.Skip
        return sum(m_.x_pack[o, mm, w] for (mm, w) in eligible_mw) == 1

    def _vol_rule(m_, o, mm, w):
        if o not in org_orders:
            return pyo.Constraint.Skip
        return m_.vol_pack[o, mm, w] == params.order_qty[o] * m_.x_pack[o, mm, w]

    rid = row.id.lower().replace('-', '_')
    model.add_component(f"cat_{rid}_placement", pyo.Constraint(model.O, rule=_placement_rule))
    model.add_component(f"cat_{rid}_vol", pyo.Constraint(model.OMW, rule=_vol_rule))


@register("two_sided_bound")
def compile_two_sided_bound(row: CatalogRow, model: pyo.ConcreteModel, params: Parameters) -> None:
    """Soft monthly band, currently specialised for Piramal."""
    if not params.piramal_orders:
        return
    band_min = float(row.parameters.get("monthly_min", params.piramal_band_min))
    band_max = float(row.parameters.get("monthly_max", params.piramal_band_max))

    def _lo(m_, t):
        weeks_in_t = [w for w in m_.W if week_to_month(w, params) == t]
        total = sum(
            m_.vol_pack[o, mm, w]
            for o in params.piramal_orders
            for mm in params.eligible[o]
            for w in weeks_in_t
        )
        return total + m_.piramal_under[t] >= band_min

    def _hi(m_, t):
        weeks_in_t = [w for w in m_.W if week_to_month(w, params) == t]
        total = sum(
            m_.vol_pack[o, mm, w]
            for o in params.piramal_orders
            for mm in params.eligible[o]
            for w in weeks_in_t
        )
        return total - m_.piramal_over[t] <= band_max

    rid = row.id.lower().replace('-', '_')
    model.add_component(f"cat_{rid}_lo", pyo.Constraint(model.T, rule=_lo))
    model.add_component(f"cat_{rid}_hi", pyo.Constraint(model.T, rule=_hi))


@register("balance")
def compile_balance(row: CatalogRow, model: pyo.ConcreteModel, params: Parameters) -> None:
    """Demand-fulfilment balance: sum_packed + slack == demand, per order."""
    def _rule(m_, o):
        eligible_mw = [(mm, w) for mm in params.eligible[o] for w in m_.W]
        if not eligible_mw:
            return m_.unfilled[o] == params.order_qty[o]
        return sum(m_.vol_pack[o, mm, w] for (mm, w) in eligible_mw) + \
            m_.unfilled[o] == params.order_qty[o]

    rid = row.id.lower().replace('-', '_')
    model.add_component(f"cat_{rid}", pyo.Constraint(model.O, rule=_rule))


@register("capacity_eq")
def compile_capacity_eq(row: CatalogRow, model: pyo.ConcreteModel, params: Parameters) -> None:
    """Per-(machine, week) hours-balance: pack + changeover + idle == avail."""
    mf = _machine_formats(model, params)

    def _rule(m_, mm, w):
        pack_hours = sum(
            m_.vol_pack[o, mm, w] / params.throughput[(mm, params.order_format[o])]
            for o in params.orders
            if mm in params.eligible[o]
            and (mm, params.order_format[o]) in params.throughput
        )
        co_hours = sum(
            m_.co_pack[mm, f1, f2, w] * params.changeover_h[(mm, f1, f2)]
            for f1 in mf.get(mm, set())
            for f2 in mf.get(mm, set()) if f1 != f2
        )
        avail = params.avail_hours[(mm, w)]
        return pack_hours + co_hours + m_.idle_hours[mm, w] == avail

    rid = row.id.lower().replace('-', '_')
    model.add_component(f"cat_{rid}", pyo.Constraint(model.M, model.W, rule=_rule))


@register("sparse_set")
def compile_sparse_set(row: CatalogRow, model: pyo.ConcreteModel, params: Parameters) -> None:
    """x_pack[o,m,w] <= y_pack[m, fmt(o), w] across the sparse OMW set."""
    mf = _machine_formats(model, params)

    def _rule(m_, o, mm, w):
        f = params.order_format[o]
        if f not in mf.get(mm, set()):
            return pyo.Constraint.Skip
        return m_.x_pack[o, mm, w] <= m_.y_pack[mm, f, w]

    rid = row.id.lower().replace('-', '_')
    model.add_component(f"cat_{rid}", pyo.Constraint(model.OMW, rule=_rule))


@register("bound_linear")
def compile_bound_linear(row: CatalogRow, model: pyo.ConcreteModel, params: Parameters) -> None:
    """vol_pack[o,m,w] <= qty[o] * x_pack[o,m,w] — gate the continuous
    volume variable by its binary activation flag."""
    def _rule(m_, o, mm, w):
        return m_.vol_pack[o, mm, w] <= params.order_qty[o] * m_.x_pack[o, mm, w]

    rid = row.id.lower().replace('-', '_')
    model.add_component(f"cat_{rid}", pyo.Constraint(model.OMW, rule=_rule))


@register("indicator_link")
def compile_indicator_link(row: CatalogRow, model: pyo.ConcreteModel, params: Parameters) -> None:
    """Big-M linearisation: co_pack[m,f1,f2,w] >= y[m,f1,w-1] + y[m,f2,w] - 1.

    Catalog C-008a: count a format change exactly when two consecutive
    weeks have different active formats. Tier-3 CP-SAT then sequences
    the resulting transition intervals at minute granularity.
    """
    weeks_sorted = sorted(params.weeks)
    week_prev = {w: weeks_sorted[i - 1] for i, w in enumerate(weeks_sorted) if i > 0}
    mf = _machine_formats(model, params)

    def _rule(m_, mm, f1, f2, w):
        if w not in week_prev:
            return pyo.Constraint.Skip
        if f1 not in mf.get(mm, set()) or f2 not in mf.get(mm, set()):
            return pyo.Constraint.Skip
        return m_.co_pack[mm, f1, f2, w] >= \
            m_.y_pack[mm, f1, week_prev[w]] + m_.y_pack[mm, f2, w] - 1

    rid = row.id.lower().replace('-', '_')
    model.add_component(f"cat_{rid}", pyo.Constraint(model.COIDX, rule=_rule))


@register("weighted_sum_le")
def compile_weighted_sum_le(row: CatalogRow, model: pyo.ConcreteModel, params: Parameters) -> None:
    """Weekly cap on total changeover-team time:
       sum_{m,f1,f2} co_pack[m,f1,f2,w] * changeover_h[m,f1,f2] <= team_hours_per_week.

    Catalog C-008b: a loose hours envelope around the Tier-3 CP-SAT
    minute-level no-overlap. Without this, MILP could ask CP-SAT to
    sequence more changeover-team work than fits in a shift week.
    """
    cap_param = row.parameters.get("team_hours_per_week")
    cap = float(cap_param) if cap_param is not None else float(params.team_hours_per_week)
    mf = _machine_formats(model, params)

    def _rule(m_, w):
        terms = [
            m_.co_pack[mm, f1, f2, w] * params.changeover_h[(mm, f1, f2)]
            for mm in params.machines
            for f1 in mf.get(mm, set())
            for f2 in mf.get(mm, set()) if f1 != f2
        ]
        if not terms:
            return pyo.Constraint.Skip
        return sum(terms) <= cap

    rid = row.id.lower().replace('-', '_')
    model.add_component(f"cat_{rid}", pyo.Constraint(model.W, rule=_rule))


@register("aggregated_balance")
def compile_aggregated_balance(row: CatalogRow, model: pyo.ConcreteModel, params: Parameters) -> None:
    """produced * (1 - scrap) >= packed, summed over the horizon, per material.

    Catalog C-012 (loose form): production over the horizon, net of
    scrap, must cover the total packed volume for that material.
    """
    def _rule(m_, s):
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

    rid = row.id.lower().replace('-', '_')
    model.add_component(f"cat_{rid}", pyo.Constraint(model.S, rule=_rule))


@register("cumulative_lag")
def compile_cumulative_lag(row: CatalogRow, model: pyo.ConcreteModel, params: Parameters) -> None:
    """Cumulative production-to-pack lag (in weeks), per material:
       sum_{pw <= w - lag} qty_prod[s, pw] >= sum_{pw <= w} packed[s, pw]

    Catalog C-009: enforced over the rolling cumulative because the
    13-week horizon doesn't carry per-batch identity.
    """
    lag = int(row.parameters.get("lag_weeks", params.prod_to_pack_min_weeks))
    if lag <= 0:
        return

    def _rule(m_, s, w):
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

    rid = row.id.lower().replace('-', '_')
    model.add_component(f"cat_{rid}", pyo.Constraint(model.S, model.W, rule=_rule))


@register("disabled")
def compile_disabled(row: CatalogRow, model: pyo.ConcreteModel, params: Parameters) -> None:
    """No-op compiler for rows we keep in the catalog for completeness
    (e.g. C-011 one-API-per-week) but don't currently install."""
    return


# ----- stubs (raise loudly when an un-migrated catalog row arrives) -----

def _stub(name: str):
    def _fn(row: CatalogRow, model: pyo.ConcreteModel, params: Parameters) -> None:
        raise NotImplementedError(
            f"pattern '{name}' compiler not yet implemented (catalog row {row.id})"
        )
    return _fn


for _p in (
    "range_eligibility", "resource_no_overlap",
    "precedence_lag", "time_window", "forbidden_assignments",
    "aggregated_demand", "deviation_penalty", "lex_min", "relaxation_marker",
):
    register(_p)(_stub(_p))
