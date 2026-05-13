# Phase 2 Plan — CP-SAT Sequencing, Constraint Catalog, and LLM Agent Loop

> Companion to `PROBLEM_AND_APPROACH.md` (overall architecture) and `MODEL_SCOPING.md` (decision variables / constraints).
> Where Phase 1 (`/demo_scheduler/`) is a single MILP that hardcodes the 15+3 constraints, **Phase 2** turns the solver into a two-stage MILP + CP-SAT pipeline driven by a *versioned constraint catalog*, with seven LLM agents bridging the user, the catalog, and the solvers.

---

## 0. Why this phase

Phase 1 answered "can we even solve this?" — yes, the v1 MILP reaches 95% OTIF on Q1 in 10 min. Phase 2 answers three questions that block real adoption:

1. **Can we sequence tightly?** v1 linearises sequence-dependent setup with big-M, so the changeover model is loose and slow. CP-SAT handles `no_overlap`, `circuit`, and forbidden regions natively — typically 5–10× faster on sequencing.
2. **Can the business edit a constraint without a code change?** v1 puts the 15+3 constraints in Python and the parameters in YAML. Phase 2 moves *every* constraint (rule + parameters + scope) into a versioned catalog. Adding "Piramal band rises to 2.5M in Q3" is a one-row edit.
3. **Can an LLM safely propose / explain / re-plan changes?** Phase 2 introduces 7 agents around the solvers. The LLM only ever writes *catalog patches* and *narrations*; numbers come from the solvers. A deterministic verifier sits between every agent output and the system of record.

---

## 1. End-state architecture (one diagram)

```
                           ┌────────────────────────┐
                           │      USER LAYER        │
                           │  chat • what-if forms  │
                           │  approval queue • KPI  │
                           └──────────┬─────────────┘
                                      │ NL request / structured patch
                                      ▼
        ┌─────────────────────────────────────────────────────────────┐
        │           AGENT ORCHESTRATION (LangGraph supervisor)        │
        │                                                             │
        │   ┌──────────────┐                                          │
        │   │ Supervisor   │  routes by intent                        │
        │   └──────┬───────┘                                          │
        │          │                                                  │
        │  ┌───────┴─────────────────────────────────────────────────┐│
        │  │ PRE-SOLVE                                               ││
        │  │   1) Constraint-Elicitation Agent (NL → catalog patch)  ││
        │  │   2) Schedule-Generation Agent (catalog → solver call)  ││
        │  └───────┬─────────────────────────────────────────────────┘│
        │          │                                                  │
        │          ▼                                                  │
        │     ╔══════════════════════════════════════════════╗        │
        │     ║         VERIFIER (deterministic)             ║        │
        │     ║  schema • unit • feasibility (warm cache)    ║        │
        │     ║  business-rule • audit-log                   ║        │
        │     ╚════════════════════╤═════════════════════════╝        │
        │                          │ (catalog committed)              │
        │                          ▼                                  │
        │  ┌──────────────────────────────────────────────────────┐   │
        │  │ SOLVER CHAIN (the source of truth on every number)   │   │
        │  │                                                      │   │
        │  │   Tier-1 LP        annual feasibility, O*            │   │
        │  │   Tier-2 MILP      assignment + customer bands       │◀──┤
        │  │   Tier-3 CP-SAT    sequencing + setup + no-overlap   │   │
        │  │   Tier-4 LNS       repair on live disruption         │   │
        │  │   DES Simulator    robustness validation             │   │
        │  └────────────────────┬─────────────────────────────────┘   │
        │                       │ solved plan + KPIs                  │
        │                       ▼                                     │
        │  ┌──────────────────────────────────────────────────────┐   │
        │  │ POST-SOLVE                                           │   │
        │  │   3) Infeasibility Agent  (OptiChat-style narration) │   │
        │  │   4) What-If Agent        (OptiGuide-style diffs)    │   │
        │  │   5) Explanation Agent    (persona-tuned rendering)  │   │
        │  └──────────────────────────────────────────────────────┘   │
        │                                                             │
        │  ┌──────────────────────────────────────────────────────┐   │
        │  │ LIVE (post-deploy)                                   │   │
        │  │   6) Disruption-Response Agent (ALAS-style LNS)      │   │
        │  └──────────────────────────────────────────────────────┘   │
        └─────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
        ┌─────────────────────────────────────────────────────────────┐
        │  TOOL / DATA LAYER (every box is an MCP server)             │
        │  Catalog • Solver • SAP Bridge • Telemetry • Simulator      │
        │  Audit + version store                                      │
        └─────────────────────────────────────────────────────────────┘
```

