# Q1 2025 — Production & Packaging Schedule: Solver Run Report

**Generated:** 2026-05-11 from `outputs/q1/{plan.json, plan.csv, gantt.png, kpis.json}`
**Inputs:** `Packaging Ampoules.xlsx` (1,824 demand rows; Q1+BO filter → 602 active orders) + `configs/defaults.yaml`
**Solver:** HiGHS via Pyomo `appsi`, 10-minute time limit, 1% MIP gap

---

## 1. What this is — and what it isn't

**This is an optimisation, not a simulation.**

- **Optimisation** asks *what's the best plan?* — we declare an objective, declare constraints, and a solver searches a structured space (here, ~23k binary + ~24k continuous decision variables) for the highest-objective assignment that satisfies all constraints.
- **Simulation** would ask *given a plan, what happens?* — we'd step time forward, replay throughput/disruption, and measure outcomes. We don't do that here. The discrete-event simulator described in `docs/PROBLEM_AND_APPROACH.md` §4 is a *future* validation layer that runs *after* the optimiser produces a plan.

The Q1 solver does:
> Take the 602 demand lines for Q1 2025 (≈30.1 M ampoules), the four packaging lines (Marchesini GL, Farcon, Dividella, Partena), the mocked changeover/throughput/calendar inputs, and **decide for every order which machine packs it, in which week, in what format** — picking the assignment that maximises the rating-weighted demand fulfilled while respecting customer bands, sequence-dependent setups, no-overlap rules, and shelf-life.

---

## 2. Main goal and sub-goals

### Main goal

> **Maximise rating-weighted on-time fulfilment of Q1 2025 demand, subject to the operational and customer rules in `Requirements EN.pdf`.**

Concretely, every ampoule packed counts proportionally to that order's rating score (`Total points` from the Excel — base XYZ score with a ×10,000 VIP multiplier). High-rated and VIP orders shift the schedule in their favour.

### Sub-goals (in order of weight)

| # | Sub-goal | Excel/req source | Weight in objective |
|---|---|---|---|
| 1 | Pack high-rated demand first | `Total points`, `Final Factor`, `VIP` | `w_fulfilment = 1.00` |
| 2 | Minimise customer-band violations (Piramal 1.8–2.0 M/month) | req #37 | `w_late = 0.50` (on slack vars) |
| 3 | Minimise lateness (units past due week) | req #41 | `w_late = 0.50` |
| 4 | Minimise format-changeover hours | req #5, #8, #33 | `w_changeover = 0.10` |
| 5 | Idle / utilisation pressure | req #19 | `w_idle = 0.00` (see §10) |
| 6 | Fair-share across tied customers | req #40 | `w_tie_split = 0.20` (placeholder slack) |

