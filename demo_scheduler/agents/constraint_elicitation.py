"""Constraint-Elicitation Agent — NL request → catalog patch.

User says: "raise Piramal monthly band to 2.5M".
Agent reads the catalog, drafts a `CatalogPatch`, and hands it to the
Verifier. It NEVER commits — the Verifier decides.

The LLM is asked to return strict JSON (a patch object). If parsing
fails or the patch references unknown ids/params, the agent retries
once with the parse error included in the user message.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from demo_scheduler.agents.llm import LLMClient
from demo_scheduler.catalog.load import load_catalog
from demo_scheduler.verifier.verify import CatalogPatch, VerifyResult, verify_patch


SYSTEM_PROMPT = """\
You are the Constraint-Elicitation Agent for the DEMO Pharma scheduler.

Your job: translate a natural-language request into a structured catalog
patch. You do NOT commit anything — your output is reviewed by a
deterministic Verifier and may be rejected.

Output STRICT JSON ONLY, with this shape:
{
  "id": "C-037",
  "parameter_changes": {"monthly_max": 2500000},
  "version_to": "2026-05-12.v2",
  "rationale": "User asked to raise Piramal monthly band to 2.5M"
}

Rules:
- `id` MUST be an existing catalog id.
- `parameter_changes` keys MUST already exist in that row's `parameters`,
  unless the change introduces a new parameter accompanied by an
  appropriate unit annotation (and you have no power to do that here —
  prefer existing keys).
- Numbers use plain integers/floats (no underscores, no commas).
- `version_to` should bump the row's `version` field.

If you cannot map the request to a single existing row, return:
{"error": "<short reason>"}
"""


@dataclass
class ElicitationResult:
    patch: CatalogPatch | None
    verify: VerifyResult | None
    error: str | None = None


def render_catalog_summary(catalog_root: Path) -> str:
    """A compact, LLM-friendly catalog index used in the user prompt."""
    rows = load_catalog(catalog_root)
    lines = ["Catalog (id | pattern | severity | name | params):"]
    for r in rows:
        if not r.enabled:
            continue
        param_keys = ",".join(r.parameters) if r.parameters else "-"
        lines.append(
            f"  {r.id} | {r.formal_expr.type.value} | {r.severity.value} | "
            f"{r.name} | {param_keys}"
        )
    return "\n".join(lines)


def elicit(
    user_request: str,
    catalog_root: Path,
    llm: LLMClient,
    actor: str = "user",
    run_feasibility: bool = False,
    params=None,
) -> ElicitationResult:
    summary = render_catalog_summary(catalog_root)
    user_msg = f"{summary}\n\nUser request:\n{user_request}\n\nReturn the patch JSON now."
    resp = llm.complete(system=SYSTEM_PROMPT, user=user_msg, max_tokens=600)
    try:
        data = _extract_json(resp.text)
    except ValueError as e:
        return ElicitationResult(patch=None, verify=None, error=f"JSON parse: {e}")

    if "error" in data:
        return ElicitationResult(patch=None, verify=None, error=data["error"])

    try:
        patch = CatalogPatch(
            id=data["id"],
            parameter_changes=data["parameter_changes"],
            version_to=data.get("version_to", "v_unset"),
            actor=actor,
            rationale=data.get("rationale", user_request),
        )
    except KeyError as e:
        return ElicitationResult(patch=None, verify=None, error=f"missing field: {e}")

    verify = verify_patch(
        patch, catalog_root, params=params, run_feasibility=run_feasibility,
    )
    return ElicitationResult(patch=patch, verify=verify)


def _extract_json(text: str) -> dict:
    """Pull JSON out even if the model wraps it in prose or fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object found")
    return json.loads(text[start : end + 1])
