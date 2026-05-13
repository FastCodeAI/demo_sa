"""Pydantic schema for catalog rows (Phase 2b).

A constraint is a YAML file under `catalog/constraints/`. Each row carries:

  * identity        — id, name, req_ref, category
  * compile target  — formal_expr (pattern + parameters) + solver_layer
  * enforcement     — severity (hard | soft) + optional soft_handling
  * guards          — units + business_rules (for the Verifier)
  * provenance      — owner, version, audit_log_ref

The Verifier (Phase 2c) reads `units` + `business_rules` to gate edits;
the compiler (Phase 2b, `model/compile.py`) reads `formal_expr` and
dispatches to a pattern function in `model/compilers/`.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Severity(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class SolverLayer(str, Enum):
    MILP_MASTER = "MILP_master"
    CP_SAT_SEQ = "CP_SAT_seq"
    BOTH = "both"


class PatternType(str, Enum):
    """All `formal_expr.type` values the compiler can dispatch on."""
    SUM_LE = "sum_le"
    BALANCE = "balance"
    SPARSE_SET = "sparse_set"
    RANGE_ELIGIBILITY = "range_eligibility"
    SINGLE_PLACEMENT = "single_placement"
    TWO_SIDED_BOUND = "two_sided_bound"
    MUTEX = "mutex"
    RESOURCE_NO_OVERLAP = "resource_no_overlap"
    PRECEDENCE_LAG = "precedence_lag"
    TIME_WINDOW = "time_window"
    FORBIDDEN_ASSIGNMENTS = "forbidden_assignments"
    CAPACITY_EQ = "capacity_eq"
    AGGREGATED_DEMAND = "aggregated_demand"
    AGGREGATED_BALANCE = "aggregated_balance"
    DEVIATION_PENALTY = "deviation_penalty"
    LEX_MIN = "lex_min"
    RELAXATION_MARKER = "relaxation_marker"
    BOUND_LINEAR = "bound_linear"
    INDICATOR_LINK = "indicator_link"
    WEIGHTED_SUM_LE = "weighted_sum_le"
    CUMULATIVE_LAG = "cumulative_lag"
    DISABLED = "disabled"


class FormalExpr(BaseModel):
    model_config = ConfigDict(extra="allow")  # patterns carry pattern-specific fields
    type: PatternType
    description: str | None = None


class SoftHandling(BaseModel):
    under_var: str | None = None
    over_var: str | None = None
    slack_var: str | None = None
    penalty_weight: str = "objective_weights.late"  # dotted reference into config


class CatalogRow(BaseModel):
    id: str
    req_ref: str | None = None
    category: str
    name: str
    description: str = ""

    severity: Severity
    solver_layer: SolverLayer
    enabled: bool = True

    formal_expr: FormalExpr
    parameters: dict[str, Any] = Field(default_factory=dict)

    soft_handling: SoftHandling | None = None

    units: dict[str, str] = Field(default_factory=dict)
    business_rules: list[str] = Field(default_factory=list)

    owner: str | None = None
    version: str
    verifier_state: str = "unverified"   # passed | failed | unverified
    audit_log_ref: str | None = None
