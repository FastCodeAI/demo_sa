"""Catalog loader + lightweight auditor.

`load_catalog(root)` reads every `*.yaml` under `<root>/constraints/` and
returns a list of validated `CatalogRow`s. Duplicate ids raise.

`audit_append(root, change)` writes one JSONL line into
`<root>/audit/<id>-history.jsonl` for each catalog mutation. Phase 2c's
Catalog MCP server will wrap this primitive.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import yaml

from demo_scheduler.catalog.schema import CatalogRow


def load_catalog(root: Path) -> list[CatalogRow]:
    constraints_dir = root / "constraints"
    if not constraints_dir.exists():
        raise FileNotFoundError(f"catalog/constraints not found at {constraints_dir}")

    rows: dict[str, CatalogRow] = {}
    for path in sorted(constraints_dir.glob("*.yaml")):
        with path.open() as f:
            data = yaml.safe_load(f)
        if data is None:
            continue
        row = CatalogRow.model_validate(data)
        if row.id in rows:
            raise ValueError(f"duplicate catalog id {row.id} (second occurrence: {path})")
        rows[row.id] = row
    return list(rows.values())


def get_row(catalog: list[CatalogRow], id: str) -> CatalogRow:
    for r in catalog:
        if r.id == id:
            return r
    raise KeyError(f"catalog id {id} not found")


def audit_append(root: Path, change: dict) -> None:
    """Append a JSONL audit record. `change` should at minimum carry
    `id`, `from`, `to`, `actor`, `rationale`.
    """
    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    rid = change.get("id", "unknown")
    path = audit_dir / f"{rid}-history.jsonl"
    record = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(), **change}
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")