---

## 2. CP-SAT sequencing — the second solver

### 2.1 Two-stage split

The MILP keeps everything where assignment + cumulative balance is naturally linear; CP-SAT takes everything where *order matters within a (machine, week)*.

| Concern | v1 (Phase 1) | v2 (Phase 2) |
|---|---|---|
| Order → (machine, week) assignment | MILP | **MILP** (Tier-2) |
| Demand fulfilment / unfilled | MILP | **MILP** (Tier-2) |
| Capacity hours per (m, w) | MILP | **MILP** (Tier-2) |
| Customer band (Piramal etc.) | MILP soft slack | **MILP** soft slack |
| Org no-split | MILP | **MILP** |
| Format-once-per-cycle | MILP `y_pack` | **MILP** `y_pack` |
| Production lag, cumulative | MILP | **MILP** |
| **Sequence within a week (which order runs first)** | not modelled | **CP-SAT** (Tier-3) — interval / no-overlap |
| **Sequence-dependent setup duration** | big-M on consecutive-week `y_pack` | **CP-SAT** `transition_time` / circuit |
| **No-overlap of changeover team across machines** | linearised, weekly-coarse | **CP-SAT** `add_no_overlap` on interval vars |
| **Shift-level Gantt + start/finish times** | not modelled | **CP-SAT** outputs minute-granularity intervals |
| **EVATON 2-batch / 3-week gap** | structural placeholder | **CP-SAT** `forbidden_assignments` |

### 2.2 Data handoff between MILP and CP-SAT

```
Tier-2 MILP                        Tier-3 CP-SAT
───────────                        ────────────
solves over WEEKS                  solves over MINUTES (within each week)

writes:                            reads:
  x_pack[o,m,w]   ──────────────►    set of orders assigned to (m, w)
  y_pack[m,f,w]   ──────────────►    active format on (m, w)
  vol_pack[o,m,w] ──────────────►    quantity to pack
  qty_prod[s,w]   ──────────────►    production needs per week
                                   writes:
                                     interval_var(start, end) per order
                                     transition interval per changeover
                                     verified weekly Gantt
                                     refined changeover/idle hours
                                   feeds back (only on failure):
                                     "this week's MILP assignment is
                                      sequence-infeasible; retry"
```

The two solvers are **not** co-optimised in a Benders loop in v2 — the MILP commits its assignment and CP-SAT respects it. If CP-SAT can't sequence what MILP gave it (which is rare with sensible inputs), the failed week's `x_pack` and `y_pack` are sent back as a *cut* (forbidden combination) and MILP re-solves that week. Two iterations is normally enough.

### 2.3 What changes in the codebase

```
demo_scheduler/
├── model/
│   ├── build.py            # SPLIT — keeps Tier-2 only (assignment, balance, capacity)
│   └── tier3_cpsat.py      # NEW — OR-Tools CP-SAT model for sequencing
├── solve/
│   ├── solver.py           # SPLIT — Tier-2 solver wrapper unchanged
│   ├── solver_cpsat.py     # NEW — OR-Tools CP-SAT wrapper
│   └── orchestrator.py     # NEW — runs Tier-2 → Tier-3 → (optional retry loop)
├── output/
│   └── gantt.py            # UPGRADE — shift-level Gantt from CP-SAT intervals
```

The MILP model loses ~5 constraints (the linearised changeover + co_team blocks); CP-SAT gains them in tighter form. Net code size: roughly neutral, but solve time drops because the LP relaxation of the MILP is now much tighter without big-M on changeover.

### 2.4 Implementation steps

