# DEMO Pharma — Production & Packaging Scheduling PoC

## Project overview

DEMO is a Greek pharmaceutical manufacturer that fills and packages injectable products (ampoules, vials, bottles) for both the domestic market and international customers (Piramal, Unicef, ICRC, Imres, IDA, MSF, regional distributors). Their production is two-stage: **semi-finished** (API → filled containers, not for sale) and **finished** (secondary packaging, ready for sale). Glass containers are stocked from a forecast-driven production plan; plastic containers are filled and packaged concurrently against orders.

The current shopfloor application is a Laravel 5.7 / Vue 2 monolith (~93,500 LOC) backed by MySQL and integrated with SAP. It does not perform automated production or packaging scheduling — that work is run manually in Excel. This project is a Proof-of-Concept for the **scheduling/optimization core** of the replacement platform.

The PoC is bound to **2025 demand** (forecast taken in December 2024) per requirement #34. This deliverable scopes the data, frames the optimization problem, and provides an exploratory analyzer — it is not the optimizer itself.

## Inputs (this folder)

| File | Purpose |
|---|---|
| `Packaging Ampoules.xlsx` | Master 2025 demand, customer ratings, manual machine allocations from the Excel run. |
| `Requirements EN.pdf` | 50 numbered platform requirements (planning, scheduling, integrations, UI). |
| `Production Planning & Scheduling overview EN.pptx` | The planning logic the algorithm must reproduce (capacity, ratings, campaign rules). |
| `starter_email` | Context on the legacy app stack and modernization scope. |

## Problem statement

This is a **mixed-integer linear / constraint-programming** scheduling problem with two coupled tiers:

