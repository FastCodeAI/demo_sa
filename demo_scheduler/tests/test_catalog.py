"""Catalog (Phase 2b) tests — schema, compilation, equivalence."""
from __future__ import annotations

from pathlib import Path

import pyomo.environ as pyo

from demo_scheduler.catalog.load import load_catalog
from demo_scheduler.catalog.schema import PatternType
from demo_scheduler.config.load import load_config
from demo_scheduler.data.load_excel import load_orders
from demo_scheduler.data.synthesize import synthesize
from demo_scheduler.model.build import build_model
from demo_scheduler.model.compile import build_from_catalog

CATALOG_ROOT = Path(__file__).resolve().parents[2] / "catalog"


def _small_params(n: int = 20):
    cfg = load_config()
    raw = load_orders(quarter="Q1", include_backorder=True)
    params = synthesize(raw, cfg)
    keep = params.orders[:n]
    for attr in (
        "order_qty", "order_format", "order_material", "order_customer",
        "order_score", "order_is_vip", "order_is_org", "order_due_week",
        "order_pref_machine", "eligible",
    ):
        d = getattr(params, attr)
        setattr(params, attr, {o: d[o] for o in keep if o in d})
    params.orders = keep
    params.materials = sorted({params.order_material[o] for o in keep})
    params.piramal_orders = [o for o in keep if o in params.piramal_orders]
    params.orgs_no_split = [o for o in keep if o in params.orgs_no_split]
    return params


def test_catalog_loads_and_validates():
    rows = load_catalog(CATALOG_ROOT)
    assert rows, "no catalog rows found"
    ids = {r.id for r in rows}
    for required in (
        "C-001", "C-002", "C-003", "C-004", "C-005", "C-007",
        "C-008", "C-008a", "C-008b",
        "C-009", "C-011", "C-012", "C-013", "C-014", "C-015", "C-037",
    ):
        assert required in ids, f"missing catalog row {required}"
    # Pattern types must all be in the enum.
    for r in rows:
        assert isinstance(r.formal_expr.type, PatternType)


def test_catalog_model_has_migrated_blocks():
    params = _small_params(20)
    model, catalog = build_from_catalog(params, CATALOG_ROOT)
    names = {c.name for c in model.component_objects(pyo.Constraint)}

    # Hardcoded names should be GONE (replaced by catalog-installed ones).
    for stripped in (
        "fulfilment", "one_format", "x_y_link", "vol_link",
        "org_no_split", "farcon_div_xor",
        "changeover_link", "co_team_hours",
        "capacity", "piramal_lo", "piramal_hi",
        "prod_total", "prod_pack_lag",
    ):
        assert stripped not in names, f"hardcoded block {stripped} should be skipped"

    # Catalog-installed names follow `cat_<lowercase-id>` convention.
    catalog_blocks = {n for n in names if n.startswith("cat_")}
    for needed in (
        "c_001", "c_002", "c_003", "c_004", "c_005", "c_007",
        "c_008a", "c_008b", "c_009", "c_012", "c_015", "c_037",
    ):
        assert any(needed in n for n in catalog_blocks), f"missing catalog block {needed}"


def test_catalog_and_hardcoded_match_on_constraint_count():
    """Same params → both flavours have the same constraint count
    (different names, equivalent structure)."""
    params = _small_params(20)
    cat_model, _ = build_from_catalog(params, CATALOG_ROOT)
    hc_model = build_model(params)
    cat_n = sum(len(c) for c in cat_model.component_objects(pyo.Constraint))
    hc_n = sum(len(c) for c in hc_model.component_objects(pyo.Constraint))
    # Allow exact match — catalog-replaced constraints should have the
    # same number of generated rows as the hardcoded version.
    assert cat_n == hc_n, f"catalog produced {cat_n} rows vs hardcoded {hc_n}"
