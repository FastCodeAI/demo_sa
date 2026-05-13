"""Parameter-synthesis tests."""
from __future__ import annotations

from demo_scheduler.config.load import load_config
from demo_scheduler.data.load_excel import load_orders
from demo_scheduler.data.synthesize import synthesize


def test_synthesize_produces_complete_param_set():
    cfg = load_config()
    raw = load_orders(quarter="Q1", include_backorder=True)
    params = synthesize(raw, cfg)

    assert len(params.orders) > 0
    assert len(params.materials) > 0
    assert len(params.machines) == 4
    assert len(params.weeks) == 13
    assert len(params.months) == 3

    # Every order has at least one eligible machine OR its qty is small enough
    no_elig = [o for o in params.orders if not params.eligible[o]]
    # Order on Partena with vol > 100k would be ineligible everywhere — small set is OK
    assert len(no_elig) < len(params.orders) * 0.05, \
        f"too many orders with no eligible machine: {len(no_elig)}"

    # Throughput coverage for every (machine, eligible_format)
    for mm in params.machines:
        for f in params.formats:
            if f in cfg.machines[mm].eligible_formats:
                assert (mm, f) in params.throughput

    # Changeover matrix complete
    for mm in params.machines:
        for f1 in params.formats:
            for f2 in params.formats:
                assert (mm, f1, f2) in params.changeover_h

    # Avail hours = calendar hours/week for every (m,w)
    expected_hpw = cfg.calendar.hours_per_week
    for mm in params.machines:
        for w in params.weeks:
            assert params.avail_hours[(mm, w)] == expected_hpw

    # Piramal orders detected
    assert len(params.piramal_orders) > 0


def test_synthesize_qty_includes_backorder_for_q1():
    cfg = load_config()
    cfg.horizon.include_backorder = True
    raw = load_orders(quarter="Q1", include_backorder=True)
    params = synthesize(raw, cfg)
    total = sum(params.order_qty.values())
    # Q1 alone is ~18.1M; BO ~12.8M; combined ~30.9M
    assert 28_000_000 < total < 33_000_000
