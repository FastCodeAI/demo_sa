"""Phase 2a end-to-end: MILP → CP-SAT orchestrator.

These tests are kept small to run in a few seconds. The full Q1 smoke
test lives in `test_q1_smoke.py` and is exercised by CI on demand.
"""
from __future__ import annotations

from collections import defaultdict

import pytest

from demo_scheduler.config.load import load_config
from demo_scheduler.data.load_excel import load_orders
from demo_scheduler.data.synthesize import synthesize
from demo_scheduler.model.tier3_cpsat import WeekInput, solve_week
from demo_scheduler.solve.orchestrator import solve_orchestrated


def _small_params(n: int = 30):
    """Take the first N orders so the MILP solves in seconds."""
    cfg = load_config()
    cfg.solver.time_limit_seconds = 30
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
    return params, cfg


def test_cpsat_single_week_no_overlap():
    """Two orders on the same machine in one week must serialise without overlap."""
    wi = WeekInput(
        week=1,
        avail_seconds={"Marchesini_GL": 7200 * 60, "Farcon": 7200 * 60},
        assignments_by_machine={
            "Marchesini_GL": [
                (1, 100_000, "2ml", 18000.0),
                (2, 80_000,  "2ml", 18000.0),
            ],
            "Farcon": [],
        },
        transitions={},
    )
    ws = solve_week(wi, time_limit_s=5.0)
    assert ws.status in ("optimal", "feasible"), f"got {ws.status}"
    packs = [iv for iv in ws.intervals if iv.kind == "pack"]
    assert len(packs) == 2
    packs.sort(key=lambda x: x.start_sec)
    assert packs[0].end_sec <= packs[1].start_sec, "packs overlap on the same machine"


def test_cpsat_global_changeover_team_no_overlap():
    """Two simultaneous changeovers (one per machine) cannot overlap — shared team."""
    co_dur_sec = 120 * 60  # 2 hours
    wi = WeekInput(
        week=2,
        avail_seconds={"Marchesini_GL": 7200 * 60, "Farcon": 7200 * 60},
        assignments_by_machine={
            "Marchesini_GL": [(1, 50_000, "5ml", 14000.0)],
            "Farcon":        [(2, 40_000, "5ml", 10000.0)],
        },
        transitions={
            "Marchesini_GL": ("2ml", "5ml", co_dur_sec),
            "Farcon":        ("2ml", "5ml", co_dur_sec),
        },
    )
    ws = solve_week(wi, time_limit_s=5.0)
    assert ws.status in ("optimal", "feasible")
    co = [iv for iv in ws.intervals if iv.kind == "changeover"]
    assert len(co) == 2
    co.sort(key=lambda x: x.start_sec)
    assert co[0].end_sec <= co[1].start_sec, "team-shared changeovers overlap"


def test_orchestrator_runs_end_to_end():
    """Tier-2 + Tier-3 should produce a sequence with no per-machine overlaps."""
    params, _cfg = _small_params(n=30)
    plan, info = solve_orchestrated(
        params,
        time_limit_milp_s=30,
        mip_gap=0.05,
        time_limit_cpsat_per_week_s=5.0,
        max_retries=1,
    )
    assert info.milp_result.status in {"optimal", "feasible", "time_limit"}
    # Some sequencing must have happened.
    assert plan.sequence, "CP-SAT produced no intervals"

    # Per-machine, per-week no-overlap
    bucket: dict[tuple[str, int], list[tuple[int, int]]] = defaultdict(list)
    for s in plan.sequence:
        bucket[(s["machine"], s["week"])].append((s["start_sec"], s["end_sec"]))
    for key, ivs in bucket.items():
        ivs.sort()
        for (s1, e1), (s2, _e2) in zip(ivs, ivs[1:]):
            assert e1 <= s2, f"overlap on {key}: {ivs}"

    # Global changeover-team no-overlap (per week)
    co_by_week: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for s in plan.sequence:
        if s["kind"] == "changeover":
            co_by_week[s["week"]].append((s["start_sec"], s["end_sec"]))
    for w, ivs in co_by_week.items():
        ivs.sort()
        for (s1, e1), (s2, _e2) in zip(ivs, ivs[1:]):
            assert e1 <= s2, f"changeover team overlap in W{w}: {ivs}"
