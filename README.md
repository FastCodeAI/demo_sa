# DEMO Pharma — Production & Packaging Scheduler

PoC implementation of the production + packaging scheduler for DEMO Pharma's 2025 demand. Replaces a manual Excel workflow with a configurable two-stage MILP that assigns 1,824 demand lines (~90.4 M ampoules) to four packaging lines (Marchesini GL, Farcon, Dividella, Partena) over 52 weeks, while respecting customer ratings, format changeovers, shelf life, and customer-volume bands.

The current state is **Phase 2 in progress** — Pyomo MILP + OR-Tools CP-SAT
sequencing, driven by a versioned constraint catalog, with a deterministic
verifier in place for the upcoming LLM-agent loop.

## Quick start

```bash
cd /home/cg/DEMO_SA
.venv/bin/pip install -e .

# Phase-1 hardcoded MILP only (~10 min on Q1)
.venv/bin/demo_scheduler solve --quarter Q1 --skip-cpsat

# Phase-2a: hardcoded MILP + CP-SAT sequencing (default)
.venv/bin/demo_scheduler solve --quarter Q1

# Phase-2b: catalog-driven MILP + CP-SAT
.venv/bin/demo_scheduler solve --quarter Q1 --use-catalog
```

Outputs land in `outputs/q1/` (plan.json, plan.csv, gantt.png, kpis.json).
`--use-catalog` writes alongside in `outputs/q1_catalog/` when `--output-dir`
is supplied.

## Catalog operations

```bash
.venv/bin/demo_scheduler catalog list             # all rows
.venv/bin/demo_scheduler catalog show C-037       # one row, JSON-formatted
.venv/bin/demo_scheduler catalog validate         # pydantic re-check
```

## Docs

| File | Purpose |
|---|---|
| [docs/README.md](docs/README.md) | Project framing, data model, problem statement (15 hard + 3 soft constraints). |
| [docs/MODEL_SCOPING.md](docs/MODEL_SCOPING.md) | Index sets, decision variables, parameters, knobs, gap log. |
| [docs/PROBLEM_AND_APPROACH.md](docs/PROBLEM_AND_APPROACH.md) | Formal problem statement and three-tier hybrid (catalog / solvers / agents). |
| [docs/PROPOSED_SOLUTION_ARCH.md](docs/PROPOSED_SOLUTION_ARCH.md) | 4-layer architecture diagram (User / Agent / Tool / Data) with glossary. |
| [docs/PHASE2_PLAN.md](docs/PHASE2_PLAN.md) | Phase 2 plan: CP-SAT split, constraint catalog, agent loop. |
| [docs/EMAIL_SUMMARY.md](docs/EMAIL_SUMMARY.md) | Three drop-in client-reply versions (150 / 250 / 600 words). |
| [OUTPUT_Q1.md](OUTPUT_Q1.md) | Phase-1 Q1 report — what was solved, how, and the result. |

## Inputs (this folder)

| File | Purpose |
|---|---|
| `Packaging Ampoules.xlsx` | Master 2025 demand, ratings, manual machine allocations. |
| `Requirements EN.pdf` | 50 numbered platform requirements. |
| `Production Planning & Scheduling overview EN.pptx` | Planning logic the algorithm must reproduce. |
| `starter_email` | Context on the legacy app + modernisation scope. |

## Layout

```
DEMO_SA/
├── README.md                        # this file
├── pyproject.toml                   # package metadata + deps
├── analyze_packaging_data.py        # legacy exploratory script (still runnable)
├── docs/                            # all design docs
├── configs/defaults.yaml            # mocked + parameterised inputs (Phase 1)
├── catalog/                         # versioned constraint catalog (Phase 2b)
│   ├── constraints/                 #   *.yaml per constraint (7 migrated so far)
│   └── audit/                       #   <id>-history.jsonl on every Verifier run
├── demo_scheduler/                  # the package
│   ├── cli.py
│   ├── config/, data/, output/
│   ├── model/                       #   build.py (hardcoded), compile.py (catalog),
│   │                                #   tier3_cpsat.py, compilers/
│   ├── solve/                       #   solver.py (MILP), solver_cpsat.py, orchestrator.py
│   ├── catalog/                     #   schema.py, load.py
│   ├── verifier/                    #   verify.py (Phase 2c trust boundary)
│   ├── agents/                      #   supervisor.py + 6 sub-agents + llm.py
│   └── tests/
├── outputs/                         # generated artefacts
│   ├── q1/                          #   default Phase-1/2a outputs
│   └── q1_catalog/                  #   Phase-2b catalog-driven outputs
└── .venv/                           # Python venv
```

## Legacy exploratory analysis

The pre-solver data analyzer is still useful:

```bash
.venv/bin/python analyze_packaging_data.py
# writes 18 artefacts under outputs/
```

## Status

- **Phase 1 — scoping + data analysis**: complete — see `docs/`.
- **Phase 1 solver — single Q1 MILP, HiGHS**: complete — 95 % OTIF in 10 min (Phase-1 baseline).
- **Phase 2a — CP-SAT sequencing split**: complete — Q1 reaches **100 % OTIF**, 0 CP-SAT-infeasible weeks, 613 minute-level intervals (see `outputs/q1/kpis.json`).
- **Phase 2b — constraint catalog**: **16 catalog rows**, 12 enabled (one per Phase-1 constraint), 4 disabled/CP-SAT-layer placeholders, 10 working pattern compilers + 4 stubs. Q1 `--use-catalog` run: **98.8 % OTIF** in 5.6 min.
- **Phase 2c — agent loop**: foundation in place — deterministic **Verifier** with 4 checks (schema, unit, business-rule approval-gate detection, optional warm-cached feasibility probe). **7 LangGraph sub-agents** wired (Supervisor / Constraint-Elicitation / Schedule-Generation / What-If / Explanation / Infeasibility / Disruption-Response) with mockable `LLMClient`. Approval queue + SAP bridge pending.

### Phase-2c agent CLI

```bash
# Routed by the Supervisor: classifies intent, dispatches to a sub-agent.
.venv/bin/demo_scheduler chat "raise Piramal monthly band to 2.5M" --use-mock
# → "C-037 -> {'monthly_max': 2500000}" + 3 Verifier ticks + ⚠ Sales-VP gate

# Two KPI files → 3-5 bullet narration of the delta.
.venv/bin/demo_scheduler whatif outputs/q1/kpis.json outputs/q1_catalog/kpis.json --use-mock
```

Set `ANTHROPIC_API_KEY` to swap `--use-mock` for the real Claude SDK.

### Headline Q1 numbers

| Path | OTIF | Rated OTIF | VIP OTIF | MILP time | CP-SAT time | Notes |
|---|---:|---:|---:|---:|---:|---|
| Phase 1 (hardcoded, MILP only) | 95.0 % | 97.9 % | 100 % | 600 s | — | `--skip-cpsat` |
| Phase 2a (hardcoded + CP-SAT) | 100.0 % | 100.0 % | 100 % | 592 s | 181 s | default path |
| Phase 2b (catalog + CP-SAT) | 98.8 % | 99.6 % | 100 % | 155 s | 182 s | `--use-catalog` |

### Tests

```bash
.venv/bin/pytest demo_scheduler/tests/ -v          # 30 fast tests, ~10 s
.venv/bin/pytest demo_scheduler/tests/test_q1_smoke.py # +1 slow Q1 smoke, ~2 min
```
