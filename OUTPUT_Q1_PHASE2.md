# Q1 2025 — Phase 2 Complete: Pipeline, Components, and Agent Loop

**Generated:** 2026-05-12, after Phase 2a + 2b + 2c
**Outputs of record:** `outputs/q1/{plan.json, plan.csv, gantt.png, kpis.json}` (default path) and `outputs/q1_catalog/...` (catalog-driven path)
**Tests:** 30 fast tests, ~16 s; plus a 2-min Q1 smoke test

> Companion to `OUTPUT_Q1.md` (the Phase-1 report). That file remains
> the canonical "what was solved and how" for the bare MILP. **This
> file is the post-Phase-2 view**: pipeline diagram, component
> contracts, MILP↔CP-SAT split, and how the agent loop verifies and
> reasons about constraint edits.

---

## 0. What's new since Phase 1

Phase 1 shipped a single-tier MILP that hardcoded 15 + 3 constraints
in Python. Phase 2 split it three ways:

| Sub-phase | What it adds | Headline result |
|---|---|---|
| **2a** | CP-SAT sequencing layer on top of MILP, with retry-cut loop | Q1 OTIF **95 % → 100 %**, 0 infeasible weeks, 613 minute-level intervals |
| **2b** | Constraint catalog — every rule is now a versioned YAML row | 16 catalog rows (12 enabled, 4 vacuous/CP-SAT-layer), 10 pattern compilers + 4 stubs; Q1 `--use-catalog` = **98.8 % OTIF** |
| **2c** | Deterministic Verifier + 7 LangGraph agents (LLM-backed, OpenAI or Anthropic) | NL-driven catalog edits flow through schema/unit/business-rule checks; live `gpt-4o-mini` end-to-end demo works |

Every Phase-2 deliverable is unit-tested; the agent layer is fully
testable without API calls via `MockLLM`.

---

## 1. The full-scale pipeline (one diagram)

```
                   ┌──────────────────────────────────────────────────────┐
                   │  USER / ANALYST                                      │
                   │  CLI: `demo_scheduler solve | chat | whatif`         │
                   │  Reads:  plan.json, gantt.png, kpis.json             │
                   │  Says:   "raise Piramal monthly band to 2.5M"        │
                   └─────────────────────┬────────────────────────────────┘
                                         │
                                         ▼
                   ┌──────────────────────────────────────────────────────┐
                   │  AGENT LAYER (Phase 2c)                              │
                   │                                                      │
                   │   Supervisor (LangGraph)                             │
                   │   ├─ classify intent (rule-based + LLM fallback)     │
                   │   └─ dispatch to one of 6 sub-agents:                │
                   │      1. Constraint-Elicitation  (NL → patch)         │
                   │      2. Schedule-Generation     (catalog → solver)   │
                   │      3. Infeasibility           (IIS → narration)    │
                   │      4. What-If                 (KPI diff)           │
                   │      5. Explanation             (persona views)      │
                   │      6. Disruption-Response     (event → repair)     │
                   │                                                      │
                   │   LLMClient backend (auto-selected from .env):       │
                   │     OpenAILLM  (gpt-4o-mini)                         │
                   │     AnthropicLLM (claude-haiku-4-5 + prompt cache)   │
                   │     MockLLM (tests / demos, no API calls)            │
                   └────────────────────┬─────────────────────────────────┘
                                        │  CatalogPatch object
                                        ▼
                   ╔══════════════════════════════════════════════════════╗
                   ║  VERIFIER (Phase 2c)  —  deterministic, no LLM       ║
                   ║   1. schema       (pydantic re-validation)           ║
                   ║   2. unit         (every number has a unit)          ║
                   ║   3. business     (regex scan for approval gates)    ║
                   ║   4. feasibility  (warm-cached solve on staged YAML) ║
                   ║   5. audit-log    (append JSONL no matter what)      ║
                   ╚════════════════════╤═════════════════════════════════╝
                                        │  pass | needs_approval | fail
                                        ▼
                   ┌──────────────────────────────────────────────────────┐
                   │  CATALOG (Phase 2b)                                  │
                   │  catalog/constraints/*.yaml — 16 rows, versioned     │
                   │  catalog/audit/<id>-history.jsonl                    │
                   └────────────────────┬─────────────────────────────────┘
                                        │  catalog rows
                                        ▼
                   ┌──────────────────────────────────────────────────────┐
                   │  COMPILER (Phase 2b)                                 │
                   │  model/compile.py walks the catalog;                 │
                   │  model/compilers/patterns.py dispatches by           │
                   │  formal_expr.type → 10 pattern functions.            │
                   │  Hardcoded blocks in model/build.py are SKIPPED      │
                   │  for any catalog row that replaces them.             │
                   └────────────────────┬─────────────────────────────────┘
                                        │  Pyomo ConcreteModel (vars + constraints)
                                        ▼
                   ┌──────────────────────────────────────────────────────┐
                   │  ORCHESTRATOR (Phase 2a)                             │
                   │  solve/orchestrator.py:                              │
                   │   • Tier-2 MILP via HiGHS  (assignment + bands)      │
                   │   • Tier-3 CP-SAT per week (sequencing + no-overlap) │
                   │   • Cut-loop retry if a week is CP-SAT-infeasible    │
                   │     (gentle 10% shave of co_team hours cap)          │
                   └────────────────────┬─────────────────────────────────┘
                                        │  Plan dataclass
                                        ▼
                   ┌──────────────────────────────────────────────────────┐
                   │  OUTPUTS                                             │
                   │  plan.json   — orders, placements, sequences, prod   │
                   │  plan.csv    — order-by-placement                    │
                   │  gantt.png   — minute-level if CP-SAT ran            │
                   │  kpis.json   — OTIF, util, bands, cpsat metadata     │
                   └──────────────────────────────────────────────────────┘
```

