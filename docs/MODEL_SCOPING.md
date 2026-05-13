# DEMO Packaging & Production Scheduler — Model Scoping

**Status:** scoping only. No model is being built in this document. The goal is to make explicit *what is being decided*, *over what dimensions*, *with what knobs*, and *where the data is missing*.

All cardinalities below come from the 2025 PoC dataset (`Packaging Ampoules.xlsx`, sheet `BO & FC 2025`).

---

## 1. Index sets — the "levels" the model will reason over

| Set | Symbol | Size (2025 PoC) | Source | Notes |
|---|---|---|---|---|
| Demand lines (orders) | `o ∈ O` | **1,824** | `BO & FC 2025` rows | Each row = (Country/Customer × Material × line × format × quarter triplet). |
| Materials / SKUs | `s ∈ S` | **240** distinct material numbers (266 descriptions) | `Material Number` | Description varies by country labelling — needs canonical key. |
| Customers / countries | `c ∈ C` | **38** | `Country` column | Mixed entities: countries (`UK`, `GR`), customers (`Piramal`, `Pharmathen`), organizations (`Unicef`, `MSF`, `ICRC`). Needs canonical taxonomy. |
| Customer **classifications** | `k ∈ K` | ~3 (`ISD`, `GR`, …) | `Ranking` sheet | Coarse channel grouping. |
| Packaging lines | `m ∈ M_pack` | **4** | `packaging line` column | `Marchesini GL`, `Farcon`, `Dividella`, `Partena`. |
| Production lines / stages | `m ∈ M_prod` | TBD | **MISSING** | Tank, filling, etc. (req #28 mentions stages). |
| Formats (mould / volume) | `f ∈ F` | **5** | `Mould / Volume` | `0002ml`, `0003ml`, `0005ml`, `0010ml`, `0020ml`. |
| Container types | `g ∈ G` | 2 expected (`Glass`, `Plastic`) | inferred from PPT | Needed for the changeover-only-on-container-change rule (req #33). |
| Time buckets — weeks | `w ∈ W` | **52** (req #17) | calendar | Atomic scheduling unit for packaging. |
| Time buckets — months | `t ∈ T` | 12 | calendar | Required for Piramal monthly band (req #37). |
| Time buckets — quarters | `q ∈ Q` | 4 + `BO` | calendar | Used for tier-1 feasibility (req #27). |
| Shifts | `h ∈ H` | up to 3 / day, 5 days / week | configurable | Packaging cycles run weekly (req #18). |
| APIs (active ingredients) | `a ∈ A` | TBD | **MISSING** explicit list — derivable from material BoM | Drives the "one API per production week" rule (req #42). |
| Customer-product rating classes | `r ∈ R` | 35 final-factor combos + VIP flag | `Final Factor`, `VIP` | XYZ + delay grade `n` per the PPT. |

Total dimensionality of the *raw* schedule space (orders × lines × weeks):
**1,824 × 4 × 52 ≈ 380 k cells** for packaging, plus an analogous production layer. Tractable for MILP/CP after symmetry breaking and pre-filtering by line eligibility.

---

## 2. Decision variables

### Packaging schedule (the primary output)

| Variable | Indices | Domain | Cardinality (PoC) | Meaning |
|---|---|---|---|---|
| `x_pack[o, m, w]` | order × pack-line × week | binary | ≤ 380 k | Order `o` packaged on line `m` in week `w`. |
| `y_pack[m, f, w]` | line × format × week | binary | 4 × 5 × 52 = 1,040 | Line `m` is configured for format `f` in week `w` (campaign indicator — at most one per (m,w)). |
| `co_pack[m, f1, f2, w]` | line × from-fmt × to-fmt × week | binary | 4 × 5 × 5 × 52 = 5,200 | Format changeover on line `m` at the boundary of week `w`. |
| `vol_pack[o, w]` | order × week | continuous ≥ 0 | 1,824 × 52 ≈ 95 k | Units of order `o` packaged in week `w` (allows multi-week orders). |
| `start_pack[o]` | order | integer week | 1,824 | First week order `o` enters packaging. |
| `late[o]` | order | continuous ≥ 0 | 1,824 | Lateness of order `o` (weeks past `due[o]`). |
| `unfilled[o]` | order | continuous ≥ 0 | 1,824 | Unmet demand of order `o`. |

### Production schedule (semi-finished)

| Variable | Indices | Domain | Cardinality (PoC) | Meaning |
|---|---|---|---|---|
| `x_prod[s, m, w]` | semi-fin × prod-line × week | binary | TBD | Semi-finished `s` produced on line `m` in week `w`. |
| `qty_prod[s, w]` | semi-fin × week | continuous ≥ 0 | 240 × 52 ≈ 12 k | Quantity of `s` produced in week `w` (planned, after scrap/yield req #47). |
| `api_active[a, w]` | API × week | binary | \|A\| × 52 | The "one API per week" indicator (req #42). |
| `evaton_batch[w]` | week | binary / int | 52 | Special rule for semi-fin "T" / EVATON: 2 batches at a time, ≥3-week gap (req #49). |

### Inventory and linkage

| Variable | Indices | Domain | Meaning |
|---|---|---|---|
| `inv[s, w]` | semi-fin × week | continuous ≥ 0 | End-of-week stock of `s`. |
| `expire[s, w]` | semi-fin × week | continuous ≥ 0 | Stock written off due to shelf-life cap (req #48). |
| `prod_to_pack_lag[o]` | order | integer days | ≥ 4 days, packaging after production (req #46). |
| `prod_to_label_lag[o]` | order | integer days | ≥ 3 days. |

### Auxiliaries used by the objective

| Variable | Meaning |
|---|---|
| `idle_hours[m, w]` | unused machine-hours (penalised). |
| `tie_split_dev[c]` | deviation from fair-share when two customers tie on score (req #40). |
| `piramal_dev[t]` | deviation of Piramal monthly volume from band centre (req #37). |

---

## 3. Parameters (model inputs)

### Available now (in the Excel)

| Parameter | Indices | Source | Cardinality |
|---|---|---|---|
| `demand[o, q]` | order × quarter | `Q1 2025`, `Q2 2025`, `Q3 2025`, `Q4 2025` columns | 1,824 × 4 |
| `bo[o]` | order | `BO 2024` column | 1,824 |
| `pack_slots[o, q]` | order × quarter | `Pack slot Q1..Q4` | 1,824 × 4 |
| `points[o, q]` | order × quarter | `Point Q1..Q4` | 1,824 × 4 |
| `total_qty[o]` | order | `Total Quantity for packaging 2025` | 1,824 |
| `format[o]` | order | `Mould / Volume` | 1,824 |
| `assigned_line[o]` | order | `packaging line` (current manual choice) | 1,824 |
| `final_factor[o]` | order | `Final Factor` (35 classes) | 1,824 |
| `vip[o]` | order | `VIP` (10000 or null) | 1,824 (73 set) |
| `rating_factor_1/2/3[o]` | order | `1st/2nd/3rd Rating Factor` (A–D) | 1,824 |
| `rating_point_1/2/3[o]` | order | `1st/2nd/3rd Rating Point` | 1,824 |
| `customer_class[c]` | customer | `Ranking` sheet | 38 |
| `region[c]` | customer | `Ranking` sheet | 38 |
| `is_org[c]` | customer | derived (Unicef/Imres/ICRC/IDA flag) | 38 |
| `mode_line_total[g]` | mode line | `ISD Forecast Totals 2025` | 10 |

### Required but NOT in the Excel — see §8

`changeover_h[m, f1, f2]`, `throughput[m, f]`, `shift_calendar[m, w]`, `maintenance[m, w]`, `bom[s, raw]`, `material_avail[raw, w]`, `shelf_life[s]`, `scrap_rate[s]`, `due_date[o]`, `min/max_batch[s]`.

---

## 4. Knobs — user-tunable parameters (req #1, #4, #9)

These must be **configuration**, never hard-coded:

| Knob | Type | Default (suggested) | Affects |
|---|---|---|---|
| `w_fulfilment` | objective weight | 1.00 | demand fulfilment term |
| `w_changeover` | objective weight | 0.10 | changeover-time penalty |
| `w_idle` | objective weight | 0.05 | idle-hours penalty |
| `w_tie_split` | objective weight | 0.20 | fair-distribution bonus (req #40) |
| `vip_multiplier` | rating multiplier | 10,000 | VIP rows (matches Excel) |
| `delay_step` | rating increment per missed window | 1 | the `n` in `n + XYZ` |
| `XYZ_weights[X,Y,Z]` | per-axis weight | configurable A/B/C/D | order ranking |
| `piramal_min_month` / `piramal_max_month` | hard / soft band | 1.8 M / 2.0 M | req #37 |
| `prod_to_pack_min_lag` | days | 4 | req #46 |
| `prod_to_label_min_lag` | days | 3 | req #46 |
| `campaign_coverage_24mo` / `_36mo` | months | 6 / 9 | req #45 |
| `glass_machine_pick_rule` | function | by volume thresholds | req #31, #32 |
| `farcon_dividella_lock` | bool | true | req #35 |
| `team_changeover_no_overlap` | bool | true | req #36 |
| `evaton_min_gap_weeks` | int | 3 | req #49 |
| `horizon_weeks` | int | 52 | req #17 |
| `shifts_per_day`, `days_per_week` | int | 3, 5 | calendar |
| `working_hours_per_shift` | hours | 8 | calendar |
| `holidays[w]`, `maintenance_blocks[m,w]` | calendar | from input | req #5 |

---

## 5. Constraints — mapped to variables and parameters

### Hard

| # | Constraint (req #) | Formal sketch |
|---|---|---|
| 1 | One format per (line, week) — each format max once per cycle (#8) | `Σ_f y_pack[m,f,w] ≤ 1` for each `(m,w)` |
| 2 | Demand fulfilment | `Σ_w vol_pack[o,w] + unfilled[o] = total_qty[o]` |
| 3 | Order ↔ machine eligibility | `x_pack[o,m,w] = 0` if `m ∉ eligible(o)` |
| 4 | Glass-machine selection by volume (#31, #32) | range constraints `vol[o] ∈ band(m)` |
| 5 | Org orders not split (#39) | `Σ_w x_pack[o,*,w] = 1` if `is_org[customer(o)]` |
| 6 | Piramal monthly band (#37) | `1.8M ≤ Σ_{o ∈ Piramal} Σ_{w ∈ month(t)} vol_pack[o,w] ≤ 2.0M` |
| 7 | Farcon XOR Dividella (#35) | `y_pack[Farcon,*,w] + y_pack[Dividella,*,w] ≤ 1` |
| 8 | No overlap of changeover team (#36) | `Σ_m co_pack[m,*,*,w] ≤ 1` per `w` |
| 9 | Production → packaging lag ≥ 4d (#46) | `start_pack[o] ≥ end_prod[o] + 4 days` |
| 10 | Production → labeling lag ≥ 3d (#46) | analogous |
| 11 | One API per production week (#42) | `Σ_a api_active[a,w] ≤ 1` |
| 12 | Campaign coverage by shelf life (#45) | linkage between `qty_prod[s,w]` and `demand` aggregates over 6 / 9 mo |
| 13 | Shelf-life storage cap (#48) | `vol_pack[o,w] ≤ inv[s,w-shelf_life[s]]` style |
| 14 | EVATON 2-batch / 3-week gap (#49) | `Σ_{w'∈[w,w+3)} evaton_batch[w'] ≤ 1` |
| 15 | Capacity (machine-hours) | `Σ_o vol_pack[o,w]/throughput[m,f] + changeover_h ≤ avail_h[m,w]` |

### Soft (penalties on objective)

- Tie-fairness — `tie_split_dev[c]` (#40)
- Schedule highest-score weeks first (#41)
- Minor format changes — small fixed cost (#38)

---

## 6. Objective function

```
maximize
    w_fulfilment   · Σ_o rating_score[o] · (total_qty[o] − unfilled[o])
  − w_changeover   · Σ_{m,f1,f2,w} co_pack[m,f1,f2,w] · changeover_h[m,f1,f2]
  − w_idle         · Σ_{m,w} idle_hours[m,w]
  − w_tie_split    · Σ_c tie_split_dev[c]
  − w_late         · Σ_o late[o]
```

`rating_score[o] = vip_multiplier · vip[o] + Σ_i rating_point_i[o] + delay_step · n[o]` (per the PPT logic).

---

## 7. What we need to solve — in plain English

The job is to take the 1,824 demand lines for 2025 and produce two interlocked schedules:

1. **Packaging schedule** — for every order, pick `(line, week, format)` so that:
   - high-rated and VIP customers come first,
   - Marchesini GL (61 % of demand) doesn't blow capacity,
   - Farcon and Dividella never run at the same time,
   - format changeovers are minimised and never overlap,
   - the Piramal monthly band is respected,
   - organization orders (Unicef / Imres / ICRC / IDA) are not split,
   - and on ties, fulfilment is spread across customers rather than all-or-nothing.
2. **Production schedule** — upstream, decide which API runs each week, in what campaign size, so that:
   - one API per week (with second only if first doesn't fill the week),
   - shelf-life-aware campaign coverage (6 mo for 24-mo SL, 9 mo for 36-mo SL),
   - production finishes ≥ 4 days before packaging,
   - EVATON's special 2-batch / 3-week-gap rule is respected,
   - scrap / yield is built into planned quantity.

Two coupling layers tie them: **inventory of semi-finished** (production output → packaging input) and **time lag** (packaging starts ≥ 4 days after production).

---

## 8. How we propose to solve it

A two-tier decomposition (matches req #27):

- **Tier 1 — Annual feasibility ranking.** A lightweight LP / greedy that uses only `total_qty[o]`, `rating_score[o]`, and rough machine-hour budgets per quarter to *confirm or reject* each order. Output: the set `O* ⊆ O` and an annual envelope. Fast (seconds) and runs at the start of each rolling-horizon cycle.
- **Tier 2 — Quarterly MILP / CP-SAT.** Full assignment for `O*` over 13 weeks at a time with all constraints from §5. Solver candidates:
  - **PuLP + CBC** — cleanest for the linear core (capacity, fulfilment, Piramal band).
  - **OR-Tools CP-SAT** — strongly preferred for the sequencing layer (changeovers, no-overlap of changeover team, sequence-dependent setup, EVATON gap). CP-SAT also natively expresses interval/no-overlap, which is awkward as MILP.
  - Likely **hybrid**: solve the assignment LP in PuLP, lock the line/week selection, hand sequencing to CP-SAT.

A **what-if runner** (req #15) re-runs Tier 2 with knob overrides without touching Tier 1, so users can A/B test parameter changes.

A **re-schedule trigger** (req #3, #7, #16) re-solves a partial Tier-2 problem when Photocells reports a slowdown ≥ threshold; the only new variable is "remainder of in-flight order: continue here or move to next cycle".

We will **not** start any of this until the gaps in §9 are closed.

---

## 9. Missing inputs — gap log

Sorted by blocking severity. Without these, the optimizer cannot be built.

| Gap | Required for | Severity | Likely source |
|---|---|---|---|
| **Format-changeover times** per packaging line, per (`f1`, `f2`) pair | constraint #15, objective changeover term | **Blocker** | DEMO industrial-engineering team; the file naming led us to believe these were in `Marchesini` / `FARCON-DIVIDELLA` / `Partena` sheets, but those are demand pivots. |
| **Throughput** (units / hour) per packaging line × format | constraint #15 (capacity) | **Blocker** | ProdAction (req #6). |
| **Shift / maintenance calendar** per machine × week | capacity (#5) | **Blocker** | Internal HR / maintenance plan. |
| **Bill of Material** per finished product → semi-finished → API | production tier, BoM availability check (#26) | **Blocker for Tier-1** | Master data; mentioned as "to be defined" in req #26. |
| **Material availability** per raw material × week | feasibility (#26, #27) | **Blocker for Tier-1** | SAP. |
| **Shelf life** per semi-finished | constraint #13 (#48), campaign size (#45) | High | Master data. |
| **Scrap / yield rate** per semi-finished | planned-qty calc (#47) | High | ProdAction. |
| **Due date / pack-slot calendar** per order | `late[o]`, `start_pack[o]` lower bound | High | Pack-slot columns exist in BO&FC but are slot *counts*, not dates. Need a date table. |
| **Glass machine selection rule (volume thresholds)** | constraint #4 (#31, #32) | Medium | Stated as a rule in PPT/req but exact thresholds undocumented. |
| **Production-line set + stages** | production tier (#28) | Medium | `M_prod` is currently empty. |
| **API → semi-finished mapping** | constraint #11 (#42) | Medium | Derivable from BoM once available. |
| **Customer canonicalisation** (`UNITED KINGDOM - TROTWOOD` vs `United Kingdom`; `Pharmathen ` trailing space) | clean joins to ratings | Low (data hygiene) | Internal. |
| **Piramal entity definition** | constraint #6 (#37) | **Clarification** | The 32.9 M Piramal demand exceeds 1.8–2.0 M every month — confirm whether this is a per-entity or aggregate band. |
| **Photocells / ProdAction integration spec** | re-schedule trigger (#3, #7, #16) | Deferred | API contract from those platforms. |

---

## 10. Open questions for the client

1. **Piramal band scope** — per legal entity, per ship-to, or aggregate?
2. **Backup-line policy** — when Marchesini overflows, which line is the documented fallback (#9 mentions configurable backup)?
3. **VIP weight** — is `10000` a literal points value or a flag we should translate?
4. **Customer-product rating period** — `Ranking GR` shows period `01.01.2024 – 31.12.2024`; do we re-rate for 2025 or carry forward?
5. **Mode-line scope of the optimizer** — start with Glass Ampoules only (this Excel), or extend to Plastic Ampoules / Bottles / Lyophillized in the same model?
6. **What-if storage** — do scenario runs need to be persisted (audit trail) or are they ephemeral?

---

## Glossary / Abbreviations

| Acronym | Expansion / Meaning |
|---|---|
| **API** | Active Pharmaceutical Ingredient — drives the "one API per production week" rule (req #42). |
| **BO** | Back Order — unfulfilled 2024 demand carrying into 2025 (`BO 2024` column). |
| **BoM** | Bill of Materials — finished-product → semi-finished → API decomposition. |
| **CBC** | COIN-OR Branch and Cut — open-source MILP solver. |
| **CP-SAT** | Constraint Programming-SATisfiability solver (Google OR-Tools); used for sequencing layer. |
| **EVATON** | A specific semi-finished product class with the 2-batch / 3-week-gap rule (req #49). |
| **GR** | Greece (domestic channel, also a sheet name). |
| **HiGHS** | High-performance LP/MIP solver (open-source). |
| **HR** | Human Resources. |
| **ICRC / IDA / Imres / Unicef / MSF** | Humanitarian organisations whose orders cannot be split (req #39). |
| **IIS** | Irreducible Infeasible Subsystem — minimal subset of constraints whose conflict makes the model infeasible. |
| **ISD** | International Sales Division (the export channel). |
| **LP** | Linear Programming. |
| **MILP** | Mixed-Integer Linear Programming. |
| **MiniZinc** | Declarative constraint-modelling language (solver-agnostic). |
| **OR-Tools** | Google's open-source operations-research toolkit (CP-SAT, LNS, etc.). |
| **PoC** | Proof of Concept (this 2025-data exercise, per req #34). |
| **PuLP** | Python LP/MIP modelling library; talks to CBC, Gurobi, HiGHS, etc. |
| **Pyomo** | Python algebraic modelling language for optimisation. |
| **Q1 / Q2 / Q3 / Q4** | Calendar quarters. |
| **SAP** | The ERP system (DEMO's master-data and order-of-record). |
| **SKU** | Stock Keeping Unit (here: SAP Material Number). |
| **TBD** | To Be Determined — placeholder for inputs not yet sourced. |
| **VIP** | Very-Important flag (weight 10,000 in the rating). |
| **XYZ** | Three-axis customer-product rating: X = on-time/in-full, Y = order flexibility, Z = profitability tier. |
