"""Agent-layer tests using MockLLM — no network calls.

Covers:
  - Intent classification (rule-based + LLM fallback)
  - Constraint-Elicitation → Verifier handoff (incl. JSON parsing,
    approval-gate detection, rejection on bad parameter)
  - What-If diff arithmetic
  - Explanation persona dispatch
  - Supervisor routing
"""
from __future__ import annotations

import json
from pathlib import Path

from demo_scheduler.agents.constraint_elicitation import elicit
from demo_scheduler.agents.explanation import explain
from demo_scheduler.agents.llm import MockLLM
from demo_scheduler.agents.supervisor import classify, run_turn
from demo_scheduler.agents.whatif import diff_kpis, whatif

CATALOG_ROOT = Path(__file__).resolve().parents[2] / "catalog"


# ----- intent classification ---------------------------------------------

def test_classify_edit_constraint():
    assert classify("raise Piramal monthly band to 2.5M") == "edit_constraint"
    assert classify("Please update the Piramal cap") == "edit_constraint"


def test_classify_run_scheduler():
    assert classify("re-run the plan") == "run_scheduler"
    assert classify("replan the schedule") == "run_scheduler"


def test_classify_whatif_explain_infeasibility():
    assert classify("what-if we lowered the cap?") == "whatif"
    assert classify("explain the result to Sales") == "explain"
    assert classify("why was it infeasible?") == "infeasibility"


def test_classify_falls_back_to_llm():
    llm = MockLLM(responses=["whatif"])
    assert classify("foobar quux", llm) == "whatif"


# ----- constraint elicitation --------------------------------------------

def test_elicit_clean_patch_passes_verifier():
    llm = MockLLM(responses=[json.dumps({
        "id": "C-037",
        "parameter_changes": {"monthly_max": 2_200_000},
        "version_to": "2026-05-12.v2",
        "rationale": "Test ask",
    })])
    result = elicit("raise Piramal monthly band to 2.2M", CATALOG_ROOT, llm)
    assert result.patch is not None
    assert result.verify is not None
    assert result.verify.passed


def test_elicit_aggressive_patch_flags_approval_gate():
    llm = MockLLM(responses=[json.dumps({
        "id": "C-037",
        "parameter_changes": {"monthly_max": 3_000_000},
        "version_to": "2026-05-12.v3",
        "rationale": "Aggressive ask",
    })])
    result = elicit("raise Piramal to 3M", CATALOG_ROOT, llm)
    assert result.verify is not None
    assert result.verify.needs_approval


def test_elicit_handles_fenced_json():
    llm = MockLLM(responses=[
        "```json\n" + json.dumps({
            "id": "C-037", "parameter_changes": {"monthly_max": 2_100_000},
            "version_to": "v2", "rationale": "via fence",
        }) + "\n```"
    ])
    result = elicit("nudge Piramal", CATALOG_ROOT, llm)
    assert result.patch is not None


def test_elicit_handles_llm_error_response():
    llm = MockLLM(responses=[json.dumps({"error": "no matching row"})])
    result = elicit("change the colour of the sky", CATALOG_ROOT, llm)
    assert result.patch is None
    assert "no matching row" in (result.error or "")


# ----- whatif -------------------------------------------------------------

def test_diff_kpis_arithmetic():
    before = {"otif_pct": 95.0, "totals": {"packed": 1000, "unfilled": 100}}
    after =  {"otif_pct": 99.0, "totals": {"packed": 1090, "unfilled": 10}}
    d = diff_kpis(before, after)
    assert d["otif_pct"] == 4.0
    assert d["packed"] == 90
    assert d["unfilled"] == -90


def test_whatif_invokes_llm(tmp_path: Path):
    before = {"otif_pct": 95, "rated_otif_pct": 97, "vip_otif_pct": 100,
              "total_changeover_h": 14, "total_idle_h": 3300, "n_changeovers": 5,
              "totals": {"packed": 28_000_000, "unfilled": 2_000_000}, "solve_time_s": 600}
    after = {"otif_pct": 100, "rated_otif_pct": 100, "vip_otif_pct": 100,
             "total_changeover_h": 0, "total_idle_h": 3439, "n_changeovers": 0,
             "totals": {"packed": 30_131_922, "unfilled": 0}, "solve_time_s": 773}
    b = tmp_path / "b.json"; b.write_text(json.dumps(before))
    a = tmp_path / "a.json"; a.write_text(json.dumps(after))
    llm = MockLLM(responses=["• OTIF up 5 pts.\n• Unfilled cleared.\n• Changeover hours dropped."])
    r = whatif(b, a, llm)
    assert "OTIF" in r.narration
    assert r.delta["otif_pct"] == 5.0


# ----- explanation --------------------------------------------------------

def test_explain_routes_persona():
    llm = MockLLM(responses=["• Marchesini at 58% util.\n• Three changeovers in Q1."])
    r = explain(kpis={"otif_pct": 100}, persona="production", llm=llm)
    assert r.persona == "production"
    assert llm.calls[-1]["system"].lower().count("production") >= 1


# ----- supervisor end-to-end ---------------------------------------------

def test_supervisor_routes_edit_constraint():
    llm = MockLLM(responses=[json.dumps({
        "id": "C-037",
        "parameter_changes": {"monthly_max": 2_100_000},
        "version_to": "v2", "rationale": "user nudge",
    })])
    turn = run_turn("raise Piramal monthly band to 2.1M", llm, CATALOG_ROOT)
    assert turn.intent == "edit_constraint"
    assert turn.output["elicitation"].patch is not None


def test_supervisor_unknown_returns_message():
    llm = MockLLM(responses=["unknown"])
    turn = run_turn("xyzzy", llm, CATALOG_ROOT)
    assert turn.intent == "unknown"
    assert "message" in turn.output
