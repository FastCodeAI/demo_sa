# DEMO Production & Packaging Scheduler — Problem Statement & End-to-End Approach

> Companion documents:
> - `README.md` — project overview and data analysis
> - `MODEL_SCOPING.md` — index sets, decision variables, parameters, gap log
> - `PROPOSED_SOLUTION_ARCH.md` — 4-layer architecture (User / Agent / Tool / Data) with LangGraph supervisor, specialist sub-agents, and MCP-served solver and data services

---

## 1. Problem statement

### 1.1 In one paragraph

DEMO operates a multi-line glass-ampoule packaging facility. Each year ~2,000 demand lines must be assigned to a `(packaging line, week, format)` slot, **upstream of** a production plan that decides which API runs on which week, with shelf-life-aware campaign sizing, sequence-dependent format changeovers, customer-volume bands (Piramal 1.8–2.0 M/month), and team-shared changeover labour that prevents two machines changing format at the same time. Demand is concentrated (Piramal alone is 36 %), one line (Marchesini GL) carries 61 % of volume, and 4 % of orders are VIP-flagged with a multiplier large enough to reorder the whole plan. The 50 numbered requirements in `Requirements EN.pdf` define what *correctness* looks like; reproducing them by hand in Excel is what DEMO does today.

### 1.2 Formal statement (compact)

Given:

- index sets `O, S, C, M_pack, M_prod, F, G, W, T, A, R` (see `MODEL_SCOPING.md` §1, with cardinalities `|O|=1,824, |S|=240, |M_pack|=4, |F|=5, |W|=52` for the 2025 PoC);
- parameters `demand[o,q]`, `total_qty[o]`, `format[o]`, rating fields, customer metadata, `bo[o]`, plus the inputs flagged in §9 of `MODEL_SCOPING.md` (changeover times, throughput, shift calendar, BoM, shelf life, scrap, due dates) — **currently missing**;
- knobs `Θ` (rating weights, band edges, lags, calendar, …);

find decisions `X = (x_pack, y_pack, co_pack, vol_pack, x_prod, qty_prod, api_active, inv, …)` that

```
maximize     w_fulfilment · Σ_o rating_score(o,Θ) · (total_qty[o] − unfilled[o])
           − w_changeover · Σ co_pack[m,f1,f2,w] · changeover_h[m,f1,f2]
           − w_idle       · Σ idle_hours[m,w]
           − w_tie_split  · Σ_c tie_split_dev[c]
           − w_late       · Σ_o late[o]
subject to   the 15 hard constraints in MODEL_SCOPING.md §5
             over horizons   annual ⊃ quarterly ⊃ weekly ⊃ real-time
```

### 1.3 Why it is hard

