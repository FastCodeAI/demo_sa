"""Versioned constraint catalog (Phase 2b).

A constraint is a structured YAML row whose `formal_expr.type` selects a
pattern compiler. The catalog is the source of truth for what rules the
model encodes; numbers and parameters live inside the same row.
"""
