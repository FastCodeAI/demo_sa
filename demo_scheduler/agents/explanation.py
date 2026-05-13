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
