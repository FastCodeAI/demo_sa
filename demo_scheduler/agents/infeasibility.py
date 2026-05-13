"""Infeasibility Agent — IIS / infeasible-weeks → NL narration.

When the orchestrator returns infeasible weeks (CP-SAT couldn't sequence
the MILP assignment) or a hard infeasibility from MILP itself, this
agent reads the failure context and produces a plain-language
explanation of which catalog rules clashed and what relaxation might
help. It cannot apply the relaxation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from demo_scheduler.agents.llm import LLMClient


SYSTEM_PROMPT = """\
You are the Infeasibility Agent for the DEMO Pharma scheduler.

You receive: (a) the catalog row excerpts for any constraints involved
in the conflict, and (b) the failure context (status, infeasible weeks,
KPI snapshot). Output a SHORT narration (≤6 bullets) that:

  1. names the conflicting rules in plain language;
  2. quantifies the shortfall (hours over budget, units unfilled, etc.);
  3. proposes ONE relaxation by catalog id + parameter (e.g.
     "raise C-008b.team_hours_per_week from 120 to 132").

You cannot apply the relaxation — your output is a recommendation.
"""


@dataclass
class InfeasibilityNarration:
    text: str
    suggested_relaxations: list[dict]


def narrate(
    context: dict,        # {status, infeasible_weeks, kpis?, catalog_excerpts: [...]}
    llm: LLMClient,
) -> InfeasibilityNarration:
    user_msg = json.dumps(context, indent=2, default=str)
    resp = llm.complete(system=SYSTEM_PROMPT, user=user_msg, max_tokens=500)
    return InfeasibilityNarration(text=resp.text.strip(), suggested_relaxations=[])
