"""Supervisor — routes the user turn via LangGraph.

For a v2c MVP we keep the graph simple:

  user_turn
      │
      ▼
  classify_intent
      │
      ├── "edit_constraint"  → constraint_elicitation → verify → (await approval)
      ├── "run_scheduler"    → schedule_generation
      ├── "whatif"           → whatif (between two named runs)
      ├── "explain"          → explanation (with persona)
      └── "infeasibility"    → infeasibility (on the latest failed run)

Classification is rule-based first (keyword match), LLM-based fallback
when rules don't fire. Rule-based is deterministic and works without
API keys, which keeps the smoke tests cheap.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict

from demo_scheduler.agents.llm import LLMClient


Intent = Literal[
    "edit_constraint", "run_scheduler", "whatif", "explain",
    "infeasibility", "disruption", "unknown",
]


_INTENT_RULES: list[tuple[re.Pattern[str], Intent]] = [
    (re.compile(r"\b(raise|lower|change|set|update|edit)\b.*\b(band|cap|limit|threshold|constraint|rule|monthly)\b", re.IGNORECASE), "edit_constraint"),
    (re.compile(r"\b(re|run|solve|replan|generate)\b.*\b(plan|scheduler|schedule|solve|run)\b", re.IGNORECASE), "run_scheduler"),
    (re.compile(r"\bwhat[- ]?if\b|\bcompare\b|\bdiff\b", re.IGNORECASE), "whatif"),
    (re.compile(r"\bexplain\b|\bsummari[sz]e\b|\boverview\b", re.IGNORECASE), "explain"),
    (re.compile(r"\binfeasib(le|ility)\b|\bwhy.*fail\b|\bbroken\b", re.IGNORECASE), "infeasibility"),
    (re.compile(r"\bdisruption\b|\bdown\b|\bmachine.*broken\b|\boutage\b", re.IGNORECASE), "disruption"),
]


def classify(text: str, llm: LLMClient | None = None) -> Intent:
    for pat, intent in _INTENT_RULES:
        if pat.search(text):
            return intent
    if llm is None:
        return "unknown"
    resp = llm.complete(
        system="Classify the user's intent. Reply with one word from: "
               "edit_constraint, run_scheduler, whatif, explain, infeasibility, disruption, unknown.",
        user=text,
        max_tokens=10,
    )
    cand = resp.text.strip().lower().split()[0] if resp.text.strip() else "unknown"
    return cand if cand in _INTENT_RULES_LABELS else "unknown"  # type: ignore[return-value]


_INTENT_RULES_LABELS = {
    "edit_constraint", "run_scheduler", "whatif", "explain",
    "infeasibility", "disruption", "unknown",
}


@dataclass
class SupervisorTurn:
    user_text: str
    intent: Intent
    output: dict[str, Any] = field(default_factory=dict)


class GraphState(TypedDict, total=False):
    user_text: str
    intent: Intent
    output: dict[str, Any]
    llm: Any
    catalog_root: Path
    persona: str
    params: Any
    kpis_before: Path
    kpis_after: Path
    failure_context: dict[str, Any]


def _classify_node(state: GraphState) -> GraphState:
    intent = classify(state["user_text"], state.get("llm"))
    return {"intent": intent}


def _edit_constraint_node(state: GraphState) -> GraphState:
    from demo_scheduler.agents.constraint_elicitation import elicit
    result = elicit(
        user_request=state["user_text"],
        catalog_root=state["catalog_root"],
        llm=state["llm"],
    )
    return {"output": {"elicitation": result}}


def _run_scheduler_node(state: GraphState) -> GraphState:
    from demo_scheduler.agents.schedule_generation import generate_schedule
    if "params" not in state:
        return {"output": {"error": "run_scheduler requires `params` in state"}}
    res = generate_schedule(state["params"], state.get("catalog_root"))
    return {"output": {"schedule": res}}


def _whatif_node(state: GraphState) -> GraphState:
    from demo_scheduler.agents.whatif import whatif
    if "kpis_before" not in state or "kpis_after" not in state:
        return {"output": {"error": "whatif requires kpis_before and kpis_after paths"}}
    res = whatif(state["kpis_before"], state["kpis_after"], state["llm"])
    return {"output": {"whatif": res}}


def _explain_node(state: GraphState) -> GraphState:
    from demo_scheduler.agents.explanation import explain
    if "kpis_after" in state:
        import json
        kpis = json.loads(state["kpis_after"].read_text())
    else:
        kpis = state.get("output", {}).get("kpis", {})
    persona = state.get("persona", "production")
    res = explain(kpis=kpis, persona=persona, llm=state["llm"])  # type: ignore[arg-type]
    return {"output": {"explanation": res}}


def _infeasibility_node(state: GraphState) -> GraphState:
    from demo_scheduler.agents.infeasibility import narrate
    res = narrate(state.get("failure_context", {}), state["llm"])
    return {"output": {"infeasibility": res}}


def _unknown_node(state: GraphState) -> GraphState:
    return {"output": {"message": "intent unclear; rephrase or use a CLI subcommand"}}


def _route(state: GraphState) -> str:
    return {
        "edit_constraint": "edit",
        "run_scheduler":   "run",
        "whatif":          "whatif",
        "explain":         "explain",
        "infeasibility":   "infeasibility",
    }.get(state.get("intent", "unknown"), "unknown")


def build_graph():  # pragma: no cover - imported lazily
    from langgraph.graph import END, StateGraph
    g = StateGraph(GraphState)
    g.add_node("classify", _classify_node)
    g.add_node("edit", _edit_constraint_node)
    g.add_node("run", _run_scheduler_node)
    g.add_node("whatif", _whatif_node)
    g.add_node("explain", _explain_node)
    g.add_node("infeasibility", _infeasibility_node)
    g.add_node("unknown", _unknown_node)
    g.set_entry_point("classify")
    g.add_conditional_edges("classify", _route, {
        "edit": "edit", "run": "run", "whatif": "whatif",
        "explain": "explain", "infeasibility": "infeasibility",
        "unknown": "unknown",
    })
    for n in ("edit", "run", "whatif", "explain", "infeasibility", "unknown"):
        g.add_edge(n, END)
    return g.compile()


def run_turn(
    user_text: str,
    llm: LLMClient,
    catalog_root: Path,
    *,
    params=None,
    persona: str | None = None,
    kpis_before: Path | None = None,
    kpis_after: Path | None = None,
    failure_context: dict | None = None,
) -> SupervisorTurn:
    """Single-shot helper that bypasses LangGraph compilation when only
    one stage runs. Useful for the CLI and for tests."""
    intent = classify(user_text, llm if not isinstance(llm, type(None)) else None)
    state: GraphState = {
        "user_text": user_text, "intent": intent, "llm": llm,
        "catalog_root": catalog_root,
    }
    if params is not None: state["params"] = params
    if persona is not None: state["persona"] = persona
    if kpis_before is not None: state["kpis_before"] = kpis_before
    if kpis_after is not None: state["kpis_after"] = kpis_after
    if failure_context is not None: state["failure_context"] = failure_context

    handler = {
        "edit_constraint": _edit_constraint_node,
        "run_scheduler":   _run_scheduler_node,
        "whatif":          _whatif_node,
        "explain":         _explain_node,
        "infeasibility":   _infeasibility_node,
    }.get(intent, _unknown_node)
    out = handler(state)
    return SupervisorTurn(user_text=user_text, intent=intent, output=out.get("output", {}))