Read the diagram top-to-bottom for a *new* plan ("solve" path) and
bottom-to-top for *follow-up reasoning* ("chat" path — Verifier
reads the catalog, re-stages, and the orchestrator re-solves).

---

## 2. Component-by-component contracts

Every box in the diagram corresponds to one Python module. Each one
has a typed input/output contract — the table below is the canonical
inventory.

### 2.1 Data layer

| Module | Reads | Writes | Notes |
|---|---|---|---|
| `data/load_excel.py` | `Packaging Ampoules.xlsx` | `RawData(orders, ranking, full_demand)` | Sheet-aware loader; quarter filter; BO 2024 carry on Q1 |
| `data/canonicalize.py` | column strings | canonical strings | Normalises customer aliases (Pharmathen, UK Trotwood, IDA Foundation), format codes (`0002ml` → `2ml`), machine names |
| `data/synthesize.py` | `RawData` + `Config` | `Parameters` dataclass | Fabricates the placeholder inputs (throughput, changeover matrix, calendar) per `docs/MODEL_SCOPING.md` §9 |
| `config/schema.py`, `config/load.py` | `configs/defaults.yaml` | `Config` (pydantic) | Validates the user-tunable knobs (weights, calendar, machines, bands) |

### 2.2 Catalog layer (Phase 2b)

| Module | Reads | Writes | Notes |
|---|---|---|---|
| `catalog/schema.py` | — | pydantic types | `CatalogRow`, `Severity` (hard/soft), `SolverLayer` (MILP_master/CP_SAT_seq/both), `PatternType` (20 enum values) |
| `catalog/load.py` | `catalog/constraints/*.yaml` | `list[CatalogRow]` | Validates each row; duplicate ids raise |
| `model/compilers/patterns.py` | one `CatalogRow` + the live Pyomo `ConcreteModel` + `Parameters` | adds constraint blocks to the model | One function per `formal_expr.type`; registered via `@register("...")` |
| `model/compile.py` | `Parameters`, catalog root path | `Pyomo ConcreteModel` + the catalog rows | Walks rows, decides which hardcoded blocks in `build.py` to skip, dispatches the rest to compilers |

**Catalog inventory (current state):**