| Hardness source | Concretely |
|---|---|
| **Combinatorial core** | Sequence-dependent setup + no-overlap on shared changeover team is NP-hard even single-machine. |
| **Multi-stage coupling** | Production must finish ≥ 4 days before packaging *and* respect shelf-life storage caps — production decisions can render packaging plans infeasible weeks later. |
| **Multi-horizon coherence** | Annual ranking (req #27) must agree with quarterly confirmation, which must agree with weekly sequencing — the same data viewed at four resolutions. |
| **Live re-planning** | Photocells reports a slowdown / freeze (req #16) → the plan from yesterday is stale, but the tail is still good — full re-solve wastes work. |
| **Constraints are not static** | Sales adds VIP overlays, regulators change shelf life, mould changes alter formats. The list of constraints is itself an artefact under change control. |
| **Stakeholder reasoning load** | When a plan changes, *Production* needs the shift view (#13), *Sales* needs the order-tracker view (#14), *Production team* wants what-ifs (#15), and *Compliance* wants the audit. The same answer needs three different explanations. |

### 1.4 The four horizons that must stay coherent

```
                                      ┌──────────────┐
                                      │  ANNUAL      │  capacity envelope, order-rank, BoM availability
                                      │  (~52 wk)    │  → confirms feasible set O*
                                      └──────┬───────┘
                                             │ feeds into
                                      ┌──────▼───────┐
                                      │  QUARTERLY   │  full MILP for ~13-week chunk
                                      │  (~13 wk)    │  → assignment + campaign sizing
                                      └──────┬───────┘
                                             │ feeds into
                                      ┌──────▼───────┐
                                      │  WEEKLY      │  CP-SAT sequencing with sequence-dep setups
                                      │  (~5–7 d)    │  → exact timeline + Gantt
                                      └──────┬───────┘
                                             │ feeds into
                                      ┌──────▼───────┐
                                      │  REAL-TIME   │  LNS repair on disruption
                                      │  (minutes)   │  → patched schedule, not full re-solve
                                      └──────────────┘
```

---

## 2. Approach overview — three-tier hybrid

The optimizer alone is not the product. The product is an **agent-orchestrated optimizer** in which large language models reason about constraints and explanations, while the solvers do what they do best — search.

```
LAYER A.  DECLARATIVE MODEL                       (the "what")
            MiniZinc + Pyomo, plus a Constraint Catalog
LAYER B.  SOLVER ENSEMBLE                         (the "how, fast")
            MILP master (HiGHS / Gurobi)
            CP-SAT sub-solver (OR-Tools)
            LNS repair (OR-Tools)
            Discrete-event simulator (validation)
LAYER C.  AGENT ORCHESTRATION                     (the "why, what-if, what-now")
            LangGraph supervisor + 6 specialist agents
            Each tool is an MCP server, with a verifier-in-the-loop
```

LLMs do not *solve* the optimization problem. They translate, explain, validate, and bridge — the solver is still the source of truth for any number that ends up in SAP.

---

## 3. How constraints are modeled

### 3.1 Constraint catalog as the spine

Every constraint is a row in a versioned **Constraint Catalog**, served by the Master-Data MCP service:

| field | example |
|---|---|
| `id` | `C-037` |
| `requirement_ref` | `req #37` |
| `nl_description` | "Piramal must receive between 1.8M and 2.0M ampoules per calendar month." |
| `category` | `customer_band` |
| `formal_expr` | `forall t in T: 1_800_000 ≤ sum(o in O_piramal, w in month(t)) vol_pack[o,w] ≤ 2_000_000` |
| `parameters` | `{piramal_min: 1_800_000, piramal_max: 2_000_000}` |
| `solver_layer` | `MILP_master` |
| `severity` | `hard` |
| `owner` | `Sales / Account Mgr` |
| `version` | `2026-05-07.v3` |
| `verifier_state` | `passed` |

The MiniZinc / Pyomo model is *generated* from the catalog at solve time, not hand-written. This makes "Piramal raises the band to 2.5 M" a one-row edit, not a code change.

### 3.2 NL → formal pipeline

```
User says: "Piramal can absorb up to 2.5M/month from Q3 onwards"
        │
        ▼
Constraint-Elicitation Agent
  ├── Parses to {entity: Piramal, dim: month, op: ≤, value: 2.5M, scope: Q3..Q4}
  ├── Drafts catalog row patch (id reuse if amending)
  └── Renders MiniZinc fragment
        │
        ▼
Verifier-in-the-loop
  ├── Schema check    — fields, types
  ├── Unit check      — 2.5M is "ampoules/month", not "boxes/week"
  ├── Solver-feasibility check — inject into a *cached* warm-started model, see if SAT
  └── Business-rule check     — Q3..Q4 cannot loosen if a contract says fixed
        │
        ▼
Catalog write (with diff vs prior version)
        │
        ▼
Re-solve trigger queued
```

If any verifier step fails, the agent narrates the failure to the user and offers an amendment — it never writes a broken constraint to the catalog.

### 3.3 Constraint categories and where they live

| Category | Solver layer | Modelling pattern | Example req |
|---|---|---|---|
| **Demand fulfilment** | MILP master | Linear `Σ vol = total_qty − unfilled` | #2, #10 |
| **Capacity** | MILP master | `Σ qty / throughput + setup ≤ avail_h` | #5, #19 |
| **Customer bands** | MILP master | Linear two-sided bound per month | #37 |
| **Order indivisibility (org)** | MILP master | Big-M or single-week assignment | #39 |
| **Format-once-per-cycle** | MILP master | `Σ_w y[m,f,w] ≤ 1` per cycle | #8 |
| **Sequence-dependent setup** | CP-SAT | `circuit` / `regular` constraints, sequence variables | #5, #33 |
| **No-overlap on shared team** | CP-SAT | `no_overlap` on changeover intervals across machines | #36 |
| **Mutex resources (Farcon ⊕ Dividella)** | CP-SAT | Cumulative resource of capacity 1 | #35 |
| **EVATON 2-batch / 3-week gap** | CP-SAT | Forbidden-region / `forbidden_assignments` | #49 |
| **Lag (prod→pack ≥ 4d, prod→label ≥ 3d)** | CP-SAT | Precedence with min lag | #46 |
| **Shelf-life storage** | CP-SAT | Time-window on inventory intervals | #48 |
| **Campaign coverage 6/9 mo** | MILP master | Aggregated demand horizon | #45 |
| **Tie-fairness** | MILP master | Penalty on `|share[c] − fair_share|` | #40 |
| **Schedule highest-score weeks first** | MILP master (soft) | Lex-min on inverted scores | #41 |
| **Glass machine selection by volume** | MILP master | Eligibility set `eligible(o)` | #31, #32 |

---

## 4. How the four solvers cooperate

### 4.1 Decomposition

```
                ┌──────────────────────────────┐
                │  Tier-1: Annual feasibility  │  LP / greedy
                │  Inputs:  total_qty, ratings │  Output: O* (confirmed orders)
                │  Time:    seconds            │
                └─────────────┬────────────────┘
                              │ O*, capacity envelope
                ┌─────────────▼────────────────┐
                │  Tier-2: Quarterly MILP      │  HiGHS / Gurobi via Pyomo
                │  Inputs:  O*, all hard cons. │  Output: x_pack, y_pack, qty_prod
                │  Time:    minutes            │  warm-started from prior cycle
                └─────────────┬────────────────┘
                              │ assignment locked, lines/weeks fixed
                ┌─────────────▼────────────────┐
                │  Tier-3: Weekly CP-SAT       │  OR-Tools
                │  Inputs:  Tier-2 assignment  │  Output: timed Gantt, changeovers
                │  Time:    seconds–minutes    │
                └─────────────┬────────────────┘
                              │ committed schedule
                ┌─────────────▼────────────────┐
                │  Tier-4: LNS repair          │  OR-Tools LNS
                │  Trigger: Photocells freeze  │  Output: patched local window
                │  Time:    < 1 minute         │  Disruption-Response Agent owns this
                └──────────────────────────────┘

                ┌──────────────────────────────┐
                │  Validation: DES simulator   │  Runs after Tier-3 and Tier-4
                │  Confirms KPIs on a stochastic replay before commit
                └──────────────────────────────┘
```

### 4.2 Why this split

- **MILP master** handles linear, aggregated decisions (assignment, lot sizing, customer fairness) where dual variables are cheap and warm-starts work.
- **CP-SAT** handles sequencing — `no_overlap`, sequence-dependent setup, forbidden regions — which are awkward to encode as MILP and where CP-SAT's propagation is faster.
- **LNS** repairs after disruption without re-solving from scratch; the `ALAS-style` Disruption-Response Agent fixes a small destruction radius around the failure window.
- **DES simulator** validates that a feasible schedule is also *robust* under throughput noise (Photocells variance) before SAP commit.

---

## 5. How LLM agents bridge the layers

The 6 specialist agents in `PROPOSED_SOLUTION_ARCH.md` each own a specific bridge:

| Agent | Bridge | Inputs | Outputs |
|---|---|---|---|
| **Supervisor** | User intent → agent route | NL request + context | sub-agent invocation graph |
| **Constraint-Elicitation** | NL → formal expr + Catalog row | NL constraint, current catalog | catalog patch (verified) |
| **Schedule-Generation** | Catalog + data → solver call → plan | catalog version, data snapshot, knobs `Θ` | plan + KPIs + provenance |
| **Infeasibility (OptiChat-style)** | Infeasible solver result → NL diagnosis | IIS / minimum infeasible subsystem | "These three constraints clash, and here's the cheapest one to relax" |
| **What-If (OptiGuide-style)** | Parameter delta → re-solve + diff narration | knob delta or hypothetical event | counterfactual plan + KPI delta |
| **Disruption-Response (ALAS-style)** | Telemetry event → LNS repair + narration | Photocells freeze, ProdAction qty | local repair patch |
| **Explanation** | Plan numbers → audience-tuned narrative | committed plan, persona | shift view / order tracker / KPI dashboard |

**Verifier-in-the-loop** sits between every agent and the catalog or SAP write:

```
Agent draft  ──►  Verifier
                  ├── Schema check        (JSON shape, types)
                  ├── Unit check          (units consistent with catalog)
                  ├── Feasibility check   (solver SAT on a frozen warm cache)
                  ├── Business-rule check (no contract violation, no SOX issue)
                  └── Audit-trail log     (who/what/why, immutable)
                  │
                  ▼
              Commit / reject
```

LLM hallucinations cannot reach SAP — every value is gated through the solver and the rules engine.

---

## 6. Bridging horizons (parent constraints, child warm-starts)

The same model is instantiated four times at four resolutions. Coherence is enforced by **lifting** parent decisions into child constraints and **descending** child observations into parent updates.

```
Annual (Tier-1)           ───► confirmed O*, capacity envelope
                                │
                                ▼ become parameters
Quarterly (Tier-2)         ───► assignment x_pack, y_pack, qty_prod
                                │
                                ▼ become bounds (warm-start + fix)
Weekly (Tier-3)            ───► sequence + changeover plan
                                │
                                ▼ become committed intervals
Real-time (Tier-4)         ◄───  telemetry says: window failed
                                │
                                ▲ rolls up: was Tier-2 over-optimistic?
Quarterly                   ◄───  if yes, re-rank in Tier-1 next cycle
```

This is a **rolling-horizon** design: every Monday morning the cycle re-fires, parameters update, last week's actuals replace last week's plan, and the four tiers re-converge.

---

## 7. Reference data flow — one full cycle

```
0. Cron (Mon 06:00) or user "run scheduler"
1. SAP Bridge MCP    : pull Planned Orders, Process Orders, BoM, mat-avail
2. Telemetry MCP     : pull last-week Photocells throughput + ProdAction batch sizes
3. Master-Data MCP   : load Constraint Catalog (current version)
4. Schedule-Generation Agent
   a. Tier-1 LP             — Solver MCP, mode=annual
   b. Tier-2 MILP            — Solver MCP, mode=quarterly, warm-start from last cycle
   c. Tier-3 CP-SAT          — Solver MCP, mode=weekly
   d. DES validate           — Simulator MCP, replays under Photocells variance
5. Verifier                  : schema + unit + feasibility + business rules
6. Explanation Agent
   a. Production view (Gantt by line × shift)         — req #13, #15
   b. Sales view    (order tracker)                   — req #14
   c. KPI dashboard (utilisation, fulfilment, OTIF)   — req #22
7. Approval queue (User Layer) — supervisor approves / amends
8. Audit MCP                 : version + diff vs last cycle
9. SAP Bridge MCP            : write Process Orders   — req #12
10. Live monitor             : Photocells events stream → Disruption-Response Agent
    a. On freeze ≥ τ minutes → Tier-4 LNS repair → re-verify → push to SAP
```

---

## 8. New / changing constraints — the change flow

The system is built so that changing a constraint is **a data operation**, not a deployment.

```
"From Q3 onward, the changeover team is 2 people instead of 1."
                   │
                   ▼
Constraint-Elicitation Agent
   - identifies C-036 (no-overlap of changeover team)
   - amends `team_capacity` parameter from 1 to 2 with effective_from = Q3
                   │
                   ▼
Verifier
   - schema OK, unit OK
   - feasibility check: does Q3 onwards still solve?  yes  →  pass
                       does Q1–Q2 still respect team_capacity = 1?  yes  →  pass
   - business-rule check: HR has signed off on the staffing change? gate on signed flag
                   │
                   ▼
Catalog version bump  v3 → v4   (diff:  C-036.team_capacity 1→2 from W27)
                   │
                   ▼
Re-solve trigger:   Tier-2 from W27 onward, warm-started from v3 plan
                   │
                   ▼
What-If Agent narrates:
   "v4 reduces total changeover hours by 14 % in Q3-Q4
    and frees 6 days of Marchesini GL capacity.
    Piramal monthly volume rises from 2.30M → 2.34M (still > band, see C-037)."
                   │
                   ▼
User approves       →  commit, audit, push to SAP
User rejects        →  catalog rolled back, audit logs the rejection rationale
```

Crucially: **the LLM only writes catalog patches**. The solver re-runs the formal model. The verifier independently re-checks. The user explicitly approves. None of those four can be skipped.

---

## 9. How this addresses each cluster of requirements

| Requirement cluster | Mechanism |
|---|---|
| Configurable scenarios (#1, #4) | Knobs `Θ` are catalog rows; What-If Agent overlays deltas on `Θ` and re-solves. |
| Real-time monitoring + adjustment (#2, #3, #16) | Telemetry MCP → Disruption-Response Agent → Tier-4 LNS repair. |
| Order-ranking algo (#4, #41) | `rating_score(o,Θ)` is a configurable function inside Schedule-Generation. |
| Changeovers, maintenance, machine state (#5) | Constraint catalog + CP-SAT sequencing. |
| Photocells / ProdAction inputs (#6) | Telemetry MCP supplies throughput, batch size as parameters at solve time. |
| Deviation alerts (#7) | Live monitor compares actual vs Tier-3 timeline; emits alerts to Disruption-Response. |
| Format-change rule, campaign cuts (#8, #33) | MILP `y_pack[m,f,w] ≤ 1` per cycle; CP-SAT sequence-dep setup. |
| Backup machines, configurable lines / SKUs (#9) | Catalog `eligible(o)` set, editable. |
| Multi-objective trade-off (#10) | Linear weighted sum on `Θ`; Pareto-front exploration via What-If. |
| Production + Packaging schedules (#11) | Two-stage model with lag (req #46). |
| SAP integration (#12, #20–24) | SAP Bridge MCP — read Planned Orders, write Process Orders, no double-entry. |
| Role-based UIs (#13–15, #22) | Explanation Agent renders persona views from one underlying plan object. |
| 52-week horizon, mixed cycle durations (#17, #18) | Rolling-horizon Tier-1/2 absorbs both. |
| Capacity feasibility (#19) | Tier-1 LP. |
| Manual breakdown declaration (#29) | UI form → Telemetry MCP synthetic event → Disruption-Response. |
| Manual edit before SAP push (#30) | Approval queue in User Layer; edits are constraint patches under verifier. |
| Glass-container machine rules (#31, #32) | `eligible(o)` set + backup-line policy in catalog. |
| 2025 PoC scope (#34) | This dataset = 90.4 M ampoules, 1,824 demand lines. |
| Piramal band, Farcon ⊕ Dividella, no-overlap team (#35–37) | Hard constraints in catalog; Verifier prevents commits that violate. |
| Order-indivisibility for orgs (#39) | `is_org` flag → single-week constraint. |
| Tie-fair distribution (#40) | Soft penalty on share deviation. |
| Production-side rules (#42–50) | Production-tier MILP/CP block; EVATON forbidden regions; scrap parameter. |

---

## 10. Verification & audit (non-negotiable)

Every committed plan and every constraint mutation is:

1. **Schema-validated** against the catalog typing.
2. **Unit-checked** (ampoules vs boxes, hours vs days, weeks vs months).
3. **Feasibility-checked** by a *real* solver run on a frozen warm cache — not by the LLM.
4. **Business-rule-checked** against contract / regulatory / SOX rules.
5. **Audit-logged** (who, what, when, why, prior version) — immutable.

The LLM is **never** the source of truth on a number. The solver is.

---

## 11. Out of scope (this document)

- Implementing any of the above. This is the approach, not the build plan.
- Closing the gaps in `MODEL_SCOPING.md` §9 (changeover times, throughput, BoM, shelf life, due dates, etc.). Those are blocking inputs and remain client-side.
- The PoC dataset is glass ampoules only (req #34) — extending to plastic ampoules / bottles / lyophillized is straightforward in this design (add lines, add formats, add catalog rows) but not in scope for the 2025 PoC.

---

## 12. Suggested first vertical slice (for a future build)

When implementation starts, the smallest end-to-end slice that exercises every layer:

1. **Data**: Q1 2025 only, 3 customers (Piramal, CIS Farma, UK), 2ml + 5ml only, Marchesini GL + Farcon only.
2. **Catalog**: 8 hard constraints (capacity, fulfilment, Piramal band, Farcon ⊕ Dividella, no-overlap team, prod→pack lag, format-once-per-cycle, glass-machine eligibility) + 1 soft (changeover penalty).
3. **Solver**: Pyomo + HiGHS for Tier-2 only; skip Tier-1 (manual confirm), skip Tier-3 (use weekly aggregation), skip Tier-4 (no live disruption).
4. **Agents**: only Schedule-Generation + Infeasibility + Explanation. Skip What-If, Disruption-Response.
5. **Output**: a Gantt for Marchesini GL + Farcon over 13 weeks, KPI summary, infeasibility narration if catalog is unsatisfiable.

That slice is small enough to ship in a sprint and rich enough to validate the whole architecture pattern (catalog → solver → verifier → narration → audit) before scaling to the full problem.

---

## Glossary / Abbreviations

| Acronym | Expansion / Meaning |
|---|---|
| **ALAS** | An LLM-agent pattern for disruption-response that prefers local repair over full re-solve (referenced in `PROPOSED_SOLUTION_ARCH.md`). |
| **API** | Active Pharmaceutical Ingredient — the substance produced upstream of packaging; drives the "one API per production week" rule (req #42). |
| **BoM** | Bill of Materials — finished-product → semi-finished → API decomposition (req #26). |
| **CP-SAT** | Constraint Programming-SATisfiability solver (Google OR-Tools); used for the sequencing layer (changeovers, no-overlap, sequence-dependent setup). |
| **DES** | Discrete-Event Simulation — replays the schedule under throughput noise to validate robustness before SAP commit. |
| **EVATON** | Specific semi-finished class with a 2-batch-at-a-time / ≥3-week-gap rule (req #49). |
| **Fiori** | SAP's design system / UX framework for embedded enterprise UIs. |
| **Gurobi / HiGHS** | Commercial / open-source MILP solvers. |
| **IIS** | Irreducible Infeasible Subsystem — minimal set of conflicting constraints; output of the Infeasibility Agent. |
| **KPI** | Key Performance Indicator. |
| **LangGraph** | LangChain's stateful agent-orchestration framework (the supervisor + sub-agents in the architecture). |
| **LLM** | Large Language Model. |
| **LNS** | Large Neighborhood Search — metaheuristic for *repairing* a schedule by destroying and re-optimising a small window. |
| **LP** | Linear Programming. |
| **MCP** | Model Context Protocol — Anthropic's tool/service protocol; each backend service (Solver, Master-Data, SAP Bridge, Telemetry, Simulator, Audit) is exposed as an MCP server. |
| **MILP** | Mixed-Integer Linear Programming. |
| **MiniZinc** | Declarative constraint-modelling language (solver-agnostic); the catalog renders into MiniZinc fragments. |
| **NL** | Natural Language. |
| **OptiChat** | LLM-driven pattern for narrating infeasibility (IIS) to non-technical users. |
| **OptiGuide** | Microsoft Research pattern for LLM-driven what-if analysis with parameter deltas + re-solve. |
| **OR-Tools** | Google's open-source operations-research toolkit. |
| **OTIF** | On Time In Full — the order-fulfilment KPI. |
| **PoC** | Proof of Concept. |
| **Pyomo** | Python algebraic modelling language for optimisation. |
| **Q1 / Q2 / Q3 / Q4** | Calendar quarters. |
| **SAP** | The ERP system (DEMO's order-of-record); the SAP Bridge service reads Planned Orders and writes Process Orders. |
| **SAT** | Boolean Satisfiability — the underlying solver class behind CP-SAT. |
| **SKU** | Stock Keeping Unit (SAP Material Number). |
| **SOX** | Sarbanes-Oxley Act — financial-controls compliance regime; constrains audit-trail and approval flows. |
| **UI** | User Interface. |
| **Unicef / Imres / ICRC / IDA / MSF** | Humanitarian organisations whose orders cannot be split (req #39). |
| **VIP** | Very-Important order flag (weight 10,000 in the rating). |
| **XYZ** | Three-axis customer-product rating: X = on-time/in-full grade (A–D), Y = order flexibility, Z = profitability tier. |
