"""Explanation Agent — KPIs + persona → tuned narration.

Three personas are supported:

  sales       — talks about customer-level fulfilment, late shipments,
                Piramal band coverage
  production  — talks about machine utilisation, format changes,
                shift impact, the Farcon/Dividella mutex
  compliance  — talks about catalog audit trail, severity of any soft
                constraint violations, approval gates triggered
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from demo_scheduler.agents.llm import LLMClient


Persona = Literal["sales", "production", "compliance"]


_PERSONA_FOCUS = {
    "sales":      "OTIF by customer, VIP coverage, Piramal monthly band, unfilled shortlist",
    "production": "machine utilisation, format-change count, idle hours, Farcon vs Dividella",
    "compliance": "soft-constraint violations, catalog version, approval gates, audit trail",
}


SYSTEM_TEMPLATE = """\
You are the Explanation Agent for the DEMO Pharma scheduler, writing for
a {persona} stakeholder. Focus on: {focus}.

Output 4-7 short bullets. No jargon, no constraint ids. End with one
"What this means for you" sentence.
"""


@dataclass
class ExplanationOutput:
    persona: Persona
    text: str


def explain(
    kpis: dict,
    persona: Persona,
    llm: LLMClient,
    extras: dict | None = None,
) -> ExplanationOutput:
    payload = {"kpis": kpis}
    if extras:
        payload.update(extras)
    system = SYSTEM_TEMPLATE.format(persona=persona, focus=_PERSONA_FOCUS[persona])
    user = "Here are the KPIs and any extras:\n" + json.dumps(payload, indent=2, default=str)
    resp = llm.complete(system=system, user=user, max_tokens=450)
    return ExplanationOutput(persona=persona, text=resp.text.strip())


UNFILLED_SYSTEM = """\
You are the Explanation Agent for the DEMO Pharma scheduler. The user
wants a plain-English answer to: why are some orders unfilled?

You are given a grounded report computed from the actual plan — every
number is real. Do NOT invent orders, customers, or constraints.

Write 4–6 short bullets that:
  - State the headline (n unfilled / fill rate %).
  - Name the dominant binding reason(s) using everyday words (capacity,
    eligible machines, Piramal monthly cap, no-split customer).
  - Cite the largest 1–3 affected orders by customer + format + units.
  - End with one bullet of "what would help" (e.g. raise the Piramal
    band, allow splitting for org X, add throughput on format F).

Do not mention constraint ids (C-002, C-037, …). Speak to a planner.
"""


def explain_unfilled(report: dict, llm: LLMClient) -> ExplanationOutput:
    """Narrate the binding-cause report produced by `build_unfilled_report`.

    Returns persona='production' since the audience is the planner.
    """
    if report["summary"]["n_unfilled"] == 0:
        return ExplanationOutput(
            persona="production",
            text="All orders are filled in the current plan — no unfilled demand to explain.",
        )
    user = (
        "Grounded unfilled-orders report:\n"
        + json.dumps(report, indent=2, default=str)
        + "\n\nWrite the explanation now."
    )
    resp = llm.complete(system=UNFILLED_SYSTEM, user=user, max_tokens=450)
    return ExplanationOutput(persona="production", text=resp.text.strip())
