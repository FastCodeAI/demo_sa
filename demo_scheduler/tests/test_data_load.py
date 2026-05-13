"""Excel loader smoke tests."""
from __future__ import annotations

import pandas as pd

from demo_scheduler.data.canonicalize import add_canonical_columns
from demo_scheduler.data.load_excel import load_orders


def test_full_master_loads():
    raw = load_orders(quarter="Q1", include_backorder=False)
    # Q1-only filter still has rows
    assert len(raw.orders) > 0
    # Full demand table is the underlying 1,824-row sheet
    assert raw.full_demand.shape[0] == 1824
    # Total across all quarters ≈ 90.4M
    total_2025 = float(raw.full_demand["Total Quantity for packaging 2025"].sum())
    assert 90_000_000 < total_2025 < 91_000_000


def test_canonical_columns():
    raw = load_orders(quarter="Q1", include_backorder=True)
    df = add_canonical_columns(raw.orders)
    assert "machine_pref" in df.columns
    assert "format" in df.columns
    assert "customer" in df.columns
    machines = set(df["machine_pref"].dropna().unique())
    # canonicalisation must have removed the space in 'Marchesini GL'
    assert "Marchesini_GL" in machines
    assert "Farcon" in machines
    # Format codes normalised
    formats = set(df["format"].dropna().unique())
    assert "2ml" in formats or "10ml" in formats
    assert not any(f.startswith("000") for f in formats)


def test_q1_horizon_includes_backorder():
    q1 = load_orders(quarter="Q1", include_backorder=True)
    q1_no_bo = load_orders(quarter="Q1", include_backorder=False)
    assert len(q1.orders) >= len(q1_no_bo.orders)