1. Strip the changeover linearisation (`changeover_link`, `co_team`) from `model/build.py`. Leave `co_pack` as a Tier-3 output only.
2. Write `model/tier3_cpsat.py`:
   - `Interval`/`IntervalVar` per (order, machine, week) with size = `qty/throughput` minutes.
   - `add_no_overlap` per machine-week (single resource).
   - `transition_time` matrix from `changeover_h[m, f1, f2]`.
   - One global `no_overlap` resource for the changeover team across all machines.
   - Forbidden assignments for EVATON (2-batch / 3-week gap).
3. `solve/orchestrator.py`:
   ```python
   def solve_quarter(params, cfg):
       milp_model = build_milp(params)
       milp_result = solve_milp(milp_model, cfg)
       sequence_plan, infeasible_weeks = solve_cpsat(milp_result, params, cfg)
       while infeasible_weeks and iters < 3:
           milp_model = add_cuts(milp_model, infeasible_weeks)
           milp_result = solve_milp(milp_model, cfg)
           sequence_plan, infeasible_weeks = solve_cpsat(milp_result, params, cfg)
       return merge(milp_result, sequence_plan)
   ```
4. Update `output/gantt.py` to render minute-granularity intervals from CP-SAT.
5. Update tests in `tests/test_q1_smoke.py` to assert minute-level no-overlap.

### 2.5 Expected gains

- Solve time: 10 min → **2–3 min** on the same Q1 instance.
- Changeover plan: weekly granularity → **shift-level**.
- The changeover model becomes tight (CP-SAT propagates `transition_time` exactly), so the objective can rely on real changeover-hour totals.
- Unlocks proper req #16 ("re-plan in seconds on disruption") because CP-SAT can re-solve a single week in <5s.

---

## 3. Constraint catalog — every rule as data

### 3.1 Why move beyond `defaults.yaml`

v1 splits the world into:
- *Parameters* (in `defaults.yaml`) — numbers
- *Constraints* (in `model/constraints.py`) — Python code

Phase 2 collapses these: **a constraint is a structured row** whose `parameters` field holds the numbers, whose `formal_expr` field holds the rule, whose `solver_layer` field tells the orchestrator where to instantiate it, and whose `version` field makes changes auditable.

### 3.2 Schema — `catalog/constraints/*.yaml`

One file per constraint. Schema:

```yaml
# catalog/constraints/C-037-piramal-band.yaml
id: C-037
req_ref: req #37
category: customer_band              # customer_band | sequencing | capacity | …
name: Piramal monthly volume band
description: |
  Piramal must receive between 1.8M and 2.0M ampoules per calendar month.
  Data shows 2.0–2.8M/month for 2025; open question — see MODEL_SCOPING §10.
severity: soft                       # hard | soft (soft adds penalised slack)
solver_layer: MILP_master            # MILP_master | CP_SAT_seq | both
enabled: true

# Symbolic form — what gets compiled to Pyomo / CP-SAT.
formal_expr:
  type: two_sided_bound
  index_over: [month]
  lhs: "sum(o in O_piramal, w in month(t)) vol_pack[o, m, w] for all m"
  bound_min: parameters.monthly_min
  bound_max: parameters.monthly_max

parameters:
  monthly_min: 1_800_000
  monthly_max: 2_000_000

# Soft-mode slack handling (omitted when severity = hard).
soft_handling:
  under_var: piramal_under
  over_var:  piramal_over
  penalty_weight: objective_weights.late   # reference to the weights block

# Verifier hooks
units:
  bound_min: ampoules_per_month
  bound_max: ampoules_per_month
business_rules:
  - "any change to monthly_max above 2.5M requires Sales-VP approval"
  - "monthly_min cannot go below 1.5M while supply contract X-2024 is active"

owner: Sales/Account Mgr
version: 2026-05-11.v1
verifier_state: passed
audit_log_ref: "catalog/audit/C-037-history.jsonl"
```

`formal_expr.type` enumerates ~12 patterns covering all 15+3 v1 constraints (see §3.4). A constraint compiler walks the catalog and emits Pyomo or CP-SAT code:

```python
# model/compile.py
def compile_catalog(catalog: list[CatalogRow], target: Literal["MILP", "CP-SAT"]) -> ConstraintBlock:
    blocks = []
    for row in catalog:
        if not row.enabled:
            continue
        if row.solver_layer != target and row.solver_layer != "both":
            continue
        compiler = COMPILERS[row.formal_expr.type]  # plugin per pattern
        blocks.append(compiler(row, target))
    return blocks
```