| ID | Pattern | Layer | Sev | Status |
|---|---|---|---|---|
| C-001 | `sum_le` | MILP_master | hard | enabled |
| C-002 | `balance` | MILP_master | hard | enabled |
| C-003 | `sparse_set` | MILP_master | hard | enabled |
| C-004 | `bound_linear` | MILP_master | hard | enabled |
| C-005 | `single_placement` | MILP_master | hard | enabled |
| C-007 | `mutex` | MILP_master | hard | enabled |
| C-008 | `resource_no_overlap` | CP_SAT_seq | hard | enabled (sequencing layer) |
| C-008a | `indicator_link` | MILP_master | hard | enabled |
| C-008b | `weighted_sum_le` | MILP_master | hard | enabled |
| C-009 | `cumulative_lag` | MILP_master | hard | enabled |
| C-011 | `disabled` | MILP_master | hard | **disabled** (no per-line data) |
| C-012 | `aggregated_balance` | MILP_master | hard | enabled |
| C-013 | `time_window` | CP_SAT_seq | hard | **disabled** (vacuous on 13-wk horizon) |
| C-014 | `forbidden_assignments` | CP_SAT_seq | hard | **disabled** (no EVATON SKUs) |
| C-015 | `capacity_eq` | MILP_master | hard | enabled |
| C-037 | `two_sided_bound` | MILP_master | soft | enabled |

### 2.3 Solver layer

| Module | Reads | Writes | Notes |
|---|---|---|---|
| `model/build.py` | `Parameters`, optional `skip={...}` set | `Pyomo ConcreteModel` | Declares all variables (sparse sets); installs only the hardcoded blocks NOT replaced by the catalog |
| `model/tier3_cpsat.py` | `WeekInput` (per-week MILP slice + transitions) | `WeekSchedule` (minute-level `IntervalSpec`s) | Builds + solves one CP-SAT model per week using `add_no_overlap` |
| `solve/solver.py` | Pyomo model | `SolveResult(status, objective, time)` | HiGHS via `appsi`; 10-min default time limit, 1 % MIP gap |
| `solve/solver_cpsat.py` | solved Pyomo model + `Parameters` | `SequenceResult(schedules, infeasible_weeks, total_time)` | Reads `vol_pack`/`y_pack`/`co_pack` from MILP, runs CP-SAT for every week, adds a 300-sec capacity headroom buffer |
| `solve/orchestrator.py` | `Parameters`, optional catalog root | `Plan + RunInfo` | The driver: MILP → CP-SAT → (cut + retry) ×≤2 |
| `solve/extract.py` | solved Pyomo model + (optional) sequence intervals | `Plan` dataclass | Order assignments, machine-weeks, changeovers, monthly Piramal, production needs, minute-level sequence |

### 2.4 Verifier (Phase 2c)

| Module | Reads | Writes | Notes |
|---|---|---|---|
| `verifier/verify.py` | `CatalogPatch` + catalog root | `VerifyResult(outcomes, approval_gates, passed)` | 5 checks: schema, unit, business-rule scan, optional feasibility probe, audit append. **No LLM.** |

### 2.5 Agents (Phase 2c)

