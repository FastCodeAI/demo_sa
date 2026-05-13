"""Index-set construction. Sets are built directly from Parameters."""
from __future__ import annotations

from demo_scheduler.data.synthesize import Parameters


def eligible_omw(params: Parameters) -> list[tuple[int, str, int]]:
    """All (order, machine, week) tuples where the order can be packaged on that machine."""
    out: list[tuple[int, str, int]] = []
    for o in params.orders:
        for m in params.eligible[o]:
            for w in params.weeks:
                out.append((o, m, w))
    return out


def eligible_ow(params: Parameters) -> list[tuple[int, int]]:
    return [(o, w) for o in params.orders for w in params.weeks]


def all_mfw(params: Parameters) -> list[tuple[str, str, int]]:
    return [(m, f, w) for m in params.machines for f in params.formats for w in params.weeks]


def all_mff_w(params: Parameters) -> list[tuple[str, str, str, int]]:
    return [
        (m, f1, f2, w)
        for m in params.machines
        for f1 in params.formats
        for f2 in params.formats
        for w in params.weeks
    ]


def all_sw(params: Parameters) -> list[tuple[int, int]]:
    return [(s, w) for s in params.materials for w in params.weeks]
