"""Verifier (Phase 2c) tests — deterministic checks on catalog patches.

The Verifier is the trust boundary between LLM agents and the catalog.
These tests cover schema/unit checks and the approval-gate detection
from `business_rules`; the feasibility check is exercised by
test_orchestrator.py via the standard end-to-end paths.
"""
from __future__ import annotations

from pathlib import Path

from demo_scheduler.verifier.verify import CatalogPatch, verify_patch

CATALOG_ROOT = Path(__file__).resolve().parents[2] / "catalog"


def test_verifier_accepts_clean_patch():
    """Bumping monthly_max to a legal value within rules passes."""
    patch = CatalogPatch(
        id="C-037",
        parameter_changes={"monthly_max": 2_200_000},
        version_to="2026-05-12.v2",
        actor="test",
        rationale="Sales adjustment, well under VP threshold",
    )
    result = verify_patch(patch, CATALOG_ROOT, run_feasibility=False)
    assert result.passed, [o.detail for o in result.outcomes if not o.passed]


def test_verifier_flags_approval_gate():
    """The C-037 rule 'requires Sales-VP approval' should be detected."""
    patch = CatalogPatch(
        id="C-037",
        parameter_changes={"monthly_max": 3_000_000},
        version_to="2026-05-12.v3",
        actor="test",
        rationale="Aggressive ask",
    )
    result = verify_patch(patch, CATALOG_ROOT, run_feasibility=False)
    # Approval gates are surfaced regardless of pass/fail.
    assert result.needs_approval, "Sales-VP approval rule should have fired"
    assert any("approval" in g.lower() for g in result.approval_gates)


def test_verifier_rejects_missing_unit():
    """Adding a numeric parameter without a unit annotation should fail."""
    patch = CatalogPatch(
        id="C-037",
        parameter_changes={"monthly_min": 1_900_000, "fudge_factor": 0.05},
        version_to="2026-05-12.v4",
        actor="test",
        rationale="Introducing un-documented param",
    )
    result = verify_patch(patch, CATALOG_ROOT, run_feasibility=False)
    assert not result.passed
    failed_names = {o.name for o in result.outcomes if not o.passed}
    assert "unit" in failed_names


def test_verifier_rejects_unknown_catalog_id():
    patch = CatalogPatch(
        id="C-999",
        parameter_changes={"x": 1},
        version_to="v1",
    )
    result = verify_patch(patch, CATALOG_ROOT, run_feasibility=False)
    assert not result.passed
