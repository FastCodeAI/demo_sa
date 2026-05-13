"""Verifier — the deterministic trust boundary between LLM agents and
the catalog / plan / SAP write-back (Phase 2c).

There is no LLM inside the Verifier. Its job is to take an opaque
agent-proposed change (catalog patch, plan commit, SAP write request)
and decide pass/fail using deterministic checks + a warm-cached
feasibility probe. The agent layer is opinionated; the Verifier is the
load-bearing correctness check.
"""