### 3.3 The 15 + 3 v1 constraints as catalog rows

| Catalog ID | Req | Pattern type | Layer | Severity |
|---|---|---|---|---|
| C-001 one-format-per-cycle | #8 | `sum_le` | MILP_master | hard |
| C-002 demand-fulfilment | #2,#10 | `balance` | MILP_master | hard |
| C-003 order-machine-eligibility | implicit | `sparse_set` | MILP_master | hard |
| C-004 glass-volume-thresholds | #31,#32 | `range_eligibility` | MILP_master | hard |
| C-005 org-no-split | #39 | `single_placement` | MILP_master | hard |
| C-006 piramal-band | #37 | `two_sided_bound` | MILP_master | soft |
| C-007 farcon-xor-dividella | #35 | `mutex` | MILP_master | hard |
| C-008 changeover-team-no-overlap | #36 | `resource_no_overlap` | **CP_SAT_seq** | hard |
| C-009 prod-pack-lag-4d | #46 | `precedence_lag` | MILP_master | hard |
| C-010 prod-label-lag-3d | #46 | `precedence_lag` | MILP_master | hard |
| C-011 one-api-per-week | #42 | `sum_le` | MILP_master | hard (toggleable) |
| C-012 campaign-coverage-by-sl | #45 | `aggregated_demand` | MILP_master | hard |
| C-013 shelf-life-storage-cap | #48 | `time_window` | CP_SAT_seq | hard |
| C-014 evaton-2-batch-3-week-gap | #49 | `forbidden_assignments` | CP_SAT_seq | hard |
| C-015 capacity-hours | #5,#19 | `capacity_eq` | MILP_master | hard |
| C-040 tie-fairness | #40 | `deviation_penalty` | MILP_master | soft |
| C-041 highest-score-first | #41 | `lex_min` | MILP_master | soft |
| C-038 minor-format-changes-allowed | #38 | `relaxation_marker` | both | soft |

### 3.4 Pattern compiler — keeping the grammar small

The catalog stays expressive but the *compiler* surface is bounded. A new constraint that fits an existing pattern is a YAML edit; a constraint that needs a *new* pattern is a code change to add one compiler plugin (one function in `model/compilers/`).

The 12 patterns cover everything in the current 50-requirement spec; we expect ≤5 new patterns over the first year as edge cases surface.

### 3.5 Versioning, diff, and audit

Every catalog write is a new version. The full history of a constraint lives under `catalog/audit/<id>-history.jsonl`:

```jsonl
{"ts": "2026-05-11T17:42:00Z", "id": "C-037", "from": "v1", "to": "v2",
 "change": {"parameters.monthly_max": [2_000_000, 2_500_000]},
 "actor": "user:cgapho...", "rationale": "Sales contract update Q3",
 "verifier_runs": ["schema:pass", "unit:pass", "feasibility:pass", "business:pass"],
 "objective_delta": "+0.4% rated OTIF", "approved_by": "user:cgapho..."}
```

The Audit MCP service serves this; the Verifier reads it for business-rule checks.

### 3.6 Visibility to the LLM

The LLM only ever sees **the catalog**, **the last plan**, and **the metadata about the run** (status, KPIs, IIS). It never sees the Pyomo model object itself. The catalog is YAML, so it's natively human/LLM readable.

Specifically:
- **Catalog MCP** exposes: `list_constraints`, `get_constraint(id)`, `propose_patch(id, change)`, `commit_patch(patch_id)`, `rollback(id, version)`.
- **Solver MCP** exposes: `solve(catalog_version, horizon)`, `last_run()`, `iis(infeasible_run_id)`.
- **Plan MCP** exposes: `get_plan(run_id)`, `compare_plans(run_a, run_b)`.

---

## 4. Agents — seven roles, three stages

