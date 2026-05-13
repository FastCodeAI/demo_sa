"""Model-build sanity tests on a small subset."""
from __future__ import annotations

import pyomo.environ as pyo

from demo_scheduler.config.load import load_config
from demo_scheduler.data.load_excel import load_orders
from demo_scheduler.data.synthesize import synthesize
from demo_scheduler.model.build import build_model


def _build_subset(n: int = 10):
    cfg = load_config()
    raw = load_orders(quarter="Q1", include_backorder=True)
    params = synthesize(raw, cfg)
    # Take a small subset for fast model-build
    keep = params.orders[:n]
    params.orders = keep
    params.order_qty = {o: params.order_qty[o] for o in keep}
    params.order_format = {o: params.order_format[o] for o in keep}
    params.order_material = {o: params.order_material[o] for o in keep}
    params.order_customer = {o: params.order_customer[o] for o in keep}
    params.order_score = {o: params.order_score[o] for o in keep}
    params.order_is_vip = {o: params.order_is_vip[o] for o in keep}
    params.order_is_org = {o: params.order_is_org[o] for o in keep}
    params.order_due_week = {o: params.order_due_week[o] for o in keep}
    params.order_pref_machine = {o: params.order_pref_machine[o] for o in keep}
    params.eligible = {o: params.eligible[o] for o in keep}
    params.materials = sorted({params.order_material[o] for o in keep})
    params.piramal_orders = [o for o in keep if o in params.piramal_orders]
    params.orgs_no_split = [o for o in keep if o in params.orgs_no_split]
    return build_model(params), params


def test_model_builds_with_all_expected_blocks():
    model, params = _build_subset(n=20)
    names = {c.name for c in model.component_objects(pyo.Constraint)}
    expected = {
        "fulfilment", "vol_link", "one_format", "x_y_link",
        "org_no_split", "org_vol_eq",
        "piramal_lo", "piramal_hi",
        "farcon_div_xor", "changeover_link", "co_team_hours",
        "capacity", "prod_total", "prod_pack_lag",
    }
    missing = expected - names
    assert not missing, f"missing constraint blocks: {missing}"


def test_model_has_decision_vars():
    model, _ = _build_subset(n=10)
    var_names = {v.name for v in model.component_objects(pyo.Var)}
    for needed in ("x_pack", "y_pack", "co_pack", "vol_pack",
                   "qty_prod", "unfilled", "late", "idle_hours",
                   "piramal_under", "piramal_over"):
        assert needed in var_names
