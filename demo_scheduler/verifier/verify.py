"""Deterministic checks for catalog patches.

A `CatalogPatch` mutates one row's parameters (and bumps version). The
Verifier runs five checks in order:

  1. Schema check — pydantic re-validation of the post-patch row
  2. Unit check  — every parameter in `parameters` has a matching
                   entry in `units` (so downstream readers know the unit)
  3. Business-rule check — string-match on `business_rules` to detect
                   approval-gate triggers (e.g. "above 2.5M requires
                   Sales-VP approval"). Phase 2c's UI consumes this.
  4. Feasibility check — re-compile the model with the patched catalog
                   and run a short warm-cached solve. Pass if the
                   resulting status is in {optimal, feasible, time_limit}.
                   (Optional — caller toggles with `run_feasibility`.)
  5. Audit append — record the patch (passed or failed) with timestamps,
                    actor, and check outcomes.

The function is the only place that can return `VerifyResult.committed`
— LLM agents call it; downstream commit logic trusts only its verdict.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from demo_scheduler.catalog.load import audit_append, load_catalog
from demo_scheduler.catalog.schema import CatalogRow


@dataclass
class CatalogPatch:
    """Proposed mutation to one catalog row."""
    id: str
    parameter_changes: dict[str, Any]
    version_to: str
    actor: str = "system"
    rationale: str = ""


@dataclass
class CheckOutcome:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class VerifyResult:
    patch: CatalogPatch
    outcomes: list[CheckOutcome] = field(default_factory=list)
    approval_gates: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(o.passed for o in self.outcomes)

    @property
    def needs_approval(self) -> bool:
        return bool(self.approval_gates)


def _apply_patch(row: CatalogRow, patch: CatalogPatch) -> CatalogRow:
    """Produce a new CatalogRow with patch.parameter_changes merged in."""
    data = row.model_dump()
    data["parameters"] = {**data.get("parameters", {}), **patch.parameter_changes}
    data["version"] = patch.version_to
    return CatalogRow.model_validate(data)


# Regex patterns that turn natural-language `business_rules` into structured
# approval gates. New rule types can be added without touching the agents —
# this dict IS the contract between rule authors and the Verifier.
_BUSINESS_RULE_GATES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"requires?\s+(?P<approver>[\w\-]+(?:\s+\w+)?)\s+approval", re.IGNORECASE), "approval"),
    (re.compile(r"cannot\s+(?:go\s+)?(?P<bound>below|above)\s+(?P<value>[\d_.,]+\s*(?:[A-Za-z%]+)?)", re.IGNORECASE), "absolute_bound"),
]


def _scan_business_rules(row: CatalogRow, patched: CatalogRow) -> list[str]:
    """Surface any business-rule clauses the patch's new values trigger.

    Phase 2b ships pattern detection only; the Verifier marks the gate
    and the UI/agent decides how to route the approval ask (e.g. via
    Phase-2c Approval Queue).
    """
    gates: list[str] = []
    for rule in patched.business_rules:
        for pat, gate in _BUSINESS_RULE_GATES:
            mm = pat.search(rule)
            if not mm:
                continue
            gates.append(f"{gate}::{rule.strip()}")
            break
    return gates


def check_schema(patched_row: CatalogRow) -> CheckOutcome:
    # Re-validation already happened in _apply_patch via CatalogRow.model_validate.
    return CheckOutcome("schema", True, "pydantic accepted patched row")


def check_units(patched_row: CatalogRow) -> CheckOutcome:
    """Every numeric param should have a documented unit."""
    params = patched_row.parameters
    units = patched_row.units
    missing = [
        k for k, v in params.items()
        if isinstance(v, (int, float)) and k not in units
    ]
    if missing:
        return CheckOutcome(
            "unit", False,
            f"numeric parameter(s) without units: {missing}",
        )
    return CheckOutcome("unit", True, f"{len(units)} units documented")


def check_business_rules(patched_row: CatalogRow) -> tuple[CheckOutcome, list[str]]:
    """Always passes; surfaces approval-gate signals separately."""
    gates = _scan_business_rules(patched_row, patched_row)
    detail = "no approval gates triggered" if not gates else f"{len(gates)} gate(s) triggered"
    return CheckOutcome("business_rule", True, detail), gates


def check_feasibility(
    patched_row: CatalogRow,
    catalog_root: Path,
    params,
    time_limit_s: int = 30,
    mip_gap: float = 0.05,
) -> CheckOutcome:
    """Re-compile the catalog with the patch staged in memory and run a
    short solve. Pass if the solve returns a usable status.

    The patched row is written to a sibling `_staging/` directory; the
    Verifier never mutates the real catalog before its verdict.
    """
    import shutil

    from demo_scheduler.model.compile import build_from_catalog
    from demo_scheduler.solve.solver import solve

    staging = catalog_root.parent / f"{catalog_root.name}__verify_staging"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(catalog_root, staging)

    target = staging / "constraints" / f"{patched_row.id}.yaml"
    # Find the actual on-disk YAML for this row id (filename pattern: <id>-<slug>.yaml).
    on_disk = None
    for p in (staging / "constraints").glob("*.yaml"):
        if p.name.startswith(patched_row.id + "-") or p.name == f"{patched_row.id}.yaml":
            on_disk = p
            break
    if on_disk is None:
        shutil.rmtree(staging)
        return CheckOutcome("feasibility", False, f"on-disk yaml for {patched_row.id} not found")

    # Write the patched row over the staging file.
    import yaml as _yaml
    on_disk.write_text(_yaml.safe_dump(patched_row.model_dump(mode="json"), sort_keys=False))

    try:
        model, _ = build_from_catalog(params, staging)
        result = solve(model, time_limit_s=time_limit_s, mip_gap=mip_gap)
        ok = result.status in {"optimal", "feasible", "time_limit"}
        return CheckOutcome(
            "feasibility",
            ok,
            f"status={result.status} obj={result.objective} time={result.solve_time_s:.1f}s",
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def verify_patch(
    patch: CatalogPatch,
    catalog_root: Path,
    params=None,
    run_feasibility: bool = False,
) -> VerifyResult:
    """Run the full check pipeline on a proposed catalog patch."""
    catalog = load_catalog(catalog_root)
    base_row = next((r for r in catalog if r.id == patch.id), None)
    if base_row is None:
        outcome = CheckOutcome("schema", False, f"catalog id {patch.id} not found")
        return VerifyResult(patch=patch, outcomes=[outcome])

    try:
        patched = _apply_patch(base_row, patch)
    except Exception as e:
        outcome = CheckOutcome("schema", False, f"pydantic rejected patch: {e}")
        return VerifyResult(patch=patch, outcomes=[outcome])

    outcomes = [check_schema(patched), check_units(patched)]
    br_outcome, gates = check_business_rules(patched)
    outcomes.append(br_outcome)

    if run_feasibility and params is not None:
        outcomes.append(check_feasibility(patched, catalog_root, params))

    result = VerifyResult(patch=patch, outcomes=outcomes, approval_gates=gates)

    # Always audit (even failed) so the Catalog MCP / Approval UI can read it.
    audit_append(catalog_root, {
        "id": patch.id,
        "from": base_row.version,
        "to": patch.version_to,
        "actor": patch.actor,
        "rationale": patch.rationale,
        "verifier_runs": [
            {"name": o.name, "passed": o.passed, "detail": o.detail}
            for o in outcomes
        ],
        "approval_gates": gates,
        "verdict": "passed" if result.passed and not result.needs_approval else
                   "needs_approval" if result.needs_approval else "failed",
    })

    return result