| # | Agent | Stage | Reads | Writes | Trigger |
|---|---|---|---|---|---|
| 0 | **Supervisor** | orchestration | user intent | sub-agent invocation graph | every user turn |
| 1 | **Constraint-Elicitation** | PRE-solve | catalog, user NL request | catalog *patch* (proposed) | user says "change/add a rule" |
| 2 | **Schedule-Generation** | PRE-solve | catalog, data snapshot, knobs | solver call (Tier-1 → 4) | user says "run scheduler" or catalog change committed |
| 3 | **Infeasibility** | POST-solve | IIS / minimal infeasible subsystem | NL narration of which constraints clash | solver returns infeasible |
| 4 | **What-If** | POST-solve | two plans (before/after) | NL narration of the delta + KPI diff | catalog patch ready to compare |
| 5 | **Explanation** | POST-solve | committed plan + persona context | persona-tuned text (Sales / Production / Compliance) | new plan committed |
| 6 | **Disruption-Response** | LIVE | Telemetry MCP events + current plan | LNS repair patch + narration | Photocells freeze ≥ τ minutes |

### 4.1 Where each agent sits in the loop

```
USER ───►  Supervisor ──┐
                        │
                  ┌─────┴───────┐
                  │             │
           "edit rule"     "run / explain"
                  │             │
                  ▼             ▼
      Constraint-Elicit   Schedule-Generation
                  │             │
                  ▼             ▼
              VERIFIER     SOLVER CHAIN  ────►  if infeasible ──► Infeasibility
                  │             │
                  ▼             ▼
            catalog v+1     plan_v+1 ────►  What-If (vs plan_v) ──► Explanation
                  │             │
                  └──────┬──────┘
                         │
                         ▼
                    APPROVAL QUEUE
                         │
                         ▼
                    SAP push  +  AUDIT

Live (independent of above):
  Telemetry ──► Disruption-Response ──► Tier-4 LNS ──► VERIFIER ──► SAP push
```

### 4.2 What each agent is allowed to do

- **Constraint-Elicitation** writes catalog *patches* only — never solver calls, never SAP. Output must be a structured patch object the Verifier can check.
- **Schedule-Generation** calls the Solver MCP and returns the result reference. It does not interpret numbers — that's downstream.
- **Infeasibility** reads IIS and narrates. It can *propose* a relaxation (e.g. "if you let C-037 monthly_max rise by 0.5M, this becomes feasible") but cannot apply it.
- **What-If** runs two catalog versions through the Solver MCP and reports the KPI diff plus a 3-bullet rationale.
- **Explanation** is a renderer — it cannot mutate state.
- **Disruption-Response** is the only agent allowed to act fast: it can call Tier-4 LNS and push to SAP *after* the Verifier passes, all within ~1 minute.
- **Supervisor** routes; it does not solve or write.

### 4.3 What the Verifier does (the trust boundary)

Between every agent output and the system of record:

```
Agent output (catalog patch | plan | SAP write request)
      │
      ▼
Verifier
  ├── schema check     (pydantic on the patch / plan dataclass)
  ├── unit check       (cross-check parameter units against catalog `units` block)
  ├── feasibility check (run a *warm-cached* solver on the patched catalog)
  ├── business-rule check (cross-check `business_rules` block against actor/context)
  └── audit log         (append to immutable JSONL)
      │
      ▼
Commit ←── all pass ───→  reject (NL reason returned to the agent)
```

The Verifier is **deterministic** — no LLM in it. That is what allows the LLM agents to be opinionated without being load-bearing on correctness.

---

## 5. Replan flow — quantifiable constraint change, end to end

### 5.1 The scenario

User chats: *"Raise Piramal monthly band to 2.5M, starting in Q1."*

### 5.2 Step-by-step

