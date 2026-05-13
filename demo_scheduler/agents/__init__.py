"""LangGraph agent loop (Phase 2c).

Seven agents at three stages, plus a Supervisor that routes user intent
to the right sub-agent.

PRE-SOLVE
  Constraint-Elicitation Agent — NL request → catalog patch (verified)
  Schedule-Generation Agent    — catalog version → solver invocation

POST-SOLVE
  Infeasibility Agent  — IIS → NL narration of conflicting rules
  What-If Agent        — two plans → KPI delta narration
  Explanation Agent    — committed plan → persona-tuned text

LIVE
  Disruption-Response Agent — telemetry + current plan → LNS repair

ORCHESTRATION
  Supervisor — routes the user turn to the right sub-agent

Every agent reads/writes structured Python (catalog patches, plan refs,
KPI dicts). The LLM is only invoked through `agents.llm.LLMClient` which
is mockable for tests so CI never calls the real API.
"""
