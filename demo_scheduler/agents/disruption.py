"""Disruption-Response Agent — telemetry + current plan → LNS patch.

Stub for Phase 2c. The real LNS repair logic lives in Phase 3
(`solve/lns.py`, not yet implemented). This module sketches the agent
contract: receive a disruption event (photocell freeze, machine down,
late delivery), and return a *bounded patch* — a list of (machine,
week-window) cells the orchestrator should re-solve.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DisruptionEvent:
    kind: str                      # "machine_down" | "late_delivery" | "photocell_freeze" | ...
    machine: str | None = None
    week: int | None = None
    duration_minutes: int = 0
    details: dict = field(default_factory=dict)


@dataclass
class RepairPatch:
    """Bounded re-solve window — never the full plan."""
    affected_cells: list[tuple[str, int]]   # (machine, week) pairs
    rationale: str
    requires_approval: bool = True


def propose_repair(event: DisruptionEvent, plan, lookahead_weeks: int = 2) -> RepairPatch:
    """Pick a small neighbourhood around the affected cell for re-solve."""
    cells: list[tuple[str, int]] = []
    if event.machine and event.week is not None:
        for dw in range(lookahead_weeks + 1):
            cells.append((event.machine, event.week + dw))
    return RepairPatch(
        affected_cells=cells,
        rationale=f"{event.kind} on {event.machine} W{event.week} — repairing next {lookahead_weeks} weeks",
        requires_approval=True,
    )