```
1. SUPERVISOR
   parses intent → "modify existing constraint" → route to Constraint-Elicitation

2. CONSTRAINT-ELICITATION AGENT
   reads catalog/constraints/C-037-piramal-band.yaml (v1)
   drafts patch:
     PATCH C-037 v1 → v2
       parameters.monthly_max: 2_000_000 → 2_500_000
       version: 2026-05-11.v1 → 2026-05-11.v2
   passes patch to Verifier

3. VERIFIER (deterministic, ~1s)
   ├── schema:        OK (correct type, two_sided_bound pattern still valid)
   ├── unit:          OK (still ampoules/month)
   ├── feasibility:   runs cached-warm solve on draft catalog → SAT (the new max
   │                   is more permissive than v1 — was always going to be SAT
   │                   given v1 was soft anyway)
   ├── business-rule: C-037.business_rules says "above 2.5M requires Sales-VP
   │                   approval" — 2.5M is the boundary; this rule fires a
   │                   GATE that requires Sales-VP sign-off. Returns
   │                   "needs_approval: sales_vp"
   └── audit: draft logged

   If gate fires → BLOCK with NL reason returned via Supervisor to user:
     "C-037 raise to 2.5M needs Sales-VP approval per the business rule
      attached to this constraint. Forward to VP?"

   If gate clears (Sales-VP approves in app) → continue.

4. CATALOG COMMIT
   New row written: C-037 v2
   audit/C-037-history.jsonl appended
   Catalog MCP returns the new catalog version id (e.g. catalog v37 → v38)

5. SCHEDULE-GENERATION AGENT
   triggers SOLVER CHAIN against catalog v38:
     Tier-1 LP   ── 5s   → annual feasibility envelope (unchanged)
     Tier-2 MILP ── 90s  → new (x_pack, y_pack, qty_prod) — Piramal can absorb more
     Tier-3 CP-SAT ── 30s → new sequence + changeover plan
     DES Simulator ── 20s → robustness OK
   solver writes plan_v38 to Plan MCP

6. WHAT-IF AGENT
   reads plan_v37 (before) and plan_v38 (after); produces:

   "Catalog C-037 v1 → v2 (Piramal band 2.0M → 2.5M monthly):

    • Piramal monthly band slack: was [0, 1.6M, 2.2M] over → now [0, 0, 0].
    • Rated OTIF: 97.9% → 99.1% (+1.2 pts).
    • Total changeover hours: 14h → 18h (+4h).
    • Piramal unfilled: 853k → 0.
    • Marchesini GL utilisation: 53.7% → 58.4%.
    • One extra changeover on Dividella W7."

7. EXPLANATION AGENT
   renders three persona-tuned views from plan_v38 + the What-If delta:
     ├── Sales view:       "Piramal Q1 will ship at full demand. Three Greek
     │                      and UK orders move from W3 to W4 to absorb the
     │                      capacity shift."
     ├── Production view:  Gantt with the +1 changeover on Dividella W7,
     │                      Marchesini W6/W8 reshuffled, KPI utilisation chart.
     └── Compliance view:  audit summary, Sales-VP approval timestamp,
                           catalog diff link, plan id, run id.

8. APPROVAL QUEUE → SAP PUSH
   user reviews the three views
   user clicks "approve and commit"
     ├── Audit MCP append:  plan_v38 commit + actor + rationale
     ├── SAP Bridge MCP:    write Process Orders for v38 (only delta from v37)
     └── Telemetry MCP:     subscribes the new plan to live monitoring

9. (If user rejects)
   ├── Catalog rollback: C-037 v2 marked obsolete, plan_v38 marked rejected
   ├── Audit log records rejection rationale
   └── Telemetry continues monitoring plan_v37
```

### 5.3 What this gives the user that v1 doesn't

| v1 (Phase 1) | v2 (Phase 2) |
|---|---|
| Edit `configs/defaults.yaml`, re-run CLI, read JSON | Chat "raise band to 2.5M" |
| You need to know the parameter name | Constraint-Elicitation finds it by NL |
| You don't know if the change broke another rule | Verifier checks feasibility + business rules |
| You compare two JSONs by eye | What-If narrates the delta in 5 bullets |
| You decide what to tell Sales / Production / Compliance | Explanation renders all three |
| You write to SAP yourself, hopefully | Audit + SAP push are gated behind a single approval |

### 5.4 Latency budget for the loop

| Step | Time |
|---|---:|
| NL → patch (Constraint-Elicitation) | 2–4 s |
| Verifier (no solver) | <1 s |
| Verifier feasibility check (cached warm solve) | 10–30 s |
| Catalog commit | <1 s |
| Tier-1 + Tier-2 + Tier-3 + DES | 2–4 min |
| What-If diff narration | 3–5 s |
| Explanation render (3 views) | 5–10 s |
| **Total user-visible round trip** | **3–5 min** |

Live disruption (Disruption-Response) targets <60 s end-to-end via LNS.

---

## 6. Build order — three sub-phases

