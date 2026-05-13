"""Catalog-driven model assembly (Phase 2b).

`build_from_catalog(params, catalog_root)` reads `<root>/constraints/*.yaml`,
strips the corresponding hardcoded blocks from `build.py`'s output, and
re-installs them via the compiler plugins in `model/compilers/`.

The end result is a Pyomo `ConcreteModel` that's structurally identical
to `build_model()` but whose enabled rules come from data, not from a
Python function body. This is the foundation for Phase 2c (an LLM agent
edits catalog YAML and re-fires the solver without touching code).
"""
from __future__ import annotations

from pathlib import Path

import pyomo.environ as pyo

from demo_scheduler.catalog.load import load_catalog
from demo_scheduler.catalog.schema import CatalogRow
from demo_scheduler.data.synthesize import Parameters
from demo_scheduler.model.build import build_model
from demo_scheduler.model.compilers import get_compiler


# Mapping from catalog id -> name of the hardcoded block in build.py
# that this catalog row REPLACES. When the row is enabled, the hardcoded
# block is skipped and the compiler installs the catalog version instead.
CATALOG_ID_TO_BUILD_BLOCK = {
    "C-001": "one_format",
    "C-002": "fulfilment",
    "C-003": "x_y_link",
    "C-004": "vol_link",
    "C-005": "org_no_split",
    "C-007": "farcon_div_xor",
    "C-008a": "changeover_link",
    "C-008b": "co_team_hours",
    "C-009": "prod_pack_lag",
    "C-012": "prod_total",
    "C-015": "capacity",
    "C-037": "piramal_band",
}


def build_from_catalog(
    params: Parameters,
    catalog_root: Path,
) -> tuple[pyo.ConcreteModel, list[CatalogRow]]:
    """Assemble the model from the catalog at `catalog_root`."""
    catalog = load_catalog(catalog_root)

    # Determine which hardcoded blocks to skip.
    skip: set[str] = set()
    for row in catalog:
        if not row.enabled:
            continue
        block = CATALOG_ID_TO_BUILD_BLOCK.get(row.id)
        if block:
            skip.add(block)

    model = build_model(params, skip=skip)

    # Compile each enabled catalog row on top.
    for row in catalog:
        if not row.enabled:
            continue
        # Only patterns we know how to compile and that target MILP master.
        if row.solver_layer.value not in ("MILP_master", "both"):
            continue
        compiler = get_compiler(row.formal_expr.type.value)
        compiler(row, model, params)

    return model, catalog