| Module | Reads | Writes | LLM call? |
|---|---|---|---|
| `agents/llm.py` | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` (via `.env`) | `LLMClient` instances + `default_llm()` | n/a |
| `agents/supervisor.py` | user text + `LLMClient` + catalog root | `SupervisorTurn(intent, output)` | intent classification only (with rule-based pre-pass) |
| `agents/constraint_elicitation.py` | user request, catalog index | `ElicitationResult(patch, VerifyResult)` | one call — strict JSON output |
| `agents/schedule_generation.py` | `Parameters`, catalog root | `ScheduleResult(plan, info)` | no LLM (purely calls the orchestrator) |
| `agents/whatif.py` | two `kpis.json` files | `WhatIfResult(narration, delta)` | one call — 3-5 bullet narration of the KPI delta |
| `agents/infeasibility.py` | failure context dict | `InfeasibilityNarration(text, suggested_relaxations)` | one call — names conflicting rules, proposes a relaxation |
| `agents/explanation.py` | `kpis`, persona ∈ {sales, production, compliance} | `ExplanationOutput(text)` | one call — persona-tuned summary |
| `agents/disruption.py` | `DisruptionEvent`, current plan | `RepairPatch(affected_cells, rationale)` | no LLM in stub (Phase-3 will add LNS) |

---

## 3. Inputs and outputs (end-to-end table)

| Pipeline step | Reads (input artefacts) | Writes (output artefacts) |
|---|---|---|
| **Excel load** | `Packaging Ampoules.xlsx` (1,824 rows) | `RawData(orders, ranking)` in-memory |
| **Canonicalise + filter** | `RawData` + quarter | `RawData` with normalised columns, 608 active rows for Q1+BO |
| **Synthesize** | `RawData` + `Config` | `Parameters` (sets, demands, eligibility, throughput, changeover matrix, calendar) |
| **Load catalog** | `catalog/constraints/*.yaml` (16 files) | `list[CatalogRow]` |
| **Compile model** | `Parameters` + catalog rows | Pyomo `ConcreteModel` with ~23 k binaries + ~26 k continuous vars + ~25 k constraints |
| **Tier-2 MILP solve** | Pyomo model | `SolveResult` (`status, objective, time`) + per-variable values in the model |
| **Read MILP slice (per week)** | solved model + `Parameters` | `WeekInput` with `assignments_by_machine`, `transitions_seconds`, `avail_seconds` |
| **Tier-3 CP-SAT solve (per week)** | `WeekInput` | `WeekSchedule(intervals, status)` — 0–60 minute-granular intervals per machine |
| **Orchestrator cut-loop retry** | infeasible week list | tightened MILP (`co_team_cut_w{w}_x{factor}`), re-solve |
| **Extract plan** | solved model + sequence intervals | `Plan(assignments, machine_weeks, changeovers, sequence, ...)` |
| **Write outputs** | `Plan` + `SolveResult` | `outputs/q1/{plan.json, plan.csv, gantt.png, kpis.json}` |
| **Supervisor `chat`** | user text + catalog + `LLMClient` | `SupervisorTurn` with intent + agent output |
| **Constraint-Elicitation** | user request + catalog index | `CatalogPatch` |
| **Verifier** | `CatalogPatch` + catalog root | `VerifyResult` + audit JSONL line |
| **`whatif` CLI** | two `kpis.json` paths | `WhatIfResult` (delta dict + NL narration) |

---

## 4. How the constraint approach was modified: MILP ↔ CP-SAT

This is the question the user asked most directly. Phase 2a moved the
sequencing-heavy constraints out of the MILP and into a second solver,
on the principle that MILP linearises sequence-dependent setup poorly
while CP-SAT handles it natively.

### 4.1 What stayed in MILP (Tier-2)

The MILP commits the *whole-quarter assignment*:

```
x_pack[o, m, w]          — binary, "is order o packed on machine m in week w?"
y_pack[m, f, w]          — binary, "is machine m configured for format f in week w?"
co_pack[m, f1, f2, w]    — continuous [0,1], "does m change f1→f2 going into week w?"
vol_pack[o, m, w]        — continuous, "how many units of o on (m, w)?"
qty_prod[s, w]           — continuous, weekly production need of material s
unfilled[o], idle_hours[m, w], piramal_under/over[t]   — slacks
```

…with the following catalog-installed constraints (Tier-2):

| C-id | Constraint |
|---|---|
| C-001 | one format per (m, w) cycle |
| C-002 | demand balance (vol + unfilled = qty) |
| C-003 | sparse linkage (x_pack ≤ y_pack[format(o)]) |
| C-004 | volume gating (vol_pack ≤ qty · x_pack) |
| C-005 | org orders single-placement |
| C-007 | Farcon ⊕ Dividella per week |
| C-008a | changeover indicator linkage (big-M) |
| C-008b | changeover-team hourly envelope |
| C-009 | cumulative production-pack lag |
| C-012 | production-total / scrap balance |
| C-015 | hours capacity: pack + co + idle = avail |
| C-037 | Piramal monthly band (soft slack) |

### 4.2 What moved to CP-SAT (Tier-3)

| C-id | What CP-SAT does |
|---|---|
| C-008 (sequencing) | `add_no_overlap` per machine within the week; second `add_no_overlap` across all machines on the changeover-team intervals |
| C-013 (shelf-life) | placeholder — `time_window` will fire when long-SL SKUs are bound |
| C-014 (EVATON gap) | placeholder — `forbidden_assignments` will fire when `evaton_skus` is non-empty |

### 4.3 The handoff — what MILP "gives" CP-SAT

The orchestrator reads four MILP results and passes them per week:

```python
# solve/solver_cpsat.py
by_week[w]            = [(order_id, machine, qty),  ...]   # from vol_pack
transitions[w][m]     = (fmt_from, fmt_to, dur_secs)       # from co_pack
avail_seconds[m]      = avail_hours[(m, w)] * 3600         # from Parameters
```

For each week, CP-SAT builds:

1. **One interval per packed order** on the assigned machine, with
   duration `ceil(qty / throughput * 3600)` seconds.
2. **One transition interval per machine that changed format**, at the
   start of the week, with duration `changeover_h * 3600`.
3. **`add_no_overlap` per machine** — orders + the optional transition
   serialise.
4. **`add_no_overlap` globally on the union of transitions** — the
   shared changeover team can only do one machine at a time.
5. Objective: `minimize sum(pack_end_times)` for earliness.

### 4.4 The two bugs we hit (and fixed)

Phase 2a went through two rough iterations before settling:

- **Bug 1: minute granularity overshoots.** Each interval ceiled to
  the next minute. MILP fills cells to exactly 120 h = 7200 min; 50
  rounded-up intervals overshoot the envelope by 50 min →
  spurious infeasibility on 8 / 13 weeks. **Fix:** switch the entire
  CP-SAT model to **second precision** (`*3600` instead of `*60`),
  then add a 300-sec headroom buffer for residual ceil overshoot.
- **Bug 2: phantom transitions over idle weeks.** Tracking
  `prev_active_format` across idle weeks created MILP-unbudgeted
  changeovers. **Fix:** read transitions directly from
  `co_pack` — MILP's own changeover count is the source of truth.

After both fixes, the Q1 re-run produced **0 infeasible weeks** and
**100 % OTIF**.

### 4.5 The retry-cut loop

When a week IS CP-SAT-infeasible (would happen if multiple machines
need a long changeover at the same week boundary), the orchestrator:

1. Adds `sum_{m,f1,f2} co_pack[m,f1,f2,w] * h <= 0.9 * team_hours_per_week`
   as a fresh Pyomo constraint named `co_team_cut_w{week}_x{factor}`.
2. Re-solves the MILP.
3. Re-runs CP-SAT.

Max 2 retries. The factor shaves 10 % at a time
(`max(0.5, 1.0 - 0.1 * (retry + 1))`). Aggressive halving
([from an earlier experiment](https://example.invalid)) tanked OTIF
to 45.6 % — gentle is better.

### 4.6 The numbers

| Path | OTIF | Rated | VIP | MILP | CP-SAT | Total |
|---|---:|---:|---:|---:|---:|---:|
| Phase 1 (MILP only) | 95.0 % | 97.9 % | 100 % | 600 s | — | 600 s |
| Phase 2a hardcoded + CP-SAT | **100 %** | **100 %** | 100 % | 592 s | 181 s | 773 s |
| Phase 2b catalog + CP-SAT | 98.8 % | 99.6 % | 100 % | 155 s | 182 s | 337 s |

(Catalog path is *4× faster* on MILP — different constraint
declaration order leads HiGHS down a different branch-and-cut path.)

---

## 5. How agents verify and reason with changing constraints

The agent layer is the user-facing surface for *editing* the catalog
without touching code. The flow follows §5 of `docs/PHASE2_PLAN.md`
literally, end-to-end.

### 5.1 The seven agents (recap)

| # | Agent | Stage | What it does |
|---|---|---|---|
| 0 | **Supervisor** | orchestration | classify user intent, route to a sub-agent |
| 1 | **Constraint-Elicitation** | pre-solve | NL request → strict JSON `CatalogPatch` |
| 2 | **Schedule-Generation** | pre-solve | run the Tier-2 + Tier-3 orchestrator |
| 3 | **Infeasibility** | post-solve | narrate which rules clashed, propose one relaxation |
| 4 | **What-If** | post-solve | diff two KPI snapshots, narrate in 3-5 bullets |
| 5 | **Explanation** | post-solve | persona-tuned summary (sales / production / compliance) |
| 6 | **Disruption-Response** | live | (stub) bound the re-solve window to nearby cells |

### 5.2 The Verifier — what makes it safe

Between every agent output and the catalog, the Verifier runs five
**deterministic** checks (zero LLM involvement):

| Check | What it does | When it fails |
|---|---|---|
| `schema` | re-validate the patched row through pydantic | unknown field, wrong type, missing required key |
| `unit` | every numeric `parameters[k]` must appear in `units[k]` | someone introduced an un-documented number |
| `business_rule` | regex-scan `business_rules` for approval phrases | "requires X approval" → adds an approval gate |
| `feasibility` | re-compile the catalog with the patch staged in `<root>__verify_staging/`, run a 30-sec warm solve | solver returns `infeasible` |
| `audit` | append a JSONL record to `catalog/audit/<id>-history.jsonl` (pass or fail) | n/a — always runs |

`VerifyResult.passed` AND `not needs_approval` is the only condition
that lets a patch be applied. Approval gates short-circuit to "ask
the right human" — the UI/queue would handle that in Phase 3.

### 5.3 Worked example — "raise Piramal monthly band to 2.5M"

The Phase-2 plan promised this concrete end-to-end. Here is the
actual transcript (live, against `gpt-4o-mini`):

```
$ demo_scheduler chat "raise Piramal monthly band to 2.5M"
(using OpenAILLM gpt-4o-mini)
intent: edit_constraint
patch: C-037 -> {'monthly_max': 2500000}
  [✓] schema: pydantic accepted patched row
  [✓] unit: 2 units documented
  [✓] business_rule: 1 gate(s) triggered
approval gates:
  ⚠ approval::Any change to monthly_max above 2.5M requires Sales-VP approval.
```

What happened, step by step:

1. **Supervisor.classify()** — rule-based pre-pass matched
   `\b(raise|set|update)\b.*\b(band|cap|limit)\b` → intent
   `edit_constraint`. No LLM call needed for this step.
2. **Constraint-Elicitation.elicit()** — built the LLM system
   prompt from the catalog index (compact `id | pattern | severity |
   name | params` lines) + the user text. One call to
   `OpenAILLM(gpt-4o-mini)`. The model returned strict JSON:
   ```json
   {"id":"C-037","parameter_changes":{"monthly_max":2500000},
    "version_to":"2026-05-12.v2","rationale":"..."}
   ```
3. **Verifier._apply_patch()** — merged the change onto C-037's
   parameters in memory and re-ran pydantic. Passed.
4. **check_units()** — both `monthly_min` and `monthly_max` already
   declared `ampoules_per_month` in the row. Passed.
5. **check_business_rules()** — regex `requires?\s+(?P<approver>...)`
   matched *"Any change to monthly_max above 2.5M requires Sales-VP
   approval"* in C-037. Surfaced as
   `approval::Any change to monthly_max above 2.5M requires Sales-VP approval`.
6. **audit_append()** — wrote a JSONL line to
   `catalog/audit/C-037-history.jsonl` recording the patch attempt,
   the outcomes, and the verdict `"needs_approval"`.

Because the approval gate fired, the patch is *not* written to disk
even with `--apply`. The Approval Queue UI (Phase-3) would surface
this to the Sales VP. If they approve, the queue can resume:
re-run `chat` with the VP as actor; the same audit trail captures the
sign-off.

If instead the user had asked for **2.2M** (under the 2.5M threshold),
no gate would fire and `--apply` would write the new YAML directly.

### 5.4 What about reasoning, not just routing?

For the four prompt-heavy agents, the LLM does the actual reasoning
(every other agent is deterministic):

| Agent | What the LLM is asked to do |
|---|---|
| Constraint-Elicitation | Pick the right catalog id from the index; map natural-language values to parameter keys; emit strict JSON or `{"error": "..."}` |
| What-If | Given BEFORE / AFTER / DELTA dicts (no catalog access), narrate the shift in 3-5 bullets — focusing on OTIF, Piramal band coverage, utilisation, # changeovers |
| Infeasibility | Given the failure context (catalog excerpts + KPI snapshot + infeasible weeks), name the conflict in plain language and propose ONE relaxation as `<catalog_id>.<parameter>` |
| Explanation | Given a KPI dict and a persona (sales / production / compliance), emit 4-7 bullets + a "what this means for you" sentence |

All prompts are short, JSON-anchored where structure matters, and
fully testable with `MockLLM`. The LLM **never** sees the Pyomo model
object, the raw Excel, or any user PII — only the catalog index and
the KPI dicts.

### 5.5 The reasoning loop on a constraint change

Combining sections 5.2 and 5.3:

```
User types          Supervisor classifies      Constraint-Elic.        Verifier
─────────────────►  ─────────────────────►  ────────────────────►  ───────────────►
"raise Piramal      intent=edit_constraint   {"id":"C-037",          schema ✓
 monthly band to    (rule-based; no LLM)      "parameter_changes":   unit    ✓
 2.5M"                                        {"monthly_max":2.5M}}  business ⚠ gate
                                                                     (no commit)
                                                                              │
                            ▲                                                 │
                            │     queued for Sales-VP                         │
                            │     approval                                    │
                            └─────────────────────────────────────────────────┘

After approval:
─────────────────►  ────────────────────►  ───────────────►   ────────────────►
                    queue replays the      audit logs the     catalog YAML
                    same patch + actor     approval +         updated; next
                    = "vp@sales"           commit             solve uses C-037 v2
                                                                            │
                                                                            ▼
                                                              Schedule-Generation
                                                              re-fires MILP+CP-SAT
                                                                            │
                                                                            ▼
                                                              What-If narrates
                                                              the OTIF/util delta
```

Every reasoning step that affects the system of record is gated by
the Verifier; every Verifier outcome is captured in the per-id
JSONL history. The LLM is opinionated; the Verifier is load-bearing.

---

## 6. Inputs and outputs at the CLI

```bash
# Phase-1 baseline (single MILP, hardcoded constraints)
demo_scheduler solve --quarter Q1 --skip-cpsat

# Phase-2a (hardcoded MILP + CP-SAT)         ← default
demo_scheduler solve --quarter Q1

# Phase-2b (catalog-driven MILP + CP-SAT)
demo_scheduler solve --quarter Q1 --use-catalog --output-dir outputs/q1_catalog

# Phase-2b catalog operations
demo_scheduler catalog list
demo_scheduler catalog show C-037
demo_scheduler catalog validate

# Phase-2c agent chat
demo_scheduler chat "raise Piramal monthly band to 2.5M"     # live LLM
demo_scheduler chat "explain to Sales" --persona sales       # live LLM
demo_scheduler chat "<anything>" --use-mock                  # deterministic demo

# Phase-2c what-if narration
demo_scheduler whatif outputs/q1/kpis.json outputs/q1_catalog/kpis.json
```

LLM selection is automatic from `.env`:

- `OPENAI_API_KEY` set → `OpenAILLM` (`gpt-4o-mini`)
- only `ANTHROPIC_API_KEY` set → `AnthropicLLM` (`claude-haiku-4-5`)
- neither set → use `--use-mock`

Override with `--llm openai|anthropic|auto`.

---

## 7. Inputs and outputs at the file level

### Inputs

| File | Role |
|---|---|
| `Packaging Ampoules.xlsx` | demand, ratings, manual machine allocations |
| `configs/defaults.yaml` | calendar, throughput, weights, scrap, bands (Phase-1 source) |
| `catalog/constraints/*.yaml` | the 16 versioned constraint rows (Phase-2 source) |
| `.env` | `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`) for the agent layer |

### Outputs

| File | Produced by | Contents |
|---|---|---|
| `outputs/q1/plan.json` | `output/plan.py` | quarter, weeks, machines, per-order assignments + minute-level sequence, machine-weeks, changeovers, Piramal monthly, production needs |
| `outputs/q1/plan.csv` | `output/plan.py` | one row per (order, placement) — spreadsheet-friendly |
| `outputs/q1/gantt.png` | `output/gantt.py` | minute-level Gantt when CP-SAT ran; weekly fallback otherwise |
| `outputs/q1/kpis.json` | `output/kpis.py` | OTIF, Rated OTIF, VIP OTIF, util-by-machine, Piramal band breakdown, CP-SAT metadata |
| `catalog/audit/<id>-history.jsonl` | `verifier/verify.py` | one JSONL line per Verifier run on that constraint id |

---

## 8. Phase 1 vs Phase 2 — the headline diff

| Aspect | Phase 1 | Phase 2a | Phase 2b | Phase 2c |
|---|---|---|---|---|
| **Solvers** | one MILP (HiGHS) | + CP-SAT per week | unchanged | unchanged |
| **Constraints** | 15+3 hardcoded in `model/constraints.py` | unchanged | 12 enabled / 4 disabled in YAML, dispatched by pattern compiler | unchanged |
| **Sequencing** | weekly granularity, big-M | minute granularity, native CP-SAT no_overlap | unchanged | unchanged |
| **Edit a rule** | edit Python + restart | unchanged | edit YAML and re-run | chat with the Supervisor |
| **Trust boundary** | code review | unchanged | YAML diff + tests | deterministic Verifier (schema/unit/business/feasibility/audit) |
| **OTIF (Q1)** | 95.0 % | 100 % | 98.8 % | dependent on patches |
| **Tests** | 8 | 11 | 17 | **30** |
| **Solve time** | 10 min | 10 min MILP + 3 min CP-SAT | 2.5 min MILP + 3 min CP-SAT | unchanged |

---

## 9. What's still open

- **11 of 18 v1 constraints are catalog-driven via working compilers; the remaining 7 are vacuous (`time_window`, `forbidden_assignments`) or held back for Phase-3 (per-line one-API).**
- The **Phase-2c Disruption-Response agent is a stub** — needs the Tier-4 LNS implementation (Phase 3) to be load-bearing.
- **MCP servers** (Catalog, Solver, Plan, Audit, SAP-stub) are not yet exposed as separate processes — the agent code calls Python functions directly. Adding MCP is a packaging task.
- **Approval Queue UI** does not exist as a web page yet; gates are detected and audit-logged, but a human still has to read the audit JSONL.
- **Real SAP write-back** is Phase 3.

---

## 10. Glossary additions

| Term | Meaning |
|---|---|
| **Catalog row** | One `catalog/constraints/<id>-<slug>.yaml` file; the source of truth for one constraint |
| **Pattern type** | The `formal_expr.type` field — chooses which compiler in `model/compilers/patterns.py` to dispatch |
| **Solver layer** | `MILP_master` (Tier-2) / `CP_SAT_seq` (Tier-3) / `both` — tells the orchestrator where to install the rule |
| **CatalogPatch** | Proposed mutation to one row's `parameters` + `version_to`; verified before any disk write |
| **Approval gate** | A `business_rules` line that, when matched by the Verifier's regex pass, requires named-actor sign-off before commit |
| **Verifier** | The deterministic, no-LLM trust boundary between LLM agents and the catalog |
| **MockLLM** | A canned-response `LLMClient` used by tests so CI never makes network calls |
| **`default_llm()`** | Auto-picks the real client (OpenAI > Anthropic) based on which API key is in `.env` |