### Phase 2a — CP-SAT split (2 weeks)

- Carve out `model/tier3_cpsat.py`, `solve/solver_cpsat.py`, `solve/orchestrator.py`.
- Move C-008 (changeover team), C-013 (shelf-life storage), C-014 (EVATON gap) into CP-SAT.
- Keep `defaults.yaml` as the parameter source — catalog comes in 2b.
- Verify Q1 still hits ≥95% OTIF in <3 min.
- Verify the smoke test post-hoc checks pass at minute-granularity (CP-SAT outputs minute starts).

### Phase 2b — Constraint catalog (3 weeks)

- Define the 12 pattern types + compiler plugins in `model/compilers/`.
- Migrate the 15+3 constraints from Python into `catalog/constraints/*.yaml`.
- `model/build.py` becomes `model/compile.py` — assembles MILP from catalog.
- `solve/orchestrator.py` accepts a catalog version id and pins the run to it.
- Add `Audit MCP` (file-backed JSONL) + `Catalog MCP` (filesystem-backed YAML watcher).
- Deprecate `configs/defaults.yaml` (its contents migrate into the catalog and a slim `knobs.yaml`).

### Phase 2c — Agent loop (4 weeks)

- LangGraph supervisor + 7 sub-agents.
- Verifier (deterministic checks + solver-cached feasibility probe).
- MCP servers for Catalog, Solver, Plan, Audit, SAP Bridge stub.
- Approval queue UI (simple web page is fine for v2).
- End-to-end "change Piramal band" smoke test passes the §5.2 flow in <5 min.

After 2c, Phase 3 (Tier-4 LNS, Photocells/ProdAction telemetry, real SAP bridge) becomes incremental.

---

## 7. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| CP-SAT model is slower than expected on tight no-overlap | Low | Bench on Q1 first; fall back to linearised MILP (Phase-1 default) if needed |
| Catalog compiler grammar can't express a rule | Med | Keep compiler plugin model open; new pattern = one function |
| LLM proposes invalid patches | High | Verifier rejects with NL reason; agent retries with the reason |
| User changes a constraint that's still under another agent's review | Med | Catalog uses optimistic concurrency on `version`; second commit re-bases or fails |
| Feasibility check in Verifier is slow | Med | Use *cached warm-started* solve on a frozen sub-model — 5–30 s, not 10 min |
| Sales-VP approval gate not modelled in v2 | Med | Business-rule check returns a *gate signal*; UI shows the approval queue |

---

## 8. Out of scope for Phase 2

- Real-time Photocells/ProdAction telemetry feeds (Phase 3).
- LNS repair on disruption (Phase 3 — Disruption-Response agent is stubbed in 2c).
- True SAP write-back (Phase 3 — SAP Bridge is a stub in 2c that writes to a file).
- Multi-tenant catalog / RBAC (Phase 4).
- Plastic ampoules / bottles / lyophilised (Phase 4 — req #34 scope).
- Re-rating customers automatically (Phase 4 — owned by Sales).

---

## 9. Glossary

| Term | Meaning |
|---|---|
| **Catalog** | The versioned YAML store of all constraints + their parameters + their solver layer. Source of truth for "what the rules are." |
| **Compiler plugin** | Function that turns one `formal_expr.type` into Pyomo (or CP-SAT) constraints. |
| **CP-SAT** | Google OR-Tools constraint-programming solver; owns the sequencing layer in Phase 2. |
| **IIS** | Irreducible Infeasible Subsystem — minimal set of constraints whose conflict makes a solve infeasible. |
| **LangGraph** | LangChain's stateful agent-orchestration framework. |
| **LNS** | Large Neighborhood Search — repairs a plan by destroying and re-solving a small window. |
| **MCP** | Model Context Protocol — Anthropic's tool/service protocol; every backend (Catalog, Solver, Plan, Audit, SAP) is an MCP server. |
| **Patch** | Proposed change to a catalog row — JSON-style diff applied via `propose_patch`. |
| **Verifier** | Deterministic check (schema/unit/feasibility/business) that gates every agent output. Not an LLM. |
| **Warm cache** | Pre-built solver state reused across feasibility checks so the Verifier's solve takes seconds, not minutes. |