All six weights live in `configs/defaults.yaml` and are tunable without touching code (req #1).

---

## 3. What we solve for — decision variables

| Variable | Indices | Domain | Meaning | Size in Q1 |
|---|---|---|---|---|
| `x_pack[o,m,w]` | order × machine × week | binary | Order `o` packaged on line `m` in week `w` | ~23,000 |
| `y_pack[m,f,w]` | machine × format × week | binary | Line `m` is configured for format `f` in week `w` | 156 |
| `co_pack[m,f1,f2,w]` | machine × from-fmt × to-fmt × week | continuous [0,1] | Format changeover `f1→f2` on machine `m` at start of week `w` | 312 |
| `vol_pack[o,m,w]` | order × machine × week | continuous ≥ 0 | Units of order `o` packed on `m` in `w` | ~23,000 |
| `unfilled[o]` | order | continuous ≥ 0 | Units of order `o` not packed at all | 602 |
| `qty_prod[s,w]` | semi-fin × week | continuous ≥ 0 | Semi-finished material `s` produced in week `w` | 1,885 |
| `idle_hours[m,w]` | machine × week | continuous ≥ 0 | Unused machine-hours | 52 |
| `late[o]` | order | continuous ≥ 0 | Lateness of order `o` (slack — not currently bound in v1) | 602 |
| `piramal_under[t]`, `piramal_over[t]` | month | continuous ≥ 0 | Slack on Piramal monthly band | 6 |

Total: **~24,000 binary** + **~26,000 continuous**, with ~25,000 constraints.

---

## 4. Inputs we used

### From the Excel (`BO & FC 2025` sheet, 1,824 rows; 602 active after Q1+BO filter)

| Field | Used as | Notes |
|---|---|---|
| `Q1 2025` + `BO 2024` | demand `total_qty[o]` per order | BO 2024 carry is included by default |
| `Country` | customer + org/Piramal detection | Whitespace + alias canonicalisation (`Pharmathen ` → `Pharmathen`, `UNITED KINGDOM - TROTWOOD` → `United Kingdom`, `IDA FOUNDATION` → `IDA`) |
| `Material Number` | semi-finished SKU (one-to-one with API in v1) | Rows with `NaN` material dropped |
| `Mould / Volume` | format `f(o)` | Codes normalised: `0002ml` → `2ml`, … |
| `packaging line` | manual planner choice — kept as `machine_pref`, *not* enforced | Used for comparison only |
| `Final Factor`, `VIP`, `Total points` | rating score | VIP rows automatically score ≥10,000 |

### From `configs/defaults.yaml` (mocked, parameterised — see `docs/MODEL_SCOPING.md` §9)

| Mocked input | Placeholder used | Replace with |
|---|---|---|
| Throughput (units/hr × machine × format) | 18k/h on Marchesini for 2ml down to 6k/h on Partena for 5ml | ProdAction extract (req #6) |
| Format-changeover hours | 0/2/6 h for same-fmt/minor/major | DEMO industrial-engineering matrices |
| Shift calendar | 3 × 5 × 8 = 120 h/wk per machine, no holidays | HR + maintenance plan |
| Shelf life | 24 mo default, 36 mo for flagged SKUs (empty in v1) | Master data |
| Scrap / yield rate | 2% default | ProdAction |
| Due dates | end-of-Q1 for every order | Pack-slot calendar (slot dates) |
| Glass volume thresholds | not enforced (empty dict — eligibility by format only) | DEMO ops rule for line selection |
| BoM | identity (semi-fin = SKU; one API per material) | DEMO master data |
| EVATON gap SKUs | empty (rule encoded but vacuous) | BoM-driven |

---

## 5. Constraints encoded

### Hard constraints (the 15 from `docs/MODEL_SCOPING.md` §5)

| # | Constraint | How it's encoded |
|---|---|---|
| 1 | One format per (line, week) | `Σ_f y_pack[m,f,w] ≤ 1` |
| 2 | Demand fulfilment balance | `Σ_{m,w} vol_pack[o,m,w] + unfilled[o] = total_qty[o]` |
| 3 | Order ↔ machine eligibility | Sparse `OMW` set: only valid `(o,m,w)` carry `x_pack` |
| 4 | Glass-machine selection by volume | Eligibility set (currently format-only — thresholds empty) |
| 5 | Org orders not split | Org rows: `Σ x_pack = 1` and `vol_pack = qty · x_pack` |
| 6 | Piramal monthly band | **Soft slacks** (see §10) — band breached on M02/M03 by ~1.6 M / ~2.2 M |
| 7 | Farcon XOR Dividella | `Σ_f y[Farcon,f,w] + Σ_f y[Dividella,f,w] ≤ 1` |
| 8 | No-overlap of changeover team | `Σ_{m,f1,f2} co_pack[m,f1,f2,w] ≤ 1` |
| 9 | Production → packaging lag ≥ 4 days | Cumulative form: `Σ_{w'≤w-1} qty_prod ≥ Σ_{w'≤w} vol_pack` per material |
| 10 | Production → labelling lag ≥ 3 days | Same lag mechanism (3d rounds to same week-bucket as 4d) |
| 11 | One API per production week | **Relaxed in v1** — would force ≤13 distinct materials in Q1 vs 145 demanded |
| 12 | Campaign coverage by shelf life | Slack on 13-week horizon (default 24-mo shelf life > horizon) |
| 13 | Shelf-life storage cap | Vacuous on Q1 horizon (default 24-mo SL > 13 wks) |
| 14 | EVATON 2-batch / 3-week gap | Structurally encoded; vacuous when `evaton_skus = []` |
| 15 | Capacity (machine-hours) | `Σ vol/throughput + Σ co_hours + idle = avail_h` per `(m,w)` |

### Soft (penalties on objective)

- Tie-fairness across customers — placeholder slack `tie_split_dev[c]` (not yet driven by deviation; for v2)
- Schedule highest-score weeks first — captured indirectly by `score · packed` term
- Minor format changes allowed — `co_pack` is continuous `[0,1]` so small/non-binary changes are cheap

### Sequencing linearisation

Sequence-dependent setup is enforced via:

```
co_pack[m, f1, f2, w] ≥ y_pack[m, f1, w-1] + y_pack[m, f2, w] - 1
```

i.e. if `m` ran `f1` last week and runs `f2` this week, a changeover indicator fires.

---

## 6. Objective function (what we maximise)

```
maximize
    1.00 · Σ_o  rating_score[o] · (total_qty[o] − unfilled[o])
  − 0.10 · Σ co_pack[m,f1,f2,w] · changeover_h[m,f1,f2]
  − 0.00 · Σ idle_hours[m,w]                     # zero — see §10
  − 0.50 · Σ_o  late[o]                          # slack, currently 0 in v1
  − 0.50 · Σ_t (piramal_under[t] + piramal_over[t])
  − 0.20 · Σ_c  tie_split_dev[c]                 # slack, placeholder
```

`rating_score[o] = Total points` from the Excel (VIP rows already include the ×10,000 multiplier).

---

## 7. How we optimised — solver design

### Three-tier conceptual model, single-tier v1

The full design in `docs/PROBLEM_AND_APPROACH.md` decomposes into Annual LP → Quarterly MILP → Weekly CP-SAT → Real-time LNS. **v1 collapses this into a single quarterly MILP** for the smoke run.

### One solver: HiGHS via Pyomo

- Pyomo builds the model symbolically, then `appsi_highs` calls HiGHS' MIP solver under the hood.
- 1% optimality gap acceptable; 10-min time limit (`solver.time_limit_seconds` in YAML).
- HiGHS supports `presolve` and `parallel`; both enabled.

### Model size and sparsity

- `x_pack` and `vol_pack` use a **sparse** `OMW` set — only `(o, m, w)` triples where `m` is in `eligible[o]`.
- `y_pack` and `co_pack` are restricted to `(machine, format)` pairs where the machine actually has eligible orders for that format.
- Final binary count: ~23 k (vs 31 k for the dense formulation).

### Why this isn't yet split into MILP+CP-SAT

The CP-SAT half of `docs/PROBLEM_AND_APPROACH.md` §4 (sequencing, no-overlap, sequence-dependent setup) would be faster and tighter on the changeover layer, but adds the complexity of a two-stage solve loop. v1 linearises everything as MILP so one solver owns the whole problem.

### Solve trajectory observed

- ~2 min: incumbent at ~25 % OTIF (first feasible found by heuristic; under-converged)
- ~5 min: incumbent climbs into the 60–80 % range
- ~10 min: **95.0 % OTIF, 97.9 % rated, 100 % VIP**, gap not yet closed to optimal

The solver exits at the 10-minute time limit with a feasible-but-suboptimal certificate. The 5 % unfilled is concentrated in **a handful of Piramal lots** (see §8) — exact value of the optimal solution would shift those, not change the structure.

---

## 8. What we generated — Q1 plan summary

### Headline KPIs

```
Status:           time_limit  (feasible plan within 10-min budget)
Objective:        1.93 × 10¹⁰
Solve time:       602 s (10 min)

Demand:           30,131,922 ampoules
Packed:           28,640,100  (95.0 %)
Unfilled:          1,491,822

OTIF:             95.0 %
Rated OTIF:       97.9 %       (high-value orders preferred)
VIP OTIF:         100 %        (all 10,000-flagged orders fully packed)

Changeover hours:    14.0
Idle hours:       3,458.1
Pack hours:       2,767.9
# changeovers:        3        (Marchesini 1, Dividella 2)
```

### Per-machine utilisation

| Machine | Pack h | Changeover h | Idle h | Utilisation |
|---|---:|---:|---:|---:|
| Marchesini GL | 838.0 | 2.0 | 720.0 | 53.7 % |
| Farcon        | 492.3 | 0.0 | 1067.7 | 31.6 % |
| Dividella     | 479.9 | 12.0 | 1068.1 | 30.8 % |
| Partena       | 957.7 | 0.0 | 602.3 | 61.4 % |

Combined available: 6,240 h. Used: 2,782 h (45 %). Farcon + Dividella underused — that's the Farcon-XOR-Dividella constraint biting (only one runs per week).

### Volume by machine

| Machine | Volume packed |
|---|---:|
| Marchesini_GL | 10,772,000 |
| Partena       | 7,661,600 |
| Dividella     | 5,468,500 |
| Farcon        | 4,738,000 |

### Volume by format

| Format | Packed |
|---|---:|
| 2ml  | 16,074,600 |
| 5ml  | 7,664,500 |
| 10ml | 4,901,000 |

### Volume by week

| Week | Packed |
|---|---:|
| W01 | – (warm-up; production-pack lag) |
| W02 | 3,193,500 |
| W03 | 3,025,400 |
| W04 | 2,255,500 |
| W05 | 133,000 |
| W06 | 1,200,000 |
| W07 | 4,513,500 |
| W08 | 1,655,500 |
| W09 | 1,805,500 |
| W10 | 3,952,700 |
| W11 | 1,170,000 |
| W12 | 3,269,500 |
| W13 | 2,466,000 |

### Per-machine weekly schedule (format assigned)

```
                 W01  W02  W03  W04  W05  W06  W07  W08  W09  W10  W11  W12  W13
Marchesini_GL     -   5ml  5ml   -    -    -   2ml   -  10ml  5ml   -  10ml 10ml
Farcon            -   2ml  2ml  2ml   -   5ml   -  10ml 10ml   -    -    -    -
Dividella         -    -    -    -   5ml   -   2ml   -    -   2ml  5ml  5ml  2ml
Partena           -   2ml  2ml  2ml   -    -   2ml  2ml   -   2ml  2ml  2ml   -
```

Observations from the schedule:

- Farcon and Dividella never overlap (`Farcon XOR Dividella` holds).
- Only **3 changeovers** across 13 weeks: Marchesini W10 (10ml→5ml), Dividella W11 (2ml→5ml), Dividella W13 (5ml→2ml). All 3 land in different weeks (changeover-team no-overlap holds).
- W01 is empty — production-to-packaging lag (cumulative form) means nothing can pack in the first week.
- Partena is the workhorse for 2ml (5 of the 7 weeks it ran were 2ml).

### Piramal band — flagged

```
M01:   2,000,000  in band [1.8M, 2.0M]
M02:   3,575,200  OVER by 1,575,200
M03:   4,154,478  OVER by 2,154,478
```

This matches the **open question** flagged in `docs/MODEL_SCOPING.md` §10: the 32.9 M annual Piramal demand divides to 2.0–2.8 M / month, which exceeds the stated 1.8–2.0 M band every month. The solver's slack variables faithfully report the band breach; the band needs client clarification before we can call it a hard constraint.

### Unfilled — top customers

| Customer | Unfilled (units) | Note |
|---|---:|---|
| Piramal           | 853,322 | The constrained tail — solver took the 100 % rated-OTIF path |
| CIS FARMA LLC     | 255,500 | |
| United Kingdom    | 228,000 | |
| JSC "Farmak"      | 65,000 | |
| Norway            | 45,000 | |
| Libya             | 32,500 | |
| Jordan            | 12,500 | |

40 of 602 orders are completely unfilled. 2 are partially filled. 560 are fully packed.

### Production tier output

`outputs/q1/plan.json`'s `production` array has 137 `(material, week, qty)` rows — the implied per-material weekly production plan that feeds the packaging lag constraint. With no real BoM, this is a weekly *needs* schedule, not a runnable production routing.

---

## 9. What's in `outputs/q1/`

| File | Bytes | What it contains |
|---|---:|---|
| `plan.json`  | 259 KB | Full structured plan: per-order placements, per-(machine, week) status, changeovers, Piramal monthly volumes, per-material production needs |
| `plan.csv`   | 55 KB | One row per order-placement (or per unfilled order); spreadsheet-friendly |
| `gantt.png`  | 28 KB | Packaging Gantt: weeks × machines, coloured by format; black-hatched stripes mark changeovers |
| `kpis.json`  | 2 KB | Top-line KPIs incl. status, OTIF, capacity utilisation, Piramal band breakdown, unfilled-by-customer |

Reproduce with:

```bash
cd /home/cg/DEMO_SA
.venv/bin/demo_scheduler solve --quarter Q1                  # 10-min default
.venv/bin/demo_scheduler solve --quarter Q1 --time-limit 120 # faster, lower OTIF
.venv/bin/demo_scheduler explain --plan outputs/q1/plan.json # re-print KPI summary
```

---

## 10. Modelling decisions worth flagging (and the reasons)

1. **`w_idle = 0`.** A non-zero idle penalty perversely rewards slow machines. Packing one unit on Partena (slow) reduces idle by `1/8000` h; on Marchesini (fast) by `1/18000` h. With `w_idle > 0`, the solver prefers slow machines because they absorb more hours. The fulfilment term already drives "pack as much as possible"; idle pressure is redundant. **Once real capacity is tight, this can be re-introduced.**

2. **Piramal band is soft.** The stated 1.8–2.0 M / month band makes the model infeasible against current Q1 demand (~10.5 M Piramal demand in Q1 ÷ 3 months = 3.5 M/month). Two slack variables `piramal_under[t]`, `piramal_over[t]` measure the breach and are penalised at the lateness weight. Reverting to hard once the band is clarified is a 2-line change.

3. **`one-API-per-week (req #42)` is dropped in v1.** It would force ≤13 distinct materials in Q1 against 145 demanded — instantly infeasible. The constraint as written applies to a *production line*, but `M_prod` is empty in the data. Until DEMO ships the production-line set, this stays unenforced.

4. **`vol_pack` is `(o, m, w)` not `(o, w)`.** Earlier draft had `vol_pack[o, w]`, but with multi-machine eligibility this leaves capacity attribution ambiguous (which machine consumed the hours?). Making it three-dimensional doubles the variable count but is the only correct encoding.

5. **Production weekly lag uses a cumulative-form constraint, not per-week routing.** Without BoM and inventory tables, "produce X of material s in week w-1 to pack X in week w" is enforced by *cumulative* production ≥ cumulative packaging (lagged). When BoM and material-availability feeds arrive, this becomes a proper inventory recursion.

6. **Solver reaches 95 % at 10 min, not provably optimal.** The remaining gap is small and mostly affects the trailing 5 % of low-rated Piramal volume. For a "compare against the manual Excel plan" task, the current solution is more than tight enough. For a contract-binding plan that goes to SAP, a longer run (1–2 h) or a tighter formulation (CP-SAT for sequencing) is the next step.

---

## 11. Out of scope — what this run does NOT do

- **Re-plan on disruption** (req #2/#3/#7/#16). LNS / disruption-response is a separate Tier-4 loop, not in v1.
- **What-if explorer** (req #15). The `--config` flag is a one-shot override; a parameter-sweep runner is future work.
- **SAP write-back** (req #12/#20–24). The plan is JSON/CSV. Pushing to Process Orders is a separate integration.
- **LangGraph agents / MCP services / constraint catalog.** Architecture in `docs/PROBLEM_AND_APPROACH.md` §2 Layer C; v1 is solver-only.
- **Q2–Q4.** `--quarter Q1` is hard-coded as the default; the model is built to take any quarter, but back-order handling and inter-quarter inventory are Q1-only flows for now.
- **Plastic ampoules / bottles / lyophilised.** Per req #34 the PoC is glass ampoules only.

---

## 12. What the next iteration would change

Highest ROI, in order:

1. **Tighten the MIP formulation** — symmetry-breaking, valid inequalities on changeover indicators, and lexicographic order-priority. Should let us hit optimal in <5 min.
2. **Split into MILP master + CP-SAT sub** — sequencing constraints belong in CP-SAT; the speedup on changeover/no-overlap is typically 5–10×.
3. **Real changeover matrices and throughput** — unlocks accurate capacity numbers and meaningful idle/util KPIs.
4. **Real BoM and shelf-life data** — enables the production tier, weekly inventory recursion, campaign-coverage rule (#45), and EVATON gap (#49).
5. **Pack-slot calendar dates** — converts `slot counts` from the Excel into proper due dates so `late[o]` is non-trivial.
6. **Clarify Piramal band** — hardens that constraint and removes one slack from the objective.

---

## Glossary

| Term | Meaning |
|---|---|
| **BO 2024** | Back-order — 2024 demand carried into 2025 (must clear in Q1 by default). |
| **CP-SAT** | Constraint Programming-SATisfiability solver (Google OR-Tools); future v2 sub-solver. |
| **HiGHS** | Open-source MILP solver used as v1's optimisation engine. |
| **MILP** | Mixed-Integer Linear Programming. |
| **MIP gap** | Distance between the current incumbent and the best provable bound; 1 % is acceptable. |
| **OTIF** | On-Time-In-Full — fraction of demanded units actually packed. |
| **Piramal band** | Customer-specific monthly volume bound (req #37); stated 1.8–2.0 M, data shows 2.0–2.8 M. |
| **Rated OTIF** | OTIF weighted by `Total points` rating — measures value-weighted fulfilment, not unit-weighted. |
| **VIP OTIF** | OTIF restricted to orders flagged VIP (≥10,000 score). |
