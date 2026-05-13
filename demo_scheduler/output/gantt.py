"""Render a packaging Gantt to PNG."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

from demo_scheduler.solve.extract import Plan


FORMAT_COLOURS = {
    "2ml":  "#1f77b4",
    "3ml":  "#ff7f0e",
    "5ml":  "#2ca02c",
    "10ml": "#d62728",
    "20ml": "#9467bd",
}


def write_gantt(plan: Plan, path: Path) -> None:
    """Render the packaging Gantt.

    If `plan.sequence` is populated (CP-SAT produced minute-level intervals),
    bars are drawn at minute granularity within each week. Otherwise the
    legacy weekly cell view is used (Phase-1 fallback).
    """
    machines = plan.machines
    weeks = plan.weeks

    fig, ax = plt.subplots(figsize=(max(8, len(weeks) * 0.9), max(3, len(machines) * 0.7)))
    bar_h = 0.7

    if plan.sequence:
        _draw_minute_level(ax, plan, machines, bar_h)
    else:
        _draw_weekly(ax, plan, machines, bar_h)

    ax.set_yticks(range(len(machines)))
    ax.set_yticklabels(machines)
    ax.set_xticks(weeks)
    ax.set_xticklabels([f"W{w:02d}" for w in weeks], rotation=45, ha="right")
    ax.set_xlim(weeks[0] - 0.6, weeks[-1] + 0.6)
    ax.set_ylim(-0.5, len(machines) - 0.5)
    ax.set_xlabel("Week")
    title = f"Packaging Gantt — {plan.quarter}"
    if plan.sequence:
        title += " (CP-SAT minute-level)"
    ax.set_title(title)
    ax.grid(axis="x", alpha=0.2)

    formats_used = sorted({mw.active_format for mw in plan.machine_weeks if mw.active_format})
    handles = [mpatches.Patch(color=FORMAT_COLOURS.get(f, "#7f7f7f"), label=f) for f in formats_used]
    if plan.changeovers or any(s["kind"] == "changeover" for s in plan.sequence):
        handles.append(mpatches.Patch(color="black", alpha=0.6, hatch="//", label="changeover"))
    if handles:
        ax.legend(handles=handles, loc="upper right", fontsize=8)

    plt.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(path, dpi=120)
    plt.close()


def _draw_weekly(ax, plan: Plan, machines: list[str], bar_h: float) -> None:
    for i, mm in enumerate(machines):
        for mw in plan.machine_weeks:
            if mw.machine != mm or mw.active_format is None:
                continue
            colour = FORMAT_COLOURS.get(mw.active_format, "#7f7f7f")
            ax.barh(i, 1, left=mw.week - 0.5, height=bar_h, color=colour, alpha=0.85)
    for c in plan.changeovers:
        i = machines.index(c["machine"])
        ax.barh(i, 0.15, left=c["week"] - 0.5, height=bar_h, color="black", alpha=0.6, hatch="//")


def _draw_minute_level(ax, plan: Plan, machines: list[str], bar_h: float) -> None:
    # Scale per-week to the largest end-time so each week unit on the
    # x-axis is consistently mapped to [start_sec=0 .. envelope].
    week_max: dict[int, int] = defaultdict(int)
    for s in plan.sequence:
        if s["end_sec"] > week_max[s["week"]]:
            week_max[s["week"]] = s["end_sec"]
    week_scale = {w: (week_max[w] if week_max[w] > 0 else 1) for w in plan.weeks}

    for s in plan.sequence:
        if s["machine"] not in machines:
            continue
        i = machines.index(s["machine"])
        scale = week_scale.get(s["week"], 1)
        left = (s["week"] - 0.5) + s["start_sec"] / scale
        width = max(1e-3, (s["end_sec"] - s["start_sec"]) / scale)
        if s["kind"] == "changeover":
            ax.barh(i, width, left=left, height=bar_h, color="black", alpha=0.6, hatch="//")
        else:
            fmt = s.get("fmt_to") or "?"
            colour = FORMAT_COLOURS.get(fmt, "#7f7f7f")
            ax.barh(i, width, left=left, height=bar_h, color=colour, alpha=0.85)
