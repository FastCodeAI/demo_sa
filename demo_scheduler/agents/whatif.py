"""What-If Agent — KPI diff between two plans.

Reads `kpis.json` from two runs (before and after a catalog patch) and
asks the LLM to narrate the delta in 3-5 bullets. The LLM never sees
the patched catalog directly; the diff is conveyed entirely in numbers.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from demo_scheduler.agents.llm import LLMClient


SYSTEM_PROMPT = """\
You are the What-If Agent for the DEMO Pharma scheduler.

You will receive two KPI dictionaries (BEFORE and AFTER a catalog change).
Narrate the delta in 3-5 bullet points. Be concise, use percentages and
absolute counts where helpful. Highlight: OTIF change, unfilled change,
Piramal band coverage change, utilisation shifts, # changeovers, solve
time. Do NOT speculate about causes outside the data.
"""


@dataclass
class WhatIfResult:
    narration: str
    delta: dict


def diff_kpis(before: dict, after: dict) -> dict:
    """Compute a structured delta the LLM can chew on."""
    def _d(a, b):
        if a is None or b is None:
            return None
        try:
            return round(b - a, 2)
        except TypeError:
            return None

    out = {
        "otif_pct":              _d(before.get("otif_pct"), after.get("otif_pct")),
        "rated_otif_pct":        _d(before.get("rated_otif_pct"), after.get("rated_otif_pct")),
        "vip_otif_pct":          _d(before.get("vip_otif_pct"), after.get("vip_otif_pct")),
        "total_changeover_h":    _d(before.get("total_changeover_h"), after.get("total_changeover_h")),
        "total_idle_h":          _d(before.get("total_idle_h"), after.get("total_idle_h")),
        "n_changeovers":         _d(before.get("n_changeovers"), after.get("n_changeovers")),
        "solve_time_s":          _d(before.get("solve_time_s"), after.get("solve_time_s")),
    }
    bt = before.get("totals", {}); at = after.get("totals", {})
    out["packed"] = _d(bt.get("packed"), at.get("packed"))
    out["unfilled"] = _d(bt.get("unfilled"), at.get("unfilled"))
    return out


def whatif(before_kpis_path: Path, after_kpis_path: Path, llm: LLMClient) -> WhatIfResult:
    before = json.loads(before_kpis_path.read_text())
    after = json.loads(after_kpis_path.read_text())
    delta = diff_kpis(before, after)

    user_msg = (
        "BEFORE KPIs (extract):\n"
        + json.dumps({k: before.get(k) for k in ("otif_pct", "rated_otif_pct", "vip_otif_pct",
                                                  "total_changeover_h", "total_idle_h",
                                                  "n_changeovers", "totals", "solve_time_s")}, indent=2)
        + "\n\nAFTER KPIs (extract):\n"
        + json.dumps({k: after.get(k) for k in ("otif_pct", "rated_otif_pct", "vip_otif_pct",
                                                 "total_changeover_h", "total_idle_h",
                                                 "n_changeovers", "totals", "solve_time_s")}, indent=2)
        + "\n\nDELTA:\n"
        + json.dumps(delta, indent=2)
        + "\n\nWrite the 3-5 bullet narration now."
    )
    resp = llm.complete(system=SYSTEM_PROMPT, user=user_msg, max_tokens=400)
    return WhatIfResult(narration=resp.text.strip(), delta=delta)