- **Annual feasibility / ranking** (req #27): rank all 2025 orders against capacity to confirm what can be produced.
- **Quarterly schedule** (req #27): for confirmed orders, assign each order to a `(line, machine, week, format)` and a packaging slot `≥4 days` after production (req #46), `≥3 days` after labeling (req #46).

### Decision variables (sketch)
- `x[o, m, w, f] ∈ {0,1}` — order `o` runs on machine `m` in week `w` with format `f`.
- `y[m, f, w] ∈ {0,1}` — machine `m` is configured for format `f` in week `w` (campaign indicator).
- Ancillary: changeover indicators `z[m, f₁, f₂, w]`, line-team conflict indicators (Farcon ⊕ Dividella).

### Objective (req #10)
Maximize a weighted combination of:
1. Demand fulfilment (order-rank × volume).
2. Negative changeover hours.
3. Negative idle time.

### Hard constraints
- One API per production week (req #42); fill week with second API only if first does not fill it (#43, #44).
- Order-splitting forbidden for organization customers Unicef / Imres / ICRC / IDA (#39).
- Piramal monthly volume must lie in **1.8M–2.0M ampoules** (#37).
- **Farcon XOR Dividella** active in any given window — same operator team (#35).
- No overlapping format changeovers across packaging machines — same changeover team (#36).
- Labeling ≥ 3 days after production; packaging ≥ 4 days after production (#46).
- Semi-finished may not enter packaging past its shelf-life storage limit (#48).
- Campaign sizing: 6 months of demand for shelf-life 24 mo, 9 months for shelf-life 36 mo (#45).
- Glass-container packaging machine selected by order volume; over-capacity orders open the third (backup) line, never downgrade to a smaller machine (#31, #32).
- Each format applied at most once per work-center per cycle (#8).

### Soft / preference constraints
- On ties, distribute production so no customer is fulfilled 100% while another is 0% (#40).
- Schedule highest-score weeks first (#41).
- Minor format changes are allowed (#38).
- Prefer 1-week minimum runs of the same format (PPT capacity logic).

### Configurability (req #1, #4, #9)
Weights of the rank algorithm, changeover times, backup-machine choice, and the set of lines/SKUs must all be parameters — not hard-coded.

## Data model (Excel)

| Sheet | Role | Shape | Notes |
|---|---|---|---|
| `BO & FC 2025` | Master demand / order list | 1,824 × 38 | One row per (Country/Customer × Material × line × format). Columns: 1st/2nd/3rd Rating Factor (A–D), Final Factor, VIP flag, Total points, packaging line, Mould/Volume, BO 2024, then per-quarter triplets (Qx 2025, Pack slot Qx, Point Qx), and Total Quantity for packaging 2025. |
| `Marchesini` | Manual pivot of demand allocated to **Marchesini GL** | 48 × 7 | Header at row 3. Row labels mix formats and customer subtotals. Columns: Sum of Pack slot ΒΟ / Q1 / Q2 / Q3 / Q4. |
| `FARCON-DIVIDELLA` | Manual pivot of demand allocated to **Farcon + Dividella** | 66 × 7 | Header at row 4. Same column layout as Marchesini. |
| `Partena` | Manual pivot of demand allocated to **Partena** | 13 × 6 | Header at row 4. Sparse. |
| `Ranking` | Customer master — classification, country, continent, 1st/2nd Rating Factor | 74 × 9 | Lookup for the rating columns in `BO & FC 2025`. |
| `Ranking ISD` | Per-SKU ranking for the ISD (international) channel — Net Profit, Net Profit %, grouped A/B/C | 193 × 12 | |
| `Ranking GR` | Per-SKU ranking for the Greek (GR) channel | 114 × 12 | Period 2024-01-01 … 2024-12-31. |
| `ISD Forecast Totals 2025` | 2025 demand by Mode Line: Plastic Ampoules, Glass Ampoules, Plastic Bottles, Lyophillized, Cephalosporins | 12 × 2 | Top-down baseline. |
| `ISD OPEN ORDERS 2025` | SAP open-order extract: sales org, sold-to party, dates, net values | 448 × 41 | |
| `UPLOAD UNIT` | Per-SKU per-quarter qty as uploaded to SAP | 910 × 14 | |
| `ITEM DESCR` | Material master: code, description, base unit, X-plant status | 787 × 4 | |
| `PIVOT` | Quarterly upload pivot by material | 791 × 5 | |

**Important gap:** The Excel does **not** contain explicit format-changeover-time matrices for the packaging or production machines. Those values appear only in the PPT logic discussion. They must be supplied as a separate input (e.g., a `changeovers.csv` per machine) before the optimizer can be built.

## Modeling approach (proposed)

A two-tier model, configurable per requirement #1:

1. **Annual ranking + feasibility (LP relaxation or greedy)** — rank orders by `n + XYZ` (per the PPT) and accumulate against rough machine-hour capacity. Output: an annual confirm/reject set per req #27.
2. **Quarterly MILP** — assign confirmed orders to `(machine, week, format)` slots. Implementations to consider:
   - `PuLP` + CBC for the linear core (campaigns, capacity, customer bands).
   - `OR-Tools CP-SAT` for the scheduling layer (changeovers, no-overlap on shared changeover team, sequence-dependent setup).
3. **What-if runner** (req #15) — same model run with parameter overlays (machine availability, weights, shift configurations).
4. **Re-schedule trigger** (req #3, #7, #16) — invoked when Photocells reports a slowdown ≥ threshold; partial-batch decision becomes an MILP variable on the remainder.

Implementation of the optimizer is **out of scope** for this commit.

## Repository layout

```
DEMO_SA/
├── README.md                       # this file
├── requirements.txt                # pinned Python deps
├── analyze_packaging_data.py       # exploratory data analyzer
├── Packaging Ampoules.xlsx         # input data (read-only)
├── Requirements EN.pdf             # input
├── Production Planning & Scheduling overview EN.pptx   # input
├── starter_email                   # input
├── .venv/                          # local Python virtual environment
└── outputs/                        # generated by analyze_packaging_data.py
    ├── sheet_profiles.txt
    ├── demand_by_quarter.csv / .png
    ├── demand_by_country.csv
    ├── demand_by_sku.csv
    ├── demand_by_packaging_line.csv
    ├── demand_by_format.csv
    ├── rating_final_factor.csv
    ├── rating_vip.csv
    ├── machine_pivot_<machine>.csv
    ├── machine_pivot_totals.csv
    ├── isd_forecast_totals.csv / .png
    ├── piramal_monthly_check.csv
    ├── top20_countries.png
    └── top20_skus.png
```

## Setup & run

```bash
cd /home/cg/DEMO_SA
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python analyze_packaging_data.py
```

The script reads `Packaging Ampoules.xlsx` from this directory, writes all output to `outputs/`, and prints a one-screen summary (totals, top customers, Piramal verdict) to stdout.

## Next steps

1. **Source changeover-time matrices** for Marchesini GL, Farcon, Dividella, Partena (and any production-side machines). Store as `data/changeovers/<machine>.csv`.
2. **Build a BoM master** (req #26) and a material-availability feed; required for the feasibility tier.
3. **Canonicalize entities**: customer names (e.g. `UNITED KINGDOM - TROTWOOD` vs `United Kingdom`), packaging-line codes, format codes (`0002ml` vs `2ml`).
4. **Encode the rating algorithm** (XYZ + delay grade `n` + VIP overlay) as a parameterized scoring function (req #4).
5. **Prototype the quarterly MILP** for **one quarter, glass ampoules only** as the first vertical slice — smallest data subset, all the hard constraints (Piramal band, Farcon XOR Dividella, glass-machine selection by volume) still apply.
6. **Wire up Photocells & ProdAction feeds** (req #6) — units/hour and units/batch are required to convert demand volume into machine-hours.
7. **Manual-edit + send-to-SAP path** (req #12, #30) — defer until the schedule output format is stable.

---

## Glossary / Abbreviations

| Acronym | Expansion / Meaning |
|---|---|
| **API** | Active Pharmaceutical Ingredient — the biologically-active substance in a drug product (not "Application Programming Interface" in this document). |
| **BO** | Back Order — demand carried over from the previous year that has not yet been fulfilled. |
| **BoM** | Bill of Materials — the structured list of raw materials, semi-finished products, and components needed to produce a finished SKU. |
| **CIS** | Commonwealth of Independent States — post-Soviet trading bloc (e.g. *CIS FARMA LLC* in the customer list). |
| **CP / CP-SAT** | Constraint Programming / Constraint Programming-SATisfiability solver (Google OR-Tools). |
| **DDD** | Domain-Driven Design — software architecture style used in the legacy Laravel app. |
| **DEMO** | The pharmaceutical client (DEMO ABEE, Greece). |
| **FC** | Forecast — projected future demand (here: 2025 quarterly demand). |
| **GR** | Greece (the domestic sales channel / country code). |
| **ICRC** | International Committee of the Red Cross — humanitarian organisation; one of the indivisible-order customers. |
| **IDA** | International Dispensary Association — humanitarian non-profit; indivisible-order customer. |
| **Imres** | Independent humanitarian supplier; indivisible-order customer. |
| **ISD** | International Sales Division — DEMO's export channel. |
| **LOC** | Lines of Code. |
| **LP** | Linear Programming — continuous-variable optimisation. |
| **MILP** | Mixed-Integer Linear Programming — LP with some integer / binary variables. |
| **MSF** | Médecins Sans Frontières (Doctors Without Borders) — humanitarian NGO; indivisible-order customer. |
| **ORM** | Object-Relational Mapping (e.g. Eloquent in the legacy Laravel stack). |
| **PoC** | Proof of Concept. |
| **Q1 / Q2 / Q3 / Q4** | Calendar quarters: Jan–Mar, Apr–Jun, Jul–Sep, Oct–Dec. |
| **SAP** | The ERP system used by DEMO (company name *Systems, Applications & Products in Data Processing*). |
| **SKU** | Stock Keeping Unit — uniquely identifiable saleable product (here: by Material Number). |
| **UI** | User Interface. |
| **Unicef** | United Nations Children's Fund — humanitarian buyer; indivisible-order customer. |
| **VIP** | Very-Important order flag — top-priority overlay set by the Sales Director (weight 10,000 in the rating). |
| **XYZ** | Three-axis customer-product rating: **X** = on-time/in-full grade (A–D), **Y** = order flexibility (fixed timing/qty vs. flexible), **Z** = profitability tier (top 25% / 50% / 75% / all). |
