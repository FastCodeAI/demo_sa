"""Pattern compiler plugins (one per `formal_expr.type`).

A compiler receives:
  * the `CatalogRow` being instantiated
  * the live `pyo.ConcreteModel` (variables already declared by build.py)
  * the `Parameters` snapshot

…and is responsible for adding the corresponding Pyomo constraint block.

Register a new pattern with the `@register("<pattern_name>")` decorator.
"""
from __future__ import annotations

from collections.abc import Callable

import pyomo.environ as pyo

from demo_scheduler.catalog.schema import CatalogRow
from demo_scheduler.data.synthesize import Parameters


Compiler = Callable[[CatalogRow, pyo.ConcreteModel, Parameters], None]

_REGISTRY: dict[str, Compiler] = {}


def register(pattern: str) -> Callable[[Compiler], Compiler]:
    def deco(fn: Compiler) -> Compiler:
        _REGISTRY[pattern] = fn
        return fn
    return deco


def get_compiler(pattern: str) -> Compiler:
    if pattern not in _REGISTRY:
        raise KeyError(f"no compiler registered for pattern: {pattern}")
    return _REGISTRY[pattern]


def available_patterns() -> list[str]:
    return sorted(_REGISTRY)


# Side-effect import: every compiler module registers via @register on import.
from demo_scheduler.model.compilers import patterns  # noqa: E402, F401
